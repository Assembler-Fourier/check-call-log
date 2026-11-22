"""Shared by the window checks of the Green box, Auto green and the roster watch: a stand-in for the roster's script that
keeps the real one's rules (rostercolour.SCRIPT, run for real in t_script.py), over a roster copy in memory that a
check may change ("tweak") to stage a case. Nothing here can reach the real roster: rostercolour.send is replaced,
the link file and the setup folder are scratch, and the pinned roster file is only read."""
import datetime
import os
import re
import shutil
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(tempfile.gettempdir(), "ccl-tests", "greenrig")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)
os.environ["CCL_ROSTER_LINK"] = os.path.join(WORK, "roster-link.json")     # (before anything imports rostercolour)
FIXTURE = os.path.join(HERE, "roster-2026-10-19.xlsx")
URL = "https://script.google.com/macros/s/AKfycbTESTONLY-not-a-real-deployment_0123/exec"

from drive6 import Rig  # noqa: E402

import sys  # noqa: E402
sys.path.insert(0, os.path.dirname(HERE))           # the tool folder: the window imports these very modules
import calllog  # noqa: E402      (the same module the window uses)
import rostercolour  # noqa: E402
import fakescript  # noqa: E402

ORIGINAL_ROSTER = calllog.Roster
COLS = {}


def col(name):
    if name not in COLS:
        COLS[name] = next(i for i in range(4000) if calllog.idx_to_col(i) == name)
    return COLS[name]


def split(ref):
    m = re.match(r"([A-Z]+)(\d+)$", ref)
    return int(m.group(2)), col(m.group(1))


def style_of(fill):
    """A style index from the pinned roster whose fill is this ('FFFFFF00', 'FF92D050', 'theme0'...)."""
    r = ORIGINAL_ROSTER(FIXTURE)
    for (row, c), v in r.cells.items():
        if v[1] is not None and r.book.style(v[1])["fill"] == fill and v[2] in ("s", "str", "inlineStr"):
            return v[1]
    raise LookupError(fill)


def tweaked(changes):
    """A Roster class whose copies have these cells changed: {ref: (text or None to keep, fill or None to keep)} or
    {ref: None} to empty a cell. The window reads it in place of the plain pinned copy."""
    styles = {fill: style_of(fill) for text, fill in (v for v in changes.values() if v) if fill}

    class Tweaked(ORIGINAL_ROSTER):
        def __init__(self, path=FIXTURE):
            super().__init__(FIXTURE)
            for ref, change in changes.items():
                key = split(ref)
                if change is None:
                    self.cells.pop(key, None)
                    continue
                text, fill = change
                old = self.cells.get(key, ("", None, "s"))
                self.cells[key] = (text if text is not None else old[0], styles[fill] if fill else old[1], "s")
    return Tweaked


class FakeScript(fakescript.Script):
    """The roster's script (fakescript.Script: the real one's checks and answers, version 4 - or version=3 / version=1, the one
    the roster runs until the new one is put in) over a roster copy in memory, answering a little after each request."""

    def __init__(self, today, key, roster, hour=0, version=4):
        super().__init__(today, key, hour=hour, version=version)
        self.roster, self.delay = roster, 0.05
        self.load_roster(roster, calllog)


def rig_for(name, now, changes=None, linked=True, saved=None, settings=None):
    """A window on the pinned roster (with changes, if any), its link set up (linked) or not, the stand-in script
    in place of the real one. saved: a dict that collects what the window would save as its settings."""
    calllog.Roster = tweaked(changes) if changes else ORIGINAL_ROSTER
    rostercolour.CONFIG = os.path.join(WORK, "roster-link.json")
    rostercolour.SETUP = os.path.join(WORK, "Roster script")
    if os.path.exists(rostercolour.CONFIG):
        os.remove(rostercolour.CONFIG)
    key = rostercolour.load()["key"]
    if linked:
        rostercolour.save({"url": URL, "key": key})
    rig = Rig(name, now=now, roster=FIXTURE, settings=settings)
    if saved is not None:
        rig.mod.save_settings = lambda data: saved.update(data)
    rig.rc = rostercolour
    at = datetime.datetime.strptime(now, "%Y-%m-%d %H:%M")
    rig.fake = FakeScript(at.date(), key, calllog.Roster(FIXTURE), hour=at.hour)
    rostercolour.send = rig.fake.send
    return rig


def row_of(rig, name):
    grid = rig.grid("calls")
    return next(i for i, k in enumerate(grid.keys) if rig.app.call_for(k)["name"] == name)


def state(rig, name):
    grid = rig.grid("calls")
    return grid.state_of(grid.keys[row_of(rig, name)], "ok")


def paint(rig, name):
    grid = rig.grid("calls")
    key = grid.keys[row_of(rig, name)]
    return (next(r for r in grid.rows if r.get("key") == key).get("paint", {}).get("name") or ("", ""))[0]


def settle(rig, seconds=15):
    """Let every request out come back and auto green finish what it has to do."""
    end = time.time() + seconds
    while time.time() < end:
        rig.pump(120)
        if not rig.app.sending and not rig.app.auto_busy and (not rig.app.auto_due() or time.time() < rig.app.quiet_until):
            rig.pump(150)
            if not rig.app.sending and not rig.app.auto_busy:
                return True
    return False


def made(rig):
    """The calls on show whose time has passed (not filled in ahead)."""
    return [c for c in rig.calls() if c["call_text"] and not c["ahead"]]
