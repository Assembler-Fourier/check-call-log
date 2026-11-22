"""Pure, session-only roster reading coordinator. No files, UI, networking or sends.

The caller supplies current local time, saved identities, bridge state and callbacks.
Turning reading on is an explicit action. A paused pass never resumes by itself.
"""
import copy
import datetime
import hashlib
import json
import math
import time
from collections.abc import Mapping

import wa_beta
from wa_bridge import reading_summary

PARTIAL_REASONS = {
    "history_start": "The available history did not reach the required earlier context.",
    "limit": "The reading limit was reached before all required history was loaded.",
    "timeout": "The available reading time ended before all required history was loaded.",
    "no_messages": "No readable dated messages were found.",
    "history_unavailable": "Some message history could not be read reliably."
}
SKIP_REASONS = {
    "not_found": "The exact saved WhatsApp chat was not found. Review this contact.",
    "ambiguous_chat": "More than one exact WhatsApp chat matched. Review this contact; none was selected.",
    "group_chat": "The saved chat is a group or broadcast. Choose this guard's verified individual chat."
}
WAIT_CODES = ("hidden", "search", "no_list", "stale", "incomplete")   # not ready for a moment: wait, do not stop
USER_ACTIVE_WAIT = 10                                  # seconds to wait before trying again while he uses WhatsApp
RETRY_DELAYS = (300, 600, 1200)                         # unresolved/partial: 5, 10, then 20 minutes
REACTION_FRESHNESS = 900                               # only while the call's takeback window is active
TAKEBACK_MINUTES = 90
STOP_REASONS = {
    "blocked": "WhatsApp could not verify the request.",
    "unknown": "The result could not be verified.",
    "expired": "The request timed out.",
    "failed": "The reader reported an error.",
    "cancelled": "The request was cancelled."
}


def _chat(value):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        return None
    try:
        return value.strip() if len(value.encode("utf-16-le")) // 2 <= 120 else None
    except UnicodeError:
        return None


def activity_signals(events):
    """Semantic activity for scheduling only, never confirmation evidence.

    Keep provenance separate from the token so the coordinator can ignore its own
    captures without ignoring quarantined passive messages. Replayed observations,
    read/view IDs and arrival timestamps cannot create a new semantic token.
    """
    chats = {}
    for event in events:
        if not isinstance(event, Mapping) or event.get("kind") not in ("line", "bubble") \
                or not isinstance(event.get("chat"), str) or not event["chat"].strip() \
                or event.get("group") is True or event.get("draft") is True or event.get("what") == "draft":
            continue
        seen = event.get("seen", 0)
        if type(seen) not in (int, float) or not math.isfinite(seen):
            continue
        semantic = [event.get(key) for key in ("kind", "id", "date", "shown", "direction", "ours", "text",
                                             "what", "media_type", "emoji", "to", "truncated")]
        try:
            token = hashlib.sha256(json.dumps(semantic, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()).hexdigest()
            reaction_token = (hashlib.sha256(json.dumps(event["reactions"], sort_keys=True, ensure_ascii=True,
                                                       allow_nan=False).encode()).hexdigest()
                              if event["kind"] == "bubble" and "reactions" in event else None)
        except (TypeError, ValueError):
            continue
        key = "preview" if event["kind"] == "line" else "message:" + event["id"] if isinstance(event.get("id"), str) and event["id"] else "unidentified:" + token
        source = event.get("read_id") if isinstance(event.get("read_id"), str) else None
        records = chats.setdefault(event["chat"], {})
        records[(key, token, seen, source)] = {"key": key, "token": token, "read_id": source, "seen": seen}
        # Passive observations deliberately omit optional reaction snapshots. Absence is no new information,
        # not a removal. A separate stream preserves explicit changes (including complete-empty removal)
        # while content/direction edits and provenance filtering continue to work independently.
        if reaction_token is not None:
            reaction_key = "reaction:" + key
            records[(reaction_key, reaction_token, seen, source)] = {
                "key": reaction_key, "token": reaction_token, "read_id": source, "seen": seen}
    # No truncation before provenance filtering: a large owned capture must never
    # push a changed external preview out of the scheduling signal set.
    return {chat: sorted(records.values(), key=lambda row: row["seen"]) for chat, records in chats.items()}


def build_plan(log, contacts, chat_names, now, settings=None, *, activity=None, edits=None):
    """Build one target per person on the live or upcoming-today Lena shift.

    Cover the whole roster date for explicitly asked early calls, and at least
    the 16-hour check-in/quiet-period lookback when that reaches further back.
    Fingerprints intentionally exclude heard responses, colours and cache timestamps.
    """
    plan = dict(eligible=False, scope=None, fingerprint=None, targets=[], issues=[], reason="")
    if not isinstance(log, Mapping) or not wa_beta.actionable_shift(log, now):
        plan["reason"] = "Choose Lena Drayton's live shift or a shift starting today."
        return plan
    if not isinstance(contacts, Mapping) or not isinstance(chat_names, Mapping):
        plan["reason"] = "Saved contact mappings need review."
        return plan
    meta = log["meta"]
    day = datetime.date.fromisoformat(meta["date_iso"])
    calls = log.get("calls")
    if not isinstance(calls, (list, tuple)):
        plan["reason"] = "The shown shift has no valid roster."
        return plan
    identities, assignments, calls_by_person = {}, [], {}
    for call in calls:
        if not isinstance(call, Mapping):
            plan["issues"].append(dict(name="Unknown guard", reason="The roster row needs review."))
            continue
        name = call.get("name")
        ident = wa_beta.normalize_name(name)
        if not ident:
            plan["issues"].append(dict(name="Unknown guard", reason="The roster row has no full name."))
            continue
        identities.setdefault(ident, name.strip())
        calls_by_person.setdefault(ident, []).append(call)
        assignments.append([ident, call.get("site"), call.get("shift"), call.get("start")])
    # A duplicate saved spelling is acceptable only when all of its values agree.
    phones, chats = {}, {}
    for key, value in contacts.items():
        ident = wa_beta.normalize_name(key)
        if ident:
            phones.setdefault(ident, []).append(wa_beta.normalize_phone(value))
    for key, value in chat_names.items():
        ident = wa_beta.normalize_name(key)
        if ident:
            chats.setdefault(ident, []).append(_chat(value))
    phone_owners, chat_owners = {}, {}
    for ident, values in phones.items():
        for value in values:
            if value:
                phone_owners.setdefault(value, set()).add(ident)
    for ident, values in chats.items():
        for value in values:
            if value:
                chat_owners.setdefault(wa_beta.normalize_name(value), set()).add(ident)
    midnight = datetime.datetime.combine(day, datetime.time())
    starts = [meta["start_min"]] + [c.get("start") for c in calls if isinstance(c, Mapping) and wa_beta._minute(c.get("start"))]
    begins = midnight + datetime.timedelta(minutes=min(starts))
    since = int(min(now, midnight, begins - datetime.timedelta(hours=16)).timestamp() * 1000)
    watch = {}
    trusted = settings.get("trusted", ()) if isinstance(settings, Mapping) else ()
    trusted = {wa_beta.normalize_name(name) for name in trusted} if isinstance(trusted, (list, tuple, set)) else set()
    edits = edits if isinstance(edits, Mapping) else {}
    for ident, name in identities.items():
        pv, cv = phones.get(ident, []), chats.get(ident, [])
        reason = None
        if not pv or not all(pv):
            reason = "Save a valid verified phone number first."
        elif len(set(pv)) != 1 or len(phone_owners[pv[0]]) != 1:
            reason = "The saved phone number is ambiguous; review Contacts."
        elif not cv or not all(cv):
            reason = "Save the exact verified WhatsApp chat name first."
        elif len(set(cv)) != 1 or len(chat_owners[wa_beta.normalize_name(cv[0])]) != 1:
            reason = "The saved chat name is ambiguous; review Contacts."
        if reason:
            plan["issues"].append(dict(name=name, reason=reason))
        else:
            plan["targets"].append(dict(name=name, chat=cv[0], phone=pv[0], since=since))
            windows = []
            for call in calls_by_person[ident]:
                if not all(isinstance(call.get(key), str) for key in ("name", "site", "shift")) or not wa_beta._minute(call.get("start")):
                    continue
                key = wa_beta._edit_key(call)
                status = wa_beta._status(call, edits.get(key), trusted)
                due = max(meta["start_min"], call["start"] - 60)  # recorded call/reply time is mutable evidence
                heard = call.get("heard") if isinstance(call.get("heard"), Mapping) else {}
                windows.append(dict(key=key, due=(midnight + datetime.timedelta(minutes=due)).timestamp(),
                                    until=(midnight + datetime.timedelta(minutes=call["start"] + TAKEBACK_MINUTES)).timestamp(),
                                    resolved=status in ("confirmed", "trusted"), reaction=heard.get("how") == "reaction"))
            watch[cv[0]] = {"windows": windows}
    scope = "%s|%s|%s|%s|%s" % (log.get("stem", ""), meta["date_iso"], meta["start_min"], meta["end_min"],
                                    wa_beta.normalize_name(meta.get("supervisor")))
    # Including all saved mappings also catches a new conflicting alias outside this roster.
    identity = [scope, sorted(assignments, key=repr),
                sorted((key, sorted(values, key=repr)) for key, values in phones.items()),
                sorted((key, sorted(values, key=repr)) for key, values in chats.items()), settings]
    try:
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    except (ValueError, TypeError):
        plan.update(targets=[], reason="Saved reading settings need review.")
        return plan
    plan.update(eligible=True, scope=scope, fingerprint=fingerprint, watch=watch,
                activity=activity if isinstance(activity, Mapping) else {})
    if not plan["targets"]:
        plan["reason"] = "No unambiguous saved contacts are ready for this shift."
    return plan


class ReadCoordinator:
    """Serialize read-only requests and stop without replay on uncertain outcomes.

    `clock` is monotonic; `wall_clock` is used only to validate returned message dates.
    Callbacks must return promptly. The GUI owns scheduling, never this helper.
    """
    def __init__(self, enqueue, action_status, cancel, clock=time.monotonic, cooldown=120, wall_clock=time.time):
        self.enqueue, self.action_status, self.cancel = enqueue, action_status, cancel
        self.clock, self.wall_clock = clock, wall_clock
        self.cooldown = max(120, float(cooldown))
        self._pending = self._owner = self._fingerprint = None
        self._targets, self._base_issues = [], []
        self._all_targets, self._checks, self._issued = [], {}, set()
        self._signal_states, self._signal_tokens = {}, {}
        self._activity_snapshot, self._activity_issued = None, -1
        self._pending_tokens, self._pending_due = set(), set()
        self._index, self._started = 0, 0
        self._cleanup_until = self._retry_at = None
        self._state = dict(state="idle", enabled=False, continuous=False, total=0, read=0,
                           messages=0, incoming=0, outgoing=0, current=None, issues=[], reason="",
                           next_cycle_at=None, last_result=None, unread_opened=[], mode="idle",
                           roster_total=0, not_needed=0, next_due_at=None, coverage=[], attention=0)

    def status(self):
        return copy.deepcopy(self._state)

    def stop(self, reason="Stopped"):
        pending, self._pending = self._pending, None
        if pending:
            try:
                self.cancel(pending)
            except Exception:
                reason = str(reason) + " The pending request could not be cancelled; inspect WhatsApp before restarting."
        self._state.update(state="paused", enabled=False, current=None, reason=str(reason)[:800], next_cycle_at=None, next_due_at=None)
        return self.status()

    def start(self, plan, continuous=False, force_full=False):
        if self._pending:
            self.stop("Starting a new pass.")
        self._owner = None
        self._cleanup_until = self._retry_at = None
        fingerprint = plan.get("fingerprint") if isinstance(plan, dict) else None
        retained_issues = copy.deepcopy(self._state["issues"]) if fingerprint == self._fingerprint else []
        if fingerprint != self._fingerprint:
            self._checks, self._issued = {}, set()
            self._signal_states, self._signal_tokens = {}, {}
            self._activity_snapshot, self._activity_issued = None, -1
        self._fingerprint = fingerprint
        self._all_targets = copy.deepcopy(plan.get("targets", [])) if isinstance(plan, dict) else []
        self._targets = copy.deepcopy(self._all_targets)
        self._base_issues = copy.deepcopy(plan.get("issues", [])) if isinstance(plan, dict) else []
        self._state.update(continuous=continuous is True, total=len(self._targets), roster_total=len(self._targets),
                           issues=copy.deepcopy(self._base_issues) + [issue for issue in retained_issues if issue not in self._base_issues],
                           not_needed=0, next_due_at=None,
                           mode="full" if force_full or continuous is not True else "bootstrap")
        self._reset_counts()
        if not isinstance(plan, dict) or plan.get("eligible") is not True or not self._fingerprint or not self._targets:
            return self.stop(plan.get("reason", "No readable contacts are ready.") if isinstance(plan, dict) else "No readable contacts are ready.")
        if continuous is True and not force_full:
            self._targets = [target for target in self._all_targets if target["chat"] not in self._checks]
            self._state.update(total=len(self._targets), not_needed=len(self._all_targets) - len(self._targets))
            if not self._targets:
                self._state.update(state="waiting", enabled=True, mode="watching", reason="Watching for new activity or a due check call.")
                self._choose_updates(plan)
                return self.status()
        self._state.update(state="running", enabled=True, reason="Starting read pass.")
        return self.status()

    def _reset_counts(self):
        self._index = 0
        self._state.update(read=0, messages=0, incoming=0, outgoing=0, current=None, next_cycle_at=None, last_result=None)

    def _finish(self):
        if self._state["continuous"]:
            self._state.update(state="waiting", current=None, mode="watching", reason="Watching for new activity or a due check call.",
                               next_cycle_at=None, next_due_at=None)
        else:
            self._state.update(state="complete", enabled=False, current=None, reason="Read pass complete.")
        return self.status()

    def _tokens(self, plan, chat):
        activity = plan.get("activity", {})
        if activity is not self._activity_snapshot or self._activity_issued != len(self._issued):
            self._activity_snapshot, self._activity_issued = activity, len(self._issued)
            for target in self._all_targets:
                name = target["chat"]
                rows = activity.get(name, ()) if isinstance(activity, Mapping) else ()
                states = self._signal_states.setdefault(name, {})
                for row in rows if isinstance(rows, (list, tuple)) else ():
                    if not isinstance(row, Mapping) or not isinstance(row.get("token"), str) or not isinstance(row.get("key"), str) \
                            or type(row.get("seen")) not in (int, float) or not math.isfinite(row["seen"]) \
                            or isinstance(row.get("read_id"), str) and row["read_id"] in self._issued:
                        continue
                    state = states.setdefault(row["key"], dict(seen=-1, seen_tokens=set(), current=None, revision=0))
                    if row["seen"] < state["seen"] or row["seen"] == state["seen"] and row["token"] in state["seen_tokens"]:
                        continue
                    if row["seen"] > state["seen"]:
                        state.update(seen=row["seen"], seen_tokens=set())
                    state["seen_tokens"].add(row["token"])
                    if row["token"] != state["current"]:
                        state.update(current=row["token"], revision=state["revision"] + 1)
                self._signal_tokens[name] = {"%s:%s:%s" % (key, state["revision"], state["current"]) for key, state in states.items()}
        return set(self._signal_tokens.get(chat, ()))

    def _windows(self, plan, chat):
        watch = plan.get("watch", {})
        value = watch.get(chat, {}) if isinstance(watch, Mapping) else {}
        return value.get("windows", []) if isinstance(value, Mapping) else []

    def _coverage(self, plan):
        now = self.wall_clock()
        coverage, attention = [], 0
        for target in self._all_targets:
            held = self._checks.get(target["chat"], {})
            windows = self._windows(plan, target["chat"])
            needs_attention = bool(held.get("blocked") or (held and (held.get("partial") or not windows or any(not w["resolved"] for w in windows))
                                   and not any(w["due"] <= now <= w["until"] for w in windows)
                                   and not any(w["due"] > now for w in windows)))
            attention += needs_attention
            coverage.append(dict(name=target["name"], last_checked_at=held.get("last_complete"),
                                 last_attempt_at=held.get("attempt_at"), partial=held.get("partial", False),
                                 needs_attention=needs_attention))
        self._state.update(coverage=coverage, attention=attention)

    def _remember(self, plan, target, summary=None, blocked=False):
        chat = target["chat"]
        prior = self._checks.get(chat, {})
        held = dict(known=set(prior.get("known", ())) | self._pending_tokens,
                    due=set(prior.get("due", ())) | self._pending_due,
                    last_attempt=self.clock(), attempt_at=self.wall_clock(),
                    last_complete=prior.get("last_complete"), partial=bool(summary and not summary["complete"]),
                    retries=prior.get("retries", -1) + 1, blocked=blocked)
        if summary and summary["complete"]:
            held["last_complete"] = self.wall_clock()
        self._checks[chat] = held
        self._coverage(plan)

    def _choose_updates(self, plan):
        """Choose only needed chats; freshness and retry deadlines never establish reply evidence."""
        now, wall = self.clock(), self.wall_clock()
        due, future = [], []
        for target in self._all_targets:
            chat = target["chat"]
            held = self._checks.get(chat)
            if not held:
                due.append((0, target, "Reading a chat not yet checked this session."))
                continue
            if held.get("blocked"):
                continue                         # contact ambiguity needs explicit review/full check
            if self._tokens(plan, chat) - held["known"]:
                due.append((0, target, "New chat activity needs checking."))
                continue
            windows = self._windows(plan, chat)
            new_due = [w for w in windows if not w["resolved"] and w["key"] not in held["due"] and w["due"] <= wall <= w["until"]]
            if new_due:
                due.append((1, target, "A later check call is now due."))
                continue
            future.extend(now + w["due"] - wall for w in windows if (not w["resolved"] or w["reaction"])
                          and w["key"] not in held["due"] and w["due"] > wall)
            active = [w for w in windows if w["due"] <= wall <= w["until"]]
            if not active:
                continue
            if held["partial"] or any(not w["resolved"] for w in active):
                delay = RETRY_DELAYS[min(held["retries"], len(RETRY_DELAYS) - 1)]
                reason = "Rechecking an unresolved or partial call after its retry interval."
            elif any(w["reaction"] for w in active):
                delay = REACTION_FRESHNESS
                reason = "Checking that an attached reaction is still present."
            else:
                continue                         # complete text/manual/trusted confirmations stay quiet
            deadline = held["last_attempt"] + delay
            if deadline <= now:
                due.append((2, target, reason))
            elif wall + deadline - now <= max(w["until"] for w in active):
                future.append(deadline)
        self._coverage(plan)
        self._state.update(not_needed=len(self._all_targets) - len(due),
                           next_cycle_at=min(future) if future else None,
                           next_due_at=wall + min(future) - now if future else None)
        if not due:
            self._state.update(state="waiting", mode="watching", current=None,
                               reason="Watching for new activity or a due check call; unchanged confirmed chats are left alone.")
            return False
        due.sort(key=lambda item: item[0])
        self._targets = [copy.deepcopy(item[1]) for item in due]
        self._reset_counts()
        self._state.update(state="running", mode="updates", total=len(self._targets),
                           reason=due[0][2], next_due_at=None)
        # Keep contact/partial issues until a later complete read resolves that chat.
        return True

    def _issue(self, target, reason, result):
        # The banner uses only the short coordinator reason. The details dialog
        # shows this bounded, in-memory action guidance; it is never persisted here.
        detail = result.get("detail")
        if isinstance(detail, str) and detail.strip():
            reason += " " + detail[:800].strip()
        self._state["issues"].append(dict(name=target["name"], reason=reason))

    def tick(self, plan, readiness):
        if not self._state["enabled"]:
            return self.status()
        if not isinstance(plan, dict) or plan.get("eligible") is not True or plan.get("fingerprint") != self._fingerprint:
            return self.stop("The shift, controller, date, contacts or settings changed. Review the shift and restart reading.")
        if not isinstance(readiness, dict):
            return self.stop("WhatsApp readiness could not be checked. Restart when ready.")
        owner = readiness.get("owner")
        if self._owner is not None and owner != self._owner:
            return self.stop("The WhatsApp tab changed. Restart reading explicitly.")
        if self._pending:
            if type(readiness.get("pending")) is int and readiness["pending"] > 1:
                return self.stop("Another WhatsApp action started. Finish it before restarting reading.")
            # A running action's own search pauses sidebar capture. The browser still
            # checks visibility and user interaction before every granted read step.
            try:
                result = self.action_status(self._pending)
            except Exception:
                return self.stop("The reading result could not be checked. Inspect WhatsApp before restarting.")
            if not isinstance(result, dict) or result.get("id") != self._pending:
                return self.stop("The reading result did not match this request. Restart explicitly.")
            status = result.get("status")
            if status in ("queued", "running"):
                if self.clock() - self._started >= 125:
                    return self.stop("The reading request did not finish in time. Inspect WhatsApp before restarting.")
                return self.status()
            target = self._targets[self._index]
            self._pending = None
            unread = result.get("unread_before")
            if type(unread) is int and 0 < unread < 1000:
                # Keep the badge warning even if interaction interrupted the read after opening.
                # It survives retries and later passes for the whole app session.
                self._state["unread_opened"] = [u for u in self._state["unread_opened"] if u["name"] != target["name"]] + [
                    dict(name=target["name"], count=unread, at=int(self.wall_clock()))]
            if status == "user_active":
                # The read may have opened a chat. Wait, then retry the SAME guard without counting a visit.
                self._state.update(current=None, reason="Waiting: you are using WhatsApp. Reading carries on when you stop.")
                self._retry_at = self.clock() + USER_ACTIVE_WAIT
                return self.status()
            if status == "read":
                summary = reading_summary(result.get("reading"), target["since"], int(self.wall_clock() * 1000))
                if summary is None:
                    self._issue(target, "The reading summary could not be verified.", result)
                    return self.stop("The reading summary could not be verified. Restart explicitly.")
                self._state["read"] += 1
                for field in ("messages", "incoming", "outgoing"):
                    self._state[field] += summary[field]
                self._state["last_result"] = summary
                self._remember(plan, target, summary)
                self._state["issues"] = [issue for issue in self._state["issues"] if issue.get("name") != target["name"]]
                if not summary["complete"]:
                    self._issue(target, "Only part of this conversation was read. " + PARTIAL_REASONS[summary["reason"]], result)
            elif status in SKIP_REASONS:
                self._remember(plan, target, blocked=True)
                self._issue(target, SKIP_REASONS[status], result)
            else:
                reason = STOP_REASONS.get(status, "The reader returned an unexpected result.")
                self._issue(target, reason, result)
                return self.stop("Reading stopped for %s. %s Check WhatsApp and restart explicitly." % (target["name"], reason))
            self._index += 1
            self._cleanup_until = self.clock() + 35
            self._state["current"] = None
            if self._index >= len(self._targets):
                return self._finish()
            # Give the action's cleanup/heartbeat a separate scheduler turn before
            # considering another recipient. No recursive queueing or immediate retry.
            return self.status()
        if readiness.get("busy") is True or readiness.get("pending", 0):
            return self.stop("Finish the current WhatsApp action before restarting reading.")
        if readiness.get("ready") is not True:
            if readiness.get("reason_code") == "search" and self._cleanup_until is not None and self.clock() < self._cleanup_until:
                self._state["reason"] = "Waiting for WhatsApp to report that its chat search has cleared."
                return self.status()
            if readiness.get("reason_code") in WAIT_CODES and isinstance(owner, str) and owner and (self._owner is None or owner == self._owner):
                # 8 Nov review: these pass by themselves (WhatsApp behind another window, a search, a slow load). Auto
                # read used to stop for good with only an amber line; it now waits on the same tab and carries on.
                self._owner = owner                   # a first hidden/searching tab still owns this explicit pass
                self._state["reason"] = "Waiting: %s Reading carries on by itself." % (readiness.get("reason") or "WhatsApp is not ready.")
                return self.status()
            return self.stop(readiness.get("reason") or "Show WhatsApp and restart reading when ready.")
        if not isinstance(owner, str) or not owner:
            return self.stop("The WhatsApp tab could not be identified. Reload the reader and restart.")
        self._owner = owner
        self._cleanup_until = None
        if self._state["state"] == "waiting":
            if not self._choose_updates(plan):
                return self.status()
        if self._index >= len(self._targets):
            return self._finish()
        if self._retry_at is not None and self.clock() < self._retry_at:
            return self.status()
        self._retry_at = None
        target = self._targets[self._index]
        try:
            key = self.enqueue("read", target["chat"], phone=target["phone"], since=target["since"])
        except Exception as exc:
            return self.stop("Reading could not start: %s" % exc)
        if not isinstance(key, str) or not key:
            return self.stop("WhatsApp did not accept the reading request. Restart explicitly.")
        self._issued.add(key)
        self._pending_tokens = self._tokens(plan, target["chat"])
        self._pending_due = {window["key"] for window in self._windows(plan, target["chat"]) if window["due"] <= self.wall_clock()}
        self._pending, self._started = key, self.clock()
        self._state.update(state="running", current=target["name"], reason="Reading the verified conversation.")
        return self.status()
