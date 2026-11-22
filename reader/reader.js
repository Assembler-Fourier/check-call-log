// NORTHWIND check-call reader - runs inside the WhatsApp Web tab (Chrome's isolated world: the page cannot
// see it, and it never runs code in the page).
//
// READ ONLY, by construction. It never clicks, types, scrolls, focuses, changes or adds anything on the page,
// never touches WhatsApp's own code, and makes no request of its own. All it does is watch the chat list and
// tell the add-on's worker what changed; the worker hands that to Check Call Log on this PC (127.0.0.1).
//
// What it reports for each chat whose last line changes: the chat name (the phone's contact name), the line,
// whether it is ours (the read/sent ticks) or theirs, the time WhatsApp shows, and what kind it is (text, a
// reaction, a voice note, a photo...). When the controller opens a chat himself, the messages in it are
// reported too, with their exact times - that settles anything the list alone left unclear.
(() => {
  "use strict";
  if (window.__northwindReader) return;
  window.__northwindReader = true;

  const VERSION = "1.5.2";
  const BIDI = /[‪-‮‎‏⁦-⁩]/g;
  const clean = (s) => (s || "").replace(BIDI, "").replace(/\s+/g, " ").trim();
  // our own last message carries a status tick: sent / delivered / read (or the clock while it goes)
  const TICK = /^wds-ic-(read|delivered|sent|pending|clock|msg-time|msg-check|msg-dblcheck|status-check|status-dblcheck)$|(?:^|-)(dblcheck|msg-check|msg-time)$/;
  const TRANSIENT = /^(typing|recording audio|recording|online)\W*$|is typing|^draft:/i;
  const MEDIA = /^(photo|video|gif|sticker|document|location|live location|contact|poll|audio|image)\b/i;
  const CLOCK = /^(?:[01]?\d|2[0-3]):[0-5]\d$/;
  const PAYLOAD = '[data-testid="selectable-text"], .selectable-text';
  const QUOTED = '[data-testid*="quoted"], [aria-label="Quoted message"]';
  const DIRECTION_ATTRS = ["aria-label", "data-id", "data-icon", "class", "aria-hidden", "hidden"];

  let pane = null, observer = null, mainObserver = null, watchedMain = null, timer = null;
  let listDirty = false, mainDirty = false;
  let firstScan = true, checks = 0, missed = 0, dead = false;
  const state = new Map();                       // chat -> what its last line was
  const sentBubbles = new Set();                 // messages from an open chat already reported
  let activeRead = null;                         // automatic traversal is published only as one verified capture
  const bubbleKey = e => [e.chat, e.id || e.shown, e.date, e.direction, e.what, e.media_type || "", e.text, e.truncated === true,
    JSON.stringify(e.reactions || null)].join("\u0001");

  function minutesApart(hhmm) {
    const [h, m] = hhmm.split(":").map(Number), now = new Date();
    const d = Math.abs(now.getHours() * 60 + now.getMinutes() - (h * 60 + m));
    return Math.min(d, 1440 - d);
  }

  let outbox = [], outTimer = null;

  function send(msg) {
    // Events found in one look (the list and the open chat) go to the worker as ONE message, so two arriving
    // together can never race each other in its queue.
    if (dead) return;
    if (msg.type === "events") {
      outbox = outbox.concat(msg.events);
      if (!outTimer) outTimer = setTimeout(() => { outTimer = null; const e = outbox; outbox = []; post({type: "events", events: e}); }, 0);
      return;
    }
    return post(msg);
  }

  function post(msg) {
    if (dead) return;
    try {
      return chrome.runtime.sendMessage(msg).catch(() => {});
    } catch (e) {
      dead = true;                               // the add-on was reloaded: this copy of the script stops
      if (observer) observer.disconnect();
      if (mainObserver) mainObserver.disconnect();
    }
  }

  function listingAll() {
    // Only the plain chat list is read. A search, the Unread/Favourites/Groups filters or the archive show
    // old messages under chat names, and a stale "Yes" from there must never count.
    // WhatsApp can replace its labelled editor with an unlabelled native input
    // after a search starts. Read every candidate: an empty old editor must not
    // hide the populated replacement. Unknown sidebar textboxes fail closed;
    // this read-only check never uses them as permission to type or clear text.
    const searches = document.querySelectorAll('[aria-label="Search or start a new chat"], [aria-label="Search input textbox"], #side input[role="textbox"][type="text"]');
    if ([...searches].some((search) => clean(search.value !== undefined ? search.value : search.textContent))) return false;
    const chosen = document.querySelector('[aria-label="chat-list-filters"] [aria-selected="true"]');
    if (chosen && clean(chosen.textContent) && clean(chosen.textContent) !== "All") return false;
    return true;
  }

  function markers(row) {
    return [...row.querySelectorAll("svg title")].filter((e) => !e.closest(QUOTED)).map((t) => t.textContent || "")
      .concat([...row.querySelectorAll("[data-icon]")].filter((e) => !e.closest(QUOTED)).map((e) => e.getAttribute("data-icon") || ""));
  }

  function authors(row) {
    return [...row.querySelectorAll("[aria-label]")]
      .filter((e) => !e.closest(QUOTED) && !e.closest(PAYLOAD) && !e.closest('[aria-hidden="true"], [hidden]'))
      .map((e) => clean(e.getAttribute("aria-label"))).filter((l) => /^[^:]{1,120}\s*:$/.test(l))
      .map((l) => clean(l.slice(0, -1)));
  }

  function unquoted(row, selector) {
    return [...row.querySelectorAll(selector)].find((e) => !e.closest(QUOTED)) || null;
  }

  function directionOf(row, icons, sender, chat) {
    const labels = authors(row);
    const marked = (selector) => [...row.querySelectorAll(selector)].some((e) => !e.closest(QUOTED)) ||
      (!!row.closest(selector) && !row.closest(QUOTED));
    const outMark = marked('.message-out, [data-id^="true_"]');
    const inMark = marked('.message-in, [data-id^="false_"]');
    const outgoing = labels.includes("You") || icons.some((t) => t === "tail-out" || TICK.test(t)) || outMark;
    if ((outgoing && inMark) || (labels.includes("You") && labels.some((l) => l !== "You"))) return "unknown";
    const incoming = inMark || labels.includes(chat) ||
      // The pre-plain header is explicit sender information; unlike a missing tick it is positive evidence.
      (!!sender && sender === chat && !/\(you\)$/i.test(chat));
    if (outgoing) return "outgoing";
    return incoming ? "incoming" : "unknown";
  }

  function directionFields(direction) {
    return direction === "unknown" ? {direction} : {direction, ours: direction === "outgoing"};
  }

  function messageText(row) {
    // WhatsApp renders emoji as images. Read only the actual selectable payload,
    // not hidden timestamps, quoted messages, status text or media labels.
    const all = [...row.querySelectorAll(PAYLOAD)].filter((e) => !e.closest(QUOTED));
    const roots = all.filter((e) => !all.some((other) => other !== e && other.contains(e)));
    function text(node) {
      if (node.nodeType === 3) return node.nodeValue || "";
      if (node.nodeType !== 1 || node.matches(QUOTED) || node.getAttribute("aria-hidden") === "true" ||
          node.hasAttribute("hidden")) return "";
      if (node.tagName === "IMG") {
        const alt = clean(node.getAttribute("data-plain-text") || node.getAttribute("alt"));
        return /^[\p{Extended_Pictographic}\p{Regional_Indicator}\u{1F3FB}-\u{1F3FF}\uFE0F\u200D\u20E3#*0-9\u{E0020}-\u{E007F}]+$/u.test(alt) &&
          /[\p{Extended_Pictographic}\p{Regional_Indicator}\u20E3]/u.test(alt) ? alt : "";
      }
      if (node.tagName === "BR") return " ";
      return [...node.childNodes].map(text).join("");
    }
    return clean(roots.map(text).join(" "));
  }

  function explicitDay(raw) {
    const value = clean(raw), day = new Date();
    if (/^(today|yesterday)$/i.test(value)) {
      if (/^yesterday$/i.test(value)) day.setDate(day.getDate() - 1);
    } else {
      const m = value.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
      if (!m || Number(m[3]) < 1970) return "";
      day.setFullYear(Number(m[3]), Number(m[2]) - 1, Number(m[1]));
      if (day.getFullYear() !== Number(m[3]) || day.getMonth() !== Number(m[2]) - 1 || day.getDate() !== Number(m[1])) return "";
    }
    return String(day.getDate()).padStart(2, "0") + "/" + String(day.getMonth() + 1).padStart(2, "0") + "/" + day.getFullYear();
  }

  function bubbleDay(row, main, container = main.querySelector('[data-testid="conversation-panel-messages"] [data-tab="8"]')) {
    if (!container || !container.contains(row)) return "";
    let wrapper = row;
    while (wrapper.parentElement && wrapper.parentElement !== container) wrapper = wrapper.parentElement;
    for (let previous = wrapper.previousElementSibling; previous; previous = previous.previousElementSibling) {
      if (previous.getAttribute("tabindex") !== "-1" || previous.hasAttribute("role") ||
          previous.querySelector('[role="row"], [data-testid="msg-container"], [data-testid^="conv-msg-"], [data-pre-plain-text], ' + PAYLOAD)) continue;
      // A distinct, non-message separator is required. An unknown separator is a
      // boundary: never borrow an older date across it or from a quoted "Today".
      return explicitDay(previous.innerText);
    }
    return "";
  }

  function readRow(row) {
    const titled = row.querySelectorAll("span[title]");
    const chat = clean(titled[0] && titled[0].getAttribute("title"));
    if (!chat) return null;
    const raw = clean(titled[1] && titled[1].getAttribute("title"));
    const icons = markers(row);
    const lines = (row.innerText || "").split("\n").map((s) => s.trim()).filter(Boolean);
    if (lines.some((s) => /^draft\s*:/i.test(s))) return null;  // unsent drafts are not messages, even without a preview title
    const shown = lines.find((s) => CLOCK.test(s)) || "";
    const badge = row.querySelector('[aria-label*="unread"]');
    const out = {chat, text: raw, what: "text", ...directionFields(directionOf(row, icons, "", chat)), read: icons.some((t) => /read$/.test(t)),
                 shown, unread: badge ? (parseInt(badge.textContent, 10) || 1) : 0,
                 truncated: raw.length > 300,
                 // a group (or community, broadcast) is never one guard's answer: anyone in it can rename it
                 group: icons.some((t) => /group|community|broadcast/i.test(t))};
    const r = raw.match(/^(you )?reacted (.+?) to:?\s*"?(.*?)"?$/i);
    if (r) {
      Object.assign(out, {what: "reaction", emoji: r[2].trim(), to: r[3].trim(), text: "",
                         ...directionFields(r[1] || out.direction === "outgoing" ? "outgoing" : out.direction)});
    } else if (icons.some((t) => /mic|ptt|audio|voice/.test(t)) || /^\d:\d{2}$/.test(raw)) {
      out.what = "voice";                        // a voice note shows its length ("0:03"); "18:00" is a time someone typed
    } else if (MEDIA.test(raw) || icons.some((t) => /image|video|document|sticker|gif|location|contact/.test(t))) {
      out.what = "media";
      if (/^Sticker$/i.test(raw)) out.media_type = "sticker";
    } else if (/^(you deleted this message|this message was deleted)$/i.test(raw)) {
      out.what = "deleted";
    }
    if (!out.text && out.what === "text") return null;  // an incomplete DOM row has no message to report
    return out;
  }

  function scan(reason) {
    if (dead) return;
    if (!pane || !document.contains(pane) || document.querySelector("#pane-side") !== pane) attach();
    if (!pane || !listingAll()) return;
    const now = Date.now(), out = [];
    for (const row of pane.querySelectorAll('[role="row"]')) {
      const r = readRow(row);
      if (!r || !CLOCK.test(r.shown) || TRANSIENT.test(r.text)) continue;    // only today's, never "typing..."
      const sig = [r.text, r.what, r.media_type || "", r.direction, r.shown, r.emoji || "", r.truncated === true].join("\u0001");
      if (state.get(r.chat) === sig) continue;
      state.set(r.chat, sig);
      // A line that is new now carries the time it is now; anything older was already there before this
      // script looked (or scrolled into view), and Check Call Log treats it with more care.
      const fresh = !firstScan && minutesApart(r.shown) <= 2;
      if (reason === "check" && fresh) missed += 1;
      out.push({kind: "line", chat: r.chat.slice(0, 120), ...directionFields(r.direction), text: r.text.slice(0, 300), what: r.what, shown: r.shown,
                seen: now, unread: r.unread, read: r.read, emoji: r.emoji, to: (r.to || "").slice(0, 120), group: r.group,
                truncated: r.truncated, ...(r.media_type ? {media_type: r.media_type} : {}),
                snapshot: !fresh, missed: reason === "check"});
    }
    firstScan = false;
    if (out.length) send({type: "events", events: out});
  }

  function captureOpenChat(reactionProofs) {
    // Pure DOM read, also used by an explicitly authorized read action in our
    // isolated world. It never opens, scrolls or changes the conversation itself.
    const main = document.querySelector("#main");
    if (!main || dead) return null;
    const header = main.querySelector("header"), head = header && header.querySelector("span[dir='auto']");
    const chat = clean(head && head.textContent);
    if (!chat) return null;
    const headerGroup = !!header && markers(header).some(s => /group|community|broadcast/i.test(s));
    let dayContainer;
    const viewId = [...crypto.getRandomValues(new Uint8Array(16))].map(n => n.toString(16).padStart(2, "0")).join("");
    const out = [], captured = new Set(), reactionTargets = [];
    const inspectReactions = activeRead && activeRead.chat === chat && reactionProofs && typeof reactionProofs.get === "function";
    let unresolved = 0;
    const unresolvedAt = [], unresolvedRows = [];
    const chronology = [];
    [...main.querySelectorAll('[role="row"]')].forEach((row, i) => {
      if (row.closest(QUOTED)) return;
      const el = unquoted(row, "[data-pre-plain-text]"), pre = el ? el.getAttribute("data-pre-plain-text") || "" : "";
      const message = unquoted(row, '[data-testid^="conv-msg-"]');
      if (!el && !message && !unquoted(row, PAYLOAD)) return;  // date/unread separators are not messages
      const m = pre.match(/\[(\d{1,2}:\d{2}), (\d{1,2}\/\d{1,2}\/\d{4})\]/);
      const meta = unquoted(row, '[data-testid="msg-meta"]');
      const clock = clean(meta && meta.innerText).match(/(?:^|\s)((?:[01]?\d|2[0-3]):[0-5]\d)(?=\s|$)/);
      const shown = m ? m[1] : clock ? clock[1] : "";
      if (!m && dayContainer === undefined) dayContainer = main.querySelector('[data-testid="conversation-panel-messages"] [data-tab="8"]');
      const date = m ? explicitDay(m[2]) : bubbleDay(row, main, dayContainer);
      if (!CLOCK.test(shown) || !date) {
        chronology.push({at: null, gap: unresolvedAt.length}); unresolved++; unresolvedAt.push(null); unresolvedRows.push(null); return;
      }  // no guessed historical date
      const [day, month, year] = date.split("/").map(Number), [hour, minute] = shown.split(":").map(Number);
      const at = new Date(year, month - 1, day, hour, minute).getTime();
      chronology.push({at});
      // In a one-to-one chat the sender of an incoming message IS the chat; in a group it is somebody else.
      const named = authors(row).filter((name) => name !== "You");
      const sender = clean((pre.match(/\]\s*(.*?):\s*$/) || [])[1] || (named.length === 1 ? named[0] : ""));
      const icons = markers(row), direction = directionOf(row, icons, sender, chat);
      const sticker = unquoted(row, '[data-testid="sticker-container"]');
      const fullText = messageText(row), text = fullText.slice(0, 300), truncated = fullText.length > 300;
      let what = "text";
      if (/^(you deleted this message|this message was deleted)$/i.test(text)) what = "deleted";
      else if (icons.some(t => /mic|ptt|audio|voice/.test(t))) what = "voice";
      else if (unquoted(row, '[data-testid="image-thumb"][aria-label="Open picture"], [data-testid="sticker-container"]') ||
        icons.some(t => /image|video|document|sticker|gif|location|contact/.test(t))) what = "media";
      if (!text && what === "text") { unresolved++; unresolvedAt.push(at); unresolvedRows.push(null); return; }
      const id = message ? (message.getAttribute("data-id") || "").slice(0, 200) : "";
      const group = /@g\.us|@broadcast/.test(id) || headerGroup ||
        (direction !== "outgoing" && (!sender || sender !== chat));
      const mediaType = sticker && what === "media" && !text ? "sticker" : "";
      const key = [chat, id || shown, date, direction, what, mediaType, text, truncated].join("\u0001");
      if (captured.has(key)) return;
      captured.add(key);
      const chips = [...row.querySelectorAll('[data-testid="reaction-bubble"]')].filter(n => !n.closest(QUOTED));
      const hasReaction = chips.length > 0 || icons.some(t => /reaction/.test(t));
      const reactionKey = JSON.stringify([key, chips.map(n => [n.getAttribute("aria-label"),
        [...n.querySelectorAll("img[alt]")].map(img => img.getAttribute("alt"))])]);
      let reactions;
      if (inspectReactions && id) {
        const proof = reactionProofs.get(id);
        reactions = !hasReaction ? {complete: true, items: []} :
          proof && proof.key === reactionKey && proof.row === row && chips.length === 1 && proof.chip === chips[0] ?
            proof.snapshot : {complete: false, items: []};
        if (hasReaction && reactions.complete !== true) reactionTargets.push({id, at, key: reactionKey});
      }
      // A dated, identified voice/photo is captured context, not a positive reply.
      // Its typed event stays manual where relevant; a deleted body is genuinely missing.
      const otherGap = truncated || group || what === "deleted" || hasReaction && (!reactions || reactions.complete !== true);
      if (otherGap || direction === "unknown") {
        unresolved++; unresolvedAt.push(at);
        // Local capture metadata only: a later identical stable message may resolve
        // sender hydration. Missing content/date, groups and other gaps remain sticky.
        unresolvedRows.push(!otherGap && direction === "unknown" && id ? id : null);
      }
      out.push({kind: "bubble", chat: chat.slice(0, 120), ...directionFields(direction), text, what, shown, group,
                date, seen: Date.now(), i, ...(id ? {id, ...(i <= 10000 ? {view_id: viewId} : {})} : {}), truncated,
                ...(reactions ? {reactions} : {}), ...(mediaType ? {media_type: mediaType} : {})});
    });
    // An undated row before a proven old row cannot be newer than that row,
    // provided every observed dated row remains chronological. This is only an
    // upper bound for coverage; it never invents a message date or emits text.
    const known = chronology.filter(row => row.at !== null).map(row => row.at);
    if (known.every((at, i) => at <= Date.now() + 5000 && (!i || at >= known[i - 1]))) {
      let next = null;
      for (let i = chronology.length - 1; i >= 0; i--) {
        if (chronology[i].at !== null) next = chronology[i].at;
        else if (next !== null) unresolvedAt[chronology[i].gap] = next;
      }
    }
    if (activeRead && activeRead.chat === chat) for (const event of out) {
      activeRead.seen.add(bubbleKey(event));
      activeRead.seen.add(bubbleKey({...event, reactions: undefined})); // passive omission is not a new reaction snapshot
    }
    return {chat, events: out, unresolved, unresolvedAt, unresolvedRows, reactionTargets};
  }

  function scanOpenChat() {
    if (dead) return;
    if (activeRead) {
      const main = document.querySelector("#main"), head = main && main.querySelector("header span[dir='auto']");
      if (head && clean(head.textContent) === activeRead.chat) return;
    } // the explicit read snapshots own this chat; avoid parsing pages only to discard them
    const capture = captureOpenChat();
    if (!capture) return;
    if (activeRead && activeRead.chat === capture.chat) return;  // publish this automatic history only with its verified capture
    const out = capture.events.filter(e => {
      const key = bubbleKey(e);
      if (sentBubbles.has(key)) return false;
      sentBubbles.add(key); return true;
    });
    if (sentBubbles.size > 5000) sentBubbles.clear();
    if (out.length) send({type: "events", events: out});
  }

  function outgoingSnapshot(expectedChat) {
    // Send verification is separate from the capped/normalized observer payload.
    // Keep full visible text and stable DOM order; never turn a matching prefix,
    // old newly loaded bubble, incoming message or missing ID into a sent receipt.
    const main = document.querySelector("#main"), capture = captureOpenChat();
    if (!main || !capture || capture.chat !== expectedChat) return null;
    const nodes = [...main.querySelectorAll('[data-testid^="conv-msg-"][data-id]')].filter(n => !n.closest(QUOTED));
    const ids = nodes.map(n => n.getAttribute("data-id"));
    if (ids.some(id => !id || id.length > 200) || new Set(ids).size !== ids.length) return null;
    function exact(node, boundary = true) {
      if (node.nodeType === 3) return node.nodeValue || "";
      if (node.nodeType !== 1 || node.matches(QUOTED) || node.getAttribute("aria-hidden") === "true" || node.hasAttribute("hidden")) return "";
      if (node.tagName === "IMG") {
        const value = node.getAttribute("data-plain-text") || node.getAttribute("alt") || "";
        return /^[\p{Extended_Pictographic}\p{Regional_Indicator}\u{1F3FB}-\u{1F3FF}\uFE0F\u200D\u20E3#*0-9\u{E0020}-\u{E007F}]+$/u.test(value) &&
          /[\p{Extended_Pictographic}\p{Regional_Indicator}\u20E3]/u.test(value) ? value : null;
      }
      if (node.tagName === "BR") return "\n";
      const parts = [...node.childNodes].map(child => exact(child));
      if (parts.some(part => part === null)) return null;
      const siblings = node.parentElement && [...node.parentElement.childNodes], hasNext = siblings && siblings.indexOf(node) < siblings.length - 1;
      return parts.join("") + (boundary && /^(DIV|P)$/.test(node.tagName) && hasNext ? "\n" : "");
    }
    const messages = [];
    for (const event of capture.events) {
      if (event.direction !== "outgoing" || event.group !== false || event.what !== "text" || !event.id || !ids.includes(event.id)) continue;
      const row = nodes[ids.indexOf(event.id)].closest('[role="row"]');
      if (!row) continue;
      const all = [...row.querySelectorAll(PAYLOAD)].filter(n => !n.closest(QUOTED));
      const roots = all.filter(n => !all.some(other => other !== n && other.contains(n)));
      if (roots.length !== 1) continue;
      const text = exact(roots[0], false);
      if (!text || text.length > 4096) continue;
      const [day, month, year] = event.date.split("/").map(Number), [hour, minute] = event.shown.split(":").map(Number);
      messages.push({id: event.id, text, at: new Date(year, month - 1, day, hour, minute).getTime()});
    }
    return {chat: capture.chat, ids, messages};
  }

  // Same-extension isolated-world hook only; no page API or network access.
  window.__northwindReadCapture = Object.freeze({capture: captureOpenChat, outgoingSnapshot, refresh: () => beat(true),
    begin(id, chat) {
      if (activeRead || !/^[a-f0-9]{32}$/.test(id || "") || typeof chat !== "string" || !chat) return false;
      activeRead = {id, chat, seen: new Set()}; return true;
    },
    end(id) {
      if (!activeRead || activeRead.id !== id) return false;
      if (sentBubbles.size + activeRead.seen.size > 5000) sentBubbles.clear();
      for (const key of activeRead.seen) sentBubbles.add(key);
      activeRead = null; return true;
    }});

  function schedule(part) {
    if (part === "list") listDirty = true;
    else mainDirty = true;
    if (timer !== null) return;                    // continuous DOM updates cannot postpone a reply indefinitely
    timer = setTimeout(() => {
      timer = null;
      const replaced = watchMain(), list = listDirty, main = mainDirty;
      listDirty = mainDirty = false;
      if (list) scan("change");
      if (main || replaced) scanOpenChat();
    }, 250);
  }

  function attach() {
    const found = document.querySelector("#pane-side");
    if (found === pane) return;
    if (observer) observer.disconnect();
    pane = found;
    if (!pane) return;
    observer = new MutationObserver(() => schedule("list"));
    observer.observe(pane, {subtree: true, childList: true, characterData: true, attributes: true,
                            attributeFilter: ["title", ...DIRECTION_ATTRS]});
  }

  function watchMain() {
    const main = document.querySelector("#main");
    if (main === watchedMain) return false;
    if (mainObserver) mainObserver.disconnect();
    watchedMain = main;
    if (!main) { mainObserver = null; return true; }
    mainObserver = new MutationObserver(() => schedule("main"));
    mainObserver.observe(main, {subtree: true, childList: true, characterData: true, attributes: true,
                                attributeFilter: ["data-pre-plain-text", ...DIRECTION_ATTRS]});
    return true;
  }

  function beat(awaited = false) {
    if (dead) return;
    checks += 1;
    scan("check");                               // anything the watching missed is found, and counted
    scanOpenChat();                               // a quiet/replaced open chat need not cause an observed mutation
    watchMain();
    const useHere = [...document.querySelectorAll("button, div[role='button']")].some((b) => /^use here$/i.test(clean(b.textContent)));
    return send({type: "beat", ...(awaited === true ? {awaited: true} : {}), beat: {version: VERSION, pane: !!pane, rows: pane ? pane.querySelectorAll('[role="row"]').length : 0,
                               chats: state.size, paused: !!pane && !listingAll(), visible: document.visibilityState,
                               focused: document.hasFocus(), useHere, missed, checks}});
  }

  attach();
  scan("first");
  scanOpenChat();                                 // include the chat the controller already had open at startup
  watchMain();                                   // observe immediately, not only at the first delayed heartbeat
  setInterval(beat, 30000);
  setTimeout(beat, 3000);
})();
