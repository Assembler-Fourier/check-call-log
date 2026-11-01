"""Auto green, the response choices, the roster watch and yellow (version 6.5, asked for on 2026-10-23), clicked and
timed the way a person meets them. Rules under test:
- Auto green only turns a name green once its call's time has passed, on the shift on now, when the response is Ok,
  when the name is white on the roster; never yellow (not confirmed - the shift may not be on) or another colour,
  never a name turned back to white by hand, one request at a time, and it stops when switched off.
- Only Ok means confirmed: a name auto green made green goes back to white when its response becomes anything else,
  and may go green again when it is set back to Ok.
- The roster is watched: a small read of the shift's columns every 30 seconds; a change brings the whole roster in
  again and says what changed for this shift.
- Yellow names show on yellow, with one line on the log about them.
Nothing here can reach the real roster (see greenrig.py)."""
import datetime
import hashlib
import os
import sys

import greenrig as g

# (the user's own window may be open while this runs, re-reading the roster and saving its settings: so what is
#  compared is the content of the real link file, and the checks' own paths are checked to be scratch ones)
REAL_LINK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache", "roster-link.json")
real_link = lambda: open(REAL_LINK, encoding="utf-8").read() if os.path.exists(REAL_LINK) else None
REAL = real_link()
FIXTURE_HASH = hashlib.sha256(open(g.FIXTURE, "rb").read()).hexdigest()
total = []
FRI, NOW = "2026-10-23", "2026-10-23 20:06"


def case(title):
    print("\n=== " + title)


# the Friday night log as it stands at 20:06 on the plain pinned roster: pick the cases from it
g.calllog.EDITS = os.path.join(g.WORK, "edits-probe")
probe = g.calllog.build(datetime.date(2026, 10, 23), g.ORIGINAL_ROSTER(g.FIXTURE), "Lena Drayton", now=datetime.datetime(2026, 10, 23, 20, 6))
made0 = [c for c in probe["calls"] if c["call_text"] and not c["ahead"] and g.rostercolour.css(c["fill"]) == "#ffffff"]
later = [c for c in probe["calls"] if not c["call_text"] and g.rostercolour.css(c["fill"]) == "#ffffff"]
YELLOW = made0[0]                                    # made, but staged as yellow on the roster
NO_ANSWER = made0[1]                                 # made, but answered "No answer" by hand
print("staged: yellow %s (%s), no answer %s (%s); %d made and white, %d still to come" % (YELLOW["name"], YELLOW["ref"], NO_ANSWER["name"], NO_ANSWER["ref"], len(made0), len(later)))

# -- 1. The toggle -----------------------------------------------------------------------------------------------------
case("1. The Auto green toggle")
saved = {}
rig = g.rig_for("a1", NOW, linked=False, saved=saved)
rig.show("Lena Drayton", FRI)
button = rig.app.buttons["auto"]
rig.check("off to begin with, and says so", not rig.app.auto_green and button.text == "Auto green: off" and not button.lit)
rig.press("auto")
rig.check("not set up yet: switching it on asks for the link instead, and it stays off", not rig.app.auto_green and rig.app.link_box is not None
          and rig.app.link_box.winfo_exists() and not rig.fake.sent)
rig.app.link_box.destroy()
rig.rc.save({"url": g.URL, "key": rig.rc.load()["key"]})
rig.app.link = None
rig.fake.gate = __import__("threading").Event()       # (hold the first answer, to look at it on its way)
rig.press("auto")
rig.check("switched on: lit, says what it does and how many to do now, and is remembered", rig.app.auto_green and button.lit and button.text == "Auto green: on"
          and "Auto green is on for Lena Drayton" in rig.said() and "never yellow" in rig.said() and saved.get("auto_green_for") == ["Lena Drayton"], rig.said())
rig.pump(300)
rig.check("...one name is on its way, the box shows it", sum(1 for c in rig.calls() if g.state(rig, c["name"]) == "busy") == 1)
rig.fake.gate.set()
rig.fake.gate = None
g.settle(rig)
rig.press("auto")
rig.check("switched off: not lit, remembered off", not rig.app.auto_green and not button.lit and saved.get("auto_green_for") == [])
total += rig.close()

# -- 2. What auto green does, and what it never does ----------------------------------------------------------------------
case("2. Auto green on the shift on now")
rig = g.rig_for("a2", NOW, changes={YELLOW["ref"]: (None, "FFFFFF00")})
rig.show("Lena Drayton", FRI)
nrow = g.row_of(rig, NO_ANSWER["name"])
rig.click("calls", nrow, "resp")
picker = rig.grid("calls").picker
words = [w.cget("text") for w in picker.winfo_children()] if picker else []
rig.check("the response cell offers Ok, No answer, Left message - or type (o / n / l)", words == ["Ok", "No answer", "Left message", "or type (o / n / l)"], words)
chip = next(w for w in picker.winfo_children() if w.cget("text") == "No answer")
chip.event_generate("<Button-1>")
rig.pump(300)
rig.check("one click on No answer: saved, the box closes", rig.box() is None and next(c for c in rig.calls() if c["name"] == NO_ANSWER["name"])["response"] == "No answer")
rig.check("the yellow name is drawn on yellow and the log says it is not confirmed",
          g.paint(rig, YELLOW["name"]) == "provisional" and any(YELLOW["name"] in n and "yellow on the roster" in n for n in rig.app.pack["calls"]["notes"]))
rig.press("auto")
g.settle(rig, 40)
greened = {s[1] for s in rig.fake.sent if s[0] == "green"}
expected = {c["ref"] for c in g.made(rig) if c["response"] == "Ok" and g.rostercolour.css(c["fill"]) == "#ffffff"}
rig.check("every call made by 20:06, answered Ok and white is green now", greened == expected and len(expected) >= 10, (len(greened), len(expected)))
rig.check("...one request at a time, never more", rig.fake.maxflight == 1, rig.fake.maxflight)
rig.check("...each sent once", len([s for s in rig.fake.sent if s[0] == "green"]) == len(greened))
rig.check("...never the yellow one", YELLOW["ref"] not in greened and g.state(rig, YELLOW["name"]) == "off")
rig.check("...never the one answered No answer", NO_ANSWER["ref"] not in greened)
rig.check("...never a call still to come", not greened & {c["ref"] for c in rig.calls() if not c["call_text"] or c["ahead"]})
rig.check("...never another colour (orange, red)", not greened & {c["ref"] for c in rig.calls() if g.rostercolour.css(c["fill"]) not in ("#ffffff", "#ffff00", "#92d050")})
rig.check("...the roster stand-in has exactly those green, nothing else changed",
          {k for k, v in rig.fake.fill.items() if v == "#92d050"} - {k for k, v in g.FakeScript(rig.fake.today, "x", g.calllog.Roster(g.FIXTURE)).fill.items() if v == "#92d050"} == greened)
n = len(rig.fake.sent)
rig.at("2026-10-23 20:40")
g.settle(rig, 40)
newly = [s for s in rig.fake.sent[n:] if s[0] == "green"]
due = {c["ref"] for c in g.made(rig) if c["response"] == "Ok" and g.rostercolour.css(c["fill"]) == "#ffffff"} - greened
rig.check("the clock moves on to 20:40: the calls made since then go green, and only those", {s[1] for s in newly} == due and due, (len(newly), len(due)))
first = next(c for c in g.made(rig) if g.state(rig, c["name"]) == "on" and c["ref"] in greened)
rig.click("calls", g.row_of(rig, first["name"]), "ok")
g.settle(rig)
rig.check("one turned back to white by hand", g.state(rig, first["name"]) == "off" and rig.fake.fill[first["ref"]] == "#ffffff")
n = len(rig.fake.sent)
rig.at("2026-10-23 20:41")
g.settle(rig)
rig.check("...auto green leaves it alone after that", not any(s[1] == first["ref"] for s in rig.fake.sent[n:]))
total += rig.close()

# -- 3. Only Ok means confirmed --------------------------------------------------------------------------------------------
case("3. A response that is not Ok undoes auto green; Ok again redoes it")
rig = g.rig_for("a3", NOW)
rig.show("Lena Drayton", FRI)
rig.press("auto")
g.settle(rig, 40)
person = next(c for c in g.made(rig) if g.state(rig, c["name"]) == "on")
rig.click("calls", g.row_of(rig, person["name"]), "resp")
next(w for w in rig.grid("calls").picker.winfo_children() if w.cget("text") == "Left message").event_generate("<Button-1>")
g.settle(rig)
rig.check("Left message on a name auto green made green: back to white on the roster", g.state(rig, person["name"]) == "off" and rig.fake.fill[person["ref"]] == "#ffffff"
          and rig.fake.sent[-1][:2] == ("white", person["ref"]), rig.fake.sent[-1:])
n = len(rig.fake.sent)
rig.at("2026-10-23 20:07")
g.settle(rig)
rig.check("...and auto green does not turn it green again while it is not Ok", not any(s[1] == person["ref"] for s in rig.fake.sent[n:]))
rig.click("calls", g.row_of(rig, person["name"]), "resp")
rig.type("ok", "<Return>")
g.settle(rig)
rig.check("typed 'ok' in small letters counts as Ok, and the name goes green again", next(c for c in rig.calls() if c["ref"] == person["ref"])["response"] == "Ok"
          and g.state(rig, person["name"]) == "on" and rig.fake.fill[person["ref"]] == "#92d050")
other = next(c for c in rig.calls() if c["call_text"] and g.rostercolour.css(c["fill"]) == "#ffffff" and g.state(rig, c["name"]) == "on" and c["ref"] != person["ref"])
rig.click("calls", g.row_of(rig, other["name"]), "ok")
g.settle(rig)
rig.click("calls", g.row_of(rig, other["name"]), "resp")
rig.type("Rang back - on the way", "<Return>")
g.settle(rig)
rig.check("free text still works (anything typed), and a name made white by hand stays white", next(c for c in rig.calls() if c["ref"] == other["ref"])["response"] == "Rang back - on the way"
          and g.state(rig, other["name"]) == "off")
manual = next(c for c in rig.calls() if not c["call_text"] and g.state(rig, c["name"]) == "off")
rig.click("calls", g.row_of(rig, manual["name"]), "ok")
g.settle(rig)
rig.check("the Green box still works by hand on a call still to come (your call)", g.state(rig, manual["name"]) == "on")
rig.click("calls", g.row_of(rig, made0[3]["name"]), "time")
rig.check("the time cell offers no choices", rig.grid("calls").picker is None and rig.box() is not None)
rig.type(None, "<Escape>")
rig.click("calls", g.row_of(rig, made0[3]["name"]), "resp")
rig.type(None, "<Escape>")
rig.check("Esc closes the response box and its choices, nothing saved", rig.box() is None and rig.grid("calls").picker is None)
total += rig.close()

# -- 3b. A name already green on the roster (made green by hand), then answered "No answer" --------------------------------
case("3b. Already green on the roster, then No answer (the user's case: Jerome Yardley, 23 Oct)")
HAND, HAND2 = made0[4], made0[5]
rig = g.rig_for("a3b", NOW, changes={HAND["ref"]: (None, "FF92D050"), HAND2["ref"]: (None, "FF92D050")})
rig.show("Lena Drayton", FRI)
rig.check("both start green on the roster", g.state(rig, HAND["name"]) == "on" and g.state(rig, HAND2["name"]) == "on")
rig.click("calls", g.row_of(rig, HAND2["name"]), "resp")
next(w for w in rig.grid("calls").picker.winfo_children() if w.cget("text") == "No answer").event_generate("<Button-1>")
g.settle(rig)
rig.check("Auto green OFF: No answer leaves the roster alone (the Green box is the hand control)", g.state(rig, HAND2["name"]) == "on"
          and not any(s[1] == HAND2["ref"] for s in rig.fake.sent))
rig.press("auto")
g.settle(rig, 40)
n = len(rig.fake.sent)
rig.click("calls", g.row_of(rig, HAND["name"]), "resp")
next(w for w in rig.grid("calls").picker.winfo_children() if w.cget("text") == "No answer").event_generate("<Button-1>")
g.settle(rig)
rig.check("Auto green ON: No answer on a name made green by hand turns it white on the roster", g.state(rig, HAND["name"]) == "off"
          and rig.fake.fill[HAND["ref"]] == "#ffffff" and ("white", HAND["ref"]) in [s[:2] for s in rig.fake.sent[n:]], rig.fake.sent[n:])
rig.check("...and says why", "not confirmed" in rig.said() and "white" in rig.said(), rig.said())
rig.click("calls", g.row_of(rig, HAND["name"]), "resp")
next(w for w in rig.grid("calls").picker.winfo_children() if w.cget("text") == "Ok").event_generate("<Button-1>")
g.settle(rig)
rig.check("set back to Ok straight away (before the roster is read again): green again", g.state(rig, HAND["name"]) == "on"
          and rig.fake.fill[HAND["ref"]] == "#92d050")
rig.click("calls", g.row_of(rig, HAND["name"]), "resp")
rig.type("Not coming tonight", "<Return>")
g.settle(rig)
rig.check("anything typed that is not Ok does the same: white", g.state(rig, HAND["name"]) == "off" and rig.fake.fill[HAND["ref"]] == "#ffffff")
rig.check("the one answered No answer while Auto green was off went white once it was switched on (the roster follows every response then)",
          g.state(rig, HAND2["name"]) == "off" and rig.fake.fill[HAND2["ref"]] == "#ffffff")

# the user's case on 23 Oct (Ivan Rutledge): No answer, then Ok again WHILE the white is still on its way
rig.click("calls", g.row_of(rig, HAND["name"]), "resp")
next(w for w in rig.grid("calls").picker.winfo_children() if w.cget("text") == "Ok").event_generate("<Button-1>")
g.settle(rig)
rig.check("(set up: Ok again, green)", g.state(rig, HAND["name"]) == "on")
rig.fake.gate = __import__("threading").Event()
rig.click("calls", g.row_of(rig, HAND["name"]), "resp")
next(w for w in rig.grid("calls").picker.winfo_children() if w.cget("text") == "No answer").event_generate("<Button-1>")
rig.pump(200)
rig.check("No answer: the white is on its way", g.state(rig, HAND["name"]) == "busy")
rig.click("calls", g.row_of(rig, HAND["name"]), "resp")
next(w for w in rig.grid("calls").picker.winfo_children() if w.cget("text") == "Ok").event_generate("<Button-1>")
rig.pump(200)
rig.fake.gate.set()
rig.fake.gate = None
g.settle(rig, 20)
rig.check("Ok again before the white came back: it ends up GREEN on the roster (what the response says now decides)",
          g.state(rig, HAND["name"]) == "on" and rig.fake.fill[HAND["ref"]] == "#92d050",
          [s[:2] for s in rig.fake.sent if s[1] == HAND["ref"]][-3:])
# ...and the other way round: No answer while a green is on its way
rig.click("calls", g.row_of(rig, HAND["name"]), "resp")
next(w for w in rig.grid("calls").picker.winfo_children() if w.cget("text") == "No answer").event_generate("<Button-1>")
g.settle(rig)
rig.fake.gate = __import__("threading").Event()
rig.click("calls", g.row_of(rig, HAND["name"]), "resp")
next(w for w in rig.grid("calls").picker.winfo_children() if w.cget("text") == "Ok").event_generate("<Button-1>")
rig.pump(400)
rig.check("Ok: the green is on its way", g.state(rig, HAND["name"]) == "busy")
rig.click("calls", g.row_of(rig, HAND["name"]), "resp")
next(w for w in rig.grid("calls").picker.winfo_children() if w.cget("text") == "Left message").event_generate("<Button-1>")
rig.pump(200)
rig.fake.gate.set()
rig.fake.gate = None
g.settle(rig, 20)
rig.check("Left message before the green came back: it ends up WHITE on the roster",
          g.state(rig, HAND["name"]) == "off" and rig.fake.fill[HAND["ref"]] == "#ffffff", [s[:2] for s in rig.fake.sent if s[1] == HAND["ref"]][-3:])
rig.check("a name made white by the Green box itself is still left alone by auto green", True)
person = next(c for c in rig.calls() if g.state(rig, c["name"]) == "on" and c["response"] == "Ok" and c["ref"] not in (HAND["ref"], HAND2["ref"]))
rig.click("calls", g.row_of(rig, person["name"]), "ok")
g.settle(rig)
n = len(rig.fake.sent)
rig.at("2026-10-23 20:12")
g.settle(rig)
rig.check("...(the box by hand: white, and auto green does not undo it)", g.state(rig, person["name"]) == "off" and not any(s[1] == person["ref"] for s in rig.fake.sent[n:]))
total += rig.close()

# -- 4. Not the shift on now: auto green does nothing -------------------------------------------------------------------
case("4. Whatever is on show, and when the roster cannot be reached")
rig = g.rig_for("a4", NOW)
rig.show("Lena Drayton", "2026-10-24")
rig.fake.fail = "the roster could not be reached (no network)"
rig.press("auto")
g.settle(rig, 20)
rig.check("switched on while tomorrow's log is on show: it belongs to Lena, and works on his shift that is on now",
          rig.app.auto_for == {"Lena Drayton"} and rig.fake.sent and all(s[1].startswith("FK") for s in rig.fake.sent), rig.fake.sent[:2])
rig.check("no network: one try, then it says so, no crash", len(rig.fake.sent) == 1 and "could not be reached" in rig.said() and not rig.errors, (len(rig.fake.sent), rig.said()))
rig.at("2026-10-23 20:07")
g.settle(rig, 20)
rig.check("...and backs off: no name after name - it waits, and its button says so", len(rig.fake.sent) == 1 and "waiting" in rig.app.buttons["auto"].text,
          (len(rig.fake.sent), rig.app.buttons["auto"].text))
rig.fake.fail = None
for k in list(rig.app.tried):
    rig.app.tried[k] -= 301                          # (5 minutes later)
rig.app.quiet_until = 0
rig.at("2026-10-23 20:10")
g.settle(rig, 40)
greens = [s for s in rig.fake.sent if s[0] == "green" and s[1] in {k for k, v in rig.fake.fill.items() if v == "#92d050"}]
rig.check("...later, with the network back, the shift on now goes green - and nothing of tomorrow's", len(greens) >= 10 and not rig.app.auto_due()
          and not any(s[1].startswith("FL") for s in rig.fake.sent), len(greens))
rig.show("Basil Jankowski", "2026-10-22")
n = len(rig.fake.sent)
rig.at("2026-10-23 21:15")
g.settle(rig, 20)
rig.check("Basil's day on show: Lena's calls coming due still go green, nobody else's",
          rig.fake.sent[n:] and all(s[1].startswith("FK") for s in rig.fake.sent[n:]), len(rig.fake.sent[n:]))
total += rig.close()

# -- 5. The roster watched --------------------------------------------------------------------------------------------
case("5. The roster checked every 30 seconds")
os.environ["CCL_WATCH"] = "1"
rig = g.rig_for("a5", NOW)
rig.show("Lena Drayton", FRI)
reads = []
g.calllog.fetch_columns = lambda first, last, rows: reads.append((first, last)) or text["now"]
text = {"now": "Fri\n23-Oct\nsame"}
loads = []
real_load = rig.app.load_roster
rig.app.load_roster = lambda force=False: loads.append(force) or real_load(force)
rig.app.watch()
rig.pump(400)
rig.app.watch()
rig.pump(400)
rig.check("a small read of just the shift's columns (its day and the next), nothing read again while it is the same",
          reads and all(r == (g.calllog.idx_to_col(rig.app.view.day_column(datetime.date(2026, 10, 23))), g.calllog.idx_to_col(rig.app.view.day_column(datetime.date(2026, 10, 24)))) for r in reads) and not loads, (reads[:2], loads))
g.calllog.Roster = g.tweaked({"FK112": ("Ann Example", "theme0")})
rig.app.roster_sig = None                             # (a change on the roster changes its file; this one is staged in memory)
text["now"] = "Fri\n23-Oct\nchanged"
rig.app.watch()
rig.wait(lambda: loads and "The roster changed" in rig.said(), 15)
rig.pump(300)
rig.check("a change: the whole roster is read again and it says what changed for this shift", loads == [True] and "added: Ann Example" in rig.said(), rig.said())
rig.check("...and the new name is on the log", any(c["name"] == "Ann Example" for c in rig.calls()))
g.calllog.fetch_columns = lambda *a: (_ for _ in ()).throw(OSError("no network"))
rig.app.watch()
rig.pump(400)
rig.check("no network for the small read: nothing happens, no error", len(loads) == 1 and not rig.errors)
n = len(reads)
g.calllog.fetch_columns = lambda first, last, rows: reads.append((first, last)) or text["now"]
rig.app.watch()
rig.pump(300)
rig.check("...and the next checks wait a while (back-off), they do not hammer Google", len(reads) == n and rig.app.quiet_until > 0)
rig.app.quiet_until = 0
rig.app.watch_count = g.calllog and rig.mod.WATCH_FULL - 1
g.calllog.fetch_columns = lambda first, last, rows: text["now"]
rig.app.watch()
rig.pump(300)
rig.check("every few checks the whole roster is read anyway (for colours set by hand)", len(loads) == 2, loads)
job = rig.app.watch_job
rig.check("the next check is booked, 30 seconds on", job is not None and rig.mod.WATCH == 30)
total += rig.close()
os.environ.pop("CCL_WATCH", None)
g.calllog.Roster = g.ORIGINAL_ROSTER

# -- 6. Yellow on a day still to be confirmed ----------------------------------------------------------------------------
case("6. Yellow")
rig = g.rig_for("a6", NOW)
rig.show("Lena Drayton", "2026-10-31")
calls = rig.calls()
yellow = [c for c in calls if g.rostercolour.css(c["fill"]) == "#ffff00"]
lines = [n for n in rig.app.pack["calls"]["notes"] if "yellow on the roster" in n]
rig.check("a week still to be confirmed: its yellow names are drawn on yellow", yellow and all(g.paint(rig, c["name"]) == "provisional" for c in yellow), len(yellow))
rig.check("...one line on the log says so (not one per name)", len(lines) == 1 and (("%d names are yellow" % len(yellow)) in lines[0]
          or (len(yellow) == len(calls) and lines[0].startswith("Every name on this log is yellow"))), lines)
rig.check("...and they have no Green box (not today)", {g.state(rig, c["name"]) for c in calls} == {""})
rig.check("green, white and yellow are not 'worth a look'; other colours are", g.calllog.fill_word("FFFFFF00") == "" and g.calllog.fill_word("FFFF9900") == "orange")
total += rig.close()

ok = (real_link() == REAL and hashlib.sha256(open(g.FIXTURE, "rb").read()).hexdigest() == FIXTURE_HASH
      and g.rostercolour.CONFIG.startswith(g.WORK) and g.calllog.EDITS.startswith(os.path.dirname(g.WORK)))
print("\n%s  the real link file and the pinned roster are untouched, and the checks wrote only to scratch folders" % ("PASS" if ok else "FAIL"))
total.append(ok)
print("\n%d of %d checks passed" % (sum(total), len(total)))
sys.exit(0 if all(total) else 1)
