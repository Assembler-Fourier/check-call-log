"use strict";
// Synthetic DOM, Chrome APIs and loopback receiver only: never opens a browser or makes a network request.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {webcrypto, createHmac} = require("node:crypto");

const readerDir = path.resolve(__dirname, "../../reader");
let passed = 0;
function check(label, test) {
  assert.ok(test, label);
  passed += 1;
  console.log("PASS  " + label);
}

function timers() {
  let id = 0, now = 0;
  const tasks = new Map();
  return {
    tasks,
    setTimeout: (fn, delay) => { tasks.set(++id, {fn, delay, due: now + delay}); return id; },
    clearTimeout: (key) => tasks.delete(key),
    fire(delay) {
      for (const [key, task] of [...tasks]) {
        if (task.delay === delay) { tasks.delete(key); task.fn(); }
      }
    },
    advance(ms) {
      const end = now + ms;
      for (;;) {
        const next = [...tasks].filter(([,task]) => task.due <= end).sort((a,b) => a[1].due - b[1].due)[0];
        if (!next) break;
        now = next[1].due; tasks.delete(next[0]); next[1].fn();
      }
      now = end;
    },
  };
}

async function until(condition, label) {
  for (let i = 0; i < 200; i += 1) {
    if (condition()) return;
    await new Promise(setImmediate);
  }
  throw new Error("Did not settle: " + label);
}

function readerChecks() {
  const clock = timers(), messages = [], observers = [];
  let beat = null;
  // Small independent DOM fixture: attributes, descendant/ancestor selectors and
  // text/image nodes. No browser, live page, network or copied staff content.
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
    get previousElementSibling() {
      if (!this.parentElement) return null;
      const nodes = this.parentElement.childNodes.filter((n) => n.nodeType === 1), at = nodes.indexOf(this);
      return at > 0 ? nodes[at - 1] : null;
    }
    getAttribute(name) { return Object.hasOwn(this.attrs, name) ? this.attrs[name] : null; }
    hasAttribute(name) { return Object.hasOwn(this.attrs, name); }
    contains(node) { return node === this || this.childNodes.some((n) => n === node || n.nodeType === 1 && n.contains(node)); }
    matches(selector) {
      return selector.split(",").some((branch) => {
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
      const cls = selector.match(/^\.([\w-]+)/);
      if (cls && !(this.attrs.class || "").split(/\s+/).includes(cls[1])) return false;
      const id = selector.match(/^#([\w-]+)/);
      if (id && this.attrs.id !== id[1]) return false;
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
  const emoji = (value) => node("img", {alt: value, "data-plain-text": value, "data-testid": "selectable-text"});
  const payload = (children) => node("span", {"data-testid": "selectable-text"}, Array.isArray(children) ? children : [children]);
  let nextId = 0;
  function bubble(children, at, options = {}) {
    const content = payload(children);
    const copy = options.noPre ? content : node("div", {"data-pre-plain-text": `[${at}, ${options.date || "07/11/2026"}] ${options.sender || "Test Guard"}: `},
      [content, node("span", {"aria-hidden": "true"}, ["HIDDEN TIMESTAMP"])]);
    const inner = [copy, node("div", {"data-testid": "msg-meta"}, [at + " Delivered"])];
    if (options.outgoing) inner.push(node("span", {"aria-label": "You:"}));
    if (options.incoming && !options.noAuthor) inner.push(node("span", {"aria-label": (options.sender || "Test Guard") + " :"}));
    if (options.quote) inner.unshift(node("div", {"data-testid": "quoted-message"}, [payload(options.quote)]));
    return node("div", {role: "row", ...(options.incoming ? {class: "message-in"} : {})},
      [node("div", {"data-testid": "conv-msg-synthetic", "data-id": "synthetic-" + (++nextId)}, inner)]);
  }
  function separator(text) { return node("div", {tabindex: "-1"}, [node("span", {}, [text])]); }
  const initial = bubble("Yes", "21:40");
  const conversation = node("div", {"data-tab": "8"}, [initial]);
  const main = node("div", {}, [node("header", {}, [node("span", {dir: "auto"}, ["Test Guard"])]),
    node("div", {"data-testid": "conversation-panel-messages"}, [conversation])]);
  let activeMain = main, historyReads = 0;
  const mainQueryAll = main.querySelectorAll.bind(main);
  main.querySelectorAll = selector => { if (selector === '[role="row"]') historyReads++; return mainQueryAll(selector); };
  const pane = node("div");
  let activePane = pane;
  const side = node("div", {id: "side"}, [pane]);
  const shell = node("div", {}, [side, main]);
  const document = {
    querySelector: (selector) => selector === "#pane-side" ? activePane : selector === "#main" ? activeMain : shell.querySelector(selector),
    querySelectorAll: (selector) => shell.querySelectorAll(selector), contains: () => true, visibilityState: "visible", hasFocus: () => true,
  };
  const context = vm.createContext({
    window: {}, document, Date, crypto: webcrypto, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    setInterval: (fn) => { beat = fn; },
    MutationObserver: class {
      constructor(callback) { this.callback = callback; observers.push(this); }
      observe(target, options) { this.target = target; this.options = options; this.active = true; }
      disconnect() { this.active = false; }
    },
    chrome: {runtime: {sendMessage: (msg) => { messages.push(msg); return Promise.resolve(); }}},
  });
  vm.runInContext(fs.readFileSync(path.join(readerDir, "reader.js"), "utf8"), context);
  clock.fire(0);
  const events = () => messages.flatMap((m) => m.events || []);
  check("startup reads the conversation already open without a DOM mutation", events().length === 1 && events()[0].text === "Yes");
  check("open conversation observer is attached immediately at startup", observers.some(o => o.active && o.target === main));
  beat(); clock.fire(0);
  check("heartbeat does not duplicate an already reported bubble", events().length === 1);
  conversation.append(bubble("Wrong text sorry", "21:41"));
  beat(); clock.fire(0);
  check("heartbeat finds a new bubble even when no observed mutation fires", events().length === 2 && events()[1].text === "Wrong text sorry");
  beat(); clock.fire(0);
  check("repeated heartbeat retains bubble deduplication", events().length === 2);
  check("explicit pre-plain sender identifies an incoming bubble", events()[0].direction === "incoming" && events()[0].ours === false);

  function scan() { beat(); clock.fire(0); }
  function mutate(target, type, attributeName, drain = true) {
    for (const observer of observers) {
      const options = observer.options;
      if (!observer.active || (observer.target !== target && !(options.subtree && observer.target.contains(target)))) continue;
      if (type === "attributes" && (!options.attributes || options.attributeFilter && !options.attributeFilter.includes(attributeName))) continue;
      if (type === "characterData" && !options.characterData || type === "childList" && !options.childList) continue;
      observer.callback([{target, type, attributeName}]);
    }
    if (drain) { clock.fire(250); clock.fire(0); } // coalesced mutations only; no heartbeat/manual scan
  }
  const list = node("div", {role: "row"}, [node("span", {title: "Test Guard"}), node("span", {title: "Yes all good"})]);
  list.visibleText = "Test Guard\n02:31\nYes all good";
  pane.append(list); scan();
  const firstPreview = events().filter((e) => e.kind === "line").at(-1);
  check("a preview with no tick is unknown, never an incoming reply", firstPreview.direction === "unknown" && !Object.hasOwn(firstPreview, "ours"));
  list.append(node("span", {"aria-label": "2 unread messages"}, ["2"]));
  list.childNodes[1].attrs.title = "Yes next message"; scan();
  check("an unread badge alone cannot establish preview direction", events().at(-1).direction === "unknown" && !Object.hasOwn(events().at(-1), "ours"));
  list.append(node("span", {"data-icon": "msg-dblcheck"})); scan();
  check("a delayed outgoing data-icon corrects the unknown preview", events().at(-1).direction === "outgoing" && events().at(-1).ours === true);
  check("direction race never emitted false incoming preview", events().filter((e) => e.kind === "line").every((e) => e.ours !== false));
  list.childNodes[1].attrs.title = "Reacted 👍 to Yes"; scan();
  check("reaction-shaped outgoing text cannot override explicit outgoing proof", events().at(-1).ours === true);
  const beforeDraft = events().length;
  list.visibleText = "Test Guard\n02:31\nDraft: \nUnsent question for tomorrow";
  list.childNodes = [list.childNodes[0]];
  scan();
  check("unsent draft without a second title emits no message", events().length === beforeDraft);
  list.visibleText = "Test Guard\n02:31"; scan();
  check("partially rendered empty preview emits no message", events().length === beforeDraft);
  list.append(node("span", {title: "Reacted 👍 to Yes"}));
  list.append(node("span", {"data-icon": "msg-dblcheck"})); scan();
  check("draft and partial rows leave the preceding message state unchanged", events().length === beforeDraft);

  const previewCount = () => events().filter((e) => e.kind === "line").length;
  const paused = () => messages.filter((m) => m.type === "beat").at(-1).beat.paused;
  const oldSearch = side.append(node("div", {"aria-label": "Search or start a new chat"}));
  const nativeSearch = side.append(node("input", {role: "textbox", type: "text", "data-tab": "3", "aria-label": ""}));
  nativeSearch.value = "Synthetic Person";
  const beforeSearch = previewCount();
  list.childNodes[1].attrs.title = "Historical Yes from a search result";
  scan();
  check("unlabelled native sidebar search pauses the heartbeat despite an empty old editor", paused());
  check("search-result rows never emit live list previews", previewCount() === beforeSearch);
  nativeSearch.attrs["aria-label"] = "Unrecognized sidebar field";
  scan();
  check("a nonempty unknown sidebar textbox remains conservatively paused", paused() && previewCount() === beforeSearch);
  conversation.append(bubble("Current reply while the list is filtered", "21:42")); scan();
  check("paused sidebar does not suppress authoritative open-chat bubbles", paused() && events().at(-1).kind === "bubble" &&
    events().at(-1).text === "Current reply while the list is filtered" && previewCount() === beforeSearch);
  nativeSearch.value = "";
  list.childNodes[1].attrs.title = "Reacted 👍 to Yes";
  scan();
  check("clearing search resumes All chats without poisoned preview state", !paused() && previewCount() === beforeSearch);
  const otherTextbox = main.append(node("input", {role: "textbox", type: "text", "data-tab": "3", "aria-label": ""}));
  otherTextbox.value = "Text outside the sidebar";
  scan();
  check("an unrelated native textbox outside the sidebar does not pause the chat list", !paused());
  oldSearch.append("Legacy labelled search text"); scan();
  check("legacy labelled search still pauses beside an empty native input", paused() && previewCount() === beforeSearch);
  oldSearch.childNodes = [];
  const filters = side.append(node("div", {"aria-label": "chat-list-filters"}, [node("button", {"aria-selected": "true"}, ["Unread"])]));
  scan();
  check("Unread filter still blocks list previews when both search fields are empty", paused() && previewCount() === beforeSearch);
  filters.childNodes[0].childNodes[0].nodeValue = "All"; scan();
  check("All filter plus empty sidebar searches restores normal reading", !paused());

  const mixed = bubble(["Yes all good ", emoji("👍")], "02:32", {outgoing: true, quote: "No yesterday"});
  conversation.append(mixed); scan();
  const mixedEvent = events().filter((e) => e.kind === "bubble").at(-1);
  check("mixed message keeps its emoji exactly once", mixedEvent.text === "Yes all good 👍");
  check("message excludes quote, timestamp and delivery labels", !/yesterday|TIMESTAMP|Delivered/.test(mixedEvent.text));
  check("explicit You label wins over the self-chat-like sender header", mixedEvent.direction === "outgoing" && mixedEvent.ours === true);
  check("bubble retains its stable message identity", mixedEvent.id === mixed.querySelector('[data-testid^="conv-msg-"]').getAttribute("data-id"));

  conversation.append(separator("Today"));
  conversation.append(bubble([emoji("👍")], "02:33", {noPre: true, outgoing: true})); scan();
  const bare = events().filter((e) => e.kind === "bubble").at(-1);
  const today = new Date(), todayText = String(today.getDate()).padStart(2, "0") + "/" + String(today.getMonth() + 1).padStart(2, "0") + "/" + today.getFullYear();
  check("emoji-only message without pre-plain wrapper is read", bare.text === "👍" && bare.shown === "02:33");
  check("emoji-only message uses explicit Today separator", bare.date === todayText && bare.ours === true);
  const quotedMetadata = bubble([emoji("✅")], "02:33", {noPre: true, incoming: true});
  const quote = node("div", {"data-testid": "quoted-message"}, [
    node("div", {"data-pre-plain-text": "[04:01, 01/01/2020] Other Participant: "}, [payload("Old quote")]),
    node("div", {"data-testid": "msg-meta"}, ["04:01"]),
    node("div", {"data-testid": "conv-msg-old", "data-id": "quoted-id"})]);
  quote.parentElement = quotedMetadata;
  quotedMetadata.childNodes.unshift(quote);
  conversation.append(quotedMetadata); scan();
  check("quoted metadata cannot supply a bare emoji's date, clock or identity",
    events().at(-1).date === todayText && events().at(-1).shown === "02:33" && events().at(-1).id !== "quoted-id" && events().at(-1).text === "✅");
  conversation.append(separator("06/11/2026"));
  conversation.append(bubble([emoji("👌")], "02:34", {noPre: true, incoming: true})); scan();
  check("an older emoji retains the explicit historical date", events().at(-1).date === "06/11/2026" && events().at(-1).direction === "incoming");
  check("explicit incoming author proves a direct-chat emoji", events().at(-1).group === false);
  conversation.append(bubble([emoji("👍")], "02:34", {noPre: true, incoming: true, noAuthor: true})); scan();
  check("no-author emoji is not assumed to be a guard's direct reply", events().at(-1).group === true);
  conversation.append(bubble([emoji("👍")], "02:34", {noPre: true, incoming: true, sender: "Other Participant"})); scan();
  check("another group participant's emoji is excluded", events().at(-1).group === true);
  const conflicted = bubble("All good", "02:34", {incoming: true, outgoing: true});
  conversation.append(conflicted); scan();
  check("contradictory explicit sender markers are unknown", events().at(-1).direction === "unknown" && !Object.hasOwn(events().at(-1), "ours"));
  const quotedStatus = bubble("Yes current reply", "02:34", {incoming: true});
  quotedStatus.querySelector('[data-testid^="conv-msg-"]').append(node("div", {"data-testid": "quoted-message"},
    [node("span", {"aria-label": "You:"}), node("span", {"data-icon": "msg-dblcheck"}), payload("Old outgoing message")]));
  conversation.append(quotedStatus); scan();
  check("quoted outgoing markers cannot change the current reply direction", events().at(-1).direction === "incoming" && events().at(-1).text === "Yes current reply");
  conversation.append(bubble("Today", "02:35", {quote: "Today"}));
  conversation.append(bubble([emoji("✅")], "02:36", {noPre: true, incoming: true})); scan();
  check("message and quoted Today do not reset the day separator", events().at(-1).date === "06/11/2026" && events().at(-1).text === "✅");
  for (const invalid of ["Earlier", "31/02/2026"]) {
    conversation.append(separator(invalid));
    conversation.append(bubble([emoji("👍")], "02:37", {noPre: true, incoming: true}));
    const count = events().length; scan();
    check("unknown or invalid separator never borrows an older date: " + invalid, events().length === count);
  }
  const count = events().length;
  conversation.append(bubble([node("img", {alt: "Photo"})], "02:38", {date: "31/02/2026"})); scan();
  check("invalid pre-plain calendar date is rejected", events().length === count);
  conversation.append(bubble(["Hello ", node("img", {alt: "Photo"}), emoji("👎")], "02:39")); scan();
  check("image media labels are omitted while negative emoji is preserved", events().at(-1).text === "Hello 👎");

  const delayedAuthor = node("span", {"aria-label": ""});
  const observedPreview = node("div", {role: "row"}, [node("span", {title: "Observed Guard"}),
    node("span", {title: "Yes observed reply"}), delayedAuthor]);
  observedPreview.visibleText = "Observed Guard\n02:40\nYes observed reply";
  pane.append(observedPreview); mutate(pane, "childList");
  check("observer sees a new ambiguous preview without a heartbeat", events().at(-1).chat === "Observed Guard" && events().at(-1).direction === "unknown");
  delayedAuthor.attrs["aria-label"] = "Observed Guard:"; mutate(delayedAuthor, "attributes", "aria-label");
  check("attribute-only explicit preview author is captured promptly", events().at(-1).chat === "Observed Guard" && events().at(-1).direction === "incoming");
  delayedAuthor.attrs.hidden = ""; mutate(delayedAuthor, "attributes", "hidden");
  check("hidden preview author is not reused as sender proof", events().at(-1).direction === "unknown");
  delete delayedAuthor.attrs.hidden; mutate(delayedAuthor, "attributes", "hidden");
  check("visible explicit author correction is captured without heartbeat", events().at(-1).direction === "incoming");
  delayedAuthor.attrs["aria-label"] = ""; mutate(delayedAuthor, "attributes", "aria-label");
  const delayedTick = node("span", {"data-icon": "pending-placeholder"});
  observedPreview.append(delayedTick); mutate(observedPreview, "childList");
  delayedTick.attrs["data-icon"] = "msg-dblcheck"; mutate(delayedTick, "attributes", "data-icon");
  check("attribute-only outgoing tick resolves a preview promptly", events().at(-1).direction === "outgoing" && events().at(-1).ours === true);
  const stableCount = events().length;
  mutate(delayedTick, "attributes", "data-icon");
  check("repeated sender metadata mutations do not duplicate an event", events().length === stableCount);
  const beforeListBurst = historyReads, beforeBurstEvents = events().length;
  observedPreview.childNodes[1].attrs.title = "Continuous mutation reply";
  mutate(observedPreview, "attributes", "title", false);
  clock.advance(100); mutate(delayedTick, "attributes", "data-icon", false);
  clock.advance(100); mutate(delayedTick, "attributes", "data-icon", false);
  clock.advance(50);
  check("continuous mutations cannot postpone a new reply beyond the first 250ms window", events().length === beforeBurstEvents + 1 && events().at(-1).text === "Continuous mutation reply");
  check("sidebar-only changes do not reparse unrelated open-chat history", historyReads === beforeListBurst);
  mutate(delayedTick, "attributes", "data-icon", false); clock.advance(250);
  check("later unchanged mutation windows keep event deduplication", events().length === beforeBurstEvents + 1);
  const beforeTogether = events().length, beforeTogetherReads = historyReads;
  observedPreview.childNodes[1].attrs.title = "Sidebar and chat changed together";
  conversation.append(bubble("Concurrent incoming reply", "02:40", {incoming:true}));
  mutate(observedPreview, "attributes", "title", false);
  mutate(conversation, "childList", undefined, false);
  clock.advance(250);
  check("one coalesced window preserves both sidebar and conversation changes", events().slice(beforeTogether).some(e => e.text === "Sidebar and chat changed together") && events().slice(beforeTogether).some(e => e.text === "Concurrent incoming reply"));
  check("simultaneous observers share one fresh history capture", historyReads === beforeTogetherReads + 1);
  const observersBeforeBeat = observers.length; beat(); clock.fire(0);
  check("heartbeat retains the existing observer when the conversation container is unchanged", observers.length === observersBeforeBeat);
  const replacement = node("div", {}, [node("header", {}, [node("span", {dir: "auto"}, ["Replacement Guard"])]),
    node("div", {"data-testid": "conversation-panel-messages"}, [node("div", {"data-tab": "8"}, [bubble("Replacement reply", "02:40", {incoming:true,sender:"Replacement Guard"})])])]);
  activeMain = replacement; mutate(pane, "childList");
  check("sidebar change reattaches and captures a replaced conversation promptly", events().at(-1).chat === "Replacement Guard" && observers.some(o => o.active && o.target === replacement) && !observers.some(o => o.active && o.target === main));
  activeMain = null; mutate(pane, "childList");
  check("closing a conversation disconnects its detached observer", !observers.some(o => o.active && o.target === replacement));
  activeMain = main; mutate(pane, "childList");
  const replacementPreview = node("div", {role:"row"}, [node("span", {title:"Replacement Sidebar Guard"}), node("span", {title:"Fresh replacement preview"})]);
  replacementPreview.visibleText = "Replacement Sidebar Guard\n02:40\nFresh replacement preview";
  const replacementPane = node("div", {}, [replacementPreview]);
  activePane = replacementPane; beat(); clock.fire(0);
  check("heartbeat recovers a replaced sidebar and captures its current preview", events().at(-1).chat === "Replacement Sidebar Guard" && observers.some(o => o.active && o.target === replacementPane) && !observers.some(o => o.active && o.target === pane));
  activePane = pane; beat(); clock.fire(0);

  conversation.append(separator("Today"));
  const lateBubble = bubble("Yes explicit bubble", "02:41", {noPre: true});
  const lateBubbleAuthor = node("span", {"aria-label": ""});
  lateBubble.querySelector('[data-testid^="conv-msg-"]').append(lateBubbleAuthor);
  conversation.append(lateBubble); mutate(conversation, "childList");
  check("new bubble without sender proof remains unknown", events().at(-1).kind === "bubble" && events().at(-1).direction === "unknown");
  lateBubbleAuthor.attrs["aria-label"] = "Test Guard:"; mutate(lateBubbleAuthor, "attributes", "aria-label");
  check("attribute-only bubble author resolves direct incoming proof promptly", events().at(-1).kind === "bubble" && events().at(-1).direction === "incoming" && !events().at(-1).group);
  const datedBubble = bubble("Yes dated bubble", "02:42", {sender: "Unresolved author"});
  conversation.append(datedBubble); mutate(conversation, "childList");
  const datedHeader = datedBubble.querySelector('[data-pre-plain-text]');
  datedHeader.attrs["data-pre-plain-text"] = "[02:42, 07/11/2026] Test Guard: ";
  mutate(datedHeader, "attributes", "data-pre-plain-text");
  check("attribute-only dated sender header is rescanned promptly", events().at(-1).text === "Yes dated bubble" && events().at(-1).direction === "incoming" && !events().at(-1).group);
  const payloadText = datedBubble.querySelector('[data-testid="selectable-text"]').childNodes[0];
  payloadText.nodeValue = "Changed visible reply";
  const beforeUnrelated = events().length;
  mutate(datedBubble, "attributes", "style");
  check("unrelated style changes do not trigger message rescans", events().length === beforeUnrelated);
  mutate(payloadText, "characterData");
  check("open-chat text-node changes are captured without a heartbeat", events().at(-1).text === "Changed visible reply" && events().at(-1).direction === "incoming");
  const readHook = context.window.__northwindReadCapture, readId = "a".repeat(32);
  check("automatic read scope starts once for its exact chat", readHook.begin(readId, "Test Guard") && !readHook.begin("b".repeat(32), "Test Guard"));
  const suppressed = bubble("Automatic traversal reply", "02:50", {incoming: true});
  conversation.append(suppressed);
  const beforeSuppressed = events().length;
  const beforeOwnedReads = historyReads;
  mutate(conversation, "childList");
  check("automatic traversal does not publish partial target-chat bubbles", events().length === beforeSuppressed);
  check("automatic traversal skips passive history work that would only be discarded", historyReads === beforeOwnedReads);
  const scopedCapture = readHook.capture();
  check("explicit read capture still receives suppressed bubble", scopedCapture.events.some(e => e.text === "Automatic traversal reply"));
  check("explicit read still obtains one complete fresh DOM snapshot", historyReads === beforeOwnedReads + 1);
  main.querySelector("header span[dir='auto']").childNodes[0].nodeValue = "Other Guard";
  mutate(main, "childList");
  check("scope does not stop passive reading of an unrelated open chat", events().length > beforeSuppressed && events().at(-1).chat === "Other Guard");
  main.querySelector("header span[dir='auto']").childNodes[0].nodeValue = "Test Guard";
  check("unrelated action cannot end automatic capture suppression", !readHook.end("b".repeat(32)));
  check("matching read scope ends normally", readHook.end(readId));
  const afterEnd = events().length; mutate(main, "childList");
  check("ending read never republishes captured pages as untagged passive events", events().length === afterEnd);
  const beforeCapture = messages.length;
  const capture = context.window.__northwindReadCapture.capture();
  check("authorized capture hook can return already reported loaded bubbles", capture.chat === "Test Guard" && capture.events.some(e => e.text === "Changed visible reply"));
  check("capture hook is a pure read with no message or page mutation", messages.length === beforeCapture && payloadText.nodeValue === "Changed visible reply");
  check("unresolved capture metadata contains bounded dates and optional stable row identities", Number.isInteger(capture.unresolved) && capture.unresolvedAt.length === capture.unresolved && capture.unresolvedRows.length === capture.unresolved && capture.unresolvedAt.every(at => at === null || Number.isFinite(at)) && capture.unresolvedRows.every(id => id === null || typeof id === "string" && id.length <= 200));
  const hydratingRow = bubble("Yes sender settles", "02:55", {incoming:true,outgoing:true});
  conversation.append(hydratingRow);
  const hydratingId = hydratingRow.querySelector('[data-testid^="conv-msg-"]').getAttribute("data-id");
  let gapView = readHook.capture();
  check("only sender uncertainty on an otherwise complete stable row is locally resolvable", gapView.unresolvedRows.includes(hydratingId) && gapView.events.find(e => e.id === hydratingId).direction === "unknown");
  hydratingRow.querySelector('[aria-label="You:"]').attrs.hidden = "";
  gapView = readHook.capture();
  check("explicit sender hydration removes the same row's current gap without changing its body", !gapView.unresolvedRows.includes(hydratingId) && gapView.events.find(e => e.id === hydratingId).direction === "incoming" && gapView.events.find(e => e.id === hydratingId).text === "Yes sender settles");
  for (const [label, body, options] of [["truncated", "x".repeat(301), {}], ["deleted", "This message was deleted", {}],
       ["unknown date", "Yes", {date:"31/02/2026"}], ["group author", "Yes", {sender:"Different Author"}]]) {
    const blockedRow = bubble(body, "02:56", {incoming:true,outgoing:true,...options});
    conversation.append(blockedRow);
    const id = blockedRow.querySelector('[data-testid^="conv-msg-"]').getAttribute("data-id");
    check("sender metadata cannot retire another capture gap: " + label, !readHook.capture().unresolvedRows.includes(id));
  }
  const voice = bubble([], "03:00", {incoming: true});
  voice.querySelector('[data-testid^="conv-msg-"]').append(node("span", {"data-icon": "ptt"}));
  conversation.append(voice); mutate(conversation, "childList");
  check("explicit voice bubble is captured as manual content without invented words", events().at(-1).what === "voice" && events().at(-1).text === "" && events().at(-1).direction === "incoming");
  const photo = bubble([], "03:01", {noPre: true, incoming: true});
  photo.querySelector('[data-testid^="conv-msg-"]').append(node("div", {"data-testid": "image-thumb", "aria-label": "Open picture"}));
  conversation.append(photo); mutate(conversation, "childList");
  check("observed image-thumb bubble captures date and incoming author for manual review", events().at(-1).what === "media" && events().at(-1).text === "" && events().at(-1).direction === "incoming" && !events().at(-1).group);
  const deleted = bubble("This message was deleted", "03:02", {incoming: true});
  conversation.append(deleted); mutate(conversation, "childList");
  check("explicit deleted bubble retains its type instead of becoming a reply", events().at(-1).what === "deleted");
  const reactionRow = bubble("Question text", "03:03", {outgoing: true});
  reactionRow.querySelector('[data-testid^="conv-msg-"]').append(node("button", {"data-testid": "reaction-bubble", "aria-label": "reaction 👍. View reactions"}));
  const beforeReaction = context.window.__northwindReadCapture.capture().unresolved;
  conversation.append(reactionRow); mutate(conversation, "childList");
  const afterReaction = context.window.__northwindReadCapture.capture();
  check("attached reaction without its author's proof marks coverage partial", afterReaction.unresolved === beforeReaction + 1 && afterReaction.events.at(-1).direction === "outgoing" && afterReaction.events.at(-1).what === "text");
  const unsupported = bubble([], "03:04", {incoming: true});
  conversation.append(unsupported);
  const unsupportedCapture = context.window.__northwindReadCapture.capture();
  check("unsupported loaded body is recorded as a dated gap, not silently complete", unsupportedCapture.unresolved === afterReaction.unresolved + 1 && Number.isFinite(unsupportedCapture.unresolvedAt.at(-1)));
  const savedHistory = conversation.childNodes;
  conversation.childNodes = [];
  const undatedGap = bubble([], "03:00", {noPre: true, incoming: true});
  conversation.append(undatedGap);
  const oldBoundary = bubble("Old dated boundary", "03:01", {date: "02/11/2026"});
  conversation.append(oldBoundary);
  const newBoundary = bubble("Current dated boundary", "03:02", {date: "07/11/2026"});
  conversation.append(newBoundary);
  const boundedGap = context.window.__northwindReadCapture.capture();
  check("undated gap before monotonic old dated row gets only an upper bound", boundedGap.unresolvedAt[0] === new Date(2026, 10, 2, 3, 1).getTime() && boundedGap.events.length === 2 && !boundedGap.events.some(e => e.id === undatedGap.querySelector('[data-testid^="conv-msg-"]').getAttribute("data-id")));
  conversation.childNodes = [undatedGap, newBoundary, oldBoundary];
  check("nonmonotonic dated rows cannot bound an undated gap", context.window.__northwindReadCapture.capture().unresolvedAt[0] === null);
  conversation.childNodes = [oldBoundary, newBoundary, undatedGap];
  check("gap after last dated row keeps its unknown time", context.window.__northwindReadCapture.capture().unresolvedAt[0] === null);
  conversation.childNodes = savedHistory;
  const longReply = "Yes " + "all good ".repeat(40) + "but I cannot come tonight";
  const longIncoming = bubble(longReply, "22:10", {incoming: true});
  const unresolvedBeforeLong = context.window.__northwindReadCapture.capture().unresolved;
  conversation.append(longIncoming); mutate(conversation, "childList");
  const longEvent = events().at(-1);
  check("clipped incoming text retains explicit truncation, never an unqualified affirmative prefix", longEvent.direction === "incoming" && longEvent.truncated === true && longEvent.text.length === 300 && !longEvent.text.includes("cannot"));
  const longCapture = context.window.__northwindReadCapture.capture();
  check("clipped bubble prevents complete history coverage in its dated range", longCapture.unresolved === unresolvedBeforeLong + 1 && longCapture.events.at(-1).truncated === true && Number.isFinite(longCapture.unresolvedAt.at(-1)));
  conversation.append(bubble("All okay for Hall? " + "all good ".repeat(40), "22:11", {outgoing: true})); mutate(conversation, "childList");
  check("clipped outgoing question also carries incomplete-context provenance", events().at(-1).direction === "outgoing" && events().at(-1).truncated === true && events().at(-1).text.length === 300);
  const exactBody = "Yes " + "a".repeat(296), growing = bubble(exactBody, "22:12", {incoming: true});
  conversation.append(growing); mutate(conversation, "childList");
  check("a complete exactly300-character bubble is not labelled clipped", events().at(-1).text.length === 300 && events().at(-1).truncated === false);
  const beforeGrowth = events().length;
  growing.querySelector('[data-testid="selectable-text"]').append(" but cannot attend"); mutate(growing, "childList");
  check("same-ID same-prefix body growth republishes its truncation warning", events().length === beforeGrowth + 1 && events().at(-1).text === exactBody && events().at(-1).truncated === true);
  list.childNodes[1].attrs.title = longReply; scan();
  const longPreview = events().filter(e => e.kind === "line").at(-1);
  check("clipped sidebar preview preserves its truncation flag and body limit", longPreview.truncated === true && longPreview.text.length === 300);
  list.childNodes[1].attrs.title = exactBody; scan();
  check("unclipped sidebar body has no stale truncation flag", events().filter(e => e.kind === "line").at(-1).truncated === false);
  const beforeTypedHistory = conversation.childNodes;
  conversation.childNodes = [];
  const contextBoundary = bubble("Earlier context", "00:00");
  const knownVoice = bubble([], "10:00", {incoming: true});
  knownVoice.querySelector('[data-testid^="conv-msg-"]').append(node("span", {"data-icon": "mic"}));
  const knownPhoto = bubble([], "10:01", {incoming: true});
  knownPhoto.querySelector('[data-testid^="conv-msg-"]').append(node("div", {"data-testid": "image-thumb", "aria-label": "Open picture"}));
  const laterQuestion = bubble("All okay for Hall?", "19:00", {outgoing: true});
  const laterAnswer = bubble("Yes", "19:01", {incoming: true});
  for (const row of [contextBoundary, knownVoice, knownPhoto, laterQuestion, laterAnswer]) conversation.append(row);
  const typedContext = context.window.__northwindReadCapture.capture();
  check("dated directed voice and photo are complete captured context before a later question and Yes", typedContext.unresolved === 0 && typedContext.unresolvedAt.length === 0 && typedContext.events.length === 5 && typedContext.events.map(e => e.what).join(",") === "text,voice,media,text,text");
  check("media bodies remain typed manual content while later question and reply retain direction", typedContext.events[1].text === "" && typedContext.events[2].text === "" && typedContext.events[3].direction === "outgoing" && typedContext.events[4].direction === "incoming" && typedContext.events[4].text === "Yes");
  const proofQ = typedContext.events[3], proofR = typedContext.events[4];
  check("one captured DOM view gives all stable bubbles one fresh order identifier", /^[a-f0-9]{32}$/.test(proofQ.view_id) && typedContext.events.every(e => e.view_id === proofQ.view_id) && proofQ.i < proofR.i && proofQ.id !== proofR.id);
  const nextView = context.window.__northwindReadCapture.capture();
  check("a later capture has a different view proof while stable message IDs remain unchanged", nextView.events[3].view_id !== proofQ.view_id && nextView.events[3].id === proofQ.id && nextView.events[4].id === proofR.id);
  const noIdMessage = laterAnswer.querySelector('[data-testid^="conv-msg-"]'), savedId = noIdMessage.attrs["data-id"];
  delete noIdMessage.attrs["data-id"];
  const withoutId = context.window.__northwindReadCapture.capture().events.at(-1);
  check("dated message without stable ID remains captured but never carries ordering proof", withoutId.text === "Yes" && !Object.hasOwn(withoutId, "id") && !Object.hasOwn(withoutId, "view_id"));
  noIdMessage.attrs["data-id"] = savedId;
  const completeRows = conversation.childNodes.slice();
  conversation.append(bubble("This message was deleted", "18:59", {outgoing: true}));
  check("deleted context remains incomplete because its original subject is lost", context.window.__northwindReadCapture.capture().unresolved === 1);
  conversation.childNodes = completeRows.slice();
  const unknownVoice = bubble([], "18:58", {sender: "Unverified Person"});
  unknownVoice.querySelector('[data-testid^="conv-msg-"]').append(node("span", {"data-icon": "mic"}));
  conversation.append(unknownVoice);
  check("voice with an unverified sender still makes capture incomplete", context.window.__northwindReadCapture.capture().unresolved === 1);
  conversation.childNodes = completeRows.slice();
  const attachedReaction = bubble("Other text", "18:57", {incoming: true});
  attachedReaction.querySelector('[data-testid^="conv-msg-"]').append(node("button", {"data-testid": "reaction-bubble", "aria-label": "reaction thumbs up. View reactions"}));
  conversation.append(attachedReaction);
  check("unattributed attached reaction still prevents complete coverage beside known media", context.window.__northwindReadCapture.capture().unresolved === 1);
  conversation.childNodes = completeRows.slice(); conversation.append(bubble(longReply, "18:56", {incoming: true}));
  check("clipped text remains incomplete even when every media sender and date is known", context.window.__northwindReadCapture.capture().unresolved === 1);
  conversation.childNodes = completeRows.slice();
  const sticker = bubble([], "19:01", {noPre: true, incoming: true});
  const stickerMedia = node("span", {"data-testid": "sticker-container"}, [node("div", {role: "button", "aria-label": "Generated animation description"},
    [node("img", {alt: "Generated animation description", hidden: ""}), node("canvas")])]);
  sticker.querySelector('[data-testid^="conv-msg-"]').append(stickerMedia);
  conversation.childNodes.unshift(separator("07/11/2026")); conversation.childNodes[0].parentElement = conversation;
  conversation.append(sticker);
  const stickerCapture = context.window.__northwindReadCapture.capture(), stickerEvent = stickerCapture.events.at(-1);
  check("observed sticker container is incoming empty-caption media without generated image words", stickerEvent.what === "media" && stickerEvent.media_type === "sticker" && stickerEvent.text === "" && stickerEvent.direction === "incoming" && stickerCapture.unresolved === 0);
  check("same-view sticker order retains stable parent ID after the affirmative", stickerEvent.id && stickerEvent.date === stickerCapture.events.at(-2).date && stickerEvent.view_id === stickerCapture.events.at(-2).view_id && stickerEvent.i > stickerCapture.events.at(-2).i);
  list.childNodes[1].attrs.title = "Sticker"; scan();
  check("exact typed Sticker preview retains subtype while arbitrary captions do not", events().filter(e => e.kind === "line").at(-1).media_type === "sticker");
  list.childNodes[1].attrs.title = "Sticker is fine"; scan();
  check("a text mentioning Sticker does not gain the empty-media subtype", !Object.hasOwn(events().filter(e => e.kind === "line").at(-1), "media_type"));
  const reactionQuestion = bubble("All okay for Hall?", "19:02", {outgoing: true});
  const reactionChip = node("button", {"data-testid": "reaction-bubble", "aria-haspopup": "true", "aria-label": "reaction 👍. View reactions"}, [node("img", {alt: "👍"})]);
  reactionQuestion.querySelector('[data-testid^="conv-msg-"]').append(reactionChip); conversation.append(reactionQuestion);
  const hook = context.window.__northwindReadCapture, proofs = new Map(), proofReadId = "c".repeat(32);
  check("passive capture does not invent or remove authoritative reaction snapshots", !Object.hasOwn(hook.capture().events.at(-1), "reactions"));
  hook.begin(proofReadId, "Test Guard");
  const unprovedReaction = hook.capture(proofs), targetReaction = unprovedReaction.reactionTargets[0];
  check("automatic reaction without author inspection is explicitly incomplete", unprovedReaction.events.at(-1).reactions.complete === false && targetReaction.id === unprovedReaction.events.at(-1).id && unprovedReaction.unresolved === 1);
  const reactionSnapshot = {complete: true, items: [{parent_id: targetReaction.id, emoji: "👍", actor: "contact", actor_name: "Test Guard", at: null}]};
  proofs.set(targetReaction.id, {key: targetReaction.key, snapshot: reactionSnapshot, row: reactionQuestion, chip: reactionChip});
  const provenReaction = hook.capture(proofs);
  check("verified reaction stays attached to outgoing question with unknown reaction time", provenReaction.unresolved === 0 && provenReaction.events.at(-1).direction === "outgoing" && provenReaction.events.at(-1).reactions.items[0].at === null && provenReaction.events.at(-1).shown === "19:02");
  const reactionMessage = reactionQuestion.querySelector('[data-testid^="conv-msg-"]');
  reactionMessage.childNodes.pop(); reactionMessage.append(node("button", {...reactionChip.attrs}, [node("img", {alt: "👍"})]));
  check("replacement chip with identical emoji cannot reuse another actor's proof", hook.capture(proofs).events.at(-1).reactions.complete === false);
  reactionMessage.childNodes.pop(); reactionMessage.append(reactionChip);
  reactionChip.attrs["aria-label"] = "reaction 👎. View reactions";
  check("changed reaction chip cannot borrow earlier author proof", hook.capture(proofs).events.at(-1).reactions.complete === false);
  reactionQuestion.querySelector('[data-testid^="conv-msg-"]').childNodes.pop();
  const removedReaction = hook.capture(proofs).events.at(-1);
  check("fresh automatic capture records explicit removal rather than preserving stale reaction", removedReaction.reactions.complete === true && removedReaction.reactions.items.length === 0);
  hook.end(proofReadId);
  check("ending the action returns passive capture to omitted reaction metadata", !Object.hasOwn(hook.capture().events.at(-1), "reactions"));
  conversation.childNodes = [];
  const sendBody = "Exact  " + "a".repeat(320) + " ending";
  const longSent = bubble([sendBody, node("br"), emoji("👍"), " last line"], "22:40", {outgoing: true, quote: "Wrong quoted body"});
  const incomingSame = bubble(sendBody, "22:41", {incoming: true});
  conversation.append(longSent); conversation.append(incomingSame);
  const sendSnapshot = hook.outgoingSnapshot("Test Guard");
  check("send snapshot preserves full body, spacing, newline and emoji beyond observer cap", sendSnapshot.messages.length === 1 && sendSnapshot.messages[0].text === sendBody + "\n👍 last line" && sendSnapshot.messages[0].text.length > 300);
  check("send snapshot retains all stable DOM IDs but only verified outgoing text candidates", sendSnapshot.ids.length === 2 && sendSnapshot.ids[0] === sendSnapshot.messages[0].id && Number.isFinite(sendSnapshot.messages[0].at));
  check("full-body send verification does not expand normal observer payloads", hook.capture().events[0].text.length === 300 && hook.capture().events[0].truncated === true);
  check("send snapshot cannot be borrowed by a different named chat", hook.outgoingSnapshot("Different Guard") === null);
  const duplicateSent = bubble("Other text", "22:42", {outgoing: true});
  duplicateSent.querySelector('[data-testid^="conv-msg-"]').attrs["data-id"] = sendSnapshot.ids[0]; conversation.append(duplicateSent);
  check("duplicate stable IDs prevent a send-verification snapshot", hook.outgoingSnapshot("Test Guard") === null);
  conversation.childNodes.pop();
  [...longSent.querySelectorAll('[data-testid="selectable-text"]')].find(n => !n.closest('[data-testid="quoted-message"]')).append(node("img", {alt: "Unknown media description"}));
  check("unknown inline media cannot be silently omitted from exact sent text", hook.outgoingSnapshot("Test Guard").messages.length === 0);
  conversation.childNodes = beforeTypedHistory;
  const beatsBefore = messages.filter(m => m.type === "beat").length;
  context.window.__northwindReadCapture.refresh();
  check("read completion can publish a fresh heartbeat without waiting thirty seconds", messages.filter(m => m.type === "beat").length === beatsBefore + 1 && messages.at(-1).awaited === true);

}

async function workerChecks(hangAt) {
  const clock = timers(), sent = [];
  const secret = "synthetic-reader-test-secret";
  let queue = [], listener, alarm, mode = hangAt;
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const hang = (signal) => new Promise((resolve, reject) => {
    signal.addEventListener("abort", () => reject(new Error("synthetic timeout")), {once: true});
  });
  const fetch = async (url, options) => {
    assert.ok(url.startsWith("http://127.0.0.1:8765/"));
    if (mode === "offline") throw new Error("synthetic offline receiver");
    if (url.includes("/wa/hello?")) {
      if (mode === "hello") return hang(options.signal);
      const nonce = new URL(url).searchParams.get("nonce");
      return {ok: true, status: 200, json: async () => ({proof: createHmac("sha256", secret).update(nonce).digest("hex")})};
    }
    const body = JSON.parse(options.body);
    sent.push(copy(body));
    if (mode === "post") return hang(options.signal);
    if (mode === "body") return {ok: true, status: 200, json: () => hang(options.signal)};
    return {ok: true, status: 200, json: async () => ({ok: true, kept: body.events.length})};
  };
  const context = vm.createContext({
    importScripts: () => {}, READER: {port: 8765, token: secret}, Date, crypto: webcrypto,
    TextEncoder, AbortController, fetch, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    chrome: {
      storage: {local: {get: async () => ({queue: copy(queue)}), set: async (value) => { queue = copy(value.queue); }}},
      runtime: {onMessage: {addListener: (fn) => { listener = fn; }}},
      alarms: {create: () => {}, onAlarm: {addListener: (fn) => { alarm = fn; }}},
    },
  });
  vm.runInContext(fs.readFileSync(path.join(readerDir, "worker.js"), "utf8"), context);
  const sender = {tab: {id: 1}, url: "https://web.whatsapp.com/"};
  const event = (text) => ({kind: "line", chat: "Test Guard", ours: false, text, what: "text", seen: Date.now()});
  listener({type: "events", events: [event("Yes")]}, sender);
  await until(() => clock.tasks.size > 0 && (hangAt === "hello" || sent.length === 1), hangAt + " started");
  check(hangAt + ": first event is persisted before contacting the receiver", queue.length === 1);
  if (hangAt === "hello") check("an unproved receiver receives no messages", sent.length === 0);
  listener({type: "events", events: [event("Wrong text sorry")]}, sender);
  mode = "offline";
  clock.fire(5000);
  await until(() => queue.length === 2 && clock.tasks.size === 0, hangAt + " serialization released");
  check(hangAt + ": timeout releases the serial queue and persists the newer event", queue[1].text === "Wrong text sorry");
  check(hangAt + ": an unacknowledged event is retained after timeout", queue[0].text === "Yes");
  mode = "healthy";
  alarm({name: "flush"});
  await until(() => queue.length === 0, hangAt + " healthy retry");
  check(hangAt + ": reconnect delivers both events in order", sent.at(-1).events.map((e) => e.text).join("|") === "Yes|Wrong text sorry");
  check(hangAt + ": completed requests clear their timeout", clock.tasks.size === 0);
}

(async () => {
  readerChecks();
  for (const phase of ["hello", "post", "body"]) await workerChecks(phase);
  console.log(`\n${passed} of ${passed} checks passed`);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
