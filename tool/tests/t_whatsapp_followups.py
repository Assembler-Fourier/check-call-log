"""Pure synthetic regressions for conversation boundaries and withdrawing a WhatsApp confirmation.

No receiver, window, files, roster or real chat history is used. Run directly with python -B.
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import calllog
import whatsapp as wa

DAY = datetime.date(2026, 11, 7)  # Saturday: a Saturday cancellation is this shift, not another day.
CALL = {"name": "Alex Example", "site": "Example Hall", "venue": "Example Hall",
        "start": 20 * 60, "shift": "20:00-close"}
LOG = {"meta": {"date_iso": DAY.isoformat()}, "calls": [CALL]}
KEY = calllog.edit_key(CALL)
checks = []


def check(label, condition, got=None):
    checks.append(bool(condition))
    if not condition:
        print("FAIL %s: %r" % (label, got))


def event(ours, text, time="18:20", what="text", **extra):
    hour, minute = map(int, time.split(":"))
    at = datetime.datetime.combine(DAY, datetime.time(hour, minute, 7))
    return dict(kind="line", chat=CALL["name"], ours=ours, text=text, what=what,
                seen=int(at.timestamp() * 1000), shown=time, **extra)


def heard(events):
    return wa.heard(LOG, events).get(KEY, {})


def reply(*later, first="Yes"):
    return heard([event(True, "All okay for Example Hall?", "18:00"),
                  event(False, first, "18:02"), *later])


def withdrawn(label, events, expected_kind="check"):
    got = reply(*events)
    check(label, got.get("response") == "" and got.get("taken_back") is True
          and got.get("how") == expected_kind and got.get("first") == "Yes", got)


def remains(label, events):
    got = reply(*events)
    check(label, got.get("response") == "Ok" and not got.get("taken_back"), got)


# The reply itself still belongs to the first question only until the controller changes subject.
for label, text, kind in (("voice duration", "0:45", "voice"), ("photo", "Photo", "media"),
                          ("calendar emoji", "\U0001f4c5", "text"), ("empty", "", "text"),
                          ("new offer", "Can you work Sunday?", "text"),
                          ("thanks before answer", "Thanks", "text")):
    got = heard([event(True, "All okay for Example Hall?", "18:00"),
                 event(True, text, "18:01", kind), event(False, "Ok", "18:02")])
    check("unanswered question closes on " + label, got.get("response") != "Ok", got)

for text, kind in (("?", "text"), ("!", "text"), ("??!", "text"), (".", "text"),
                   ("Please reply ASAP", "text"), ("At 8pm", "text"),
                   ("Voice call, No answer", "call"), ("You deleted this message", "deleted")):
    got = heard([event(True, "All okay for Example Hall?", "18:00"),
                 event(True, text, "18:01", kind), event(False, "All good", "18:02")])
    check("real chaser stays open: " + text, got.get("response") == "Ok", got)

# Offer vocabulary and a date in another clause must not hide a cancellation.
for text in ("Sorry not available", "Sorry I can't come, need cover please",
             "Sorry I can't make it, can someone swap with me?", "I'll be late, next bus is in 40 minutes",
             "Sorry I'll be late, missed the 1st bus", "Running late, sat nav says 20 mins",
             "Tom said I'm not needed, so not coming", "Can't make it, my car is in the garage till Monday",
             "I'm not available until Monday", "Can't make it tonight, I can do Sunday instead",
             "I'm not well, can't make it. I can do an extra shift next week",
             "Sorry I'm sick, can't come in, maybe Sunday", "Not coming this evening, I'll do next week",
             "Sorry I can't work Saturday", "Sorry I can't work this weekend", "Sorry I can't work this week",
             "Sorry I can't work on November 7th", "Sorry I can't work on the 7th"):
    withdrawn("cancellation preserved: " + text, [event(False, text)])

for text in ("Can you please put me off tomorrow instead of Sunday", "Sorry I can't work Sunday",
             "Sorry, I can't work Sunday", "Sorry not available on December 25th",
             "I'm off next week", "Can I have the 8th off?", "Can I have Christmas off?"):
    remains("other-day request: " + text, [event(False, text)])

# A question mark must never conceal negative content. Other questions remain unrelated.
for text in ("Actually I'm sick, can someone cover me?", "Sorry I can't do it, is that ok?",
             "I'm stuck in traffic can you let them know I'm running late?",
             "Hang on, am I working tonight?", "Am I in tonight?", "Wait I thought I was off?",
             "Just woke up", "Slept through my alarm", "My car broke down"):
    withdrawn("later shift doubt: " + text, [event(False, text)])

for text in ("Thanks", "See you later", "Can you send me my payslip?", "Where can I park?",
             "What is the office address?", "Am I on your contact list?"):
    remains("unrelated follow-up: " + text, [event(False, text)])

for text in ("Family situation, need to go home. Can you send the list?",
             "Family situation need to go home can you send the list?",
             "Sorry, family situation, need to go home. Can you send the list?",
             "Can you send the list? Family situation, need to go home",
             "Please send the list, I have a family emergency"):
    withdrawn("a request keyword cannot hide another clause: " + text, [event(False, text)])
for text in ("Please send the list", "Could you please send me the roster? Thanks",
             "Can I get my payslip?", "Who is working with me?", "Where should we park?"):
    remains("whole harmless request stays harmless: " + text, [event(False, text)])

for text in ("لا أستطيع الحضور", "OK لا أستطيع الحضور", "Can you send the list? لا أستطيع الحضور",
             "Sorry, can you send the list? لا أستطيع الحضور", "👍 我不能来", "Tomorrow لا أستطيع الحضور"):
    withdrawn("unreadable script after Yes requires review: " + text, [event(False, text)])
for text in ("Thánks", "Can you sénd the list?", "👍🏿", "🙂"):
    remains("accents and plain harmless emoji survive normalization: " + text, [event(False, text)])
for text in ("All okay for Example Hall? لا أستطيع الحضور", "All good for Example Hall? 我不能来"):
    got = heard([event(True, text, "18:00"), event(False, "Yes", "18:01")])
    check("unreadable outgoing clause cannot become a check-call question", got.get("response") != "Ok", got)
got = heard([event(True, "All okay for Example Hall?", "18:00"),
             event(True, "Hello 我不能来", "18:01"), event(False, "Yes", "18:02")])
check("an unreadable outgoing message cannot become a harmless chaser", got.get("response") != "Ok", got)

for kind, text in (("voice", "0:41"), ("media", "I'm sick, can't make it tonight. Photo")):
    withdrawn("later unreadable " + kind, [event(False, text, what=kind)], kind)

for text in ("Photo", "Timesheet photo", "On duty - photo", "Timesheet, sorry for sending it late. Photo"):
    remains("a later photo is not a cancellation: " + text, [event(False, text, what="media")])

for text in ("Sorry I need off 21st", "Sorry I can't work on 21 December",
             "Can I confirm my shift tomorrow? I am at Another Hall, not Example Hall.",
             "My exam is on August 6 next Wednesday, so I won't be available on that day",
             "I need Thursday and Friday off. I still need those off.",
             "Please don't give me long shifts from next week. I cannot work those hours.",
             "Sunday is a possible shift but I cannot work on that day.",
             "The person I worked with on Monday didn't help at all.",
             "I'm sorry but can I schedule an appointment for July?",
             "Sorry about that, can you send me the office address?"):
    remains("unrelated date/question context: " + text, [event(False, text)])

for text in ("The person on Monday didn't help. I'm sick tonight and can't come.",
             "I can't work. I can work Sunday instead.", "Sorry I can't make it, I can do next week",
             "I'm away, I can work Sunday instead", "Sunday is fine but I am sick",
             "I can do Sunday instead but I cannot come", "Sunday is fine but I can't make it, can do Monday"):
    withdrawn("future context cannot hide current cancellation: " + text, [event(False, text)])

# These continue the established answer, without allowing a new yes after a thank-you.
for text in ("Thanks", "Thank you mate", "Okay thanks", "Cheers", "\U0001f44d", "Are you in Example Hall?",
             "Are you there yet?", "Have you arrived?", "Will you be on time?", "Where are you?"):
    withdrawn("take-back after " + text,
              [event(True, text, "18:03"), event(False, "Not yet, going to be late", "18:04")])

for text in ("Can you work Sunday?", "Send your timesheet", "Are you at Another Hall?",
             "What is your address?", "Roster for next week", "Can you cover tonight at Another Hall?"):
    remains("new subject boundary: " + text,
            [event(True, text, "18:03"), event(False, "Sorry I can't", "18:04")])

for kind, text in (("voice", "0:45"), ("media", "Photo"), ("text", "\U0001f4c5")):
    remains("unknown controller content closes take-back: " + kind,
            [event(True, text, "18:03", kind), event(False, "Sorry I can't", "18:04")])

remains("later acknowledgment cannot reopen new subject",
        [event(True, "Send your timesheet", "18:03"), event(True, "Thanks", "18:04"),
         event(False, "Sorry I can't", "18:05")])
# 8 Nov review: a CLEAR cancellation still takes the yes back until 90 min after the start (20:00 here), even past
# three hours and even after we moved on to another subject; anything after that, and anything unclear after a new
# subject, does not.
withdrawn("a clear cancellation up to 90 min after the start takes the yes back", [event(False, "Sorry I can't make it", "21:03")])
remains("...but not after that", [event(False, "Sorry I can't make it", "21:45")])
withdrawn("our roster message between, then 'sick tonight': white (a real chat, 22/09)",
          [event(True, "Roster for next week", "18:30"), event(False, "Sorry I'm sick tonight, I can't come in", "19:00")])
withdrawn("our timesheet reminder between, then 'running late': white",
          [event(True, "Please send your timesheet", "18:30"), event(False, "I'm running late, 20 minutes late", "19:40")])
withdrawn("our Sunday offer between, then tonight's breakdown: white",
          [event(True, "Can you work Sunday?", "18:30"), event(False, "No sorry", "18:31"),
           event(False, "Actually can't make it tonight, car broke down", "19:10")])
remains("our Sunday offer answered 'No sorry, Sunday I'm away': tonight's yes stands",
        [event(True, "Can you work Sunday?", "18:30"), event(False, "No sorry, Sunday I'm away", "18:31")])
for text in ("Family situation, need to go home", "Ma sinto mal", "I've a temperature"):
    withdrawn("after a yes, a message it cannot read goes back to Lena: " + text, [event(False, text)])
withdrawn("late answer gets its own bounded take-back window",
          [event(True, "Thanks", "18:03"), event(False, "Sorry I can't make it", "21:01")])

# Both reactions and unprompted check-ins must use the same withdrawal logic.
got = heard([event(True, "All okay for Example Hall?", "18:00"),
             event(False, "", "18:02", "reaction", emoji="\U0001f44d", to="All okay for Example Hall?"),
             event(True, "Thanks", "18:03"), event(False, "Actually I'm sick, can someone cover?", "18:04")])
check("reaction can be taken back", got.get("taken_back") is True and got.get("response") == "", got)

for text, ours in (("Sorry not available", None), ("Sorry I can't, need cover?", "Thanks"),
                   ("Not yet, going to be late", "Are you in Example Hall?")):
    events = [event(False, "All good", "18:02")]
    if ours:
        events.append(event(True, ours, "18:03"))
    events.append(event(False, text, "18:04"))
    got = heard(events)
    check("check-in can be taken back: " + text,
          got.get("taken_back") is True and got.get("response") == "", got)

got = heard([event(False, "All good", "18:02"), event(True, "Can you work Sunday?", "18:03"),
             event(False, "Sorry I can't", "18:04")])
check("check-in keeps new-subject boundary", got.get("response") == "Ok", got)

check("tomorrow means this shift for an after-midnight start",
      not wa._another_day("Sorry I can't work tomorrow", DAY, DAY - datetime.timedelta(days=1)))
check("a future weekend still means another shift",
      wa._another_day("Sorry I can't work next weekend", DAY, DAY))

# Direction is evidence, not a default. A list preview can appear before outgoing ticks render.
def unknown(text="Yes", time="18:02", **extra):
    e = event(False, text, time, **extra)
    e.pop("ours")
    e["direction"] = "unknown"
    return e


def bubble(ours, text="Yes", time="18:02", **extra):
    e = event(ours, text, time, **extra)
    e.update(kind="bubble", date=DAY.strftime("%d/%m/%Y"))
    return e


question = event(True, "All okay for Example Hall?", "18:00")
for direction, ours in (("incoming", False), ("outgoing", True), ("unknown", None)):
    raw = unknown()
    raw["direction"] = direction
    clean = wa._clean(raw)
    check("direction enum retained: " + direction,
          clean.get("direction") == direction and clean.get("ours") is ours
          and (ours is not None or "ours" not in clean), clean)

for raw in (unknown(), dict(event(False, "Yes", "18:02"), direction="outgoing"),
            dict(event(True, "Yes", "18:02"), direction="incoming"),
            dict(event(False, "Yes", "18:02"), direction="invalid")):
    clean = wa._clean(raw)
    got = heard([question, clean])
    check("unknown/conflicting direction cannot affirm", clean.get("direction") == "unknown"
          and "ours" not in clean and got.get("response") != "Ok", got)

missing = event(False, "Yes", "18:02")
missing.pop("ours")
check("missing legacy direction is not incoming", heard([question, missing]).get("response") != "Ok")
check("legacy explicit incoming still works", heard([question, event(False, "Yes", "18:02")]).get("response") == "Ok")
check("bubble id is retained and capped", wa._clean(bubble(False, id="x" * 250)).get("id") == "x" * 200)

wrong = event(False, "Yes", "18:02")
corrected = dict(event(True, "Yes", "18:02"), seen=wrong["seen"] + 4000)
for observations in ([wrong, corrected], [corrected, wrong]):
    collapsed = wa._collapse(observations)
    got = heard([question, *observations])
    check("legacy line direction conflict is nonaffirming in either arrival order",
          len(collapsed) == 1 and wa._direction(collapsed[0]) == "unknown"
          and got.get("response") != "Ok" and got.get("direction_unknown"), (collapsed, got))

for observations in ([wrong, bubble(True)], [wrong, corrected, bubble(True)],
                     [unknown(), bubble(True)], [bubble(True), wrong]):
    collapsed = wa._collapse(observations)
    check("outgoing bubble overrides a false incoming preview",
          len(collapsed) == 1 and wa._direction(collapsed[0]) == "outgoing"
          and heard([question, *observations]).get("response") != "Ok", collapsed)

observations = [corrected, bubble(False)]
check("incoming bubble overrides a false outgoing preview",
      heard([question, *observations]).get("response") == "Ok", wa._collapse(observations))
observations = [wrong, corrected, bubble(True, id="out-1"), bubble(False, id="in-1")]
collapsed = wa._collapse(observations)
check("real identical incoming and outgoing bubbles are both retained",
      len(collapsed) == 2 and {wa._direction(e) for e in collapsed} == {"incoming", "outgoing"}
      and all(e["kind"] == "bubble" for e in collapsed), collapsed)
# Same-minute opposite messages have no proven order, but the real incoming bubble must not be discarded.
check("real incoming bubble survives duplicate preview suppression",
      any(e.get("id") == "in-1" and wa._direction(e) == "incoming" for e in collapsed), collapsed)
check("same physical bubble with contradictory direction needs review",
      all(wa._direction(e) == "unknown" for e in wa._collapse([bubble(False, id="same"), bubble(True, id="same")])))
check("distinct repeated incoming bubble ids survive",
      len(wa._collapse([bubble(False, id="one"), bubble(False, id="two")])) == 2)
check("legacy ID-less bubble and upgraded reader bubble are one message",
      len(wa._collapse([bubble(False), bubble(False, id="upgraded")])) == 1)
check("legacy ID-less copy never adds a third message to two real IDs",
      len(wa._collapse([bubble(False), bubble(False, id="one"), bubble(False, id="two")])) == 2)
second_call = dict(CALL, start=21 * 60, shift="21:00-close")
overlap = dict(LOG, calls=[CALL, second_call])
answers = wa.heard(overlap, [bubble(True, question["text"], "18:00"),
                            bubble(True, question["text"], "18:00", id="upgraded-question"),
                            bubble(False, "Yes", "18:02")])
check("a question re-read with an ID cannot answer two overlapping calls",
      sum(e.get("response") == "Ok" for e in answers.values()) == 1, answers)

fresh = event(False, "Yes", "18:02")
fresh["seen"] += 21000
collapsed = wa._collapse([fresh, bubble(False, id="in-fresh")])
check("one authoritative bubble keeps unambiguous live preview seconds",
      len(collapsed) == 1 and wa._at(collapsed[0]).second == 28
      and wa._direction(collapsed[0]) == "incoming" and collapsed[0].get("id") == "in-fresh", collapsed)
same_minute_question = event(True, "All okay for Example Hall?", "18:02")
check("unambiguous fresh seconds preserve reply ordering within a minute",
      heard([same_minute_question, fresh, bubble(False)]).get("response") == "Ok")
fresh_unknown = dict(fresh, direction="unknown")
fresh_unknown.pop("ours")
collapsed = wa._collapse([fresh_unknown, bubble(False)])
check("unknown preview cannot supply a bubble's within-minute clock",
      collapsed[0]["kind"] == "bubble" and wa._at(collapsed[0]).second == 0
      and heard([same_minute_question, fresh_unknown, bubble(False)]).get("response") != "Ok", collapsed)
check("opposite authoritative bubbles do not invent within-minute ordering",
      heard([same_minute_question, fresh, bubble(False), bubble(True)]).get("response") != "Ok")
check("unknown preview corrected by outgoing ticks is outgoing",
      wa._direction(wa._collapse([unknown(), corrected])[0]) == "outgoing")
check("unknown preview cannot corroborate legacy guessed incoming",
      wa._direction(wa._collapse([unknown(), wrong])[0]) == "unknown")
late_wrong = dict(wrong, seen=wrong["seen"] + 52000)
late_corrected = dict(corrected, seen=wrong["seen"] + 55000)
collapsed = wa._collapse([late_wrong, late_corrected, bubble(True)])
check("preview direction correction across a minute matches the original outgoing bubble",
      len(collapsed) == 1 and wa._direction(collapsed[0]) == "outgoing"
      and heard([question, late_wrong, late_corrected, bubble(True)]).get("response") != "Ok", collapsed)
collapsed = wa._collapse([late_wrong, late_corrected])
check("cross-minute legacy line-only conflict stays unknown",
      len(collapsed) == 1 and wa._direction(collapsed[0]) == "unknown", collapsed)
late_incoming = dict(wrong, seen=wrong["seen"] + 55000)
collapsed = wa._collapse([late_incoming, bubble(False)])
check("delayed preview cannot lend next-minute observation seconds to an old bubble",
      len(collapsed) == 1 and collapsed[0]["kind"] == "bubble" and wa._at(collapsed[0]).minute == 2, collapsed)
stale_unknown = dict(unknown("Earlier unrelated context", "18:03"), shown="18:00")
collapsed = wa._collapse([stale_unknown])
check("late-rendered unknown preview keeps its displayed message minute",
      collapsed[0].get("snapshot") and wa._at(collapsed[0]).strftime("%H:%M:%S") == "18:00:00", collapsed)
got = heard([question, bubble(False, "Yes", "18:02"), stale_unknown])
check("old unknown preview observed after a reply cannot falsely revoke the reply",
      got.get("response") == "Ok" and not got.get("taken_back"), got)
bad_shown = dict(wrong, shown="99:99")
check("invalid live preview display clock is not guessed for identity",
      wa._message_key(bad_shown)[-1] == wa._at(bad_shown).replace(second=0, microsecond=0))
check("emoji-only messages with different meaning do not collapse",
      len(wa._collapse([event(False, "\U0001f44d", "18:02"), event(False, "\U0001f44e", "18:02")])) == 2)
check("question and affirmation punctuation do not collapse",
      len(wa._collapse([event(False, "Ok?", "18:02"), event(False, "Ok", "18:02")])) == 2)

got = heard([question, unknown("Can you work Sunday?", "18:01"), event(False, "Yes", "18:02")])
check("unknown context cannot be skipped to accept a later yes",
      got.get("response") == "" and got.get("direction_unknown") is True, got)
got = heard([unknown("All okay for Example Hall?", "18:00"), event(False, "Yes", "18:02")])
check("unknown question is not ours and blocks a guessed unprompted yes", got.get("response") != "Ok", got)
got = heard([unknown("Yes", "18:02")])
check("unknown unprompted message needs review", got.get("response") == "" and got.get("direction_unknown"), got)

for text, what in (("Yes", "text"), ("Sorry I can't make it", "text"), ("0:45", "voice")):
    got = reply(unknown(text, "18:03", what=what))
    check("unknown new context invalidates confidence without interpreting content: " + what + text,
          got.get("response") == "" and got.get("direction_unknown") and got.get("taken_back"), got)
    described = wa.story(dict(CALL, heard=got))
    check("unknown context explanation never claims guard cancellation: " + what + text,
          "sender unverified" in described and "taken back" not in described and "sent a voice" not in described, described)

got = reply(unknown("Yes", "18:03"), bubble(True, "Yes", "18:03"))
check("authoritative outgoing bubble restores confidence after an unknown preview",
      got.get("response") == "Ok" and not got.get("direction_unknown"), got)
old_unknown = bubble(False, "Uncertain message", "18:02", direction="unknown")
got = reply(old_unknown)
check("minute-only uncertain bubble cannot claim it follows a same-minute known reply",
      not got.get("taken_back"), got)
got = reply(unknown("Yes", "21:03"))
check("unknown context does not invalidate an answer outside the bounded window", got.get("response") == "Ok", got)
got = reply(unknown("Yes", "18:03"))
check("unknown sender cell shows review rather than confirmation or cancellation",
      wa.cell_hint(dict(CALL, call_text="18:00", response="", heard=got)) == ("verify reply", "amber"))

# A transport prefix must never hide a later cancellation or a new question.
prefix = "Yes " * 75
long_negative = prefix + "but I cannot come tonight"
check("fixture demonstrates the unsafe affirmative-prefix boundary", len(prefix) == 300 and wa.judge(prefix, CALL) == "yes"
      and wa.judge(long_negative, CALL) == "no")
for raw in (event(False, long_negative, "18:02"), event(False, prefix, "18:02", truncated=True),
            event(False, prefix, "18:02"), event(False, long_negative, "18:02", truncated=False)):
    cleaned = wa._clean(raw)
    got = heard([event(True, "All okay for Example Hall?", "18:00"), cleaned])
    check("receiver clipping or legacy boundary stays manual, never affirmative", cleaned.get("truncated") is True
          and got.get("response") == "" and got.get("text_truncated"), got)
for raw in (event(False, "Yes", truncated=True), event(False, "Yes", truncated="false"),
            event(False, "👍" * 150), event(False, "👍" * 151, truncated=False)):
    got = heard([wa._clean(raw)])
    check("short flagged and UTF16-limited check-ins require full text", got.get("response") != "Ok" and got.get("text_truncated"), got)
complete_boundary = wa._clean(event(False, prefix, truncated=False))
check("new explicit full-text boundary remains usable", complete_boundary.get("truncated") is False
      and heard([complete_boundary]).get("response") == "Ok")
question_prefix = "All okay for Example Hall? " + "Yes " * 80
got = heard([wa._clean(event(True, question_prefix, "18:00")), event(False, "Yes", "18:02")])
check("truncated outgoing question cannot establish confirmation context", got.get("response") != "Ok", got)
got = heard([event(True, "All okay for Example Hall?", "18:00"),
             event(True, "All okay for Example Hall?", "18:01", truncated=True), event(False, "Yes", "18:02")])
check("truncated outgoing question is not treated as a harmless chaser", got.get("response") != "Ok", got)
got = heard([event(True, "All okay for Example Hall?", "18:00"),
             event(False, "", "18:02", what="reaction", emoji="👍", to="All okay for Example Hall?", truncated=True)])
check("truncated reaction metadata cannot confirm", got.get("response") != "Ok" and got.get("text_truncated"), got)
got = reply(event(False, "Yes", "18:03", truncated=True))
check("later truncated text removes automatic confidence without guessing its ending", got.get("response") == ""
      and got.get("taken_back") and got.get("text_truncated"), got)
description = wa.story(dict(CALL, heard=got))
check("truncated hover asks for full message without claiming cancellation", "full message" in description and "taken back" not in description)
check("truncated response cell has a concise action", wa.cell_hint(dict(CALL, response="", call_text="18:00", heard=got)) == ("read full text", "amber"))
old_prefix = bubble(False, "Yes", "18:02", id="same-physical-message", truncated=False)
new_prefix = bubble(False, prefix, "18:02", id="same-physical-message", truncated=True)
got = heard([event(True, "All okay for Example Hall?", "18:00"), old_prefix, new_prefix])
check("expanded same-ID message cannot keep its earlier affirmative prefix", got.get("response") != "Ok" and got.get("text_truncated"), got)
got = heard([event(True, "All okay for Example Hall?", "18:00"),
             event(False, "Yes", "18:02", truncated=False), bubble(False, "Yes", "18:02", truncated=True)])
check("precise preview seconds cannot erase a bubble's incomplete-text flag", got.get("response") != "Ok" and got.get("text_truncated"), got)

# A new matching question renews its own reply window, not the original call time.
qtext = "All okay for Example Hall?"
got = heard([event(True, qtext, "17:00"), event(True, qtext, "20:01"), event(False, "Yes", "20:02")])
check("later same-call question renews expired reply window", got.get("response") == "Ok"
      and got.get("time") == "17:00" and got.get("at") == "20:02", got)
got = heard([event(True, qtext, "18:00"), event(False, "0:45", "18:01", what="voice"),
             event(True, qtext, "19:00"), event(False, "Yes", "19:01")])
check("an explicit renewed question can resolve an earlier voice reply", got.get("response") == "Ok"
      and got.get("time") == "18:00", got)
got = heard([event(True, qtext, "17:00"), event(False, "Yes", "17:01"),
             event(True, qtext, "20:01"), event(False, "No", "20:02")])
check("renewed negative revokes old yes after its original withdrawal window", got.get("response") == ""
      and got.get("taken_back") and got.get("said") == "No" and got.get("time") == "17:00", got)
got = heard([event(True, qtext, "17:00"), event(False, "Yes", "17:01"), event(True, qtext, "20:01")])
check("unanswered renewed question preserves established yes", got.get("response") == "Ok"
      and got.get("at") == "17:01", got)
got = heard([event(True, qtext, "17:00"), event(True, qtext, "20:01"),
             event(True, "Can you work Sunday?", "20:02"), event(False, "Yes", "20:03")])
check("unrelated outgoing context closes a renewed window", got.get("response") != "Ok", got)
got = heard([event(True, qtext, "17:00"), event(True, qtext, "20:01"), event(False, "Yes", "20:02"),
             event(False, "Sorry I cannot come tonight", "20:03")])
check("renewed affirmative still permits subsequent withdrawal", got.get("response") == "" and got.get("taken_back"), got)
got = heard([event(True, qtext, "17:00"), event(True, qtext, "20:01"),
             unknown("Can you cover Sunday?", "20:02"), event(False, "Yes", "20:03")])
check("unknown context after renewal cannot be skipped", got.get("response") != "Ok" and got.get("direction_unknown"), got)
got = heard([event(True, qtext, "17:00"), event(True, "All okay for Different Hall?", "20:01"), event(False, "Yes", "20:02")])
check("another venue's new question does not renew this call", got.get("response") != "Ok", got)
got = heard([event(True, qtext, "17:00"), event(False, "Yes", "17:01"),
             event(True, qtext, "20:01"), event(False, "Yes", "20:02", truncated=True)])
check("renewed incomplete text removes old automatic confidence", got.get("response") == ""
      and got.get("taken_back") and got.get("text_truncated"), got)
other_call = dict(CALL, site="Second Hall", venue="Second Hall", start=20 * 60 + 30, shift="20:30-close")
two_calls = dict(LOG, calls=[CALL, other_call])
got = wa.heard(two_calls, [event(True, "All good?", "18:00"), event(True, "All good?", "18:01"), event(False, "Yes", "18:02")])
check("one reply cannot confirm two nearby assignments", sum(v.get("response") == "Ok" for v in got.values()) == 1, got)
shared = got.get(calllog.edit_key(other_call), {})
check("shared reply review explains assignment ambiguity rather than cancellation", shared.get("shared_reply")
      and "another assignment" in wa.story(dict(other_call, heard=shared)), shared)
got = wa.heard(two_calls, [event(True, qtext, "18:00"), event(False, "Yes", "18:01"),
                          event(True, "All okay for Second Hall?", "18:02"), event(False, "Yes", "18:03")])
check("distinct question-reply pairs still confirm distinct assignments", sum(v.get("response") == "Ok" for v in got.values()) == 2, got)
# 8 Nov review: one man, two shifts. A clear cancellation of the night withdraws both; one naming a venue, only that one.
both = [event(True, qtext, "18:00"), event(False, "Yes", "18:01"),
        event(True, "All okay for Second Hall?", "19:30"), event(False, "Yes", "19:31")]
got = wa.heard(two_calls, both + [event(False, "Sorry I'm sick, can't work tonight at all", "19:50")])
check("two shifts, then 'sick, can't work tonight at all': both white", all(v.get("response") == "" and v.get("taken_back")
      for v in got.values()) and len(got) == 2, got)
got = wa.heard(two_calls, both + [event(False, "Sorry, I can't do Second Hall tonight", "19:50")])
check("...'can't do Second Hall': only that shift white, the other stays green",
      got.get(KEY, {}).get("response") == "Ok" and got.get(calllog.edit_key(other_call), {}).get("response") == "", got)

# Equal clock minutes need committed capture membership AND a shared DOM view, not an inferred second.
READ_ID, VIEW_ID = "d" * 32, "e" * 32
def millis(clock):
    return int(datetime.datetime.combine(DAY, datetime.time.fromisoformat(clock)).timestamp() * 1000)
READ_COMMITS = {READ_ID: dict(chat=CALL["name"], since=millis("17:00"), created=millis("22:00"), completed=millis("22:01"))}
pair_q = dict(bubble(True, qtext, "18:00", id="question-id", i=8, read_id=READ_ID, view_id=VIEW_ID), seen=millis("22:00"))
pair_r = dict(bubble(False, "Yes", "18:00", id="reply-id", i=9, read_id=READ_ID, view_id=VIEW_ID), seen=millis("22:00"))
def paired(items, commits=READ_COMMITS):
    return wa.heard(LOG, items, read_commits=commits).get(KEY, {})
got = paired([pair_q, pair_r])
check("two committed same-view messages resolve a same-minute reply", got.get("response") == "Ok"
      and got.get("at") == "18:00", got)
check("transport retains valid stable-ID view proof", wa._clean(pair_q).get("view_id") == VIEW_ID)
for change in ({"view_id": "x" * 32}, {"view_id": "e" * 31}, {"i": True}, {"i": 8.5}, {"i": -1}, {"i": 10001}, {"id": ""}):
    check("invalid DOM proof cannot survive cleaning: " + str(change), "view_id" not in wa._clean(dict(pair_q, **change)))
for label, q, r, receipts in (
        ("no complete receipt", pair_q, pair_r, {}),
        ("different views", pair_q, dict(pair_r, view_id="f" * 32), READ_COMMITS),
        ("no view proof", {k: v for k, v in pair_q.items() if k != "view_id"}, pair_r, READ_COMMITS),
        ("no stable reply ID", pair_q, {k: v for k, v in pair_r.items() if k != "id"}, READ_COMMITS),
        ("same physical ID", pair_q, dict(pair_r, id=pair_q["id"]), READ_COMMITS),
        ("reversed DOM rows", pair_q, dict(pair_r, i=7), READ_COMMITS),
        ("same DOM row", pair_q, dict(pair_r, i=8), READ_COMMITS),
        ("boolean row", pair_q, dict(pair_r, i=True), READ_COMMITS),
        ("future observation", pair_q, dict(pair_r, seen=millis("22:02")), READ_COMMITS),
        ("passive bubbles", {k: v for k, v in pair_q.items() if k != "read_id"},
         {k: v for k, v in pair_r.items() if k != "read_id"}, READ_COMMITS),
        ("different committed reads", pair_q, dict(pair_r, read_id="a" * 32),
         dict(READ_COMMITS, **{"a" * 32: READ_COMMITS[READ_ID]}))):
    got = paired([q, r], receipts)
    check("same-minute pair stays manual with " + label, got.get("response") != "Ok", got)
for extra in (bubble(False, "No", "18:00", id="negative-id"),
              bubble(True, "Can you work Sunday?", "18:00", id="new-topic"),
              bubble(False, "0:25", "18:00", id="voice-id", what="voice"),
              dict(bubble(False, "Unresolved", "18:00", id="partial-id"), read_id="a" * 32)):
    got = paired([pair_q, pair_r, extra])
    check("any third same-minute context blocks inferred pair order: " + extra["text"], got.get("response") != "Ok", got)
got = paired([pair_q, dict(pair_r, text="No")])
check("same-view ordering never turns a negative into confirmation", got.get("response") == "" and got.get("said") == "No", got)
got = paired([pair_q, pair_r, bubble(False, "I cannot come tonight", "18:01", id="withdrawal")])
check("following-minute negative withdraws a proven same-minute yes", got.get("response") == "" and got.get("taken_back"), got)
old_q = {k: v for k, v in pair_q.items() if k not in ("view_id", "read_id")}
old_r = {k: v for k, v in pair_r.items() if k not in ("view_id", "read_id")}
got = paired([old_q, old_r, pair_q, pair_r])
check("older bubble copies cannot erase a later committed view proof", got.get("response") == "Ok", got)
got = paired([event(True, qtext, "18:00"), event(False, "Yes", "18:00"), pair_q, pair_r])
check("collapsed precise previews still use original committed bubble proof", got.get("response") == "Ok", got)
got = paired([pair_q, pair_r, dict(pair_r, text="No")])
check("edited same-ID content prevents a two-message proof", got.get("response") != "Ok", got)

check("today qualifies a plain current-shift acknowledgement", wa.judge("Yes I’m in today", CALL) == "yes")
for text in ("Yes I’m in today but I cannot come", "Maybe I’m in today", "Yes I’m in tomorrow", "Am I in today?", "Today"):
    check("today does not bypass negative, doubt or other-day meaning: " + text, wa.judge(text, CALL) != "yes")
got = heard([event(True, qtext, "18:00"), event(True, "Can you work Sunday?", "18:01"), event(False, "Yes I’m in today", "18:02")])
check("today acknowledgement still requires the actual check-call context", got.get("response") != "Ok", got)

got = heard([event(True, qtext, "18:00"), event(True, "All okay for Different Hall?", "19:00"), event(False, "Yes", "19:01")])
check("similarly named venue closes the original reply window too", got.get("response") != "Ok", got)
got = heard([event(True, qtext, "18:00"), event(False, "Yes", "18:01"),
             event(True, "All okay for Different Hall?", "19:00"), event(False, "No", "19:01")])
check("another venue's negative does not withdraw this confirmed call", got.get("response") == "Ok", got)
got = heard([event(True, qtext, "18:00"), event(False, "Yes", "18:01"),
             event(True, qtext, "19:00"), event(False, "No", "19:01")])
check("same named question retains the withdrawal context", got.get("response") == "" and got.get("taken_back"), got)
same_yes = bubble(False, "Yes", "18:01", id="same-minute-yes", i=1)
same_no = bubble(False, "Sorry I cannot come tonight", "18:01", id="same-minute-no", i=2)
for message in (same_no, bubble(False, "Am I working today?", "18:01", id="same-minute-doubt", i=2),
                bubble(False, "0:30", "18:01", what="voice", id="same-minute-voice", i=2),
                bubble(False, "Yes I’m in", "18:01", truncated=True, id="same-minute-prefix", i=2)):
    got = heard([event(True, qtext, "18:00"), same_yes, message])
    check("distinct same-minute trouble cannot follow a guessed Yes order: " + message["what"] + message["text"],
          got.get("response") == "" and got.get("taken_back") and got.get("same_minute_conflict"), got)
    check("same-minute review never claims chronological cancellation: " + message["text"],
          "taken back" not in wa.story(dict(CALL, heard=got)))
got = heard([event(True, qtext, "18:00"), dict(same_no, i=1), dict(same_yes, i=2)])
check("negative-first same-minute pair also stays manual", got.get("response") != "Ok", got)
got = heard([same_yes, same_no])
check("spontaneous check-in also needs same-minute conflict review", got.get("response") != "Ok"
      and got.get("same_minute_conflict"), got)
got = heard([event(True, qtext, "18:00"), same_yes, dict(same_yes, seen=same_yes["seen"] + 500),
             bubble(False, "Yes", "18:01", id="second-positive", i=2)])
check("repeated positive copies do not invent a same-minute conflict", got.get("response") == "Ok", got)
got = heard([event(True, qtext, "18:00"), same_yes,
             bubble(False, "I cannot work Sunday", "18:01", id="other-day", i=2)])
check("same-minute future-day request does not revoke this shift", got.get("response") == "Ok", got)
got = heard([event(True, qtext, "18:00"), same_yes, unknown("Unverified content", "18:01")])
check("unknown same-minute content stays review without assigning sender/order", got.get("response") != "Ok", got)
alias_call = dict(CALL, venue="K9 / Red Door", site="K9 / Red Door")
alias_log = dict(LOG, calls=[alias_call])
got = wa.heard(alias_log, [event(True, "All okay for K9?", "18:00"), event(False, "Yes", "18:01"),
                         event(True, "All okay for K9?", "19:00"),
                         event(False, "I cannot come tonight", "19:01", read_id="a" * 32)]).get(calllog.edit_key(alias_call), {})
check("same alias reminder cannot hide a later incomplete negative", got.get("response") == ""
      and got.get("read_incomplete") and got.get("taken_back"), got)
got = heard([event(True, qtext, "18:00"), event(False, "Yes", "18:01"),
             bubble(False, "I cannot come tonight", "18:01", id="partial-same-minute", read_id="a" * 32)])
check("precise preview seconds cannot hide same-minute incomplete conflicting context", got.get("response") == ""
      and got.get("read_incomplete") and got.get("taken_back"), got)

# The user verified this exact WhatsApp loading notice. Later complete coverage can
# replace it only with unique stable incoming text and no competing minute context.
later_answer = dict(pair_r, shown="18:01")
got = paired([pair_q, later_answer])
check("complete ordinary question and later-minute reply still confirm", got.get("response") == "Ok", got)
loading_preview = unknown("Waiting for this message. Check your phone.", "18:01")
got = paired([pair_q, loading_preview, later_answer])
check("unique later complete incoming text replaces the exact stale loading notice", got.get("response") == "Ok"
      and not got.get("direction_unknown"), got)
got = paired([pair_q, unknown("I cannot come tonight", "18:01"), later_answer])
check("clock-matched known reply cannot erase distinct unknown cancellation content", got.get("response") != "Ok", got)

for label, replacement, notice, receipts, extra in (
        ("missing complete receipt", later_answer, loading_preview, {}, []),
        ("missing stable ID", {k: v for k, v in later_answer.items() if k != "id"}, loading_preview, READ_COMMITS, []),
        ("outgoing bubble", dict(later_answer, ours=True), loading_preview, READ_COMMITS, []),
        ("truncated incoming", dict(later_answer, truncated=True), loading_preview, READ_COMMITS, []),
        ("stale capture observation", dict(later_answer, seen=millis("21:59")), loading_preview, READ_COMMITS, []),
        ("fresh unresolved notice", later_answer, dict(loading_preview, seen=millis("22:00:01")), READ_COMMITS, []),
        ("another incoming ID", later_answer, loading_preview, READ_COMMITS, [dict(later_answer, id="other-yes")]),
        ("new unknown cancellation", later_answer, loading_preview, READ_COMMITS, [unknown("I cannot come tonight", "18:01")]),
        ("partial competing message", later_answer, loading_preview, READ_COMMITS, [dict(later_answer, id="partial-no", text="No", read_id="a" * 32)]),
        ("notice is actual bubble content", later_answer, dict(loading_preview, kind="bubble", date=DAY.strftime("%d/%m/%Y"), id="real-notice-text"), READ_COMMITS, []),
        ("similar unsupported wording", later_answer, dict(loading_preview, text="Waiting for this message. Check your phone. I cannot come"), READ_COMMITS, []),
        ("different message minute", later_answer, unknown(loading_preview["text"], "18:02"), READ_COMMITS, [])):
    got = paired([pair_q, notice, replacement, *extra], receipts)
    check("loading notice is not retired with " + label, got.get("response") != "Ok", got)
got = paired([pair_q, loading_preview, dict(later_answer, text="No")])
check("resolved negative replacement remains a negative reply", got.get("response") == "" and got.get("said") == "No"
      and not got.get("direction_unknown"), got)

trailing_media = dict(bubble(False, "", "18:00", what="media", id="captionless-sticker", i=10,
                             read_id=READ_ID, view_id=VIEW_ID), seen=millis("22:00"))
got = paired([pair_q, pair_r, trailing_media])
check("proven captionless incoming media after a same-minute Yes does not erase text confirmation", got.get("response") == "Ok", got)
got = paired([pair_q, pair_r, trailing_media, dict(trailing_media)])
check("duplicate media observations preserve the same physical ordering proof", got.get("response") == "Ok", got)
got = paired([pair_q, pair_r, trailing_media, dict(trailing_media, id="second-media", i=11)])
check("multiple proven captionless media after Yes remain nonverbal context", got.get("response") == "Ok", got)
for label, change in (("media before answer", {"i": 7}), ("same row", {"i": 9}), ("different snapshot", {"view_id": "a" * 32}),
                      ("uncommitted read", {"read_id": "a" * 32}), ("unknown sender", {"direction": "unknown"}),
                      ("outgoing media", {"ours": True}), ("voice note", {"what": "voice"}),
                      ("negative caption", {"text": "I cannot come tonight"}), ("ordinary caption", {"text": "Sticker"}),
                      ("truncated caption", {"truncated": True}), ("missing identity", {"id": ""})):
    got = paired([pair_q, pair_r, dict(trailing_media, **change)])
    check("same-minute media cannot be ignored with " + label, got.get("response") != "Ok", got)
got = paired([pair_q, trailing_media])
check("a sticker alone is never affirmative text", got.get("response") != "Ok", got)
got = paired([pair_q, dict(pair_r, text="No"), trailing_media])
check("a later sticker cannot override an explicit negative", got.get("response") != "Ok", got)

typed_sticker = dict(trailing_media, media_type="sticker")
sticker_preview = unknown("Sticker", "18:00", what="media")
got = paired([pair_q, pair_r, typed_sticker, sticker_preview])
check("unique complete typed sticker resolves its old sidebar descriptor", got.get("response") == "Ok", got)
for label, sticker, preview, extra in (
        ("generic photo", trailing_media, sticker_preview, []),
        ("partial sticker", dict(typed_sticker, read_id="a" * 32), sticker_preview, []),
        ("missing sticker identity", dict(typed_sticker, id=""), sticker_preview, []),
        ("unknown sticker sender", dict(typed_sticker, direction="unknown"), sticker_preview, []),
        ("different preview kind", typed_sticker, dict(sticker_preview, what="text"), []),
        ("newer unresolved preview", typed_sticker, dict(sticker_preview, seen=millis("22:02")), []),
        ("different unknown media", typed_sticker, dict(sticker_preview, text="Photo"), []),
        ("two possible stickers", typed_sticker, sticker_preview, [dict(typed_sticker, id="other-sticker", i=11)])):
    got = paired([pair_q, pair_r, sticker, preview, *extra])
    check("typed sticker retirement refuses " + label, got.get("response") != "Ok", got)
got = paired([pair_q, dict(typed_sticker, shown="18:01")])
check("sticker-only reply is named honestly in cell and hover", got.get("response") != "Ok"
      and wa.cell_hint(dict(CALL, call_text="18:00", response="", heard=got)) == ("sticker", "amber")
      and "sent a sticker" in wa.story(dict(CALL, heard=got)) and "photo" not in wa.story(dict(CALL, heard=got)), got)

# 8 Nov: message times are cached (the analysis asked for each ~10x). Same answers as uncached, never mixed up.
samples = [event(False, "Yes", "18:02"), bubble(False, "Yes", "18:02"), dict(event(False, "Yes", "18:02"), snapshot=True),
           {"kind": "bubble", "seen": 1}, {"seen": 5}, None, dict(event(False, "Yes", "18:02"), seen=[1])]
check("cached message times equal the uncached ones (normal, broken, odd)",
      all(wa._at(s) == wa._at_uncached(s) for s in samples) and all(wa._at(s) == wa._at_uncached(s) for s in samples))
check("two different messages never share a cached time", wa._at(event(False, "Yes", "18:02")) != wa._at(bubble(False, "Yes", "18:03")))

# Attached reactions are snapshots on an exact parent, never messages with an invented clock.
def reaction_item(emoji="👍", actor="contact", **change):
    value = dict(parent_id=pair_q["id"], emoji=emoji, actor=actor,
                 actor_name=CALL["name"] if actor == "contact" else "You", at=None)
    value.update(change)
    return value
def reaction_parent(items=None, complete=True, **change):
    value = dict(pair_q, group=False, reactions=dict(complete=complete, items=[reaction_item()] if items is None else items))
    value.update(change)
    return value
react_q = reaction_parent()
got = paired([react_q])
check("verified contact reaction on exact committed question confirms", got.get("response") == "Ok"
      and got.get("attached_reaction") and got.get("how") == "reaction", got)
check("attached reaction has no fabricated occurrence clock", "at" not in got and got.get("time") == "18:00"
      and "reaction time" in wa.story(dict(CALL, heard=got)) and "at 18:00" not in wa.story(dict(CALL, heard=got)), got)
check("transport preserves nested null reaction time", wa._clean(react_q).get("reactions") == react_q["reactions"])
for emoji in ("👍", "👍🏿", "👍️", "👌", "✅", "✔️"):
    check("supported single affirmative reaction: " + emoji, paired([reaction_parent([reaction_item(emoji)])]).get("response") == "Ok")
for emoji in ("👎", "😆", "👍👎", "yes 👍", "👍?", "👍👍"):
    check("other or mixed reaction cannot affirm: " + emoji, paired([reaction_parent([reaction_item(emoji)])]).get("response") != "Ok")
for label, parent, receipts in (
        ("self-only", reaction_parent([reaction_item(actor="self")]), READ_COMMITS),
        ("uncommitted", react_q, {}),
        ("incomplete author list", reaction_parent(complete=False), READ_COMMITS),
        ("wrong parent", reaction_parent([reaction_item(parent_id="different-parent")]), READ_COMMITS),
        ("wrong contact", reaction_parent([reaction_item(actor_name="Another Person")]), READ_COMMITS),
        ("unknown reactor", reaction_parent([reaction_item(actor="unknown")]), READ_COMMITS),
        ("invented clock", reaction_parent([reaction_item(at=millis("18:01"))]), READ_COMMITS),
        ("group", reaction_parent(group=True), READ_COMMITS),
        ("not a question", reaction_parent(text="Can you work Sunday?"), READ_COMMITS),
        ("another venue", reaction_parent(text="All okay for The Library?"), READ_COMMITS),
        ("another date", reaction_parent(date="06/11/2026"), READ_COMMITS),
        ("repeated actor", reaction_parent([reaction_item(), reaction_item("👎")]), READ_COMMITS)):
    got = paired([parent], receipts)
    check("attached reaction rejects " + label, got.get("response") != "Ok", got)
    if label in ("wrong parent", "wrong contact", "unknown reactor", "invented clock", "repeated actor"):
        check("malformed snapshot remains explicit uncertainty after cleaning: " + label,
              wa._clean(parent).get("reactions") == {"complete": False, "items": []})
# 8 Nov review: a thumbs-up put on an ignored question hours later used to count. Reads of the question with NO
# reaction prove when it was still missing; one still missing after the reply window (21:00 here) is too late.
def snapshot_at(clock, items, rid):
    return dict(pair_q, group=False, read_id=rid, seen=millis(clock), reactions=dict(complete=True, items=items))
def receipts_for(*pairs):
    return {rid: dict(chat=CALL["name"], since=millis("17:00"), created=millis(c), completed=millis(c) + 60000) for rid, c in pairs}
E1, E2, P1 = "1" * 32, "2" * 32, "3" * 32
got = paired([snapshot_at("18:30", [], E1), snapshot_at("22:30", [], E2), snapshot_at("23:40", [reaction_item()], P1)],
             receipts_for((E1, "18:30"), (E2, "22:30"), (P1, "23:40")))
check("a thumbs-up proven to arrive after the reply window: white, marked late", got.get("response") == ""
      and got.get("reaction_late") and got.get("reaction_review"), got)
check("...the cell and hover say it was late", wa.cell_hint(dict(CALL, call_text="18:00", response="", heard=got)) == ("late thumbs-up", "amber")
      and "after the reply window" in wa.story(dict(CALL, heard=got)))
got = paired([snapshot_at("18:30", [], E1), snapshot_at("18:45", [reaction_item()], P1)],
             receipts_for((E1, "18:30"), (P1, "18:45")))
check("...one seen within the window still confirms", got.get("response") == "Ok" and got.get("attached_reaction"), got)
got = paired([snapshot_at("23:40", [reaction_item()], P1)], receipts_for((P1, "23:40")))
check("...and with no read proving it was missing, its time is unknown and it still confirms (unchanged, on purpose)",
      got.get("response") == "Ok", got)

got = paired([snapshot_at("18:30", [reaction_item()], E1), snapshot_at("22:00", [], E2),
              snapshot_at("23:00", [reaction_item()], P1)],
             receipts_for((E1, "18:30"), (E2, "22:00"), (P1, "23:00")))
check("a removed reaction re-added late cannot borrow its first episode's timing", got.get("response") == ""
      and got.get("reaction_late") and got.get("taken_back"), got)
got = paired([snapshot_at("18:10", [reaction_item()], E1), snapshot_at("18:20", [], E2),
              snapshot_at("18:30", [reaction_item()], P1)],
             receipts_for((E1, "18:10"), (E2, "18:20"), (P1, "18:30")))
check("reaction removed and re-added within the window still confirms", got.get("response") == "Ok", got)
got = paired([snapshot_at("18:30", [reaction_item()], E1), snapshot_at("22:00", [], E2),
              snapshot_at("23:00", [reaction_item()], P1), dict(pair_r, shown="18:01")],
             dict(READ_COMMITS, **receipts_for((E1, "18:30"), (E2, "22:00"), (P1, "23:00"))))
check("late reaction re-addition does not erase an independent timely text Yes", got.get("response") == "Ok"
      and got.get("how") == "reply", got)
got = paired([reaction_parent([reaction_item(), reaction_item(actor="self")])])
check("our reaction does not erase the independently verified contact reaction", got.get("response") == "Ok", got)
for later in (event(False, "No", "18:01"), event(False, "0:30", "18:01", what="voice"),
              event(True, "Can you work Sunday?", "18:01"), event(True, qtext, "19:00"),
              unknown("Unverified message", "18:01"), event(False, "No", "22:00", read_id="a" * 32)):
    got = paired([react_q, later])
    check("unknown reaction time cannot overrule later context: " + later["text"], got.get("response") != "Ok", got)
new_read = "c" * 32
new_commits = dict(READ_COMMITS, **{new_read: dict(READ_COMMITS[READ_ID], created=millis("22:02"), completed=millis("22:03"))})
new_q = dict(react_q, read_id=new_read, seen=millis("22:02"))
removed_q = dict(new_q, reactions={"complete": True, "items": []})
got = paired([react_q, removed_q], new_commits)
check("newest complete empty snapshot removes the old reaction", got.get("response") == ""
      and got.get("reaction_removed") and got.get("taken_back"), got)
check("removed reaction hint explains actual removal without an invented reply time",
      wa.cell_hint(dict(CALL, call_text="18:00", response="", heard=got)) == ("verify reaction", "amber")
      and "no longer present" in wa.story(dict(CALL, heard=got)) and "at 18:00" not in wa.story(dict(CALL, heard=got)))
for label, newest, receipts in (
        ("partial explicit snapshot", dict(new_q, reactions={"complete": False, "items": []}), READ_COMMITS),
        ("partial positive snapshot", new_q, READ_COMMITS),
        ("new complete legacy capture", {k: v for k, v in new_q.items() if k != "reactions"}, new_commits),
        ("contact negative reaction", dict(new_q, reactions={"complete": True, "items": [reaction_item("👎")]}), new_commits),
        ("unknown actor metadata", dict(new_q, reactions={"complete": True, "items": [reaction_item(actor="unknown")]}), new_commits)):
    got = paired([react_q, newest], receipts)
    check("newer uncertain reaction snapshot cannot retain stale positive: " + label, got.get("response") != "Ok"
          and got.get("reaction_review") and got.get("taken_back"), got)
passive_q = {k: v for k, v in new_q.items() if k not in ("reactions", "read_id")}
got = paired([react_q, passive_q])
check("ordinary passive metadata omission does not erase proven reaction", got.get("response") == "Ok", got)
got = paired([react_q, removed_q, dict(pair_r, shown="18:01")], new_commits)
check("reaction removal does not erase a valid independent typed Yes", got.get("response") == "Ok"
      and got.get("how") == "reply" and not got.get("reaction_review"), got)
got = paired([react_q, dict(new_q, text="Can you work Sunday?")], new_commits)
check("edited parent body cannot lend its reaction to the old question", got.get("response") != "Ok", got)
old_question_preview = event(True, qtext, "18:00")
partial_question_preview = dict(unknown(qtext, "18:00"), read_id="a" * 32)
got = paired([old_question_preview, react_q, partial_question_preview])
check("precise question preview preserves raw bubble proof against duplicate partial copies", got.get("response") == "Ok"
      and got.get("attached_reaction"), got)
got = paired([old_question_preview, react_q, dict(partial_question_preview, text="I cannot come tonight")])
check("unrelated unknown content still blocks beside a precise question preview", got.get("response") != "Ok", got)

failed = len(checks) - sum(checks)
print("%d of %d checks passed" % (sum(checks), len(checks)))
raise SystemExit(1 if failed else 0)
