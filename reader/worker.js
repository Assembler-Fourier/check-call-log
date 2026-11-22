// NORTHWIND check-call reader - the add-on's worker. It holds what the WhatsApp tab reported until Check Call
// Log on this PC takes it (127.0.0.1 only), so nothing is lost while the window is closed. It talks to nothing
// else: not to WhatsApp, not to Google, not to the internet.
//
// Before it hands over the secret or a single message, the receiver must prove it is Check Call Log: it is sent a
// random number and must answer with HMAC(secret, number), which only the real one can. While the window is
// closed, any program could sit on the port - it gets nothing.
importScripts("config.js");                     // READER = {port, token}, written by tool\whatsapp.py setup

const BASE = "http://127.0.0.1:" + READER.port;
const KEEP = 3000;                              // at most this many waiting; the oldest go first
const READ_HOLD = 20 * 3600 * 1000;           // one shift: how long an automatic read holds a chat
const OLD = 2 * 24 * 3600 * 1000;               // and none older than two days (they are staff's messages)
const REQUEST_TIMEOUT = 5000;                 // a stalled local receiver must not block persisting newer events
let pendingBeat = null, lastAcceptedBeat = null; // the latest heartbeat, and the exact report accepted locally
let chain = Promise.resolve();                  // every change to the queue happens one at a time, in order
const DRAFT_CAPABILITY = "drafts-v4";
const capable = new Map();                      // only this worker's observed, same-extension tab/documents
let currentClient = null, clientAt = 0, actionBusy = false, activeAction = null;

const hex = (bytes) => [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
const idOf = (e) => [e.kind, e.chat, e.id || "", e.date || "", e.shown || "", e.direction || "", e.ours,
                     e.text, e.what, e.media_type || "", e.truncated === true, e.emoji || "", e.seen, e.i || 0, e.read_id || "", e.view_id || "",
                     reactionKey(e)].join("\u0001");

// An attached reaction is a current snapshot on its parent message, never a
// new message with an invented clock. Missing metadata remains legacy/unknown.
function reactionKey(event) {
  if (!Object.hasOwn(event, "reactions")) return "absent";
  const value = event.reactions;
  if (!validReactions(event)) return "invalid:" + JSON.stringify(value);
  return JSON.stringify([value.complete, value.items.map(item =>
    [item.parent_id, item.emoji, item.actor, item.actor_name, item.at]).sort((a, b) => a[2].localeCompare(b[2]))]);
}

function validReactions(event) {
  if (!Object.hasOwn(event, "reactions")) return true;
  const value = event.reactions;
  if (event.kind !== "bubble" || event.group !== false || typeof event.id !== "string" || !event.id || event.id.length > 200 ||
      !value || typeof value !== "object" || Array.isArray(value) ||
      Object.keys(value).sort().join(",") !== "complete,items" || typeof value.complete !== "boolean" ||
      !Array.isArray(value.items) || value.items.length > 2) return false;
  const actors = new Set();
  for (const item of value.items) {
    if (!item || typeof item !== "object" || Array.isArray(item) ||
        Object.keys(item).sort().join(",") !== "actor,actor_name,at,emoji,parent_id" ||
        item.parent_id !== event.id || !["contact", "self"].includes(item.actor) || actors.has(item.actor) ||
        item.actor_name !== (item.actor === "self" ? "You" : event.chat) || item.at !== null ||
        typeof item.emoji !== "string" || !item.emoji || item.emoji.length > 16 || item.emoji.trim() !== item.emoji ||
        /[\u0000-\u001f\u007f]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?:^|[^\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(item.emoji)) return false;
    actors.add(item.actor);
  }
  return true;
}

function serial(job) {
  chain = chain.then(job, job).catch(() => {});
  return chain;
}

async function proofFor(nonce) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(READER.token),
                                            {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
  return hex(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(nonce)));
}

async function fetchJson(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);
  try {
    const res = await fetch(BASE + path, Object.assign({}, options, {signal: controller.signal}));
    let body = null;
    try { body = await res.json(); } catch (e) { /* includes a stalled response body aborted by the timer */ }
    return {res, body};
  } finally {
    clearTimeout(timer);
  }
}

async function isCheckCallLog() {
  const nonce = hex(crypto.getRandomValues(new Uint8Array(16)));
  const {res, body} = await fetchJson("/wa/hello?nonce=" + nonce);
  if (!res.ok) return false;
  return !!body && body.proof === await proofFor(nonce);
}

async function getReadChats() {
  const got = await chrome.storage.local.get({readChats: {}});
  const markers = got.readChats && typeof got.readChats === "object" ? got.readChats : {};
  let changed = false;
  // A chat read automatically holds its ordinary sightings back until a complete read is committed - but only for
  // one shift. Before 8 Nov the hold never ended, so on a later night with Auto read off that guard could never be
  // confirmed from ordinary watching. (Holds with no start time are left as they were.)
  for (const [chat, marker] of Object.entries(markers)) {
    if (marker && Number.isFinite(marker.started) && marker.started < Date.now() - READ_HOLD) {
      delete markers[chat];
      changed = true;
    }
  }
  for (const marker of Object.values(markers)) {
    if (marker && /^[a-f0-9]{32}$/.test(marker.id || "") &&
        (!/^[a-f0-9]{32}$/.test(marker.passiveId || "") || marker.passiveId === marker.id)) {
      marker.passiveId = hex(crypto.getRandomValues(new Uint8Array(16)));
      changed = true;
    }
  }
  if (changed) await chrome.storage.local.set({readChats: markers});
  return markers;
}

async function getQueue() {
  const markers = await getReadChats();
  const got = await chrome.storage.local.get({queue: []});
  const cut = Date.now() - OLD;
  return got.queue.filter((e) => e && e.seen > cut).slice(-KEEP).map(e =>
    /^[a-f0-9]{32}$/.test(e.read_id || "") ? e : ownedEvent(e, markers));
}

function ownedEvent(event, readChats) {
  // Only the worker assigns provenance. Once automatic reading owns a chat,
  // ordinary observations remain quarantined until a complete read is committed
  // by the app. This survives worker/tab reloads and zero-event interrupted reads.
  const clean = {...event};
  delete clean.read_id;
  const marker = readChats && Object.hasOwn(readChats, clean.chat) && readChats[clean.chat];
  if (marker && /^[a-f0-9]{32}$/.test(marker.passiveId || "")) clean.read_id = marker.passiveId;
  return clean;
}

function add(events) {
  return serial(async () => {
    const queue = await getQueue();
    const markers = await getReadChats();
    await chrome.storage.local.set({queue: queue.concat(events.map(e => ownedEvent(e, markers))).slice(-KEEP)});
  });
}

function clientFor(sender) {
  if (!sender || sender.id !== chrome.runtime.id || !sender.tab || !Number.isInteger(sender.tab.id) ||
      sender.frameId !== 0 || !/^[A-Za-z0-9-]{8,100}$/.test(sender.documentId || "") ||
      !sender.url || !sender.url.startsWith("https://web.whatsapp.com/")) return null;
  return {tab: sender.tab.id, document: sender.documentId,
          owner: sender.tab.id + ":" + sender.documentId, capability: DRAFT_CAPABILITY};
}

function liveClient() {
  return currentClient && Date.now() - clientAt >= 0 && Date.now() - clientAt < 90000 &&
    Date.now() - (capable.get(currentClient.owner) || 0) < 45000 ? currentClient : null;
}

async function ackAction(result) {
  // The challenge still precedes every authenticated operation, including a retry of an ACK.
  if (!(await isCheckCallLog())) return;
  await fetchJson("/wa/action", {method: "POST",
    headers: {"Content-Type": "application/json", "X-Reader-Token": READER.token}, body: JSON.stringify(result)});
}

async function authorizeAction(request, client) {
  const current = () => client && activeAction && request.id === activeAction.id && request.owner === activeAction.owner &&
    request.owner === client.owner && liveClient() && liveClient().owner === client.owner &&
    ["search", "open", "profile", "draft", "read", "send"].includes(request.step) &&
    (request.step !== "read" || activeAction.kind === "read") &&
    (request.step !== "draft" || activeAction.kind === "draft" || activeAction.kind === "send") &&
    (request.step !== "send" || activeAction.kind === "send");
  try {
    if (!current() || !(await isCheckCallLog()) || !current()) return {ok: false};
    const {res, body} = await fetchJson("/wa/authorize", {method: "POST",
      headers: {"Content-Type": "application/json", "X-Reader-Token": READER.token},
      body: JSON.stringify({id: request.id, owner: request.owner, step: request.step})});
    if (!current() || !res.ok || !body || body.ok !== true || body.id !== request.id || body.owner !== client.owner ||
        body.step !== request.step || !Number.isFinite(body.expires) || body.expires <= Date.now()) return {ok: false};
    if (body.step === "read") activeAction.readUntil = body.expires;
    if (body.step === "open") activeAction.openGranted = true;
    return {ok: true, id: body.id, owner: body.owner, step: body.step, expires: body.expires};
  } catch (e) { return {ok: false}; }             // receiver closed, revoked, changed owner or unproved: never type
}

function readingSummary(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (Object.keys(value).sort().join(",") !== "complete,incoming,messages,newest,oldest,outgoing,reason") return null;
  const {messages, incoming, outgoing, complete, oldest, newest, reason} = value;
  if (![messages, incoming, outgoing].every(n => Number.isInteger(n) && n >= 0 && n <= 1200) ||
      incoming + outgoing > messages || typeof complete !== "boolean" ||
      !["since_reached", "history_start", "limit", "timeout", "no_messages", "history_unavailable"].includes(reason) ||
      ![oldest, newest].every(n => n === null || Number.isInteger(n) && n >= 0 && n <= Date.now() + 5000) ||
      ((oldest === null) !== (newest === null)) || oldest !== null && oldest > newest ||
      (messages === 0 ? oldest !== null || complete : oldest === null) ||
      complete && reason !== "since_reached" || reason === "since_reached" && !complete ||
      reason === "no_messages" && messages !== 0) return null;
  return {messages, incoming, outgoing, complete, oldest, newest, reason};
}

function capturedAt(event) {
  const d = typeof event.date === "string" && event.date.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  const t = typeof event.shown === "string" && event.shown.match(/^([01]?\d|2[0-3]):([0-5]\d)$/);
  if (!d || !t || +d[3] < 1970) return null;
  const at = new Date(+d[3], +d[2]-1, +d[1], +t[1], +t[2]);
  return at.getFullYear() === +d[3] && at.getMonth() === +d[2]-1 && at.getDate() === +d[1] ? +at : null;
}

function deliverReadCapture(message, client) {
  // A read succeeds only after the authenticated receiver has stored every captured
  // event. An ordinary open result or a drained/refused normal queue is not proof.
  const action = activeAction;
  const current = () => action && activeAction === action && action.kind === "read" &&
    client && liveClient() && liveClient().owner === client.owner && action.owner === client.owner &&
    message.id === action.id && message.owner === action.owner && Date.now() < action.expires;
  const reading = readingSummary(message.reading);
  let events = message.events;
  const parentCounts = new Map();
  if (Array.isArray(events)) for (const event of events) {
    if (event && typeof event.id === "string") parentCounts.set(event.id, (parentCounts.get(event.id) || 0) + 1);
  }
  if (!current() || !(action.readUntil > Date.now()) || action.captureBusy || action.delivered || !reading ||
      !Array.isArray(events) || events.length > 1200 || events.length !== reading.messages ||
      (reading.complete && reading.oldest > action.since) ||
      events.some(e => !e || e.kind !== "bubble" || e.chat !== action.chat || e.group !== false ||
        !["incoming", "outgoing", "unknown"].includes(e.direction) ||
        (Object.hasOwn(e, "ours") && (e.direction === "unknown" || typeof e.ours !== "boolean" || e.ours !== (e.direction === "outgoing"))) ||
        !["text", "voice", "media", "deleted"].includes(e.what) ||
        (Object.hasOwn(e, "media_type") && (e.what !== "media" || e.media_type !== "sticker")) ||
        typeof e.text !== "string" || e.text.length > 300 || (!e.text && e.what === "text") ||
        (Object.hasOwn(e, "truncated") && typeof e.truncated !== "boolean") ||
        !validReactions(e) || (Object.hasOwn(e, "reactions") && parentCounts.get(e.id) !== 1) ||
        (reading.complete && (e.truncated === true || e.reactions?.complete === false) && capturedAt(e) >= action.since) ||
        (Object.hasOwn(e, "view_id") && (!/^[a-f0-9]{32}$/.test(e.view_id) || typeof e.view_id !== "string" ||
          !Number.isInteger(e.i) || e.i < 0 || e.i > 10000 || typeof e.id !== "string" || !e.id || e.id.length > 200)) ||
        !Number.isFinite(e.seen) || e.seen < Date.now() - OLD || e.seen > Date.now() + 5000 ||
        capturedAt(e) === null) ||
      events.filter(e => e.direction === "incoming").length !== reading.incoming ||
      events.filter(e => e.direction === "outgoing").length !== reading.outgoing ||
      new Set(events.map(idOf)).size !== events.length ||
      reading.oldest !== (events.length ? Math.min(...events.map(capturedAt)) : null) ||
      reading.newest !== (events.length ? Math.max(...events.map(capturedAt)) : null)) return Promise.resolve({ok: false});
  events = events.map(e => ({...e, read_id: action.id}));
  action.captureBusy = true;
  let answer = {ok: false};
  return serial(async () => {
    try {
      if (!current()) return;
      const queue = await getQueue();
      const ids = new Set(events.map(idOf));
      await chrome.storage.local.set({queue: queue.filter(e => !ids.has(idOf(e))).concat(events).slice(-KEEP)});
      // Empty supported views are not claimed complete, but still need an app grant.
      for (let offset = 0; offset < Math.max(events.length, 1); offset += 300) {
        if (!current()) return;
        const granted = await authorizeAction({id: action.id, owner: action.owner, step: "read"}, client);
        if (!granted.ok || !current() || !(granted.expires > Date.now()) || !(await isCheckCallLog()) ||
            !current() || !(granted.expires > Date.now())) return;
        const batch = events.slice(offset, offset + 300);
        const {res, body} = await fetchJson("/wa", {method: "POST",
          headers: {"Content-Type": "application/json", "X-Reader-Token": READER.token},
          body: JSON.stringify({events: batch})});
        if (!current() || !res.ok || !body || body.ok !== true || body.kept !== batch.length) return;
        const sent = new Set(batch.map(idOf));
        const now = await getQueue();
        await chrome.storage.local.set({queue: now.filter(e => !sent.has(idOf(e)))});
      }
      if (!current()) return;
      action.delivered = JSON.stringify(reading);
      answer = {ok: true, id: action.id, owner: action.owner, kept: events.length};
    } finally { action.captureBusy = false; }
  }).then(() => answer);
}

async function performAction(action, client) {
  if (!action || typeof action.id !== "string" || !/^[a-f0-9]{32}$/.test(action.id) || !client) return;
  const result = (status, detail) => ({id: action.id, owner: action.owner, status, detail});
  let answer = result("unknown", "The operation was interrupted; inspect WhatsApp before retrying.");
  let acquired = false;
  try {
    if (actionBusy || !liveClient() || liveClient().owner !== client.owner || action.owner !== client.owner ||
        !["open", "draft", "read", "send"].includes(action.kind) || !Number.isFinite(action.expires) || action.expires <= Date.now() ||
        (action.kind === "read" && (action.text != null || !/^[1-9][0-9]{8,14}$/.test(action.phone || "") ||
          !Number.isFinite(action.since) || action.since < Date.now() - OLD || action.since > Date.now() + 5000))) {
      await ackAction(result("blocked", "The verified WhatsApp document is no longer ready.")); return;
    }
    actionBusy = true; acquired = true;
    let persisted = false, duplicate = false;
    await serial(async () => {
      const saved = await chrome.storage.local.get({draftActions: {}, readChats: {}});
      const used = saved.draftActions && typeof saved.draftActions === "object" ? saved.draftActions : {};
      if (Object.hasOwn(used, action.id)) { duplicate = true; return; }
      for (const id of Object.keys(used)) if (used[id] < Date.now() - OLD) delete used[id];
      used[action.id] = Date.now();
      const changes = {draftActions: used};
      if (action.kind === "read") {
        const readChats = {...saved.readChats};
        // Passive observations can arrive between the snapshot and its ACK.
        // They must never borrow the completed snapshot's receipt, even in the
        // same millisecond. Only explicit capture events receive action.id.
        Object.defineProperty(readChats, action.chat, {value: {id: action.id, passiveId: hex(crypto.getRandomValues(new Uint8Array(16))),
                              since: action.since, started: Date.now()},
                              enumerable: true, configurable: true, writable: true});
        changes.readChats = readChats;
      }
      await chrome.storage.local.set(changes); // durable BEFORE dispatch, including empty/failed reads
      persisted = true;
    });
    if (duplicate) {
      await ackAction(result("unknown", "This operation was already attempted; it will not be replayed.")); return;
    }
    if (!persisted) return;
    if (!liveClient() || liveClient().owner !== client.owner) {
      await ackAction(result("blocked", "The WhatsApp tab changed before the operation.")); return;
    }
    let timeout;
    try {
      activeAction = {id: action.id, owner: client.owner, kind: action.kind, chat: action.chat,
                      since: action.since, expires: action.expires, readUntil: 0, openGranted: false, captureBusy: false, delivered: null};
      const response = await Promise.race([
        chrome.tabs.sendMessage(client.tab, {type: "draft_action", action}, {documentId: client.document}),
        new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error("action timeout")), action.kind === "read" ? 28000 : 18000); }),
      ]);
      const states = ["opened", "drafted", "already_draft", "read", "user_active", "sent", "existing_draft", "not_found", "ambiguous_chat", "group_chat", "blocked", "expired", "failed", "unknown"];
      if (response && response.id === action.id && response.owner === client.owner && states.includes(response.status)) {
        const reading = response.status === "read" ? readingSummary(response.reading) : null;
        if (response.status !== "read" || action.kind === "read" && reading && activeAction.delivered === JSON.stringify(reading)) {
          answer = {id: action.id, owner: client.owner, status: response.status,
                    detail: typeof response.detail === "string" ? response.detail.slice(0, 800) : "",
                    ...(reading ? {reading} : {}),
                    ...(action.kind === "read" && activeAction.openGranted && Number.isInteger(response.unread_before) && response.unread_before > 0 && response.unread_before < 1000
                        ? {unread_before: response.unread_before} : {})};
        }
      }
    } finally { clearTimeout(timeout); }
    await ackAction(answer);
  } catch (e) {
    try { await ackAction(answer); } catch (ignored) { /* server lease becomes unknown; never redeliver */ }
  } finally {
    if (acquired) {
      actionBusy = false; activeAction = null;
      // The app queues the next chat after receiving this ACK. Poll briefly for
      // that fresh request instead of waiting thirty seconds per guard. These
      // polls cannot replay an action, and stop once another action is running.
      if (action.kind === "read") for (const delay of [800, 2000, 5000]) setTimeout(() => {
        if (!actionBusy && liveClient()?.owner === client.owner) flush();
      }, delay);
    }
  }
}

function flush() {
  return serial(async () => {
    try {
      const queue = await getQueue();
      await chrome.storage.local.set({queue});
      const client = liveClient();
      const beat = pendingBeat;
      if (!queue.length && !beat && !client) return;
      if (!(await isCheckCallLog())) return;    // not the real receiver: send nothing, not even the secret
      const batch = queue.slice(0, 300);
      const {res, body} = await fetchJson("/wa", {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-Reader-Token": READER.token},
        body: JSON.stringify({events: batch, beat, client}),
      });
      const taken = res.ok && !!body && body.ok === true && typeof body.kept === "number";
      if (taken && beat) lastAcceptedBeat = beat;
      const refused = res.status === 400 || res.status === 413;   // never taken: drop it rather than block the rest
      if (taken || refused) {
        const sent = new Set(batch.map(idOf));               // removed by identity: the queue may have moved since
        const now = await getQueue();
        const left = now.filter((e) => !sent.has(idOf(e)));
        await chrome.storage.local.set({queue: left});
        if (taken && pendingBeat === beat) pendingBeat = null;
        if (left.length) setTimeout(flush, 200);
      }
      if (taken && body.action) performAction(body.action, client).catch(() => {});
    } catch (e) {
      // Check Call Log is not open: keep everything for when it is
    }
  });
}

chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  if (!sender.tab || !sender.url || !sender.url.startsWith("https://web.whatsapp.com/") || !msg) return;
  if (sender.id && sender.id !== chrome.runtime.id) return;
  if (sender.frameId !== undefined && sender.frameId !== 0) return;
  const client = clientFor(sender);
  if (msg.type === "draft_authorize") {
    authorizeAction(msg, client).then(value => { if (respond) respond(value); });
    return true;
  }
  if (msg.type === "read_capture") {
    deliverReadCapture(msg, client).then(value => { if (respond) respond(value); });
    return true;
  }
  if (msg.type === "drafts_ready" && msg.capability === DRAFT_CAPABILITY && client) {
    capable.set(client.owner, Date.now());
    if (respond) respond({owner: client.owner});
    return;
  }
  if (msg.type === "events" && Array.isArray(msg.events)) {
    add(msg.events).then(flush);
  } else if (msg.type === "beat" && msg.beat) {
    pendingBeat = Object.assign({}, msg.beat, {at: Date.now()});
    currentClient = client && Date.now() - (capable.get(client.owner) || 0) < 45000 ? client : null;
    clientAt = Date.now();
    const thisBeat = pendingBeat;
    if (msg.awaited === true) {
      flush().then(() => { if (respond) respond({ok: lastAcceptedBeat === thisBeat && !!liveClient() && liveClient().owner === client?.owner}); });
      return true;
    }
    flush();
  }
});

chrome.alarms.create("flush", {periodInMinutes: 0.5});
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "flush") flush(); });
