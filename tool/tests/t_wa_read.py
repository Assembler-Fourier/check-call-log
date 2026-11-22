"""Pure synthetic roster reading tests. No private files, services, UI or real WhatsApp."""
import copy
import datetime as dt
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wa_read

checks = []
def check(label, value):
    checks.append(bool(value))
    if not value:
        print("FAIL", label)

NOW = dt.datetime(2026, 11, 7, 21, 30)
LOG = {"stem": "synthetic-night", "meta": {"date_iso": "2026-11-07", "supervisor": "Lena Drayton",
        "start_min": 1080, "end_min": 1800}, "calls": [
        {"name": "Alex Example", "site": "Hall", "shift": "20:00-close", "start": 1200},
        {"name": "Alex Example", "site": "Other Hall", "shift": "22:00-close", "start": 1320},
        {"name": "Blair Example", "site": "Hall", "shift": "20:00-close", "start": 1200}]}
PHONES = {"Alex Example": "353870000001", "Blair Example": "353870000002"}
CHATS = {"Alex Example": "Alex Saved", "Blair Example": "Blair Saved"}
def plan(log=None, contacts=None, chats=None, now=NOW, settings=None, activity=None, edits=None):
    return wa_read.build_plan(LOG if log is None else log, PHONES if contacts is None else contacts,
                              CHATS if chats is None else chats, now, settings=settings, activity=activity, edits=edits)

original = copy.deepcopy((LOG, PHONES, CHATS))
p = plan()
check("one target per normalized person, no draft payload", p["eligible"] and len(p["targets"]) == 2
      and p["targets"][0]["chat"] == "Alex Saved" and all(set(t) == {"name", "chat", "phone", "since"} for t in p["targets"]))
check("evening shifts cover their entire roster date for early explicit questions",
      p["targets"][0]["since"] == int(dt.datetime(2026, 11, 7).timestamp() * 1000))
check("a same-day question at half past midnight falls inside the requested history",
      p["targets"][0]["since"] <= int(dt.datetime(2026, 11, 7, 0, 30).timestamp() * 1000))
morning_shift = copy.deepcopy(LOG); morning_shift["meta"]["start_min"] = 8*60
check("the existing sixteen-hour quiet-period lookback is retained when earlier than midnight",
      plan(log=morning_shift)["targets"][0]["since"] == int(dt.datetime(2026, 11, 6, 16).timestamp() * 1000))
check("plan leaves every supplied object untouched", (LOG, PHONES, CHATS) == original)
for controller in ("Lena", "Lena Other", "Other Controller", None):
    changed = copy.deepcopy(LOG); changed["meta"]["supervisor"] = controller
    check("only exact full controller eligible", not plan(log=changed)["eligible"])
changed = copy.deepcopy(LOG); changed["meta"]["supervisor"] = "  LENA   DRAYTON "
check("controller Unicode case and spacing normalize", plan(log=changed)["eligible"])
check("upcoming same local day allowed", plan(now=dt.datetime(2026, 11, 7, 1))["eligible"])
check("ongoing overnight allowed", plan(now=dt.datetime(2026, 11, 8, 5, 59))["eligible"])
check("exact shift endpoint is no longer eligible", not plan(now=dt.datetime(2026, 11, 8, 6))["eligible"])
check("future calendar day remains preview only", not plan(now=dt.datetime(2026, 11, 6, 23, 59))["eligible"])
check("timezone-aware time is not silently reinterpreted", not plan(now=NOW.replace(tzinfo=dt.timezone.utc))["eligible"])
for field, value in (("date_iso", "bad"), ("start_min", True), ("end_min", 1080)):
    changed = copy.deepcopy(LOG); changed["meta"][field] = value
    check("malformed shift metadata rejected", not plan(log=changed)["eligible"])
check("missing contacts highlighted individually", len(plan(contacts={})["issues"]) == 2 and not plan(contacts={})["targets"])
check("missing exact chat does not guess roster name", len(plan(chats={})["issues"]) == 2 and not plan(chats={})["targets"])
for invalid in ("", "not a phone", 353870000001, None):
    value = dict(PHONES, **{"Alex Example": invalid})
    check("malformed saved phone excluded", [t["name"] for t in plan(contacts=value)["targets"]] == ["Blair Example"])
for invalid in ("", "Alex\nSaved", "👍" * 61, "\ud800", None):
    value = dict(CHATS, **{"Alex Example": invalid})
    check("unsupported chat aliases excluded", [t["name"] for t in plan(chats=value)["targets"]] == ["Blair Example"])
equivalent = dict(PHONES, **{"Ａｌｅｘ Example": "+353 87 000 0001"})
check("equivalent full-name aliases with same phone are one identity", len(plan(contacts=equivalent)["targets"]) == 2)
conflict = dict(PHONES, **{"Ａｌｅｘ Example": "353870000003"})
check("same-name conflicting number cannot be chosen arbitrarily", len(plan(contacts=conflict)["targets"]) == 1)
conflict = dict(CHATS, **{" ALEX  EXAMPLE ": "Different exact chat"})
check("same-name conflicting chat cannot be chosen arbitrarily", len(plan(chats=conflict)["targets"]) == 1)
conflict = dict(PHONES, **{"Blair Example": PHONES["Alex Example"]})
check("shared phone blocks both distinct guards", not plan(contacts=conflict)["targets"])
conflict = dict(CHATS, **{"Blair Example": " alex   saved "})
check("ambiguous chat blocks both even with normalization differences", not plan(chats=conflict)["targets"])
conflict = dict(PHONES, **{"Outside Roster": PHONES["Alex Example"]})
check("conflicting saved identity outside roster still blocks target", len(plan(contacts=conflict)["targets"]) == 1)
changed = copy.deepcopy(LOG)
changed["calls"][0].update(heard={"response": "Ok"}, response="Ok", colour="green")
changed["timestamp"] = "new cache"
check("normal reply ingestion does not invalidate the active pass", plan(log=changed)["fingerprint"] == p["fingerprint"])
changed["calls"][0]["site"] = "Changed Assignment"
check("assignment change invalidates pass", plan(log=changed)["fingerprint"] != p["fingerprint"])
check("same overnight shift survives local midnight", plan(now=dt.datetime(2026, 11, 8, 1))["fingerprint"] == p["fingerprint"])
changed = copy.deepcopy(LOG); changed["meta"]["date_iso"] = "2026-11-08"
check("selecting another shift date still changes scope", plan(log=changed, now=dt.datetime(2026, 11, 8, 20))["fingerprint"] != p["fingerprint"])
check("saved settings change invalidates pass", plan(settings={"controllers": []})["fingerprint"] != p["fingerprint"])
check("saved contacts change invalidates pass", plan(contacts=dict(PHONES, **{"Alex Example": "353870000004"}))["fingerprint"] != p["fingerprint"])
check("invalid saved settings never create a partial armed plan", not plan(settings={"bad": object()})["eligible"])


class Harness:
    def __init__(self):
        self.now = 100.0
        self.sent, self.cancelled, self.replies = [], [], {}
        self.ready = dict(ready=True, owner="3:synthetic-doc", pending=0, busy=False)
        self.coordinator = wa_read.ReadCoordinator(self.enqueue, self.result, self.cancel,
              clock=lambda: self.now, wall_clock=lambda: NOW.timestamp() + self.now - 100)
    def enqueue(self, kind, chat, **fields):
        key = "request-%d" % len(self.sent)
        self.sent.append(dict(id=key, kind=kind, chat=chat, **fields))
        self.replies[key] = dict(id=key, status="queued")
        return key
    def result(self, key):
        return copy.deepcopy(self.replies[key])
    def cancel(self, key):
        self.cancelled.append(key)
        self.replies[key] = dict(id=key, status="cancelled")
    def tick(self, current=None, readiness=None):
        return self.coordinator.tick(p if current is None else current, self.ready if readiness is None else readiness)
    def success(self, reason="since_reached"):
        request = self.sent[-1]
        complete = reason == "since_reached"
        self.replies[request["id"]] = dict(id=request["id"], status="read", reading=dict(
            messages=3, incoming=1, outgoing=2, complete=complete, oldest=request["since"] - 1000,
            newest=request["since"] + 1000, reason=reason))

# 8 Nov review: while Lena is using WhatsApp the reader refuses to start (user_active). That must WAIT and retry the
# same guard - not skip him, not record an issue, not stop the pass.
h = Harness(); h.coordinator.start(p); h.tick()
first = h.sent[0]
h.replies[first["id"]] = dict(id=first["id"], status="user_active")
state = h.tick()
check("he is using WhatsApp: reading waits, still on, says why, no issue recorded", state["enabled"] and "using WhatsApp" in state["reason"]
      and not state["issues"] and state["read"] == 0)
h.now += 5; h.tick()
check("...nothing new is asked for during the wait", len(h.sent) == 1)
h.now += 6; h.tick()
check("...then the SAME guard is tried again", len(h.sent) == 2 and h.sent[1]["chat"] == first["chat"])
h.success(); state = h.tick()
check("...and the pass carries on normally", state["read"] == 1 and state["enabled"])
import wa_bridge as _bridge
check("the bridge accepts the new outcome from the add-on", "user_active" in _bridge.FINAL)

for outcome in ("user_active", "blocked", "unknown", "failed"):
    h = Harness(); h.coordinator.start(p); h.tick()
    first = h.sent[0]
    h.replies[first["id"]] = dict(id=first["id"], status=outcome, unread_before=3)
    state = h.tick()
    check("read interruption preserves the unread warning without counting a visit: " + outcome,
          state["read"] == 0 and len(state["unread_opened"]) == 1
          and state["unread_opened"][0]["name"] == "Alex Example" and state["unread_opened"][0]["count"] == 3)
    if outcome == "user_active":
        h.now += wa_read.USER_ACTIVE_WAIT; h.tick(); h.success(); state = h.tick()
        check("later badge-free retry cannot erase the original unread warning", state["read"] == 1
              and state["unread_opened"][0]["count"] == 3 and h.sent[1]["chat"] == first["chat"])
h = Harness(); h.coordinator.start(p); h.tick()
h.replies[h.sent[-1]["id"]] = dict(id="other-request", status="user_active", unread_before=3)
check("mismatched result cannot invent an unread warning", h.tick()["unread_opened"] == [])

h = Harness(); h.coordinator.start(p, continuous=True); h.tick()
h.success(); h.replies[h.sent[-1]["id"]]["unread_before"] = "many"; state = h.tick()
check("a malformed unread count is ignored", state["unread_opened"] == [])
h.tick(); h.success(); h.replies[h.sent[-1]["id"]]["unread_before"] = 1; state = h.tick()
check("a real one is listed", [u["count"] for u in state["unread_opened"]] == [1])
h.now += 200; h.tick()
check("...and it is still listed after the next pass begins (it does not vanish with the pass)", len(h.coordinator.status()["unread_opened"]) == 1)

import types as _types, wa_ui as _wa_ui
_fake = _types.SimpleNamespace(log=lambda: {"calls": []}, read_coordinator=_types.SimpleNamespace(status=lambda: dict(
    read=2, total=43, state="waiting", issues=[], unread_opened=[dict(name="Alex Example", count=2, at=0), dict(name="Blair", count=1, at=0)])))
_line = _wa_ui.BetaUI.read_summary(_fake)
check("the reading line says chats had unread messages and where to look", "2 chats had unread messages - read them in WhatsApp" in _line)

h = Harness(); h.tick()
check("startup never enqueues", not h.sent and h.coordinator.status()["state"] == "idle")
h.coordinator.start(p); state = h.tick()
check("explicit start queues exactly one read with no text", len(h.sent) == 1 and h.sent[0]["kind"] == "read"
      and "text" not in h.sent[0] and state["current"] == "Alex Example")
for _ in range(5): h.tick()
check("pending request never duplicates or advances target", len(h.sent) == 1)
state = h.tick(readiness=dict(h.ready, ready=False, pending=1, busy=True, reason="own search"))
check("own running search heartbeat does not cancel its read", state["enabled"] and not h.cancelled)
h.success(); state = h.tick()
check("read counts actual successful visits and messages only", state["read"] == 1 and state["incoming"] == 1
      and state["messages"] == 3 and "confirmed" not in state and len(h.sent) == 1)
state = h.tick(readiness=dict(h.ready, ready=False, reason_code="search", reason="Search is still reported"))
check("fresh post-cleanup heartbeat may arrive on a later scheduler turn", state["enabled"] and len(h.sent) == 1)
h.tick(); check("next scheduler turn advances to next verified guard", len(h.sent) == 2 and h.sent[1]["chat"] == "Blair Saved")
h.success("limit"); state = h.tick()
check("partial reads count as visits but remain visible issues", state["read"] == 2 and state["state"] == "complete"
      and not state["enabled"] and any("part" in issue["reason"] for issue in state["issues"]))
for _ in range(3): h.tick()
check("one-shot never restarts unattended", len(h.sent) == 2)
state["issues"].clear()
check("caller cannot mutate coordinator evidence", bool(h.coordinator.status()["issues"]))

for status in ("blocked", "cancelled", "unknown", "expired", "failed", "drafted"):
    h = Harness(); h.coordinator.start(p, continuous=True); h.tick()
    h.replies[h.sent[-1]["id"]]["status"] = status
    state = h.tick(); h.now += 1000; h.tick()
    check("uncertain terminal result pauses without any automatic retry: " + status,
          state["state"] == "paused" and not state["enabled"] and state["read"] == 0 and len(h.sent) == 1)
for outcome, wording in (("not_found", "not found"), ("ambiguous_chat", "none was selected"), ("group_chat", "group or broadcast")):
    h = Harness(); h.coordinator.start(p); h.tick()
    h.replies[h.sent[-1]["id"]]["status"] = outcome
    state = h.tick()
    check("contact-specific outcome is skipped with truthful review note: " + outcome,
          state["enabled"] and state["read"] == state["messages"] == state["incoming"] == 0
          and len(state["issues"]) == 1 and state["issues"][0]["name"] == "Alex Example"
          and wording in state["issues"][0]["reason"] and len(h.sent) == 1)
    h.tick()
    check("skip advances only to next exact contact on a separate tick: " + outcome,
          len(h.sent) == 2 and h.sent[-1]["chat"] == "Blair Saved")
    h.success(); state = h.tick(); h.tick()
    check("remaining contact completes without counting or retrying skipped contact: " + outcome,
          state["state"] == "complete" and state["read"] == 1 and state["total"] == 2 and len(h.sent) == 2)
for outcome, wording in (("blocked", "could not verify the request"), ("unknown", "result could not be verified"),
                         ("expired", "request timed out"), ("failed", "reader reported an error")):
    h = Harness(); h.coordinator.start(p); h.tick()
    detail = "Synthetic recovery instructions: " + "x" * 900
    h.replies[h.sent[-1]["id"]].update(status=outcome, detail=detail)
    state = h.tick()
    check("stop banner explains the outcome in plain language: " + outcome,
          wording in state["reason"] and "Synthetic recovery" not in state["reason"] and state["state"] == "paused")
    check("read details retain bounded original action guidance: " + outcome,
          state["issues"][-1]["name"] == "Alex Example" and detail[:800] in state["issues"][-1]["reason"]
          and detail[:801] not in state["issues"][-1]["reason"])
h = Harness(); h.coordinator.start(p); h.tick()
h.replies[h.sent[-1]["id"]].update(status="ambiguous_chat", detail="Two exact saved chats matched the search.")
state = h.tick()
check("skipped contact retains its diagnostic without raw status in banner", "Two exact saved chats" in state["issues"][0]["reason"]
      and "ambiguous_chat" not in state["reason"] and state["enabled"])
for readiness in (dict(ready=False, owner="3:synthetic-doc", reason="hidden"),
                  dict(ready=True, owner="changed-doc"), dict(ready=True, owner="3:synthetic-doc", pending=2)):
    h = Harness(); h.coordinator.start(p); h.tick()
    if readiness.get("reason") == "hidden":
        h.replies[h.sent[-1]["id"]]["status"] = "blocked"  # browser observes hidden and stops active work
    state = h.tick(readiness=readiness)
    check("hidden/owner/interference pauses the active pass", state["state"] == "paused" and not state["enabled"])
for changed in (dict(p, eligible=False), dict(p, fingerprint="changed")):
    h = Harness(); h.coordinator.start(p); h.tick(); state = h.tick(current=changed)
    check("scope changes revoke owned request before another callback", h.cancelled == [h.sent[0]["id"]]
          and state["state"] == "paused" and len(h.sent) == 1)
h = Harness(); h.coordinator.start(p); state = h.tick(readiness=dict(h.ready, busy=True, pending=1))
check("manual queue interference prevents starting a read", not h.sent and state["state"] == "paused")
h = Harness(); h.coordinator.start(p); state = h.tick(readiness=dict(h.ready, ready=False, reason="Show WhatsApp"))
check("unready before first action is paused with recovery guidance", not h.sent and state["reason"] == "Show WhatsApp")
h = Harness(); h.coordinator.start(p); h.tick(); h.coordinator.stop("User stopped")
check("explicit stop cancels only owned pending id", h.cancelled == [h.sent[0]["id"]] and not h.coordinator.status()["enabled"])
h.tick(); check("stop never resumes on an available heartbeat", len(h.sent) == 1)
h.coordinator.start(p); h.tick(); check("explicit restart is permitted", len(h.sent) == 2)
h = Harness(); h.coordinator.start(p); h.tick(); h.now += 125; state = h.tick()
check("lost status polling is bounded and cancels", state["state"] == "paused" and len(h.cancelled) == 1)
h = Harness(); h.coordinator.start(p); h.tick(); h.success()
h.replies[h.sent[-1]["id"]]["reading"]["incoming"] = 4
check("bad metadata never becomes a successful visit", h.tick()["read"] == 0 and not h.coordinator.status()["enabled"])
h = Harness(); h.coordinator.start(p); h.tick(); h.replies[h.sent[-1]["id"]]["id"] = "other-request"
check("wrong response identity cannot advance scan", h.tick()["state"] == "paused" and len(h.cancelled) == 1)
h = Harness(); h.coordinator.start(p); h.tick(); h.success(); h.tick(); h.now += 35
state = h.tick(readiness=dict(h.ready, ready=False, reason_code="search", reason="Clear the search"))
check("a search still in the box: reading waits (says so) and asks for nothing", state["enabled"] and "Waiting" in state["reason"]
      and len(h.sent) == 1)
h = Harness(); h.coordinator.start(p); h.tick(); h.success(); h.tick()
state = h.tick(readiness=dict(h.ready, ready=False, reason_code="hidden", reason="Show WhatsApp"))
check("WhatsApp hidden: reading waits visibly instead of stopping for good (8 Nov review)", state["enabled"]
      and "Show WhatsApp" in state["reason"] and len(h.sent) == 1)
state = h.tick()
check("...and carries on by itself when WhatsApp is back", state["enabled"] and len(h.sent) == 2)
h = Harness(); h.coordinator.start(p); h.tick(); h.success(); h.tick()
state = h.tick(readiness=dict(h.ready, ready=False, reason_code="hidden", reason="Show WhatsApp", owner="3:another-tab"))
check("...but a different WhatsApp tab still stops it", state["state"] == "paused" and len(h.sent) == 1)
h = Harness(); h.coordinator.start(p); h.tick(); h.success(); h.tick()
state = h.tick(readiness=dict(h.ready, ready=False, reason_code="old_reader", reason="Reload the add-on"))
check("...and so does an add-on that needs reloading", state["state"] == "paused")
h = Harness(); h.coordinator.start(p); h.tick(); h.success(); h.tick()
state = h.tick(readiness=dict(h.ready, ready=False, reason_code="search", busy=True, pending=1))
check("post-cleanup grace never waits behind a new manual action", state["state"] == "paused" and len(h.sent) == 1)

# Even before the first enqueue, a temporary wait belongs to the observed document.
for code in wa_read.WAIT_CODES:
    initial_wait = dict(ready=False, owner="3:synthetic-doc", reason_code=code, reason="Synthetic temporary wait", pending=0, busy=False)
    h = Harness(); h.coordinator.start(p); state = h.tick(readiness=initial_wait)
    check("initial temporary wait keeps the explicit pass enabled: " + code, state["enabled"] and not h.sent)
    state = h.tick(readiness=dict(h.ready, owner="3:replacement-document"))
    check("initial wait cannot silently resume on a replacement document: " + code,
          state["state"] == "paused" and not state["enabled"] and not h.sent)
    h = Harness(); h.coordinator.start(p); h.tick(readiness=initial_wait); state = h.tick()
    check("same original document resumes the initial temporary wait: " + code,
          state["enabled"] and len(h.sent) == 1 and h.sent[0]["chat"] == "Alex Saved")

# Exercise the real queue as the adapter, without sockets, Tk, persistence or browser.
from wa_bridge import ActionQueue, CAPABILITY
wall = [NOW.timestamp()]
queue = ActionQueue(lambda: wall[0])
client = dict(tab=3, document="synthetic-document", owner="3:synthetic-document", capability=CAPABILITY)
def heartbeat(paused=False):
    queue.touch(client, dict(at=wall[0] * 1000, pane=True, rows=2, paused=paused, useHere=False, visible="visible"))
heartbeat()
serial = wa_read.ReadCoordinator(queue.enqueue, queue.status, queue.cancel, clock=lambda: wall[0], wall_clock=lambda: wall[0])
serial.start(p); serial.tick(p, queue.readiness())
command = queue.lease(client)
for step in ("search", "open", "read"):
    queue.authorize(dict(id=command["id"], owner=client["owner"], step=step))
heartbeat(paused=True)
check("real queue permits own paused search without duplicate actions", serial.tick(p, queue.readiness())["enabled"]
      and queue.readiness()["pending"] == 1)
reading = dict(messages=1, incoming=1, outgoing=0, complete=True, oldest=command["since"], newest=command["since"], reason="since_reached")
queue.acknowledge(dict(id=command["id"], owner=client["owner"], status="read", reading=reading, unread_before=2))
check("the bridge keeps how many were unread when the reader opened the chat", queue.status(command["id"]).get("unread_before") == 2)
serial.tick(p, queue.readiness()); serial.tick(p, queue.readiness())
opened = serial.status().get("unread_opened", [])
check("...and reading lists that chat for Lena to read in WhatsApp (8 Nov review: badges were cleared silently)",
      len(opened) == 1 and opened[0]["name"] == p["targets"][0]["name"] and opened[0]["count"] == 2)
check("successful real ACK waits for own-search heartbeat recovery", serial.status()["read"] == 1 and serial.status()["enabled"]
      and queue.readiness()["pending"] == 0)
heartbeat(); serial.tick(p, queue.readiness())
command2 = queue.lease(client)
check("fresh chat-list heartbeat enables only next exact recipient", command2["chat"] == "Blair Saved" and command2["id"] != command["id"])
serial.stop("Manual action selected")
check("real coordinator stop revokes queued/running read before navigation", queue.status(command2["id"])["status"] == "cancelled"
      and queue.authorize(dict(id=command2["id"], owner=client["owner"], step="open")) is None)

h = Harness(); h.coordinator.start(p, continuous=True)
h.tick(); h.success(); h.tick(); h.tick(); h.success(); state = h.tick()
check("continuous mode watches after finite bootstrap", state["state"] == "waiting" and state["enabled"] and state["mode"] == "watching")
h.now = 219.9; h.tick(); check("no tight retry loop during cooldown", len(h.sent) == 2)
h.now = 400; state = h.tick()
check("only the still-relevant unresolved call retries after five minutes", len(h.sent) == 3 and state["read"] == 0
      and state["current"] == "Alex Example" and state["total"] == 1)
h = Harness(); h.coordinator.start(plan(contacts={}), continuous=True); h.tick()
check("all missing contacts remain off with useful issues", not h.sent and not h.coordinator.status()["enabled"] and len(h.coordinator.status()["issues"]) == 2)

# Adaptive Auto is separate from an explicit Full check. Signals are invitations to
# inspect the existing chat, never evidence of a reply and never message payloads.
confirmed = copy.deepcopy(LOG)
for call in confirmed["calls"]:
    call.update(response="Ok", heard={"how":"reply", "response":"Ok"})
cp = plan(log=confirmed)

def finish_batch(h, current, reason="since_reached"):
    for _ in range(10):
        state = h.coordinator.status()
        if state["state"] in ("waiting", "complete", "paused"):
            return state
        if not h.coordinator._pending:
            h.tick(current)
        h.success(reason)
        h.tick(current)
    raise AssertionError("Synthetic batch did not finish")

def signal(text="New message", **fields):
    return dict(dict(kind="line", chat="Alex Saved", text=text, direction="unknown", what="text",
                     shown="21:30", seen=NOW.timestamp()*1000), **fields)

signals = wa_read.activity_signals([signal()])
replayed = wa_read.activity_signals([signal(seen=NOW.timestamp()*1000+30000, read_id="other-observation", view_id="another-view")])
check("activity token ignores volatile arrival, read and view identifiers", signals["Alex Saved"][0]["token"] == replayed["Alex Saved"][0]["token"])
for fields in (dict(text="Changed reply"),dict(what="deleted"),dict(emoji="👍",what="reaction"),dict(truncated=True),dict(direction="incoming")):
    changed_signal = wa_read.activity_signals([signal(**fields)])
    check("meaningful preview change produces an investigation signal", changed_signal["Alex Saved"][0]["token"] != signals["Alex Saved"][0]["token"])
check("group and unsent draft events cannot schedule guard reads", wa_read.activity_signals([signal(group=True),signal(draft=True)]) == {})
check("activity and response state do not alter identity fingerprint", plan(log=confirmed,activity=signals)["fingerprint"] == p["fingerprint"])
changed_time = copy.deepcopy(confirmed); changed_time["calls"][0]["call"] = 1350
check("recorded reply time cannot move the stable check-call mark", plan(log=changed_time)["watch"] == cp["watch"])

h = Harness(); h.coordinator.start(cp,continuous=True); state = finish_batch(h,cp)
check("Auto bootstrap visits every verified chat once, including shared-person assignments", len(h.sent) == 2 and state["read"] == 2 and state["roster_total"] == 2)
h.now += 120; state = h.tick(cp)
check("confirmed conversations are not reopened after the old two-minute interval", len(h.sent) == 2 and state["mode"] == "watching" and state["not_needed"] == 2)
h.now += 7200; state = h.tick(cp)
check("unchanged text confirmations stay quiet hours later", len(h.sent) == 2 and state["next_due_at"] is None)
h.coordinator.start(cp,continuous=True,force_full=True); state = finish_batch(h,cp)
check("explicit Full check revisits all and returns to Auto watching", len(h.sent) == 4 and state["enabled"] and state["mode"] == "watching")
h.coordinator.stop(); h.coordinator.start(cp,continuous=True); h.tick(cp)
check("toggling Auto in the same session does not forget completed unchanged chats", len(h.sent) == 4)
h.coordinator.start(cp,continuous=False); finish_batch(h,cp)
check("manual-only Full check still visits every target and stops", len(h.sent) == 6 and h.coordinator.status()["state"] == "complete")

h = Harness(); h.coordinator.start(cp,continuous=True); finish_batch(h,cp)
active = plan(log=confirmed,activity=signals)
h.tick(active)
check("new unknown preview wakes only its exact verified chat", len(h.sent) == 3 and h.sent[-1]["chat"] == "Alex Saved" and h.coordinator.status()["total"] == 1)
finish_batch(h,active); h.tick(plan(log=confirmed,activity=replayed))
check("replayed same semantics do not reopen an already handled signal", len(h.sent) == 3)

h = Harness(); initial = plan(log=confirmed,activity=signals)
h.coordinator.start(initial,continuous=True); h.tick(initial)
changed_signals = wa_read.activity_signals([signal(),signal("Cannot come",shown="21:31",seen=NOW.timestamp()*1000+60000)])
changed_plan = plan(log=confirmed,activity=changed_signals)
h.success(); h.tick(changed_plan); finish_batch(h,changed_plan); h.tick(changed_plan)
check("activity arriving during a read remains pending for one follow-up", len(h.sent) == 3 and h.sent[-1]["chat"] == "Alex Saved")
finish_batch(h,changed_plan); h.tick(changed_plan)
check("the changed signal is consumed once after the follow-up completes", len(h.sent) == 3)

h = Harness(); h.coordinator.start(cp,continuous=True); h.tick(cp)
own_id = h.sent[-1]["id"]
own = plan(log=confirmed,activity=wa_read.activity_signals([signal(read_id=own_id,kind="bubble",id="synthetic-message",
          reactions={"complete":True,"items":[]})]))
h.success(); h.tick(own); finish_batch(h,own); h.tick(own)
check("the coordinator's own captured history cannot trigger a read loop", len(h.sent) == 2)
passive = plan(log=confirmed,activity=wa_read.activity_signals([signal(read_id="quarantined-passive",kind="bubble",id="synthetic-message")]))
h.tick(passive)
check("quarantined passive observations remain valid investigation triggers", len(h.sent) == 3)

# Live8Nov: a prior captured old bubble had an explicit empty reaction snapshot. A passive copy emitted
# while reading omitted that optional metadata, causing one false activity change and an unnecessary revisit.
old_parent = signal("All okay for Example Hall?", kind="bubble", id="old-question", direction="outgoing", ours=True,
                    date="06/11/2026",shown="16:56",read_id="prior-app-capture", reactions={"complete":True,"items":[]})
old_plan = plan(log=confirmed,activity=wa_read.activity_signals([old_parent]))
omitted = dict(old_parent,seen=old_parent["seen"]+1000,read_id="quarantined-passive")
omitted.pop("reactions")
h=Harness(); h.coordinator.start(old_plan,continuous=True); finish_batch(h,old_plan)
omitted_plan=plan(log=confirmed,activity=wa_read.activity_signals([old_parent,omitted]))
h.tick(omitted_plan)
check("passive omission of an old reaction snapshot does not manufacture a changed message",len(h.sent)==2)

positive=dict(old_parent,seen=old_parent["seen"]+2000,read_id="external-complete-capture",
              reactions={"complete":True,"items":[{"parent_id":"old-question","emoji":"👍","actor":"contact","actor_name":"Alex Saved","at":None}]})
h=Harness(); h.coordinator.start(old_plan,continuous=True); finish_batch(h,old_plan)
positive_plan=plan(log=confirmed,activity=wa_read.activity_signals([old_parent,positive]))
h.tick(positive_plan)
check("a newly explicit contact reaction still requests inspection",len(h.sent)==3 and h.sent[-1]["chat"]=="Alex Saved")
finish_batch(h,positive_plan)
removed=dict(old_parent,seen=old_parent["seen"]+3000,read_id="external-removal-capture")
removed_plan=plan(log=confirmed,activity=wa_read.activity_signals([old_parent,positive,removed]))
h.tick(removed_plan)
check("an explicit empty snapshot removes reaction activity and requests inspection",len(h.sent)==4)
finish_batch(h,removed_plan)
same_removed=dict(removed,seen=old_parent["seen"]+4000,read_id="external-repeated-capture")
h.tick(plan(log=confirmed,activity=wa_read.activity_signals([old_parent,positive,removed,same_removed])))
check("a repeated unchanged explicit removal is consumed once",len(h.sent)==4)

h=Harness(); h.coordinator.start(positive_plan,continuous=True); finish_batch(h,positive_plan)
after_positive=dict(omitted,seen=old_parent["seen"]+3000)
h.tick(plan(log=confirmed,activity=wa_read.activity_signals([old_parent,positive,after_positive])))
check("missing reaction metadata does not pretend a known positive reaction disappeared",len(h.sent)==2)
for change in ({"text":"Different question"},{"direction":"incoming","ours":False}):
    h=Harness(); h.coordinator.start(old_plan,continuous=True); finish_batch(h,old_plan)
    real_change=dict(omitted,**change)
    h.tick(plan(log=confirmed,activity=wa_read.activity_signals([old_parent,real_change])))
    check("real bubble content or direction change still requests inspection",len(h.sent)==3)

h = Harness(); a = signal("State A")
ap = plan(log=confirmed,activity=wa_read.activity_signals([a]))
h.coordinator.start(ap,continuous=True); finish_batch(h,ap)
b = signal("State B",seen=a["seen"]+1000)
bp = plan(log=confirmed,activity=wa_read.activity_signals([a,b]))
h.tick(bp); finish_batch(h,bp)
again = signal("State A",seen=a["seen"]+2000)
again_plan = plan(log=confirmed,activity=wa_read.activity_signals([a,b,again]))
h.tick(again_plan)
check("a preview returning A to B to A is a fresh state transition", len(h.sent) == 4)
finish_batch(h,again_plan)
replay_a = signal("State A",seen=a["seen"]+3000,view_id="new-view")
h.tick(plan(log=confirmed,activity=wa_read.activity_signals([a,b,again,replay_a])))
check("a newly observed copy of unchanged final A is not another transition", len(h.sent) == 4)
h.tick(plan(log=confirmed,activity=wa_read.activity_signals([replay_a])))
check("aging old observations out of the input cannot manufacture a transition", len(h.sent) == 4)

h = Harness(); h.coordinator.start(ap,continuous=True); h.tick(ap)
own_id = h.sent[-1]["id"]
large_capture = [signal(kind="bubble",id="owned-%d"%i,read_id=own_id,seen=a["seen"]+2000+i) for i in range(1200)]
flood = plan(log=confirmed,activity=wa_read.activity_signals([a,b]+large_capture))
h.success(); h.tick(flood); finish_batch(h,flood); h.tick(flood)
check("a large owned capture cannot evict a changed external preview before filtering", len(h.sent) == 3 and h.sent[-1]["chat"] == "Alex Saved")
finish_batch(h,flood); h.tick(flood)
check("large own-capture replay stays quiet after the external change is handled", len(h.sent) == 3)

future = copy.deepcopy(confirmed)
future["calls"][1].update(start=1440,shift="00:00-close",heard={},response="")
fp = plan(log=future)
h = Harness(); h.coordinator.start(fp,continuous=True); finish_batch(h,fp)
h.now += 300; h.tick(fp)
check("shared guard's later unresolved assignment waits for its own mark", len(h.sent) == 2)
h.now = 100 + 90*60; h.tick(fp)
check("later unresolved assignment schedules that shared chat exactly once at its mark", len(h.sent) == 3 and h.sent[-1]["chat"] == "Alex Saved")
early = copy.deepcopy(future); early["calls"][1].update(heard={"how":"reply","response":"Ok"},response="Ok")
ep = plan(log=early)
h = Harness(); h.coordinator.start(ep,continuous=True); finish_batch(h,ep); h.now += 90*60; h.tick(ep)
check("an early confirmed later assignment is left alone when its mark arrives", len(h.sent) == 2)

h = Harness(); h.coordinator.start(fp,continuous=True); finish_batch(h,fp)
h.now += 60
early_signal = wa_read.activity_signals([signal("Yes, all good",kind="bubble",id="early-future-reply",
        direction="incoming",read_id="quarantined-early-passive",shown="21:31",seen=NOW.timestamp()*1000+60000)])
future_activity = plan(log=future,activity=early_signal)
h.tick(future_activity)
check("new passive incoming activity on a future assignment is read before its scheduled mark",
      len(h.sent) == 3 and h.sent[-1]["chat"] == "Alex Saved"
      and h.coordinator.status()["mode"] == "updates"
      and h.coordinator.wall_clock() < next(w["due"] for w in future_activity["watch"]["Alex Saved"]["windows"] if not w["resolved"]))
early_confirmed = plan(log=early,activity=early_signal)
finish_batch(h,early_confirmed); h.now = 100+90*60; h.tick(early_confirmed)
check("the verified early reply stays quiet at its future mark after that immediate read", len(h.sent) == 3)

waiting = copy.deepcopy(future)
waiting["calls"][1].update(start=1320,shift="22:00-close")
wp = plan(log=waiting)
h = Harness(); h.coordinator.start(wp,continuous=True); finish_batch(h,wp)
for delay in (300,600,1200):
    before = len(h.sent); h.now += delay - 1; h.tick(wp)
    check("unresolved backoff does not retry early", len(h.sent) == before)
    h.now += 1; h.tick(wp)
    check("unresolved backoff retries only its relevant chat", len(h.sent) == before+1 and h.sent[-1]["chat"] == "Alex Saved")
    finish_batch(h,wp)
h.now = 100 + 121*60; state = h.tick(wp)
check("unresolved retries end after start plus ninety minutes and retain attention", len(h.sent) == 5 and state["attention"] == 1)
h.tick(plan(log=waiting,activity=signals))
check("new activity can request inspection after automatic retries have ended", len(h.sent) == 6)

reaction = copy.deepcopy(confirmed); reaction["calls"][1]["heard"].update(how="reaction",attached_reaction=True)
rp = plan(log=reaction)
h = Harness(); h.coordinator.start(rp,continuous=True); finish_batch(h,rp)
h.now += 899; h.tick(rp); check("attached-reaction confirmation is not revisited every two minutes", len(h.sent) == 2)
h.now += 1; h.tick(rp); check("attached-reaction freshness has a bounded fifteen-minute safeguard", len(h.sent) == 3)
finish_batch(h,rp); h.now = 100+121*60; h.tick(rp)
check("reaction freshness stops after its takeback window", len(h.sent) == 3)

h = Harness(); h.coordinator.start(cp,continuous=True); finish_batch(h,cp)
checked = h.coordinator.status()["coverage"][0]["last_checked_at"]
h.tick(plan(log=confirmed,activity=signals)); h.success("limit"); state = h.tick(plan(log=confirmed,activity=signals))
cover = state["coverage"][0]
check("partial revisit never overwrites the prior complete-check timestamp", cover["last_checked_at"] == checked and cover["partial"])
check("a partial revisit remains an explicit issue, never a fabricated no-reply or completion", any("part" in item["reason"] for item in state["issues"]))
h.coordinator.stop(); h.coordinator.start(plan(log=confirmed,activity=signals),continuous=True)
state = h.tick(plan(log=confirmed,activity=signals))
check("toggling Auto preserves unresolved partial history and its explanation", state["coverage"][0]["partial"]
      and state["coverage"][0]["last_checked_at"] == checked and any("part" in item["reason"] for item in state["issues"]))

# Persisted automatic provenance is quarantined until a complete locally committed
# history exists. These fixtures reproduce a tail-only Yes to an unrelated offer.
import whatsapp as wa
READ_A, READ_B = "a" * 32, "b" * 32
seen = int(NOW.timestamp() * 1000)
since = p["targets"][0]["since"]
def bubble(text, shown, direction="incoming", read_id=READ_A, message_id="synthetic-yes"):
    value = dict(kind="bubble", chat="Alex Example", text=text, shown=shown, date="07/11/2026", seen=seen,
                 what="text", group=False, direction=direction, ours=direction == "outgoing", id=message_id)
    if read_id is not None:
        value["read_id"] = read_id
    if direction == "unknown":
        value.pop("ours")
    return value
single = copy.deepcopy(LOG); single["calls"] = single["calls"][:1]
def heard(events, commits=None, log=single):
    return next(iter(wa.heard(log, events, taught={}, trusted=set(), read_commits=commits).values()), {})
def receipt(read_id=READ_B):
    return dict(id=read_id, chat="Alex Example", since=since, created=seen, completed=seen + 1000)
def commits(read_id=READ_B):
    value = receipt(read_id); value.pop("id")
    return {read_id: value}
tail = bubble("Yes", "19:01")
offer = bubble("Could you cover tomorrow?", "19:00", "outgoing", message_id="synthetic-offer")
check("legacy untagged passive rule is preserved", heard([bubble("Yes", "19:01", read_id=None)])["response"] == "Ok")
check("new automatic tail-only Yes stays review-only before history arrives", heard([tail]).get("read_incomplete")
      and heard([tail]).get("response") != "Ok")
check("loading unrelated earlier context never creates a transient positive", heard([offer, tail]).get("response") != "Ok")
check("fully committed unrelated offer and Yes still do not confirm", heard([offer, tail], commits(READ_A)).get("response") != "Ok")
question = bubble("All okay for Hall?", "19:00", "outgoing", READ_B, "synthetic-question")
answer = bubble("Yes", "19:01", read_id=READ_B)
check("actual check-call question and reply confirm only after full commit", heard([question, answer], commits())["response"] == "Ok")
check("full committed copies resolve earlier partial copies of the same bubble", heard([tail, question, answer], commits())["response"] == "Ok")
for status in ("partial", "cancelled", "reloaded", "zero-event failure"):
    check("no receipt after " + status + " cannot release future passive tagged Yes", heard([tail], {}).get("read_incomplete"))
for altered in ({}, {READ_A: dict(commits(READ_A)[READ_A], chat="Other Person")},
                {READ_A: dict(commits(READ_A)[READ_A], since=seen + 2000)},
                {READ_A: dict(commits(READ_A)[READ_A], completed=True)}):
    check("missing/malformed/cross-recipient receipt cannot prove context", heard([tail], altered).get("response") != "Ok")
future = dict(tail, seen=seen + 2000)
check("future passive observation cannot borrow completed coverage even in same message minute", heard([future], commits(READ_A)).get("read_incomplete"))
captured_history = bubble("Earlier context", "02:00", read_id=READ_B, message_id="early-context")
between_capture_and_ack = dict(tail, seen=seen + 500)   # earlier than ACK completion, but separate worker-owned passiveId
check("passive event between capture and ACK cannot borrow snapshot receipt", heard([captured_history, between_capture_and_ack], commits()).get("read_incomplete")
      and not wa._read_event_committed(between_capture_and_ack, commits()))
late = bubble("Yes", "21:31"); late["seen"] = seen
check("future message clock cannot borrow an old completion receipt", not wa._read_event_committed(late, commits(READ_A)))
check("routine partial duplicate of already-approved reply preserves confirmation", heard([question, answer, tail], commits())["response"] == "Ok")
uncertain_copy = bubble("Yes", "19:01", "unknown")
check("complete authoritative bubble resolves earlier unknown-direction copy of same ID", heard([question, answer, uncertain_copy], commits())["response"] == "Ok")
preview_copy = dict(uncertain_copy, kind="line"); preview_copy.pop("id")
check("routine unknown sidebar copy of an approved bubble does not revoke it", heard([question, answer, preview_copy], commits())["response"] == "Ok")
outgoing_copy = bubble("Yes", "19:01", "outgoing", READ_B)
check("complete authoritative outgoing bubble resolves mistaken partial direction of same ID", not heard([question, outgoing_copy, tail], commits()).get("read_incomplete"))
for direction, text in (("incoming", "I cannot come"), ("unknown", "A new message"), ("incoming", "Yes")):
    newer = bubble(text, "19:02", direction, message_id="newer-message")
    outcome = heard([question, answer, newer], commits())
    check("new uncommitted later message removes automatic confidence: " + direction + text,
          outcome.get("read_incomplete") and outcome.get("taken_back") and outcome.get("response") == "")
    check("incomplete review copy does not claim guard cancellation", "taken back" not in wa.story(dict(name="Alex Example", heard=outcome)))
earlier = bubble("Unrelated earlier context", "18:59", message_id="earlier-message")
check("older partial context cannot revoke a later approved reply", heard([question, answer, earlier], commits())["response"] == "Ok")
repeat = dict(tail, id="distinct-same-minute-message")
check("distinct same-minute partial message is not silently deduplicated", heard([question, answer, repeat], commits()).get("read_incomplete"))
late_problem = bubble("I cannot make it", "21:45", message_id="late-problem")
late_problem["seen"] = seen + 3600000
check("partial later concern uses the existing three-hour withdrawal window", heard([question, answer, late_problem], commits()).get("taken_back"))
new_topic = bubble("Could you cover tomorrow?", "19:03", "outgoing", READ_B, "new-topic")
later_answer = bubble("No", "19:04", message_id="later-answer")
check("approved unrelated outgoing context closes partial review window", heard([question, answer, new_topic, later_answer], commits())["response"] == "Ok")
check("quarantine has explicit cell and hover guidance", wa.cell_hint(dict(heard=heard([tail]), call_text="19:00")) == ("Recheck chat", "amber")
      and "conversation history" in wa.story(dict(name="Alex Example", heard=heard([tail]))))
check("client completion claims are discarded by event cleaner", "read_complete" not in wa._clean(dict(tail, read_complete=True))
      and heard([wa._clean(dict(tail, read_complete=True))]).get("read_incomplete"))
for invalid_id in (None, 3, "A" * 32, "a" * 31, "bad/receipt"):
    check("invalid provenance cannot be stripped into ordinary evidence", wa._clean(dict(tail, read_id=invalid_id)) is None)

old_logs = wa.LOGS


class _ClockAtNow:
    """whatsapp's `time`, but with time() at this file's NOW: receipts are pruned a week after the clock, and these
    receipts are stamped at NOW (7 Nov) - on the real clock they all went stale on 14 Nov and the checks failed."""
    def __getattr__(self, name):
        return getattr(_real_time, name)

    def time(self):
        return NOW.timestamp()


import time as _real_time
try:
    with tempfile.TemporaryDirectory(prefix="ccl-read-receipts-") as folder, patch.object(wa, "time", _ClockAtNow()):
        wa.LOGS = str(Path(folder) / "events")
        path = Path(wa.read_coverage_file())
        check("coverage path follows isolated event folder", path.parent == Path(folder))
        check("missing receipts load failclosed without creating files", wa.load_read_commits() == {} and not path.exists())
        check("valid completion is atomically persisted without message/phone fields", wa.commit_read_coverage(receipt())
              and set(wa.load_read_commits()[READ_B]) == {"chat", "since", "created", "completed"})
        check("receipt survives independent file reload", heard([question, answer], wa.load_read_commits())["response"] == "Ok")
        saved = path.read_bytes()
        with patch.object(wa.os, "replace", side_effect=OSError("synthetic full disk")):
            try:
                wa.commit_read_coverage(receipt(READ_A))
            except OSError:
                failed = True
            else:
                failed = False
        check("failed atomic replacement preserves previous receipts", failed and path.read_bytes() == saved and READ_A not in wa.load_read_commits())
        # 8 Nov review: Windows refuses the replace while someone reads the file for a moment; a good read was lost.
        real_replace, busy = wa.os.replace, [2]
        def locked_twice(src, dst):
            if busy[0]:
                busy[0] -= 1
                raise PermissionError(13, "synthetic: file in use")
            return real_replace(src, dst)
        with patch.object(wa.os, "replace", side_effect=locked_twice), patch.object(wa.time, "sleep", lambda s: None):
            ok = wa.commit_read_coverage(receipt(READ_A))
        check("a file briefly in use: the save waits and succeeds", ok and READ_A in wa.load_read_commits())
        saved = path.read_bytes()
        with patch.object(wa.os, "replace", side_effect=PermissionError(13, "synthetic: always in use")), \
                patch.object(wa.time, "sleep", lambda s: None):
            try:
                wa.commit_read_coverage(dict(receipt(READ_A), id="9" * 32))
            except PermissionError:
                failed = True
            else:
                failed = False
        check("...a file that stays in use still fails honestly and keeps the old file", failed and path.read_bytes() == saved)
        old = dict(chat="Old Example", since=1, created=2, completed=int((wa.time.time() - 10 * 86400) * 1000))
        data = json.loads(path.read_text(encoding="utf-8")); data["8" * 32] = old
        path.write_text(json.dumps(data), encoding="utf-8")
        wa.commit_read_coverage(dict(receipt(READ_A), id="7" * 32))
        kept = wa.load_read_commits()
        check("receipts older than a week are dropped on the next save; fresh ones stay",
              "8" * 32 not in kept and "7" * 32 in kept and READ_B in kept)
        wa.record([tail, answer])
        stored = wa.events_between(NOW - dt.timedelta(hours=1), NOW + dt.timedelta(minutes=1))
        check("event dedup retains new complete batch beside older partial batch", len(stored) == 2 and {e["read_id"] for e in stored} == {READ_A, READ_B})
        update_log = copy.deepcopy(single); update_log["stem"] = "synthetic-coverage-update"
        wa.record([question])
        with patch.object(wa, "_edits_dir", return_value=str(Path(folder) / "edits")), \
                patch.object(wa, "is_for", return_value=True), patch.object(wa, "live", return_value=True), \
                patch.object(wa, "load_aliases", return_value={}), patch.object(wa, "trusted_names", return_value=set()):
            wa._last.pop(update_log["stem"], None)
            wa.update(update_log, NOW)
            overlay = wa.load_heard(update_log["stem"])
            check("production update consumes persisted receipt for full committed question and reply", any(isinstance(v, dict) and v.get("response") == "Ok" for v in overlay.values()))
            path.unlink()
            wa.update(update_log, NOW)
            overlay = wa.load_heard(update_log["stem"])
            check("receipt deletion invalidates cached overlay even without new message rows", not any(isinstance(v, dict) and v.get("response") == "Ok" for v in overlay.values())
                  and any(isinstance(v, dict) and v.get("read_incomplete") for v in overlay.values()))
            wa._last.pop(update_log["stem"], None)
        path.write_text('{"broken":', encoding="utf-8")
        broken = path.read_bytes()
        try:
            wa.commit_read_coverage(receipt(READ_A))
        except ValueError:
            failed = True
        else:
            failed = False
        check("malformed receipt file is preserved and cannot release evidence", failed and path.read_bytes() == broken and wa.load_read_commits() == {})
finally:
    wa.LOGS = old_logs

print("%s of %s checks passed" % (sum(checks), len(checks)))
sys.exit(0 if all(checks) else 1)
