"""The roster's script, in Python: the same checks in the same order and the same answers as the real one -
rostercolour.SCRIPT (version 3), or with version=1 the first one, which stays on the roster until the new one is put
in (tests\\roster-script-v1.gs) - over cells held in memory. The window's checks (greenrig.py, t_green.py) use it in
place of the real script, so nothing they do can reach the roster. t_script.py sends the real scripts (both versions)
and this one the very same requests and requires the very same answers, so the stand-in cannot drift from them.

Its clock is fixed when it is made: `today` and `hour` (the control room's, Europe/Dublin); a check may change them."""
import re
import threading
import time

GREEN, WHITE, YELLOW = "#92d050", "#ffffff", "#ffff00"
MAY_CHANGE = (WHITE, YELLOW, GREEN)
CELL_RE = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,5})$")
# the real script's not-a-name rules (rostercolour.SCRIPT: HOLE, NOTE, WEEKDAY, notAName), written the Python way
HOLE = re.compile(r"^[\s?.\-\u2013\u2014/()]*(?:(?:t\.?b\.?[acd]\.?|close|time)[\s?.\-\u2013\u2014/()]*)+$", re.I)
NOTE = re.compile(r"^[\s!-/:-@\[-`{-~]*(?:important|notes?|reminders?|remind|remember|notice|attention|urgent|warning|please|pls|"
                  r"fyi|asap|tb[acd]|t\.b\.[acd]|cancel|cancell?ed|info|updated?|updates|comments?|message|msg|see|read|check|closed|"
                  r"never|forget|attn|pto|vacant|unconfirmed|provisional|postponed)\.*(?![A-Za-z0-9_.'\u2019]|[^\x00-\x7f])", re.I)
WEEKDAY = re.compile(r"^(?:mon|tues?|wed|thu|thurs?|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\.?$", re.I)


def tidy(text):
    """Spaces as the script sees them: runs of white space made one, none at either end."""
    return " ".join(str(text).split())


def not_a_name(shown, is_text=True):
    """notAName in the real script: a time, a date, a number, a note, a heading or a start time not filled in."""
    return (not is_text or shown[:1].isdigit() or shown == shown.lower() or bool(HOLE.match(shown))
            or bool(NOTE.match(shown)) or bool(WEEKDAY.match(shown)))


class Script:
    def __init__(self, today, key, hour=0, version=3):
        self.today, self.hour, self.key, self.version = today, hour, key, version
        self.text, self.fill, self.day = {}, {}, {}      # "FK74" -> shown text; "FK74" -> "#rrggbb"; "FK" -> date or None
        self.numbers = set()                              # cells whose value is a number or a date, not text
        self.serials = {}                                 # "GA" -> a row-2 date kept as a plain number (version 1 rounds it)
        self.merged = set()
        self.sent, self.fail, self.gate, self.delay = [], None, None, 0.0
        self.inflight = self.maxflight = 0
        self.lock = threading.Lock()

    @staticmethod
    def css(fill):
        if fill in (None, "", "theme0"):
            return WHITE
        m = re.match(r"^(?:[0-9a-fA-F]{2})?([0-9a-fA-F]{6})$", str(fill))
        return "#" + m.group(1).lower() if m else "?" + str(fill)

    def load_roster(self, roster, calllog):
        """Every cell of a calllog.Roster copy: its text, fill, and whether it is text; row 2's dates."""
        import datetime
        idx = calllog.idx_to_col
        for (row, c), v in roster.cells.items():
            ref = "%s%d" % (idx(c), row)
            self.text[ref] = v[0]
            self.fill[ref] = self.css(roster.style(row, c)["fill"])
            if v[2] not in ("s", "str", "inlineStr"):
                self.numbers.add(ref)
            if row == 2 and v[2] not in ("s", "str", "inlineStr"):
                try:
                    self.day[idx(c)] = calllog.EPOCH + datetime.timedelta(days=int(float(v[0])))   # (whole days, as Math.floor)
                    self.serials[idx(c)] = float(v[0])
                except ValueError:
                    pass
        return self

    # ---- one request, as the window sends it ----
    def send(self, link, action, cell="", name="", was=""):
        with self.lock:
            self.inflight += 1
            self.maxflight = max(self.maxflight, self.inflight)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.gate:
                self.gate.wait(10)
            with self.lock:
                self.sent.append((action, cell, name, link.get("url")))
                if self.fail:
                    import calllog
                    raise calllog.Problem(self.fail)
                return self.answer(link.get("key"), action, cell, name, was)
        finally:
            with self.lock:
                self.inflight -= 1

    def answer(self, key, action, cell="", name="", was=""):
        """What the script answers (and does, to self.fill) - its checks in its order."""
        import datetime
        v3 = self.version >= 3
        if key != self.key:
            return {"ok": False, "why": "wrong key"}
        if action == "ping":
            return {"ok": True, "sheet": "Sheet1", "version": 3} if v3 else {"ok": True, "sheet": "Sheet1"}
        m = CELL_RE.match(cell) if isinstance(cell, str) else None
        if not m:
            return {"ok": False, "why": "not a cell: %s" % cell}
        if int(m.group(2)) < 3:
            return {"ok": False, "why": "not a cell with a name: %s" % cell}
        if action not in ("green", "white", "check"):
            return {"ok": False, "why": "unknown action"}
        shown, want = tidy(self.text.get(cell, "")), tidy(name or "")
        if not want or shown != want:
            return {"ok": False, "why": "moved"} if v3 else {"ok": False, "why": "moved", "shown": shown}
        if (not_a_name(shown, cell not in self.numbers) if v3 else shown[:1].isdigit()):
            return {"ok": False, "why": "not a name"}
        day = self.day.get(m.group(1))
        if not v3 and m.group(1) in self.serials:         # (version 1: Math.round - half a day or more counts as the next)
            day = datetime.date(1899, 12, 30) + datetime.timedelta(days=int(self.serials[m.group(1)] + 0.5))
        yesterday = self.today - datetime.timedelta(days=1)
        if not (day == self.today or day == yesterday and (self.hour < 12 or not v3)):
            return {"ok": False, "why": "not today", "day": day.isoformat() if day else ""}
        before = self.fill.get(cell, WHITE)
        if before not in MAY_CHANGE:
            return {"ok": False, "why": "coloured", "before": before}
        to = WHITE if action == "white" else GREEN
        if v3 and cell in self.merged:
            return {"ok": False, "why": "merged"}
        if v3 and was and before != str(was).lower():
            return {"ok": False, "why": "changed", "before": before}
        if v3 and action == "white" and before == YELLOW:
            return {"ok": False, "why": "changed", "before": before}
        if action == "check" or before == to:
            return {"ok": True, "before": before, "after": before if action == "check" else to}
        self.fill[cell] = to
        return {"ok": True, "before": before, "after": to}
