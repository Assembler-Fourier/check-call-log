"""The bridge between the live demo page and Check Call Log's own code (tool/calllog.py), run by Pyodide in the
browser. Nothing here is special to the web: it calls the same functions the window and the command line call, on
the made-up demo roster, and writes the same PDFs - into the browser's memory, from where the page offers them."""
import datetime
import json
import os
import shutil
import sys

sys.path.insert(0, "/app/tool")
import calllog  # noqa: E402

calllog.download_roster()                      # DEMO mode: builds the made-up roster with its newest week = this week
ROSTER = calllog.Roster()
OUT = "/out"


def duty(date_iso):
    """Who sits in the control room that day: [{name, shift}] in start order."""
    d = datetime.date.fromisoformat(date_iso)
    return json.dumps([{"name": s["name"], "shift": s["shift"]} for s in ROSTER.duty(d)])


def make(date_iso, who, finished):
    """Every log of one controller's shift, and the PDFs. finished: as if the shift were over (every call made)."""
    d = datetime.date.fromisoformat(date_iso)
    try:
        first = calllog.build(d, ROSTER, who)
        now = None
        if finished:
            midnight = datetime.datetime.combine(d, datetime.time())
            now = midnight + datetime.timedelta(minutes=first["meta"]["end_min"] + 60)
        log = calllog.build(d, ROSTER, who, now=now) if now else first
        patrols = calllog.build_patrols(d, ROSTER, who, now=now)
        shift = calllog.build_shift(d, ROSTER, who)
    except calllog.Problem as e:
        return json.dumps({"error": str(e)})
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    pack = calllog.write_pack({"calls": log, "patrols": patrols, "shift": shift}, OUT)
    one = calllog.write_outputs(log, OUT)
    meta = log["meta"]
    return json.dumps({
        "who": meta["supervisor"], "shift": meta["shift"], "date": d.strftime("%A %d %B %Y"),
        "incoming": shift.get("incoming") or "",
        "calls": [{"no": c["no"], "name": c["name"], "venue": c["venue"], "time": c["call_text"],
                   "due": c["due"], "response": c["response"], "their": c["shift"],
                   "ahead": bool(c["ahead"]), "strike": bool(c.get("strike"))} for c in log["calls"]],
        "patrols": [{"start": r["start_text"], "finish": r["finish_text"], "area": r["area"],
                     "officer": r["officer"], "notes": r["notes"]} for r in patrols["rows"]],
        "entries": [list(e) if isinstance(e, (list, tuple)) else e for e in shift["entries"]],
        "quiet": shift.get("quiet") or "", "handover": shift.get("handover") or shift.get("handover_default") or "",
        "notes": [n if isinstance(n, str) else str(n) for n in log["notes"]],
        "files": {"all": pack.get("pdf"), "calls_pdf": one.get("pdf"), "calls_xlsx": one.get("xlsx")},
    })
