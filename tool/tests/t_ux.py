"""Version 6.7, clicked and timed the way a person meets it (asked for on 2026-10-24):
- at a handover the window moves on by itself to the controller who has just come on - from the outgoing shift only,
  not while something is typed - writing the outgoing log and offering to print it;
- Auto green is on or off per controller: on for Lena Drayton, off for everyone else, by default;
- a window started for a check can never reach the live roster's script;
- the screen of 6.6: the line under the table wraps, the top line counts green, o / n / l pick a response, the
  choices stay on screen, an untouched open cell does not freeze the table, all notes can be shown, a row's note
  is said when the row is clicked.
Nothing here reaches the real roster (see greenrig.py)."""
import os
import sys

import greenrig as g

total = []
FRI = "2026-10-23"


def case(title):
    print("\n=== " + title)


# -- 1. Handover ---------------------------------------------------------------------------------------------------------
case("1. Handover: Soren's log is on show when Lena comes on at 16:30")
rig = g.rig_for("u1", "2026-10-23 16:20")
rig.show("Soren Rasmussen", FRI)
rig.at("2026-10-23 16:29")
rig.check("before the handover: Soren's log stays (chosen by hand, and his calls are the ones being made)", rig.app.shown_who() == "Soren Rasmussen")
rig.at("2026-10-23 16:30")
rig.check("16:30, Lena comes on: the window moves to Lena's shift by itself", rig.app.shown_who() == "Lena Drayton" and rig.app.pack["date"].isoformat() == FRI,
          (rig.app.shown_who(), rig.app.dates))
rig.check("...Soren's logs were written first", any(k[1] == "Soren Rasmussen" and v["paths"] for k, v in rig.app.files.items()), list(rig.app.files))
note = rig.app.notice
rig.check("...and the notice says so, with a button to print Soren's", "Handover at 16:30" in note.text and "Soren Rasmussen's logs" in note.text
          and note.button is not None and note.button.text == "Print Soren's", (note.text, note.button and note.button.text))
rig.press(note.button)
rig.check("the button opens Soren's logs to print, and the notice goes", "Opened Soren Rasmussen's logs" in rig.said() and rig.app.handed is None and rig.app.notice.kind is None, rig.said())
rig.at("2026-10-23 16:45")
rig.check("it then stays with Lena (it follows the desk again)", rig.app.shown_who() == "Lena Drayton")
total += rig.close()

case("2. Handover: not from another day's log, not while typing")
rig = g.rig_for("u2", "2026-10-23 16:20")
rig.show("Soren Rasmussen", "2026-10-22")
rig.at("2026-10-23 16:29")
rig.at("2026-10-23 16:30")
rig.check("Soren's log of the day before on show: it stays (somebody is looking at it)", rig.app.shown_who() == "Soren Rasmussen" and rig.app.pack["date"].isoformat() == "2026-10-22")
rig.show("Soren Rasmussen", FRI)
rig.app.desk_was = ("Soren Rasmussen", rig.app.pack["date"])           # (as if it were still before the handover)
row = next(i for i, c in enumerate(rig.calls()) if c["call_text"])
rig.click("calls", row, "resp")
rig.box()[0].insert("end", " - rang back")          # (typed, not yet saved)
rig.at("2026-10-23 16:31")
rig.check("something being typed at the handover: it waits", rig.app.shown_who() == "Soren Rasmussen" and rig.box() is not None)
rig.type(None, "<Escape>")
rig.at("2026-10-23 16:32")
rig.check("...and moves on at the next minute once the typing is done", rig.app.shown_who() == "Lena Drayton")
total += rig.close()

case("3. Handover at 06:00: Lena's night to Soren's day")
rig = g.rig_for("u3", "2026-10-24 05:58")
rig.show("Lena Drayton", FRI)
rig.at("2026-10-24 05:59")
rig.check("05:59: still Lena's night", rig.app.shown_who() == "Lena Drayton")
rig.at("2026-10-24 06:00")
rig.check("06:00: Soren's day, and a notice to print Lena's night", rig.app.shown_who() == "Soren Rasmussen" and "Lena Drayton's logs" in rig.app.notice.text,
          (rig.app.shown_who(), rig.app.notice.text))
total += rig.close()

# -- 4. Auto green per controller -----------------------------------------------------------------------------------------
case("4. Auto green: on for Lena, off for everyone else, by default")
rig = g.rig_for("u4", "2026-10-23 16:20", settings={})                # (no saved setting: the defaults)
rig.show("Lena Drayton", FRI)
rig.check("with nothing saved: on for Lena Drayton", rig.app.auto_for == {"Lena Drayton"} and rig.app.buttons["auto"].text.startswith("Auto green: on"))
rig.show("Soren Rasmussen", FRI)
rig.check("...and off for Soren (the button shows the controller on show)", rig.app.buttons["auto"].text == "Auto green: off" and not rig.app.auto_green)
rig.press("auto")
rig.check("switching it on for Soren leaves Lena's on too", rig.app.auto_for == {"Lena Drayton", "Soren Rasmussen"})
rig.show("Lena Drayton", FRI)
rig.press("auto")
rig.check("switching Lena's off leaves Soren's on", rig.app.auto_for == {"Soren Rasmussen"} and rig.app.buttons["auto"].text == "Auto green: off")
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
    rig.check("the newest script in the roster: 'up to date (version 3)'", "The roster's script is up to date (version 3)" in rig.said()
              and rig.fake.sent[-1][0] == "ping", rig.said())
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
rig.app.pinged({"ok": True, "sheet": "Sheet1", "version": 3}, None)
rig.check("the link test names the version (newest)", "version 3, the newest" in rig.said(), rig.said())
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

print("\n%d of %d checks passed" % (sum(total), len(total)))
sys.exit(0 if all(total) else 1)
