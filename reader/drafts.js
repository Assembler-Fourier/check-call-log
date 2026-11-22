// Authorized READ / OPEN / DRAFT and one explicitly reviewed SEND. No Enter key or WhatsApp private API.
(function (root) {
  "use strict";
  const CAPABILITY = "drafts-v4";
  const outcomes = new Set(["opened", "drafted", "already_draft", "read", "user_active", "sent", "existing_draft", "not_found", "ambiguous_chat", "group_chat", "blocked", "expired", "failed", "unknown"]);
  const same = (value) => String(value || "").replace(/\r\n/g, "\n");
  const fault = (status, detail) => Object.assign(new Error(detail), {status});

  // The adapter is deliberately small: tests use synthetic state, never a real browser or recipient.
  const validUnicode = text => !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?:^|[^\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(text);
  // Chrome slows a tab that is not on screen (minimised, another tab in front, or a window fully covering it):
  // its 100 ms waits stretch to seconds, so a search started there ran out of time half-way (7 Nov, live). Such a
  // request is refused before anything is touched, and stops if WhatsApp goes out of sight part-way.
  const HIDDEN = "WhatsApp is not on screen, and Chrome slows hidden tabs down. Show the WhatsApp tab (it can sit " +
                 "beside Check Call Log, as long as it is not covered or minimised), then try again. Nothing was changed.";
  function bubbleTime(event) {
    const d = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(event.date || ""), t = /^([01]?\d|2[0-3]):([0-5]\d)$/.exec(event.shown || "");
    if (!d || !t) return null;
    const at = new Date(+d[3], +d[2] - 1, +d[1], +t[1], +t[2]);
    return at.getFullYear() === +d[3] && at.getMonth() === +d[2] - 1 && at.getDate() === +d[1] ? at.getTime() : null;
  }

  async function readHistory(ui, command, target, grant, guard, now, deadline) {
    const captured = new Map(), reactionAttempts = new Set(), directionGaps = new Map(), knownDirections = new Map();
    let container = null, initialTop = null, initialAtBottom = false, scrolled = false, unresolved = false, bottom = false;
    let reason = "history_unavailable", complete = false, previousOldest = null, stalled = 0, bottomAttempts = 0, steps = 0, popups = 0;
    const sameDirectionBody = (a, b) => ["kind", "chat", "id", "date", "shown", "text", "what", "media_type", "truncated", "group"]
      .every(key => a[key] === b[key]) && JSON.stringify(a.reactions || null) === JSON.stringify(b.reactions || null);
    // At most 16 movement/popup grants + one restore + one final capture. Four delivery
    // batches still fit the app's 24-grant cap. Every permission is short-lived.
    for (let pass = 0; steps < 16; pass++) {
      if (now() >= deadline - 3500) { reason = "timeout"; break; }
      steps++;
      const boundary = await grant(); boundary();
      let view = ui.readView(target);
      if (!view) break;
      if (container && view.container !== container) throw fault("blocked", "The conversation history changed while reading; the request stopped.");
      if (!container) {
        container = view.container; initialTop = view.top;
        initialAtBottom = view.top >= Math.max(0, view.height - view.viewport) - 2;
      }
      // Media/layout can increase height after the first scroll. Re-establish
      // the latest-message boundary before spending any grant moving backwards.
      const destination = !bottom ? Math.max(0, view.height - view.viewport) : Math.max(0, view.top - Math.max(200, view.viewport * 0.8));
      if (view.scrollable && Math.abs(destination - view.top) > 1) {
        ui.scrollRead(view, destination, boundary); scrolled = true;
      }
      await ui.wait(300); guard();
      view = ui.readView(target);
      if (!view || view.container !== container) throw fault("blocked", "The conversation history changed while reading; the request stopped.");
      if (!bottom) {
        bottomAttempts++;
        bottom = view.scrollable && view.top >= Math.max(0, view.height - view.viewport) - 2;
      }
      // Inspect only an existing chip belonging to a captured in-window message.
      // Opening and closing share the movement budget; unknown authors stay partial.
      if (typeof ui.inspectReaction === "function") {
        for (const reaction of view.reactionTargets || []) {
          if (reaction.at < command.since || reactionAttempts.has(reaction.id + reaction.key) || popups >= 2 || steps > 14 || now() >= deadline - 5000) continue;
          reactionAttempts.add(reaction.id + reaction.key);
          popups++;
          await ui.inspectReaction(target, reaction, async () => { steps++; return grant(); }, guard);
          guard();
        }
        view = ui.readView(target);
        if (!view || view.container !== container) throw fault("blocked", "The conversation changed while checking reactions.");
      }
      const directionOnly = new Set();
      if (Array.isArray(view.unresolvedAt) && view.unresolvedAt.length === view.unresolved) {
        const ids = Array.isArray(view.unresolvedRows) && view.unresolvedRows.length === view.unresolved ? view.unresolvedRows : [];
        view.unresolvedAt.forEach((at, i) => {
          if (Number.isFinite(at) && at < command.since) return;
          const matches = typeof ids[i] === "string" && ids[i] ? view.events.filter(e => e.id === ids[i]) : [];
          const event = matches.length === 1 ? matches[0] : null;
          if (!event || event.kind !== "bubble" || event.chat !== command.chat || event.group !== false ||
              event.direction !== "unknown" || !Number.isFinite(at) || bubbleTime(event) !== at || at > now() + 5000 ||
              event.truncated === true || event.what === "deleted" || event.reactions && event.reactions.complete !== true ||
              (!event.text && !["voice", "media"].includes(event.what))) {
            unresolved = true; return;
          }
          const prior = directionGaps.get(event.id);
          if (prior && !sameDirectionBody(prior.event, event)) unresolved = true;
          if (!prior) directionGaps.set(event.id, {event: {...event, reactions: event.reactions ? JSON.parse(JSON.stringify(event.reactions)) : event.reactions}, resolved: false, direction: null});
          else prior.resolved = false;
          directionOnly.add(event);
        });
      } else if (view.unresolved > 0) unresolved = true;
      for (const event of view.events) {
        const at = bubbleTime(event);
        if (event.kind !== "bubble" || event.chat !== command.chat || event.group !== false || at === null ||
            at > now() + 5000 || (!event.text && !["voice", "media", "deleted"].includes(event.what)) ||
            !["incoming", "outgoing", "unknown"].includes(event.direction)) {
          if (at === null || at >= command.since) unresolved = true;
          continue;
        }
        const key = event.id ? "id:" + event.id : [event.date, event.shown, event.direction, event.text].join("\u0001");
        if (captured.size >= 1200 && !captured.has(key)) { reason = "limit"; break; }
        const prior = captured.get(key);
        const gap = event.id && directionGaps.get(event.id);
        if (gap && event.direction !== "unknown") {
          if (!sameDirectionBody(gap.event, event) || gap.direction && gap.direction !== event.direction ||
              typeof event.ours === "boolean" && event.ours !== (event.direction === "outgoing")) unresolved = true;
          else { gap.resolved = true; gap.direction = event.direction; }
        }
        if (event.id && event.direction !== "unknown" && at >= command.since) {
          if (knownDirections.has(event.id) && knownDirections.get(event.id) !== event.direction) unresolved = true;
          knownDirections.set(event.id, event.direction); // an unknown intermediate view cannot erase explicit conflict
        }
        // A virtualized overlap can move row indices. Keep the earliest actual
        // same-view order only while this stable message's meaning is unchanged.
        const sameProof = prior && event.id && /^[a-f0-9]{32}$/.test(prior.view_id || "") &&
          Number.isInteger(prior.i) && prior.i >= 0 && prior.i <= 10000 &&
          ["id", "date", "shown", "direction", "ours", "text", "what", "media_type", "truncated", "group"].every(k => prior[k] === event[k]) &&
          JSON.stringify(prior.reactions || null) === JSON.stringify(event.reactions || null);
        captured.set(key, sameProof ? {...event, view_id: prior.view_id, i: prior.i} : event);
        if ((event.direction === "unknown" && !directionOnly.has(event) || event.truncated === true ||
            event.what === "deleted" || event.reactions && event.reactions.complete !== true) && at >= command.since) unresolved = true;
      }
      const dates = [...captured.values()].map(bubbleTime), oldest = dates.length ? Math.min(...dates) : null;
      if (!bottom) {
        if (!view.scrollable || bottomAttempts >= 3) {
          reason = captured.size ? "history_unavailable" : "no_messages"; break;
        }
        continue;  // bounded settling shares the existing 16-movement budget
      }
      if (bottom && oldest !== null && oldest <= command.since) {
        complete = !unresolved && [...directionGaps.values()].every(gap => gap.resolved);
        reason = complete ? "since_reached" : "history_unavailable"; break;
      }
      if (captured.size >= 1200) { reason = "limit"; break; }
      if (!view.scrollable) { reason = captured.size ? "history_unavailable" : "no_messages"; break; }
      stalled = oldest === previousOldest && view.top <= 1 ? stalled + 1 : 0;
      previousOldest = oldest;
      if (stalled >= 2) { reason = captured.size ? "history_unavailable" : "no_messages"; break; }
      reason = "limit";
    }
    if (scrolled && now() < deadline - 2500) {
      const boundary = await grant(); boundary();
      const view = ui.readView(target);
      if (!view || view.container !== container) throw fault("blocked", "The conversation history changed before restoring its position.");
      const end = Math.max(0, view.height - view.viewport);
      ui.scrollRead(view, initialAtBottom ? end : Math.min(initialTop, end), boundary);
    }
    const events = [...captured.values()], dates = events.map(bubbleTime);
    const reading = {messages: events.length, incoming: events.filter(e => e.direction === "incoming").length,
      outgoing: events.filter(e => e.direction === "outgoing").length, complete,
      oldest: dates.length ? Math.min(...dates) : null, newest: dates.length ? Math.max(...dates) : null,
      reason};
    const boundary = await grant(); boundary();
    const receipt = await ui.deliverRead(command, events, reading);
    guard();
    if (!receipt || receipt.ok !== true || receipt.id !== command.id || receipt.owner !== command.owner || receipt.kept !== events.length) {
      throw fault("unknown", "Captured messages were not confirmed by Check Call Log. Reading is incomplete; no message was sent.");
    }
    return reading;
  }
  const USER_ACTIVE = "You are using WhatsApp, so nothing was done. It will try again once you have stopped for a few seconds.";
  const IDLE_MS = 15000;                   // how long after his last key or click WhatsApp counts as "in use"
  function executor(ui, now = Date.now, authorize = async () => null) {
    const used = new Set();
    const onScreen = () => typeof ui.shown !== "function" || ui.shown() === true;
    let busy = false;
    return async function execute(command, owner) {
      let result = null;
      const reply = (status, detail) => (result = {id: command && command.id, owner, status, detail});
      const needsPhone = command && (command.kind === "read" || command.kind === "send");
      if (!command || !/^[a-f0-9]{32}$/.test(command.id || "") || command.owner !== owner ||
          !["open", "draft", "read", "send"].includes(command.kind) || typeof command.chat !== "string" ||
          !command.chat.trim() || command.chat.length > 120 || !validUnicode(command.chat) || /[\x00-\x1f]/.test(command.chat) ||
          !Number.isFinite(command.expires) || !Number.isFinite(command.created) ||
          command.created > now() + 5000 || command.expires <= command.created || command.expires - command.created > 120000 ||
          (command.phone != null && !/^[1-9][0-9]{8,14}$/.test(command.phone)) ||
          ((command.kind === "draft" || command.kind === "send") && (typeof command.text !== "string" || !command.text.trim() || command.text.length > 4096 || !validUnicode(command.text) || command.text.includes("\0"))) ||
          (command.kind !== "draft" && command.kind !== "send" && command.text != null) ||
          (command.kind === "send" && !/^[1-9][0-9]{8,14}$/.test(command.phone || "")) ||
          (command.kind === "read" && (!/^[1-9][0-9]{8,14}$/.test(command.phone || "") || !Number.isInteger(command.since) ||
            command.since < now() - 48 * 3600000 || command.since > now() + 5000))) return reply("blocked", "Invalid open/draft/read request.");
      if (used.has(command.id)) return reply("unknown", "This request was already attempted; inspect WhatsApp. It will not run again.");
      used.add(command.id);                    // consumed BEFORE any DOM operation, including unsuccessful attempts
      if (busy) return reply("blocked", "Another draft is being prepared.");
      if (now() >= command.expires) return reply("expired", "The request expired before starting.");
      if (!onScreen()) return reply("blocked", HIDDEN);
      // 8 Nov review: a read started while Lena was typing in WhatsApp switched chats under him, so his keys (Enter
      // too) could land in a guard's message box. Nothing starts while he is active there; reading waits and retries.
      if (typeof ui.userBusy === "function" && ui.userBusy()) return reply("user_active", USER_ACTIVE);
      busy = true;
      const deadline = Math.min(command.expires, now() + (command.kind === "read" ? 20000 : 12000));
      let searched = false, mutated = false, readSuppressed = false, stop = () => {}, unreadBefore = 0, readOpenAttempted = false, sendPressed = false;
      function guard() {
        if (now() >= deadline) throw fault(mutated ? "unknown" : "expired", "The operation timed out; inspect WhatsApp before retrying.");
        if (!onScreen()) throw fault("blocked", HIDDEN.replace(" Nothing was changed.", " The request stopped."));
        if (!ui.safe()) {
          if (command.kind === "read" && !mutated && typeof ui.interrupted === "function" && ui.interrupted()) {
            throw fault("user_active", "You used WhatsApp while a chat was being read; that read stopped and will be tried again once you stop.");
          }
          const reason = typeof ui.safeReason === "function" ? ui.safeReason() : "";
          throw fault("blocked", typeof reason === "string" && reason && reason.length <= 400 ? reason :
            "WhatsApp changed or you interacted with it; the request stopped.");
        }
      }
      async function permission(step) {
        guard();
        const grant = await authorize({id: command.id, owner, step});
        guard();
        if (!grant || grant.ok !== true || grant.id !== command.id || grant.owner !== owner || grant.step !== step ||
            !Number.isFinite(grant.expires) || grant.expires <= now() || grant.expires > now() + 2500) {
          throw fault("blocked", "The app did not authorize this step; it may have been cancelled or closed.");
        }
        return grant.expires;
      }
      async function waitFor(read) {
        for (let i = 0; i < 35; i++) {
          guard();
          const result = read();
          if (result) return result;
          await ui.wait(100);
        }
        return null;
      }
      try {
        stop = ui.watch();
        guard();
        if (command.kind === "read") {
          if (typeof ui.beginRead !== "function" || !ui.beginRead(command)) throw fault("blocked", "The complete-history reader is not ready; reload the WhatsApp tab.");
          readSuppressed = true;
        }
        if (!ui.all() || ui.searchText()) throw fault("blocked", "Return to the All chat list and clear your search first.");
        let rows = ui.rows(command.chat);
        if (!rows.length) {
          const searchUntil = await permission("search");
          guard();
          if (ui.searchText()) throw fault("blocked", "The chat search changed while waiting for authorization; it was preserved.");
          ui.search(command.chat, () => {
            guard();
            if (now() >= searchUntil || ui.searchText()) throw fault("blocked", "Search authorization expired or the search changed before typing.");
            searched = true;                    // owns only a write we are about to attempt, never a focus-restored user query
          });
          rows = await waitFor(() => { const found = ui.rows(command.chat); return found.length ? found : null; }) || [];
        }
        guard();
        if (rows.length > 1) throw fault("ambiguous_chat", "More than one exact chat matched; choose the chat yourself.");
        if (!rows.length) throw fault("not_found", "The exact existing chat was not found.");
        if (rows[0].group) throw fault("group_chat", "Group and broadcast chats are not supported.");
        const openUntil = await permission("open");
        rows = ui.rows(command.chat);
        if (rows.length !== 1) throw fault(rows.length ? "ambiguous_chat" : "not_found", "The exact chat search result changed before opening.");
        if (rows[0].group) throw fault("group_chat", "Group and broadcast chats are not supported.");
        guard();
        if (now() >= openUntil) throw fault("blocked", "Chat authorization expired before opening.");
        const opening = rows[0];
        // Opening a chat marks its messages read in WhatsApp. An automatic read reports how many were unread, so the
        // controller is told and nothing (a holiday request, a question) quietly loses its badge (8 Nov review).
        if (command.kind === "read") unreadBefore = Number.isInteger(opening.unread) && opening.unread > 0 ? Math.min(opening.unread, 999) : 0;
        ui.open(opening, () => {
          guard();
          if (now() >= openUntil) throw fault("blocked", "Chat authorization expired before opening.");
          const fresh = ui.rows(command.chat);
          if (fresh.length !== 1 || fresh[0].node !== opening.node || fresh[0].group) {
            throw fault("blocked", "The exact chat row changed during opening.");
          }
          if (command.kind === "read") readOpenAttempted = true;
        });
        let current = await waitFor(() => { const c = ui.current(); return c && c.chat === command.chat ? c : null; });
        if (!current) throw fault("blocked", "The opened chat header could not be verified.");
        guard();
        if (needsPhone && current.numericConflict) throw fault("blocked", "Conflicting chat numbers prevent recipient verification.");
        if (!current.direct || needsPhone && !(current.phone || current.profilePhone)) {
          if (!command.phone) throw fault("blocked", "A verified phone is required when the chat has no direct message identity.");
          if (current.group || current.profileBlocked) throw fault("blocked", "This chat's contact details could not be verified safely.");
          if (!current.profileCurrent) {
            const profileUntil = await permission("profile");
            ui.openProfile(command.chat, () => {
              guard();
              if (now() >= profileUntil) throw fault("blocked", "Contact-info authorization expired before opening.");
            });
          }
          current = await waitFor(() => { const c = ui.current(); return c && c.chat === command.chat &&
            (c.profileBlocked || c.direct && (!needsPhone || !!(c.phone || c.profilePhone))) ? c : null; });
          if (!current) throw fault("blocked", "The current chat's Contact info could not be verified.");
        }
        function verified() {
          const value = ui.current();
          if (!value || value.chat !== command.chat) throw fault("blocked", "The conversation changed; the request stopped.");
          if (value.group) throw fault("group_chat", "Group and broadcast chats are not supported.");
          if (!value.direct) throw fault("blocked", "This chat could not be verified as a direct conversation.");
          if (value.identity === "profile" && !command.phone) throw fault("blocked", "A saved verified phone is required for Contact info verification.");
          if (needsPhone && (value.numericConflict || value.phone && value.profilePhone && value.phone !== value.profilePhone ||
              !(value.phone || value.profilePhone) ||
              (value.phone || value.profilePhone) !== command.phone)) {
            throw fault("blocked", "The current chat number could not be matched to the saved contact; the request stopped.");
          }
          if (command.phone && value.phone && command.phone !== value.phone) throw fault("blocked", "The observed chat number does not match the reviewed contact.");
          if ((command.kind === "draft" || command.kind === "send") && value.plain !== true) throw fault("blocked", "Finish or cancel the reply, edit, attachment or recording yourself before preparing a new draft.");
          return value;
        }
        let target = verified();
        if (command.kind === "open") return reply("opened", "The exact direct chat is open. Nothing was typed or sent.");
        if (command.kind === "read") {
          const readGuard = () => {
            guard();
            const current = verified();
            if (current.composer !== target.composer || same(current.text) !== same(target.text)) {
              throw fault("blocked", "The message box changed while reading; its contents were preserved.");
            }
          };
          const reading = await readHistory(ui, command, target, async () => {
            const until = await permission("read");
            return () => {
              readGuard();
              if (now() >= until) throw fault("blocked", "Reading authorization expired; the request stopped.");
            };
          }, readGuard, now, deadline);
          return Object.assign(reply("read", reading.complete ? "The requested messages were captured by Check Call Log. Nothing was typed or sent." :
            "The available messages were captured, but some history still needs review. Nothing was typed or sent."),
            {reading, ...(unreadBefore ? {unread_before: unreadBefore} : {})});
        }
        if (command.kind === "send") {
          // ONE reviewed message, sent because he pressed Send for this guard. Only into an empty box or a box holding
          // exactly this text; it is "sent" only when WhatsApp shows one more outgoing message with this text and the box
          // is empty again. Anything less is "unknown" and is never retried.
          if (target.text && same(target.text) !== same(command.text)) {
            return reply("existing_draft", "This chat already has a different unsent message in its box. It was left alone and nothing was sent.");
          }
          if (!target.text) {
            const typeUntil = await permission("draft");
            guard();
            target = verified();
            if (target.text) return reply("existing_draft", "A message appeared in the box. It was left alone and nothing was sent.");
            mutated = true;
            ui.insert(target, command.text, () => { guard(); if (now() >= typeUntil) throw fault("blocked", "Permission to type expired; nothing was sent."); });
            await ui.wait(100);
            guard();
            target = verified();
            if (same(target.text) !== same(command.text)) {
              return reply("unknown", "The message could not be placed in the box exactly, so it was NOT sent. Check the chat's message box.");
            }
          }
          let before = typeof ui.outgoingSnapshot === "function" ? ui.outgoingSnapshot(command.chat) : null;
          if (before === null) {
            return reply("blocked", "The chat's messages could not be read to confirm a send, so it was NOT sent. The text is in the message box.");
          }
          const sendUntil = await permission("send");
          guard();
          target = verified();
          if (same(target.text) !== same(command.text)) return reply("existing_draft", "The message box changed, so it was NOT sent. It was left alone.");
          before = ui.outgoingSnapshot(command.chat);
          if (!before || !Array.isArray(before.ids) || !Array.isArray(before.messages)) return reply("blocked", "The chat's messages changed before sending; nothing was sent.");
          const previousIds = new Set(before.ids), tail = before.ids.at(-1), sentAfter = Math.floor(now() / 60000) * 60000;
          mutated = sendPressed = true;
          ui.pressSend(target, command.text, () => { guard(); if (now() >= sendUntil) throw fault("blocked", "Permission to send expired before pressing Send."); });
          const shown = await waitFor(() => {
            const after = ui.outgoingSnapshot(command.chat), box = verified();
            if (!after || !Array.isArray(after.ids) || !Array.isArray(after.messages) || box.text) return null;
            return after.messages.some(message => !previousIds.has(message.id) && same(message.text) === same(command.text) &&
              Number.isFinite(message.at) && message.at >= sentAfter && message.at <= now() + 5000 &&
              after.ids.includes(message.id) && (!tail || after.ids.includes(tail) && after.ids.indexOf(message.id) > after.ids.indexOf(tail))) ? true : null;
          });
          return shown ? reply("sent", "Sent: WhatsApp shows the message in the chat.")
                       : reply("unknown", "Send was pressed, but WhatsApp did not show the message in time. Check the chat; it will not be sent again automatically.");
        }
        if (same(target.text) === same(command.text)) return reply("already_draft", "The identical unsent draft is already present. Nothing was sent.");
        if (target.text) return reply("existing_draft", "This chat already has a different unsent draft; it was preserved.");
        const authorizedUntil = await permission("draft");
        guard();
        target = verified();                    // immediate header, recipient and composer recheck before the only draft write
        if (target.text) return reply("existing_draft", "An unsent draft appeared; it was preserved.");
        mutated = true;
        ui.insert(target, command.text, () => {
          guard();
          if (now() >= authorizedUntil) throw fault("blocked", "Draft authorization expired before insertion.");
        });
        await ui.wait(100);
        guard();
        target = verified();
        if (same(target.text) !== same(command.text)) return reply("unknown", "The resulting draft could not be verified. Inspect the composer; do not retry automatically.");
        return reply("drafted", "The reviewed text is in the verified chat composer, unsent.");
      } catch (error) {
        return reply(mutated ? "unknown" : (outcomes.has(error.status) ? error.status : "failed"),
                     sendPressed ? "Send was pressed but the result could not be confirmed. Check the chat; it will not be sent again automatically." :
                     mutated ? "Draft insertion was attempted but the result could not be verified. Inspect WhatsApp before retrying." :
                       (error.status ? error.message : "WhatsApp's controls were not supported; inspect the composer before retrying."));
      } finally {
        // The original unread badge matters even if reading is interrupted after
        // its guarded open. Opening may already have cleared WhatsApp's badge.
        if (result && command.kind === "read" && readOpenAttempted && unreadBefore > 0) result.unread_before = unreadBefore;
        let cleanupWarning = "";
        try {
          if (searched) {
            if (ui.canCleanSearch() && ui.searchText() === command.chat) {
              ui.search("", () => {
                // Focus can restore another query or trigger interaction. Never clear that text.
                if (!ui.canCleanSearch() || ui.searchText() !== command.chat) throw fault("blocked", "The search changed before cleanup; it was preserved.");
              });
              // Editors may commit the clear after the input event. Wait only for
              // readback, never repeat the write, and never wait on a hidden tab.
              await Promise.resolve();
              const cleanupUntil = Math.min(command.expires, now() + 500);
              for (let i = 0; i < 10 && now() < cleanupUntil && onScreen() && ui.canCleanSearch() &&
                    ui.searchText() === command.chat; i++) {
                await ui.wait(Math.min(50, cleanupUntil - now()));
              }
            }
            if (ui.searchText()) cleanupWarning = ui.canCleanSearch() && ui.searchText() === command.chat
              ? "Clear the name left in WhatsApp's search box and select All chats."
              : "Your current WhatsApp search was left alone. Clear it and select All chats when ready.";
          }
        } catch (e) {
          cleanupWarning = "Check WhatsApp's search box; clear it and select All chats when ready.";
        }
        if (cleanupWarning && result) {
          // Search cleanup is separate from the already verified chat/draft result.
          // Keep its status and original reason; the warning explains the next step.
          result.detail = (result.detail ? result.detail + " " : "") + cleanupWarning;
        }
        if (readSuppressed) { try { ui.endRead(command); } catch (e) { /* keep normal publication suppressed if cleanup failed */ } }
        // An automatic read leaves no cursor in a guard's message box or the search box, so a later key cannot land there.
        if (command && command.kind === "read" && typeof ui.releaseFocus === "function") { try { ui.releaseFocus(); } catch (e) { /* nothing typed */ } }
        // Publish the actual All/search state after cleanup; the next read must
        // not rely on a stale heartbeat from this action's temporary search.
        if (typeof ui.refreshReader === "function") {
          let refreshed = false;
          try { refreshed = (await ui.refreshReader())?.ok === true; } catch (e) { /* next normal heartbeat can retry */ }
          if (!refreshed && result && command.kind === "read") {
            result.detail += " The updated connection state was not acknowledged; wait for the next reader check.";
          }
        }
        stop();
        busy = false;
      }
    };
  }

  function browserAdapter(doc, win) {
    let interrupted = false, lastUserInput = 0, running = false;
    const noteInput = e => { if (e.isTrusted) lastUserInput = Date.now(); };
    if (doc && typeof doc.addEventListener === "function") {
      for (const kind of ["keydown", "pointerdown", "paste", "drop", "compositionstart", "wheel", "touchstart"]) doc.addEventListener(kind, noteInput, true);
    }
    let activeReadCommand = null, ownedReaction = null;
    const reactionProofs = new Map();
    let contextReason = "", lastSafeReason = "";
    const blockedContext = reason => { contextReason = reason; return false; };
    // WhatsApp replaces the labelled input after typing with this empty-label
    // sidebar textbox. Never broaden this to an arbitrary editor or composer.
    const searchSelector = '[aria-label="Search or start a new chat"], [aria-label="Search input textbox"], ' +
      '#side input[role="textbox"][type="text"][data-tab="3"][aria-label=""]';
    const visible = (node) => !!node && !node.closest('[aria-hidden="true"], [hidden]') && node.getClientRects().length > 0;
    const editable = (node) => node && (node.isContentEditable || node.tagName === "INPUT" || node.tagName === "TEXTAREA");
    const searchBox = () => {
      const found = [...doc.querySelectorAll(searchSelector)].filter(n => visible(n) && editable(n));
      if (found.length !== 1) throw fault("blocked", "The chat search control could not be identified safely.");
      return found[0];
    };
    const textOf = (node, includeBoundary = true) => {
      if (!node) return "";
      if (node.nodeType === 3) return node.nodeValue || "";
      if (node.nodeType !== 1 || node.getAttribute("aria-hidden") === "true") return "";
      if (node.tagName === "IMG") return node.getAttribute("data-plain-text") || node.getAttribute("alt") || "";
      if (node.tagName === "BR") return "\n";
      const children = [...node.childNodes], block = node.tagName === "DIV" || node.tagName === "P";
      // A single BR holds an otherwise empty editing paragraph open. Paragraph
      // boundaries still preserve blank lines; literal whitespace is never trimmed.
      const placeholder = block && children.length === 1 && children[0].nodeType === 1 && children[0].tagName === "BR";
      const text = placeholder ? "" : children.map(child => textOf(child)).join("");
      return includeBoundary && block && node.nextSibling ? text + "\n" : text;
    };
    // Siblings of the outer editor are controls/placeholders, not its text.
    const read = (node) => node.value !== undefined ? node.value : textOf(node, false);
    const icons = (node) => [...node.querySelectorAll('[data-icon], svg title')]
      .map(n => n.getAttribute("data-icon") || n.textContent || "");
    const group = (node) => icons(node).some(x => /group|community|broadcast/i.test(x));
    function contactDrawer() {
      const drawers = [...doc.querySelectorAll('[data-testid="chat-info-drawer"]')].filter(visible);
      if (!drawers.length) return {kind: "none"};
      if (drawers.length !== 1) return {kind: "blocked"};
      const node = drawers[0], header = node.querySelector('[data-testid="contact-info-header"]');
      const heading = header && header.querySelector("h2");
      if (!heading || heading.textContent.trim() !== "Contact info") return {kind: "blocked"};
      const names = [...node.querySelectorAll("span[dir='auto']")].filter(n => visible(n) && !header.contains(n));
      const name = names[0] && names[0].textContent.trim();
      if (!name) return {kind: "blocked"};
      return {kind: "contact", node, name};
    }
    function profileIdentity(chat) {
      const drawer = contactDrawer();
      if (drawer.kind !== "contact") return drawer;
      if (drawer.name !== chat) return {kind: "other"};
      const current = {kind: "current"}, proofs = [];
      const number = text => {
        text = text.trim();
        if (!/^\+[1-9][0-9 ()\u00a0.\-]+$/.test(text) || /\(\s*0\s*\)/.test(text)) return null;
        const phone = text.replace(/[^0-9]/g, "");
        return /^[1-9][0-9]{8,14}$/.test(phone) && !phone.startsWith("3530") ? phone : null;
      };
      const headings = [...drawer.node.querySelectorAll('[data-testid="section-about-and-phone-number"]')].filter(visible);
      // The About field is contact-controlled text, never recipient identity. Only the
      // observed dedicated phone field, a direct sibling of this heading, can prove it.
      if (headings.length) {
        if (headings.length !== 1 || !headings[0].parentElement || !drawer.node.contains(headings[0].parentElement)) return current;
        const fields = [...headings[0].parentElement.childNodes].filter(n => n.nodeType === 1 &&
          n.getAttribute("data-testid") === "container_with_separator" && visible(n));
        if (fields.length !== 1) return current;
        const phones = [...fields[0].querySelectorAll("*")].filter(n => visible(n) &&
          ![...n.childNodes].some(child => child.nodeType === 1)).map(n => number(n.textContent)).filter(Boolean);
        if (phones.length !== 1) return current;
        proofs.push(phones[0]);
      }
      // New observed Contact info layout puts the phone beside the marked name,
      // above a separate About section. Accept only that exact structural field.
      const names = [...drawer.node.querySelectorAll('span[data-testid="contact-info-subtitle selectable-text"][dir="auto"]')].filter(visible);
      if (names.length) {
        const elements = node => node && [...node.childNodes].every(n => n.nodeType === 1 || n.nodeType === 8 || n.nodeType === 3 && !n.nodeValue.trim()) ?
          [...node.childNodes].filter(n => n.nodeType === 1) : [];
        const passive = node => visible(node) && !node.matches('[contenteditable], input, textarea, select, [role="textbox"]');
        if (names.length !== 1 || names[0].textContent.trim() !== chat || !passive(names[0]) ||
            [...names[0].childNodes].some(n => n.nodeType === 1)) return current;
        let wrapper = names[0];
        for (const tag of ["DIV", "DIV", "SPAN"]) {
          const parent = wrapper.parentElement, children = elements(parent);
          if (!parent || parent.tagName !== tag || !passive(parent) || !drawer.node.contains(parent) ||
              children.length !== 1 || children[0] !== wrapper) return current;
          wrapper = parent;
        }
        const identity = wrapper.parentElement, children = elements(identity);
        if (!identity || identity.tagName !== "DIV" || !passive(identity) || !drawer.node.contains(identity) ||
            children.length !== 2 || children[0] !== wrapper || children[1].tagName !== "DIV" || !passive(children[1])) return current;
        const fields = elements(children[1]);
        if (fields.length !== 1 || fields[0].tagName !== "SPAN" || fields[0].getAttribute("data-testid") !== "selectable-text" ||
            fields[0].getAttribute("dir") !== "auto" || !passive(fields[0])) return current;
        const leaves = elements(fields[0]);
        if (leaves.length !== 1 || leaves[0].tagName !== "SPAN" || !passive(leaves[0]) ||
            [...leaves[0].childNodes].some(n => n.nodeType === 1)) return current;
        const phone = number(leaves[0].textContent);
        if (!phone) return current;
        proofs.push(phone);
      }
      return {kind: "current", phone: proofs.length && new Set(proofs).size === 1 ? proofs[0] : null};
    }
    const reactionToolbar = '[role="toolbar"][aria-label="All message reactions, including the possibility to add a new one"]';
    const reactionMarker = '[data-testid="reactions-details-cell-not-me"], [data-testid="reactions-details-cell-me"]';
    function reactionPopup() {
      if (!ownedReaction || !activeReadCommand) return null;
      const bars = [...doc.querySelectorAll(reactionToolbar)].filter(visible);
      if (bars.length !== 1) return null;
      const toolbar = bars[0], node = toolbar.parentElement;
      if (!node || node.getAttribute("tabindex") !== "-1" || !visible(node) ||
          node.getAttribute("aria-modal") === "true" || node.querySelector('[aria-modal="true"], [contenteditable], input, textarea, select, [role="textbox"]')) return null;
      const headers = [...node.querySelectorAll('[data-testid="reaction-count-header"]')].filter(visible);
      const lists = [...node.querySelectorAll('[role="list"]')].filter(visible);
      const match = headers.length === 1 && /^([12]) reactions?$/.exec(headers[0].textContent.trim());
      if (!match || lists.length !== 1) return null;
      const markers = [...lists[0].querySelectorAll(reactionMarker)].filter(visible);
      if (markers.length !== +match[1]) return null;
      if (ownedReaction.popup && ownedReaction.popup !== node) return null;
      return {node, toolbar, list: lists[0], markers, count: +match[1]};
    }
    function reactionSnapshot(popup, parentId, chat) {
      const items = [], actors = new Set();
      for (const marker of popup.markers) {
        const self = marker.getAttribute("data-testid") === "reactions-details-cell-me";
        const row = marker.closest(self ? '[role="button"]' : '[role="listitem"]');
        if (!row || !popup.list.contains(row)) return null;
        const titles = [...marker.querySelectorAll('[data-testid="cell-frame-title"]')].filter(visible);
        const pictures = [...marker.querySelectorAll('img[alt]')].filter(n => n.getAttribute("alt"));
        if (titles.length !== 1 || pictures.length !== 1) return null;
        const emoji = pictures[0].getAttribute("alt"), title = titles[0].textContent.trim();
        if (!emoji || emoji.length > 16 || emoji !== emoji.trim() || !validUnicode(emoji) || /[\x00-\x1f\x7f]/.test(emoji)) return null;
        const label = row.getAttribute("aria-label") || "";
        if (self ? title !== "You" || !label.startsWith("You, ") ||
            !label.endsWith(" reacted with " + emoji + " Click to remove") || label.length <= ("You,  reacted with " + emoji + " Click to remove").length :
            title !== chat || label !== chat + " reacted with " + emoji ||
            !titles[0].querySelector('button[aria-label="Click to open profile details"]')) return null;
        const actor = self ? "self" : "contact";
        if (actors.has(actor)) return null;
        actors.add(actor); items.push({parent_id: parentId, emoji, actor, actor_name: self ? "You" : chat, at: null});
      }
      return items.length === popup.count ? {complete: true, items} : null;
    }
    function plainContext(main) {
      // Defensive, synthetic-tested mode checks, not a claim that every WhatsApp DOM version is supported.
      // Never close a reply/edit/preview ourselves: an empty textbox can still carry that pending context.
      contextReason = "";
      const drawer = contactDrawer();
      const gallery = node => node && node.getAttribute("data-testid") === "media-gallery" &&
        node.getAttribute("aria-modal") !== "true" && visible(node) &&
        !node.matches('[contenteditable], input, textarea, select, [role="textbox"]') &&
        !node.querySelector('[aria-modal="true"], [contenteditable], input, textarea, select, [role="textbox"]');
      const galleryDialog = dialog => {
        if (drawer.kind !== "contact" || !drawer.node.contains(dialog) || dialog.getAttribute("aria-modal") === "true") return false;
        if (gallery(dialog)) return true;
        // Observed Contact info thumbnails: one nonmodal DIV dialog wrapping only
        // its focusable media gallery. No unrelated content or editor is exempted.
        const children = [...dialog.childNodes], elements = children.filter(n => n.nodeType === 1);
        return dialog.tagName === "DIV" && dialog.getAttribute("role") === "dialog" && dialog.getAttribute("tabindex") === "-1" &&
          !dialog.matches('[contenteditable], [role="textbox"]') && elements.length === 1 &&
          elements[0].tagName === "DIV" && elements[0].getAttribute("tabindex") === "0" && gallery(elements[0]) &&
          children.every(n => n.nodeType === 1 || n.nodeType === 8 || n.nodeType === 3 && !n.nodeValue.trim());
      };
      for (const dialog of [...doc.querySelectorAll('[role="dialog"], [aria-modal="true"]')].filter(visible)) {
        const popup = reactionPopup();
        const owned = popup && dialog.getAttribute("aria-modal") !== "true" && popup.list.contains(dialog) &&
          popup.markers.some(marker => dialog.contains(marker));
        if (!galleryDialog(dialog) && !owned) {
          return blockedContext("WhatsApp has a dialog open that the draft tool cannot safely use. Close it yourself, then try again.");
        }
      }
      if (!main) return true;
      const footer = main.querySelector("footer");
      if (!footer || !visible(footer)) return blockedContext("The chat's message controls are missing or still loading; the request stopped.");
      const composers = [...footer.querySelectorAll('[contenteditable="true"][role="textbox"]')].filter(visible);
      if (composers.length !== 1) return blockedContext("The chat's message box is missing or ambiguous; the request stopped.");
      const composer = composers[0], header = main.querySelector("header");
      const history = n => !footer.contains(n) && !!n.closest('[role="row"], [data-testid^="conv-msg-"], [data-pre-plain-text]');
      const outside = n => n !== composer && !composer.contains(n) && !(header && header.contains(n)) && !history(n) &&
        !(drawer.kind === "contact" && drawer.node.contains(n)) && !(reactionPopup() && reactionPopup().node.contains(n));
      // A regular Attach button is allowed. Pending reply/edit/caption/preview/recording controls are not.
      const pendingLabel = /^(?:quoted message|replying(?: to)?|reply to|editing(?: message)?|edit message|cancel(?: reply| edit| attachment| preview| recording)?|close (?:reply|edit|attachment|preview)|(?:photo|video|document|attachment|media) preview|add a caption|caption|recording(?: voice message)?)\b/i;
      const pendingMarker = /(?:^|[-_])(?:quoted|reply|replying|editing|edit-message|message-edit|media-editor|media-preview|attachment-preview|document-preview|voice-recording|audio-recording|recording|caption)(?:$|[-_])/i;
      for (const node of main.querySelectorAll('[aria-label], [title], [data-testid], [data-icon]')) {
        if (!visible(node) || !outside(node)) continue;
        if (["aria-label", "title"].some(key => pendingLabel.test(node.getAttribute(key) || "")) ||
            ["data-testid", "data-icon"].some(key => pendingMarker.test(node.getAttribute(key) || ""))) {
          return blockedContext("A reply, edit, attachment or recording control is active. Finish or cancel it yourself before preparing a draft.");
        }
      }
      if ([...main.querySelectorAll('input[type="file"]')].some(n => n.files && n.files.length)) {
        return blockedContext("A file is selected in WhatsApp. Finish or cancel the attachment yourself before preparing a draft.");
      }
      if ([...main.querySelectorAll('img, video, audio, canvas')].some(n => visible(n) && outside(n))) {
        return blockedContext("Visible media outside the chat history or message box could be an attachment preview. The request stopped.");
      }
      if ([...footer.querySelectorAll('[contenteditable="true"], input, textarea')].some(n => visible(n) && outside(n))) {
        return blockedContext("An additional editor is open beside the message box. Finish or cancel it yourself before preparing a draft.");
      }
      function extraText(node) {
        if (node === composer) return "";
        if (node.nodeType === 3) return node.nodeValue || "";
        if (node.nodeType !== 1 || !visible(node) || node.closest('button, [role="button"], svg')) return "";
        return [...node.childNodes].map(extraText).join(" ");
      }
      // A quote/edit heading with an unknown test id still cannot silently become a plain new draft.
      const extra = extraText(footer).replace(/\s+/g, " ").trim();
      return !extra || extra === "Type a message" ||
        blockedContext("Unrecognized text or controls beside the message box prevent safe draft preparation.");
    }
    function edit(node, value, replace, beforeInput) {
      if (!visible(node) || !editable(node)) throw fault("blocked", "The editable control changed.");
      node.focus();
      if (node.isContentEditable) {
        const selection = win.getSelection(), range = doc.createRange();
        range.selectNodeContents(node);
        if (!replace) range.collapse(false);
        selection.removeAllRanges(); selection.addRange(range);
        if (beforeInput) beforeInput();
        const accepted = replace && value === "" ? doc.execCommand("delete", false, null) : doc.execCommand("insertText", false, value);
        if (!accepted) throw fault("failed", "This editor did not accept the text change.");
      } else {
        const prototype = node.tagName === "INPUT" ? win.HTMLInputElement.prototype : win.HTMLTextAreaElement.prototype;
        if (beforeInput) beforeInput();
        Object.getOwnPropertyDescriptor(prototype, "value").set.call(node, value);
        node.dispatchEvent(new win.Event("input", {bubbles: true}));
      }
    }
    return {
      watch() {
        interrupted = false;
        running = true;
        // His input stops the request. His KEYS are also held back while it runs: the request may have just opened a
        // guard's chat, and a key or Enter meant for the chat he had open must never reach that guard's message box.
        const cancel = e => {
          if (!e.isTrusted) return;
          interrupted = true;
          if (["keydown", "keypress", "paste", "drop"].includes(e.type)) { e.preventDefault(); e.stopImmediatePropagation(); }
        };
        const kinds = ["pointerdown", "keydown", "keypress", "paste", "drop", "wheel", "touchstart"];
        for (const kind of kinds) doc.addEventListener(kind, cancel, true);
        return () => { running = false; for (const kind of kinds) doc.removeEventListener(kind, cancel, true); };
      },
      // In use = WhatsApp's window has the keyboard and he pressed a key or clicked there in the last 15 s.
      interrupted: () => interrupted,
      outgoingSnapshot(chat) {
        const reader = win.__northwindReadCapture;
        return reader && typeof reader.outgoingSnapshot === "function" ? reader.outgoingSnapshot(chat) : null;
      },
      pressSend(target, text, authorized) {
        const candidates = () => {
          const main = doc.querySelector("#main"), footer = main && main.querySelector("footer");
          return footer ? [...new Set([...footer.querySelectorAll('[aria-label="Send"], [data-icon="send"], [data-icon="wds-ic-send-filled"]')]
            .map(n => n.closest('button, [role="button"]')).filter(n => n && visible(n) && footer.contains(n) &&
              !n.disabled && n.getAttribute("aria-disabled") !== "true"))] : [];
        };
        const buttons = candidates();
        if (buttons.length !== 1) throw fault("blocked", "WhatsApp's Send button could not be identified safely; nothing was sent.");
        for (const type of ["mousedown", "mouseup", "click"]) {
          if (authorized) authorized();
          const box = this.current(), fresh = candidates();
          if (!box || box.chat !== target.chat || box.composer !== target.composer || same(box.text) !== same(text) || !box.plain ||
              box.group || !box.direct || box.numericConflict || box.phone !== target.phone || box.profilePhone !== target.profilePhone ||
              fresh.length !== 1 || fresh[0] !== buttons[0]) throw fault("blocked", "The chat, message or Send control changed; the request stopped.");
          buttons[0].dispatchEvent(new win.MouseEvent(type, {bubbles: true, cancelable: true, composed: true,
            view: win, button: 0, buttons: type === "mousedown" ? 1 : 0, detail: 1}));
        }
      },
      userBusy: () => !running && typeof doc.hasFocus === "function" && doc.hasFocus() && Date.now() - lastUserInput < IDLE_MS,
      releaseFocus() {
        const a = doc.activeElement;
        if (a && a !== doc.body && typeof a.blur === "function" && typeof a.closest === "function" &&
            (a.closest("#main footer") || a.closest(searchSelector) || (a.matches && a.matches(searchSelector)))) a.blur();
      },
      shown: () => doc.visibilityState === "visible",
      safe() {
        lastSafeReason = "";
        if (interrupted) lastSafeReason = "You clicked or typed in WhatsApp while the request was running; it stopped.";
        else if (win.location.origin !== "https://web.whatsapp.com") lastSafeReason = "The WhatsApp page changed; the request stopped.";
        else if (!doc.querySelector("#pane-side")) lastSafeReason = "The WhatsApp chat list is not available; wait for it to load before trying again.";
        else if (!plainContext(doc.querySelector("#main"))) lastSafeReason = contextReason;
        return !lastSafeReason;
      },
      safeReason: () => lastSafeReason,  // Fixed categories only; no message, contact or control contents.
      wait: ms => new Promise(resolve => win.setTimeout(resolve, ms)),
      all() {
        const chosen = doc.querySelector('[aria-label="chat-list-filters"] [aria-selected="true"]');
        return !chosen || chosen.textContent.trim() === "All";
      },
      searchText: () => read(searchBox()).trim(),
      canCleanSearch: () => !interrupted,
      search: (value, authorized) => edit(searchBox(), value, true, authorized),
      rows(chat) {
        let candidates = [...doc.querySelectorAll('#pane-side [role="row"]')].filter(visible);
        const results = [...doc.querySelectorAll('#pane-side [role="grid"]')].filter(n =>
          visible(n) && /^Search results\.?$/.test((n.getAttribute("aria-label") || "").trim()));
        if (!results.length && [...doc.querySelectorAll(searchSelector + ', #side [role="textbox"], #side input')]
          .some(n => visible(n) && read(n).trim())) return [];  // unknown search layout is never the ordinary chat list
        if (results.length) {
          if (results.length !== 1) return [];
          // Search has separate Chats and Messages sections. A message hit can
          // carry the same title as a chat, but is never a recipient candidate.
          const rows = [...results[0].childNodes].filter(n => n.nodeType === 1 && visible(n) && n.getAttribute("role") === "row");
          const sections = rows.map((row, index) => ({index, headings: [...row.querySelectorAll("h2")].filter(visible)}))
            .filter(section => section.headings.length);
          if (!sections.length || sections[0].index !== 0 || sections[0].headings.length !== 1 ||
              sections[0].headings[0].textContent.trim() !== "Chats" ||
              sections.slice(1).some(section => section.headings.some(h => h.textContent.trim() === "Chats"))) return [];
          const end = sections.length > 1 ? sections[1].index : rows.length;
          candidates = rows.slice(1, end);
        }
        return candidates.filter(n => {
          const title = n.querySelector("span[title]");
          return title && title.getAttribute("title").trim() === chat;
        }).map(node => {
          const badge = node.querySelector('[aria-label*="unread"]');
          const count = badge ? parseInt(badge.textContent, 10) : 0;
          return {node, group: group(node), unread: badge ? (Number.isInteger(count) && count > 0 ? count : 1) : 0};
        });
      },
      open(row, authorized) {
        if (!visible(row.node)) throw fault("blocked", "The chat row changed.");
        const cells = [...row.node.childNodes].filter(n => n.nodeType === 1 && visible(n) &&
          n.getAttribute("role") === "gridcell" && n.getAttribute("tabindex") === "0");
        if (cells.length !== 1) throw fault("blocked", "The chat row's actionable cell could not be identified safely.");
        const frames = [...cells[0].querySelectorAll('[data-testid="cell-frame-container"]')].filter(n =>
          visible(n) && n.closest('[role="gridcell"]') === cells[0] && n.closest('[role="row"]') === row.node);
        if (frames.length !== 1) throw fault("blocked", "The chat row's clickable frame could not be identified safely.");
        const cell = cells[0], frame = frames[0];
        const sameTarget = () => visible(row.node) && visible(cell) && visible(frame) &&
          cell.parentElement === row.node && cell.getAttribute("role") === "gridcell" && cell.getAttribute("tabindex") === "0" &&
          [...row.node.childNodes].filter(n => n.nodeType === 1 && visible(n) &&
            n.getAttribute("role") === "gridcell" && n.getAttribute("tabindex") === "0").length === 1 &&
          frame.closest('[role="gridcell"]') === cell && frame.closest('[role="row"]') === row.node &&
          cell.contains(frame) && [...cell.querySelectorAll('[data-testid="cell-frame-container"]')].filter(visible).length === 1;
        // A click alone does not deliver mouse down/up handlers. Keep the standard
        // sequence on this one verified frame; never retry on a replacement node.
        for (const type of ["mousedown", "mouseup", "click"]) {
          if (!sameTarget()) return;  // Opening can replace the row; verify the header next.
          if (authorized) authorized();
          if (!sameTarget()) return;
          frame.dispatchEvent(new win.MouseEvent(type, {bubbles: true, cancelable: true, composed: true,
            view: win, button: 0, buttons: type === "mousedown" ? 1 : 0, detail: 1}));
        }
      },
      openProfile(chat, authorized) {
        const main = doc.querySelector("#main"), header = main && main.querySelector("header");
        const title = header && header.querySelector("span[dir='auto']");
        const buttons = header ? [...header.querySelectorAll('[aria-label="Profile details"]')].filter(n =>
          visible(n) && (n.tagName === "BUTTON" || n.getAttribute("role") === "button")) : [];
        if (!title || title.textContent.trim() !== chat || buttons.length !== 1) throw fault("blocked", "The exact chat's Profile details control could not be verified.");
        if (authorized) authorized();
        buttons[0].click();
      },
      current() {
        const main = doc.querySelector("#main"), header = main && main.querySelector("header");
        const title = header && header.querySelector("span[dir='auto']");
        if (!title || !visible(header)) return null;
        const editors = [...main.querySelectorAll('footer [contenteditable="true"][role="textbox"]')].filter(visible);
        if (editors.length !== 1) throw fault("blocked", "The message composer could not be identified safely.");
        const composer = editors[0], label = composer.getAttribute("aria-label") || "";
        if (!/^Type a message(?: to .+)?$/.test(label)) throw fault("blocked", "The message composer label was not recognized.");
        if (label.startsWith("Type a message to ") && label.slice(18).trim() !== title.textContent.trim()) throw fault("blocked", "The composer recipient does not match the open chat header.");
        const drawer = contactDrawer();
        const ids = [...main.querySelectorAll('[data-testid^="conv-msg-"][data-id]')]
          .filter(n => !n.closest('[data-testid*="quoted"], [aria-label="Quoted message"]') &&
            !(drawer.kind === "contact" && drawer.node.contains(n))).map(n => n.getAttribute("data-id"));
        const phones = new Set(ids.flatMap(id => [...id.matchAll(/(?:^|_)([1-9][0-9]{8,14})@c\.us(?:_|$)/g)].map(m => m[1])));
        const jidDirect = ids.some(id => /@(?:c\.us|lid)(?:_|$)/.test(id));
        const profile = profileIdentity(title.textContent.trim());
        const profileDirect = !jidDirect && profile.kind === "current" && !!profile.phone;
        return {chat: title.textContent.trim(), composer, text: read(composer), plain: plainContext(main),
                group: group(header) || ids.some(id => /@(?:g\.us|broadcast)(?:_|$)/.test(id)),
                direct: (jidDirect || profileDirect) && phones.size <= 1,
                identity: jidDirect ? "jid" : profileDirect ? "profile" : null,
                profileCurrent: profile.kind === "current", profileBlocked: profile.kind === "blocked",
                profilePhone: profile.kind === "current" ? profile.phone || null : null, numericConflict: phones.size > 1,
                phone: phones.size === 1 ? [...phones][0] : profileDirect ? profile.phone : null};
      },
      readView(target) {
        const current = this.current();
        if (!current || current.chat !== target.chat || current.composer !== target.composer ||
            same(current.text) !== same(target.text) || current.phone !== target.phone || current.profilePhone !== target.profilePhone || current.group || !current.direct) {
          throw fault("blocked", "The chat or message box changed while reading; the request stopped.");
        }
        const main = doc.querySelector("#main");
        const candidates = main ? [...main.querySelectorAll('[data-testid="conversation-panel-messages"]')].filter(visible) : [];
        if (candidates.length !== 1 || !win.__northwindReadCapture || typeof win.__northwindReadCapture.capture !== "function") return null;
        const container = candidates[0], lists = [...container.childNodes].filter(n => n.nodeType === 1 && n.getAttribute("data-tab") === "8" && visible(n));
        if (lists.length !== 1) return null;  // observed direct parent, never an arbitrary scrollable ancestor
        const capture = win.__northwindReadCapture.capture(activeReadCommand ? reactionProofs : undefined);
        if (!capture || capture.chat !== target.chat || !Array.isArray(capture.events) || !Number.isInteger(capture.unresolved)) return null;
        const top = container.scrollTop, height = container.scrollHeight, viewport = container.clientHeight;
        if (![top, height, viewport].every(Number.isFinite) || top < 0 || height < 0 || viewport <= 0) return null;
        const style = typeof win.getComputedStyle === "function" ? win.getComputedStyle(container) : null;
        return {container, list: lists[0], top, height, viewport, events: capture.events, unresolved: capture.unresolved, unresolvedAt: capture.unresolvedAt, unresolvedRows: capture.unresolvedRows,
          reactionTargets: capture.reactionTargets || [], scrollable: !!style && /^(auto|scroll)$/.test(style.overflowY)};
      },
      async inspectReaction(target, reaction, grant, guard) {
        if (!activeReadCommand || ownedReaction || activeReadCommand.chat !== target.chat) return false;
        const find = () => {
          const main = doc.querySelector("#main");
          const messages = main ? [...main.querySelectorAll('[data-testid^="conv-msg-"][data-id]')].filter(n =>
            n.getAttribute("data-id") === reaction.id && visible(n) && !n.closest('[data-testid*="quoted"], [aria-label="Quoted message"]')) : [];
          if (messages.length !== 1) return null;
          const row = messages[0].closest('[role="row"]');
          const chips = row ? [...row.querySelectorAll('button[data-testid="reaction-bubble"][aria-haspopup="true"]')].filter(n =>
            visible(n) && n.closest('[role="row"]') === row && !n.closest('[data-testid*="quoted"], [aria-label="Quoted message"]') &&
            /^reaction .+\. View reactions$/.test(n.getAttribute("aria-label") || "")) : [];
          const view = this.readView(target);
          return chips.length === 1 && view && view.reactionTargets.some(r => r.id === reaction.id && r.key === reaction.key) ? {row, chip: chips[0]} : null;
        };
        if ([...doc.querySelectorAll(reactionToolbar)].some(visible)) return false;
        const initial = find();
        if (!initial) return false;
        const boundary = await grant(); boundary();
        const check = () => {
          boundary();
          const fresh = find();
          if (!fresh || fresh.row !== initial.row || fresh.chip !== initial.chip) throw fault("blocked", "The message reaction changed before inspection.");
        };
        ownedReaction = {id: reaction.id, popup: null};
        for (const type of ["mousedown", "mouseup", "click"]) {
          check();
          initial.chip.dispatchEvent(new win.MouseEvent(type, {bubbles: true, cancelable: true, composed: true,
            view: win, button: 0, buttons: type === "mousedown" ? 1 : 0, detail: 1}));
        }
        let popup = null;
        for (let i = 0; i < 8; i++) {
          await this.wait(100); guard();
          popup = reactionPopup();
          if (popup) break;
        }
        if (!popup) { ownedReaction = null; return false; }
        ownedReaction.popup = popup.node;
        let snapshot = reactionSnapshot(popup, reaction.id, target.chat);
        const closing = await grant(); closing();
        closing();
        const currentPopup = reactionPopup();
        if (!currentPopup) snapshot = null;
        else {
          if (currentPopup.node !== popup.node) throw fault("blocked", "The reactions popup changed before closing.");
          if (JSON.stringify(reactionSnapshot(currentPopup, reaction.id, target.chat)) !== JSON.stringify(snapshot)) snapshot = null;
          // Match native Escape's legacy numeric fields as well as its key name.
          // Send it once: a second event during the closing animation can reach
          // the underlying chat instead of the popup that owned the first one.
          popup.toolbar.dispatchEvent(new win.KeyboardEvent("keydown", {key: "Escape", code: "Escape", keyCode: 27, which: 27,
            bubbles: true, cancelable: true}));
        }
        for (let i = 0; i < 10 && [...doc.querySelectorAll(reactionToolbar)].some(visible); i++) {
          await this.wait(100); guard();
        }
        if ([...doc.querySelectorAll(reactionToolbar)].some(visible)) throw fault("blocked", "The reactions popup is still open. Close it yourself before continuing.");
        ownedReaction = null;
        const fresh = find();
        if (snapshot && fresh && fresh.row === initial.row && fresh.chip === initial.chip) {
          reactionProofs.set(reaction.id, {key: reaction.key, snapshot, row: initial.row, chip: initial.chip}); return true;
        }
        return false;
      },
      scrollRead(view, position, stillAuthorized) {
        if (stillAuthorized) stillAuthorized();
        const main = doc.querySelector("#main"), container = view.container;
        if (!main || !visible(container) || !main.contains(container) || !view.scrollable ||
            view.list.parentElement !== container || !container.contains(view.list) || !visible(view.list) ||
            [...main.querySelectorAll('[data-testid="conversation-panel-messages"]')].filter(visible).length !== 1 ||
            [...container.childNodes].filter(n => n.nodeType === 1 && n.getAttribute("data-tab") === "8" && visible(n)).length !== 1 ||
            !Number.isFinite(position) || position < 0 || position > Math.max(0, container.scrollHeight - container.clientHeight)) {
          throw fault("blocked", "The supported conversation scroll area changed; the request stopped.");
        }
        container.scrollTop = position;
      },
      deliverRead(command, events, reading) {
        return win.chrome.runtime.sendMessage({type: "read_capture", id: command.id, owner: command.owner, events, reading});
      },
      beginRead(command) {
        const started = win.__northwindReadCapture && typeof win.__northwindReadCapture.begin === "function" &&
          win.__northwindReadCapture.begin(command.id, command.chat);
        if (started) { activeReadCommand = command; reactionProofs.clear(); ownedReaction = null; }
        return started;
      },
      endRead(command) {
        const ended = win.__northwindReadCapture && win.__northwindReadCapture.end(command.id);
        if (ended) { activeReadCommand = null; reactionProofs.clear(); ownedReaction = null; }
        return ended;
      },
      refreshReader() {
        return win.__northwindReadCapture && typeof win.__northwindReadCapture.refresh === "function" ?
          win.__northwindReadCapture.refresh() : undefined;
      },
      insert(target, text, stillAuthorized) {
        const validate = () => {
          const current = this.current();
          if (stillAuthorized) stillAuthorized();
          if (!current || current.chat !== target.chat || current.composer !== target.composer || current.text || !current.plain ||
              current.group || !current.direct || current.phone !== target.phone) throw fault("blocked", "The composer or recipient changed before typing.");
        };
        validate();
        edit(target.composer, text, false, validate);
      },
    };
  }

  if (typeof module === "object" && module.exports) { module.exports = {executor, browserAdapter, readHistory, bubbleTime, CAPABILITY}; return; }
  if (!root.chrome || root.__northwindDrafts) return;
  root.__northwindDrafts = true;
  let owner = null;
  const execute = executor(browserAdapter(document, root), Date.now,
    request => chrome.runtime.sendMessage(Object.assign({type: "draft_authorize"}, request)));
  async function announce() {
    try {
      const result = await chrome.runtime.sendMessage({type: "drafts_ready", capability: CAPABILITY});
      owner = result && typeof result.owner === "string" ? result.owner : null;
    } catch (e) { owner = null; }
  }
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (!message || message.type !== "draft_action" || sender.id !== chrome.runtime.id || !owner) return;
    execute(message.action, owner).then(respond);
    return true;
  });
  announce();
  root.setInterval(announce, 20000);
})(typeof window === "object" ? window : globalThis);
