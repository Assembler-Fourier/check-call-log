"""Example corrections for a picture of the window, in a scratch folder (never the tool's own edits folder)."""
import datetime
import os
import tempfile
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import calllog as c  # noqa: E402

WORK = os.path.join(tempfile.gettempdir(), "ccl-tests")         # throwaway output; the checks themselves live in tool\tests
c.EDITS = os.path.join(WORK, "edits-show")
shutil.rmtree(c.EDITS, ignore_errors=True)
roster, day, who = c.Roster(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "demo", "roster-template.xlsx")), datetime.date(2026, 10, 3), "Lena Drayton"
now = datetime.datetime(2026, 10, 4, 9, 0)
log = c.build_patrols(day, roster, who, now=now)
c.patrol_add(log, "19:40", "19:55", "Car park", "Barrier arm stuck up - reported")
c.patrol_set(log, "u1", start="01:40", finish="02:05", notes="Gate B left open - closed")
shift = c.build_shift(day, roster, who)
c.save_shift(shift, [{"id": "e1", "time": "19:40", "area": "Block A", "text": "Fire door propped open - closed and checked"},
                     {"id": "e2", "time": "23:15", "area": "Main gate", "text": "Delivery driver turned away - no booking on the system"},
                     {"id": "e3", "time": "02:05", "area": "Car park", "text": "Barrier arm stuck up - reported to maintenance"}],
             "Barrier at car park faulty.\nKeys 14 and 15 with maintenance.", "", shift["incoming"])
calls = c.build(day, roster, who, now=now)
c.save_edit(calls, calls["calls"][3], "time", c.fmt(calls["calls"][3]["call"] + 1))
c.save_edit(calls, calls["calls"][7], "response", "No answer")
print(os.listdir(c.EDITS))
