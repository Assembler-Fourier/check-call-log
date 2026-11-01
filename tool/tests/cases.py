"""Real-use cases for the patrol log and the shift log, clicked and typed through the event loop.
Run before and after a fix: the same checks, so a FAIL that turns to PASS is the fix working."""
import sys

from drive import Rig

total = []


def case(title):
    print("\n=== " + title)


# -- 1. Monday 20:06, Henry's night shift is on: he adds a shift log line and types it -------------------
case("1. Live shift: add a shift log entry and type it")
rig = Rig("c1", now="2026-10-05 20:06")
rig.check("logs made for the live shift", rig.make("Henry Marlow", "2026-10-05"))
rig.tab("shift")
rig.press(rig.app.shift_buttons[0])                                   # Add an entry
rig.check("a cell is open for typing and stays open", rig.box() is not None)
rig.type("Block B", "<Tab>") if rig.box() and rig.box()[2] == "area" else None
if rig.box() and rig.box()[2] == "time":
    rig.type("20:05", "<Tab>")
    rig.type("Block B", "<Tab>")
rig.check("moved on to the occurrence cell", rig.box() is not None and rig.box()[2] == "text", rig.box() and rig.box()[2])
rig.type("Exit door alarm sounding - reset, door secured", "<Return>")
rig.click_away()
rig.check("the entry is saved with time, place and text", any(e[1] == "Block B" and e[2].startswith("Exit door alarm") and e[0] for e in rig.entries()), rig.entries())
rig.check("no empty lines left behind", all(e[1] or e[2] for e in rig.entries()), rig.entries())
rig.check("it is printed on the shift log", "Exit door alarm sounding" in rig.pdf_text(rig.app.packs[0]["shift_files"]["pdf"]))
rig.press(rig.app.shift_buttons[0])                                   # add another and abandon it
rig.type(None, "<Escape>")
rig.click_away()
rig.check("an abandoned new line does not stay as an empty row", len(rig.entries()) == 1, rig.entries())
total += rig.close()

# -- 2. Same evening: the patrol log before any round is due ---------------------------------------------
case("2. Live shift, early: adding a patrol must not wipe the usual rounds")
rig = Rig("c2", now="2026-10-05 20:06")
rig.make("Henry Marlow", "2026-10-05")
rig.tab("patrols")
rig.check("the three usual rounds are listed from the start", len(rig.patrols()) == 3, rig.patrols())
rig.press(rig.app.patrol_buttons[0])                                  # Add a patrol
added = rig.patrols()
rig.check("a patrol is added, already in the past", len(added) == 4 and added[0][1] <= "20:06", added)
if rig.box():
    rig.type("19:30", "<Return>")
rig.click_away()
rig.check("its start can be set to when it was walked (19:30)", rig.patrols() and rig.patrols()[0][0] == "19:30", rig.patrols())
rig.click(rig.app.ptree, 0, "start")
rig.type("23:30", "<Return>")
rig.click_away()
rig.check("a time still to come is accepted now, with a remark", ("23:30", "23:45", "All", "") in rig.patrols() and "still to come" in rig.status(), rig.status())
rig.click(rig.app.ptree, [p[0] for p in rig.patrols()].index("23:30"), "start")      # ...and back to when it was really walked
rig.type("19:30", "<Return>")
rig.click_away()
rig.at("2026-10-06 07:00")                                            # next morning
rig.make("Henry Marlow", "2026-10-05")
rig.tab("patrols")
after = rig.patrols()
rig.check("next morning: the added patrol AND the three usual rounds are all there", len(after) == 4 and after[0][0] == "19:30", after)
total += rig.close()

# -- 3. A finished shift: adding several patrols, changing times, removing -------------------------------
case("3. Finished shift: add twice, change times so rows re-sort, remove")
rig = Rig("c3", now="2026-10-07 10:00")
rig.make("Lena Drayton", "2026-10-03")
rig.tab("patrols")
usual = rig.patrols()
rig.check("three usual rounds to start with", len(usual) == 3, usual)
rig.press(rig.app.patrol_buttons[0])
rig.type(None, "<Escape>")
rig.press(rig.app.patrol_buttons[0])
rig.type(None, "<Escape>")
rig.click_away()
rig.check("two more patrols can be added (no 'no room')", len(rig.patrols()) == 5, "%s | %s" % (rig.patrols(), rig.status()))
n = len(rig.patrols())
spans = [(r["start"], r["finish"]) for r in rig.app.packs[0]["patrols"]["rows"]]
rig.check("the added patrols do not sit on top of one another or of a round", all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), rig.patrols())
rig.click(rig.app.ptree, rig.patrols().index(usual[0]), "start")      # move the first usual round to after the second: the list re-sorts
rig.type("03:10", "<Tab>")
box = rig.box()
moved = next((p for p in rig.patrols() if p[0] == "03:10"), None)
rig.check("the patrol took the new start and kept its length", moved is not None and len(rig.patrols()) == n, rig.patrols())
rig.check("the typing box followed the same patrol to its finish cell",
          box is not None and box[2] == "finish" and box[0].get() == (moved[1] if moved else None), box and (box[2], box[0].get()))
rig.type("03:40", "<Tab>")                                            # along the row: finish, then notes
rig.type("Gate left open - closed", "<Return>")
rig.check("Enter in the last cell of the row finishes the row", rig.box() is None, rig.box() and rig.box()[2])
rig.click(rig.app.ptree, [p[0] for p in rig.patrols()].index("03:10"), "area")
rig.type("Car park", "<Return>")
rig.click_away()
rig.check("finish, area and note all landed on that same patrol", ("03:10", "03:40", "Car park", "Gate left open - closed") in rig.patrols(), rig.patrols())
rig.check("the other rounds are untouched", all(u in rig.patrols() for u in usual[1:]), rig.patrols())
rig.select(rig.app.ptree, len(rig.patrols()) - 1)
rig.press(rig.app.patrol_buttons[1])                                  # Remove
rig.check("Remove takes out the selected patrol", len(rig.patrols()) == n - 1, rig.patrols())
at = [p[0] for p in rig.patrols()].index("03:10")
rig.click(rig.app.ptree, at, "finish")
rig.type("02:50", "<Return>")
rig.check("a finish before the start is refused with a reason", "finish before" in rig.status() or "before it starts" in rig.status(), rig.status())
box = rig.box()
rig.check("...the cell stays open with what was typed, and says why beside it",
          box is not None and box[2] == "finish" and box[0].get() == "02:50" and rig.app.patrol_cells.tip is not None, box and (box[2], box[0].get()))
rig.type("03:35", "<Return>")
rig.click_away()
rig.check("...and putting it right is accepted", ("03:10", "03:35", "Car park", "Gate left open - closed") in rig.patrols(), rig.patrols())
rig.click(rig.app.ptree, at, "finish")
rig.type("03:40", "<Return>")
rig.click_away()
text = rig.pdf_text(rig.app.packs[0]["patrols_files"]["pdf"])
rig.check("the patrol log PDF shows the changed patrol", "03:10" in text and "Car park" in text and "Gate left open" in text)
rig.make("Lena Drayton", "2026-10-03")
rig.tab("patrols")
rig.check("making the logs again keeps every change", ("03:10", "03:40", "Car park", "Gate left open - closed") in rig.patrols() and len(rig.patrols()) == n - 1, rig.patrols())
rig.press(rig.app.patrol_buttons[2])                                  # Clear all
rig.check("Clear all empties the list", rig.patrols() == [], rig.patrols())
rig.press(rig.app.patrol_buttons[3])                                  # Usual rounds
rig.check("Usual rounds puts the three suggestions back", rig.patrols() == usual, rig.patrols())
total += rig.close()

# -- 4. Finished shift: shift log with several entries, edited and re-ordered ----------------------------
case("4. Finished shift: several shift log entries, handover notes, re-ordering")
rig = Rig("c4", now="2026-10-07 10:00")
rig.make("Lena Drayton", "2026-10-03")
rig.tab("shift")
for when, place, what in (("23:15", "Main gate", "Delivery driver turned away - no booking"), ("19:40", "Block A", "Fire door propped open - closed"),
                          ("02:05", "Car park", "Barrier arm stuck up - reported to maintenance")):
    rig.press(rig.app.shift_buttons[0])
    rig.type(when, "<Tab>")
    rig.type(place, "<Tab>")
    rig.type(what, "<Return>")
    rig.click_away()
rig.check("three entries, kept in the order they happened", [e[0] for e in rig.entries()] == ["19:40", "23:15", "02:05"], rig.entries())
rig.check("each entry has its own place and text", ("23:15", "Main gate", "Delivery driver turned away - no booking") in rig.entries(), rig.entries())
rig.click(rig.app.stree, 0, "time")                                   # 19:40 becomes 03:30: it moves to the end
rig.type("03:30", "<Tab>")
box = rig.box()
rig.check("after the time change the box is still on the same entry", box is not None and box[2] == "area" and box[0].get() == "Block A", box and (box[2], box[0].get()))
rig.type("Block A rear", "<Return>")
rig.click_away()
rig.check("the re-ordered entry kept its text", ("03:30", "Block A rear", "Fire door propped open - closed") in rig.entries(), rig.entries())
rig.app.handover.focus_set()
rig.app.handover.insert("1.0", "Barrier at car park faulty.\nKeys 14 and 15 with maintenance.")
rig.key(rig.app.handover, "<KeyRelease>")
rig.pump(1200)
rig.click(rig.app.stree, 0, "text")                                   # the 23:15 line is first now
rig.type("Delivery driver turned away - no booking on the system", "<Return>")
rig.click_away()
log = rig.app.packs[0]["shift"]
rig.check("the text went to the line that was clicked", ("23:15", "Main gate", "Delivery driver turned away - no booking on the system") in rig.entries(), rig.entries())
rig.check("handover notes survive later edits to the entries", log["handover"].endswith("with maintenance."), log["handover"])
text = rig.pdf_text(rig.app.packs[0]["shift_files"]["pdf"])
rig.check("everything is on the printed shift log", all(t in text for t in ("Barrier at car park faulty.", "Block A rear", "no booking on the system", "03:30")))
rig.click(rig.app.stree, 1, "time")                                   # a time that cannot be right
rig.type("12:00", "<Return>")
box = rig.box()
rig.check("a time outside the shift is refused, the cell stays open and says why",
          box is not None and box[0].get() == "12:00" and "outside this shift" in rig.status() and rig.app.shift_cells.tip is not None, rig.status())
rig.type(None, "<Escape>")
rig.check("Esc leaves the line as it was", rig.entries()[1][0] == "02:05", rig.entries())
rig.click(rig.app.stree, 1, "time")
rig.type("06:10", "<Return>")
rig.click_away()
rig.check("a line just after the shift ended (06:10) is accepted and goes last", rig.entries()[-1][:2] == ("06:10", "Car park"), rig.entries())
rig.click(rig.app.stree, 0, "area")                                   # Remove acts on the line being typed in
rig.press(rig.app.shift_buttons[1])                                   # Remove
rig.check("Remove takes out the line being typed in", len(rig.entries()) == 2 and all(e[0] != "23:15" for e in rig.entries()), rig.entries())
total += rig.close()

# -- 5. The dark look, switched in the middle of the work ------------------------------------------------
case("5. Dark look: switch it on mid-shift, carry on working, switch back")
rig = Rig("c5", now="2026-10-10 23:20")
rig.make("Lena Drayton", "2026-10-10")
rig.tab("shift")
rig.press(rig.app.shift_buttons[0])
rig.type("Reception", "<Return>")
rig.type("Visitor signed out late", "<Return>")
rig.app.handover.focus_set()
rig.app.handover.insert("1.0", "Spare radio on charge.")
rig.key(rig.app.handover, "<KeyRelease>")
rig.pump(1200)
before = (rig.entries(), rig.patrols(), rig.app.headline.get(), [c["call_text"] for c in rig.app.packs[0]["calls"]["calls"]], rig.app.notes.get("1.0", "end"))
rig.press(rig.app.theme_button)
rig.check("the dark look is on", rig.mod.THEME == "dark" and rig.root.cget("bg") == rig.mod.THEMES["dark"]["PAPER"], rig.mod.THEME)
after = (rig.entries(), rig.patrols(), rig.app.headline.get(), [c["call_text"] for c in rig.app.packs[0]["calls"]["calls"]], rig.app.notes.get("1.0", "end"))
rig.check("nothing on show was lost in the change", before == after and rig.app.tab == "shift" and rig.app.handover.get("1.0", "end").strip() == "Spare radio on charge."
          and len(rig.rows(rig.app.tree)) == len(before[3]) and len(rig.rows(rig.app.stree)) == 1)
rig.tab("patrols")
rig.press(rig.app.patrol_buttons[0])                                  # work carries on in the dark look
rig.type("22:40", "<Return>")
rig.type("22:55", "<Return>")
rig.type("Radio check with gate", "<Return>")
rig.check("a patrol can be added and typed in the dark look", ("22:40", "22:55", "All", "Radio check with gate") in rig.patrols(), rig.patrols())
rig.tab("calls")
logged = [i for i, c in enumerate(rig.app.packs[0]["calls"]["calls"]) if c["call_text"]]
rig.click(rig.app.tree, logged[0], "resp")
rig.type("No answer", "<Return>")
rig.click_away()
rig.check("a call response can be changed in the dark look", rig.app.packs[0]["calls"]["calls"][logged[0]]["response"] == "No answer")
rig.press(rig.app.theme_button)
rig.check("and back to the light look, with everything still there", rig.mod.THEME == "light" and ("22:40", "22:55", "All", "Radio check with gate") in rig.patrols()
          and rig.entries() == before[0] and rig.app.tab == "calls", rig.patrols())
total += rig.close()

# -- 6. Filling in the rest of the shift ahead of the clock ----------------------------------------------
case("6. Live shift: fill in the rest of the shift, change one of the times, blank it again")
rig = Rig("c6", now="2026-10-10 20:10")
rig.make("Lena Drayton", "2026-10-10")
calls = lambda: rig.app.packs[0]["calls"]["calls"]
blank = sum(1 for c in calls() if not c["call_text"])
rig.check("at 20:10 some calls are still to come, and are blank", 0 < blank < len(calls()), blank)
rig.check("...and the three rounds are listed already", len(rig.patrols()) == 3, rig.patrols())
rig.check("the button offers to fill in the rest", rig.app.call_buttons[0].label.cget("text") == "Fill in the rest of the shift" and rig.app.call_buttons[0].enabled)
rig.press(rig.app.call_buttons[0])
rig.check("Fill in the rest: every call has a time and Ok", all(c["call_text"] and c["response"] == "Ok" for c in calls()), rig.status())
rig.check("the ones ahead of the clock are known as such (shown in blue)", sum(1 for c in calls() if c["ahead"]) == blank
          and sum(1 for i in rig.app.tree.get_children() if "ahead" in rig.app.tree.item(i, "tags")) == blank)
rig.check("the three rounds of the night are listed too", len(rig.patrols()) == 3 and all(r["ahead"] for r in rig.app.packs[0]["patrols"]["rows"]), rig.patrols())
last = calls()[-1]
text = rig.pdf_text(rig.app.packs[0]["calls_files"]["pdf"])
rig.check("the printed check call log is complete", last["call_text"] in text and text.count(" Ok") >= len(calls()) - 1, last["call_text"])
rig.check("the patrol log PDF has the rounds", all(p[0] in rig.pdf_text(rig.app.packs[0]["patrols_files"]["pdf"]) for p in rig.patrols()))
rig.check("the button now offers to blank them again", rig.app.call_buttons[0].label.cget("text") == "Leave the rest of the shift blank")
ahead = next(i for i, c in enumerate(calls()) if c["ahead"])
who, typed = calls()[ahead]["name"], rig.calllog.fmt(calls()[ahead]["call"] + 1)
rig.click(rig.app.tree, ahead, "time")
rig.type(typed, "<Return>")
rig.click_away()
mine = lambda: next(c for c in calls() if c["name"] == who and c["edited"])
rig.check("one of those times can be changed", mine()["call_text"] == typed, (who, typed))
rig.press(rig.app.call_buttons[0])                                    # blank the rest again
rig.check("Leave the rest blank: back to only what has happened...", sum(1 for c in calls() if not c["call_text"]) == blank - 1 and len(rig.patrols()) == 3, rig.status())
rig.check("...except the time typed by hand, which stays", mine()["call_text"] == typed and mine()["ahead"])
rig.tab("shift")
rig.press(rig.app.shift_buttons[0])
rig.type("Reception", "<Return>")
rig.type("Contractor due at 23:00 - expected", "<Return>")
rig.click(rig.app.stree, 0, "time")
rig.type("23:00", "<Return>")
rig.click_away()
rig.check("a shift log line can carry a time still to come", rig.entries() == [("23:00", "Reception", "Contractor due at 23:00 - expected")], rig.entries())
rig.at("2026-10-11 07:00")                                            # the morning after
rig.make("Lena Drayton", "2026-10-10")
rig.check("the morning after everything is filled in, nothing is 'ahead', and the typed time is still there",
          all(c["call_text"] for c in calls()) and not any(c["ahead"] for c in calls()) and mine()["call_text"] == typed and len(rig.patrols()) == 3)
rig.check("...and the button has nothing left to do", not rig.app.call_buttons[0].enabled)
rig.check("no error came up in any callback", not rig.errors, rig.errors[:1])
total += rig.close()

print("\n%d of %d checks passed" % (sum(total), len(total)))
sys.exit(0 if all(total) else 1)
