"""Pure synthetic beta policy/preview/arrival checks. No Tk, real files, receiver, contacts or network."""
import copy
import datetime as dt
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wa_beta as beta

checks = []


def check(label, okay, detail=None):
    checks.append(bool(okay))
    if not okay:
        print("FAIL %s: %r" % (label, detail))


def refused(label, action):
    try:
        action()
    except ValueError:
        check(label, True)
    else:
        check(label, False)


def call(name="Alex Example", **fields):
    return dict(dict(name=name, site="Example Hall", venue="Example Hall", shift="20:00-close",
                     start=1200, call_text="19:00", response="Ok", default_response="Ok", heard={}), **fields)


def log(*calls, **meta):
    return dict(stem="2026-11-07 Lena Drayton", calls=list(calls or [call()]),
                meta=dict(dict(supervisor="Lena Drayton", date_iso="2026-11-07", start_min=990, end_min=1800), **meta))


for name in ("Lena Drayton", "  LENA   DRAYTON  ", "Lena\tDrayton"):
    check("exact normalized controller accepted: " + repr(name), beta.allowed_controller(name))
for name in ("Lena", "Lena Other", "Lena Drayton Jr", "Other Lena Drayton", "", None):
    check("partial or different controller refused: " + repr(name), not beta.allowed_controller(name))

for value in ("+353 87 000 0001", "00353-87-000-0001", "087 000 0001", "353870000001"):
    check("formatted saved number accepted", beta.normalize_phone(value) == "353870000001", value)
for value in ("", None, 353870000001, "+00353870000001", "123", "0", "+0000000000",
              "https://example.test/353870000001", "353870000001&text=bad", "0870000001 ext 3",
              "353870000001\n123", "1234567890123456", "３５３８７００００００１",
              "+3530870000001", "+44 (0) 20 7946 0000", "0870000"):
    check("unsafe or malformed phone rejected: " + repr(value), beta.normalize_phone(value) == "")

source = log(call(), call("Blair Example", heard={"response": "Ok", "how": "reply"}),
             call("Casey Trusted"), call("Drew Review", heard={"direction_unknown": True, "how": "check"}),
             call("Evan Manual", heard={"response": "", "how": "voice"}),
             call("Frank Timed", edited=True))
edits = {"Evan Manual|Example Hall|20:00-close": {"response": " ok "},
         "Frank Timed|Example Hall|20:00-close": {"time": "19:05"}}
before = copy.deepcopy((source, edits))
rows = beta.draft_rows(source, edits=edits, trusted=["Casey Trusted"])
by_name = {r["name"]: r for r in rows}
check("every shown guard has a preview without any phone lookup", len(rows) == 6)
check("row key matches existing calllog edit identity", rows[0]["key"] == "Alex Example|Example Hall|20:00-close")
check("generated Ok is not an evidence-based confirmation", by_name["Alex Example"]["status"] == "no_reply")
check("missing captured reply does not claim the guard has not answered", by_name["Alex Example"]["status_label"] == "Reply not read")
check("a verified question without an answer says waiting for reply", beta.draft_rows(log(call(heard={"how":"asked","response":""})))[0]["status_label"] == "Waiting for reply")
check("new unverified history asks for a chat recheck rather than implying a reply exists", beta.draft_rows(log(call(heard={"how":"check","read_incomplete":True,"direction_unknown":True})))[0]["status_label"] == "Recheck chat")
check("same-shift authoritative yes is confirmed", by_name["Blair Example"]["status"] == "confirmed")
check("trusted remains a separate status", by_name["Casey Trusted"]["status"] == "trusted")
check("uncertain sender needs review", by_name["Drew Review"]["status"] == "review")
check("saved manual Ok overrides uncertain evidence", by_name["Evan Manual"]["status"] == "confirmed")
check("time-only edit cannot manufacture confirmation", by_name["Frank Timed"]["status"] == "no_reply")
check("parallel status counts are exact",
      beta.status_counts(rows) == dict(confirmed=2, trusted=1, no_reply=2, review=1), beta.status_counts(rows))
check("preview creation does not mutate log or saved edits", (source, edits) == before)
check("default wording follows the concise check-call style",
      rows[0]["text"] == "All okay for Example Hall?", rows[0]["text"])
night = beta.draft_rows(log(call(start=1530, shift="01:30-close")))[0]
check("overnight start rolls into the next calendar date", night["start_date"] == "2026-11-08" and night["start_time"] == "01:30")
check("overnight date is metadata rather than an invented extra message", night["text"] == "All okay for Example Hall?")
reordered = beta.draft_rows(dict(source, calls=list(reversed(source["calls"]))))
check("keys are stable across rebuilding and reordering", {r["key"] for r in rows} == {r["key"] for r in reordered})
check("another controller gets no beta guard previews", beta.draft_rows(log(supervisor="Lena Other")) == [])
check("a short first name cannot enable beta previews", beta.draft_rows(log(supervisor="Lena")) == [])
bad_start = beta.draft_rows(log(call(start=None)))[0]
check("missing start is reviewed rather than guessed", bad_start["status"] == "review" and bad_start["text"] == "")
refused("invalid shift date is not invented", lambda: beta.draft_rows(log(date_iso="2026-02-30")))
review = beta.draft_rows(log(call(heard={"response": "Ok"})), edits={rows[0]["key"]: {"response": "No answer"}})[0]
check("manual negative overrides old WhatsApp yes", review["status"] == "review")
check("trusted person with contrary message is reviewed",
      beta.draft_rows(log(call(heard={"how": "voice"})), trusted=["Alex Example"])[0]["status"] == "review")
check("asked without an answer stays no reply", beta.draft_rows(log(call(heard={"how": "asked"})))[0]["status"] == "no_reply")
check("cleared explicit response does not show old yes confirmed",
      beta.draft_rows(log(call(heard={"response": "Ok"})), edits={rows[0]["key"]: {"response": ""}})[0]["status"] == "no_reply")

settings = {"draft_to": ["Lena Drayton"], "phones": {"Lena Drayton": "+353870000001"}}
self_url = beta.self_link(settings, controller="Lena Drayton")
check("self open-chat link has no message text", self_url == "whatsapp://send?phone=353870000001", self_url)
body = "Self test 👍 &phone=999?\nPlease review."
draft_url = beta.self_link(settings, controller="Lena Drayton", text=body)
query = urllib.parse.parse_qs(urllib.parse.urlparse(draft_url).query)
check("draft content cannot inject a new recipient or query field", query == {"phone": ["353870000001"], "text": [body]}, query)
refused("self testing cannot target a guard", lambda: beta.self_link(settings, controller="Lena Drayton", target="Alex Example"))
refused("self testing requires the exact controller", lambda: beta.self_link(settings, controller="Lena"))
refused("self testing requires the explicit draft allowlist",
        lambda: beta.self_link(dict(settings, draft_to=[]), controller="Lena Drayton"))
refused("first-name allowlist does not authorize self", lambda: beta.self_link(dict(settings, draft_to=["Lena"]), controller="Lena Drayton"))
refused("missing self phone is not filled from a guard contact",
        lambda: beta.self_link(dict(settings, phones={"Alex Example": "+353870000002"}), controller="Lena Drayton"))
refused("duplicate normalized self contacts with different phones are ambiguous",
        lambda: beta.self_link(dict(settings, phones={"Lena Drayton": "+353870000001", " LENA  DRAYTON ": "+353870000002"}), controller="Lena Drayton"))
refused("blank prepared text is refused", lambda: beta.self_link(settings, controller="Lena Drayton", text=" "))
refused("overlong prepared text is refused", lambda: beta.self_link(settings, controller="Lena Drayton", text="x" * 4097))

current, key = log(), rows[0]["key"]
phones = {"Alex Example": "+353870000002"}
now = dt.datetime(2026, 11, 7, 20)


def guard(**overrides):
    args = dict(now=now, enabled=True, text=rows[0]["text"])
    args.update(overrides)
    return beta.guard_link(current, key, phones, **args)


check("reviewed current-shift guard draft returns only a compose URL", guard().startswith("whatsapp://send?phone=353870000002&text="))
check("current guard open-chat link has no prepared message", guard(text=None) == "whatsapp://send?phone=353870000002")
refused("guard draft gate is off by default", lambda: beta.guard_link(current, key, phones, now=now))
refused("truthy nonboolean does not enable guard drafts", lambda: guard(enabled="yes"))
check("upcoming shift starting today allows draft preparation", bool(guard(now=dt.datetime(2026, 11, 7, 3, 45))))
refused("tomorrow's shift remains preview only", lambda: guard(now=dt.datetime(2026, 11, 6, 23)))
check("live previous-day overnight shift is actionable", beta.actionable_shift(current, dt.datetime(2026, 11, 8, 1)))
check("ended previous-day shift is not actionable", not beta.actionable_shift(current, dt.datetime(2026, 11, 8, 7)))
check("another controller never becomes actionable", not beta.actionable_shift(log(supervisor="Lena Other"), now))
check("shift's exact start is active", bool(guard(now=dt.datetime(2026, 11, 7, 16, 30))))
check("overnight guard draft stays in the original shift", bool(guard(now=dt.datetime(2026, 11, 8, 1))))
refused("at handover the outgoing shift no longer enables a draft", lambda: guard(now=dt.datetime(2026, 11, 8, 6)))
refused("historic shift cannot open a guard draft", lambda: guard(now=dt.datetime(2026, 11, 9, 20)))
refused("timezone-offset input cannot silently alter control-room dates", lambda: guard(now=now.replace(tzinfo=dt.timezone.utc)))
refused("another controller cannot open a guard draft",
        lambda: beta.guard_link(log(supervisor="Lena Other"), key, phones, now=now, enabled=True))
refused("a name is not a selected exact row key", lambda: beta.guard_link(current, "Alex Example", phones, now=now, enabled=True))
refused("a row from another venue is not in this shift",
        lambda: beta.guard_link(current, "Alex Example|Other Hall|20:00-close", phones, now=now, enabled=True))
refused("missing contact cannot route to self or another guard", lambda: beta.guard_link(current, key, {}, now=now, enabled=True))
refused("first-name contact cannot resolve a guard", lambda: beta.guard_link(current, key, {"Alex": "+353870000002"}, now=now, enabled=True))
refused("conflicting full-name contact numbers require a decision",
        lambda: beta.guard_link(current, key, dict(phones, **{" ALEX EXAMPLE ": "+353870000003"}), now=now, enabled=True))
refused("a number saved for another person cannot silently route a guard action",
        lambda: beta.guard_link(current, key, dict(phones, **{"Different Person": phones["Alex Example"]}), now=now, enabled=True))
check("equivalent spelling of the same person with the same number is still usable",
      bool(beta.guard_link(current, key, dict(phones, **{" ALEX  EXAMPLE ": phones["Alex Example"]}), now=now, enabled=True)))
refused("duplicate identical roster keys are ambiguous",
        lambda: beta.guard_link(log(call(), call()), key, phones, now=now, enabled=True))
check("case and spacing normalize exact full-name contact",
      beta.guard_link(current, key, {"  ALEX  EXAMPLE ": "+353870000002"}, now=now, enabled=True).endswith("353870000002"))

baseline = dt.datetime(2026, 11, 7, 20, 0, 30)
later = baseline + dt.timedelta(minutes=1)


def incoming(mid="new", at=later, **fields):
    return dict(dict(kind="bubble", chat="Alex Chat", direction="incoming", ours=False, text="All good 👍", what="text",
                     date=at.strftime("%d/%m/%Y"), shown=at.strftime("%H:%M"), seen=int(at.timestamp() * 1000), id=mid), **fields)


def sample(tracker, events, at=later, **overrides):
    args = dict(controller="Lena Drayton", scope=current["stem"], matched_chats={"Alex Chat": "Alex Example"}, now=at)
    args.update(overrides)
    return tracker.sample(events, **args)


tracker = beta.NotificationTracker()
check("first sample silently establishes a baseline", sample(tracker, [incoming("old", baseline)], baseline) == [])
check("new dated incoming roster bubble notifies", len(sample(tracker, [incoming()])) == 1)
check("replayed stable ID never notifies again", sample(tracker, [incoming(text="edited text")]) == [])
check("new incoming self message is in scope", len(sample(tracker, [incoming("self", chat="LEna Drayton")])) == 1)
check("explicit saved self alias is supported", len(sample(tracker, [incoming("alias", chat="My phone")], self_chats=["My phone"])) == 1)
for label, change in (("outgoing", dict(direction="outgoing", ours=True)), ("unknown", dict(direction="unknown", ours=None)),
                      ("conflicting direction", dict(ours=True)), ("group", dict(group=True)),
                      ("startup snapshot", dict(snapshot=True)), ("draft flag", dict(draft=True)),
                      ("draft preview text", dict(text="Draft: yes")), ("history replay", dict(history=True)),
                      ("unmatched chat", dict(chat="Someone Else")), ("list preview", dict(kind="line")),
                      ("unknown date", dict(date="")), ("invalid clock", dict(shown="99:99")),
                      ("invalid arrival", dict(seen=float("nan")))):
    check("notification excludes " + label, sample(tracker, [incoming(label, **change)]) == [])
check("same-minute startup is conservatively silent",
      sample(tracker, [incoming("same-minute", baseline + dt.timedelta(seconds=20))], baseline + dt.timedelta(seconds=20)) == [])
check("old message newly re-rendered is history, not an arrival",
      sample(tracker, [incoming("old-render", baseline - dt.timedelta(days=1), seen=int(later.timestamp() * 1000))]) == [])
check("old queued arrival timestamp is not new", sample(tracker, [incoming("queued", seen=int((baseline-dt.timedelta(seconds=1)).timestamp()*1000))]) == [])
check("future message clock is not trusted", sample(tracker, [incoming("future", later + dt.timedelta(minutes=3))]) == [])
check("too-old arrivals stay silent after a polling gap",
      sample(tracker, [incoming("late")], later + dt.timedelta(minutes=4)) == [])
unknown = incoming("corrected", direction="unknown", ours=None)
sample(tracker, [unknown])
check("later direction correction is not a new arrival", sample(tracker, [incoming("corrected")]) == [])
fallback = incoming("")
check("ID-less incoming bubble can notify once", len(sample(tracker, [fallback])) == 1)
check("fallback dedupe ignores changing DOM indexes", sample(tracker, [dict(fallback, i=999)]) == [])
check("fallback preserves distinct emoji payloads", len(sample(tracker, [dict(fallback, text="All good 👎")])) == 1)
check("changing selected shift establishes a silent baseline", sample(tracker, [incoming("other-scope")], scope="another shift") == [])
check("controller gate also disables notifications", sample(tracker, [incoming("wrong-controller")], controller="Lena Other") == [])
check("re-enabling controller establishes baseline without startup sound", sample(tracker, [incoming("return")]) == [])

print("%d of %d checks passed" % (sum(checks), len(checks)))
raise SystemExit(0 if all(checks) else 1)
