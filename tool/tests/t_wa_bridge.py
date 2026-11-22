"""Pure command/receiver contract tests with synthetic contacts and isolated temporary files. No UI/network."""
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wa_bridge as bridge
import whatsapp as wa

checks = []
def check(label, value):
    checks.append(bool(value))
    if not value:
        print("FAIL", label)

def refused(label, fn):
    try:
        fn()
    except ValueError:
        check(label, True)
    else:
        check(label, False)

now = [1000.0]
def queue():
    return bridge.ActionQueue(lambda: now[0])

client = {"tab": 3, "document": "synthetic-document-1", "owner": "3:synthetic-document-1", "capability": bridge.CAPABILITY}
def beat(**values):
    return dict(dict(at=now[0] * 1000, pane=True, rows=2, paused=False, useHere=False, visible="visible"), **values)

q = queue()
check("no client is not ready", not q.readiness()["ready"])
refused("offline enqueue rejected", lambda: q.enqueue("open", "Alex Example"))
check("fresh exact capable client accepted", q.touch(client, beat()) and q.readiness()["ready"])
for invalid in [None, {}, dict(client, capability="drafts-v1"), dict(client, tab=True), dict(client, owner="wrong"), dict(client, document="bad/path")]:
    check("malformed capability rejected", not q.touch(invalid, beat()) and not q.readiness()["ready"])
    q.touch(client, beat())
for fields in [dict(at=(now[0] - 90) * 1000), dict(at=(now[0] + 6) * 1000), dict(at=float("nan")), dict(at="1000000"), dict(pane=1), dict(rows=True), dict(rows=0), dict(rows="2"), dict(paused=0), dict(useHere=None)]:
    q.touch(client, beat(**fields))
    check("stale malformed or unusable beat not ready", not q.readiness()["ready"])
    q.touch(client, beat())
for visible in (None, "", "prerender", True, "hidden"):
    incomplete = queue(); incomplete.touch(client, beat())
    key = incomplete.enqueue("draft", "Alex", text="Synthetic draft")
    incomplete.lease(client)
    incomplete.authorize(dict(id=key, owner=client["owner"], step="open"))
    incomplete.touch(client, beat(visible=visible))
    check("only explicitly visible WhatsApp can be ready", not incomplete.readiness()["ready"])
    check("hidden or invalid visibility cannot authorize a pending insertion",
          incomplete.authorize(dict(id=key, owner=client["owner"], step="draft")) is None)
missing_visibility = beat(); missing_visibility.pop("visible")
q.touch(client, missing_visibility)
check("missing visibility is incomplete rather than ready", not q.readiness()["ready"])
q.touch(client, beat())
q.touch(client, beat(at=(now[0] - 80) * 1000)); now[0] += 11
check("replayed old beat cannot extend capability", not q.readiness()["ready"])
q.touch(client, beat()); now[0] -= 1
check("clock reversal does not arm action", not q.readiness()["ready"])
now[0] += 1; q.touch(client, beat())
for args in [("send", "Alex"), ("draft", "Alex"), ("open", "Alex", "text"), ("open", ""), ("open", "a\nb"), ("draft", "Alex", "\0")]:
    refused("invalid shape refused", lambda args=args: q.enqueue(*args))
for fields in [dict(ttl=0), dict(ttl=121), dict(ttl=True), dict(ttl=float("nan")), dict(phone="+353870000001"), dict(phone=353870000001)]:
    refused("invalid lifetime or phone refused", lambda fields=fields: q.enqueue("open", "Alex", **fields))
for value in ("👍" * 2049, "\ud800", "\udc00"):
    refused("UTF-16 overflow or unpaired surrogate refused", lambda value=value: q.enqueue("draft", "Alex", value))
refused("chat name UTF-16 limit matches browser", lambda: q.enqueue("open", "👍" * 61))

key = q.enqueue("draft", "Alex Example", "All okay for Example Hall tonight? 👍", phone="353870000001")
check("queue exposes status but not private draft", q.status(key)["status"] == "queued" and "text" not in q.status(key))
check("wrong owner cannot lease", q.lease(dict(client, document="synthetic-document-2", owner="3:synthetic-document-2")) is None)
leased = q.lease(client)
check("lease preserves exact draft", leased["text"].endswith("👍") and leased["phone"] == "353870000001")
check("lease cannot replay", q.lease(client) is None and q.status(key)["status"] == "running")
def grant(q, key, step):
    return q.authorize({"id": key, "owner": client["owner"], "step": step})
check("draft cannot be authorized before opening", grant(q, key, "draft") is None)
check("profile cannot be authorized before opening", grant(q, key, "profile") is None)
check("search typing needs its own short grant", grant(q, key, "search")["expires"] == int((now[0] + 2) * 1000))
check("search authorization is never reissued", grant(q, key, "search") is None)
check("open grant is short-lived and exact", grant(q, key, "open") == {"ok": True, "id": key, "owner": client["owner"], "step": "open", "expires": int((now[0] + 2) * 1000)})
check("open authorization is never reissued", grant(q, key, "open") is None)
check("profile opening has its own short one-use grant", grant(q, key, "profile")["expires"] == int((now[0] + 2) * 1000) and grant(q, key, "profile") is None)
check("search cannot be authorized after opening", grant(q, key, "search") is None)
check("final draft authorization granted once", grant(q, key, "draft") is not None and grant(q, key, "draft") is None)
for value in [None, [], {"id": []}, {"id": key, "status": []}, {"id": key, "owner": "wrong", "status": "drafted"}, {"id": key, "owner": client["owner"], "status": "sent"}]:
    check("malformed or wrong ACK rejected", not q.acknowledge(value))
ack = {"id": key, "owner": client["owner"], "status": "drafted", "detail": "Unsent draft verified."}
check("verified ACK records success", q.acknowledge(ack) and q.status(key)["status"] == "drafted")
check("ACK retry is idempotent", q.acknowledge(ack) and q.lease(client) is None)
check("terminal outcome cannot change", not q.acknowledge(dict(ack, status="opened")))
# 8 Nov: sending ONE reviewed message, because Lena pressed Send for that guard
try:
    q.enqueue("send", "Alex Example", "All okay for Example Hall tonight?")
    no_phone = False
except ValueError:
    no_phone = True
check("send needs the guard's verified phone", no_phone)
skey = q.enqueue("send", "Alex Example", "All okay for Example Hall tonight?", phone="353870000001"); q.lease(client)
check("send cannot be allowed before the chat is opened", grant(q, skey, "send") is None)
grant(q, skey, "open")
check("typing for a send has its own one-use grant", grant(q, skey, "draft") is not None and grant(q, skey, "draft") is None)
check("send is allowed once, after opening", grant(q, skey, "send") is not None and grant(q, skey, "send") is None)
check("'sent' is accepted for that send", q.acknowledge({"id": skey, "owner": client["owner"], "status": "sent"}) and q.status(skey)["status"] == "sent")
dkey = q.enqueue("draft", "Alex Example", "All okay for Example Hall tonight?", phone="353870000001"); q.lease(client); grant(q, dkey, "open")
check("a draft request can never be allowed to send", grant(q, dkey, "send") is None)
check("...nor report 'sent'", not q.acknowledge({"id": dkey, "owner": client["owner"], "status": "sent"}))
q.cancel(dkey)
ukey = q.enqueue("send", "Alex Example", "All okay for Example Hall tonight?", phone="353870000001"); q.lease(client)
grant(q, ukey, "open"); grant(q, ukey, "send")
stopped = q.cancel(ukey)
check("stopping after Send was allowed says it may have been sent - never 'nothing happened'",
      stopped["status"] == "unknown" and "may already have been sent" in stopped["detail"])
key = q.enqueue("open", "Alex", ttl=5); now[0] += 6
check("queued expiration does not execute", q.lease(client) is None and q.status(key)["status"] == "expired")
q.touch(client, beat()); key = q.enqueue("open", "Alex"); q.lease(client); now[0] += 26
check("lost ACK becomes unknown and is never retried", q.status(key)["status"] == "unknown" and q.lease(client) is None)
check("late ACK cannot turn uncertain result into assumed success", not q.acknowledge(dict(ack, id=key)))
q.touch(client, beat()); running = q.enqueue("open", "Alex"); q.lease(client); queued = q.enqueue("open", "Blair")
replacement = dict(client, document="synthetic-document-2", owner="3:synthetic-document-2")
q.touch(replacement, beat())
check("document replacement never replays running request", q.status(running)["status"] == "unknown")
check("old document cannot authorize profile opening", grant(q, running, "profile") is None)
check("document replacement blocks queued request", q.status(queued)["status"] == "blocked" and q.lease(replacement) is None)
for i in range(8): q.enqueue("open", "Example " + str(i))
refused("bounded queue", lambda: q.enqueue("open", "One more"))
check("malformed status lookup is safe", q.status([])["status"] == "unknown")

q = queue(); q.touch(client, beat())
key = q.enqueue("draft", "Alex", "Reviewed draft")
check("queued cancellation is immediate and prevents leasing", q.cancel(key)["status"] == "cancelled" and q.lease(client) is None)
check("cancelled queued action cannot obtain permission", grant(q, key, "open") is None)
key = q.enqueue("draft", "Alex", "Reviewed draft"); q.lease(client)
check("ungranted running cancellation prevents all mutations", q.cancel(key)["status"] == "cancelled" and grant(q, key, "open") is None)
key = q.enqueue("draft", "Alex", "Reviewed draft"); q.lease(client); grant(q, key, "search")
check("cancellation after search revokes opening and insertion", q.cancel(key)["status"] == "cancelled" and grant(q, key, "open") is None and grant(q, key, "draft") is None)
key = q.enqueue("draft", "Alex", "Reviewed draft"); q.lease(client); grant(q, key, "open")
check("cancellation after opening still prevents insertion", q.cancel(key)["status"] == "cancelled" and grant(q, key, "draft") is None)
check("cancellation after opening also prevents profile changes", grant(q, key, "profile") is None)
key = q.enqueue("draft", "Alex", "Reviewed draft"); q.lease(client); grant(q, key, "open"); grant(q, key, "draft")
check("late profile opening cannot follow final draft authorization", grant(q, key, "profile") is None)
check("cancellation after final grant is honestly uncertain", q.cancel(key)["status"] == "unknown" and grant(q, key, "draft") is None)
check("late drafted ACK cannot replace cancellation uncertainty", not q.acknowledge({"id": key, "owner": client["owner"], "status": "drafted"}))
key = q.enqueue("open", "Alex"); q.lease(client); grant(q, key, "open")
check("cancelling already-authorized opening is uncertain", q.cancel(key)["status"] == "unknown")
key = q.enqueue("draft", "Alex", "Reviewed draft"); q.lease(client)
check("success cannot be claimed without relevant grant", not q.acknowledge({"id": key, "owner": client["owner"], "status": "drafted"}))
for value in [None, {"id": []}, {"id": key, "owner": "wrong", "step": "open"}, {"id": key, "owner": client["owner"], "step": "send"}]:
    check("invalid authorization denied", q.authorize(value) is None)
q.touch(client, beat(paused=True))
check("own exact chat search can finish while ordinary readiness is paused", not q.readiness()["ready"] and grant(q, key, "open") is not None)
q.touch(client, beat(useHere=True))
check("disconnected WhatsApp cannot authorize typing", grant(q, key, "draft") is None)
q.touch(client, beat())
check("new app queue cannot authorize previous instance request", queue().authorize({"id": key, "owner": client["owner"], "step": "draft"}) is None)
key2 = q.enqueue("open", "Blair")
check("app close cancels both queued and ungranted running operations", {item["status"] for item in q.cancel_all()} == {"cancelled"} and q.lease(client) is None)
check("bulk cancellation is idempotent", q.cancel_all() == [])
key = q.enqueue("open", "Alex", ttl=5); q.lease(client); now[0] += 6
check("expired operation cannot get authorization", grant(q, key, "open") is None)

# The final Send grant is a terminal mutation boundary, including an already prepared composer.
for step in ("search", "open", "profile", "draft", "read", "send"):
    final_send = queue(); final_send.touch(client, beat())
    send_key = final_send.enqueue("send", "Synthetic Person", "Reviewed text", phone="353870000001")
    final_send.lease(client); grant(final_send, send_key, "open"); grant(final_send, send_key, "send")
    check("no mutation grant follows Send authorization: " + step, grant(final_send, send_key, step) is None)
    check("denied late mutation does not prevent truthful sent ACK", final_send.acknowledge(dict(id=send_key, owner=client["owner"], status="sent")))
for steps, phrase in ((("open",), "Send was not allowed"),
                      (("open", "draft"), "reviewed text may be in the chat's message box"),
                      (("open", "send"), "may already have been sent")):
    for interruption in ("timeout", "new document"):
        interrupted = queue(); interrupted.touch(client, beat())
        send_key = interrupted.enqueue("send", "Synthetic Person", "Reviewed text", phone="353870000001")
        interrupted.lease(client)
        for step in steps: grant(interrupted, send_key, step)
        original_clock = now[0]
        if interruption == "timeout":
            now[0] += 26
        else:
            interrupted.touch(dict(client, document="replacement-document", owner="3:replacement-document"), beat())
        outcome = interrupted.status(send_key)
        check("interrupted send reports its actual permitted stage: " + interruption + repr(steps),
              outcome["status"] == "unknown" and phrase in outcome["detail"])
        check("interrupted send cannot acquire new grants or become a late success", grant(interrupted, send_key, "send") is None
              and not interrupted.acknowledge(dict(id=send_key, owner=client["owner"], status="sent")))
        now[0] = original_clock

# A request accepted on the previous heartbeat must not silently wait behind a
# newly observed search/filter, missing chat list, hidden tab or Use here screen.
for fields, hint in ((dict(paused=True), "search"), (dict(useHere=True), "Use here"),
                     (dict(rows=0), "chat list"), (dict(visible="hidden"), "on screen")):
    pending = queue(); pending.touch(client, beat())
    blocked_key = pending.enqueue("draft", "Synthetic Person", "Reviewed text")
    pending.touch(client, beat(**fields))
    state, outcome = pending.readiness(), pending.status(blocked_key)
    check("unavailable heartbeat explains its actual recovery step: " + hint,
          not state["ready"] and hint in state["reason"] and "reload" not in state["reason"].lower())
    check("undelivered request stops immediately with truthful no-action detail: " + hint,
          outcome["status"] == "blocked" and state["reason"] in outcome["detail"]
          and "had not started" in outcome["detail"] and pending.lease(client) is None)
    refused("new enqueue also refuses unavailable tab: " + hint,
            lambda: pending.enqueue("open", "Synthetic Person"))
    pending.touch(client, beat())
    check("recovery never replays a previously blocked request: " + hint,
          pending.status(blocked_key)["status"] == "blocked" and pending.lease(client) is None)
    retry_key = pending.enqueue("open", "Synthetic Person")
    check("explicit retry works after a fresh usable report: " + hint, pending.lease(client)["id"] == retry_key)

stale = queue(); stale.touch(client, beat(at=(now[0] - 80) * 1000))
stale_key = stale.enqueue("open", "Synthetic Person", ttl=120)
now[0] += 11
check("status polling settles queued work when the heartbeat becomes stale",
      stale.status(stale_key)["status"] == "blocked" and "recent update" in stale.readiness()["reason"])
stale.touch(client, beat())
check("a later fresh heartbeat cannot revive the stale queued intent", stale.lease(client) is None)
stale.touch(dict(client, capability="drafts-v1"), beat())
check("an old reader gets reload guidance rather than a search instruction",
      not stale.readiness()["ready"] and "Reload" in stale.readiness()["reason"])

own_search = queue(); own_search.touch(client, beat())
running_key = own_search.enqueue("draft", "Synthetic Person", "Reviewed text")
own_search.lease(client); grant(own_search, running_key, "search")
waiting_key = own_search.enqueue("open", "Other Synthetic Person")
own_search.touch(client, beat(paused=True))
check("own search only stops never-started work and preserves the running action",
      own_search.status(waiting_key)["status"] == "blocked"
      and own_search.status(running_key)["status"] == "running"
      and grant(own_search, running_key, "open") is not None
      and grant(own_search, running_key, "draft") is not None)
detail = "Verified result. " + "Recovery guidance. " * 70
check("verified result keeps bounded cleanup guidance beyond the old 240-character cap",
      own_search.acknowledge({"id": running_key, "owner": client["owner"], "status": "drafted", "detail": detail})
      and own_search.status(running_key)["detail"] == detail[:800])

for steps, expected, hint in (((), "cancelled", "before WhatsApp was touched"),
                              (("search",), "cancelled", "search box"),
                              (("open",), "cancelled", "chat was allowed to open"),
                              (("open", "draft"), "unknown", "check WhatsApp")):
    cancelled = queue(); cancelled.touch(client, beat())
    cancel_key = cancelled.enqueue("draft", "Synthetic Person", "Reviewed text")
    cancelled.lease(client)
    for step in steps:
        grant(cancelled, cancel_key, step)
    outcome = cancelled.cancel(cancel_key)
    check("cancellation describes only the possible effects of granted steps: " + repr(steps),
          outcome["status"] == expected and hint in outcome["detail"]
          and grant(cancelled, cancel_key, "draft") is None)
never_started = queue(); never_started.touch(client, beat())
cancel_key = never_started.enqueue("open", "Synthetic Person")
check("cancelling an unleased request does not claim a possibly opened chat",
      "Nothing in WhatsApp was changed" in never_started.cancel(cancel_key)["detail"])

# Opening an interrupted read may already clear the unread badge. Keep that fact
# only for read requests whose opening was authorized, never from an unrelated ACK.
for final_status in ("user_active", "blocked", "unknown", "failed"):
    interrupted_read = queue(); interrupted_read.touch(client, beat())
    read_key = interrupted_read.enqueue("read", "Synthetic Person", phone="353870000001", since=int(now[0] * 1000) - 60000)
    interrupted_read.lease(client); grant(interrupted_read, read_key, "open")
    result = dict(id=read_key, owner=client["owner"], status=final_status, unread_before=3)
    check("interrupted read retains its observed unread count: " + final_status,
          interrupted_read.acknowledge(result) and interrupted_read.status(read_key).get("unread_before") == 3)
    check("interrupted unread ACK remains idempotent", interrupted_read.acknowledge(result)
          and interrupted_read.status(read_key).get("unread_before") == 3)
for invalid_unread in (True, 0, -1, 1000, 3.5, "3"):
    interrupted_read = queue(); interrupted_read.touch(client, beat())
    read_key = interrupted_read.enqueue("read", "Synthetic Person", phone="353870000001", since=int(now[0] * 1000) - 60000)
    interrupted_read.lease(client); grant(interrupted_read, read_key, "open")
    interrupted_read.acknowledge(dict(id=read_key, owner=client["owner"], status="user_active", unread_before=invalid_unread))
    check("invalid interrupted unread count is omitted", "unread_before" not in interrupted_read.status(read_key))
for kind, opened in (("read", False), ("open", True), ("send", True)):
    ignored_badge = queue(); ignored_badge.touch(client, beat())
    fields = dict(phone="353870000001", since=int(now[0] * 1000) - 60000) if kind == "read" else dict(phone="353870000001", text="Reviewed text") if kind == "send" else {}
    badge_key = ignored_badge.enqueue(kind, "Synthetic Person", **fields); ignored_badge.lease(client)
    if opened: grant(ignored_badge, badge_key, "open")
    ignored_badge.acknowledge(dict(id=badge_key, owner=client["owner"], status="user_active", unread_before=3))
    check("unread metadata requires a read and its open grant: " + kind + str(opened), "unread_before" not in ignored_badge.status(badge_key))

# Reading shares recipient proof/navigation, never composer permission. All data here
# is synthetic; the worker tests separately prove event-delivery-before-ACK ordering.
reads = queue(); reads.touch(client, beat())
check("prior draft-only clients cannot arm new reading protocol", not reads.touch(dict(client, capability="drafts-v2"), beat()))
reads.touch(client, beat())
since = int(now[0] * 1000) - 60000
for fields in ({}, {"phone": "353870000001"}, {"since": since},
               {"phone": "353870000001", "since": True}, {"phone": "353870000001", "since": float(since)},
               {"phone": "353870000001", "since": -1}, {"phone": "353870000001", "since": int(now[0] * 1000) + 5001}):
    refused("read requires exact phone and bounded integer since", lambda fields=fields: reads.enqueue("read", "Synthetic Person", **fields))
refused("read cannot carry any draft text", lambda: reads.enqueue("read", "Synthetic Person", "Unsent text", phone="353870000001", since=since))
refused("ordinary open cannot smuggle reading time", lambda: reads.enqueue("open", "Synthetic Person", since=since))
read_key = reads.enqueue("read", "Synthetic Person", phone="353870000001", since=since)
check("readiness exposes queue occupancy and tab ownership without private identity", reads.readiness()["pending"] == 1
      and reads.readiness()["busy"] and reads.readiness()["owner"] == client["owner"] and "chat" not in reads.readiness())
command = reads.lease(client)
check("reading lease carries since and no composer text", command["kind"] == "read" and command["since"] == since and command["text"] is None)
check("read grant requires opening first", grant(reads, read_key, "read") is None)
grant(reads, read_key, "open")
check("read action can inspect a separately authorized profile", grant(reads, read_key, "profile") is not None)
check("read never obtains a draft permission", grant(reads, read_key, "draft") is None)
good_reading = dict(messages=3, incoming=2, outgoing=1, complete=True, oldest=since - 1000,
                    newest=since + 1000, reason="since_reached")
read_ack = dict(id=read_key, owner=client["owner"], status="read", reading=good_reading)
check("read ACK needs an actual read grant", not reads.acknowledge(read_ack))
reads.touch(client, beat(paused=True))
permits = [grant(reads, read_key, "read") for _ in range(24)]
check("own search permits at most24 individually short read grants", all(p and p["expires"] == int((now[0] + 2) * 1000) for p in permits)
      and grant(reads, read_key, "read") is None)
for bad in (None, {}, dict(good_reading, text="Do not persist private text"), dict(good_reading, messages=True),
            dict(good_reading, messages=1201), dict(good_reading, incoming=-1), dict(good_reading, outgoing=2),
            dict(good_reading, oldest=None), dict(good_reading, oldest=True), dict(good_reading, newest=since - 2000),
            dict(good_reading, newest=int(now[0] * 1000) + 5001), dict(good_reading, complete=1),
            dict(good_reading, reason="arbitrary private words"), dict(good_reading, reason=[]),
            dict(good_reading, complete=False), dict(good_reading, oldest=since + 1),
            dict(good_reading, reason="history_start"), dict(good_reading, reason="timeout"),
            dict(good_reading, reason="limit"), dict(good_reading, reason="no_messages"),
            dict(good_reading, reason="history_unavailable")):
    check("malformed or falsely complete metadata rejected", not reads.acknowledge(dict(read_ack, reading=bad)))
check("valid bounded reading ACK accepted after grants", reads.acknowledge(read_ack) and reads.status(read_key)["reading"] == good_reading)
check("reading ACK retry is exact and idempotent", reads.acknowledge(read_ack)
      and not reads.acknowledge(dict(read_ack, reading=dict(good_reading, messages=4))))
public = reads.status(read_key); public["reading"]["messages"] = 999
check("status summaries cannot mutate retained reading evidence", reads.status(read_key)["reading"]["messages"] == 3
      and not any(k in public for k in ("chat", "phone", "text", "since")))
for reason in ("history_start", "limit", "timeout", "history_unavailable"):
    partial = dict(good_reading, complete=False, reason=reason, oldest=since + 1)
    check("bounded partial history remains explicitly incomplete", bridge.reading_summary(partial, since, int(now[0] * 1000)) == partial)
empty = dict(messages=0, incoming=0, outgoing=0, complete=False, oldest=None, newest=None, reason="no_messages")
check("no readable messages is an honest incomplete result", bridge.reading_summary(empty, since, int(now[0] * 1000)) == empty)
check("empty counts cannot claim dated history", bridge.reading_summary(dict(empty, oldest=since), since, int(now[0] * 1000)) is None)
check("nonempty result cannot claim no messages", bridge.reading_summary(dict(good_reading, complete=False, reason="no_messages"), since, int(now[0] * 1000)) is None)
reads.touch(client, beat()); read_key = reads.enqueue("read", "Synthetic Person", phone="353870000001", since=since)
reads.lease(client); grant(reads, read_key, "open"); grant(reads, read_key, "read")
check("read grant cancellation prevents all further scroll grants", reads.cancel(read_key)["status"] == "unknown"
      and "no message text was typed" in reads.status(read_key)["detail"] and grant(reads, read_key, "read") is None)
check("cancelled read cannot later become successful", not reads.acknowledge(dict(read_ack, id=read_key)))
read_key = reads.enqueue("read", "Synthetic Person", phone="353870000001", since=since)
reads.lease(client); grant(reads, read_key, "open")
check("read cannot ACK as open or draft", not reads.acknowledge(dict(id=read_key, owner=client["owner"], status="opened"))
      and not reads.acknowledge(dict(id=read_key, owner=client["owner"], status="drafted")))
now[0] += 26
check("reading has35 seconds rather than ordinary25 second lease", reads.status(read_key)["status"] == "running")
now[0] += 9
check("reading lease expires without replay at35 seconds", reads.status(read_key)["status"] == "unknown" and reads.lease(client) is None)
reads.touch(client, beat()); read_key = reads.enqueue("open", "Synthetic Person"); reads.lease(client); grant(reads, read_key, "open")
check("ordinary actions never obtain read permission or ACK", grant(reads, read_key, "read") is None
      and not reads.acknowledge(dict(read_ack, id=read_key)))

committing = queue(); committing.touch(client, beat())
commit_key = committing.enqueue("read", "Synthetic Person", phone="353870000001", since=since)
committing.lease(client); grant(committing, commit_key, "open"); grant(committing, commit_key, "read")
complete_ack = dict(read_ack, id=commit_key)
for callback in (lambda value: False, lambda value: None):
    check("complete ACK cannot succeed without confirmed persistence", not committing.acknowledge(complete_ack, commit_read=callback)
          and committing.status(commit_key)["status"] == "running")
def failed_commit(value):
    raise OSError("synthetic full disk")
check("receipt I/O failure leaves complete result unacknowledged", not committing.acknowledge(complete_ack, commit_read=failed_commit)
      and committing.status(commit_key)["status"] == "running")
receipts = []
def save_commit(value):
    check("receipt is written before status claims read", committing.status(commit_key)["status"] == "running")
    receipts.append(value)
    return True
check("complete ACK commits bounded identity metadata before success", committing.acknowledge(complete_ack, commit_read=save_commit)
      and set(receipts[0]) == {"id", "chat", "since", "created", "completed"} and receipts[0]["id"] == commit_key)
check("accepted ACK retry never writes its receipt twice", committing.acknowledge(complete_ack, commit_read=save_commit) and len(receipts) == 1)
commit_key = committing.enqueue("read", "Synthetic Person", phone="353870000001", since=since)
committing.lease(client); grant(committing, commit_key, "open"); grant(committing, commit_key, "read")
partial_ack = dict(read_ack, id=commit_key, reading=dict(good_reading, complete=False, reason="limit"))
check("partial reading never writes a complete-history receipt", committing.acknowledge(partial_ack, commit_read=failed_commit)
      and committing.status(commit_key)["reading"]["complete"] is False)

old_names, old_beta, old_actions, old_record = wa.NAMES, wa.BETA, wa._actions, wa.record
try:
    files, scratch = {}, "synthetic"
    with patch.object(wa, "_read_json", side_effect=lambda name, default: files.get(name, default)), \
            patch.dict(os.environ, {"CCL_BETA_STATE": ""}):
        wa.NAMES, wa.BETA = os.path.join(scratch, "names.json"), os.path.join(scratch, "beta.json")
        def save(path, value):
            files[path] = json.loads(json.dumps(value))
        save(wa.NAMES, {"Known Label": "Saved Guard", "Legacy": "Legacy Guard"})
        save(wa.BETA, {"chat_names": {"Alex Example": "Alex ✅", "New Guard": "Known Label"}})
        alternate = os.path.join(scratch, "explicit-override.json")
        save(alternate, {"chat_names": {"Override Guard": "Override Label"}})
        with patch.dict(os.environ, {"CCL_BETA_STATE": alternate, "CCL_NO_DOWNLOAD": "1", "CCL_EDITS": "other-synthetic"}):
            check("beta environment override wins over module and test paths", wa._beta_file() == alternate)
            selected = wa.load_aliases("Lena Drayton")
            check("alias loader uses the same explicit beta state as UI", selected.get("Override Label") == "Override Guard" and "Alex ✅" not in selected)
        check("blank beta override preserves explicit module path", wa._beta_file() == wa.BETA)
        check("beta aliases exact Lena scope", wa.load_aliases("Lena Drayton").get("Alex ✅") == "Alex Example")
        check("existing taught alias wins", wa.load_aliases("Lena Drayton")["Known Label"] == "Saved Guard")
        for name in (None, "Lena", "Lena Other", "Lena Drayton Jr", "Other Controller"):
            check("other controller cannot use beta contacts", "Alex ✅" not in wa.load_aliases(name))
        check("normalized exact controller supported", "Alex ✅" in wa.load_aliases(" LENA   DRAYTON "))
        save(wa.BETA, {"chat_names": {"Alex": "Duplicate", "Blair": "Duplicate", "Casey": "Unique"}})
        check("ambiguous aliases rejected individually", "Duplicate" not in wa.load_aliases("Lena Drayton") and wa.load_aliases("Lena Drayton")["Unique"] == "Casey")
        for bad in [[], {"chat_names": []}, {"chat_names": {"Alex": 3}}, {"chat_names": {"Alex": "a\nb"}}]:
            save(wa.BETA, bad)
            check("malformed beta leaves legacy aliases only", wa.load_aliases("Lena Drayton") == {"Known Label": "Saved Guard", "Legacy": "Legacy Guard"})

        # Exercise the actual HTTP handler without a socket or any real receiver/cache files.
        wa._actions = queue()
        wa.record = lambda events, heartbeat: len(events or [])
        def post(path, body, token="synthetic-secret", origin="chrome-extension://synthetic"):
            raw = json.dumps(body).encode()
            h = SimpleNamespace(path=path, rfile=io.BytesIO(raw), headers={"Content-Length": str(len(raw)), "X-Reader-Token": token, "Origin": origin}, server=SimpleNamespace(token="synthetic-secret"))
            h._drain = lambda size: wa._Handler._drain(h, size)
            h._allowed = lambda: wa._Handler._allowed(h)
            h._answer = lambda code, value: (code, value)
            captured = []
            h._answer = lambda code, value: captured.append((code, value))
            wa._Handler.do_POST(h)
            return captured[-1]
        check("old reader event schema still ingests without arming bridge", post("/wa", {"events": [], "beat": beat()})[0] == 200 and not wa.bridge_status()["ready"])
        check("fresh authenticated capable reader arms bridge", post("/wa", {"events": [], "beat": beat(), "client": client})[0] == 200 and wa.bridge_status()["ready"])
        key = wa.enqueue_action("open", "Alex")
        code, value = post("/wa", {"events": [], "client": client})
        check("command piggybacks only authenticated receiver response", code == 200 and value["action"]["id"] == key)
        permit = {"id": key, "owner": client["owner"], "step": "open"}
        check("wrong token cannot authorize DOM mutation", post("/wa/authorize", permit, token="wrong")[0] == 404)
        check("web origin cannot authorize DOM mutation", post("/wa/authorize", permit, origin="https://web.whatsapp.com")[0] == 404)
        check("profile endpoint refuses stage before chat opening", post("/wa/authorize", dict(permit, step="profile")) == (409, {"ok": False}))
        check("authenticated search grant uses existing protected endpoint", post("/wa/authorize", dict(permit, step="search"))[0] == 200)
        check("authenticated final boundary grants once", post("/wa/authorize", permit)[0] == 200 and post("/wa/authorize", permit) == (409, {"ok": False}))
        profile_permit = dict(permit, step="profile")
        check("wrong token cannot authorize profile opening", post("/wa/authorize", profile_permit, token="wrong")[0] == 404)
        check("web origin cannot authorize profile opening", post("/wa/authorize", profile_permit, origin="https://web.whatsapp.com")[0] == 404)
        code, value = post("/wa/authorize", profile_permit)
        check("authenticated profile endpoint grants exact short stage once", code == 200 and value == dict(profile_permit, ok=True, expires=int((now[0] + 2) * 1000)) and post("/wa/authorize", profile_permit) == (409, {"ok": False}))
        ack = {"id": key, "owner": client["owner"], "status": "opened"}
        check("wrong token cannot ACK", post("/wa/action", ack, token="wrong")[0] == 404 and wa.action_status(key)["status"] == "running")
        check("web origin cannot ACK", post("/wa/action", ack, origin="https://web.whatsapp.com")[0] == 404)
        check("authenticated ACK accepted", post("/wa/action", ack) == (200, {"ok": True}))
        read_key = wa.enqueue_action("read", "Alex", phone="353870000001", since=since)
        post("/wa", {"events": [], "client": client})
        for step in ("open", "read"):
            post("/wa/authorize", dict(id=read_key, owner=client["owner"], step=step))
        completed = dict(id=read_key, owner=client["owner"], status="read", reading=good_reading)
        with patch.object(wa, "commit_read_coverage", side_effect=OSError("synthetic full disk")):
            check("HTTP complete ACK fails closed when coverage cannot be saved", post("/wa/action", completed) == (400, {"ok": False})
                  and wa.action_status(read_key)["status"] == "running")
        with patch.object(wa, "commit_read_coverage", return_value=True) as commit:
            check("unauthorized ACK never invokes coverage persistence", post("/wa/action", completed, token="wrong")[0] == 404
                  and commit.call_count == 0)
            check("authenticated complete ACK invokes server-owned receipt callback", post("/wa/action", completed) == (200, {"ok": True})
                  and commit.call_count == 1 and commit.call_args.args[0]["id"] == read_key)
            check("HTTP ACK retry preserves one coverage commit", post("/wa/action", completed) == (200, {"ok": True}) and commit.call_count == 1)
        check("arbitrary enqueue HTTP path absent", post("/wa/enqueue", {"kind": "send"})[0] == 404)
        check("malformed ACK safe and unsuccessful", post("/wa/action", {"id": [], "status": "unknown"}) == (400, {"ok": False}))
finally:
    wa.NAMES, wa.BETA, wa._actions, wa.record = old_names, old_beta, old_actions, old_record

print("%s of %s checks passed" % (sum(checks), len(checks)))
sys.exit(0 if all(checks) else 1)
