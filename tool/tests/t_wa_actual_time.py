"""Guard reply clocks, separate from outgoing questions and generated times. Pure synthetic roster, no I/O/Tk."""
import copy
import datetime as dt
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import calllog as c
import whatsapp as wa

DAY = dt.date(2026, 11, 7)
MID = dt.datetime.combine(DAY, dt.time())
checks = []


def check(label, ok):
    checks.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + label)


def minute(clock):
    h, m = map(int, clock.split(":"))
    return h * 60 + m


class Roster:
    remarks, empty = {}, {}

    def __init__(self, starts=(1200,)):
        self.starts = starts

    def shifts(self, date):
        if date != DAY:
            return 0, [], []
        rows = [dict(name="Lena Drayton", site="Kilcarran", start=1080, end=360, shift="18:00-06:00")]
        for n, start in enumerate(self.starts):
            rows.append(dict(name="Guard %s Example" % chr(65+n), site="Example Hall", start=start % 1440,
                             end=None, shift=c.fmt(start).zfill(5)+"-close", row=n+10, ref="A%d" % (n+10), strike=False, fill="FFFFFFFF"))
        return 0, rows, []


def event(ours, text, clock, name="Guard A Example", next_day=False, kind="bubble", **fields):
    date = DAY + dt.timedelta(days=bool(next_day))
    at = dt.datetime.combine(date, dt.time()) + dt.timedelta(minutes=minute(clock), seconds=7)
    value = dict(kind=kind, chat=name, text=text, what="text", ours=ours, direction="outgoing" if ours else "incoming",
                 date=date.strftime("%d/%m/%Y"), shown=clock, seen=int(at.timestamp()*1000),
                 id=name+clock+text[:8] if kind == "bubble" else "", truncated=False, group=False)
    value.update(fields)
    return value


Q = lambda clock="19:00", text="All okay for Example Hall?", **kw: event(True, text, clock, **kw)
Y = lambda clock="19:08", text="Yes", **kw: event(False, text, clock, **kw)


def built(events=(), *, starts=(1200,), now=None, edits=None, override=None, commits=None, trusted=(), emojis=None):
    now = now or MID + dt.timedelta(hours=23)
    roster = Roster(starts)
    with patch.object(c, "load_edits", return_value=edits or {}), patch.object(wa, "load_heard", return_value={}), \
            patch.object(wa, "trusted_names", return_value={wa.fold(name) for name in trusted}):
        plain = c.build(DAY, roster, "Lena Drayton", now=now)
        heard = override if override is not None else wa.heard(plain, events, read_commits=commits, confirmation_emojis=emojis)
        overlay = dict(heard, __shift__=dict(waiting=True, since="18:00", since_at=DAY.isoformat()+"T18:00:00"))
        with patch.object(wa, "load_heard", return_value=overlay):
            result = c.build(DAY, roster, "Lena Drayton", now=now)
    return result, plain


log, plain = built([Q(), Y()])
row = log["calls"][0]
key = c.edit_key(row)
check("Time of call uses the guard's reply, not our question", row["call_text"] == "19:08" and row["heard"]["time"] == "19:00")
check("reply time carries an explicit message date and source", row["heard"].get("reply_at") == "2026-11-07T19:08" and row["heard"].get("reply_source") == "bubble")
check("actual reply time is distinguished from hand edits and generation", row["time_source"] == "whatsapp_reply" and row["timed"] and row["heard_time"])
check("reply-clock hover explains what Time of call uses", "guard's reply clock: 19:08 on 2026-11-07" in wa.story(row))
early, _ = built([Q("20:40"), Y("20:51")], starts=(23*60,), now=MID+dt.timedelta(hours=21))
row = early["calls"][0]
check("a real reply before the scheduled mark replaces generation", row["call_text"] == "20:51" and row["call"] < row["start"]-c.LEAD)
check("early reply does not change the scheduled due time", row["due"] == "22:00" and not row["ahead"] and row["response"] == "Ok")
early, _ = built([Q("17:05"), Y("17:06")], now=MID+dt.timedelta(hours=19))
check("valid same-roster-day reply before controller start remains actual", early["calls"][0]["call_text"] == "17:06")

manual, _ = built([Q(), Y()], edits={key: {"time": "18:55", "response": "Manual decision"}})
check("manual time and response remain first priority", manual["calls"][0]["call_text"] == "18:55" and manual["calls"][0]["response"] == "Manual decision"
      and manual["calls"][0]["time_source"] == "manual" and not manual["calls"][0]["heard_time"])
check("manual override hover does not claim the reply clock is displayed", "Time of call uses" not in wa.story(manual["calls"][0]))
trusted, base = built(trusted=("Guard A Example",))
check("a trusted guard without a reply retains the generated fallback", trusted["calls"][0]["response"] == "Ok"
      and trusted["calls"][0]["time_source"] == "generated" and trusted["calls"][0]["call"] == base["calls"][0]["call"])
only_response, _ = built([Q(), Y()], edits={key: {"response": "No answer"}})
check("a manual response alone does not replace the reply's actual time", only_response["calls"][0]["call_text"] == "19:08" and only_response["calls"][0]["response"] == "No answer")

night, original = built([Q("23:59"), Y("00:05", next_day=True)], starts=(26*60,), now=MID+dt.timedelta(hours=27))
row = night["calls"][0]
check("overnight reply retains its next-calendar-day offset", row["call"] == 1445 and row["call_text"] == "0:05" and row["heard"]["reply_at"] == "2026-11-08T00:05")
check("overnight reply remains earlier than its unchanged due time", row["due"] == "1:00" and not row["ahead"])
future, original = built([Q("23:59"), Y("00:05", next_day=True)], starts=(26*60,), now=MID+dt.timedelta(hours=24, minutes=2))
check("future clock does not become a completed call", future["calls"][0]["time_source"] == "generated" and future["calls"][0]["call"] == original["calls"][0]["call"])

for stamp in ("2026-11-06T19:08", "2026-11-09T19:08", "2026-11-07T99:99", "19:08", "2026-11-07T19:08+00:00"):
    bad = {key: dict(how="reply", response="Ok", time="19:00", at="19:08", reply_source="bubble", reply_at=stamp)}
    result, base = built(override=bad)
    check("invalid/unsupported date-clock falls back without inventing a day: "+stamp, result["calls"][0]["call"] == base["calls"][0]["call"]
          and result["calls"][0]["time_source"] == "generated")
for fields in ({}, {"reply_source": "observation", "reply_at": "2026-11-07T19:08"},
               {"reply_source": "bubble", "reply_at": "2026-11-07T19:08", "shared_reply": True}):
    result, base = built(override={key: dict(how="reply", response="Ok", time="19:00", at="19:08", **fields)})
    check("legacy/unproven/shared reply clock stays generated", result["calls"][0]["time_source"] == "generated"
          and result["calls"][0]["call"] == base["calls"][0]["call"])

for label, events in (("no reply", [Q()]), ("unverified preview", [Q(), Y(kind="line", direction="unknown")]),
                      ("partial read", [Q(), Y(read_id="a"*32)]), ("truncated body", [Q(), Y(truncated=True)]),
                      ("no displayed clock", [Q(), Y(kind="line", shown="Yesterday")])):
    result, base = built(events)
    check(label+" cannot borrow a question or observation time", result["calls"][0]["time_source"] == "generated"
          and "reply_at" not in result["calls"][0]["heard"])
preview, _ = built([Q(), Y(kind="line", shown="19:07")])
check("known incoming preview uses its displayed clock, not later observation", preview["calls"][0]["call_text"] == "19:07"
      and preview["calls"][0]["heard"]["reply_source"] == "preview_clock")
for label, message in (("standalone thumbs-up", Y(text="👍")), ("voice reply", Y(text="0:30", what="voice")),
                       ("negative reply", Y(text="I cannot come tonight"))):
    result, _ = built([Q(), message])
    check(label+" retains its own real message clock", result["calls"][0]["call_text"] == "19:08")
cancel, _ = built([Q(), Y(), event(False, "I cannot come tonight", "19:20")])
check("a selected later cancellation uses its own clock", cancel["calls"][0]["call_text"] == "19:20"
      and cancel["calls"][0]["heard"].get("taken_back") and not cancel["calls"][0]["response"])
sticker, _ = built([Q(), Y(), event(False, "", "19:09", what="media", media_type="sticker")])
check("a captionless later sticker does not replace the clear Yes's time", sticker["calls"][0]["call_text"] == "19:08"
      and sticker["calls"][0]["response"] == "Ok")

rid = "1"*32
q = Q(read_id=rid)
q["reactions"] = dict(complete=True, items=[dict(parent_id=q["id"], emoji="👍", actor="contact", actor_name=q["chat"], at=None)])
receipt = {rid: dict(chat=q["chat"], since=int((MID+dt.timedelta(hours=17)).timestamp()*1000), created=q["seen"]-1000, completed=q["seen"]+1000)}
reaction, base = built([q], commits=receipt)
check("attached thumbs-up confirms without inventing a reply timestamp", reaction["calls"][0]["response"] == "Ok"
      and "reply_at" not in reaction["calls"][0]["heard"] and reaction["calls"][0]["time_source"] == "generated")
check("attached-reaction hover still says its time is unavailable", "does not provide the reaction time" in wa.story(reaction["calls"][0]))
same_q = Q("19:08", read_id=rid, view_id="2"*32, i=3)
same_yes = Y(read_id=rid, view_id="2"*32, i=4)
same_sticker = event(False, "", "19:08", what="media", media_type="sticker", read_id=rid, view_id="2"*32, i=5)
receipt[rid]["created"], receipt[rid]["completed"] = same_yes["seen"]-1000, same_yes["seen"]+1000
same, _ = built([same_q, same_yes, same_sticker], commits=receipt)
check("committed same-minute Yes followed by a sticker uses the Yes's real clock", same["calls"][0]["response"] == "Ok"
      and same["calls"][0]["call_text"] == "19:08" and same["calls"][0]["heard"]["reply_at"] == "2026-11-07T19:08")
timed, _ = built([Q(), Y(kind="line", what="reaction", emoji="👍", to="All okay for Example Hall?")])
check("legacy reaction preview does not acquire a message reply clock", timed["calls"][0]["time_source"] == "generated")

second = "Guard B Example"
ordered, _ = built([Q(), Y("19:30"), Q("19:05", name=second), Y("19:20", name=second)], starts=(1200,1260))
check("rows sort by actual reply time while identities stay attached", [r["name"] for r in ordered["calls"]] == [second, "Guard A Example"])
check("sorting retains each assignment's own due time", [r["due"] for r in ordered["calls"]] == ["20:00", "19:00"])

# An explicit question about this one assignment can be asked much earlier on the roster day.
early_events = [Q("10:00"), Y("17:59")]
result, _ = built(early_events, starts=(22*60,), now=MID+dt.timedelta(hours=18))
early_key = c.edit_key(result["calls"][0])
check("a delayed answer to today's exact early question confirms immediately", result["calls"][0]["response"] == "Ok"
      and result["calls"][0]["call_text"] == "17:59" and not result["calls"][0]["ahead"])
result, _ = built([Q("00:10"), Y("00:30")], starts=(22*60,), now=MID+dt.timedelta(hours=18))
check("same-roster-date proof works from midnight, not an arbitrary pre-shift floor", result["calls"][0]["response"] == "Ok"
      and result["calls"][0]["call_text"] == "0:30")
for label, items in (
    ("unprompted early Yes", [Y("17:59")]),
    ("generic early question", [Q("10:00", text="All good?"), Y("17:59")]),
    ("wrong venue sharing a word", [Q("10:00", text="All okay for Different Hall?"), Y("17:59")]),
    ("another day offer", [Q("10:00", text="All okay for Example Hall tomorrow?"), Y("17:59")]),
    ("prior-day question", [Q("10:00", date="06/11/2026"), Y("17:59")]),
    ("undated early preview", [Q("10:00", kind="line"), Y("17:59")]),
    ("new outgoing topic", [Q("10:00"), event(True,"Roster for next week","15:00"), Y("17:59")]),
    ("reply about tomorrow", [Q("10:00"), Y("17:59",text="Yes tomorrow")]),
    ("reply after start plus ninety minutes", [Q("10:00"),Y("23:31")]),
    ("unverified sender", [Q("10:00"),Y("17:59",direction="unknown")]),
    ("incomplete capture", [Q("10:00"),Y("17:59",read_id="a"*32)]),
):
    result, _ = built(items, starts=(22*60,), now=MID+dt.timedelta(hours=23,minutes=50))
    check(label+" does not acquire early confirmation", result["calls"][0]["response"] != "Ok")
result, _ = built(early_events, starts=(22*60,23*60))
same_person = copy.deepcopy(result)
for row in same_person["calls"]:
    row["name"] = "Guard A Example"
ambiguous = wa.heard(same_person, early_events)
check("one early venue question cannot choose between two same-person assignments", all(r.get("response") != "Ok" for r in ambiguous.values()))
for label, message in (("known cancellation",event(False,"I cannot come tonight","20:00")),
                       ("unverified later negative",event(False,"I cannot come tonight","20:00",read_id="a"*32))):
    result, _ = built(early_events+[message], starts=(22*60,))
    check(label+" revokes early confirmation beyond the old three-hour window", result["calls"][0]["response"] != "Ok"
          and result["calls"][0]["heard"].get("taken_back"))
for label, message in (("unknown sender",event(False,"New context","20:00",direction="unknown")),
                       ("voice",event(False,"0:10","20:00",what="voice")),
                       ("unreadable text",event(False,"لا أستطيع الحضور","20:00"))):
    result, _ = built([Q("10:00"),Y("10:15"),message],starts=(22*60,))
    check("early morning Yes still requires review for later "+label,result["calls"][0]["response"] != "Ok"
          and result["calls"][0]["heard"].get("taken_back"))
result, _ = built(early_events, starts=(22*60,), edits={early_key:{"time":"18:55","response":"No answer"}})
check("early acceptance never changes explicit hand corrections", result["calls"][0]["response"] == "No answer"
      and result["calls"][0]["call_text"] == "18:55")
check("manual time provenance is explained beside WhatsApp evidence", "Your manual time is kept" in wa.story(result["calls"][0]))

# User-defined meaning applies only to this exact full-name person's standalone reply to a scoped question.
rules={"Guard A Example":["🤙"]}
for symbol in ("🤙","🤙🏽","🤙️"):
    result, _ = built([Q(),Y(text=symbol)],emojis=rules)
    check("saved personal emoji and presentation variants confirm with the actual clock: "+symbol,
          result["calls"][0]["response"] == "Ok" and result["calls"][0]["call_text"] == "19:08")
for label, items, rule in (
    ("no saved rule",[Q(),Y(text="🤙")],{}),
    ("different full-name person",[Q(),Y(text="🤙")],{"Guard B Example":["🤙"]}),
    ("unprompted emoji",[Y(text="🤙")],rules),
    ("wrong-day question",[Q(text="All okay for Example Hall tomorrow?"),Y(text="🤙")],rules),
    ("emoji plus cancellation",[Q(),Y(text="🤙 I cannot come tonight")],rules),
    ("emoji plus conflicting symbol",[Q(),Y(text="🤙👎")],rules),
    ("unknown sender emoji",[Q(),Y(text="🤙",direction="unknown")],rules),
    ("partial emoji",[Q(),Y(text="🤙",read_id="a"*32)],rules),
):
    result, _ = built(items,emojis=rule)
    check(label+" does not use the personal confirmation rule",result["calls"][0]["response"] != "Ok")
check("personal rule names are exact normalized identities, never first-name matches",wa._confirmation_rules({" GUARD A EXAMPLE ":["🤙"]})
      == {"guard a example":("🤙",)} and wa._confirmation_rules({"Guard A":["🤙"]}).get("guard a example") is None)
check("malformed symbolic rules cannot authorize arbitrary words or mixed negative text",wa._confirmation_rules({"Guard A Example":["Yes","🤙 no",None]})=={})
check("conflicting equivalent full-name rules stay disabled",wa._confirmation_rules({"Guard A Example":["🤙"]," GUARD A EXAMPLE ":["👍"]})=={})
with patch.object(wa,"settings",return_value={"confirmation_emojis":rules}):
    check("saved rules are loaded through the pure normalizer",wa.load_confirmation_emojis()=={"guard a example":("🤙",)})

new_rid="b"*32
receipt={new_rid:dict(chat="Guard A Example",since=int((MID+dt.timedelta(hours=17)).timestamp()*1000),
                    created=int((MID+dt.timedelta(hours=19,minutes=1)).timestamp()*1000),completed=int((MID+dt.timedelta(hours=19,minutes=2)).timestamp()*1000))}
result,_=built([Q(),Y(read_id="a"*32)],commits=receipt)
row=result["calls"][0]
check("new message after complete read is explained as new context",row["heard"].get("read_reason")=="new_context"
      and wa.cell_hint(row)==("Recheck chat","amber") and "newer message was observed after the last verified read" in wa.story(row))
result,_=built([Q(),Y(read_id="a"*32)])
check("partial history remains distinct from an unanswered chat", "not verified enough conversation history" in wa.story(result["calls"][0]))
check("generated fallback is labelled scheduled, not an actual reply time", "Time of call is scheduled; no verified reply clock yet" in wa.story(result["calls"][0]))

print("%d of %d checks passed" % (sum(checks),len(checks)))
raise SystemExit(0 if all(checks) else 1)
