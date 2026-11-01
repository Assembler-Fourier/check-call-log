"""The patrol store, the per-person rounds and the shift-log line ids, checked without the window.
Hand corrections go to a scratch folder; the saved roster copy is only read."""
import datetime
import json
import os
import tempfile
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import calllog as c  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(tempfile.gettempdir(), "ccl-tests")         # throwaway output; the checks themselves live in tool\tests
c.EDITS = os.path.join(WORK, "edits-t10")
shutil.rmtree(c.EDITS, ignore_errors=True)
results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print("%s  %s  %s" % ("PASS" if ok else "FAIL", label, str(detail)[:260]))


D = datetime.date
at = lambda text: datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")
roster = c.Roster()
show = lambda rows: [(r["id"], r["start_text"], r["finish_text"], r["area"], r["notes"]) for r in rows]

# ---- the usual rounds ----
old = c.usual_patrols(D(2026, 10, 3), "Lena Drayton", 1080, 1800)
check("shifts before 11 Oct keep the first rule (on the hour-ish, 22 / 01-02 / 04-05)",
      len(old) == 3 and old[0]["start"] // 60 == 22 and old[1]["start"] // 60 in (25, 26) and old[2]["start"] // 60 in (28, 29), old)
print("\n  usual rounds from 11 Oct on, by person:")
seen, bad = {}, []
for who, a, b in (("Lena Drayton", 1080, 1800), ("Lena Drayton", 990, 1800), ("Henry Marlow", 360, 1080), ("Henry Marlow", 990, 1800),
                  ("Soren Rasmussen", 360, 1080), ("Basil Jankowski", 990, 1800)):
    days = []
    for n in range(28):
        day = D(2026, 10, 11) + datetime.timedelta(days=n)
        rows = c.usual_patrols(day, who, a, b)
        days.append(tuple((r["start"], r["finish"]) for r in rows))
        for i, r in enumerate(rows):
            if not (a + 30 <= r["start"] and r["finish"] <= b - 10 and 10 <= r["finish"] - r["start"] <= 30):
                bad.append((who, day, r))
            if i and r["start"] < rows[i - 1]["finish"] + 20:
                bad.append((who, day, "too close", r))
        if len(rows) != 3:
            bad.append((who, day, "rounds", len(rows)))
    seen[(who, a)] = days
    print("   %-15s %s-%s  " % (who, c.fmt(a).zfill(5), c.fmt(b).zfill(5)) + "   ".join(
        " ".join("%s-%s" % (c.fmt(s).zfill(5), c.fmt(f).zfill(5)) for s, f in d) for d in days[:3]))
check("every round is inside the person's own shift, 10-30 minutes long, three a shift", not bad, bad[:3])
check("the same person and day always give the same times", c.usual_patrols(D(2026, 10, 17), "Lena Drayton", 1080, 1800) == c.usual_patrols(D(2026, 10, 17), "Lena Drayton", 1080, 1800))
check("the times change from day to day", all(len(set(d)) >= 20 for d in seen.values()), {k: len(set(d)) for k, d in seen.items()})
check("two people on the same shift on the same days do not get the same times",
      sum(1 for x, y in zip(seen[("Henry Marlow", 990)], seen[("Basil Jankowski", 990)]) if x == y) == 0)
lean = lambda days: sum(d[0][0] for d in days) / float(len(days))
print("   average first round:  " + ",  ".join("%s %s" % (k[0].split()[0], c.fmt(int(lean(d))).zfill(5)) for k, d in seen.items()))
odd = c.usual_patrols(D(2026, 10, 14), "Cover Person", 840, 1320)
check("an unusual shift (14:00-22:00) still gets rounds, spread through it", len(odd) == 3 and all(870 <= r["start"] and r["finish"] <= 1310 for r in odd), odd)
short = c.usual_patrols(D(2026, 10, 14), "Cover Person", 600, 780)
check("a three-hour shift gets one round", len(short) == 1, short)

# ---- the patrol store ----
day, who = D(2026, 10, 3), "Lena Drayton"
build = lambda now: c.build_patrols(day, roster, who, now=at(now))
log = build("2026-10-03 20:00")
check("the three rounds are on the log from the start of the shift, marked as still to come", [r["id"] for r in log["rows"]] == ["u0", "u1", "u2"] and all(r["ahead"] for r in log["rows"]) and not log["typed"], show(log["rows"]))
new = c.patrol_add(log, "19:30", "19:45")
log = build("2026-10-03 20:00")
check("an added patrol is listed at once, before them", show(log["rows"])[0] == (new, "19:30", "19:45", "All", "") and len(log["rows"]) == 4, show(log["rows"]))
log = build("2026-10-04 07:00")
check("...and the usual rounds still arrive as they are done", [r["id"] for r in log["rows"]] == [new, "u0", "u1", "u2"], show(log["rows"]))
usual = {r["id"]: r for r in log["rows"]}
c.patrol_set(log, "u1", start="01:40", finish="02:05", notes="Gate checked")
c.patrol_set(log, new, area="Car park")
log = build("2026-10-04 07:00")
rows = {r["id"]: r for r in log["rows"]}
check("a change lands on the patrol it was made to", (rows["u1"]["start_text"], rows["u1"]["finish_text"], rows["u1"]["notes"]) == ("01:40", "02:05", "Gate checked")
      and rows[new]["area"] == "Car park" and rows["u0"]["start"] == usual["u0"]["start"] and rows["u2"]["start"] == usual["u2"]["start"], show(log["rows"]))
check("only the changed rows are marked as changed", [r["id"] for r in log["rows"] if r["edited"]] == [new, "u1"])
c.patrol_set(log, new, start="03:00", finish="03:15")
log = build("2026-10-04 07:00")
check("rows stay in time order when a time changes (small hours after midnight)", [r["id"] for r in log["rows"]] == ["u0", "u1", new, "u2"], show(log["rows"]))
c.patrol_remove(log, ["u0", new])
log = build("2026-10-04 07:00")
check("a usual round and an added patrol can both be removed", [r["id"] for r in log["rows"]] == ["u1", "u2"], show(log["rows"]))
c.patrol_clear(log)
check("clear: nothing listed", build("2026-10-04 07:00")["rows"] == [])
second = c.patrol_add(log, "23:00", "23:10")
check("...and a patrol added after clearing is the only one", [r["id"] for r in build("2026-10-04 07:00")["rows"]] == [second])
c.patrol_reset(log)
log = build("2026-10-04 07:00")
check("usual rounds: everything typed is forgotten and the file is gone", [r["id"] for r in log["rows"]] == ["u0", "u1", "u2"] and not log["typed"]
      and not os.path.exists(os.path.join(c.EDITS, log["stem"] + ".json")))

# ---- a file saved by the version before ----
with open(os.path.join(c.EDITS, log["stem"] + ".json"), "w", encoding="utf-8") as f:
    json.dump({"rows": [{"start": "18:30", "finish": "18:45", "area": "All", "notes": ""}]}, f)
log = build("2026-10-04 07:00")
check("an older saved list still prints exactly what it printed", [(r["start_text"], r["finish_text"]) for r in log["rows"]] == [("18:30", "18:45")], show(log["rows"]))
c.patrol_set(log, log["rows"][0]["id"], notes="All secure")
log = build("2026-10-04 07:00")
check("...and can be changed", [(r["start_text"], r["notes"]) for r in log["rows"]] == [("18:30", "All secure")], show(log["rows"]))
c.patrol_reset(log)

# ---- where an added patrol goes ----
log = build("2026-10-03 20:06")
check("during the shift: the quarter of an hour just gone", c.patrol_slot(log, at("2026-10-03 20:06")) == (19 * 60 + 51, 20 * 60 + 6))
log = build("2026-10-04 07:00")
slots = []
for n in range(6):
    s, f = c.patrol_slot(log, at("2026-10-04 07:00"))
    slots.append((c.fmt(s), c.fmt(f)))
    c.patrol_add(log, c.fmt(s).zfill(5), c.fmt(f).zfill(5))
    log = build("2026-10-04 07:00")
spans = sorted((r["start"], r["finish"]) for r in log["rows"])
check("after the shift: six more patrols all find room, none overlapping", len(spans) == 9 and all(spans[i][1] <= spans[i + 1][0] for i in range(8)), slots)
s, f = c.patrol_slot(build("2026-10-03 17:00"), at("2026-10-03 17:00"))
check("before the shift starts a patrol can be put down, somewhere inside the shift", 1080 <= s < f <= 1800 and f - s == 15, (s, f))

c.patrol_reset(log)

# ---- filling in ahead ----
calls_done = c.build(day, roster, who, now=at("2026-10-04 07:00"))
live = lambda: c.build(day, roster, who, now=at("2026-10-03 20:10"))
blank = sum(1 for x in live()["calls"] if not x["call_text"])
c.fill_ahead(live(), True)
filled = live()
check("fill ahead: every call has a time and Ok, the ones still to come are marked", blank > 0 and all(x["call_text"] and x["response"] == "Ok" for x in filled["calls"])
      and sum(1 for x in filled["calls"] if x["ahead"]) == blank == filled["meta"]["early"] and filled["meta"]["pending"] == 0 and filled["meta"]["ahead"])
check("...in the same order and at the same times as the finished log", [(x["name"], x["call"]) for x in filled["calls"]] == [(x["name"], x["call"]) for x in calls_done["calls"]])
plog = build("2026-10-03 20:10")
c.fill_ahead(plog, True)
plog = build("2026-10-03 20:10")
check("fill ahead: the three rounds are listed before they are walked", [r["id"] for r in plog["rows"]] == ["u0", "u1", "u2"] and all(r["ahead"] for r in plog["rows"]) and plog["ahead"] and not plog["typed"])
c.patrol_reset(plog)
check("...and Usual rounds does not switch that off", build("2026-10-03 20:10")["ahead"])
c.fill_ahead(plog, False)
c.fill_ahead(live(), False)
check("blank again: only what has happened, and nothing left on file", sum(1 for x in live()["calls"] if not x["call_text"]) == blank and len(build("2026-10-03 20:10")["rows"]) == 3 and os.listdir(c.EDITS) == [], os.listdir(c.EDITS))
target = next(x for x in live()["calls"] if not x["call_text"])
c.save_edit(live(), target, "time", c.fmt(target["call"] + 2))
shown = next(x for x in live()["calls"] if x["name"] == target["name"] and x["edited"])
check("a time typed for a call still to come is shown, and marked as ahead", shown["call_text"] == c.fmt(target["call"] + 2) and shown["ahead"] and sum(1 for x in live()["calls"] if not x["call_text"]) == blank - 1)
c.save_edit(live(), shown, "time", None)
c.patrol_reset(log)

calls_done = c.build(day, roster, who, now=at("2026-10-04 07:00"))

# ---- typed times ----
log = build("2026-10-04 07:00")
check("the last minute of an overnight shift is inside it", c.read_time("06:00", log) == "6:00")
for text, why in (("17:30", "outside"), ("06:10", "outside"), ("abc", "not a time")):
    try:
        c.read_time(text, log)
        check("%s is refused on a patrol log" % text, False)
    except c.Problem as e:
        check("%s is refused on a patrol log" % text, why in str(e), e)
shift = c.build_shift(day, roster, who)
check("a shift log line may be a little before or after the shift", c.read_time("17:45", shift, early=c.EARLY, late=c.GRACE) == "17:45"
      and c.read_time("06:20", shift, early=c.EARLY, late=c.GRACE) == "6:20")
check("a time still to come is accepted (the rule was dropped on 2026-10-11)", c.read_time("23:30", shift) == "23:30")

# ---- times as people type them ----
P = lambda text: c.fmt(c.parse_clock(text, now=at("2026-10-04 03:12")))
typed = {"17:05": "17:05", "17.05": "17:05", "17 05": "17:05", "17,05": "17:05", "1705": "17:05", "705": "7:05", "7:5": "7:05", "0310": "3:10",
         "17": "17:00", "7": "7:00", "0": "0:00", "5pm": "17:00", "5.05pm": "17:05", "5:05 pm": "17:05", "12am": "0:00", "12pm": "12:00", "9a": "9:00",
         "1705h": "17:05", "17h": "17:00", " 2305 ": "23:05", "now": "3:12", "N": "3:12", "23:59": "23:59"}
wrong = {k: P(k) for k in typed if P(k) != typed[k]}
check("times typed loosely are read as meant (%d ways)" % len(typed), not wrong, wrong)
unread = []
for text in ("25:10", "03:75", "abc", "", "24:00", "13pm", "0pm", "12345", "1:2:3", "17:", ":05", "-5", "5 past"):
    try:
        unread.append((text, P(text)))
    except c.Problem as e:
        if "is not a time" not in str(e):
            unread.append((text, str(e)))
check("what cannot be a time is refused, with a reason", not unread, unread)
check("read_time puts a loosely typed time in the right half of the night", c.read_time("0310", log) == "3:10" and c.read_time("7pm", log) == "19:00")

# ---- undo is a snapshot of the shift's small files ----
stems = c.shift_stems(day, who)
check("the three files of a shift", stems == ["2026-10-03 Lena Drayton", "2026-10-03 Lena Drayton patrols", "2026-10-03 Lena Drayton shift"], stems)
before = c.snapshot(stems)
plog = build("2026-10-04 07:00")
c.patrol_set(plog, "u1", notes="Gate checked")
c.patrol_add(plog, "23:00", "23:10")
cl = c.build(day, roster, who, now=at("2026-10-04 07:00"))
c.save_edit(cl, cl["calls"][2], "response", "No answer")
after = c.snapshot(stems)
c.restore(before)
check("restore puts everything back (nothing was on file: the files are gone)", os.listdir(c.EDITS) == [] and [r["id"] for r in build("2026-10-04 07:00")["rows"]] == ["u0", "u1", "u2"], os.listdir(c.EDITS))
c.restore(after)
again = build("2026-10-04 07:00")
check("...and forward again (redo)", len(again["rows"]) == 4 and c.build(day, roster, who, now=at("2026-10-04 07:00"))["calls"][2]["response"] == "No answer")
c.restore(before)

# ---- a quiet shift says so ----
quiet = c.build_shift(day, roster, who)
check("no lines and no handover notes: NO MAJOR OCCURRENCE and NTR", quiet["quiet"] == "NO MAJOR OCCURRENCE" and quiet["handover_default"] == "NTR" and quiet["handover"] == "")
c.save_shift(quiet, [{"id": "e1", "time": "21:00", "area": "Gate", "text": "Checked"}], "", "", quiet["incoming"])
check("a line typed takes NO MAJOR OCCURRENCE away, and the handover points up at it instead of saying NTR", c.build_shift(day, roster, who)["quiet"] == "" and c.build_shift(day, roster, who)["handover_default"] == "AS PER OCCURRENCES ABOVE")
c.save_shift(quiet, [{"id": "e1", "time": "21:00", "area": "Gate", "text": "Checked"}], "Keys with Henry.", "", quiet["incoming"])
both = c.build_shift(day, roster, who)
check("a line and handover notes typed: neither stand-in is left", both["quiet"] == "" and both["handover_default"] == "" and both["handover"] == "Keys with Henry.")
c.save_shift(quiet, [], "   \n ", "", quiet["incoming"])
check("handover notes that are only spaces count as not typed", c.build_shift(day, roster, who)["handover_default"] == "NTR")
c.save_shift(quiet, [], "Keys with Henry.", "", quiet["incoming"])
check("handover notes typed take NTR away, and NO MAJOR OCCURRENCE stays on the empty table", c.build_shift(day, roster, who)["handover_default"] == "" and c.build_shift(day, roster, who)["handover"] == "Keys with Henry." and c.build_shift(day, roster, who)["quiet"] == "NO MAJOR OCCURRENCE")
c.save_shift(quiet, [], "", "", quiet["incoming"])
rounds = [(n, s["name"], len(c.usual_patrols(D(2026, 10, 12) + datetime.timedelta(days=n), s["name"], s["start"], s["end"] + (c.DAY if s["end"] <= s["start"] else 0))))
          for n in range(60) for s in roster.duty(D(2026, 10, 12) + datetime.timedelta(days=n))]
check("every controller shift on the roster for the next 60 days has at least three rounds", rounds and all(r[2] >= 3 for r in rounds), [r for r in rounds if r[2] < 3][:5])

# ---- shift log lines ----
with open(os.path.join(c.EDITS, shift["stem"] + ".json"), "w", encoding="utf-8") as f:
    json.dump({"entries": [{"time": "", "area": "", "text": ""}, {"time": "20:07", "area": "", "text": ""}], "handover": "", "supervisor": ""}, f)
shift = c.build_shift(day, roster, who)
check("a file from the version before: the empty line goes, the other gets an id", [(e["id"], e["time"]) for e in shift["entries"]] == [("e1", "20:07")], shift["entries"])
entries = shift["entries"] + [{"id": c.new_entry_id(shift), "time": "06:10", "area": "Desk", "text": "Handed over"},
                              {"id": "e3", "time": "02:15", "area": "Gate", "text": "Checked"}, {"id": "e4", "time": "", "area": "", "text": "No time"}]
c.save_shift(shift, entries, "Keys with Henry.", "", shift["incoming"])
shift = c.build_shift(day, roster, who)
check("lines are kept in the order they happened, with their ids", [(e["id"], e["time"]) for e in shift["entries"]] == [("e1", "20:07"), ("e3", "02:15"), ("e2", "06:10"), ("e4", "")],
      [(e["id"], e["time"]) for e in shift["entries"]])
check("handover notes are kept", shift["handover"] == "Keys with Henry.")
c.save_shift(shift, [], "", "", shift["incoming"])
check("with nothing typed the file is removed", not os.path.exists(os.path.join(c.EDITS, shift["stem"] + ".json")))

# ---- the check calls are untouched ----
calls = c.build(day, roster, who, now=at("2026-10-04 07:00"))
check("the check call log is as before (42 calls, in order, never before the mark)", calls["meta"]["total"] == 42
      and all(x["call"] <= y["call"] for x, y in zip(calls["calls"], calls["calls"][1:])) and all(x["call"] > x["start"] - 60 for x in calls["calls"]), calls["meta"]["total"])
check("nothing was left in the scratch corrections folder", os.listdir(c.EDITS) == [], os.listdir(c.EDITS))
print("\n%d of %d checks passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
