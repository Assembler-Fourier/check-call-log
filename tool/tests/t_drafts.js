// Synthetic executor/DOM/worker tests. No live browser, contacts, HTTP receiver or filesystem data is used.
"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const {webcrypto, createHmac} = require("node:crypto");
const reader = path.resolve(__dirname, "../../reader");
const {executor, browserAdapter, readHistory, bubbleTime} = require(path.join(reader, "drafts.js"));
let passed = 0;
function check(label, value) { assert.ok(value, label); passed++; }
let sequence = 0;
const owner = "3:synthetic-document-1";
function command(fields = {}) {
  return Object.assign({id: (++sequence).toString(16).padStart(32, "0"), kind: "draft", chat: "Alex Example", text: "All okay for Example Hall tonight? 👍", created: 1000000, expires: 1090000, owner, phone: "353870000001"}, fields);
}
function fixture(fields = {}) {
  const state = Object.assign({now: 1000000, safe: true, all: true, search: "", available: true, count: 1, group: false,
    direct: true, phone: "353870000001", targetText: "", currentChat: "Previous Example", oldDraft: "My existing unsent draft",
    opened: 0, inserted: 0, waits: 0, searches: [], headerWrong: false, onWait: null,
    plain: true, pendingContext: false, currentChecks: 0, onCurrent: null,
    permissions: [], denyStep: null, onAuthorize: null, beforeInsert: null, beforeSearch: null, beforeOpen: null, rowNode: {}, interrupted: false,
    profilePossible: false, profileCurrent: false, profileBlocked: false, profileOpened: 0, identity: "jid", onProfile: null}, fields);
  const ui = {
    shown: () => state.shown !== false,
    beginRead: () => { state.readBegun = true; return true; }, endRead: () => { state.readEnded = true; },
    watch: () => () => { state.stopped = true; }, safe: () => state.safe && !state.pendingContext && !state.interrupted, all: () => state.all,
    searchText: () => state.search, canCleanSearch: () => !state.interrupted,
    search: (value, authorized) => { if (state.beforeSearch) state.beforeSearch(state); if (authorized) authorized(); if (state.cleanupFails && value === "") throw new Error("synthetic cleanup failure"); state.search = value; state.searches.push(value); },
    rows: () => state.available || state.search ? Array.from({length: state.count}, () => ({node: state.rowNode, group: state.group, unread: state.unread || 0})) : [],
    interrupted: () => !!state.interrupted,
    outgoingSnapshot: () => state.countUnreadable ? null : state.snapshot ? state.snapshot(state) :
      {ids: ["baseline-message", ...(state.sentMessages || []).map(m => m.id)], messages: state.sentMessages || []},
    pressSend: (_, text, authorized) => { if (authorized) authorized(); state.pressed = (state.pressed || 0) + 1;
      if (!state.sendIgnored) { state.outgoing = (state.outgoing || 0) + 1; state.targetText = "";
        (state.sentMessages ||= []).push({id: "sent-" + state.outgoing, text, at: Math.floor(state.now / 60000) * 60000}); } },
    open: (_, authorized) => { if (state.beforeOpen) state.beforeOpen(state); if (authorized) authorized(); state.opened++; state.currentChat = state.headerWrong ? "Wrong Example" : "Alex Example"; },
    openProfile: (_, authorized) => {
      if (!state.profilePossible) throw Object.assign(new Error("Synthetic profile unavailable"), {status: "blocked"});
      if (authorized) authorized();
      state.profileOpened++;
      if (state.onProfile) state.onProfile(state);
      else { state.direct = true; state.identity = "profile"; state.profileCurrent = true; }
    },
    current: () => {
      state.currentChecks++;
      if (state.onCurrent) state.onCurrent(state);
      return {chat: state.currentChat, text: state.currentChat === "Previous Example" ? state.oldDraft : state.targetText,
        plain: state.plain, direct: state.direct, group: state.group, phone: state.phone,
        identity: state.identity, profileCurrent: state.profileCurrent, profileBlocked: state.profileBlocked,
        profilePhone: state.profilePhone || null, numericConflict: !!state.numericConflict};
    },
    insert: (_, text, authorized) => { if (state.beforeInsert) state.beforeInsert(state); if (authorized) authorized(); state.inserted++; state.targetText = text; if (state.insertThrows) throw new Error("synthetic partial insert"); },
    wait: async ms => { state.now += ms; state.waits++; if (state.onWait) state.onWait(state); },
  };
  const authorize = async request => {
    state.permissions.push(request.step);
    if (state.onAuthorize) state.onAuthorize(state, request);
    return state.denyStep === request.step ? {ok: false} : {...request, ok: true, expires: state.now + 2000};
  };
  return {state, ui, run: executor(ui, () => state.now, authorize)};
}

async function executorChecks() {
  let f = fixture(), c = command(), r = await f.run(c, owner);
  check("draft is verified and stays unsent", r.status === "drafted" && f.state.targetText === c.text && f.state.inserted === 1);
  check("opening another chat leaves its existing draft untouched", f.state.oldDraft === "My existing unsent draft" && f.state.opened === 1);
  check("duplicate command never types again", (await f.run(c, owner)).status === "unknown" && f.state.inserted === 1);
  check("interaction listeners cleaned up", f.state.stopped);
  check("open and insertion each require their own authorization", f.state.permissions.join(",") === "open,draft");
  // 8 Nov: Lena asked to really send - ONE reviewed message, when he presses Send for that guard.
  const sendCmd = (fields = {}) => command(Object.assign({kind: "send"}, fields));
  f = fixture(); r = await f.run(sendCmd(), owner);
  check("send: empty box -> typed, Send pressed once, reported sent only when WhatsApp shows it",
        r.status === "sent" && f.state.inserted === 1 && f.state.pressed === 1 && f.state.permissions.join(",") === "open,draft,send");
  c = sendCmd(); f = fixture({targetText: c.text}); r = await f.run(c, owner);
  check("send: his reviewed text already in the box -> not typed again, just sent", r.status === "sent" && f.state.inserted === 0 && f.state.pressed === 1
        && f.state.permissions.join(",") === "open,send");
  f = fixture({targetText: "Something else he was writing"}); r = await f.run(sendCmd(), owner);
  check("send: a DIFFERENT message in the box -> left alone, nothing sent", r.status === "existing_draft" && !f.state.pressed
        && f.state.targetText === "Something else he was writing");
  const ignored = sendCmd(); f = fixture({sendIgnored: true}); r = await f.run(ignored, owner);
  check("send: pressed but WhatsApp does not show it -> 'check the chat', never retried", r.status === "unknown" && f.state.pressed === 1
        && /not be sent again automatically/.test(r.detail));
  r = await f.run(ignored, owner);
  check("...and the same request can never send twice", r.status === "unknown" && f.state.pressed === 1);
  f = fixture({countUnreadable: true}); r = await f.run(sendCmd(), owner);
  check("send: messages cannot be read to confirm -> NOT sent", r.status === "blocked" && !f.state.pressed && /NOT sent/.test(r.detail));
  f = fixture({denyStep: "send"}); r = await f.run(sendCmd(), owner);
  check("send: the app withdraws permission before Send -> Send is not pressed", !f.state.pressed && r.status !== "sent");
  f = fixture(); r = await f.run(sendCmd({phone: null}), owner);
  check("send: no verified phone -> refused before anything", r.status === "blocked" && f.state.opened === 0 && !f.state.pressed);
  for (const [label, fields, expected] of [
    ["LID without numeric proof", {phone: null}, "blocked"],
    ["LID with correct Contact info", {phone: null, profilePossible: true, onProfile: s => { s.profileCurrent = true; s.profilePhone = "353870000001"; }}, "sent"],
    ["LID with different Contact info", {phone: null, profilePossible: true, onProfile: s => { s.profileCurrent = true; s.profilePhone = "353870000002"; }}, "blocked"],
    ["conflicting numeric identities", {numericConflict: true}, "blocked"],
    ["JID conflicts with visible Contact info", {profileCurrent: true, profilePhone: "353870000002"}, "blocked"],
  ]) {
    f = fixture(fields); r = await f.run(sendCmd(), owner);
    check("send requires actual saved-number proof: " + label, r.status === expected && (expected === "sent" ? f.state.pressed === 1 : !f.state.pressed && f.state.inserted === 0));
  }
  for (const [label, change] of [
    ["different long-body suffix", (s, text) => ({ids: ["baseline-message", "new-bubble"], messages: [{id: "new-bubble", text: text.slice(0, 300) + "DIFFERENT", at: Math.floor(s.now / 60000) * 60000}]})],
    ["older newly loaded identical text", (s, text) => ({ids: ["baseline-message", "old-bubble"], messages: [{id: "old-bubble", text, at: s.now - 60000}]})],
    ["same-minute older bubble before original tail", (s, text) => ({ids: ["old-bubble", "baseline-message"], messages: [{id: "old-bubble", text, at: Math.floor(s.now / 60000) * 60000}]})],
    ["same old stable ID changes body", (s, text) => ({ids: ["baseline-message"], messages: [{id: "baseline-message", text, at: Math.floor(s.now / 60000) * 60000}]})],
  ]) {
    c = sendCmd({text: "a".repeat(330) + " exact ending"});
    f = fixture({snapshot: s => s.pressed ? change(s, c.text) : {ids: ["baseline-message"], messages: []}});
    r = await f.run(c, owner);
    check("send cannot claim success from " + label, r.status === "unknown" && f.state.pressed === 1);
  }
  c = sendCmd({text: "First line\n" + "👍".repeat(170) + "\nSecond line"}); f = fixture(); r = await f.run(c, owner);
  check("send verifies the full long multiline message rather than its first300 characters", r.status === "sent" && f.state.pressed === 1);
  // 7 Nov live: a hidden tab (minimised / covered) is slowed by Chrome; the search ran out of time half-way.
  f = fixture({shown: false}); r = await f.run(command(), owner);
  check("WhatsApp not on screen: refused before anything is touched", r.status === "blocked" && /not on screen/.test(r.detail) &&
        f.state.opened === 0 && f.state.inserted === 0 && f.state.searches.length === 0 && f.state.permissions.length === 0);
  f = fixture({shown: false}); r = await f.run(command({kind: "open", text: null}), owner);
  check("...an open request too", r.status === "blocked" && f.state.opened === 0);
  f = fixture({available: false, onAuthorize: (s, q) => { if (q.step === "open") s.shown = false; }}); r = await f.run(command(), owner);
  check("WhatsApp hidden part-way: stops, says why, types nothing, and clears the search it typed",
        r.status === "blocked" && /request stopped/.test(r.detail) && f.state.opened === 0 && f.state.inserted === 0 &&
        f.state.searches[0] === "Alex Example" && f.state.search === "");
  // 8 Nov review: a request started while Lena typed in WhatsApp could switch chats under him (keys into a guard's box).
  f = fixture(); f.ui.userBusy = () => true; r = await f.run(command(), owner);
  check("using WhatsApp: a draft request does nothing at all and says why", r.status === "user_active" && /using WhatsApp/.test(r.detail) &&
        f.state.opened === 0 && f.state.inserted === 0 && f.state.searches.length === 0 && f.state.permissions.length === 0 && !f.state.stopped);
  {
    const listeners = {}, prevented = [], doc = {visibilityState: "visible", hasFocus: () => focused,
      addEventListener: (kind, fn) => { (listeners[kind] = listeners[kind] || []).push(fn); },
      removeEventListener: (kind, fn) => { listeners[kind] = (listeners[kind] || []).filter(x => x !== fn); }};
    let focused = true;
    const fire = (type, trusted = true) => { const e = {type, isTrusted: trusted, preventDefault: () => prevented.push(type + ":default"),
      stopImmediatePropagation: () => prevented.push(type + ":stop")}; for (const fn of listeners[type] || []) fn(e); };
    const ui = browserAdapter(doc, {});
    check("idle WhatsApp is not 'in use'", ui.userBusy() === false);
    fire("keydown", false);
    check("a key the page made itself does not count as him", ui.userBusy() === false);
    fire("keydown");
    check("his key in WhatsApp marks it in use (before any request starts)", ui.userBusy() === true);
    focused = false;
    check("...but not while he is working in another window", ui.userBusy() === false);
    focused = true;
    const stopWatch = ui.watch(); prevented.length = 0;
    fire("keydown");
    check("while a request runs his key (Enter too) is held back, so it cannot reach a guard's message box",
          prevented.includes("keydown:default") && prevented.includes("keydown:stop") && !ui.safe() && /clicked or typed/.test(ui.safeReason()));
    prevented.length = 0; fire("pointerdown");
    check("...a click is not held back (it only stops the request)", prevented.length === 0);
    stopWatch();
    const blurred = [], el = (inside) => ({blur: () => blurred.push(inside), closest: sel => (inside && sel === "#main footer") ? {} : null, matches: () => false});
    doc.activeElement = el(true); ui.releaseFocus();
    doc.activeElement = el(false); ui.releaseFocus();
    check("after a read the cursor leaves a guard's message box, and only that", blurred.join(",") === "true");
  }
  check("the real page answers from Chrome's own visibility", browserAdapter({visibilityState: "hidden"}, {}).shown() === false &&
        browserAdapter({visibilityState: "visible"}, {}).shown() === true);
  f = fixture({denyStep: "open"}); r = await f.run(command(), owner);
  check("cancelled or closed app before opening cannot change the chat", r.status === "blocked" && f.state.opened === 0 && f.state.inserted === 0);
  for (const [label, change] of [
    ["expired opening grant", s => { s.now += 2001; }],
    ["expired operation deadline", s => { s.now += 12001; }],
    ["new trusted interaction", s => { s.interrupted = true; }],
    ["replacement exact row", s => { s.rowNode = {}; }],
    ["new duplicate exact row", s => { s.count = 2; }],
    ["new group identity", s => { s.group = true; }],
  ]) {
    f = fixture({beforeOpen: change}); r = await f.run(command(), owner);
    check(label + " at activation boundary prevents opening and insertion", ["blocked", "expired"].includes(r.status) && f.state.opened === 0 && f.state.inserted === 0);
  }
  f = fixture({denyStep: "draft"}); r = await f.run(command(), owner);
  check("cancellation after opening prevents draft insertion", r.status === "blocked" && f.state.opened === 1 && f.state.inserted === 0);
  f = fixture({beforeInsert: s => { s.now += 2001; }}); r = await f.run(command(), owner);
  check("permit expiration at the actual input boundary prevents typing", r.status === "unknown" && f.state.inserted === 0);
  f = fixture({onAuthorize: (s, request) => { if (request.step === "draft") s.targetText = "User wrote a draft"; }}); r = await f.run(command(), owner);
  check("user text entered during authorization is preserved", r.status === "existing_draft" && f.state.targetText === "User wrote a draft" && f.state.inserted === 0);
  f = fixture(); r = await f.run(command({kind: "open", text: null}), owner);
  check("open operation never types", r.status === "opened" && f.state.inserted === 0);
  f = fixture({targetText: "Other shift draft"}); r = await f.run(command(), owner);
  check("same person second shift never overwrites first draft", r.status === "existing_draft" && f.state.targetText === "Other shift draft" && f.state.inserted === 0);
  c = command(); f = fixture({targetText: c.text}); r = await f.run(c, owner);
  check("identical draft is a verified no-op", r.status === "already_draft" && f.state.inserted === 0);
  f = fixture({plain: false}); r = await f.run(command(), owner);
  check("blank target in a special composer mode cannot receive a new draft", r.status === "blocked" && f.state.inserted === 0);
  c = command(); f = fixture({plain: false, targetText: c.text}); r = await f.run(c, owner);
  check("matching text in reply or edit mode is not an already prepared plain draft", r.status === "blocked" && f.state.inserted === 0);
  f = fixture({pendingContext: true}); r = await f.run(command(), owner);
  check("opening does not disturb a pending special mode in the previous chat", r.status === "blocked" && f.state.opened === 0 && f.state.inserted === 0);
  f = fixture({pendingContext: true});
  f.ui.safeReason = () => "Visible media outside the chat history or message box could be an attachment preview. The request stopped.";
  r = await f.run(command(), owner);
  check("executor reports the adapter's precise safety reason without opening or typing", r.status === "blocked" && r.detail === f.ui.safeReason() && f.state.opened === 0 && f.state.inserted === 0);
  f = fixture({safe: false}); f.ui.safeReason = () => "x".repeat(401); r = await f.run(command(), owner);
  check("unexpected oversized safety diagnostics fall back to bounded generic wording", r.status === "blocked" && r.detail.length < 150 && f.state.opened === 0 && f.state.inserted === 0);
  f = fixture({onCurrent: s => { if (s.currentChecks === 3) s.plain = false; }}); r = await f.run(command(), owner);
  check("context transition at final pre-input check blocks insertion", r.status === "blocked" && f.state.inserted === 0);
  f = fixture({onWait: s => { s.plain = false; }}); r = await f.run(command(), owner);
  check("context transition after insertion cannot falsely report draft success", r.status === "unknown");
  for (const [fields, status] of [[{count: 2}, "ambiguous_chat"], [{count: 0}, "not_found"], [{group: true}, "group_chat"],
    [{direct: false}, "blocked"], [{phone: "353870000002"}, "blocked"], [{headerWrong: true}, "blocked"],
    [{all: false}, "blocked"], [{search: "User search"}, "blocked"], [{safe: false}, "blocked"]]) {
    f = fixture(fields); r = await f.run(command(), owner);
    check("unsafe target rejected: " + JSON.stringify(fields), r.status === status && f.state.inserted === 0);
    if (fields.search) check("existing user search preserved", f.state.search === fields.search);
  }
  f = fixture({available: false}); r = await f.run(command(), owner);
  check("exact name search returns to All after draft", r.status === "drafted" && f.state.searches.join("|") === "Alex Example|" && f.state.search === "");
  check("search has a separate grant before opening and drafting", f.state.permissions.join(",") === "search,open,draft");
  f = fixture({available: false, denyStep: "search"}); r = await f.run(command(), owner);
  check("closed or cancelled app cannot even type the chat search", r.status === "blocked" && f.state.searches.length === 0 && f.state.opened === 0);
  f = fixture({available: false, beforeSearch: s => { s.now += 2001; }}); r = await f.run(command(), owner);
  check("expired search grant prevents search input", r.status === "blocked" && f.state.searches.length === 0);
  f = fixture({available: false, onAuthorize: (s, request) => { if (request.step === "search") s.search = "User search"; }}); r = await f.run(command(), owner);
  check("new user search during authorization is never overwritten", r.status === "blocked" && f.state.search === "User search" && f.state.searches.length === 0);
  f = fixture({available: false, count: 0, onWait: s => { s.interrupted = true; }}); r = await f.run(command(), owner);
  check("trusted input preserves user-owned search even when its text equals our own", r.status === "blocked" && f.state.search === "Alex Example" && f.state.searches.length === 1 && /All chats/.test(r.detail));
  f = fixture({available: false, cleanupFails: true}); c = command(); r = await f.run(c, owner);
  check("failed search cleanup preserves verified draft success and gives recovery guidance", r.status === "drafted" && f.state.targetText === c.text && f.state.search === c.chat && /All chats/.test(r.detail));
  check("cleanup warning does not replace the verified draft detail", /reviewed text/.test(r.detail) && /search box/.test(r.detail));
  f = fixture({available: false, cleanupFails: true}); r = await f.run(command({kind: "open", text: null}), owner);
  check("verified open stays successful even when search cleanup fails", r.status === "opened" && f.state.inserted === 0 && /Nothing was typed or sent/.test(r.detail) && /All chats/.test(r.detail));
  c = command(); f = fixture({available: false, cleanupFails: true, targetText: c.text}); r = await f.run(c, owner);
  check("identical existing draft stays verified when search cleanup fails", r.status === "already_draft" && f.state.inserted === 0 && /already present/.test(r.detail) && /search box/.test(r.detail));
  f = fixture({available: false, cleanupFails: true, onAuthorize: (s, q) => { if (q.step === "open") s.shown = false; }});
  r = await f.run(command(), owner);
  check("hidden-tab root cause survives a failed search cleanup", r.status === "blocked" && /Show the WhatsApp tab/.test(r.detail) && /search box/.test(r.detail) && f.state.inserted === 0);
  f = fixture({available: false, beforeSearch: s => { if (s.search === "Alex Example") s.search = "User restored query"; }});
  c = command(); r = await f.run(c, owner);
  check("focus-restored query immediately before cleanup is preserved",
    r.status === "drafted" && f.state.search === "User restored query" && f.state.searches.length === 1 && f.state.targetText === c.text && /All chats/.test(r.detail));
  f = fixture({available: false, beforeSearch: s => { if (s.search === "Alex Example") s.interrupted = true; }});
  r = await f.run(command(), owner);
  check("interaction during cleanup focus preserves even our exact query",
    r.status === "drafted" && f.state.search === "Alex Example" && f.state.searches.length === 1 && /All chats/.test(r.detail));
  for (const mode of ["microtask", "later", "ignored", "user-change", "hidden"]) {
    f = fixture({available: false});
    const current = f, originalSearch = f.ui.search;
    let clearAttempts = 0;
    f.ui.search = (value, authorized) => {
      if (value) return originalSearch(value, authorized);
      if (authorized) authorized();
      clearAttempts++;
      if (mode === "microtask") queueMicrotask(() => { current.state.search = ""; });
      if (mode === "hidden") current.state.shown = false;
    };
    f.state.onWait = state => {
      if (clearAttempts && mode === "later") state.search = "";
      if (clearAttempts && mode === "user-change") { state.search = "User new query"; state.interrupted = true; }
    };
    c = command(); r = await f.run(c, owner);
    check("cleanup never repeats its write: " + mode, clearAttempts === 1 && f.state.inserted === 1);
    if (mode === "microtask" || mode === "later") {
      check("async editor clear is verified without a false warning: " + mode,
        r.status === "drafted" && f.state.search === "" && !/search box/.test(r.detail));
    } else if (mode === "ignored") {
      check("ignored clear has bounded readback and preserves result", r.status === "drafted" && f.state.search === c.chat && f.state.waits <= 11 && /search box/.test(r.detail));
    } else if (mode === "user-change") {
      check("user typing during cleanup readback is preserved", r.status === "drafted" && f.state.search === "User new query" && /left alone/.test(r.detail));
    } else {
      check("cleanup does not start throttled waits on a hidden tab", r.status === "drafted" && f.state.waits === 1 && /search box/.test(r.detail));
    }
  }
  f = fixture({available: false, beforeSearch: s => { if (!s.search) s.search = "Alex Example"; }});
  r = await f.run(command(), owner);
  check("rejected initial search never claims ownership of a focus-restored matching query",
    r.status === "blocked" && f.state.search === "Alex Example" && f.state.searches.length === 0 && f.state.opened === 0 && f.state.inserted === 0);
  f = fixture({available: false, count: 2}); r = await f.run(command(), owner);
  check("failed exact search also clears only its own search", r.status === "ambiguous_chat" && f.state.search === "");
  f = fixture({available: false, count: 0, onWait: s => { s.safe = false; s.search = "User changed search"; }}); r = await f.run(command(), owner);
  check("changed user search is preserved on interruption", r.status === "blocked" && f.state.search === "User changed search" && f.state.inserted === 0);
  f = fixture({onWait: s => { s.currentChat = "Another Example"; }}); r = await f.run(command(), owner);
  check("chat switch after insertion has uncertain result, never false success", r.status === "unknown");
  f = fixture({insertThrows: true}); r = await f.run(command(), owner);
  check("partial insertion failure is unknown, never safely retryable", r.status === "unknown" && f.state.inserted === 1);
  f = fixture({onWait: s => { s.targetText += " altered"; }}); r = await f.run(command(), owner);
  check("changed resulting composer cannot report success", r.status === "unknown");
  f = fixture({onWait: s => { s.now += 13000; }}); r = await f.run(command(), owner);
  check("operation deadline prevents late success", r.status === "unknown");
  f = fixture({available: false, count: 0, onWait: s => { s.now += 13000; }}); r = await f.run(command(), owner);
  check("timeout before insertion never types", r.status === "expired" && f.state.inserted === 0);
  for (const fields of [{kind: "delete"}, {kind: "send", phone: null}, {owner: "wrong"}, {id: "bad"}, {text: ""}, {text: "\0"}, {phone: "+353870000001"},
    {expires: NaN}, {expires: 999999}, {expires: 1000000}, {created: 1100000, expires: 1120000}, {expires: 1200001}, {chat: "a\nb"},
    {text: "👍".repeat(2049)}, {text: "\ud800"}, {text: "\udc00"}, {chat: "👍".repeat(61)}, {chat: "\ud800"}]) {
    f = fixture(); r = await f.run(command(fields), owner);
    check("malformed or expired action cannot mutate: " + Object.keys(fields).join(","), ["blocked", "expired"].includes(r.status) && f.state.opened === 0 && f.state.inserted === 0);
  }
  f = fixture(); c = command({text: "First line\nSecond line 👨‍👩‍👧‍👦"}); r = await f.run(c, owner);
  check("multiline and emoji draft retained exactly", r.status === "drafted" && f.state.targetText === c.text);
  f = fixture({phone: null}); r = await f.run(command(), owner);
  check("positive direct lid identity can use exact alias without a phone assertion", r.status === "drafted");
  f = fixture({direct: false, profilePossible: true}); r = await f.run(command(), owner);
  check("opaque IDs require separately authorized visible profile verification", r.status === "drafted" && f.state.profileOpened === 1 && f.state.permissions.join(",") === "open,profile,draft");
  f = fixture({direct: false, profilePossible: true, denyStep: "profile"}); r = await f.run(command(), owner);
  check("cancelled profile grant never opens details or inserts text", r.status === "blocked" && f.state.profileOpened === 0 && f.state.inserted === 0);
  f = fixture({direct: true, identity: "profile", profileCurrent: true}); r = await f.run(command(), owner);
  check("already visible matching profile is reused without a profile click", r.status === "drafted" && f.state.profileOpened === 0 && f.state.permissions.join(",") === "open,draft");
  f = fixture({direct: true, identity: "profile", profileCurrent: true}); r = await f.run(command({phone: null}), owner);
  check("profile fallback never substitutes for a configured verified phone", r.status === "blocked" && f.state.inserted === 0);
  f = fixture({direct: false, profilePossible: true, phone: "353870000002"}); r = await f.run(command(), owner);
  check("different profile phone blocks before drafting", r.status === "blocked" && f.state.inserted === 0);
  f = fixture({direct: false, profileCurrent: true}); r = await f.run(command(), owner);
  check("incomplete current profile never causes a blind profile click", r.status === "blocked" && f.state.profileOpened === 0 && f.state.inserted === 0);
  f = fixture({direct: false, profilePossible: true, onProfile: s => { s.profileCurrent = true; },
    onWait: s => { if (s.waits === 2) { s.direct = true; s.identity = "profile"; } }}); r = await f.run(command(), owner);
  check("delayed profile phone is re-read before success", r.status === "drafted" && f.state.waits >= 2 && f.state.inserted === 1);
  f = fixture(); r = await f.run(command({text: "👍".repeat(2048)}), owner);
  check("exact UTF-16 limit accepts2048 complete astral characters", r.status === "drafted");
}

async function domChecks() {
  class Element {
    constructor(tag, attrs = {}, children = []) {
      this.nodeType = 1; this.tagName = tag.toUpperCase(); this.attrs = attrs; this.childNodes = [];
      for (const child of children) this.append(child);
    }
    append(child) {
      if (typeof child === "string") child = {nodeType: 3, nodeValue: child};
      child.parentElement = this; this.childNodes.push(child); return child;
    }
    get textContent() { return this.childNodes.map((n) => n.nodeType === 3 ? n.nodeValue : n.textContent).join(""); }
    get innerText() { return this.visibleText === undefined ? this.textContent : this.visibleText; }
    get nextSibling() {
      if (!this.parentElement) return null;
      const nodes = this.parentElement.childNodes, at = nodes.indexOf(this);
      return at >= 0 ? nodes[at + 1] || null : null;
    }
    get previousElementSibling() {
      if (!this.parentElement) return null;
      const nodes = this.parentElement.childNodes.filter((n) => n.nodeType === 1), at = nodes.indexOf(this);
      return at > 0 ? nodes[at - 1] : null;
    }
    getClientRects() { return this.closest('[aria-hidden="true"], [hidden]') ? [] : [1]; }
    getAttribute(name) { return Object.hasOwn(this.attrs, name) ? this.attrs[name] : null; }
    hasAttribute(name) { return Object.hasOwn(this.attrs, name); }
    contains(node) { return node === this || this.childNodes.some((n) => n === node || n.nodeType === 1 && n.contains(node)); }
    matches(selector) {
      return (selector.match(/(?:\[[^\]]*\]|[^,])+/g) || []).some((branch) => {
        const parts = branch.trim().split(/\s+(?=[^\]]*(?:\[|$))/), last = parts.pop();
        if (!this.simple(last)) return false;
        let ancestor = this.parentElement;
        while (parts.length) {
          const wanted = parts.pop();
          while (ancestor && !ancestor.simple(wanted)) ancestor = ancestor.parentElement;
          if (!ancestor) return false;
          ancestor = ancestor.parentElement;
        }
        return true;
      });
    }
    simple(selector) {
      const tag = selector.match(/^[a-z][\w-]*/i);
      if (tag && this.tagName !== tag[0].toUpperCase()) return false;
      const id = selector.match(/^#([\w-]+)/);
      if (id && this.getAttribute("id") !== id[1]) return false;
      const cls = selector.match(/^\.([\w-]+)/);
      if (cls && !(this.attrs.class || "").split(/\s+/).includes(cls[1])) return false;
      for (const m of selector.matchAll(/\[([\w-]+)(?:(\^=|\*=|=)["']([^"']*)["'])?\]/g)) {
        const value = this.getAttribute(m[1]);
        if (value === null || m[2] === "=" && value !== m[3] || m[2] === "^=" && !value.startsWith(m[3]) ||
            m[2] === "*=" && !value.includes(m[3])) return false;
      }
      return true;
    }
    closest(selector) { return this.matches(selector) ? this : this.parentElement ? this.parentElement.closest(selector) : null; }
    querySelectorAll(selector) {
      const all = [];
      for (const child of this.childNodes) if (child.nodeType === 1) {
        if (child.matches(selector)) all.push(child);
        all.push(...child.querySelectorAll(selector));
      }
      return all;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  }
  const node = (tag, attrs, children) => new Element(tag, attrs, children);
  const searchInput = node("input", {role: "textbox", type: "text", "data-tab": "3", "aria-label": "Search or start a new chat"});
  searchInput.value = "";
  const sidebar = node("div", {id: "side"}, [searchInput]), searchPage = node("div", {}, [sidebar]);
  const morphedSearchUi = browserAdapter(searchPage, {});
  check("labelled sidebar search starts empty", morphedSearchUi.searchText() === "");
  searchInput.attrs["aria-label"] = ""; searchInput.value = "Alex Example";
  check("morphed unlabelled sidebar search retains ownership text", morphedSearchUi.searchText() === "Alex Example");
  sidebar.attrs.id = "main";
  assert.throws(() => morphedSearchUi.searchText(), /identified safely/); passed++;
  sidebar.attrs.id = "side";
  const otherSearch = node("input", {role: "textbox", type: "text", "data-tab": "3", "aria-label": ""});
  otherSearch.value = "Someone else"; sidebar.append(otherSearch);
  assert.throws(() => morphedSearchUi.searchText(), /identified safely/); passed++;
  otherSearch.attrs.hidden = "";
  check("hidden duplicate search cannot replace the visible field", morphedSearchUi.searchText() === "Alex Example");
  const section = label => node("div", {role: "row"}, [node("h2", {}, [label])]);
  const searchHit = label => node("div", {role: "row"}, [node("div", {role: "gridcell", tabindex: "0"},
    [node("div", {"data-testid": "cell-frame-container"}, [node("span", {title: label}, [label])])])]);
  const chatsHeading = section("Chats"), messagesHeading = section("Messages");
  const actualChat = searchHit("Alex Example"), olderMessage = searchHit("Alex Example");
  const resultGrid = node("div", {role: "grid", "aria-label": "Search results."}, [chatsHeading, actualChat, messagesHeading, olderMessage]);
  const resultPane = node("div", {id: "pane-side"}, [resultGrid]), resultPage = node("div", {}, [resultPane]);
  const resultUi = browserAdapter(resultPage, {});
  check("sectioned search selects actual chat, never older same-name message hits", resultUi.rows("Alex Example").length === 1 && resultUi.rows("Alex Example")[0].node === actualChat);
  const duplicateChat = searchHit("Alex Example");
  resultGrid.childNodes.splice(2, 0, duplicateChat); duplicateChat.parentElement = resultGrid;
  check("duplicate names within actual Chats remain ambiguous", resultUi.rows("Alex Example").length === 2);
  resultGrid.childNodes.splice(2, 1);
  chatsHeading.childNodes[0].childNodes[0].nodeValue = "Contacts";
  check("unknown first search section cannot stand in for Chats", resultUi.rows("Alex Example").length === 0);
  chatsHeading.childNodes[0].childNodes[0].nodeValue = "Chats";
  resultGrid.childNodes.shift();
  check("missing Chats header never promotes a message hit", resultUi.rows("Alex Example").length === 0);
  resultGrid.childNodes.unshift(chatsHeading);
  messagesHeading.childNodes[0].childNodes[0].nodeValue = "Other results";
  check("any later section ends recipient candidates", resultUi.rows("Alex Example").length === 1 && resultUi.rows("Alex Example")[0].node === actualChat);
  messagesHeading.childNodes[0].childNodes[0].nodeValue = "Chats";
  check("duplicate Chats sections are rejected conservatively", resultUi.rows("Alex Example").length === 0);
  messagesHeading.childNodes[0].childNodes[0].nodeValue = "Messages";
  resultGrid.childNodes = [chatsHeading, actualChat];
  check("Chats-only search results still identify the actual conversation", resultUi.rows("Alex Example").length === 1);
  const nestedChat = searchHit("Alex Example"); resultGrid.append(node("div", {}, [nestedChat]));
  check("nested unknown search rows cannot join direct Chats candidates", resultUi.rows("Alex Example").length === 1);
  const duplicateGrid = node("div", {role: "grid", "aria-label": "Search results."}, [section("Chats"), searchHit("Alex Example")]);
  resultPane.append(duplicateGrid);
  check("multiple visible search-result grids cannot choose a recipient", resultUi.rows("Alex Example").length === 0);
  duplicateGrid.attrs.hidden = "";
  check("hidden prior search grid does not create ambiguity", resultUi.rows("Alex Example").length === 1);
  resultPane.childNodes = [actualChat]; actualChat.parentElement = resultPane;
  check("ordinary unsectioned chat list remains supported", resultUi.rows("Alex Example").length === 1 && resultUi.rows("Alex Example")[0].node === actualChat);
  const activeSearchInput = node("input", {role: "textbox", type: "text", "data-tab": "3", "aria-label": ""});
  activeSearchInput.value = "Alex Example";
  const resultSide = node("div", {id: "side"}, [activeSearchInput]); resultPage.append(resultSide);
  check("active search without a recognized results grid never falls back to all rows", resultUi.rows("Alex Example").length === 0);
  resultPane.childNodes = [resultGrid]; resultGrid.parentElement = resultPane; actualChat.parentElement = resultGrid;
  resultGrid.attrs["aria-label"] = "Results layout changed";
  check("changed results label remains failclosed while search has text", resultUi.rows("Alex Example").length === 0);
  resultGrid.attrs["aria-label"] = "Search results.";
  check("recognized sectioned results remain usable during active search", resultUi.rows("Alex Example").length === 1 && resultUi.rows("Alex Example")[0].node === actualChat);
  resultPane.childNodes = [actualChat]; actualChat.parentElement = resultPane;
  activeSearchInput.attrs["aria-label"] = "Unknown sidebar query";
  check("unknown nonempty sidebar search control cannot expose message hits", resultUi.rows("Alex Example").length === 0);
  activeSearchInput.value = "";
  check("empty search returns normal chat-list candidates", resultUi.rows("Alex Example").length === 1);
  const title = node("span", {dir: "auto"}, ["Alex Example"]), header = node("header", {}, [title]);
  const composer = node("div", {"aria-label": "Type a message to Alex Example", contenteditable: "true", role: "textbox"}, ["Hello ", node("img", {alt: "👍"})]);
  const message = node("div", {"data-testid": "conv-msg-synthetic", "data-id": "false_353870000001@c.us_SYNTHETIC"});
  const footer = node("footer", {}, [composer]), main = node("div", {}, [header, message, footer]);
  let dialogs = [], drawers = [], listRows = [], inputCalls = 0;
  const doc = {querySelector: selector => selector === "#main" ? main : selector === "#pane-side" ? node("div") : null,
    querySelectorAll: selector => selector === '#pane-side [role="row"]' ? listRows : selector.includes("chat-info-drawer") ? drawers :
      dialogs.concat(drawers.flatMap(d => d.querySelectorAll('[role="dialog"], [aria-modal="true"]'))),
    createRange: () => ({selectNodeContents() {}, collapse() {}}),
    execCommand: () => { inputCalls++; return true; }};
  class FakeMouseEvent { constructor(type, fields) { this.type = type; Object.assign(this, fields); } }
  const adapterWindow = {location: {origin: "https://web.whatsapp.com"}, MouseEvent: FakeMouseEvent,
    getSelection: () => ({removeAllRanges() {}, addRange() {}})};
  const ui = browserAdapter(doc, adapterWindow);
  const cellFrame = node("div", {"data-testid": "cell-frame-container"}, [node("span", {title: "Alex Example "}, ["Alex Example"])]);
  const actionable = node("div", {role: "gridcell", tabindex: "0"}, [node("div", {tabindex: "-1", "aria-selected": "false"}, [cellFrame])]);
  const chatRow = node("div", {role: "row", "data-testid": "list-item-0"}, [actionable]);
  let parentClicks = 0, cellClicks = 0, frameClicks = 0, directFrameClicks = 0, chatOpened = false;
  let mouseEvents = [], onFrameEvent = null;
  chatRow.click = () => { parentClicks++; };
  actionable.click = () => { cellClicks++; };
  cellFrame.click = () => { directFrameClicks++; };
  cellFrame.dispatchEvent = event => {
    mouseEvents.push(event);
    if (event.type === "mousedown") chatOpened = true;
    if (event.type === "click") frameClicks++;
    if (onFrameEvent) onFrameEvent(event);
    return true;
  };
  listRows = [chatRow];
  chatRow.click();
  actionable.click();
  cellFrame.click();
  check("click-only row cell and frame activation is inert in the mouse-down fixture", parentClicks === 1 && cellClicks === 1 && directFrameClicks === 1 && !chatOpened);
  let boundaries = 0;
  ui.open(ui.rows("Alex Example")[0], () => { boundaries++; });
  check("adapter opens the exact contained frame through its mouse-down handler", chatOpened && frameClicks === 1 && cellClicks === 1 && parentClicks === 1 && directFrameClicks === 1);
  check("one standard left-button sequence rechecks authorization before each event", boundaries === 3 && mouseEvents.map(e => e.type).join(",") === "mousedown,mouseup,click" &&
    mouseEvents.every(e => e.bubbles && e.cancelable && e.composed && e.button === 0 && e.detail === 1) && mouseEvents.map(e => e.buttons).join(",") === "1,0,0");
  mouseEvents = []; boundaries = 0;
  assert.throws(() => ui.open({node: chatRow}, () => { if (++boundaries === 2) throw new Error("revoked"); }), /revoked/); passed++;
  check("revocation after mouse-down prevents mouse-up and click", mouseEvents.map(e => e.type).join(",") === "mousedown" && frameClicks === 1);
  const frameParent = cellFrame.parentElement;
  mouseEvents = [];
  onFrameEvent = event => { if (event.type === "mousedown") { chatRow.childNodes = []; actionable.parentElement = null; } };
  ui.open({node: chatRow});
  check("row detached by mouse-down stops remaining events for later header verification", mouseEvents.map(e => e.type).join(",") === "mousedown" && frameClicks === 1);
  chatRow.append(actionable); onFrameEvent = null;
  mouseEvents = [];
  onFrameEvent = event => { if (event.type === "mousedown") chatRow.append(node("div", {role: "gridcell", tabindex: "0"})); };
  ui.open({node: chatRow});
  check("a new ambiguous actionable cell stops the remaining activation events", mouseEvents.map(e => e.type).join(",") === "mousedown" && frameClicks === 1);
  chatRow.childNodes.pop(); onFrameEvent = null;
  mouseEvents = [];
  const replacementFrame = node("div", {"data-testid": "cell-frame-container"});
  let replacementEvents = 0; replacementFrame.dispatchEvent = () => { replacementEvents++; };
  onFrameEvent = event => { if (event.type === "mousedown") { frameParent.childNodes = []; frameParent.append(replacementFrame); } };
  ui.open({node: chatRow});
  check("frame replaced during activation is never followed or retried", mouseEvents.map(e => e.type).join(",") === "mousedown" && replacementEvents === 0 && frameClicks === 1);
  frameParent.childNodes = []; frameParent.append(cellFrame); onFrameEvent = null;
  mouseEvents = [];
  ui.open({node: chatRow}, () => { frameParent.childNodes = []; });
  check("frame detached by final boundary check receives no event", mouseEvents.length === 0 && frameClicks === 1);
  frameParent.append(cellFrame);
  frameParent.childNodes = [];
  assert.throws(() => ui.open({node: chatRow}), /clickable frame/); passed++;
  frameParent.append(cellFrame);
  frameParent.append(node("div", {"data-testid": "cell-frame-container"}));
  assert.throws(() => ui.open({node: chatRow}), /clickable frame/); passed++;
  frameParent.childNodes.pop();
  cellFrame.attrs.hidden = "";
  assert.throws(() => ui.open({node: chatRow}), /clickable frame/); passed++;
  delete cellFrame.attrs.hidden;
  frameParent.childNodes = [];
  chatRow.append(node("div", {role: "gridcell", tabindex: "-1"}, [cellFrame]));
  assert.throws(() => ui.open({node: chatRow}), /clickable frame/); passed++;
  chatRow.childNodes.pop();
  frameParent.append(node("div", {role: "row"}, [cellFrame]));
  assert.throws(() => ui.open({node: chatRow}), /clickable frame/); passed++;
  frameParent.childNodes = [];
  frameParent.append(node("div", {role: "gridcell", tabindex: "-1"}, [cellFrame]));
  assert.throws(() => ui.open({node: chatRow}), /clickable frame/); passed++;
  frameParent.childNodes = [];
  frameParent.append(cellFrame);
  check("missing duplicate hidden moved or nested frames never click", frameClicks === 1 && cellClicks === 1 && parentClicks === 1);
  actionable.attrs.tabindex = "-1";
  assert.throws(() => ui.open({node: chatRow}), /actionable cell/); passed++;
  actionable.attrs.tabindex = "0";
  chatRow.append(node("div", {role: "gridcell", tabindex: "0"}));
  assert.throws(() => ui.open({node: chatRow}), /actionable cell/); passed++;
  chatRow.childNodes.pop();
  actionable.attrs.hidden = "";
  assert.throws(() => ui.open({node: chatRow}), /actionable cell/); passed++;
  delete actionable.attrs.hidden;
  chatRow.childNodes = [];
  chatRow.append(node("div", {}, [actionable]));
  assert.throws(() => ui.open({node: chatRow}), /actionable cell/); passed++;
  check("unknown ambiguous hidden and nested row layouts never click", frameClicks === 1 && cellClicks === 1 && parentClicks === 1);
  listRows = [];
  check("DOM adapter reads image emoji in composer", ui.current().text === "Hello 👍");
  const savedComposerContent = composer.childNodes;
  const outsidePlaceholder = node("div", {"aria-hidden": "true"}, ["Type a message"]);
  footer.append(outsidePlaceholder);
  check("fixture exposes the real external editor sibling", composer.nextSibling === outsidePlaceholder);
  const content = children => { composer.childNodes = []; for (const child of children) composer.append(child); };
  content([node("p", {}, [node("br")])]);
  check("empty paragraph plus external placeholder is an empty composer", ui.current().text === "");
  content([node("br")]);
  check("bare structural BR placeholder remains empty with external controls", ui.current().text === "");
  content([node("p", {}, ["Exact draft text"])]);
  check("external controls add no trailing newline to filled composer", ui.current().text === "Exact draft text");
  content([node("p", {}, ["Yes ", node("img", {alt: "👍"})])]);
  check("filled emoji paragraph reads back exactly beside controls", ui.current().text === "Yes 👍");
  content([node("p", {}, ["First"]), node("p", {}, ["Second"])]);
  check("paragraph boundary within composer preserves one newline", ui.current().text === "First\nSecond");
  content([node("p", {}, ["First"]), node("p", {}, [node("br")]), node("p", {}, ["Third"])]);
  check("intentional blank paragraph preserves exactly one empty line", ui.current().text === "First\n\nThird");
  content([node("p", {}, [node("br")]), node("p", {}, [node("br")])]);
  check("multiple blank paragraphs remain existing whitespace instead of empty", ui.current().text === "\n");
  content(["\n"]);
  check("literal newline text is never mistaken for a BR placeholder", ui.current().text === "\n");
  content([" \t "]);
  check("intentional spaces and tabs are preserved exactly", ui.current().text === " \t ");
  content([node("p", {}, [" leading ", node("br"), " trailing "])]);
  check("explicit internal BR and surrounding whitespace survive readback", ui.current().text === " leading \n trailing ");
  composer.childNodes = savedComposerContent; footer.childNodes.pop();
  check("DOM adapter verifies direct message JID and phone", ui.current().direct && ui.current().phone === "353870000001");
  const historyList = node("div", {"data-tab": "8"});
  const historyPanel = node("div", {"data-testid": "conversation-panel-messages"}, [historyList]);
  Object.assign(historyPanel, {scrollTop: 200, scrollHeight: 1000, clientHeight: 500});
  main.append(historyPanel);
  adapterWindow.getComputedStyle = () => ({overflowY: "scroll"});
  adapterWindow.__northwindReadCapture = {capture: () => ({chat: "Alex Example", events: [], unresolved: 0, unresolvedAt: []})};
  const readTarget = ui.current(), readView = ui.readView(readTarget);
  check("read adapter uses grounded panel parent rather than non-scrollable data-tab child", readView.container === historyPanel && readView.list === historyList && readView.scrollable);
  let readBoundaries = 0; ui.scrollRead(readView, 100, () => { readBoundaries++; });
  check("scroll changes only verified history position after authorization", historyPanel.scrollTop === 100 && readBoundaries === 1 && ui.current().text === readTarget.text);
  assert.throws(() => ui.scrollRead(readView, 0, () => { throw new Error("revoked read"); }), /revoked/); passed++;
  check("revoked scroll leaves position unchanged", historyPanel.scrollTop === 100);
  main.append(node("div", {"data-testid": "conversation-panel-messages"}, [node("div", {"data-tab": "8"})]));
  check("ambiguous history panels cannot be selected", ui.readView(readTarget) === null);
  main.childNodes.pop();
  historyPanel.childNodes = []; historyPanel.append(node("div", {}, [historyList]));
  check("unobserved nested history layout never selects an ancestor", ui.readView(readTarget) === null);
  historyPanel.childNodes = []; historyPanel.append(historyList);
  adapterWindow.getComputedStyle = () => ({overflowY: "visible"});
  check("non-scrollable known panel is available only as partial history", !ui.readView(readTarget).scrollable);
  adapterWindow.getComputedStyle = () => ({overflowY: "scroll"});
  const beforeReadText = composer.childNodes; composer.childNodes = []; composer.append("User changed draft");
  assert.throws(() => ui.readView(readTarget), /chat or message box changed/); passed++;
  composer.childNodes = beforeReadText;
  historyPanel.childNodes = []; historyPanel.append(node("div", {"data-tab": "8"}));
  assert.throws(() => ui.scrollRead(readView, 0, () => {}), /scroll area changed/); passed++;
  main.childNodes.pop();
  delete adapterWindow.__northwindReadCapture;
  message.attrs["data-id"] = "false_12345@g.us_SYNTHETIC";
  check("DOM group JID refuses direct identity", ui.current().group && !ui.current().direct);
  message.attrs["data-id"] = "false_123456789@lid_SYNTHETIC";
  check("DOM lid proves direct type without inventing phone", ui.current().direct && ui.current().phone === null);
  message.attrs["data-id"] = "unknown";
  check("DOM missing direct proof fails closed", !ui.current().direct);
  title.childNodes[0].nodeValue = "Alex Example ";
  composer.attrs["aria-label"] = "Type a message to Alex Example ";
  check("observed trailing ASCII space in header and recipient label is harmless", ui.current().chat === "Alex Example");
  composer.attrs["aria-label"] = "Type a message to Alex  Example ";
  assert.throws(() => ui.current(), /recipient/); passed++;
  composer.attrs["aria-label"] = "Type a message to alex example ";
  assert.throws(() => ui.current(), /recipient/); passed++;
  composer.attrs["aria-label"] = "Type a message to Other Example ";
  assert.throws(() => ui.current(), /recipient/); passed++;
  title.childNodes[0].nodeValue = "Alex Example";
  composer.attrs["aria-label"] = "Type a message to Other Example";
  assert.throws(() => ui.current(), /recipient/); passed++;
  composer.attrs["aria-label"] = "Unknown editable";
  assert.throws(() => ui.current(), /label/); passed++;
  composer.attrs["aria-label"] = "Type a message";
  composer.childNodes = [node("br")];
  check("DOM empty editor line break is empty", ui.current().text === "");
  const profileHeading = node("h2", {}, ["Contact info"]);
  const profileName = node("span", {dir: "auto"}, ["Alex Example "]);
  const phoneHeading = node("div", {"data-testid": "section-about-and-phone-number"}, ["About and phone number"]);
  const phoneLeaf = node("span", {}, ["+353 87 000 0001"]);
  const aboutField = node("div", {"data-testid": "container"}, ["Synthetic about"]);
  const phoneField = node("div", {"data-testid": "container_with_separator"}, [phoneLeaf]);
  const phoneSection = node("div", {}, [phoneHeading, aboutField, phoneField]);
  const gallery = node("div", {role: "dialog", "data-testid": "media-gallery"});
  const drawer = node("div", {"data-testid": "chat-info-drawer"}, [node("header", {"data-testid": "contact-info-header"}, [profileHeading]),
    profileName, node("span", {dir: "auto", "data-testid": "business-top-card-name-title"}, ["Alex Example"]), phoneSection, gallery]);
  drawers = [drawer];
  message.attrs["data-id"] = "OPAQUE20CHARMESSAGEID";
  check("actual Contact info structure proves opaque-ID direct chat", ui.current().direct && ui.current().identity === "profile" && ui.current().phone === "353870000001");
  check("observed nonmodal profile media-gallery does not block the composer", ui.current().plain && ui.safe());
  drawer.childNodes.pop();
  delete gallery.attrs.role; gallery.attrs.tabindex = "0";
  const galleryWrapper = node("div", {role: "dialog", tabindex: "-1"}, [gallery]);
  drawer.append(galleryWrapper);
  check("observed sole-gallery dialog wrapper inside Contact info is safe", ui.current().plain && ui.safe() && ui.current().direct);
  galleryWrapper.append(node("div", {hidden: ""}, ["Unrelated synthetic sibling"]));
  check("even hidden non-gallery wrapper siblings remain blocked", !ui.safe());
  galleryWrapper.childNodes.pop();
  galleryWrapper.append("Unrelated synthetic text");
  check("nonblank wrapper text remains blocked without disclosing it", !ui.safe() && !/synthetic/.test(ui.safeReason()));
  galleryWrapper.childNodes.pop();
  galleryWrapper.attrs["aria-modal"] = "true";
  check("a modal gallery wrapper remains blocked", !ui.safe());
  delete galleryWrapper.attrs["aria-modal"];
  gallery.attrs["aria-modal"] = "true";
  check("a modal gallery child remains blocked", !ui.safe());
  delete gallery.attrs["aria-modal"];
  galleryWrapper.attrs.tabindex = "0";
  check("an unrecognized wrapper tab order is not exempted", !ui.safe());
  galleryWrapper.attrs.tabindex = "-1";
  gallery.attrs.tabindex = "-1";
  check("an unrecognized gallery tab order is not exempted", !ui.safe());
  gallery.attrs.tabindex = "0";
  for (const attrs of [{contenteditable: "true"}, {contenteditable: ""}, {contenteditable: "plaintext-only"}]) {
    Object.assign(galleryWrapper.attrs, attrs);
    check("editable wrapper is never a passive gallery: " + JSON.stringify(attrs), !ui.safe());
    delete galleryWrapper.attrs.contenteditable;
  }
  for (const editor of [node("input"), node("textarea"), node("select"), node("div", {role: "textbox"}),
    node("div", {contenteditable: "plaintext-only"}), node("div", {"aria-modal": "true", hidden: ""})]) {
    gallery.append(editor);
    check("gallery descendants cannot hide editors or modal controls: " + editor.tagName, !ui.safe());
    gallery.childNodes.pop();
  }
  gallery.append(node("div", {role: "dialog"}));
  check("foreign dialog nested in the gallery wrapper stays blocked", !ui.safe());
  gallery.childNodes.pop();
  profileHeading.childNodes[0].nodeValue = "Group info";
  check("gallery wrapper needs a validated Contact info panel", !ui.safe());
  profileHeading.childNodes[0].nodeValue = "Contact info";
  drawer.childNodes.pop(); dialogs = [galleryWrapper];
  check("matching gallery wrapper outside Contact info remains blocked", !ui.safe());
  dialogs = []; drawer.append(galleryWrapper);
  check("restored exact gallery wrapper returns to safe state", ui.safe());
  drawer.childNodes.pop(); drawer.append(gallery);
  gallery.attrs.role = "dialog"; delete gallery.attrs.tabindex;
  profileName.childNodes[0].nodeValue = "Other Example";
  check("stale different-name drawer cannot supply current-chat identity", !ui.current().direct && !ui.current().profileCurrent);
  profileName.childNodes[0].nodeValue = "Alex Example ";
  phoneLeaf.childNodes[0].nodeValue = "+353 87 000 0002";
  check("phone is re-read from current drawer, never cached", ui.current().phone === "353870000002");
  phoneLeaf.childNodes[0].nodeValue = "+353 87 000 0001";
  phoneField.append(node("span", {}, ["+353 87 000 0002"]));
  check("two visible profile phone leaves remain ambiguous", !ui.current().direct);
  phoneField.childNodes.pop();
  phoneSection.childNodes.splice(2, 1);
  check("incomplete profile does not borrow phone from elsewhere", !ui.current().direct);
  aboutField.childNodes[0].nodeValue = "+353 87 000 0001";
  check("phone-looking About cannot replace a missing actual phone field", !ui.current().direct && ui.current().phone === null);
  phoneSection.append(phoneField);
  phoneField.attrs.hidden = "";
  check("phone-looking About cannot replace a hidden actual phone field", !ui.current().direct && ui.current().phone === null);
  delete phoneField.attrs.hidden;
  phoneLeaf.attrs.hidden = "";
  check("phone-looking About cannot replace a hidden actual number", !ui.current().direct && ui.current().phone === null);
  delete phoneLeaf.attrs.hidden;
  phoneLeaf.childNodes[0].nodeValue = "+353 87 000 0002";
  check("actual phone field wins over a different phone-looking About", ui.current().phone === "353870000002");
  phoneLeaf.childNodes[0].nodeValue = "+353 87 000 0001";
  phoneSection.append(node("div", {"data-testid": "container_with_separator"}, [node("span", {}, ["+353 87 000 0001"])]));
  check("duplicate phone fields are ambiguous even with identical numbers", !ui.current().direct);
  phoneSection.childNodes.pop();
  phoneSection.childNodes.splice(2, 1);
  phoneSection.append(node("div", {}, [phoneField]));
  check("unknown nested phone-field layout fails closed", !ui.current().direct);
  phoneSection.childNodes.pop();
  phoneSection.append(phoneField);
  aboutField.childNodes[0].nodeValue = "Synthetic about";
  const headerName = node("span", {"data-testid": "contact-info-subtitle selectable-text", dir: "auto"}, ["Alex Example "]);
  const nameInner = node("div", {}, [headerName]), nameOuter = node("div", {}, [nameInner]), nameWrapper = node("span", {}, [nameOuter]);
  const headerPhoneLeaf = node("span", {}, ["+353 87 000 0001"]);
  const headerPhoneText = node("span", {"data-testid": "selectable-text", dir: "auto"}, [headerPhoneLeaf]);
  const headerPhoneField = node("div", {}, [headerPhoneText]), identityHeader = node("div", {}, [nameWrapper, headerPhoneField]);
  const separateAbout = node("div", {"data-testid": "section-about"}, [node("span", {dir: "auto"}, ["+353 87 000 0002"])]);
  const modernDrawer = node("div", {"data-testid": "chat-info-drawer"}, [node("div", {"data-testid": "contact-info-header"}, [node("h2", {}, ["Contact info"])]), identityHeader, separateAbout]);
  drawers = [modernDrawer];
  check("observed marked-name identity header supplies its exact sibling phone", ui.current().direct && ui.current().phone === "353870000001" && ui.current().identity === "profile");
  check("separate phone-looking About cannot override the identity header", ui.current().phone !== "353870000002");
  identityHeader.childNodes.pop();
  check("missing header phone cannot borrow a number from About", !ui.current().direct && ui.current().phone === null);
  identityHeader.append(headerPhoneField);
  headerPhoneText.attrs["data-testid"] = "unknown-field";
  check("unknown header phone markup stays unverified", !ui.current().direct);
  headerPhoneText.attrs["data-testid"] = "selectable-text";
  headerPhoneLeaf.attrs.hidden = "";
  check("hidden header phone cannot prove the recipient", !ui.current().direct);
  delete headerPhoneLeaf.attrs.hidden;
  headerPhoneText.append(node("span", {}, ["+353 87 000 0001"]));
  check("duplicate header phone leaves remain ambiguous even when equal", !ui.current().direct);
  headerPhoneText.childNodes.pop();
  identityHeader.append(node("div", {hidden: ""}, ["extra field"]));
  check("extra identity-header child rejects even hidden alternative fields", !ui.current().direct);
  identityHeader.childNodes.pop();
  nameInner.append(node("span", {}, ["additional name"]));
  check("extra name-wrapper content cannot supply identity", !ui.current().direct);
  nameInner.childNodes.pop();
  headerPhoneLeaf.attrs.contenteditable = "true";
  check("editable header number is never recipient proof", !ui.current().direct);
  delete headerPhoneLeaf.attrs.contenteditable;
  modernDrawer.append(node("span", {"data-testid": "contact-info-subtitle selectable-text", dir: "auto"}, ["Alex Example"]));
  check("multiple marked profile names are rejected", !ui.current().direct);
  modernDrawer.childNodes.pop();
  modernDrawer.append(phoneSection);
  check("two supported phone layouts must agree before they can prove identity", ui.current().direct && ui.current().phone === "353870000001");
  phoneLeaf.childNodes[0].nodeValue = "+353 87 000 0002";
  check("conflicting legacy and header phone fields block recipient identity", !ui.current().direct && ui.current().phone === null);
  phoneLeaf.childNodes[0].nodeValue = "+353 87 000 0001";
  modernDrawer.childNodes.pop(); phoneSection.parentElement = drawer;
  headerName.childNodes[0].nodeValue = "Other Example";
  check("same structural phone field for another displayed name stays unverified", !ui.current().direct && !ui.current().profileCurrent);
  headerName.childNodes[0].nodeValue = "Alex Example";
  headerPhoneLeaf.childNodes[0].nodeValue = "+353 87 000 0002";
  check("header phone is freshly reread after its visible value changes", ui.current().phone === "353870000002");
  drawers = [drawer];

  profileHeading.childNodes[0].nodeValue = "Group info";
  check("group-info drawer never proves a direct chat or exempts a dialog", !ui.current().direct && !ui.safe());
  profileHeading.childNodes[0].nodeValue = "Contact info";
  drawers.push(drawer);
  check("multiple visible drawers remain ambiguous", !ui.current().direct && !ui.safe());
  drawers.pop();
  gallery.append(node("div", {role: "dialog"}));
  check("nested modal is not exempted by profile gallery", !ui.safe());
  gallery.childNodes.pop();
  gallery.attrs["aria-modal"] = "true";
  check("aria-modal profile overlay is still blocked", !ui.safe());
  delete gallery.attrs["aria-modal"];
  dialogs = [node("div", {role: "dialog"})];
  check("unrelated modal remains blocked beside valid profile", !ui.safe());
  dialogs = [];
  let profileClicks = 0;
  const profileButton = node("button", {"aria-label": "Profile details"}); profileButton.click = () => { profileClicks++; };
  header.append(profileButton);
  ui.openProfile("Alex Example", () => {});
  check("profile opening uses only the exact main-header control", profileClicks === 1);
  assert.throws(() => ui.openProfile("Other Example", () => {}), /exact chat/); passed++;
  assert.throws(() => ui.openProfile("Alex Example", () => { throw new Error("revoked"); }), /revoked/); passed++;
  check("revoked profile permission never clicks", profileClicks === 1);
  composer.isContentEditable = true;
  composer.focus = () => { phoneLeaf.childNodes[0].nodeValue = "+353 87 000 0002"; };
  assert.throws(() => ui.insert(ui.current(), "Reviewed draft"), /recipient changed/); passed++;
  check("profile identity is rechecked after composer focus", inputCalls === 0);
  drawers = [];
  check("ordinary empty composer is a plain draft context", ui.current().plain && ui.safe() && ui.safeReason() === "");
  footer.attrs.hidden = "";
  check("missing or hidden message controls have their own blocked reason", !ui.safe() && /message controls are missing/.test(ui.safeReason()));
  delete footer.attrs.hidden;
  composer.attrs.hidden = "";
  check("missing or ambiguous message box has its own blocked reason", !ui.safe() && /message box is missing or ambiguous/.test(ui.safeReason()));
  delete composer.attrs.hidden;
  check("a recovered context clears the previous diagnostic", ui.safe() && ui.safeReason() === "");
  const noPane = browserAdapter({querySelector: () => null}, {location: {origin: "https://web.whatsapp.com"}});
  check("unavailable chat list has a specific reason", !noPane.safe() && /chat list is not available/.test(noPane.safeReason()));
  const changedPage = browserAdapter({}, {location: {origin: "https://example.invalid"}});
  check("changed page reports no URL or private contents", !changedPage.safe() && changedPage.safeReason() === "The WhatsApp page changed; the request stopped.");
  let cancelWatch;
  doc.addEventListener = (_, listener) => { cancelWatch = listener; };
  doc.removeEventListener = () => {};
  const stopWatch = ui.watch(); cancelWatch({isTrusted: true});
  check("trusted input is distinct from unsupported controls", !ui.safe() && /You clicked or typed/.test(ui.safeReason()));
  stopWatch(); ui.watch()();
  check("new request resets old interaction state and diagnostic", ui.safe() && ui.safeReason() === "");
  const attach = node("button", {"aria-label": "Attach"}, [node("span", {"data-icon": "attach"})]);
  footer.append(attach);
  check("ordinary Attach button is not an active attachment", ui.current().plain);
  {
    // 8 Nov: the real page presses WhatsApp's OWN Send button once - and only for exactly the reviewed text
    const before = footer.childNodes.slice(), savedId = message.attrs["data-id"];
    let notVerified = false;
    try { ui.pressSend(ui.current(), ui.current().text, () => {}); } catch (e) { notVerified = /nothing was sent/.test(e.message); }
    check("real page: an unverified chat (no proof it is that one person) -> refuses to send", notVerified);
    message.attrs["data-id"] = "false_353870000001@c.us_SYNTHETIC";
    const sendButton = node("button", {"aria-label": "Send"}, [node("span", {"data-icon": "send"})]), clicks = [];
    sendButton.dispatchEvent = e => { clicks.push(e.type); return true; };
    footer.append(sendButton);
    const box = ui.current(), text = box.text;
    ui.pressSend(box, text, () => {});
    check("real page: WhatsApp's own Send button gets one mousedown/mouseup/click", clicks.join(",") === "mousedown,mouseup,click");
    let refused = false;
    try { ui.pressSend(box, text + " (changed)", () => {}); } catch (e) { refused = /request stopped/.test(e.message); }
    check("real page: refuses if the box does not hold exactly the reviewed text", refused && clicks.length === 3);
    footer.append(node("button", {"aria-label": "Send"}));
    refused = false;
    try { ui.pressSend(ui.current(), text, () => {}); } catch (e) { refused = /could not be identified/.test(e.message); }
    check("real page: two Send buttons -> refuses, presses nothing", refused && clicks.length === 3);
    footer.childNodes.pop();
    for (const change of ["phone", "text", "button", "disabled", "permission"]) {
      const savedBody = composer.childNodes.slice(), savedFooter = footer.childNodes.slice();
      const exactTarget = ui.current(); clicks.length = 0; let grants = 0;
      sendButton.dispatchEvent = e => {
        clicks.push(e.type);
        if (e.type !== "mousedown") return true;
        if (change === "phone") message.attrs["data-id"] = "false_353870000002@c.us_SYNTHETIC";
        if (change === "text") composer.append(" changed after mouse down");
        if (change === "button") { footer.childNodes.pop(); footer.append(node("button", {"aria-label": "Send"})); }
        if (change === "disabled") sendButton.disabled = true;
        return true;
      };
      assert.throws(() => ui.pressSend(exactTarget, text, () => {
        grants++; if (change === "permission" && grants === 2) throw new Error("send cancelled");
      }), /request stopped|cancelled/); passed++;
      check("real Send gesture stops after synchronous " + change + " change", clicks.join(",") === "mousedown");
      composer.childNodes = savedBody; footer.childNodes = savedFooter; sendButton.disabled = false;
      message.attrs["data-id"] = "false_353870000001@c.us_SYNTHETIC";
    }
    footer.childNodes = before;
    message.attrs["data-id"] = savedId;
  }
  function pending(label, element, container = footer, reason = /control is active/) {
    container.append(element);
    check(label, !ui.current().plain && !ui.safe());
    check(label + ": diagnostic is specific and contains no fixture contents", reason.test(ui.safeReason()) &&
      ui.safeReason().length <= 400 && !/Synthetic|Earlier synthetic|synthetic\.txt/.test(ui.safeReason()));
    container.childNodes.pop();
    check(label + ": removing pending fixture restores ordinary composer", ui.current().plain && ui.safe() && ui.safeReason() === "");
  }
  pending("reply quote context is not plain", node("div", {"data-testid": "quoted-message"}, ["Earlier synthetic message"]));
  pending("edit mode with an empty textbox is not plain", node("div", {"aria-label": "Editing message"}));
  pending("edit mode test id is not plain", node("div", {"data-testid": "edit-message"}));
  pending("reply cancellation control identifies pending mode", node("button", {"aria-label": "Cancel reply"}));
  pending("photo pending outside the textbox is not plain", node("img", {alt: "Synthetic pending image"}), footer, /Visible media outside/);
  pending("attachment preview outside the footer is not plain", node("div", {"data-testid": "attachment-preview"}), main);
  pending("recording mode is not plain", node("div", {"data-icon": "voice-recording"}));
  pending("unknown extra footer text fails closed", node("div", {}, ["Synthetic special composer context"]), footer, /Unrecognized text or controls/);
  const file = node("input", {type: "file", hidden: ""}); file.files = [{name: "synthetic.txt"}];
  pending("selected hidden file input is not an empty message", file, main, /A file is selected/);
  const caption = node("textarea", {"aria-label": "Caption"});
  pending("additional caption editor is not ordinary composer", caption);
  pending("unknown second editor remains blocked", node("textarea", {"aria-label": "Synthetic custom input"}), footer, /additional editor/);
  dialogs = [node("div", {role: "dialog"})];
  check("visible attachment or other dialog blocks drafting", !ui.current().plain && !ui.safe());
  check("blocking dialog has its own content-free reason", /dialog open/.test(ui.safeReason()) && !/Synthetic/.test(ui.safeReason()));
  dialogs = [];
  const oldQuote = node("div", {role: "row"}, [node("div", {"data-testid": "quoted-message"}, ["Historical quote", node("img", {alt: "Old photo"})])]);
  main.append(oldQuote);
  check("historical quoted messages do not create a pending composer mode", ui.current().plain);
  main.childNodes.pop();
  footer.append(node("div", {hidden: "", "data-testid": "quoted-message"}, ["Hidden stale quote"]));
  check("hidden stale mode marker is ignored", ui.current().plain);
  footer.childNodes.pop();
  composer.isContentEditable = true;
  message.attrs["data-id"] = "false_353870000001@c.us_SYNTHETIC";
  composer.focus = () => { footer.append(node("div", {"aria-label": "Editing message"})); };
  assert.throws(() => ui.insert(ui.current(), "Reviewed draft"), /composer or recipient changed/); passed++;
  check("focus-triggered edit context is rechecked before DOM insertion", inputCalls === 0);
  footer.childNodes.pop();
  for (const changed of ["false_353870000002@c.us_SYNTHETIC", "false_12345@g.us_SYNTHETIC", "unknown"]) {
    message.attrs["data-id"] = "false_353870000001@c.us_SYNTHETIC";
    composer.focus = () => { message.attrs["data-id"] = changed; };
    assert.throws(() => ui.insert(ui.current(), "Reviewed draft"), /recipient changed/); passed++;
    check("focus-triggered recipient/type change never inserts: " + changed.split("@").at(-1), inputCalls === 0);
  }
  footer.append(node("div", {role: "textbox", contenteditable: "true", "aria-label": "Type a message"}));
  assert.throws(() => ui.current(), /composer/); passed++;
  const search = node("input", {"aria-label": "Search or start a new chat"});
  search.value = ""; search.focus = () => {}; search.dispatchEvent = () => {};
  let searchWrites = 0;
  class FakeInput {}
  Object.defineProperty(FakeInput.prototype, "value", {set(value) { searchWrites++; this.value = value; }});
  const searchUi = browserAdapter({querySelectorAll: () => [search]}, {HTMLInputElement: FakeInput, Event: class {}});
  assert.throws(() => searchUi.search("Alex Example", () => { throw new Error("revoked before native input"); }), /revoked/); passed++;
  check("native input search also checks authorization immediately before value write", searchWrites === 0 && search.value === "");
  const contentSearch = node("div", {"aria-label": "Search input textbox", contenteditable: "true"}, ["Alex Example"]);
  contentSearch.isContentEditable = true; contentSearch.focus = () => {};
  const searchEdits = [];
  const editableSearchUi = browserAdapter({querySelectorAll: () => [contentSearch],
    createRange: () => ({selectNodeContents() {}, collapse() {}}),
    execCommand: (kind, _, value) => { searchEdits.push({kind, value}); return true; }},
    {getSelection: () => ({removeAllRanges() {}, addRange() {}})});
  editableSearchUi.search("", () => {});
  check("contenteditable cleanup deletes its selected search instead of inserting an empty string", searchEdits.length === 1 && searchEdits[0].kind === "delete");
  assert.throws(() => editableSearchUi.search("", () => { throw new Error("query changed on focus"); }), /query changed/); passed++;
  check("cleanup deletion still rechecks ownership at the actual input boundary", searchEdits.length === 1);

  function reactionFixture(options = {}) {
    const state = {clicks: [], keys: [], grants: 0, waits: 0, open: false, closing: false, cancelled: false};
    const cid = "true_353870000001@c.us_SYNTHETIC-QUESTION", chipKey = "synthetic-parent-and-chip-state";
    const box = node("div", {role: "textbox", contenteditable: "true", "aria-label": "Type a message"}, ["My existing unsent draft"]);
    const chip = node("button", {"data-testid": "reaction-bubble", "aria-haspopup": "true", "aria-label": "reaction 👍. View reactions"}, [node("img", {alt: "👍"})]);
    const message = node("div", {"data-testid": "conv-msg-synthetic", "data-id": cid}, [chip]);
    const row = node("div", {role: "row"}, [message]), history = node("div", {"data-testid": "conversation-panel-messages"}, [node("div", {"data-tab": "8"}, [row])]);
    history.scrollTop = 0; history.scrollHeight = 500; history.clientHeight = 500;
    const main = node("div", {id: "main"}, [node("header", {}, [node("span", {dir: "auto"}, ["Alex Example"])]), history, node("footer", {}, [box])]);
    const page = node("div", {}, [node("div", {id: "pane-side"}), main]); page.visibilityState = "visible";
    const listeners = new Map(); page.addEventListener = (kind, callback) => listeners.set(kind, callback); page.removeEventListener = () => {};
    const toolbar = node("div", {role: "toolbar", "aria-label": "All message reactions, including the possibility to add a new one"},
      [node("button", {"aria-label": "Add reaction"})]);
    const header = node("div", {"data-testid": "reaction-count-header"}, [options.count || (options.both ? "2 reactions" : "1 reaction")]);
    function author(self, actorName = "Alex Example", emoji = "👍") {
      const title = node("div", {"data-testid": "cell-frame-title"}, self ? ["You"] :
        [node("button", {"aria-label": "Click to open profile details"}, [node("span", {dir: "auto"}, [actorName])])]);
      const marker = node("div", {"data-testid": self ? "reactions-details-cell-me" : "reactions-details-cell-not-me"}, [title, node("img", {alt: emoji})]);
      const authorRow = node("div", {role: self ? "button" : "listitem", "aria-label": self ? "You, ~Synthetic Account reacted with " + emoji + " Click to remove" : actorName + " reacted with " + emoji}, [marker]);
      authorRow.dispatchEvent = () => { throw new Error("author/removal control must never be activated"); };
      return authorRow;
    }
    const contact = author(!!options.self, options.actor || "Alex Example"), authorList = node("div", {role: "list"}, [node("div", {role: "dialog"}, [contact])]);
    if (options.both) authorList.childNodes[0].append(author(true));
    if (options.duplicateActor) authorList.childNodes[0].append(author(false));
    const popup = node("div", {tabindex: "-1", ...(options.modal ? {"aria-modal": "true"} : {})}, [toolbar, header, authorList]);
    if (options.editor) popup.append(node("input"));
    if (options.foreignDialog) popup.append(node("div", {role: "dialog"}, ["Unrelated modal"]));
    chip.dispatchEvent = e => {
      state.clicks.push(e.type);
      if (options.cancelAfterDown && e.type === "mousedown") state.cancelled = true;
      if (e.type === "click") { page.append(popup); state.open = true; }
    };
    toolbar.dispatchEvent = e => {
      state.keys.push(e.type + ":" + e.key);
      if (options.nativeEscape && (e.keyCode !== 27 || e.which !== 27 || e.type !== "keydown" || state.closing)) {
        state.chatClosed = true; page.childNodes = page.childNodes.filter(n => n !== main); return;
      }
      if (e.key === "Escape") state.closing = true;
    };
    const win = {location: {origin: "https://web.whatsapp.com"}, getComputedStyle: () => ({overflowY: "scroll"}),
      MouseEvent: class { constructor(type, fields) { this.type = type; Object.assign(this, fields); } },
      KeyboardEvent: class { constructor(type, fields) { this.type = type; Object.assign(this, fields); } },
      setTimeout(fn) { state.waits++; if (state.closing && !options.refuseClose) { page.childNodes = page.childNodes.filter(n => n !== popup); state.open = false; }
        if (options.onWait) options.onWait(state, {row, chip, page, popup, listeners, box}); fn(); },
      __northwindReadCapture: {begin: () => true, end: () => true, capture(proofs) {
        const proof = proofs && proofs.get(cid), reactions = proof ? proof.snapshot : {complete: false, items: []};
        return {chat: "Alex Example", events: [{id: cid, reactions}], unresolved: proof ? 0 : 1, unresolvedAt: proof ? [] : [1],
          reactionTargets: proof ? [] : [{id: cid, at: 1, key: chipKey}]};
      }},
    };
    const ui = browserAdapter(page, win), cmd = command({kind: "read", text: null}); ui.watch(); ui.beginRead(cmd);
    const target = ui.current(), guard = () => { if (state.cancelled) throw new Error("cancelled"); if (!ui.safe()) throw new Error(ui.safeReason()); ui.readView(target); };
    const grant = async () => { state.grants++; if (options.onGrant) options.onGrant(state, {popup, authorList, author}); guard(); return guard; };
    return {state, ui, target, popup, chip, row, page, cmd, box, run: () => ui.inspectReaction(target, {id: cid, at: 1, key: chipKey}, grant, guard)};
  }
  let rf = reactionFixture();
  check("observed contact popup inspection records author without touching composer", await rf.run() && rf.ui.readView(rf.target).events[0].reactions.items[0].actor_name === "Alex Example" && rf.box.textContent === "My existing unsent draft");
  check("only existing chip receives standard activation and toolbar receives one owned Escape", rf.state.clicks.join(",") === "mousedown,mouseup,click" && rf.state.keys.join(",") === "keydown:Escape" && rf.state.grants === 2 && !rf.state.open);
  check("popup closure waits for animation before accepting proof", rf.state.waits >= 2 && rf.ui.safe());
  check("reaction snapshot does not invent a timestamp or change parent direction", rf.ui.readView(rf.target).events[0].reactions.items[0].at === null);
  const nativeRf = reactionFixture({nativeEscape: true});
  check("full numeric Escape reaches legacy popup handler without closing the underlying chat", await nativeRf.run() && !nativeRf.state.chatClosed && nativeRf.ui.current().chat === "Alex Example");
  check("animated popup close gets no second Escape event that can fall through to the chat", nativeRf.state.keys.join(",") === "keydown:Escape" && !nativeRf.state.open && nativeRf.state.waits >= 2);
  rf.ui.endRead(rf.cmd); rf.ui.beginRead(command({kind: "read", text: null}));
  check("each new automatic read re-verifies rather than reusing prior popup proof", rf.ui.readView(rf.target).events[0].reactions.complete === false);
  rf = reactionFixture({self: true}); check("observed You removal row is read without clicking removal", await rf.run() && rf.ui.readView(rf.target).events[0].reactions.items[0].actor === "self");
  rf = reactionFixture({both: true}); check("two distinct observed actors are retained without confusing self with contact", await rf.run() && rf.ui.readView(rf.target).events[0].reactions.items.length === 2);
  rf = reactionFixture({actor: "Different Example"}); check("different author cannot count as current contact even inside owned popup", !await rf.run() && rf.ui.readView(rf.target).events[0].reactions.complete === false && !rf.state.open);
  for (const options of [{count: "2 reactions"}, {count: "3 reactions"}, {modal: true}, {editor: true}, {foreignDialog: true}, {duplicateActor: true, count: "2 reactions"}]) {
    rf = reactionFixture(options); let rejected = false;
    try { rejected = !await rf.run(); } catch (_) { rejected = true; }
    check("malformed or unsafe reaction popup cannot provide proof: " + JSON.stringify(options), rejected && rf.ui.readView(rf.target).events[0].reactions.complete === false);
  }
  rf = reactionFixture({cancelAfterDown: true}); await assert.rejects(rf.run(), /cancelled/); passed++;
  check("cancellation after mouse down blocks remaining activation and author proof", rf.state.clicks.join(",") === "mousedown" && rf.state.grants === 1);
  rf = reactionFixture({onWait: (state, nodes) => { if (state.waits === 1) nodes.listeners.get("keydown")({isTrusted: true}); }});
  await assert.rejects(rf.run(), /clicked or typed/); passed++;
  check("trusted user input stops without issuing Escape against their popup", rf.state.keys.length === 0);
  rf = reactionFixture({onGrant: (state, nodes) => { if (state.grants === 2) { nodes.authorList.childNodes[0].childNodes = []; nodes.authorList.childNodes[0].append(nodes.author(true)); } }});
  check("actor changing during close authorization cannot preserve stale contact proof", !await rf.run() && !rf.state.open && rf.ui.readView(rf.target).events[0].reactions.complete === false);
  rf = reactionFixture({onWait: (state, nodes) => { if (state.waits === 1) nodes.box.append(" user edit"); }});
  await assert.rejects(rf.run(), /message box changed/); passed++;
  check("composer change during popup leaves user text intact and stops further actions", rf.box.textContent.endsWith(" user edit") && rf.state.keys.length === 0);
  rf = reactionFixture({refuseClose: true}); await assert.rejects(rf.run(), /still open/); passed++;
  check("unclosed reaction popup never yields a verified snapshot", rf.ui.readView(rf.target).events[0].reactions.complete === false && rf.state.waits === 11);
  rf = reactionFixture(); rf.chip.attrs["data-testid"] = "react-entry-point";
  check("React entry point is never substituted for existing View reactions chip", !await rf.run() && rf.state.clicks.length === 0 && rf.state.grants === 0);
}

async function until(predicate) {
  for (let i = 0; i < 200; i++) { if (predicate()) return; await new Promise(resolve => setImmediate(resolve)); }
  throw new Error("synthetic worker did not settle");
}
async function readChecks() {
  const at = (clock) => new Date(2026, 10, 7, ...clock.split(":").map(Number)).getTime();
  const since = at("18:00"), stamp = at("22:00");
  const event = (clock, direction = "incoming", extra = {}) => ({kind: "bubble", chat: "Alex Example", direction,
    ours: direction === "outgoing", text: "Synthetic reply", what: "text", group: false, date: "07/11/2026", shown: clock,
    seen: stamp, id: "synthetic-" + clock + "-" + direction, ...extra});
  function harness(options = {}) {
    const state = {now: stamp, top: 250, grants: 0, boundaries: 0, moves: [], delivered: [], safe: true, ...options};
    const container = {};
    const recent = [event("21:00", "outgoing"), event("21:01")], older = [event("17:30", "outgoing"), event("18:05")];
    const ui = {
      readView: () => state.unavailable ? null : {container: state.replaced ? {} : container, top: state.top, height: state.height || 1000, viewport: 500,
        scrollable: state.scrollable !== false, events: state.events || (state.top >= 300 ? recent : older),
        unresolved: state.unresolvedAt ? state.unresolvedAt.length : 0, unresolvedAt: state.unresolvedAt || [], unresolvedRows: state.unresolvedRows, reactionTargets: state.reactionTargets || []},
      scrollRead(view, top, boundary) { boundary(); state.moves.push(top); state.top = top; if (state.afterMove) state.afterMove(state); },
      wait: async ms => { state.now += ms; if (state.onWait) state.onWait(state); },
      deliverRead: async (cmd, events, reading) => { state.delivered.push({events, reading}); if (state.deliveryChange) state.deliveryChange(state);
        return state.badReceipt ? {ok: false} : {ok: true, id: cmd.id, owner: cmd.owner, kept: events.length}; },
    };
    const cmd = command({kind: "read", text: null, since, created: stamp, expires: stamp + 90000});
    const guard = () => { if (!state.safe) throw Object.assign(new Error("Synthetic user interaction"), {status: "blocked"}); };
    const grant = async () => { state.grants++; if (state.grants === state.denyGrant) throw Object.assign(new Error("Read cancelled"), {status: "blocked"});
      const until = state.now + 2000;
      return () => { state.boundaries++; guard(); if (state.now >= until) throw Object.assign(new Error("Read grant expired"), {status: "blocked"}); }; };
    return {state, ui, cmd, run: () => readHistory(ui, cmd, {}, grant, guard, () => state.now, stamp + 20000)};
  }
  let h = harness(), result = await h.run();
  check("bounded read captures both recent and older pages through cutoff", result.complete && result.reason === "since_reached" && result.messages === 4 && result.oldest === at("17:30") && result.newest === at("21:01"));
  check("read metadata counts real captured directions and no message text", result.incoming === 2 && result.outgoing === 2 && Object.keys(result).sort().join(",") === "complete,incoming,messages,newest,oldest,outgoing,reason");
  check("read restores initial scroll position with separate permissions", h.state.top === 250 && h.state.grants === 4 && h.state.boundaries >= 4 && h.state.delivered.length === 1);
  const firstView = "a".repeat(32), laterView = "b".repeat(32);
  const orderedQ = event("21:00", "outgoing", {id: "ordered-question", text: "All okay for Hall?", view_id: firstView, i: 7, truncated: false});
  const orderedR = event("21:00", "incoming", {id: "ordered-reply", text: "Yes", view_id: firstView, i: 8, truncated: false});
  async function overlap(changes = {}, earlier = {}) {
    const firstQ = {...orderedQ, ...earlier}, firstR = {...orderedR, ...earlier};
    const sample = harness({onWait: state => { state.events = state.top >= 300 ? [firstQ, firstR] :
      [event("17:00"), {...orderedQ, view_id: laterView, i: 20, seen: stamp + 500},
       {...orderedR, view_id: laterView, i: 21, seen: stamp + 500, ...changes}]; }});
    const coverage = await sample.run(); return {coverage, events: sample.state.delivered[0].events};
  }
  let overlapRead = await overlap(), overlapQ = overlapRead.events.find(e => e.id === orderedQ.id), overlapR = overlapRead.events.find(e => e.id === orderedR.id);
  check("overlapping history preserves the first real same-view question/reply order", overlapRead.coverage.complete && overlapQ.view_id === firstView && overlapR.view_id === firstView && overlapQ.i === 7 && overlapR.i === 8);
  check("preserved order proof still retains the latest observation time", overlapQ.seen === stamp + 500 && overlapR.seen === stamp + 500);
  for (const changes of [{text: "No"}, {direction: "outgoing", ours: true}, {what: "voice"}, {media_type: "sticker"}, {reactions: {complete: true, items: []}}, {truncated: true}, {shown: "21:01"}, {date: "06/11/2026"}]) {
    overlapRead = await overlap(changes); overlapR = overlapRead.events.find(e => e.id === orderedR.id);
    check("changed message semantics cannot borrow old snapshot order: " + Object.keys(changes).join(","), overlapR.view_id === laterView && overlapR.i === 21);
  }
  overlapRead = await overlap({id: "different-stable-reply"});
  check("different stable IDs never merge merely because their visible words match", overlapRead.events.some(e => e.id === orderedR.id && e.view_id === firstView) && overlapRead.events.some(e => e.id === "different-stable-reply" && e.view_id === laterView));
  overlapRead = await overlap({}, {view_id: "unproved-view", i: -1});
  check("invalid earlier snapshot metadata cannot replace a later valid proof", overlapRead.events.find(e => e.id === orderedR.id).view_id === laterView);
  async function hydrated(firstChanges = {}, laterChanges = {}, gapIds = ["hydrating-reply"], extraGap = false) {
    const first = event("21:00", "unknown", {id: "hydrating-reply", text: "Yes", truncated: false, view_id: firstView, i: 7, ...firstChanges});
    const later = event("21:00", "incoming", {id: "hydrating-reply", text: "Yes", truncated: false, view_id: laterView, i: 12, ...laterChanges});
    const sample = harness({onWait: state => {
      const recent = state.top >= 300;
      state.events = recent ? [first] : [event("17:00"), later];
      state.unresolvedAt = recent ? [at("21:00"), ...(extraGap ? [null] : [])] : [];
      state.unresolvedRows = recent ? [...gapIds, ...(extraGap ? [null] : [])] : [];
    }});
    return {result: await sample.run(), state: sample.state};
  }
  let hydration = await hydrated();
  check("same stable message with later verified sender resolves only its direction gap", hydration.result.complete && hydration.result.reason === "since_reached" && hydration.result.messages === 2 && hydration.result.incoming === 2);
  check("direction hydration uses later snapshot order and stays in original read budget", hydration.state.delivered[0].events.find(e => e.id === "hydrating-reply").view_id === laterView && hydration.state.grants === 4);
  hydration = await hydrated({}, {direction: "outgoing", ours: true});
  check("hydration can identify an outgoing message without inventing an incoming reply", hydration.result.complete && hydration.result.outgoing === 1);
  for (const changes of [{text: "No"}, {date: "06/11/2026"}, {shown: "20:59"}, {what: "voice"}, {media_type: "sticker"}, {reactions: {complete: true, items: []}}]) {
    hydration = await hydrated({}, changes);
    check("changed semantics cannot retire an earlier direction gap: " + Object.keys(changes), !hydration.result.complete && hydration.result.reason === "history_unavailable");
  }
  for (const changes of [{truncated: true}, {group: true}, {what: "deleted"}, {reactions: {complete: false, items: []}}, {id: ""}]) {
    hydration = await hydrated(changes);
    check("another gap cannot be relabelled as direction-only: " + Object.keys(changes), !hydration.result.complete);
  }
  hydration = await hydrated({}, {}, [null]);
  check("legacy or anonymous direction gaps remain partial without stable-row metadata", !hydration.result.complete);
  hydration = await hydrated({}, {}, ["different-row"]);
  check("direction gap metadata cannot borrow a different stable message", !hydration.result.complete);
  hydration = await hydrated({}, {}, ["hydrating-reply"], true);
  check("resolving one stable sender never clears an unrelated anonymous gap", !hydration.result.complete);
  overlapRead = await overlap({direction: "outgoing", ours: true});
  check("conflicting explicit senders for the same stable message remain partial", !overlapRead.coverage.complete);
  for (const finalDirection of ["incoming", "outgoing"]) {
    const sample = harness({onWait: state => {
      const middle = state.top > 0 && state.top < 300;
      const direction = state.top >= 300 ? "incoming" : middle ? "unknown" : finalDirection;
      state.events = [event("21:00", direction, {id:"three-view-reply", text:"Yes", truncated:false}),
        ...(state.top === 0 ? [event("17:00")] : [])];
      state.unresolvedAt = middle ? [at("21:00")] : [];
      state.unresolvedRows = middle ? ["three-view-reply"] : [];
    }});
    const coverage = await sample.run();
    check("explicit sender survives an unknown intermediate page: " + finalDirection, coverage.complete === (finalDirection === "incoming") && sample.state.grants === 5);
  }
  h = harness({top: 500, onWait: state => { if (state.top < 500) state.height = 2000; }}); result = await h.run();
  check("initial bottom is restored to updated bottom after virtual history grows", result.complete && h.state.top === 1500);
  const alreadyCovered = [event("17:00", "outgoing"), event("21:00")];
  h = harness({events: alreadyCovered, onWait: state => { state.waits = (state.waits || 0) + 1; if (state.waits === 1) state.height = 1200; }});
  result = await h.run();
  check("a first-scroll height increase is settled before declaring old context incomplete", result.complete && result.reason === "since_reached" && result.messages === 2);
  check("bottom reflow retries the new bottom before any upward traversal", h.state.moves.slice(0, 2).join(",") === "500,700" && h.state.top === 250 && h.state.grants === 4);
  h = harness({events: alreadyCovered, onWait: state => { state.height = (state.height || 1000) + 100; }});
  result = await h.run();
  check("continually changing bottom fails promptly instead of scrolling months backwards", !result.complete && result.reason === "history_unavailable" && h.state.moves.slice(0, -1).join(",") === "500,600,700" && h.state.grants <= 5 && h.state.now - stamp <= 1000);
  check("unsettled bottom still delivers observed messages as partial context", result.messages === 2 && h.state.delivered.length === 1 && h.state.delivered[0].reading.complete === false);
  h = harness({onWait: state => { state.waits = (state.waits || 0) + 1; if (state.waits <= 2) state.height = 1000 + 100 * state.waits; state.events = state.top >= 300 ? [event("21:00")] : [event("17:00")]; }});
  result = await h.run();
  check("upward history movement starts only after the bounded bottom has actually settled", result.complete && h.state.moves.slice(0, 4).join(",") === "500,600,700,300" && h.state.grants <= 18);
  h = harness();
  let finishCapture, readFinished = false;
  h.ui.deliverRead = (cmd, events) => new Promise(resolve => { finishCapture = () => resolve({ok: true, id: cmd.id, owner: cmd.owner, kept: events.length}); });
  const pendingRead = h.run().then(value => { readFinished = true; return value; });
  await until(() => finishCapture);
  check("read does not finish while local event receipt is pending", !readFinished);
  finishCapture(); await pendingRead;
  check("acknowledged event receipt releases successful read", readFinished);
  h = harness({badReceipt: true}); await assert.rejects(h.run(), error => error.status === "unknown"); passed++;
  check("failed delivery never produces successful read metadata", h.state.delivered.length === 1);
  h = harness({denyGrant: 2}); await assert.rejects(h.run(), /cancelled/); passed++;
  check("cancelled read stops before next scroll or delivery", h.state.moves.length === 1 && h.state.delivered.length === 0);
  h = harness({onWait: state => { state.safe = false; }}); await assert.rejects(h.run(), /interaction/); passed++;
  check("trusted interaction prevents further movement and capture delivery", h.state.moves.length === 1 && h.state.delivered.length === 0);
  h = harness({onWait: state => { state.replaced = true; }}); await assert.rejects(h.run(), /history changed/); passed++;
  check("replacement history area is never followed", h.state.moves.length === 1 && h.state.delivered.length === 0);
  h = harness({scrollable: false}); result = await h.run();
  check("unsupported scroll area reports only available partial history", !result.complete && result.reason === "history_unavailable" && h.state.moves.length === 0 && result.messages === 2);
  h = harness({unavailable: true}); result = await h.run();
  check("missing history hook is not called a completed chat read", !result.complete && result.messages === 0 && result.oldest === null && result.newest === null && result.reason === "history_unavailable");
  h = harness({unresolvedAt: [at("17:00")]}); result = await h.run();
  check("unreadable media proved older than cutoff does not poison requested coverage", result.complete);
  h = harness({unresolvedAt: [at("20:00")]}); result = await h.run();
  check("unreadable current media makes coverage partial", !result.complete && result.reason === "history_unavailable");
  h = harness({events: [event("17:00"), event("21:00", "incoming", {truncated: true})]}); result = await h.run();
  check("truncated current reply cannot certify complete history even if adapter misses gap count", !result.complete && result.reason === "history_unavailable" && h.state.delivered[0].events.some(e => e.truncated));
  h = harness({events: [event("17:00", "incoming", {truncated: true}), event("21:00")]}); result = await h.run();
  check("truncation proved older than required context does not poison requested coverage", result.complete);
  h = harness({unresolvedAt: [null]}); result = await h.run();
  check("undated unsupported message cannot be assumed outside requested range", !result.complete);
  h = harness({events: [event("17:00"), event("21:00", "outgoing", {reactions: {complete: false, items: []}})],
    unresolvedAt: [at("21:00")], reactionTargets: [{id: "q", key: "chip", at: at("21:00")}]});
  h.ui.inspectReaction = async (target, reaction, grant, guard) => {
    const opening = await grant(); opening(); guard(); const closing = await grant(); closing();
    h.state.events[1].reactions = {complete: true, items: [{parent_id: h.state.events[1].id, emoji: "👍", actor: "contact", actor_name: "Alex Example", at: null}]};
    h.state.unresolvedAt = []; h.state.reactionTargets = [];
  };
  result = await h.run();
  check("read resolves the owned popup before treating its initial unknown author as a lasting gap", result.complete && h.state.grants === 5 && h.state.delivered[0].events[1].reactions.items[0].at === null);
  h = harness({events: [event("17:00"), event("21:00")], unresolvedAt: [at("21:00")],
    reactionTargets: [1, 2, 3].map(i => ({id: "q" + i, key: "chip" + i, at: at("21:00")}))});
  let inspected = 0;
  h.ui.inspectReaction = async (target, reaction, grant) => { (await grant())(); (await grant())(); inspected++; };
  result = await h.run();
  check("at most two existing reaction popups share the bounded movement budget", !result.complete && inspected === 2 && h.state.grants === 7);
  h = harness({events: [event("21:00")], unresolvedAt: [at("21:00")], reactionTargets: [{id: "q", key: "chip", at: at("21:00")}]});
  h.ui.inspectReaction = async (target, reaction, grant) => { (await grant())(); (await grant())(); };
  result = await h.run();
  check("unresolved author is not retried each virtual history page and cannot certify coverage", !result.complete && h.state.grants <= 18);
  h = harness({events: [event("17:00", "incoming", {what: "voice", text: ""})], unresolvedAt: [at("17:00")]}); result = await h.run();
  check("explicit dated voice bubble reaches receiver as manual content", result.messages === 1 && result.incoming === 1 && h.state.delivered[0].events[0].what === "voice");
  h = harness({events: [event("17:00", "incoming", {group: true})]}); result = await h.run();
  check("group records are excluded from a direct read capture", result.messages === 0 && !result.complete);
  h = harness({events: [event("21:00")], onWait: state => { state.now += 1200; }}); result = await h.run();
  check("slow history loading returns a bounded partial capture", !result.complete && ["timeout", "history_unavailable"].includes(result.reason) && h.state.grants <= 18);
  check("bubble clock validation rejects invented date and malformed time", bubbleTime(event("99:99")) === null && bubbleTime(event("21:00", "incoming", {date: "31/02/2026"})) === null);
  for (const fields of [{phone: null}, {since: undefined}, {since: stamp + 6000}, {since: stamp - 49 * 3600000}, {text: "must not type"}]) {
    const f = fixture({now: stamp}); const r = await f.run(command({kind: "read", text: null, since, created: stamp, expires: stamp + 90000, ...fields}), owner);
    check("malformed read cannot open or type: " + Object.keys(fields), r.status === "blocked" && f.state.opened === 0 && f.state.inserted === 0);
  }
  const f = fixture({now: stamp, targetText: "Existing unsent draft"});
  const readContainer = {}; f.ui.readView = () => ({container: readContainer, top: 0, height: 400, viewport: 400, scrollable: false, events: [], unresolved: 0});
  f.ui.deliverRead = async (cmd, events) => ({ok: true, id: cmd.id, owner: cmd.owner, kept: events.length});
  const r = await f.run(command({kind: "read", text: null, since, created: stamp, expires: stamp + 90000}), owner);
  check("executor read preserves existing draft and never requests draft authorization", r.status === "read" && f.state.targetText === "Existing unsent draft" && f.state.inserted === 0 && f.state.permissions.join(",") === "open,read,read" && f.state.readBegun && f.state.readEnded);
  {
    const g = fixture({now: stamp}), box = {}; let released = 0;
    g.ui.readView = () => ({container: box, top: 0, height: 400, viewport: 400, scrollable: false, events: [], unresolved: 0});
    g.ui.deliverRead = async (cmd, events) => ({ok: true, id: cmd.id, owner: cmd.owner, kept: events.length});
    g.ui.releaseFocus = () => { released++; };
    const rr = await g.run(command({kind: "read", text: null, since, created: stamp, expires: stamp + 90000}), owner);
    check("an automatic read takes the cursor out of the guard's chat when it ends", rr.status === "read" && released === 1);
    const b = fixture({now: stamp}); b.ui.userBusy = () => true;
    const rb = await b.run(command({kind: "read", text: null, since, created: stamp, expires: stamp + 90000}), owner);
    check("using WhatsApp: an automatic read waits - nothing opened, searched or asked", rb.status === "user_active" && b.state.opened === 0 &&
          b.state.searches.length === 0 && b.state.permissions.length === 0 && !b.state.readBegun);
    const d = fixture(); let draftReleased = 0; d.ui.releaseFocus = () => { draftReleased++; };
    const rd = await d.run(command(), owner);
    check("a draft he asked for keeps its cursor where it is", rd.status === "drafted" && draftReleased === 0);
    const u = fixture({now: stamp, unread: 3}), ubox = {};
    u.ui.readView = () => ({container: ubox, top: 0, height: 400, viewport: 400, scrollable: false, events: [], unresolved: 0});
    u.ui.deliverRead = async (cmd, events) => ({ok: true, id: cmd.id, owner: cmd.owner, kept: events.length});
    const ru = await u.run(command({kind: "read", text: null, since, created: stamp, expires: stamp + 90000}), owner);
    check("a read reports how many messages were unread when it opened the chat", ru.status === "read" && ru.unread_before === 3);
    const quiet = fixture({now: stamp}), qbox = {};
    quiet.ui.readView = () => ({container: qbox, top: 0, height: 400, viewport: 400, scrollable: false, events: [], unresolved: 0});
    quiet.ui.deliverRead = async (cmd, events) => ({ok: true, id: cmd.id, owner: cmd.owner, kept: events.length});
    const rq = await quiet.run(command({kind: "read", text: null, since, created: stamp, expires: stamp + 90000}), owner);
    check("...and says nothing when there were none", rq.status === "read" && !("unread_before" in rq));
    const mid = fixture({now: stamp, unread: 4, onAuthorize: (s, q) => { if (q.step === "open") s.interrupted = true; }});
    const rm = await mid.run(command({kind: "read", text: null, since, created: stamp, expires: stamp + 90000}), owner);
    check("he clicks in WhatsApp during a read: wait and retry (user_active), not a stop", rm.status === "user_active" && mid.state.inserted === 0);
    check("a read interrupted before guarded opening does not claim an unread badge was opened", !("unread_before" in rm));
    const openedMid = fixture({now: stamp, unread: 4, onAuthorize: (s, q) => { if (q.step === "read") s.interrupted = true; }});
    const openedResult = await openedMid.run(command({kind: "read", text: null, since, created: stamp, expires: stamp + 90000}), owner);
    check("a read interrupted after opening retains the original unread warning", openedResult.status === "user_active" && openedMid.state.opened === 1 && openedResult.unread_before === 4);
    const manualOpen = fixture({unread: 4}); const manualResult = await manualOpen.run(command({kind: "open", text: null}), owner);
    check("unread warning metadata belongs only to automatic reads", manualResult.status === "opened" && !("unread_before" in manualResult));
    const dmid = fixture({onAuthorize: (s, q) => { if (q.step === "open") s.interrupted = true; }});
    const rdm = await dmid.run(command(), owner);
    check("...a draft he asked for still just stops (blocked) - he should see it", rdm.status === "blocked");
  }
  for (const [label, fields, expected] of [
    ["matching Contact info for a LID", {phone: null, direct: true, profilePossible: true, onProfile: state => { state.profileCurrent = true; state.profilePhone = "353870000001"; }}, "read"],
    ["different Contact info phone", {phone: null, direct: true, profilePossible: true, onProfile: state => { state.profileCurrent = true; state.profilePhone = "353870000002"; }}, "blocked"],
    ["no numeric phone proof", {phone: null, direct: true, profilePossible: true}, "blocked"],
    ["conflicting message JIDs", {numericConflict: true}, "blocked"],
    ["matching numeric JID and visible profile", {profilePhone: "353870000001", profileCurrent: true}, "read"],
    ["saved JID conflicts with visible same-name profile", {profilePhone: "353870000002", profileCurrent: true}, "blocked"],
    ["phone changes during read", {onWait: state => { state.phone = "353870000002"; }}, "blocked"],
  ]) {
    const test = fixture({now: stamp, ...fields});
    const history = {}; let delivered = 0;
    test.ui.readView = () => ({container: history, top: 0, height: 400, viewport: 400, scrollable: false, events: [], unresolved: 0});
    test.ui.deliverRead = async (cmd, events) => { delivered++; return {ok: true, id: cmd.id, owner: cmd.owner, kept: events.length}; };
    const response = await test.run(command({kind: "read", text: null, since, created: stamp, expires: stamp + 90000}), owner);
    check("read verifies saved numeric identity: " + label, response.status === expected && test.state.inserted === 0 && delivered === (expected === "read" ? 1 : 0));
    if (label === "matching Contact info for a LID") check("LID reading explicitly opens Contact info for phone proof", test.state.profileOpened === 1 && test.state.permissions.includes("profile"));
  }
}

function workerFixture(persist = {queue: [], draftActions: {}}) {
  let listener, alarm, nextAction = null, wrongProof = false, failAck = false, dispatch = [], posts = [], acknowledgements = [], authorizations = [], deniedStep = null;
  const secret = "synthetic-secret", tasks = new Map(); let timer = 0;
  const copy = value => JSON.parse(JSON.stringify(value));
  const setTimer = (fn, ms) => { const id = ++timer; tasks.set(id, {fn, ms}); return id; };
  const chrome = {runtime: {id: "synthetic-extension", onMessage: {addListener: fn => { listener = fn; }}},
    storage: {local: {get: async defaults => Object.assign(copy(defaults), copy(persist)), set: async values => Object.assign(persist, copy(values))}},
    tabs: {sendMessage: async (tab, msg, options) => {
      check("worker persists attempt before any browser dispatch", Object.hasOwn(persist.draftActions, msg.action.id));
      dispatch.push({tab, msg: copy(msg), options});
      for (const step of ["search", "open", "profile", "draft"]) {
        const result = await new Promise(resolve => listener({type: "draft_authorize", id: msg.action.id, owner: msg.action.owner, step}, sender, resolve));
        if (!result.ok) return {id: msg.action.id, owner: msg.action.owner, status: "blocked", detail: "Synthetic app refused authorization."};
      }
      return {id: msg.action.id, owner: msg.action.owner, status: "drafted", detail: "Synthetic unsent draft verified."};
    }},
    alarms: {create: () => {}, onAlarm: {addListener: fn => { alarm = fn; }}}};
  const context = vm.createContext({importScripts: () => {}, READER: {port: 8765, token: secret}, Date, crypto: webcrypto, TextEncoder, AbortController,
    setTimeout: setTimer, clearTimeout: id => tasks.delete(id), chrome,
    fetch: async (url, options) => {
      check("worker communicates only with local receiver", url.startsWith("http://127.0.0.1:8765/"));
      if (url.includes("/wa/hello?")) {
        const nonce = new URL(url).searchParams.get("nonce");
        return {ok: true, status: 200, json: async () => ({proof: wrongProof ? "wrong" : createHmac("sha256", secret).update(nonce).digest("hex")})};
      }
      const body = JSON.parse(options.body);
      if (url.endsWith("/wa/authorize")) {
        authorizations.push(body);
        return {ok: body.step !== deniedStep, status: body.step === deniedStep ? 409 : 200,
          json: async () => body.step === deniedStep ? {ok: false} : {...body, ok: true, expires: Date.now() + 2000}};
      }
      if (url.endsWith("/wa/action")) {
        acknowledgements.push(body);
        if (failAck) throw new Error("synthetic lost ACK");
        return {ok: true, status: 200, json: async () => ({ok: true})};
      }
      posts.push(body);
      const action = nextAction; nextAction = null;
      return {ok: true, status: 200, json: async () => ({ok: true, kept: body.events.length, action})};
    }});
  vm.runInContext(fs.readFileSync(path.join(reader, "worker.js"), "utf8"), context);
  const sender = {id: chrome.runtime.id, tab: {id: 3}, documentId: "synthetic-document-1", frameId: 0, url: "https://web.whatsapp.com/"};
  const api = {persist, dispatch, posts, acknowledgements, authorizations, sender, chrome, tasks,
    ready: (who = sender, capability = "drafts-v4") => { let result; listener({type: "drafts_ready", capability}, who, value => { result = value; }); return result; },
    beat: (who = sender) => listener({type: "beat", beat: {pane: true, rows: 2, paused: false, useHere: false}}, who),
    poll: () => alarm({name: "flush"}), action: action => { nextAction = action; },
    wrongProof: value => { wrongProof = value; }, failAck: value => { failAck = value; },
    denyStep: value => { deniedStep = value; },
    authorize: (request, who = sender) => new Promise(resolve => listener({type: "draft_authorize", ...request}, who, resolve)),
  };
  return api;
}

async function workerChecks() {
  let w = workerFixture();
  check("wrong extension cannot announce action capability", !w.ready({...w.sender, id: "other-extension"}));
  check("iframe cannot announce action capability", !w.ready({...w.sender, frameId: 2}));
  check("documentless legacy sender cannot announce action capability", !w.ready({...w.sender, documentId: undefined}));
  check("old content script cannot claim new authorization capability", !w.ready(w.sender, "drafts-v1"));
  check("drafts-v2 reader cannot claim automatic-read capability", !w.ready(w.sender, "drafts-v2"));
  check("capability is bound to browser-provided tab/document", w.ready().owner === owner);
  w.beat(); await until(() => w.posts.length === 1);
  check("fresh capability accompanies authenticated heartbeat", w.posts[0].client.owner === owner && w.posts[0].client.capability === "drafts-v4");
  const c = command({created: Date.now(), expires: Date.now() + 90000});
  w.action(c); w.poll(); await until(() => w.acknowledgements.length === 1);
  check("known exact tab/document is the only browser target", w.dispatch.length === 1 && w.dispatch[0].tab === 3 && w.dispatch[0].options.documentId === "synthetic-document-1");
  check("verified result is ACKed", w.acknowledgements[0].status === "drafted");
  check("worker forwards every mutation boundary only to authenticated app", w.authorizations.map(x => x.step).join(",") === "search,open,profile,draft");
  check("idle action cannot mint an execution grant", !(await w.authorize({id: c.id, owner, step: "draft"})).ok);
  w.action(c); w.poll(); await until(() => w.acknowledgements.length === 2);
  check("duplicate command is not dispatched twice", w.dispatch.length === 1 && w.acknowledgements[1].status === "unknown");
  const restarted = workerFixture(w.persist); restarted.ready(); restarted.action(c); restarted.beat();
  await until(() => restarted.acknowledgements.length === 1);
  check("worker restart retains consumed request IDs", restarted.dispatch.length === 0 && restarted.acknowledgements[0].status === "unknown");
  w = workerFixture(); w.ready(); w.wrongProof(true); w.beat();
  await new Promise(resolve => setTimeout(resolve, 20));
  check("unproved receiver sees no heartbeat or action ACK", w.posts.length === 0 && w.acknowledgements.length === 0 && w.dispatch.length === 0);
  w = workerFixture(); w.ready(); w.action(command({owner: "3:other-document", created: Date.now(), expires: Date.now() + 90000})); w.beat();
  await until(() => w.acknowledgements.length === 1);
  check("wrong owner never reaches content script", w.dispatch.length === 0 && w.acknowledgements[0].status === "blocked");
  w = workerFixture(); w.ready(); w.action(command({created: Date.now(), expires: Date.now() + 90000})); w.failAck(true); w.beat();
  await until(() => w.acknowledgements.length >= 2);
  check("lost ACK never repeats DOM execution", w.dispatch.length === 1);
  const afterLost = workerFixture(w.persist); afterLost.ready(); afterLost.action(w.dispatch[0].msg.action); afterLost.beat();
  await until(() => afterLost.acknowledgements.length === 1);
  check("lost ACK plus restart still cannot replay mutation", afterLost.dispatch.length === 0 && afterLost.acknowledgements[0].status === "unknown");
  w = workerFixture(); w.beat(); await until(() => w.posts.length === 1);
  check("ordinary legacy heartbeat never claims draft capability", w.posts[0].client === null);
  w = workerFixture(); w.ready(); w.denyStep("draft"); w.action(command({created: Date.now(), expires: Date.now() + 90000})); w.beat();
  await until(() => w.acknowledgements.length === 1);
  check("server revocation before typing is reported blocked", w.acknowledgements[0].status === "blocked" && w.authorizations.map(x => x.step).join(",") === "search,open,profile,draft");
  w = workerFixture(); w.ready();
  let finishDispatch;
  w.chrome.tabs.sendMessage = () => new Promise(resolve => { finishDispatch = resolve; });
  const held = command({created: Date.now(), expires: Date.now() + 90000}); w.action(held); w.beat();
  await until(() => finishDispatch);
  const ask = {id: held.id, owner, step: "open"};
  check("different browser document cannot authorize an in-bullseye action", !(await w.authorize(ask, {...w.sender, documentId: "synthetic-document-2"})).ok && w.authorizations.length === 0);
  w.wrongProof(true);
  check("authorization still requires a fresh HMAC proof", !(await w.authorize(ask)).ok && w.authorizations.length === 0);
  w.wrongProof(false);
  const replacement = {...w.sender, documentId: "synthetic-document-2"}; w.ready(replacement); w.beat(replacement);
  check("document replacement revokes the old content's authorization path", !(await w.authorize(ask)).ok);
  finishDispatch({id: held.id, owner, status: "blocked"});
  await until(() => w.acknowledgements.length === 1);
}

(async () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(reader, "manifest.json"), "utf8"));
  check("manifest description meets Chrome's 132-character limit", manifest.description.length <= 132);
  check("bridge adds no new extension permissions", manifest.permissions.join(",") === "storage,alarms");
  check("draft executor is installed beside the read-only reader", manifest.content_scripts[0].js.join(",") === "reader.js,drafts.js");
  await executorChecks(); await domChecks(); await readChecks(); await workerChecks();
  console.log(`${passed}/${passed} draft bridge checks passed`);
})().catch(error => { console.error(error); process.exitCode = 1; });
