"""The Confirmed box in the window, clicked the way a person does, against a stand-in for the roster's script that
keeps the very same rules as the real one (rostercolour.SCRIPT; tested for real in t_script.py) - so nothing here can
reach the roster. The pinned roster copy is only read; the link file, the setup folder and the corrections are scratch.
The user's rules (2026-10-23): only today's names (the shift on now, past midnight too); green or back to white only;
"this can't go bad"."""
import hashlib
import os
import re
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(tempfile.gettempdir(), "ccl-tests", "green")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)
os.environ["CCL_ROSTER_LINK"] = os.path.join(WORK, "roster-link.json")     # (before anything imports rostercolour)
FIXTURE = os.path.join(HERE, "roster-2026-10-19.xlsx")
REAL_LINK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache", "roster-link.json")
digest = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
# (what is compared is the CONTENT of the real link file: the user's own window may be open while this runs,
#  re-reading the roster and saving its settings - and the checks' own paths are checked to be scratch ones)
linked = lambda: open(REAL_LINK, encoding="utf-8").read() if os.path.exists(REAL_LINK) else None
before = {"fixture": digest(FIXTURE), "link": linked()}

from drive6 import Rig  # noqa: E402
import fakescript  # noqa: E402

total = []
URL = "https://script.google.com/macros/s/AKfycbTESTONLY-not-a-real-deployment_0123/exec"


class FakeScript(fakescript.Script):
    """The roster's script (fakescript.Script: the real one's checks and answers) over a copy of the pinned roster."""

    def __init__(self, calllog, today, key, hour=0):
        super().__init__(today, key, hour=hour)
        self.load_roster(calllog.Roster(FIXTURE), calllog)


def case(title):
    print("\n=== " + title)


def rig_for(name, now):
    import datetime
    rig = Rig(name, now=now, roster=FIXTURE)
    import rostercolour
    rostercolour.CONFIG = os.path.join(WORK, "roster-link.json")
    rostercolour.SETUP = os.path.join(WORK, "Roster script")
    rig.rc = rostercolour
    at = datetime.datetime.strptime(now, "%Y-%m-%d %H:%M")
    rig.fake = FakeScript(rig.calllog, at.date(), rostercolour.load()["key"], hour=at.hour)
    rostercolour.send = rig.fake.send
    return rig


def row_of(rig, name):
    grid = rig.grid("calls")
    return next(i for i, k in enumerate(grid.keys) if rig.app.call_for(k)["name"] == name)


def state(rig, name):
    grid = rig.grid("calls")
    return grid.state_of(grid.keys[row_of(rig, name)], "ok")


def painted(rig, name):
    grid = rig.grid("calls")
    key = grid.keys[row_of(rig, name)]
    return bool(next(r for r in grid.rows if r.get("key") == key).get("paint", {}).get("name"))


def g_on(rig, call):
    return state(rig, call["name"]) == "on"


def settle(rig, count, seconds=10):
    rig.wait(lambda: len(rig.fake.sent) >= count and not rig.app.sending, seconds)
    rig.pump(150)


# -- 1. Today's shift, not switched on yet: the box asks for the link ----------------------------------------------
case("1. Today, Fri 23 Oct 20:06, Lena: switching it on")
rig = rig_for("g1", "2026-10-23 20:06")
rig.show("Lena Drayton", "2026-10-23")
calls = rig.calls()
boxes = [state(rig, c["name"]) for c in calls]
rig.check("every call today with a white, yellow or green name has a box; others none",
          all((b in ("on", "off")) == rig.rc.changeable(c["fill"]) for b, c in zip(boxes, calls)) and boxes.count("off") > 20, (boxes.count("off"), boxes.count("on"), boxes.count("")))
orange = [c for c in calls if rig.rc.css(c["fill"]) == "#ff9900"]
rig.check("an orange name (Ruben Corbett) has no box - left to a person", orange and all(state(rig, c["name"]) == "" for c in orange), [c["name"] for c in orange])
rig.check("...and its note says orange, not 'coloured differently'", any("Ruben Corbett at The Meadows is marked orange on the roster" in n for n in rig.app.pack["calls"]["notes"]))
first = next(c for c in calls if state(rig, c["name"]) == "off")
rig.click("calls", row_of(rig, first["name"]), "ok")
rig.check("not switched on: a click opens the link box and sends nothing", rig.app.link_box is not None and rig.app.link_box.winfo_exists() and not rig.fake.sent)
rig.check("...the box is off the screen with the test window (never in front of the user)", rig.app.link_box.winfo_rootx() < 0, rig.app.link_box.winfo_rootx())
rig.check("...the script and the steps are written out (to the scratch folder here)",
          os.path.exists(os.path.join(WORK, "Roster script", "Roster script.gs")) and os.path.exists(os.path.join(WORK, "Roster script", "How to switch it on.txt")))
fields = [w for w in rig.app.link_box.winfo_children() if w.winfo_class() == "Frame"]
entry = next(w for f in [rig.app.link_box] + list(rig.app.link_box.winfo_children()) for w in [f] + list(f.winfo_children()) if w.winfo_class() == "Entry")
save = next(w for f in rig.app.link_box.winfo_children() for w in f.winfo_children() if getattr(w, "text", "") == "Save and test")
entry.delete(0, "end")
entry.insert(0, "https://docs.google.com/spreadsheets/d/1DemoRosterSheetIdNotARealOne_0123456789abcd/edit")
rig.press(save)
rig.check("a link that is not the script's is turned down, not saved, nothing sent",
          "not the link" in rig.app.link_msg.cget("text") and rig.rc.load()["url"] == "" and not rig.fake.sent, rig.app.link_msg.cget("text"))
entry.delete(0, "end")
entry.insert(0, URL)
rig.press(save)
settle(rig, 2)
rig.check("the right link: tested (ping), saved, the box closes", rig.fake.sent[0][0] == "ping" and rig.rc.load()["url"] == URL and not rig.app.link_box.winfo_exists(), rig.fake.sent)
rig.check("...and the click that was waiting goes ahead: that one name, green", rig.fake.sent[1][:3] == ("green", first["ref"], first["name"]) and state(rig, first["name"]) == "on"
          and painted(rig, first["name"]), rig.fake.sent)
rig.check("...the roster stand-in has that cell green, and no other", rig.fake.fill[first["ref"]] == "#92d050"
          and sum(1 for k, v in rig.fake.fill.items() if v == "#92d050") == sum(1 for k, v in FakeScript(rig.calllog, rig.fake.today, "x").fill.items() if v == "#92d050") + 1)
total += rig.close()

# -- 2. Today, switched on: one click green, another white; busy; Ctrl+G; refusals ---------------------------------
case("2. Today: clicks, Ctrl+G, and what can go wrong")
rig = rig_for("g2", "2026-10-23 20:06")
rig.rc.save({"url": URL, "key": rig.rc.load()["key"]})
rig.show("Lena Drayton", "2026-10-23")
whites = [c for c in rig.calls() if state(rig, c["name"]) == "off"]
a, b, c3 = whites[0], whites[1], whites[2]
undo = len(rig.app.undone)
rig.click("calls", row_of(rig, a["name"]), "ok")
settle(rig, 1)
rig.check("one click: exactly one request - green, that person's own cell and name", rig.fake.sent == [("green", a["ref"], a["name"], URL)] and state(rig, a["name"]) == "on", rig.fake.sent)
rig.check("...the name is drawn on green, and the line under the table says so", painted(rig, a["name"]) and "green on the roster" in rig.said(), rig.said())
rig.click("calls", row_of(rig, a["name"]), "ok")
settle(rig, 2)
rig.check("a second click: back to white", rig.fake.sent[-1][:3] == ("white", a["ref"], a["name"]) and state(rig, a["name"]) == "off" and not painted(rig, a["name"])
          and rig.fake.fill[a["ref"]] == "#ffffff")
rig.check("each Green press can be undone (Ctrl+Z undoes anything): two presses, two steps", len(rig.app.undone) == undo + 2)
rig.key(rig.grid("calls").body, "<Control-z>")
settle(rig, 3)
rig.check("...Ctrl+Z: the last press (white) undone - green again on the roster", g_on(rig, a) and rig.fake.fill[a["ref"]] == "#92d050", rig.fake.sent[-1:])
rig.key(rig.grid("calls").body, "<Control-y>")
settle(rig, 4)
rig.check("...Ctrl+Y: white again", not g_on(rig, a) and rig.fake.fill[a["ref"]] == "#ffffff")
rig.check("the hand's decision is kept with the shift, by person (not by roster cell)",
          any(f.endswith(" green.json") for f in os.listdir(rig.edits)))
rig.fake.gate = threading.Event()
n2 = len(rig.fake.sent)
rig.click("calls", row_of(rig, b["name"]), "ok")
rig.pump(150)
rig.check("while the roster is being asked, the box shows it is busy", state(rig, b["name"]) == "busy")
rig.click("calls", row_of(rig, b["name"]), "ok")
rig.pump(150)
rig.fake.gate.set()
settle(rig, n2 + 1)
rig.fake.gate = None
rig.check("...and a second click then does nothing: one request, not two", [s[:2] for s in rig.fake.sent[n2:]] == [("green", b["ref"])] and state(rig, b["name"]) == "on", rig.fake.sent[n2:])
n3 = len(rig.fake.sent)
rig.click("calls", row_of(rig, a["name"]), "name")
rig.click("calls", row_of(rig, c3["name"]), "name", ctrl=True)
rig.key(rig.grid("calls").body, "<Control-g>")
settle(rig, n3 + 2)
rig.check("Ctrl+G with two rows chosen: both green, two requests", sorted(s[1] for s in rig.fake.sent[n3:n3 + 2]) == sorted([a["ref"], c3["ref"]]) and all(s[0] == "green" for s in rig.fake.sent[n3:n3 + 2])
          and state(rig, a["name"]) == state(rig, c3["name"]) == "on", rig.fake.sent[n3:])
rig.key(rig.grid("calls").body, "<Control-g>")
settle(rig, n3 + 4)
rig.check("Ctrl+G again: both back to white", all(s[0] == "white" for s in rig.fake.sent[n3 + 2:n3 + 4]) and state(rig, a["name"]) == state(rig, c3["name"]) == "off")
n = len(rig.fake.sent)
rig.fake.text[a["ref"]] = "Somebody Else"                    # the roster changed under the window
rig.click("calls", row_of(rig, a["name"]), "ok")
settle(rig, n + 1)
rig.check("the roster changed (another name in that cell): nothing changed, it says so and reads the roster again",
          state(rig, a["name"]) == "off" and "changed since it was read" in rig.said() and rig.fake.fill[a["ref"]] == "#ffffff", rig.said())
rig.fake.text[a["ref"]] = a["name"]
rig.fake.fail = "the roster could not be reached (no network)"
rig.click("calls", row_of(rig, a["name"]), "ok")
settle(rig, n + 2)
rig.check("no network: a plain message, nothing changed, no crash", state(rig, a["name"]) == "off" and "could not be reached" in rig.said() and not rig.errors, rig.said())
rig.fake.fail = None
rig.fake.today = rig.fake.today.replace(day=rig.fake.today.day + 3)    # the script's own clock says another day
rig.click("calls", row_of(rig, a["name"]), "ok")
settle(rig, n + 3)
rig.check("the script refuses another day: nothing changed, it says why", state(rig, a["name"]) == "off" and "only changes today's names" in rig.said(), rig.said())
rig.fake.today = rig.fake.today.replace(day=rig.fake.today.day - 3)
rig.rc.save({"url": URL, "key": "a-different-key-entirely-0000000000"})
rig.app.link = None
rig.click("calls", row_of(rig, a["name"]), "ok")
settle(rig, n + 4)
rig.check("a key that does not match the script: refused, nothing changed", state(rig, a["name"]) == "off" and "wrong key" in rig.said(), rig.said())
total += rig.close()

# -- 3. Other days: no boxes, nothing sent ---------------------------------------------------------------------------
case("3. Tomorrow and yesterday cannot be changed")
rig = rig_for("g3", "2026-10-23 20:06")
rig.rc.save({"url": URL, "key": rig.rc.load()["key"]})
for day, words in (("2026-10-24", "tomorrow"), ("2026-10-22", "yesterday (a finished day)"), ("2026-10-30", "next week")):
    rig.show("Lena Drayton" if day != "2026-10-22" else "Basil Jankowski", day)
    rows = [state(rig, c["name"]) for c in rig.calls()]
    rig.click("calls", 0, "ok")
    rig.key(rig.grid("calls").body, "<Control-g>")
    rig.pump(300)
    rig.check("%s: no box on any row, a click and Ctrl+G send nothing, and it says only today's" % words,
              set(rows) == {""} and not rig.fake.sent and "Only today's" in rig.said(), (set(rows), rig.fake.sent[-1:], rig.said()))
rig.show("Lena Drayton", "2026-10-17")
greens = [c for c in rig.calls() if rig.rc.is_green(c["fill"])]
rig.check("a finished day still shows who is green on the roster (drawn, not changeable)", len(greens) > 20 and all(painted(rig, c["name"]) for c in greens) and
          not any(painted(rig, c["name"]) for c in rig.calls() if not rig.rc.is_green(c["fill"])) and {state(rig, c["name"]) for c in rig.calls()} == {""}, len(greens))
total += rig.close()

# -- 4. A night shift past midnight is still today's ----------------------------------------------------------------
case("4. 01:30 on Sat 24 Oct: Friday night's shift is on")
rig = rig_for("g4", "2026-10-24 01:30")
rig.rc.save({"url": URL, "key": rig.rc.load()["key"]})
rig.show("Lena Drayton", "2026-10-23")
target = next(c for c in rig.calls() if state(rig, c["name"]) == "off")
rig.click("calls", row_of(rig, target["name"]), "ok")
settle(rig, 1)
rig.check("its names can still be turned green (the script allows yesterday's column)", rig.fake.sent[-1][:3] == ("green", target["ref"], target["name"]) and state(rig, target["name"]) == "on")
rig.show("Lena Drayton", "2026-10-24")
rig.check("...while Saturday's own log (later today) has boxes too", any(state(rig, c["name"]) == "off" for c in rig.calls()))
total += rig.close()

# -- 5. Things around it: the print, the look, closing mid-request --------------------------------------------------
case("5. The print, the dark look, closing while a request is out")
rig = rig_for("g5", "2026-10-23 20:06")
rig.rc.save({"url": URL, "key": rig.rc.load()["key"]})
rig.show("Lena Drayton", "2026-10-23")
rig.app.flush()
printed = rig.pdf_text(rig.files()["calls"])
target = next(c for c in rig.calls() if state(rig, c["name"]) == "off")
rig.click("calls", row_of(rig, target["name"]), "ok")
settle(rig, 1)
rig.app.flush()
rig.check("the printed log is the same before and after (green is on the roster, not on the paper)", rig.pdf_text(rig.files()["calls"]) == printed)
call = next(c for c in rig.calls() if c["ref"] == target["ref"])
rig.check("until the roster is read again, the click is remembered", (target["ref"], target["name"]) in rig.app.pending)
call["fill"] = "FF92D050"                                   # a fresh copy of the roster that shows it green
rig.check("a fresh roster copy that shows it: the memory goes, the roster's own colour is shown", rig.app.green(call) == "on" and (target["ref"], target["name"]) not in rig.app.pending)
call["fill"] = "theme0"                                     # ...and later someone makes it white again by hand in the sheet
rig.check("...so a change made by hand on the roster afterwards shows too (the roster always has the last word)", rig.app.green(call) == "off")
call["fill"] = "FF92D050"
rig.grid("calls").chosen = set()
rig.grid("calls").cursor = None
rig.key(rig.grid("calls").body, "<Control-g>")
rig.check("Ctrl+G with no call chosen: it says to click one first, sends nothing", "Click a call first" in rig.said() and len(rig.fake.sent) == 1, rig.said())
rig.key(rig.grid("calls").body, "<Control-d>")
rig.pump(400)
rig.check("switching to the dark look keeps the name green", state(rig, target["name"]) == "on" and painted(rig, target["name"]))
rig.fake.gate = threading.Event()
other = next(c for c in rig.calls() if state(rig, c["name"]) == "off")
rig.click("calls", row_of(rig, other["name"]), "ok")
rig.pump(100)
rig.app.on_close()
rig.fake.gate.set()
import time
time.sleep(0.5)
rig.check("closing the window while a request is out: no error, nothing left waiting on the window", not rig.errors and rig.app.gone)
total += rig.results

after = {"fixture": digest(FIXTURE), "link": linked()}
print()
import rostercolour  # noqa: E402
ok = after == before and rostercolour.CONFIG.startswith(WORK)
print("%s  the pinned roster and the real link file are untouched, and the link file used was a scratch one  %s" % ("PASS" if ok else "FAIL", "" if ok else (before, after)))
total.append(ok)
print("\n%d of %d checks passed" % (sum(total), len(total)))
shutil.rmtree(WORK, ignore_errors=True)
sys.exit(0 if all(total) else 1)
