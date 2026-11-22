"""Example corrections for a picture of a live shift filled in ahead (scratch folder only)."""
import datetime
import os
import tempfile
import shutil
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import calllog as c  # noqa: E402

WORK = os.path.join(tempfile.gettempdir(), "ccl-tests")         # throwaway output; the checks themselves live in tool\tests
c.EDITS = os.path.join(WORK, "edits-ahead")
shutil.rmtree(c.EDITS, ignore_errors=True)
roster, day, who, now = c.Roster(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "demo", "roster-template.xlsx")), datetime.date(2026, 10, 10), "Lena Drayton", datetime.datetime(2026, 10, 10, 20, 10)
calls = c.build(day, roster, who, now=now)
c.fill_ahead(calls, True)
c.fill_ahead(c.build_patrols(day, roster, who, now=now), True)
calls = c.build(day, roster, who, now=now)
c.save_edit(calls, calls["calls"][4], "response", "No answer - rang back 19:20")
c.save_edit(calls, calls["calls"][9], "time", c.fmt(calls["calls"][9]["call"] + 2))
shift = c.build_shift(day, roster, who)
c.save_shift(shift, [{"id": "e1", "time": "19:40", "area": "Block A", "text": "Fire door propped open - closed and checked"}], "", "", shift["incoming"])
print(os.listdir(c.EDITS))
