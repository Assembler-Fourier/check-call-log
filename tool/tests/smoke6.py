"""First run of the version 6 window: does it come up, show a finished shift and a live one, and take a few actions?"""
import sys

from drive6 import Rig

rig = Rig("s6", now="2026-10-07 10:00")
rig.check("a finished shift is on show at once (no 'make' button)", rig.show("Lena Drayton", "2026-10-03"), rig.app.packs and rig.app.packs[0].get("problems"))
rig.check("42 calls, three rounds, an empty shift log", (len(rig.rows("calls")), len(rig.rows("patrols")), len(rig.rows("shift"))) == (42, 3, 0),
          (len(rig.rows("calls")), len(rig.rows("patrols")), len(rig.rows("shift"))))
rig.check("looking wrote no file", not rig.files() and not __import__("os").path.exists(rig.out), rig.files())
print("   top line:", rig.app.who_label.cget("text"), "|", rig.app.when_label.cget("text"))
print("   tally:", rig.app.tally.cget("text"))
rig.click("calls", 0, "time")
rig.check("a click on a time opens it for typing", rig.box() is not None and rig.box()[2] == "time")
rig.type("18:03", "<Return>")
rig.check("typing a time and Enter saves it and goes to the row that was next", rig.calls()[1]["call_text"] == "18:03" or any(c["call_text"] == "18:03" and c["timed"] for c in rig.calls()),
          [c["call_text"] for c in rig.calls()[:3]])
rig.click_away()
rig.check("the change wrote the files", bool(rig.files().get("all")), rig.files())
rig.check("it is said under the table", "time 18:03" in rig.said(), rig.said())
rig.press("undo")
rig.check("Undo puts it back", not any(c["timed"] for c in rig.calls()), rig.said())
rig.tab("patrols")
rig.press("add_patrol")
rig.check("Add a patrol adds one and opens its start", len(rig.patrols()) == 4 and rig.box() is not None and rig.box()[2] == "start", rig.patrols())
rig.type(None, "<Escape>")
rig.tab("shift")
rig.press("add_line")
rig.type("21:15", "<Return>")
rig.type("Main gate", "<Return>")
rig.type("Delivery turned away - no booking", "<Return>")
rig.check("a shift log line typed with Enter along the row", rig.entries() == [("21:15", "Main gate", "Delivery turned away - no booking")], rig.entries())
rig.press("theme")
rig.check("the dark look keeps everything", rig.app.skin.theme == "dark" and rig.entries() == [("21:15", "Main gate", "Delivery turned away - no booking")] and rig.app.tab == "shift")
rig.check("no error in any callback", not rig.errors, rig.errors[:1])
results = rig.close()
print("\n%d of %d checks passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
