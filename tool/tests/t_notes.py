"""Notes typed on the roster are never a venue or a person, and no real person is ever lost (the bug reported on
2026-10-19, and everything the reviews of its fix found). Runs on a pinned copy of that day's roster
(roster-2026-10-19.xlsx), so it does not change when the live roster does; cells are typed into copies of it in
memory only. Hand corrections go to a scratch folder. CCL_TOOL=<folder> tests another copy of calllog.py."""
import datetime
import os
import re
import shutil
import sys
import tempfile

TOOL = os.environ.get("CCL_TOOL", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, TOOL)
import calllog as c  # noqa: E402
from xl import idx_to_col  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "roster-2026-10-19.xlsx")
WORK = os.path.join(tempfile.gettempdir(), "ccl-tests")         # throwaway output; the checks themselves live in tool\tests
c.EDITS = os.path.join(WORK, "edits-notes")
shutil.rmtree(c.EDITS, ignore_errors=True)
results = []
D = datetime.date
LATE = datetime.datetime(2026, 12, 31)


def check(label, ok, detail=""):
    results.append(bool(ok))
    print("%s  %s  %s" % ("PASS" if ok else "FAIL", label, str(detail)[:300]))


COLS = {}


def col(name):
    if name not in COLS:
        COLS[name] = next(i for i in range(4000) if idx_to_col(i) == name)
    return COLS[name]


def cell(roster, ref):
    m = re.match(r"([A-Z]+)(\d+)$", ref)
    return roster.text(int(m.group(2)), col(m.group(1)))


def fake(cells):
    """A fresh roster from the pinned copy with some cells typed in: {ref: (text, ref whose look to copy) or None}."""
    roster = c.Roster(FIXTURE)
    for ref, what in cells.items():
        m = re.match(r"([A-Z]+)(\d+)$", ref)
        key = (int(m.group(2)), col(m.group(1)))
        if what is None:
            roster.cells.pop(key, None)
        else:
            text, like = what
            lm = re.match(r"([A-Z]+)(\d+)$", like)
            roster.cells[key] = (text, roster.cells[(int(lm.group(2)), col(lm.group(1)))][1], "s")
    return roster


def calls(log):
    return [(x["name"], x["venue"], x["shift"]) for x in log["calls"]]


def log_of(roster, day, who):
    try:
        return c.build(day, roster, who, now=LATE)
    except c.Problem as e:
        return {"calls": [], "notes": ["PROBLEM " + str(e)]}


DARK, WHITE, NAME = "EX264", "FJ162", "EZ266"       # looks to copy: a venue label, a time cell, a name
base = c.Roster(FIXTURE)
check("the pinned roster is laid out as these checks expect",
      (cell(base, "EX264"), cell(base, "EX265"), cell(base, "FJ165"), cell(base, "FF180"), cell(base, "FJ186"), cell(base, "FL181"),
       cell(base, "FF68"), cell(base, "FM290"), cell(base, "FM291")) ==
      ("Perch Kilcarran", "IMPORTANT NOTE", "Important reminder", "Lido", "The Old Haunt", "16:00-22:00", "Kilcarran", "TBC", "Wesley Carden"))

# ---- the words ----
notes = ["IMPORTANT NOTE", "Important reminder", "Reminder 5pm", "TBC", "tba", "T.B.C.", "To be confirmed", "NB keys at the bar",
         "N.B. gate code changed", "NB: Reddy has the keys", "Cancelled", "CANCELLED", "Please call early", "PLEASE NOTE", "Note:", "FYI",
         "Urgent", "Attention all staff", "ATTN", "PTO", "Info", "See comment", "SEE COMMENT", "Read comment", "Check comment",
         "CLOSED", "Remember", "VACANT", "Spare", "Cover needed", "Sick", "AL", "A/L", "N/A", "X", "***", "---", "Open shift",
         "Agency", "No show", "Day off", "Annual leave", "Training", "Standby", "On call", "Keys"]
check("notes are notes, wherever they are typed", all(c.is_note(t) for t in notes), [t for t in notes if not c.is_note(t)])
people = ["Dmitri O'Iyer", "Roland De Sutton", "Victor de Sutton", "Ali bin Omar", "Tomas (Malow) Lindqvist", "Rory 'Hollis' Orwell",
          "Tobias", "Ivan", "Noel Ring", "Nora Keys", "Rutledge khan", "Sai kiran farrow", "Mehmet 'Memo' Yilmaz", "Memo Ozdemir",
          "N.B. Reddy", "Al Jankowski", "Johnny Cash", "Billie Holiday", "Alicia Keys", "Lena Drayton (TBC)", "Glen Ivers (to confirm)",
          "Will (agency)", "Omar al-Farouq al-Masri", "Pádraig Mac an tSaoir", "José de los Ríos", "Dorian Sandler!", "Nthan Chaudry NB keys",
          "Morgan Read", "Lena Drayton to cover"]    # ("Read ..." first is taken as a note: "Read comment" is likelier here)
check("people are never notes, whatever follows their name", not any(c.is_note(t) for t in people), [t for t in people if c.is_note(t)])
venues = ["The Wharf", "Perch Kilcarran", "12 Harbour st", "Brennans Mill st", "Column bar (Delacroix)", "K9 / Red Door",
          "Bullseye Club Darts / Pats", "Loft", "Lido", "The Old Haunt", "QuickStop Elmfield", "WESTGATE", "Nona's", "O'Garvey", "Kilcarran",
          "Blue Note", "The Note Bar", "Yo! Sushi", "Memo's Kebabs", "The Spare Room", "Holiday Inn Express", "Cash Converters"]
check("venue names are never notes", not any(c.is_note(t) for t in venues), [t for t in venues if c.is_note(t)])
check("a name with a note after it is pointed out; a plain or unusual name is not",
      all(c.name_flag(t) for t in ("Dorian Sandler (TBC)", "Glen Ivers - please confirm", "Nthan Chaudry NB keys", "Dorian Sandler!",
                                   "Keys with the bar manager"))
      and not any(c.name_flag(t) for t in ("Omar al-Farouq al-Masri", "Pádraig Mac an tSaoir", "Tomas (Malow) Lindqvist", "Victor de Sutton",
                                           "Rutledge khan", "Dmitri O'Iyer")))
times = {v[0].strip() for (r, k), v in base.cells.items() if v[2] in ("s", "str", "inlineStr")}
OLD_TIME = re.compile(r"^\s*(\d{1,2})(?:[:.]|-(?=\d{2}\s*-))(\d{2})\s*(?:hrs?)?\s*-\s*(?:(\d{1,2})[:.-](\d{2}))?")
moved = [t for t in times if OLD_TIME.match(t) and (not c.TIME_RE.match(t) or c.TIME_RE.match(t).groups()[:4] != OLD_TIME.match(t).groups())]
check("every start time on the roster reads exactly as before", not moved and len([t for t in times if OLD_TIME.match(t)]) > 50, moved[:5])
check("...but a note that starts with a time is not one", not any(c.TIME_RE.match(t) for t in ("21:30 - doors open", "20:00 - ring Will", "18.00 - keys from bar")))

# ---- the whole roster ----
dates = sorted(c.EPOCH + datetime.timedelta(days=int(k)) for k in base._date_cols)
bad = [(d, s["ref"], s["site"], s["name"]) for d in dates for s in base.shifts(d)[1] if c.is_note(s["site"]) or c.is_note(s["name"])]
check("no venue and no person on any date of the roster is a note", not bad, bad[:5])
perch = [s for d in dates for s in base.shifts(d)[1] if 266 <= s["row"] <= 272 and col("EY") <= col(re.match(r"[A-Z]+", s["ref"]).group(0)) <= col("FE")]
check("week of 12 Oct: everyone under the IMPORTANT NOTE cell is at Perch Kilcarran", perch and all(s["site"] == "Perch Kilcarran" for s in perch),
      sorted({s["site"] for s in perch}))
henry = log_of(base, D(2026, 10, 13), "Henry Marlow")
check("13 Oct, Henry: Dmitri O'Iyer, Glen Ivers and Levi Lowell are called at Perch Kilcarran",
      {("Dmitri O'Iyer", "Perch Kilcarran", "21:00-close"), ("Glen Ivers", "Perch Kilcarran", "21:00-close"),
       ("Levi Lowell", "Perch Kilcarran", "21:00-close")} <= set(calls(henry)), calls(henry)[:6])
said = [n for n in henry["notes"] if "IMPORTANT NOTE" in n]
check("...and one line says the roster has a note beside Perch Kilcarran, in a comment on that cell, among the first three",
      len(said) == 1 and "comment on that cell" in said[0] and said[0] in henry["notes"][:3], henry["notes"][:3])
check("12 Oct (nobody at Perch Kilcarran that day): no line about it",
      not any("IMPORTANT NOTE" in n for w in ("Soren Rasmussen", "Henry Marlow") for n in log_of(base, D(2026, 10, 12), w)["notes"]))
basil = log_of(base, D(2026, 10, 22), "Basil Jankowski")
check("22 Oct, Basil: 'Important reminder' is not a person to call, and his log says there is a note under Loft",
      all(x["name"] != "Important reminder" for x in basil["calls"]) and any(n.startswith("Roster note under Loft: 'Important reminder'") for n in basil["notes"]))
check("...Soren's day log does not mention it (Loft's 22:00 calls are Basil's)",
      not any("Important reminder" in n for n in log_of(base, D(2026, 10, 22), "Soren Rasmussen")["notes"]))
check("20 Oct: 'Reminder 5pm' under the control room is on both controllers' logs",
      all(any("'Reminder 5pm'" in n for n in log_of(base, D(2026, 10, 20), w)["notes"]) for w in ("Soren Rasmussen", "Lena Drayton")))
sun = [log_of(base, D(2026, 10, 25), w) for w in ("Soren Rasmussen", "Lena Drayton")]
check("25 Oct: Wesley Carden's start time is 'TBC' - not on the log, and both logs say so in one plain line",
      all(not any(x["name"] == "Wesley Carden" and "Bullseye" in x["venue"] for x in log["calls"])
          and sum("Wesley Carden at Bullseye Club Darts / Pats has no start time on the roster yet ('TBC')" in n for n in log["notes"]) == 1
          and not any(n.startswith("Roster note") and "'TBC'" in n for n in log["notes"]) for log in sun), [l["notes"][:2] for l in sun])
jul = log_of(base, D(2026, 7, 30), "Basil Jankowski")
check("30 Jul: Wesley Carden, under a start typed '-close', is still called with the time above, and the log says why",
      ("Wesley Carden", "12 Harbour st", "22:00-close") in calls(jul)
      and any(n.startswith("Wesley Carden at 12 Harbour st: the start time above it is not filled in") for n in jul["notes"]))

# ---- Lido and The Old Haunt, week of 19 Oct (asked for as test cases on 2026-10-19) ----
week = [D(2026, 10, 19) + datetime.timedelta(days=n) for n in range(7)]
check("Lido (venue column) and The Old Haunt (typed in the Thursday and Sunday columns) are venues all week, never notes",
      not any(m["text"] in ("Lido", "The Old Haunt") for d in week for m in (base.shifts(d), base.remarks[base.day_column(d)])[1]))
sat = D(2026, 10, 24)
empty = {(e["site"], e["shift"]) for e in (base.shifts(sat), base.empty[base.day_column(sat)])[1]}
check("Sat 24 Oct: Lido has two 16:00-22:00 starts and The Old Haunt one, all still with nobody under them",
      {("Lido", "16:00-22:00"), ("The Old Haunt", "16:00-22:00")} <= empty, sorted(empty))
day_log = log_of(base, sat, "Soren Rasmussen")
line = [n for n in day_log["notes"] if n.startswith("No name on the roster yet for")]
check("...no call is made up for them, and the day log (whose calls those would be) says so in one line",
      not any("Lido" in x["venue"] or "Old Haunt" in x["venue"] for x in day_log["calls"]) and len(line) == 1
      and "Lido 16:00-22:00 (x2)" in line[0] and "The Old Haunt 16:00-22:00" in line[0], line)
check("...the night log does not mention them", not any("Lido" in n or "Old Haunt" in n for n in log_of(base, sat, "Lena Drayton")["notes"]))
named = fake({"FL182": ("Ann Example", NAME), "FL188": ("Ben Example", NAME)})
got = calls(log_of(named, sat, "Soren Rasmussen"))
check("once names are typed under them they are called at Lido and at The Old Haunt (its label typed in other days' columns)",
      ("Ann Example", "Lido", "16:00-22:00") in got and ("Ben Example", "The Old Haunt", "16:00-22:00") in got, [g for g in got if "Example" in g[0]])
check("...and only the slot still empty is left in the line",
      [n for n in log_of(named, sat, "Soren Rasmussen")["notes"] if n.startswith("No name")] == ["No name on the roster yet for Lido 16:00-22:00 - nobody to call there yet  [FL183]"])

# ---- notes typed into copies of the roster: the control room ----
duty = lambda roster, day: [s["name"] for s in roster.duty(day)]
real = {d: duty(base, d) for d in week}
for text in ("IMPORTANT NOTE", "See comment", "Comment", "Read me", "Closed", "No show", "Do not call", "Change of time", "Cover needed",
             "Not open", "Keys in office", "***"):
    for look in (WHITE, DARK):
        lost = [d for d in week if duty(fake({"FF72": (text, look)}), d) != real[d]]
        if lost:
            break
    check("%r typed between the two controllers (plain and dark): both still on duty all week" % text, not lost, lost)
for ref in ("FF69", "FF73"):
    roster = fake({ref: ("IMPORTANT NOTE", DARK)})
    check("a note beside a controller's start time (%s): duty and the log are unchanged" % ref,
          [duty(roster, d) for d in week] == [real[d] for d in week] and calls(log_of(roster, sat, "Lena Drayton")) == calls(log_of(base, sat, "Lena Drayton")))
for text in ("Lena Drayton to cover", "Lena Drayton (TBC)", "Lena Drayton!"):
    roster = fake({"FK74": (text, NAME)})
    check("the night controller written %r: still in the control room, and the log is made" % text,
          any(c.same_person("Lena Drayton", n) for n in duty(roster, D(2026, 10, 23))) and log_of(roster, D(2026, 10, 23), "Lena Drayton")["calls"])

# ---- notes typed into copies of the roster: venues and people ----
fri = D(2026, 10, 23)
for text in ("Dorian Sandler (new starter)", "Dorian Sandler to cover", "Dorian Sandler TBC", "Dorian Sandler - please confirm",
             "Dorian Sandler!", "Sai kiran farrow"):
    log = log_of(fake({"FK165": (text, NAME)}), fri, "Lena Drayton")
    check("%r under Loft: still called, with a line to check it" % text,
          (text, "Loft", "22:00-close") in calls(log) and any(n.startswith(text + " at Loft:") for n in log["notes"]), calls(log)[:2])
sat17 = D(2026, 10, 17)
before17 = calls(log_of(base, sat17, "Lena Drayton"))
for ref, text, look in (("EX265", "See comment", WHITE), ("EX268", "SEE COMMENT", WHITE), ("EY268", "CLOSED", DARK)):
    got = calls(log_of(fake({ref: (text, look)}), sat17, "Lena Drayton"))
    check("%r at %s: every Perch Kilcarran call on Sat 17 Oct is as before" % (text, ref), got == before17,
          [x for x in got if x not in before17][:3])
log = log_of(fake({"EX264": ("Perch Kilcarran (TBC)", DARK)}), sat17, "Lena Drayton")
check("a venue label with a remark ('Perch Kilcarran (TBC)'): its staff stay at Perch Kilcarran, and the remark is passed on",
      calls(log) == before17 and any(n.startswith("The roster adds a note to the venue Perch Kilcarran: 'Perch Kilcarran (TBC)'") for n in log["notes"]))
emm = next(s for s in base.shifts(sat17)[1] if s["name"] == "Ines Nakamura")
log = log_of(fake({emm["ref"]: ("VACANT", NAME)}), sat17, "Lena Drayton")
check("'VACANT' where a name goes is not called 'Ok'", not any(x[0] == "VACANT" for x in calls(log)) and any("'VACANT'" in n for n in log["notes"]))
log = log_of(fake({emm["ref"]: ("Staff Must Sign In At The Front Desk Tonight", DARK)}), sat17, "Lena Drayton")
check("a dark sentence in a day cell is one note line, not a call as well",
      not any("Staff Must" in x[0] for x in calls(log)) and sum("Staff Must" in n for n in log["notes"]) == 1, log["notes"][:3])
log = log_of(fake({emm["ref"]: ("21:30 - doors open", WHITE)}), sat17, "Lena Drayton")
check("'21:30 - doors open' does not re-time the people under it", all(x[2] != "21:30 - doors open" for x in calls(log)))
row = next(s for s in base.shifts(fri)[1] if s["name"] == "Hector Nash" and "Bullseye" in s["site"])
log = log_of(fake({"FK%d" % (row["row"] - 1): ("TBC", WHITE)}), fri, "Lena Drayton")
check("'TBC' over a later group: its person keeps the time above and the log points it out",
      any(x[0] == "Hector Nash" and "Bullseye" in x[1] for x in calls(log))
      and any(n.startswith("Hector Nash at Bullseye Club Darts / Pats: the start time above it is not filled in") for n in log["notes"]))
log = log_of(fake({"FF158": ("IMPORTANT NOTE", WHITE), "FK158": ("Town Ballyrock", DARK), "FK160": ("Ann Example", NAME)}), fri, "Lena Drayton")
check("a note in the venue column beside a venue label typed in a day column: the label still counts",
      ("Ann Example", "Town Ballyrock", "18:00-close") in calls(log) and not any(x[0] == "Town Ballyrock" for x in calls(log)), calls(log)[:3])
mon = fake({"FG165": ("Important reminder", DARK)})
check("a dark note typed in Monday's column is Monday's business only",
      any("Important reminder" in n for n in log_of(mon, D(2026, 10, 19), "Lena Drayton")["notes"])
      and not any("Important reminder" in n and "FG165" in n for d in week[1:] for w in [s["name"] for s in mon.duty(d)] for n in log_of(mon, d, w)["notes"]))
log = log_of(fake({"FF151": ("the meadows", DARK)}), fri, "Lena Drayton")
check("a venue label typed in small letters is still that venue", any(x[1] == "the meadows" for x in calls(log)), calls(log)[:3])

print("\n%d of %d checks passed" % (sum(results), len(results)))
shutil.rmtree(c.EDITS, ignore_errors=True)
sys.exit(0 if all(results) else 1)
