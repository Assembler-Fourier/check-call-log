"""Version 6.7, clicked and timed the way a person meets it (asked for on 2026-10-24):
- at a handover the window moves on by itself to the controller who has just come on - from the outgoing shift only,
  not while something is typed - writing the outgoing log, silently (no reminder since 20 Nov);
- Auto green is on or off per controller: on for Lena Drayton, off for everyone else, by default;
- a window started for a check can never reach the live roster's script;
- the screen of 6.6: the line under the table wraps, the top line counts green, o / n / l pick a response, the
  choices stay on screen, an untouched open cell does not freeze the table, all notes can be shown, a row's note
  is said when the row is clicked.
Nothing here reaches the real roster (see greenrig.py)."""
import os
import sys
import threading
import time

import greenrig as g

# The original 6.6-6.8 cases exercise a controller without a configured WhatsApp
# reader. Restore the real configuration before every WhatsApp integration below.
_real_is_for = g.calllog.whatsapp.is_for
g.calllog.whatsapp.is_for = lambda name: False

total = []
FRI = "2026-10-23"


def case(title):
    print("\n=== " + title)


# -- 1. Handover ---------------------------------------------------------------------------------------------------------
case("1. Handover: Soren's log is on show when Lena comes on at 16:30 (switch 15 minutes early, 14 Nov)")
rig = g.rig_for("u1", "2026-10-23 16:05")
rig.show("Soren Rask", FRI)
rig.at("2026-10-23 16:14")
rig.check("before the handover: Soren's log stays (chosen by hand, and his calls are the ones being made)", rig.app.shown_who() == "Soren Rask")
rig.at("2026-10-23 16:15")
rig.check("16:15, 15 minutes before Lena's 16:30 start: the window moves to Lena's shift by itself", rig.app.shown_who() == "Lena Drayton" and rig.app.pack["date"].isoformat() == FRI,
          (rig.app.shown_who(), rig.app.dates))
rig.check("...Soren's logs were written first", any(k[1] == "Soren Rask" and v["paths"] for k, v in rig.app.files.items()), list(rig.app.files))
note = rig.app.notice
rig.check("...silently: no handover reminder and no print button (Lena, 20 Nov: 'no need to remind or do anything')",
          "Handover" not in note.text and "print them now" not in note.text and note.button is None
          and "Handover" not in rig.said() and "has come on" not in rig.said(), (note.text, rig.said()))
rig.at("2026-10-23 16:45")
rig.check("it then stays with Lena (it follows the desk again)", rig.app.shown_who() == "Lena Drayton")
total += rig.close()

case("2. Handover: not from another day's log, not while typing")
rig = g.rig_for("u2", "2026-10-23 16:05")
rig.show("Soren Rask", "2026-10-22")
rig.at("2026-10-23 16:14")
rig.at("2026-10-23 16:15")
rig.check("Soren's log of the day before on show: it stays (somebody is looking at it)", rig.app.shown_who() == "Soren Rask" and rig.app.pack["date"].isoformat() == "2026-10-22")
rig.show("Soren Rask", FRI)
rig.app.desk_was = ("Soren Rask", rig.app.pack["date"])           # (as if it were still before the handover)
row = next(i for i, c in enumerate(rig.calls()) if c["call_text"])
rig.click("calls", row, "resp")
rig.box()[0].insert("end", " - rang back")          # (typed, not yet saved)
rig.at("2026-10-23 16:16")
rig.check("something being typed at the handover: it waits", rig.app.shown_who() == "Soren Rask" and rig.box() is not None)
rig.type(None, "<Escape>")
rig.at("2026-10-23 16:17")
rig.check("...and moves on at the next minute once the typing is done", rig.app.shown_who() == "Lena Drayton")
total += rig.close()

case("3. Handover at 06:00: Lena's night to Soren's day (15 minutes early)")
rig = g.rig_for("u3", "2026-10-24 05:43")
rig.show("Lena Drayton", FRI)
rig.at("2026-10-24 05:44")
rig.check("05:44: still Lena's night", rig.app.shown_who() == "Lena Drayton")
rig.at("2026-10-24 05:45")
rig.check("05:45 (15 minutes before 06:00): Soren's day, with no reminder about Lena's night", rig.app.shown_who() == "Soren Rask"
          and "Lena Drayton's logs" not in rig.app.notice.text and "finished at" not in rig.app.notice.text,
          (rig.app.shown_who(), rig.app.notice.text))
rig.check("...Lena's night was still written first", any(k[1] == "Lena Drayton" and v["paths"] for k, v in rig.app.files.items()), list(rig.app.files))
total += rig.close()

case("3b. Clicking his name just before his shift opens the coming shift (Lena, 14 Nov)")
rig = g.rig_for("u3b", "2026-10-23 16:20")                        # Lena's Friday shift starts at 16:30
rig.app.set_name("Henry Marlow")
rig.app.touched_date = False
rig.app.set_name("Lena Drayton")
rig.check("10 minutes before his 16:30 start, a click on his name opens that coming shift, not the one before",
          rig.app.shown_who() == "Lena Drayton" and [d.isoformat() for d in rig.app.dates] == [FRI], rig.app.dates)
total += rig.close()
rig = g.rig_for("u3c", "2026-10-23 16:10")
rig.app.set_name("Henry Marlow")
rig.app.touched_date = False
rig.app.set_name("Lena Drayton")
rig.check("...and 20 minutes before it, his shift today still opens, not the one before (20 Nov)", [d.isoformat() for d in rig.app.dates] == [FRI], rig.app.dates)
total += rig.close()
case("3c. A click on a name opens that person's shift on now, else today's, else the NEXT one (Lena, 20 Nov)")
# His own example, on a stand-in week: Friday 20 Nov, Soren on days until 16:30 and Lena on from 16:30; Henry on
# Saturday and Sunday; Soren off at the weekend and back on Monday.
rig = g.rig_for("u3d", "2026-11-20 17:00")


def _his_week(d):
    if d.weekday() == 4:
        return [dict(name="Soren Rask", start=6 * 60, end=16 * 60 + 30), dict(name="Lena Drayton", start=16 * 60 + 30, end=6 * 60)]
    if d.weekday() == 5:
        return [dict(name="Henry Marlow", start=6 * 60, end=18 * 60), dict(name="Lena Drayton", start=18 * 60, end=6 * 60)]
    if d.weekday() == 6:
        return [dict(name="Henry Marlow", start=6 * 60, end=18 * 60), dict(name="Basil Janek", start=18 * 60, end=6 * 60)]
    return [dict(name="Soren Rask", start=6 * 60, end=18 * 60), dict(name="Basil Janek", start=18 * 60, end=6 * 60)]


rig.app.duty = _his_week
opens = []
for clock, who, want in (("2026-11-20 17:00", "Soren Rask", "2026-11-20"),   # "soren ... go to soren shift for friday"
                         ("2026-11-20 17:00", "Henry Marlow", "2026-11-21"),  # "henry ... tomorrow saturday shift"
                         ("2026-11-20 17:00", "Lena Drayton", "2026-11-20"),    # his own shift, on now
                         ("2026-11-21 03:00", "Lena Drayton", "2026-11-20"),    # Friday's night still on after midnight
                         ("2026-11-21 05:50", "Lena Drayton", "2026-11-20"),    # ...to its last minutes
                         ("2026-11-21 10:00", "Soren Rask", "2026-11-23"),   # "soren ... tomorrow saturday ... monday"
                         ("2026-11-21 17:45", "Lena Drayton", "2026-11-21"),    # 15 minutes before his Saturday 18:00
                         ("2026-11-22 12:00", "Soren Rask", "2026-11-23")):  # Sunday: still his Monday
    rig.clock.set(clock)
    got = rig.app.shift_to_open(who).isoformat()
    opens.append(got == want)
    print("%s  %s, click on %s: opens %s (wanted %s)" % ("PASS" if got == want else "FAIL", clock, who, got, want))
total.extend(opens)
total += rig.close()
rig = g.rig_for("u3e", "2026-10-23 17:00")
rig.app.touched_date = False
rig.app.set_name("Henry Marlow")
rig.check("through the window: a click on Henry on Friday opens his next shift, Monday's, not one he worked before",
          [d.isoformat() for d in rig.app.dates] == ["2026-10-26"], rig.app.dates)
rig.app.touched_date = False
rig.app.set_name("Soren Rask")
rig.check("...and a click on Soren then opens his shift today", [d.isoformat() for d in rig.app.dates] == [FRI], rig.app.dates)
total += rig.close()
_duty = lambda d: [dict(name="Soren Rask", start=6 * 60, end=18 * 60), dict(name="Lena Drayton", start=18 * 60, end=6 * 60)]
_at = lambda clock: g.calllog.at_the_desk(g.datetime.datetime(2026, 11, 14, *clock), _duty, fallback=False, newest=True, early=g.calllog.EARLY)
check_desk = [_at((17, 44))[0], _at((17, 45))[0], _at((5, 44))[0], _at((5, 45))[0]]
total.append(check_desk == ["Soren Rask", "Lena Drayton", "Lena Drayton", "Soren Rask"])
print("%s  the desk moves to the next controller 15 minutes before their start, both ways  %s" % ("PASS" if total[-1] else "FAIL", check_desk))

# -- 4. Auto green per controller -----------------------------------------------------------------------------------------
case("4. Auto green: on for Lena, off for everyone else, by default")
rig = g.rig_for("u4", "2026-10-23 16:20", settings={})                # (no saved setting: the defaults)
rig.show("Lena Drayton", FRI)
rig.check("with nothing saved: on for Lena Drayton", rig.app.auto_for == {"Lena Drayton"} and rig.app.buttons["auto"].text.startswith("Auto green: on"))
rig.show("Soren Rask", FRI)
rig.check("...and off for Soren (the button shows the controller on show)", rig.app.buttons["auto"].text == "Auto green: off" and not rig.app.auto_green)
rig.press("auto")
rig.check("switching it on for Soren leaves Lena's on too", rig.app.auto_for == {"Lena Drayton", "Soren Rask"})
rig.show("Lena Drayton", FRI)
rig.press("auto")
rig.check("switching Lena's off leaves Soren's on", rig.app.auto_for == {"Soren Rask"} and rig.app.buttons["auto"].text == "Auto green: off")
total += rig.close()
rig = g.rig_for("u4b", "2026-10-23 16:20", settings={"auto_green": "Henry Marlow"})
rig.check("a setting kept from 6.6 (Auto green for Henry) is carried over, and Lena's default is on", rig.app.auto_for == {"Lena Drayton", "Henry Marlow"})
total += rig.close()

# -- 5. Never the live roster from a check -----------------------------------------------------------------------------------
case("5. A window started for a check never reaches the roster")
from drive6 import Rig  # noqa: E402
plain = Rig("u5", now="2026-10-23 20:06", roster=g.FIXTURE)
try:
    plain.mod.rostercolour.send({"url": g.URL, "key": "x" * 30}, "green", "FK74", "Lena Drayton")
    refused = False
except plain.calllog.Problem as e:
    refused = "never reaches the roster" in str(e)
plain.check("in a window started for a check (CCL_NO_DOWNLOAD), sending to the roster is refused outright", refused)
plain.check("...and the rigs start with Auto green off for everyone unless a check says otherwise", plain.app.auto_for == set())
total += plain.close()

# -- 6. The screen -------------------------------------------------------------------------------------------------------
case("6. The screen of 6.6")
rig = g.rig_for("u6", "2026-10-23 20:06")
rig.show("Lena Drayton", FRI)
rig.pump(300)
rig.check("the line under the table wraps (it is not cut off)", int(rig.app.said_label.cget("wraplength")) > 100, rig.app.said_label.cget("wraplength"))
rig.check("the top line counts green", " green" in rig.app.tally.cget("text"), rig.app.tally.cget("text"))
made = [i for i, c in enumerate(rig.calls()) if c["call_text"] and not c["ahead"]]
rig.click("calls", made[2], "resp")
entry = rig.box()[0]
entry.delete(0, "end")
entry.insert(0, "n")
entry.icursor("end")
rig.key(entry, "<KeyRelease>", char="n")
rig.check("typing n in a response puts in No answer (the rest selected)", entry.get() == "No answer" and entry.selection_present(), entry.get())
rig.key(entry, "<Return>")
rig.check("...and Enter keeps it", rig.calls()[made[2]]["response"] == "No answer", rig.calls()[made[2]]["response"])
rig.click("calls", made[3], "resp")
entry = rig.box()[0]
entry.delete(0, "end")
entry.insert(0, "Not")
entry.icursor("end")
rig.key(entry, "<KeyRelease>", char="t")
rig.check("typing on past a choice keeps what is typed ('Not ...' is not No answer)", entry.get() == "Not", entry.get())
rig.type(None, "<Escape>")
grid = rig.grid("calls")
last = len(grid.keys) - 1
rig.click("calls", last, "resp")
rig.pump(200)
b = grid.body
win = [i for i in b.find_withtag("over") if b.type(i) == "window" and b.itemcget(i, "window") == str(grid.picker)]
x0, y0, x1, y1 = grid.cell(grid.keys[last], "resp")
rig.check("the last row's choices are shown above it, inside the table (not below the edge)", win and b.itemcget(win[0], "anchor") == "sw"
          and b.coords(win[0])[1] <= y0 and b.coords(win[0])[0] + grid.picker.winfo_reqwidth() <= b.winfo_width(), win and (b.itemcget(win[0], "anchor"), b.coords(win[0])))
rig.type(None, "<Escape>")
later = next(i for i, c in enumerate(rig.calls()) if not c["call_text"])
who = rig.calls()[later]["name"]
rig.click("calls", later, "resp")
rig.check("(a call still to come opened, nothing typed)", rig.box() is not None and rig.box()[0].get() == "")
rig.at("2026-10-23 23:59")
call = next(c for c in rig.calls() if c["name"] == who)
rig.check("time passes with that cell open but untouched: the table still fills in, and the cell shows the call's Ok",
          call["call_text"] and rig.box() is not None and rig.box()[0].get() == "Ok", (call["call_text"], rig.box() and rig.box()[0].get()))
rig.box()[0].insert("end", " - checked")
rig.check("...once something is typed in it, the table waits (nothing typed is lost)", rig.app.typing())
rig.type(None, "<Escape>")
notes = rig.app.note_lines
more = [w for w in rig.app.notes.winfo_children() if w.winfo_class() == "Label" and "show all" in w.cget("text")]
if len(notes) > 3:
    more[0].event_generate("<Button-1>")
    rig.pump(200)
    shown = rig.app.notes.winfo_children()
    rig.check("'show all' shows every note", len([f for f in shown if f.winfo_class() == "Frame"]) == len(notes), (len(shown), len(notes)))
else:
    rig.check("(fewer than 4 notes: nothing to show more of)", not more)
same = rig.app.roster
rig.app.load_roster(force=True)
rig.wait(lambda: not rig.app.fetching, 15)
rig.pump(200)
rig.check("the roster read again but unchanged: not taken apart again (the same copy is kept)", rig.app.roster is same and same is not None)
noted = next((i for i, c in enumerate(rig.calls()) if any("[%s]" % c["ref"] in n for n in rig.app.pack["calls"]["notes"])), None)
if noted is not None:
    rig.click("calls", noted, "name")
    rig.check("clicking a row with a note ('!') says the note on the line under the table", rig.calls()[noted]["name"] in rig.said() and "[" not in rig.said(), rig.said())
total += rig.close()

# == 6.8 (2026-10-25): the roster's script, version 3 ====================================================================
case("7. Which version of its script the roster runs")
rig = g.rig_for("u7", "2026-10-23 20:06")
rig.show("Lena Drayton", FRI)
rig.pump(5600)
rig.check("a window started for a check never asks the roster's script anything by itself (no ping at start-up)",
          not any(s[0] == "ping" for s in rig.fake.sent), rig.fake.sent)
os.environ["CCL_SCRIPT_CHECK"] = "1"
try:
    rig.app.check_script()
    rig.wait(lambda: "up to date" in rig.said(), 10)
    rig.check("the newest script in the roster: 'up to date (version 4)'", "The roster's script is up to date (version 4)" in rig.said()
              and rig.fake.sent[-1][0] == "ping", rig.said())
    rig.fake.version = 3                       # 14 Nov: version 3 is what the roster runs until Lena pastes version 4
    rig.app.check_script()
    rig.wait(lambda: "older version" in rig.said(), 10)
    rig.check("version 3 still in the roster: it says an older version runs (the reminder to paste version 4)",
              "still runs an older version" in rig.said(), rig.said())
    rig.app.say("(nothing)")
    rig.fake.version = 1
    rig.app.check_script()
    rig.wait(lambda: "older version" in rig.said(), 10)
    rig.check("the first script still in: it says so, and where the steps are", "still runs an older version" in rig.said()
              and "How to switch it on.txt" in rig.said() and rig.app.said_kind == "check", rig.said())
    rig.app.say("(nothing)")
    rig.fake.fail = "the roster could not be reached (no network)"
    rig.app.check_script()
    rig.pump(1500)
    rig.check("no network at start-up: nothing said (a press will say it)", rig.said() == "(nothing)", rig.said())
    rig.fake.fail, rig.fake.key = None, "another-key-0000000000000000000000"
    rig.app.check_script()
    rig.wait(lambda: "key" in rig.said(), 10)
    rig.check("a script with another key: it says to paste the script in again", "does not have this PC's key" in rig.said(), rig.said())
finally:
    os.environ.pop("CCL_SCRIPT_CHECK", None)
rig.app.pinged({"ok": True, "sheet": "Sheet1", "version": 4}, None)
rig.check("the link test names the version (newest)", "version 4, the newest" in rig.said(), rig.said())
rig.app.pinged({"ok": True, "sheet": "Sheet1"}, None)
rig.check("...or says the roster runs an older one", "older version of its script" in rig.said() and rig.app.said_kind == "check", rig.said())
total += rig.close()

case("8. What the new script refuses is said plainly, and nothing changes")
rig = g.rig_for("u8", "2026-10-23 20:06")
rig.show("Lena Drayton", FRI)
whites = [c for c in rig.calls() if g.state(rig, c["name"]) == "off" and rig.fake.fill.get(c["ref"]) == "#ffffff"]
a, b, c3 = whites[0], whites[1], whites[2]
rig.fake.merged.add(a["ref"])
rig.click("calls", g.row_of(rig, a["name"]), "ok")
g.settle(rig)
rig.check("a merged cell: 'merged with other cells on the roster - left to a person', still white", "merged with other cells" in rig.said()
          and rig.fake.fill[a["ref"]] == "#ffffff" and g.state(rig, a["name"]) == "off", rig.said())
rig.fake.numbers.add(b["ref"])
rig.click("calls", g.row_of(rig, b["name"]), "ok")
g.settle(rig)
rig.check("a cell the script does not take for a name: said, still white", "does not take %s for a name" % b["ref"] in rig.said()
          and rig.fake.fill[b["ref"]] == "#ffffff", rig.said())
rig.click("calls", g.row_of(rig, c3["name"]), "ok")
g.settle(rig)
rig.fake.fill[c3["ref"]] = "#ffff00"                  # someone makes it yellow on the roster after the window turned it green
rig.click("calls", g.row_of(rig, c3["name"]), "ok")
g.settle(rig)
rig.check("white asked for a name made yellow since: refused ('changed'), the yellow stays, the roster is read again",
          rig.fake.fill[c3["ref"]] == "#ffff00" and "changed since it was read" in rig.said(), (rig.fake.fill[c3["ref"]], rig.said()))
total += rig.close()

case("9. The window works with the script on the roster now (version 1) as well")
rig = g.rig_for("u9", "2026-10-23 20:06")
rig.fake.version = 1
rig.show("Lena Drayton", FRI)
first = next(c for c in rig.calls() if g.state(rig, c["name"]) == "off" and rig.fake.fill.get(c["ref"]) == "#ffffff")
rig.click("calls", g.row_of(rig, first["name"]), "ok")
g.settle(rig)
rig.check("a press: green", rig.fake.fill[first["ref"]] == "#92d050" and g.state(rig, first["name"]) == "on")
rig.click("calls", g.row_of(rig, first["name"]), "ok")
g.settle(rig)
rig.check("...and back to white", rig.fake.fill[first["ref"]] == "#ffffff" and g.state(rig, first["name"]) == "off")
n = len(rig.fake.sent)
rig.press("auto")
g.settle(rig, 40)
greened = {s[1] for s in rig.fake.sent[n:] if s[0] == "green"}
expected = {c["ref"] for c in g.made(rig) if c["response"] == "Ok" and g.rostercolour.css(c["fill"]) == "#ffffff" and c["ref"] != first["ref"]}
rig.check("Auto green against version 1: every made Ok white name green (the hand's white kept)", greened == expected and expected and not rig.errors,
          (len(greened), len(expected)))
total += rig.close()

case("10. Auto green never greens a name struck through on the roster")
rig = g.rig_for("u10", "2026-10-23 20:06")
rig.show("Lena Drayton", FRI)
made_ok = [c for c in g.made(rig) if c["response"] == "Ok" and g.rostercolour.css(c["fill"]) == "#ffffff"]
gone_green = made_ok.pop(1)                           # this one green on the roster, and struck through as well
STRUCK = {made_ok[0]["ref"], gone_green["ref"]}


class Struck(g.tweaked({gone_green["ref"]: (None, "FF92D050")})):
    def style(self, r, c):
        s = super().style(r, c)
        return dict(s, strike=True) if "%s%d" % (g.calllog.idx_to_col(c), r) in STRUCK else s


rig.fake.fill[gone_green["ref"]] = "#92d050"
g.calllog.Roster = Struck
rig.app.roster_sig = None
rig.app.load_roster(force=True)
rig.wait(lambda: not rig.app.fetching, 15)
rig.pump(300)
struck = next(c for c in rig.calls() if c["ref"] == made_ok[0]["ref"])
rig.check("(the window sees it struck through, and its note says so)", struck.get("strike") and any("STRUCK" in n.upper() and struck["name"] in n for n in rig.app.pack["calls"]["notes"]),
          struck.get("strike"))
gone = next(c for c in rig.calls() if c["ref"] == gone_green["ref"])
rig.click("calls", g.row_of(rig, gone["name"]), "resp")
entry = rig.box()[0]
entry.delete(0, "end")
entry.insert(0, "No answer")
rig.key(entry, "<Return>")
n = len(rig.fake.sent)
rig.press("auto")
g.settle(rig, 40)
sent = rig.fake.sent[n:]
rig.check("Auto green on: the struck white name is not sent", not any(s[1] == struck["ref"] for s in sent) and rig.fake.fill[struck["ref"]] == "#ffffff",
          [s for s in sent if s[1] == struck["ref"]])
rig.check("...every other made Ok white name is", {s[1] for s in sent if s[0] == "green"} == {c["ref"] for c in made_ok[1:]}, len(sent))
rig.check("...and a struck green name answered No answer still goes white (only greens are held back)",
          ("white", gone["ref"]) in [(s[0], s[1]) for s in sent] and rig.fake.fill[gone["ref"]] == "#ffffff")
rig.click("calls", g.row_of(rig, struck["name"]), "ok")
g.settle(rig)
rig.check("the Green box by hand still turns the struck name green (a person decides)", rig.fake.fill[struck["ref"]] == "#92d050"
          and g.state(rig, struck["name"]) == "on")
total += rig.close()
g.calllog.Roster = g.ORIGINAL_ROSTER

g.calllog.whatsapp.is_for = _real_is_for
case("WhatsApp in the table (7 Nov): his words in the cell, the whole story on hover, and back when the pointer leaves")
import json as _json
rig = g.rig_for("u-wa", "2026-10-23 21:40")
g.calllog.whatsapp.set_replies(True)        # (WhatsApp mode, as with Auto read on - a window opens with it off, 20 Nov)
rig.show("Lena Drayton", FRI)
log = rig.app.pack["calls"]
made = [c for c in log["calls"] if c["call_text"] and not c["ahead"]]
late, answered, quiet = made[-1], made[-2], made[-3]
os.makedirs(rig.edits, exist_ok=True)
with open(os.path.join(rig.edits, log["stem"] + " whatsapp.json"), "w", encoding="utf-8") as f:
    _json.dump({"__shift__": {"waiting": True, "since": "16:30", "since_at": FRI + "T16:30:00"},
                g.calllog.edit_key(late): {"time": late["call_text"], "how": "check", "response": "", "said": "I will be 10 minutes late", "at": late["call_text"]},
                g.calllog.edit_key(answered): {"time": answered["call_text"], "how": "reply", "response": "Ok", "said": "Yes indeed", "at": answered["call_text"]}}, f)
rig.show("Lena Drayton", FRI)
grid = rig.grid("calls")
cell = lambda c: next(r for r in grid.rows if r.get("key") == g.calllog.edit_key(c))["cells"]["resp"]
rig.check("his words are in the cell, in quotes - not cut behind 'replied:'", cell(late) == "“I will be 10 minutes late”", cell(late))
rig.check("an answered call just says Ok", cell(answered) == "Ok", cell(answered))
rig.check("a made call with nothing captured says 'reply not read' (not an empty cell)", cell(quiet) == "reply not read", cell(quiet))
before = rig.app.said_label.cget("text")
x, y = grid.cell_on_screen(g.calllog.edit_key(answered), "resp")
grid.body.event_generate("<Motion>", x=x, y=y)
rig.pump(120)
shown = rig.app.said_label.cget("text")
rig.check("pointing at a green from WhatsApp says the words that made it", "Yes indeed" in shown and "taken as Ok" in shown, shown)
x, y = grid.cell_on_screen(g.calllog.edit_key(late), "resp")
grid.body.event_generate("<Motion>", x=x, y=y)
rig.pump(120)
rig.check("...moving to the next row says that row's", "10 minutes late" in rig.app.said_label.cget("text"), rig.app.said_label.cget("text"))
grid.body.event_generate("<Leave>")
rig.pump(120)
rig.check("...and leaving the table puts back what the line said before", rig.app.said_label.cget("text") == before, (before, rig.app.said_label.cget("text")))
grid.body.event_generate("<Motion>", x=x, y=y)
rig.pump(80)
rig.show("Soren Rask", FRI)
rig.check("...and when the rows are rebuilt under the pointer it never keeps describing a call that is gone",
          "10 minutes late" not in rig.app.said_label.cget("text"), rig.app.said_label.cget("text"))
rig.check("no error inside the window while doing all that", not rig.errors, rig.errors[:1])
total += rig.close()

case("WhatsApp took back a yes (7 Nov): Auto green had turned him green - it turns him white again")
rig = g.rig_for("u-tb", "2026-10-23 21:40", settings={"auto_green_for": ["Lena Drayton"]})
g.calllog.whatsapp.set_replies(True)        # (WhatsApp mode, as with Auto read on - a window opens with it off, 20 Nov)
rig.show("Lena Drayton", FRI)
log = rig.app.pack["calls"]
made = [c for c in log["calls"] if c["call_text"] and not c["ahead"] and c.get("ref") and not c.get("strike")
        and g.rostercolour.changeable(c.get("fill"))]
took, plain_blank = made[-1], made[-2]
os.makedirs(rig.edits, exist_ok=True)
with open(os.path.join(rig.edits, log["stem"] + " whatsapp.json"), "w", encoding="utf-8") as f:
    _json.dump({"__shift__": {"waiting": True, "since": "16:30", "since_at": FRI + "T16:30:00"},
                g.calllog.edit_key(took): {"time": took["call_text"], "how": "check", "response": "", "said": "Wrong text sorry",
                                           "at": took["call_text"], "taken_back": True, "first": "Yeah all good"},
                g.calllog.edit_key(plain_blank): {"time": plain_blank["call_text"], "how": "asked", "response": ""}}, f)
for c in (took, plain_blank):                          # as if Auto green had already turned both green
    rig.app.pending[(c["ref"], c["name"])] = (True, rig.app.view_at)
    tried_key = (log["stem"], g.calllog.edit_key(c))
    rig.app.tried[tried_key] = time.time()
    rig.app.tried_target[tried_key] = "green"
due = {(c["name"], action) for c, action, _, _ in rig.app.auto_due()}
rig.check("the yes he took back goes white immediately, even just after auto green tried that name", (took["name"], "white") in due, sorted(due)[:6])
rig.check("...but a call merely waiting for a reply is left as it is (a blank alone never whitens)",
          not any(n == plain_blank["name"] for n, _ in due), [d for d in due if d[0] == plain_blank["name"]])
tried_key = (log["stem"], g.calllog.edit_key(took))
rig.app.tried_target[tried_key] = "white"
rig.check("a failed attempt at that same white still waits five minutes", not any(c["name"] == took["name"] for c, _, _, _ in rig.app.auto_due()))
rig.app.tried[tried_key] -= 301
rig.check("...then the white is eligible to retry", any(c["name"] == took["name"] and action == "white" for c, action, _, _ in rig.app.auto_due()))
for manual in ("green", "white"):
    rig.app.decide(log["stem"], g.calllog.edit_key(took), manual)
    rig.check("a manual %s decision still owns the name after a WhatsApp withdrawal" % manual,
              not any(c["name"] == took["name"] for c, _, _, _ in rig.app.auto_due()))
rig.check("no error inside the window", not rig.errors, rig.errors[:1])
total += rig.close()

case("WhatsApp withdraws while green is in bullseye: settle on the latest answer without a five-minute wait")
rig = g.rig_for("u-tb-bullseye", "2026-10-23 21:40")
g.calllog.whatsapp.set_replies(True)        # (WhatsApp mode, as with Auto read on - a window opens with it off, 20 Nov)
rig.show("Lena Drayton", FRI)
log = rig.app.pack["calls"]
took = next(c for c in log["calls"] if c["call_text"] and not c["ahead"] and c.get("ref") and not c.get("strike")
            and g.rostercolour.css(c.get("fill")) == g.rostercolour.WHITE)
ekey = g.calllog.edit_key(took)
auto_logs = rig.app.auto_logs


def just_this_call():
    return [dict(l, calls=[c for c in l["calls"] if c.get("ref") == took["ref"] and c["name"] == took["name"]]) for l in auto_logs()]


def heard_response(response, taken_back=False):
    os.makedirs(rig.edits, exist_ok=True)
    with open(os.path.join(rig.edits, log["stem"] + " whatsapp.json"), "w", encoding="utf-8") as f:
        _json.dump({"__shift__": {"waiting": True, "since": "16:30", "since_at": FRI + "T16:30:00"},
                    ekey: {"time": took["call_text"], "how": "check" if taken_back else "reply", "response": response,
                           "said": "Wrong text sorry" if taken_back else "Yeah all good", "at": took["call_text"],
                           "taken_back": taken_back, "first": "Yeah all good"}}, f)
    rig.app.rebuild()
    rig.app.refresh()


rig.app.auto_logs = just_this_call
heard_response("Ok")
rig.fake.gate = gate = threading.Event()
rig.app.auto_for.add("Lena Drayton")
rig.app.auto_run()
rig.pump(150)
rig.check("the affirmative reply has started one green request", rig.app.auto_busy and rig.app.sending.get((took["ref"], took["name"])) == "green")
heard_response("", taken_back=True)
gate.set()
rig.fake.gate = None
g.settle(rig, 5)
rig.check("the green arriving late is followed by white immediately", [s[0] for s in rig.fake.sent] == ["green", "white"]
          and rig.fake.fill[took["ref"]] == g.rostercolour.WHITE, rig.fake.sent)
heard_response("Ok")
rig.app.auto_run()
g.settle(rig, 5)
rig.check("a new affirmative reply can immediately turn the name green again", [s[0] for s in rig.fake.sent] == ["green", "white", "green"]
          and rig.fake.fill[took["ref"]] == g.rostercolour.GREEN, rig.fake.sent)
rig.app.decide(log["stem"], ekey, "green")
heard_response("", taken_back=True)
before = len(rig.fake.sent)
rig.app.auto_run()
g.settle(rig, 2)
rig.check("an explicit manual Green-box decision blocks WhatsApp from taking ownership", len(rig.fake.sent) == before
          and rig.fake.fill[took["ref"]] == g.rostercolour.GREEN)
rig.app.decide(log["stem"], ekey, None)
g.calllog.save_edit(log, took, "response", "Ok")
rig.app.rebuild()
rig.app.auto_run()
g.settle(rig, 2)
rig.check("a response confirmed Ok by hand wins over the withdrawn WhatsApp reply", len(rig.fake.sent) == before
          and next(c for c in rig.app.pack["calls"]["calls"] if c["ref"] == took["ref"])["response"] == "Ok")
g.calllog.save_edit(log, took, "response", None)
rig.fake.fail = "synthetic refusal"
rig.app.auto_run()
g.settle(rig, 2)
after_failure = len(rig.fake.sent)
rig.app.auto_run()
rig.pump(200)
rig.check("a failed white is not retried in a tight loop", after_failure == before + 1 and len(rig.fake.sent) == after_failure
          and rig.fake.sent[-1][0] == "white", rig.fake.sent)
rig.check("requests stay serial throughout the reply changes", rig.fake.maxflight == 1, rig.fake.maxflight)
rig.check("no error inside the window", not rig.errors, rig.errors[:1])
total += rig.close()

case("An explicit Ok is evidence; merely opening the generated Ok is not")
rig = g.rig_for("u-explicit-ok", "2026-10-23 20:06")
g.calllog.whatsapp.set_replies(True)        # (WhatsApp mode, as with Auto read on - a window opens with it off, 20 Nov)
rig.show("Lena Drayton", FRI)
log = rig.app.pack["calls"]
candidate = next(c for c in log["calls"] if c["call_text"] and not c["ahead"] and not c.get("strike")
                 and c["response"] == "Ok" and not c.get("heard") and not g.calllog.whatsapp.trusted(c["name"])
                 and g.rostercolour.css(c.get("fill")) == g.rostercolour.WHITE)
ekey, stem = g.calllog.edit_key(candidate), log["stem"]


def explicit_response():
    return g.calllog.load_edits(stem).get(ekey, {}).get("response")


def candidate_due():
    rig.app.auto_for.add("Lena Drayton")
    try:
        return any(c["ref"] == candidate["ref"] and action == "green" for c, action, _, _ in rig.app.auto_due())
    finally:
        rig.app.auto_for.discard("Lena Drayton")


def open_generated_ok():
    rig.click("calls", g.row_of(rig, candidate["name"]), "resp")


rig.check("a generated Ok has no explicit response and cannot auto green", explicit_response() is None and not candidate_due())
open_generated_ok()
rig.click_away()
rig.check("opening then blurring the unchanged Ok records no confirmation", explicit_response() is None and not candidate_due())
open_generated_ok()
rig.type(None, "<Tab>")
rig.app.settle()
rig.check("opening then tabbing away records no confirmation", explicit_response() is None and not candidate_due())
open_generated_ok()
rig.app.settle()
rig.check("settling an untouched editor records no confirmation", explicit_response() is None and not candidate_due())
open_generated_ok()
rig.app.call_for(ekey)["response"] = "No answer"       # a background model update while its cell is open
rig.app.refill()
rig.check("refill really replaces the editor text without recording user intent",
          rig.box()[0].get() == "No answer" and rig.grid("calls").untouched() and explicit_response() is None)
rig.app.call_for(ekey)["response"] = "Ok"
rig.app.refill()
rig.click_away()
rig.check("a background refill then blur records no confirmation", explicit_response() is None and not candidate_due())

open_generated_ok()
next(w for w in rig.grid("calls").picker.winfo_children() if w.cget("text") == "Ok").event_generate("<Button-1>")
rig.pump(150)
rig.app.settle()
rig.check("explicitly picking the displayed Ok persists confirmation", explicit_response() == "Ok" and candidate_due())
rig.app.rebuild()
rig.app.refresh()
rig.check("the explicit response survives a log rebuild", explicit_response() == "Ok" and rig.app.call_for(ekey)["response"] == "Ok" and candidate_due())
undo_count = len(rig.app.undone)
open_generated_ok()
next(w for w in rig.grid("calls").picker.winfo_children() if w.cget("text") == "Ok").event_generate("<Button-1>")
rig.pump(150)
rig.app.settle()
rig.check("picking Ok again is idempotent", explicit_response() == "Ok" and len(rig.app.undone) == undo_count)
rig.app.undo()
rig.check("undo removes the manual evidence while the generated Ok remains", explicit_response() is None and rig.app.call_for(ekey)["response"] == "Ok" and not candidate_due())
rig.app.redo()
rig.check("redo restores the manual evidence", explicit_response() == "Ok" and candidate_due())
rig.app.undo()
open_generated_ok()
rig.type("Ok")
rig.click_away()
rig.check("deleting and retyping the same Ok is an explicit confirmation", explicit_response() == "Ok" and candidate_due())
rig.app.undo()
open_generated_ok()
rig.type(None, "<Return>")
rig.app.settle()
rig.check("pressing Enter explicitly confirms the displayed Ok", explicit_response() == "Ok" and candidate_due())
open_generated_ok()
rig.type("", "<Return>")
rig.app.settle()
rig.check("clearing an explicit Ok removes its evidence even when the generated Ok remains",
          explicit_response() is None and rig.app.call_for(ekey)["response"] == "Ok" and not candidate_due())
rig.app.undo()
rig.check("undoing the clear restores the explicit confirmation", explicit_response() == "Ok" and candidate_due())
rig.check("all evidence checks left the fake roster untouched", not rig.fake.sent, rig.fake.sent)
rig.check("no error inside the window", not rig.errors, rig.errors[:1])
total += rig.close()

print("\n%d of %d checks passed" % (sum(total), len(total)))
sys.exit(0 if all(total) else 1)
