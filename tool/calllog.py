"""NORTHWIND Security - Daily Check Call Log builder.

    python calllog.py 03/10                     one date (DD/MM, DD/MM/YY, DD/MM/YYYY or YYYY-MM-DD)
    python calllog.py fri sat sun               the most recent Friday, Saturday and Sunday
    python calllog.py sat --name henry          another controller's log (first name is enough)
    python calllog.py sat --dry                 just print the list, write no files

For each date it downloads a fresh read-only copy of the roster, finds the
controller's own shift in the control room, lists everyone whose check call
(one hour before their start) falls inside that shift, gives each a call time,
and writes a printable PDF plus a spreadsheet to Logs/ (forms.py writes both directly).
It can also write the Campus Patrol Log and the Shift Log for the same shift (--patrols, --shift-log).
app.pyw is the window on top of this; "Make Call Log.bat" opens it.

The live roster is only ever downloaded (export link). Nothing here can write to it.
"""
import argparse
import collections
import datetime
import difflib
import json
import os
import random
import re
import shutil
import sys
import threading
import urllib.request
import zipfile

import forms
from xl import Book, idx_to_col

def _config():
    """tool/config.json (optional, never committed): {"roster_id": "..."}."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"), encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ---- settings ---------------------------------------------------------------
# The roster is a Google Sheet shared by link (view only). Its id comes from the ROSTER_SHEET_ID environment
# variable or tool/config.json. With no id the tool runs in DEMO mode on the made-up roster in demo/.
ROSTER_ID = os.environ.get("ROSTER_SHEET_ID") or _config().get("roster_id", "")
DEMO = not ROSTER_ID
ROSTER_EXPORT = "https://docs.google.com/spreadsheets/d/%s/export?format=xlsx" % ROSTER_ID
ROSTER_COLUMNS = "https://docs.google.com/spreadsheets/d/%s/export?format=csv&gid=0&range=%%s" % ROSTER_ID    # a few columns, text only
CONTROLLERS = ["Lena Drayton", "Henry Marlow", "Soren Rasmussen", "Basil Jankowski"]   # offered in the window
CONTROL_SITES = ("kilcarran", "kilcarran business campus")            # roster labels of the control room (controllers, not check calls)
# The usual control-room week (Mon=0). Only used to decide who the window opens on before the
# roster has loaded; the roster itself always decides whose shift a log is for.
_DAYS, _EVES = ("06:00", "18:00"), ("16:30", "06:00")
NORMAL_SHIFTS = {
    0: (("Soren Rasmussen",) + _DAYS, ("Henry Marlow",) + _EVES),
    1: (("Soren Rasmussen",) + _DAYS, ("Henry Marlow",) + _EVES),
    2: (("Soren Rasmussen",) + _DAYS, ("Basil Jankowski",) + _EVES),
    3: (("Soren Rasmussen",) + _DAYS, ("Basil Jankowski",) + _EVES),
    4: (("Soren Rasmussen",) + _DAYS, ("Lena Drayton",) + _EVES),
    5: (("Henry Marlow",) + _DAYS, ("Lena Drayton", "18:00", "06:00")),
    6: (("Henry Marlow",) + _DAYS, ("Lena Drayton", "18:00", "06:00")),
}
GRACE = 45                        # minutes after a shift ends during which its controller is still "the one at the desk"
EARLY = 30                        # a shift log line may be this long before the shift starts (and GRACE after it ends)
LEAD = 60                         # a check call is due this many minutes before the shift starts (the "mark")
FIRST = (1, 3)                    # the first call of a group is logged this many minutes after the mark
GAPS = [1, 1, 2, 2]               # ...and the rest of the group follows this many minutes apart
RESPONSE = "Ok"
NO_OCCURRENCE = "NO MAJOR OCCURRENCE"     # printed across a shift log that has no lines typed (asked for on 2026-10-12)
NOTHING_TO_REPORT = "NTR"                 # ...and, large, in its handover notes while none are typed
SEE_ABOVE = "AS PER OCCURRENCES ABOVE"    # ...or this instead of NTR once lines are typed: "nothing to report" would no longer be true
WHOLE_SHIFT = "(whole shift)"     # in a check call log's saved corrections: what applies to the log, not to one call
# venue names as written on the sample logs, where they differ from the roster label
VENUE_ALIASES = {
    "K9 / Red Door": "K9/Red Door",
    "QuickStop Elmfield": "QuickStop",
    "WESTGATE": "Voltmart Westgate", "HILLCREST": "Voltmart Hillcrest", "ASHGROVE": "Voltmart Ashgrove",
    "BROOKVALE": "Voltmart Brookvale", "OAKRIDGE": "Voltmart Oakridge", "KILMORAN": "Voltmart Kilmoran",
    "BRIDGEPORT": "Voltmart Bridgeport", "SEAVIEW": "Voltmart Seaview", "STONEBRIDGE": "Voltmart Stonebridge",
    "MEADOWBANK": "Voltmart Meadowbank", "ELMHURST": "Voltmart Elmhurst",
}
# -----------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(HERE, "cache")
LOGS = os.path.join(ROOT, "Logs")
EDITS = os.path.join(HERE, "edits")                    # times/responses corrected by hand, one small file per log
ROSTER_PATH = os.path.join(CACHE, "roster.xlsx")
EPOCH = datetime.date(1899, 12, 30)
DAY = 1440
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# "20:00-close", "18.00-close", "21:00-03-00", "22:00hrs-close", "20-00 - close"
TIME_RE = re.compile(r"^\s*(\d{1,2})(?:[:.]|-(?=\d{2}\s*-))(\d{2})\s*(?:hrs?)?\s*-\s*"
                     r"(?:(\d{1,2})[:.-](\d{2})|\d{1,2}\s*[ap]m\b|close\b|late\b|finish\b|end\b|till?\b|until\b|\?|\s*$)", re.I)
# (after the dash: an end time, close/late/finish, "?" or nothing - so "21:30 - doors open" is a note, not a start time)
# A cell typed on the roster for whoever reads it - never a venue, never a person to call. On 2026-10-19 an
# "IMPORTANT NOTE" typed where the venue names go (pointing at a comment on the cell) put a whole venue's staff
# under a venue of that name, and an "Important reminder" in a day column was listed as a person. A note is known by
# its FIRST word: no person or venue on the roster starts with one of these. A cell that starts with a name stays a
# person whatever follows ("Lena Drayton (TBC)", "Dorian Sandler to cover") - losing a real person from the log
# is far worse than an odd row - and the controller is told to look at it (name_flag).
NOTE_WORDS = {"important", "note", "notes", "nb", "n.b", "reminder", "reminders", "remind", "remember", "notice", "attention",
              "urgent", "warning", "please", "pls", "fyi", "asap", "tbc", "tba", "tbd", "cancel", "cancelled", "canceled",
              "info", "update", "updated", "updates", "comment", "comments", "message", "msg", "see", "read", "check", "closed",
              "never", "forget", "attn", "pto", "vacant", "unconfirmed", "provisional", "postponed", "t.b.c", "t.b.a", "t.b.d"}
# ...and what is a note only as the WHOLE cell: as words they are surnames (Johnny Cash, Alicia Keys) or in venue
# names (The Spare Room, Holiday Inn, Cash Converters, Off Licence). "AL" (annual leave) only in capitals: Al is a name.
PLACEHOLDER_RE = re.compile(r"^(?:spare|cover(?: needed| required)?|needs? cover|open(?: shift)?|agency(?: staff)?|sick|off sick|"
                            r"no show|off|day off|(?:annual )?leave|a/l|hol(?:iday)?s?|training|shadow(?:ing)?|trial(?: shift)?|"
                            r"standby|on call|keys|radios?|cash|n/?a|x+|none|nobody|no staff|not (?:needed|required|confirmed)|"
                            r"pending|called off|see (?:below|above)|confirmed|ok|done|male|female|"
                            r"to be (?:confirmed|arranged|advised|decided))$", re.I)
FIRST_RE = re.compile(r"[^\W\d_][\w'’.]*")
WORD_RE = re.compile(r"[^\W\d_][\w'’-]*")
# small words a person's name may carry ("Victor de Sutton", "Ali bin Omar"); others make a name worth a look
PARTICLES = {"da", "de", "del", "della", "der", "den", "di", "do", "dos", "das", "du", "van", "von", "la", "le", "bin", "binti", "bint",
             "ibn", "al", "el", "y", "e", "los", "las", "zu", "und", "ter", "dei", "degli", "dalla", "dal", "an"}


class Problem(Exception):
    """Something the user needs to know about; shown without a traceback."""


def fmt(mins):
    mins %= DAY
    return "%d:%02d" % (mins // 60, mins % 60)


def parse_dates(tokens, today):
    out = []
    for tok in tokens:
        t = tok.strip().strip(",").lower()
        if not t:
            continue
        if t == "today":
            d = today
        elif t == "yesterday":
            d = today - datetime.timedelta(days=1)
        elif t[:3] in [w.lower() for w in WEEKDAYS] and t.isalpha():
            want = [w.lower() for w in WEEKDAYS].index(t[:3])
            d = today - datetime.timedelta(days=(today.weekday() - want) % 7)
        else:
            m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", t)
            if m:
                y, mo, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                m = re.match(r"^(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2}|\d{4}))?$", t)
                if not m:
                    raise Problem("can't read the date %r - use e.g. 03/10, 03/10/2026, 2026-10-03, sat" % tok)
                dd, mo = int(m.group(1)), int(m.group(2))
                y = today.year if not m.group(3) else int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
            try:
                d = datetime.date(y, mo, dd)
            except ValueError:
                raise Problem("%r is not a real date" % tok)
        if d not in out:
            out.append(d)
    return out


def download_roster(dest=ROSTER_PATH):
    """Fetch a read-only .xlsx export of the roster. Falls back to the last copy."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if DEMO:
        sys.path.insert(0, os.path.join(ROOT, "demo"))
        import make_demo
        make_demo.build(dest)
        return "downloaded - DEMO roster (made-up people and venues); set ROSTER_SHEET_ID to use a real one"
    part = "%s.%d-%d.part" % (dest, os.getpid(), threading.get_ident())     # (two downloads at once never share one)
    try:
        req = urllib.request.Request(ROSTER_EXPORT, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=90) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f)
        if not zipfile.is_zipfile(part):
            raise ValueError("the link did not return a spreadsheet (is it still shared by link?)")
        with zipfile.ZipFile(part) as z:              # every part of it whole, before it replaces the saved copy
            bad = z.testzip()
        if bad:
            raise ValueError("the download came in damaged (%s)" % bad)
        os.replace(part, dest)
        return "downloaded just now"
    except Exception as e:
        try:
            os.remove(part)
        except OSError:
            pass
        if not os.path.exists(dest):
            raise Problem("could not download the roster and there is no saved copy: %s" % e)
        age = datetime.datetime.fromtimestamp(os.path.getmtime(dest))
        return "DOWNLOAD FAILED (%s) - using the copy saved %s" % (e, age.strftime("%a %d %b %H:%M"))


def fetch_columns(first, last, rows):
    """The text of a few roster columns (A1 letters), as CSV: the small read the window makes every 30 seconds to see
    whether the shift on show has changed (about 5 KB). Read-only, like everything else here."""
    if DEMO:                                       # the demo roster only changes when it is rebuilt
        return "demo %r" % (roster_signature(),)
    req =urllib.request.Request(ROSTER_COLUMNS % ("%s1:%s%d" % (first, last, rows)), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.headers.get_content_type() != "text/csv":          # a sign-in page or Google's error page is no roster
            raise ValueError("Google did not send the roster's text (%s)" % r.headers.get_content_type())
        return r.read(2000000).decode("utf-8", "replace")


def roster_signature(path=ROSTER_PATH):
    """What a roster copy holds, cheaply (each part's checksum): the same signature, the same roster - no need to
    read it again."""
    with zipfile.ZipFile(path) as z:
        return tuple(sorted((i.filename, i.CRC, i.file_size) for i in z.infolist()))


class Roster:
    def __init__(self, path=ROSTER_PATH):
        self.book = Book(path)
        with zipfile.ZipFile(path) as z:              # which cells carry a comment - only where; the text is never read
            self.commented = {ref for n in z.namelist() if re.match(r"xl/comments\d*\.xml$", n)
                              for ref in re.findall(r'<comment\b[^>]*\bref="([A-Z]+\d+)"', z.read(n).decode("utf-8", "replace"))}
        self.cells = self.book.cells(self.book.sheets[0])
        self.max_row = max(r for r, _ in self.cells)
        self._date_cols = collections.defaultdict(list)       # Excel date serial -> header columns
        for (r, c), v in self.cells.items():
            if r == 2 and v[2] not in ("s", "str", "inlineStr"):
                try:
                    self._date_cols[float(v[0])].append(c)
                except ValueError:
                    pass
        self._duty = {}
        self.remarks = {}                                     # day column -> notes typed on the roster that day (see shifts)
        self.empty = {}                                       # day column -> start times with nobody under them yet
        self.weeks = {}                                       # week's label column -> (day columns)
        for serial, cols in self._date_cols.items():
            if (EPOCH + datetime.timedelta(days=int(serial))).weekday() == 0:
                for c in cols:
                    self.weeks[c - 1] = range(c, c + 7)
        self.label_weeks = collections.defaultdict(set)       # a venue label -> the weeks it is on (see label_note)
        self.control_people = {c.lower() for c in CONTROLLERS}
        self.people = collections.Counter()                   # a name as written -> how many day cells carry it
        self.spelled, self._often = {}, None                  # ...and how it is written (for the spelling check)
        week_of = {c: lc for lc, days in self.weeks.items() for c in [lc, *days]}
        control_rows = collections.defaultdict(list)
        for (r, c), v in self.cells.items():
            lc = week_of.get(c)
            if lc is None or r < 3 or v[2] not in ("s", "str", "inlineStr") or not v[0].strip() or v[0].strip() in WEEKDAYS:
                continue
            if c != lc and not TIME_RE.match(v[0]):
                self.people[" ".join(v[0].lower().split())] += 1
                self.spelled.setdefault(" ".join(v[0].lower().split()), " ".join(v[0].split()))
            if c == lc or _dark(self.book.style(v[1])["fill"]):
                self.label_weeks[" ".join(v[0].lower().split())].add(lc)
                if c == lc and is_control(v[0]):
                    control_rows[lc].append(r)
        for lc, rows in control_rows.items():                 # everyone who has sat in the control room, cover included
            for r0 in rows:
                r = r0 + 1
                while r <= self.max_row and not self.text(r, lc) and self.text(r, lc + 1) not in WEEKDAYS:
                    for c in self.weeks[lc]:
                        t = self.text(r, c)
                        if t and self.is_text(r, c) and not TIME_RE.match(t) and looks_like_name(t) and not is_note(t):
                            self.control_people.add(t.lower())
                    r += 1

    def duty(self, date):
        """The controllers in the control room on a date: [{'name', 'start', 'end', 'shift'}]; [] if unknown."""
        if date not in self._duty:
            try:
                _, shifts, _ = self.shifts(date)
            except Problem:
                shifts = []
            self._duty[date] = [s for s in shifts if is_control(s["site"]) and s["end"] is not None]
        return self._duty[date]

    def text(self, r, c):
        v = self.cells.get((r, c))
        return "" if v is None else v[0].strip()

    def style(self, r, c):
        v = self.cells.get((r, c))
        return self.book.style(v[1] if v else None)

    def is_text(self, r, c):
        v = self.cells.get((r, c))
        return v is not None and v[2] in ("s", "str", "inlineStr")

    def day_column(self, date):
        cols = self._date_cols.get(float((date - EPOCH).days), [])
        cols = [c for c in cols if self.text(1, c) == WEEKDAYS[date.weekday()]] or cols
        if not cols:
            raise Problem("%s is not on the roster (no such date in the header row)" % date.strftime("%a %d %b %Y"))
        if len(cols) > 1:
            raise Problem("%s appears %d times in the roster header (%s) - can't tell which column is right"
                          % (date.strftime("%d %b %Y"), len(cols), ", ".join(idx_to_col(c) for c in cols)))
        return cols[0]

    def shifts(self, date):
        """Every (site, time, name) rostered on the date, top to bottom. Notes typed on the roster for its readers
        are neither a site nor a name (label_note, is_note): they are kept in self.remarks[column] with the venue they
        sit under and the start time above them, for build() to pass on to the controller they concern. Start times
        with nobody under them are kept in self.empty[column]."""
        col = self.day_column(date)
        label_col = col - date.weekday() - 1          # the column left of that week's Monday
        out, warnings = [], []
        remarks = self.remarks[col] = []
        slots = []
        site = when = slot = last = hole = None       # last: what the cell above was - "label", "time" or "name"

        def remark(r, c, text, label=False, on=False):
            ref = "%s%d" % (idx_to_col(c), r)
            if all(m["ref"] != ref for m in remarks):
                remarks.append({"ref": ref, "text": text, "site": site, "start": when[1] if when and not label else None,
                                "label": label, "comment": ref in self.commented, "on": on})

        for r in range(3, self.max_row + 1):
            label, at, done, above = self.text(r, label_col), label_col, False, None
            label = "" if label in WEEKDAYS else label
            if label and self.label_note(label, r, label_col, col, site):   # a note where the venues go: the venue stays the one above
                remark(r, label_col, label, label=True)
                label = ""
            if not label:                             # some site labels are typed into a day column instead
                for c in range(label_col + 1, label_col + 8):
                    t = self.text(r, c)
                    if t and self.is_text(r, c) and t not in WEEKDAYS and _dark(self.style(r, c)["fill"]):
                        if self.label_note(t, r, c, col, site):
                            if c == col:              # (a note typed in another day's column is that day's business)
                                remark(r, c, t)
                                done = True           # (and the cell is dealt with: it is not read again as a person)
                            continue
                        label, at = t, c
                        break
            if label:
                venue = self.venue_of(label)          # "Perch Kilcarran (TBC)": Perch Kilcarran, with a note on it
                if last == "time" and slot and not slot["named"] and slot["ref"] == "%s%d" % (idx_to_col(col), r - 1):
                    above = (site, when, slot, label)     # a name typed on this row belongs to the start time straight above
                site, when, slot, last, hole = venue, None, None, "label", None
                if venue != label:
                    remark(r, at, label, label=True, on=True)
                if self.text(r, col) == label:
                    continue
            if done:
                continue
            v = self.text(r, col)
            if not v or not self.is_text(r, col) or v in WEEKDAYS:
                continue
            ref = "%s%d" % (idx_to_col(col), r)
            m = TIME_RE.match(v)
            if m:
                end = int(m.group(3)) * 60 + int(m.group(4)) if m.group(3) else None
                when, last, hole = (v, int(m.group(1)) * 60 + int(m.group(2)), end), "time", None
                slot = {"ref": ref, "site": site, "shift": v, "start": when[1], "named": False}
                slots.append(slot)
                continue
            if v.lower().startswith("break"):
                continue
            if time_hole(v):                          # a start time not filled in ("TBC", "-close"; straight under a time
                if last != "time":                    # it is a place still to fill): the names under it keep the time above,
                    hole, slot, last = v, None, "time"    # if there is one, and are pointed out (losing a call is worse)
                    when = when or (v, None, None)
                continue
            if is_note(v):                            # "Important reminder": for the reader, not a person to call
                remark(r, col, v)
                continue
            if not looks_like_name(v):                # "reminder", "cancelled" and the like
                if site and not is_control(site):
                    warnings.append("%s: %r under %s is not a name - left out; check that part of the roster by hand" % (ref, v, site))
                continue
            if any(ch.isdigit() for ch in v):
                warnings.append("%s: can't read %r as a time or a name - skipped, check it by hand" % (ref, v))
                continue
            n_site, n_when, n_slot, moved = site, when, slot, ""
            if above and when is None:                # (Carl Fielding on Sat 24 Oct: his name on the row of The lido label)
                n_site, n_when, n_slot = above[:3]
                moved = "it is typed on the row of the venue label %s, so it has the start time straight above it (%s at %s)" % (above[3], n_when[0], n_site)
            if n_site is None or n_when is None:
                warnings.append("%s: %r has no site or time above it - skipped, check it by hand" % (ref, v))
                continue
            if n_when[1] is None:
                warnings.append("%s: %s at %s has no start time on the roster yet (%r) - not on the log; add the call by hand "
                                "once it is known" % (ref, v, n_site, n_when[0]))
                continue
            st = self.style(r, col)
            flag = moved or ("the start time above it is not filled in on the roster (%r), so it has the time before it (%s)" % (hole, when[0])
                             if hole else name_flag(v) or self.spelling(v, n_site))
            out.append({"row": r, "ref": ref, "name": v, "site": n_site, "shift": n_when[0], "start": n_when[1],
                        "end": n_when[2], "strike": st["strike"], "fill": st["fill"], "flag": flag})
            if not moved:
                last = "name"
            if n_slot:
                n_slot["named"] = True
        self.empty[col] = [s for s in slots if not s["named"]]
        return col, out, warnings

    def spelling(self, name, site):
        """A name written this way only once on the whole roster, close to one written often: a likely slip
        ("Inees Nakamura" / "Ines Nakamura"). Nothing when there is no close one: a new starter is no slip."""
        key = " ".join(name.lower().split())
        if is_control(site) or self.people[key] > 1:
            return ""
        if self._often is None:
            self._often = [n for n, k in self.people.items() if k > 1 and looks_like_name(self.spelled.get(n, ""))]
        base = " ".join(re.sub(r"[(\[][^)\]]*[)\]]|\s-\s.*$", " ", key).split())      # the name without "(new starter)", "- to confirm"
        if base != key and self.people[base] > 1:
            return "the roster adds %r to the name" % " ".join(name.replace(self.spelled[base], "", 1).split()) if self.spelled[base] in name else \
                "the roster adds a note to the name"
        close = difflib.get_close_matches(key, self._often, 1, 0.85)
        return "also written %r on the roster - a spelling slip?" % self.spelled[close[0]] if close else ""

    def known_label(self, text):
        """A venue label that comes back week after week on this roster (Lido, Loft, The Old Haunt), or the control room."""
        return is_control(text) or len(self.label_weeks.get(" ".join(text.lower().split()), ())) >= 2

    def venue_of(self, label):
        """The venue a label names: itself, or - for an unknown label that is a known one with a remark after it
        ("Perch Kilcarran (TBC)", "Fogartys - NB new manager") - the known part."""
        head = re.split(r"\s*[(\[:,!]|\s+-\s+", label, 1)[0].strip()
        return head if head and head != label and not self.known_label(label) and self.known_label(head) else label

    def label_note(self, text, r, c, col, site):
        """Is this text, typed where a venue label goes (row r, column c), a note rather than a venue?"""
        if is_note(text):
            return True
        if self.known_label(text) or self.venue_of(text) != text:
            return False                              # a venue that comes back week after week
        words = WORD_RE.findall(text)
        if len(words) > 6 or sum(1 for w in words if w.islower() and w not in PARTICLES) >= 3:
            return True                               # a sentence, not a name ("Staff must sign in at the front desk")
        if site and is_control(site) and self.controllers_below(r, col):
            return True                               # never cut the control room off from a controller under it
        return not _dark(self.style(r, c)["fill"])    # a one-off plain cell where the venue names go

    def controllers_below(self, r, col):
        """Does anyone who sits in the control room come under row r in this column, before the next venue label?"""
        label_col = next(lc for lc, days in self.weeks.items() if col in days)
        for row in range(r + 1, self.max_row + 1):
            if self.text(row, label_col) or self.text(row, col) in WEEKDAYS:
                return False
            if self.text(row, col).lower() in self.control_people or any(same_person(c, self.text(row, col)) for c in CONTROLLERS):
                return True
        return False


def _dark(color):
    if not color:
        return False
    if color.startswith("theme"):
        return color == "theme1"
    try:
        rgb = int(color[-6:], 16)
    except ValueError:
        return False
    return max(rgb >> 16, (rgb >> 8) & 255, rgb & 255) < 0x40


def fill_word(fill):
    """What a name's colour on the roster is worth a note for, or "": white, yellow (provisional) and green (confirmed)
    are the roster's everyday states - the Confirmed box turns names green - so only other colours are named."""
    plain = {None: "", "": "", "theme0": "", "FFFFFFFF": "", "FFFFFF00": "", "FF92D050": ""}
    if str(fill).upper() in plain or fill in plain:
        return ""
    names = {"FF9900": "orange", "FFC000": "orange", "FF0000": "red", "C00000": "red", "666666": "grey", "999999": "grey",
             "808080": "grey", "BFBFBF": "grey", "00B0F0": "blue", "0070C0": "blue", "7030A0": "purple", "FF00FF": "pink"}
    return names.get(str(fill).upper()[-6:], "coloured differently")


def is_control(site):
    return site.strip().lower() in CONTROL_SITES


def is_note(text):
    """A note to whoever reads the roster: it starts with a note word ("IMPORTANT NOTE", "Important reminder", "NB:",
    "TBC", "See comment", "Reminder 5pm"), is a placeholder as a whole ("VACANT", "Cover needed", "Sick", "AL",
    "N/A", "X") or has no letter at all ("***", "---"). No person or venue on the roster is written like that.
    ("N.B. Reddy" - initials and a name - is a person.)"""
    core = " ".join(re.sub(r"[^\w/' ]+", " ", text).split())
    if not WORD_RE.search(text) or PLACEHOLDER_RE.match(core) or text.strip() == "AL":
        return True
    m = FIRST_RE.search(text)
    first = m.group(0).lower().rstrip(".") if m else ""
    if first in ("nb", "n.b"):                        # initials, if all that follows is a name in capitals
        rest = WORD_RE.findall(text[m.end():])
        return not rest or ":" in text[:m.end() + 2] or not all(w[0].isupper() for w in rest)
    return first in NOTE_WORDS


def time_hole(text):
    """A start time not filled in yet: "TBC", "TBA", "?? -close", "-close"."""
    words = [w.lower() for w in WORD_RE.findall(text)]
    return (bool(words) or "?" in text) and all(w in ("tbc", "tba", "tbd", "close", "time") for w in words)


def name_flag(text):
    """Why a cell read as a person is worth a look on the roster, or "": a note word after the name ("Dorian
    Sandler (TBC)", "... cancelled", "... !"), or small words that are no part of a name ("Keys with the bar
    manager", "Sai kiran farrow")."""
    words = WORD_RE.findall(text)
    if "!" in text or any(w.lower().rstrip(".") in NOTE_WORDS for w in words[1:]):
        return "the roster adds a note to the name"
    plain = re.sub(r"\([^)]*\)|'[^']*'", " ", text)       # (a nickname in brackets or quotes is part of a name)
    if sum(1 for w in WORD_RE.findall(plain) if w.islower() and w not in PARTICLES) >= 2:
        return "it reads more like a note than a name"
    return ""


def looks_like_name(text):
    """Roster names always carry a capital letter; notes typed into the grid usually do not."""
    return any(ch.isupper() for ch in text)


def usual_duty(date):
    """The control room on a normal week, in the same shape as Roster.duty()."""
    return [{"name": n, "start": hhmm(a), "end": hhmm(b)} for n, a, b in NORMAL_SHIFTS[date.weekday()]]


def at_the_desk(now, duty, fallback=True, newest=False):
    """Who is most likely making a log at this moment, and for which day's shift: (name, date).

    duty(date) lists that date's control-room shifts. Picks the controller who has been on longest
    among those on shift now or finished within the last GRACE minutes - so the one finishing up,
    not the one who has just walked in. newest (the version 6 window, asked for on 2026-10-24): the
    controller who came on LAST among those on shift now - at a handover the one who has just come on,
    whose calls the new ones are (build gives a call due at a handover to the one who has just come on).
    Falls back to whoever finished most recently, or returns None instead when fallback is False.
    """
    live, ended = [], []
    for back in (1, 0):
        date = now.date() - datetime.timedelta(days=back)
        midnight = datetime.datetime.combine(date, datetime.time())
        for s in duty(date):
            start = midnight + datetime.timedelta(minutes=s["start"])
            end = midnight + datetime.timedelta(minutes=s["end"] + (DAY if s["end"] <= s["start"] else 0))
            if start <= now <= end + datetime.timedelta(minutes=0 if newest else GRACE):
                live.append((start, s["name"], date))
            elif end < now:
                ended.append((end, s["name"], date))
    if live:
        return (max(live) if newest else min(live))[1:]
    if not fallback:
        return None                                   # nobody is on shift right now according to this source
    return max(ended)[1:] if ended else (CONTROLLERS[0], now.date())


def same_person(typed, roster_name):
    """'henry' or 'henry m' matches 'Henry Marlow'."""
    want = re.sub(r"[^\w\s]", " ", typed.lower()).split()
    have = roster_name.lower().split()
    return bool(want) and all(any(h.startswith(w) for h in have) for w in want)


def hhmm(text):
    m = re.match(r"^(\d{1,2})[:.](\d{2})$", text.strip())
    if not m:
        raise Problem("can't read the time %r - use HH:MM" % text)
    return int(m.group(1)) * 60 + int(m.group(2))


def assign_times(calls, seed):
    """Call times. Each start-time group goes out just after its one-hour mark (16:01-16:03 for a
    17:00 start), then a minute or two apart: never before the mark, never two in the same minute.
    Every call carries 'limit', the first minute that is no longer this controller's to log."""
    rng = random.Random(seed)
    out, last = [], None
    for start in sorted({c["start"] for c in calls}):
        group = [c for c in calls if c["start"] == start]
        mark = start - LEAD
        first = mark + rng.randint(*FIRST)
        gaps = [rng.choice(GAPS) for _ in group[1:]]
        latest = min(c["limit"] for c in group) - 1
        if first + sum(gaps) > latest:                # a handover is close: one a minute, as early as allowed
            gaps = [1] * len(gaps)
            first = max(mark, min(mark + 1, latest - len(gaps)))
        t = first if last is None else max(first, last + 1)
        for c, gap in zip(group, [0] + gaps):
            t += gap
            out.append(dict(c, call=t))
        last = t
    times = [c["call"] for c in out]
    assert times == sorted(times) and len(set(times)) == len(times), "call times out of order"
    return out


def build(date, roster, name=None, start=None, end=None, seed=None, now=None):
    """Work out one controller's call log for one date. Returns a dict (title, meta, calls, notes, ...)."""
    col, shifts, warnings = roster.shifts(date)
    # Notes are ranked, and the window shows the first three: 0 people the roster could not be read for, 1 a person
    # worth a look, 2 the roster's own notes, 3 after midnight / next morning, 4 yellow, 5 empty starts, 6 the control
    # room, 7 calls still to come. (Stable: the same rank keeps the order the notes were found in.)
    ranked = [(0, w) for w in warnings]

    def note(rank, text):
        ranked.append((rank, text))
    day = date.strftime("%a %d %b %Y")
    typed = (name or CONTROLLERS[0]).strip()
    control = [s for s in shifts if is_control(s["site"])]
    on_duty = ", ".join("%s %s" % (s["name"], s["shift"]) for s in control) or "nobody"

    people = sorted({s["name"] for s in control if same_person(typed, s["name"])})
    name = people[0] if len(people) == 1 else next((c for c in CONTROLLERS if same_person(typed, c)), typed)
    if start and end:                                 # a controller who is not on the roster that day
        desk = [(hhmm(start), hhmm(end), name)]
    elif len(people) > 1:
        raise Problem("%r matches more than one controller on %s (%s) - type the full name" % (typed, day, ", ".join(people)))
    elif not people:
        raise Problem("%s is not in the control room on %s. On the roster that day: %s." % (name, day, on_duty))
    else:
        if any(s["end"] is None for s in control if s["name"] == name):
            raise Problem("%s's control-room shift has no end time on the roster for %s" % (name, day))
        desk = [(s["start"], s["end"], s["name"]) for s in control if s["end"] is not None]
    # the control room that day as (start, end, who), with an end after midnight pushed past 24:00
    desk = [(a, b + (DAY if b <= a else 0), who) for a, b, who in desk]
    mine = [d for d in desk if d[2] == name]
    if len(mine) > 1:
        note(6, "%s has %d control-room shifts that day - all their calls are on this log" % (name, len(mine)))
    ctrl_start, ctrl_end = mine[0][0], max(d[1] for d in mine)
    shift_text = "%02d:%02d-%02d:%02d" % (ctrl_start // 60, ctrl_start % 60, ctrl_end % DAY // 60, ctrl_end % 60)
    overnight = ctrl_end > DAY
    wrap = ctrl_end - DAY if overnight else 0         # typed times before this belong to after midnight
    small_hours = max((b - DAY for _, b, _ in desk if b > DAY), default=0)   # same, for times on the roster
    handover = [d for d in desk if d[2] != name and ctrl_start < d[0] < ctrl_end]
    for a, _, who in handover:
        note(6, "%s comes on at %s, so calls from then on are on their log, not this one" % (who, fmt(a)))

    staff = [s for s in shifts if not is_control(s["site"])]
    calls, before, after, called = [], 0, 0, set()
    for s in staff:
        s = dict(s)
        if s["start"] < small_hours:
            s["start"] += DAY
        due = s["start"] - LEAD
        # the call belongs to whoever is at the desk when it is due; at a handover, to the one who has just come on
        on = [d for d in desk if d[0] <= due < d[1]]
        owner = max(on, key=lambda d: (d[0], -desk.index(d))) if on else None
        if owner:
            called.add(s["site"])                     # (a venue someone in the control room calls that day)
        if owner is None or owner[2] != name:
            before, after = before + (due < ctrl_start), after + (due >= ctrl_start)
            continue
        s["limit"] = min([owner[1]] + [d[0] for d in desk if d[0] > due])
        s["venue"] = VENUE_ALIASES.get(s["site"], s["site"])
        calls.append(s)
        if s["start"] >= DAY:
            note(3, "%s at %s starts after midnight (%s) - call logged just after %s  [%s]" % (s["name"], s["venue"], s["shift"], fmt(due), s["ref"]))
        if s["strike"]:
            note(1, "%s at %s is STRUCK THROUGH on the roster - maybe cancelled; remove the row if so  [%s]" % (s["name"], s["venue"], s["ref"]))
        elif fill_word(s["fill"]):                    # (white, yellow and green are everyday states, not worth a note)
            colour = fill_word(s["fill"])
            note(1, "%s at %s is %s on the roster - worth a look  [%s]"
                 % (s["name"], s["venue"], colour if colour.startswith("coloured") else "marked " + colour, s["ref"]))
        if s.get("flag"):
            note(1, "%s at %s: %s - check it on the roster  [%s]" % (s["name"], s["venue"], s["flag"], s["ref"]))
    calls.sort(key=lambda s: (s["start"], s["row"]))
    # The same person twice at one time (R3): one of the two places is probably wrong, and may have nobody.
    twice = collections.defaultdict(list)
    for s in calls:
        twice[(" ".join(s["name"].lower().split()), s["start"])].append(s)
    for (_, start), group in twice.items():
        if len(group) > 1:
            note(1, "%s is on the roster twice at %s (%s) - probably a roster mistake; one venue may have nobody  %s"
                 % (group[0]["name"], fmt(start), ", ".join(g["venue"] for g in group), " ".join("[%s]" % g["ref"] for g in group)))
    # Notes typed on the roster go to the controller they concern: one about the control room to every controller
    # that day; one under a start time to whoever makes that time's calls; one beside a venue to whoever calls that
    # venue's staff (a note in the venue column stands all week: only on days that venue has someone to call). A
    # note in the day's own column that nobody's calls lead to goes to everyone, so none is dropped without a word.
    venues = {s["site"] for s in calls}
    for m in roster.remarks.get(col, []):
        on = []
        if m["start"] is not None:
            due = m["start"] + (DAY if m["start"] < small_hours else 0) - LEAD
            on = [d for d in desk if d[0] <= due < d[1]]
        if m["site"] is None or is_control(m["site"]):
            mine_too = True
        elif on:
            mine_too = max(on, key=lambda d: (d[0], -desk.index(d)))[2] == name
        else:
            mine_too = m["site"] in venues or not m["label"] and m["site"] not in called
        if mine_too:
            read = "The note itself is a comment on that cell of the roster" if m.get("comment") else "Read it on the roster"
            note(2, ("The roster adds a note to the venue %s: %r. %s  [%s]" % (m["site"], m["text"], read, m["ref"]))
                        if m.get("on") else
                        ("Roster note beside %s: %r - not a venue, so the names under it stay at %s. %s  [%s]"
                         % (m["site"], m["text"], m["site"], read, m["ref"])) if m["label"] and m["site"] else
                        ("Roster note under %s: %r - not a person to call. %s  [%s]"
                         % (m["site"] or "the top of the day", m["text"], read, m["ref"])))
    # Yellow on the roster: not confirmed yet - the shift may not be on (the user, 2026-10-23). One line for the log.
    yellow = [s["name"] for s in calls if str(s["fill"]).upper() == "FFFFFF00"]
    if yellow and len(yellow) == len(calls) > 1:
        note(4, "Every name on this log is yellow on the roster - not confirmed yet (the shift may not be on)")
    elif yellow:
        note(4, "%d %s yellow on the roster - not confirmed yet, the shift may not be on: %s"
             % (len(yellow), "name is" if len(yellow) == 1 else "names are",
                ", ".join(yellow[:6]) + (" and %d more" % (len(yellow) - 6) if len(yellow) > 6 else "")))
    # Start times with nobody under them yet (Lido's two 16:00-22:00 on Sat 24 Oct): one line, for whoever would
    # make those calls - or everyone, for the control room's own.
    empty = collections.OrderedDict()
    for e in roster.empty.get(col, []):
        due = e["start"] + (DAY if e["start"] < small_hours else 0) - LEAD
        on = [d for d in desk if d[0] <= due < d[1]]
        if (e["site"] and is_control(e["site"])) or (on and max(on, key=lambda d: (d[0], -desk.index(d)))[2] == name):
            empty.setdefault("%s %s" % (VENUE_ALIASES.get(e["site"], e["site"]) if e["site"] else "?", e["shift"]), []).append(e["ref"])
    if empty:
        listed = [k + (" (x%d)" % len(v) if len(v) > 1 else "") for k, v in empty.items()]
        note(5, "No name on the roster yet for %s - nobody to call there yet  [%s]"
             % (", ".join(listed[:6]) + (" and %d more" % (len(listed) - 6) if len(listed) > 6 else ""),
                " ".join(r for v in empty.values() for r in v)))
    if not calls:
        raise Problem("nobody on the roster needs a check call during %s's shift (%s) on %s" % (name, shift_text, day))

    # early starts the next morning whose call would still fall inside this shift
    if overnight and not handover:
        try:
            _, nxt, _ = roster.shifts(date + datetime.timedelta(days=1))
        except Problem:
            nxt = []
        for s in nxt:
            if not is_control(s["site"]) and wrap <= s["start"] and s["start"] + DAY - LEAD < ctrl_end:
                note(3, "next morning: %s at %s starts %s - NOT in this log, add by hand if you called  [%s]"
                     % (s["name"], VENUE_ALIASES.get(s["site"], s["site"]), fmt(s["start"]), s["ref"]))

    calls = assign_times(calls, seed if seed is not None else int(date.strftime("%Y%m%d")))
    # a time or response corrected by hand replaces the generated one for that person, and the
    # log goes back into time order (the sort is stable, so untouched rows keep their order)
    file_name = re.sub(r'[\\/:*?"<>|]', "", name)
    stem = "%s %s" % (date.isoformat(), file_name)
    edits = load_edits(stem)
    for c in calls:
        fix = edits.get(edit_key(c))                  # (only what has the right shape: a damaged file must not stop the log)
        c["fix"] = {k: v for k, v in fix.items() if isinstance(v, str) and (k != "time" or _clock_text(v))} if isinstance(fix, dict) else {}
        c["edited"] = bool(c["fix"])
        c["timed"] = bool(c["fix"].get("time"))       # the time was recorded or typed by hand, not generated
        if c["fix"].get("time"):
            typed = hhmm(c["fix"]["time"])
            c["call"] = next((t for t in (typed, typed + DAY) if ctrl_start <= t <= ctrl_end), typed + (DAY if typed < wrap else 0))
    calls.sort(key=lambda c: c["call"])
    # A call that has not happened yet is listed but left blank - unless the controller wants it otherwise: a time
    # he typed is always shown, and with the whole shift "filled in ahead" (fill_ahead) every call gets its time
    # and "Ok" as if the shift were over, to be corrected afterwards where it went differently.
    whole = edits.get(WHOLE_SHIFT)
    ahead = isinstance(whole, dict) and whole.get("ahead") == "yes"
    now = now or datetime.datetime.now()
    midnight = datetime.datetime.combine(date, datetime.time())
    pending = 0
    for i, c in enumerate(calls, 1):
        fix = c.pop("fix")
        c["no"] = i
        c["due"] = fmt(c["start"] - LEAD)
        made = midnight + datetime.timedelta(minutes=c["call"]) <= now
        c["ahead"] = not made and (ahead or c["timed"])     # shown although the call is still to be made
        if made or c["ahead"]:
            c["clock"], c["call_text"], c["response"] = c["call"] % DAY, fmt(c["call"]), fix.get("response", RESPONSE)
        else:
            c["clock"], c["call_text"], c["response"] = None, "", ""
            pending += 1
    early = sum(1 for c in calls if c["ahead"])
    if pending:
        note(7, "%d of the %d calls are still to come - their time and response are left blank to fill in by hand "
                "(or make the log again after the shift)" % (pending, len(calls)))
    if early:
        note(7, "%d call%s filled in ahead of being made - change any that turn out different" % (early, " is" if early == 1 else "s are"))
    notes = [text for _, text in sorted(ranked, key=lambda x: x[0])]

    meta = {"date": date.strftime("%d/%m/%y"), "date_iso": date.isoformat(), "date_serial": (date - EPOCH).days,
            "supervisor": name, "shift": shift_text, "total": len(calls), "pending": pending, "ahead": ahead, "early": early,
            "roster_column": idx_to_col(col), "control_room": on_duty,
            "start_min": ctrl_start, "end_min": ctrl_end, "wrap": wrap}
    return {"title": "Check Call Log - %s - %s" % (day, file_name), "stem": stem,
            "day": day, "meta": meta, "calls": calls,
            "notes": notes, "before": before, "after": after}


def edit_key(call):
    """What identifies a row across rebuilds: the person, the venue and the shift, as the roster writes them."""
    return "%s|%s|%s" % (call["name"], call["site"], call["shift"])


def load_edits(stem):
    try:
        with open(os.path.join(EDITS, stem + ".json"), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def save_edit(log, call, field, value):
    """Remember a hand correction for one row ('time' as H:MM, or 'response'); None forgets it."""
    edits = load_edits(log["stem"])
    entry = edits.get(edit_key(call), {})
    if value is None:
        entry.pop(field, None)
    else:
        entry[field] = value
    if entry:
        edits[edit_key(call)] = entry
    else:
        edits.pop(edit_key(call), None)
    _keep_edits(log["stem"], edits)


def _keep_edits(stem, edits):
    path = os.path.join(EDITS, stem + ".json")
    if edits:
        os.makedirs(EDITS, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(edits, f, indent=1, ensure_ascii=False)
    elif os.path.exists(path):
        os.remove(path)


def fill_ahead(log, on):
    """Fill in - or stop filling in - what is still to come on a check call log or a patrol log: every call gets
    its time and "Ok", every usual round is listed, as if the shift were over. The user asked for this on
    2026-10-11: sometimes the sheet is wanted complete before the shift ends, and what turns out different is
    corrected afterwards. It is the controller's choice for that one shift, kept with its other corrections."""
    if "calls" in log:
        edits = load_edits(log["stem"])
        edits.pop(WHOLE_SHIFT, None)
        if on:
            edits[WHOLE_SHIFT] = {"ahead": "yes"}
        _keep_edits(log["stem"], edits)
    else:
        store = _patrol_store(log)
        store["ahead"] = bool(on)
        _keep_patrol_store(log, store)


def shift_clock(text, meta, late=0, early=0):
    """HH:MM -> minutes from the midnight the shift's date began with. A time that only fits the shift on the next
    calendar day - the small hours of an overnight shift, or 00:00 as the last minute of one ending at midnight -
    counts as after 24:00. early / late: minutes allowed before the shift starts and after it ends."""
    return in_shift(hhmm(text), meta, late, early)


def in_shift(t, meta, late=0, early=0):
    """The same, from minutes after midnight."""
    for at in (t, t + DAY, t - DAY):                  # (t - DAY: just before a shift that starts soon after midnight)
        if meta["start_min"] - early <= at <= meta["end_min"] + late:
            return at
    return t


def parse_clock(text, now=None):
    """A time of day the way people type it -> minutes after midnight. Takes 17:05, 17.05, 17 05, 1705, 705, 7:5, a
    bare hour (17 = 17:00), 5pm / 5.05pm / 12am, a trailing h (1705h), and n or now for the minute it is.
    Raises Problem for anything that cannot be read as a time."""
    raw = text.strip().lower()
    if raw in ("n", "now"):
        at = now or datetime.datetime.now()
        return at.hour * 60 + at.minute
    half = r"\s*(am|pm|a|p|h|hrs?)?$"
    m = re.match(r"^(\d{1,2})(\d{2})" + half, raw) or re.match(r"^(\d{1,2})(?:\s*[:.,;]\s*|\s+)(\d{1,2})" + half, raw)
    hour, minute, word = (int(m.group(1)), int(m.group(2)), m.group(3) or "") if m else (None, 0, "")
    if not m:
        m = re.match(r"^(\d{1,2})" + half, raw)
        hour, word = (int(m.group(1)), m.group(2) or "") if m else (None, "")
    if hour is not None and word[:1] in ("a", "p"):   # 12am is midnight, 12pm is noon
        hour = None if not 1 <= hour <= 12 else hour % 12 + (12 if word[0] == "p" else 0)
    if hour is None or hour > 23 or minute > 59:
        raise Problem("%r is not a time - type it like 17:05 or 1705" % text.strip())
    return hour * 60 + minute


def read_time(text, log, early=0, late=0):
    """A time typed by hand -> 'H:MM', checked against this log's shift. Raises Problem if it cannot be right.
    early / late: minutes allowed before the shift starts and after it ends (a shift log line can be a little outside).
    A time still to come is accepted (since 2026-10-11, at the user's wish): the window only remarks on it."""
    meta = log["meta"]
    t = in_shift(parse_clock(text), meta, late, early)
    if not meta["start_min"] - early <= t <= meta["end_min"] + late:
        raise Problem("%s is outside this shift (%s)" % (fmt(t), meta["shift"]))
    return fmt(t)


def shift_stems(date, who):
    """The names of the three small files that hold one shift's hand corrections (calls, patrols, shift log)."""
    base = "%s %s" % (date.isoformat(), re.sub(r'[\\/:*?"<>|]', "", who))
    return [base, base + " patrols", base + " shift"]


def snapshot(stems):
    """What those files hold right now (None = no such file): the whole of a shift's corrections, to go back to."""
    held = {}
    for stem in stems:
        try:
            with open(os.path.join(EDITS, stem + ".json"), "rb") as f:
                held[stem] = f.read()
        except OSError:
            held[stem] = None
    return held


def restore(held):
    """Put a snapshot back exactly: this is all that undo and redo are."""
    for stem, data in held.items():
        path = os.path.join(EDITS, stem + ".json")
        if data is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            os.makedirs(EDITS, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)


def desk_shift(date, roster, name=None, start=None, end=None):
    """Whose control-room shift it is on a date: (full name, start, end) in minutes, the end past 24:00 if overnight."""
    typed = (name or CONTROLLERS[0]).strip()
    day = date.strftime("%a %d %b %Y")
    control = roster.duty(date)
    people = sorted({s["name"] for s in control if same_person(typed, s["name"])})
    who = people[0] if len(people) == 1 else next((c for c in CONTROLLERS if same_person(typed, c)), typed)
    if start and end:
        a, b = hhmm(start), hhmm(end)
    elif len(people) > 1:
        raise Problem("%r matches more than one controller on %s (%s) - type the full name" % (typed, day, ", ".join(people)))
    elif not people:
        on_duty = ", ".join("%s %s" % (s["name"], s["shift"]) for s in control) or "nobody"
        raise Problem("%s is not in the control room on %s. On the roster that day: %s." % (who, day, on_duty))
    else:
        mine = [s for s in control if s["name"] == who]
        a, b = mine[0]["start"], max(s["end"] + (DAY if s["end"] <= s["start"] else 0) for s in mine)
    return who, a, b + (DAY if b <= a else 0)


def _shift_meta(date, who, a, b):
    """The heading shared by the patrol log and the shift log."""
    return {"date": date.strftime("%d/%m/%y"), "date_iso": date.isoformat(), "supervisor": who,
            "shift": "%s-%s" % (fmt(a).zfill(5), fmt(b).zfill(5)), "shift_name": "Day" if a < 12 * 60 else "Night",
            "start_text": fmt(a).zfill(5), "end_text": fmt(b).zfill(5),
            "start_min": a, "end_min": b, "wrap": b - DAY if b > DAY else 0}


PATROLS_V2_FROM = datetime.date(2026, 10, 11)     # shifts from this day get the more varied rounds; earlier ones keep the times already printed
ROUNDS = {"Day": ((455, 515), (695, 755), (935, 995)),            # the three rounds: about 08:00, 12:00 and 16:00
          "Night": ((1290, 1355), (1485, 1570), (1670, 1760))}    # ...or about 22:00, 01:00-02:00 and 04:00-05:00
PATROL_MINUTES = [10, 12, 14, 15, 15, 17, 18, 20, 20, 22, 25, 28, 30]


def usual_patrols(date, who, a, b):
    """The usual three rounds of a shift, as rows with the ids u0, u1, u2: a starting point for the controller to
    correct to what was walked. They follow the person: each controller leans early or late in their own way, the
    times move about from one day to the next, they stay inside that person's own hours (a short or odd shift has
    its rounds spread through it), and each lasts 10-30 minutes. The same person and day always give the same times."""
    rng = random.Random("%s %s patrols" % (date.isoformat(), who))
    rows = []
    if date < PATROLS_V2_FROM:                        # the first rule: on or near the hour at 08/12/16, or 22, 01-02, 04-05
        for n, choice in enumerate(((8,), (12,), (16,)) if a < 12 * 60 else ((22,), (25, 26), (28, 29))):
            start = rng.choice(choice) * 60 + rng.choice([0, 0, 0, 0, 5, 10, 20])
            finish = start + rng.choice([10, 12, 15, 15, 17, 20, 20, 25, 30])
            if a + 30 <= start and finish <= b - 10:
                rows.append({"id": "u%d" % n, "start": start, "finish": finish, "area": "All", "notes": ""})
        return rows
    habit = random.Random("%s rounds" % who)          # the same for this person every shift
    span = b - a
    fits = lambda windows: [w for w in ((max(lo, a + 30), min(hi, b - 45)) for lo, hi in windows) if w[1] - w[0] >= 10]
    windows = fits(ROUNDS["Day" if a < 12 * 60 else "Night"])
    if len(windows) < 3:                              # not the usual hours (cover, a long or late shift): every round time inside it...
        windows = fits(ROUNDS["Day"] + ROUNDS["Night"])
        if len(windows) < 3:                          # ...and where that leaves fewer than three, three rounds spread evenly through the shift
            windows = [(a + span * k // 6 - 12, a + span * k // 6 + 12) for k in (1, 3, 5)] if span >= 240 else \
                [(a + span // 2 - 10, a + span // 2 + 10)] if span >= 90 else []
    last = a
    for n, (lo, hi) in enumerate(windows):
        lean = lo + (hi - lo) * habit.random()        # where in the window this person tends to set off
        start = rng.triangular(lo, hi, lean)
        hour = int(round(start / 60.0)) * 60
        start = hour if rng.random() < 0.35 and lo <= hour <= hi else min(hi, max(lo, int(round(start / 5.0)) * 5))
        finish = start + rng.choice(PATROL_MINUTES)
        if start >= last + 20 and finish <= b - 10:
            rows.append({"id": "u%d" % n, "start": start, "finish": finish, "area": "All", "notes": ""})
            last = finish
    return rows


def _clock_text(text):
    """Is this a time of day as the tool saves it (HH:MM, 00:00-23:59)?"""
    m = re.match(r"^(\d{1,2})[:.](\d{2})$", text.strip()) if isinstance(text, str) else None
    return bool(m) and int(m.group(1)) <= 23 and int(m.group(2)) <= 59


def _patrol_store(log):
    """What has been changed by hand on a patrol log: rounds altered, rounds taken out, patrols added, or all cleared.
    Only what has the right shape is kept, so a damaged file can never stop the log being made."""
    raw = load_edits(log["stem"])
    if isinstance(raw.get("rows"), list) and "added" not in raw:   # the first format: a plain list that stood in place of the usual rounds
        raw = {"blank": True, "added": [dict(r, id="a%d" % n, finish_by="hand") for n, r in enumerate(raw["rows"], 1) if isinstance(r, dict)]}
    of = lambda key, kind: raw[key] if isinstance(raw.get(key), kind) else kind()
    changed = {}
    for row_id, fix in of("changed", dict).items():
        fix = {k: v for k, v in fix.items() if isinstance(v, str) and (k not in ("start", "finish") or _clock_text(v))} if isinstance(fix, dict) else {}
        if fix:
            changed[row_id] = fix
    added = [{k: v for k, v in r.items() if isinstance(v, str)} for r in of("added", list)
             if isinstance(r, dict) and isinstance(r.get("id"), str) and _clock_text(r.get("start")) and _clock_text(r.get("finish"))]
    return {"changed": changed, "removed": [x for x in of("removed", list) if isinstance(x, str)], "added": added,
            "blank": bool(raw.get("blank")), "ahead": bool(raw.get("ahead"))}


def _keep_patrol_store(log, store):
    path = os.path.join(EDITS, log["stem"] + ".json")
    if not (store["changed"] or store["removed"] or store["added"] or store["blank"] or store.get("ahead")):
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(EDITS, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=1, ensure_ascii=False)


def build_patrols(date, roster, name=None, start=None, end=None, now=None):
    """The Campus Patrol Log for one controller's shift: the heading and the patrols to print.

    The rows are the usual rounds of the shift - all of them, whether done yet or not: the user asked on 2026-10-12
    that every controller always has their three rounds on the log ("sometimes soren has zero"); a round still
    to come carries ahead=True and is marked on screen - with whatever was changed by hand laid over them: a
    round's times, area or note altered, a round taken out, patrols added. Every row has a lasting id, so a
    change always lands on the patrol it was made to.
    A usual round nobody has touched gives way to a patrol put down by hand at the same time (it was only a
    suggestion): it is left off the list, and named in log["covered"]."""
    who, a, b = desk_shift(date, roster, name, start, end)
    meta = _shift_meta(date, who, a, b)
    day = date.strftime("%a %d %b %Y")
    file_name = re.sub(r'[\\/:*?"<>|]', "", who)
    log = {"title": "Patrol Log - %s - %s" % (day, file_name), "stem": "%s %s patrols" % (date.isoformat(), file_name),
           "day": day, "meta": meta}
    store = _patrol_store(log)
    gone = ((now or datetime.datetime.now()) - datetime.datetime.combine(date, datetime.time())).total_seconds() / 60.0

    clock = lambda text: shift_clock(text, meta)
    usual = []
    for r in [] if store["blank"] else usual_patrols(date, who, a, b):
        fix = store["changed"].get(r["id"], {})
        if r["id"] in store["removed"]:
            continue
        row = dict(r, edited=bool(fix), area=fix.get("area", r["area"]), notes=fix.get("notes", r["notes"]),
                   finish_typed=fix.get("finish_by") == "hand")     # a finish typed by hand is not moved when the start is
        row["start"] = clock(fix["start"]) if "start" in fix else r["start"]
        row["finish"] = clock(fix["finish"]) if "finish" in fix else r["finish"]
        usual.append(row)                             # every round of the shift, done or still to come (see the docstring)
    rows = [{"id": r["id"], "start": clock(r["start"]), "finish": clock(r["finish"]), "area": r.get("area", ""), "notes": r.get("notes", ""),
             "edited": True, "finish_typed": r.get("finish_by") == "hand"} for r in store["added"]]
    by_hand = rows + [r for r in usual if r["edited"]]
    covered = [r for r in usual if not r["edited"] and any(r["start"] < h["finish"] and h["start"] < r["finish"] for h in by_hand)]
    rows += [r for r in usual if r not in covered]
    # in time order; patrols starting together stay as they were saved (usual rounds first, then a1, a2 ... a10)
    rows.sort(key=lambda r: (r["start"], r["id"][:1] != "u", int(r["id"][1:]) if r["id"][1:].isdigit() else 0))
    for r in rows + covered:
        r.update(officer=who, start_text=fmt(r["start"]).zfill(5), finish_text=fmt(r["finish"]).zfill(5), ahead=r["finish"] > gone)
    log.update(rows=rows, covered=["%s-%s" % (r["start_text"], r["finish_text"]) for r in covered], ahead=store["ahead"],
               typed=bool(store["changed"] or store["removed"] or store["added"] or store["blank"]))
    return log


def patrol_set(log, row_id, **fields):
    """Change one patrol: any of start / finish (as HH:MM), area, notes."""
    store = _patrol_store(log)
    if row_id.startswith("u"):
        store["changed"].setdefault(row_id, {}).update(fields)
    else:
        for r in store["added"]:
            if r["id"] == row_id:
                r.update(fields)
    _keep_patrol_store(log, store)


def patrol_add(log, start, finish, area="All", notes=""):
    """Add a patrol (times as HH:MM); returns its id."""
    store = _patrol_store(log)
    new = "a%d" % (max([int(r["id"][1:]) for r in store["added"] if r["id"][1:].isdigit()] + [0]) + 1)
    store["added"].append({"id": new, "start": start, "finish": finish, "area": area, "notes": notes})
    _keep_patrol_store(log, store)
    return new


def patrol_remove(log, ids):
    """Take patrols out: a usual round is remembered as removed, an added patrol is simply dropped."""
    store = _patrol_store(log)
    for row_id in ids:
        if row_id.startswith("u"):
            store["changed"].pop(row_id, None)
            if row_id not in store["removed"]:
                store["removed"].append(row_id)
    store["added"] = [r for r in store["added"] if r["id"] not in ids]
    _keep_patrol_store(log, store)


def patrol_clear(log):
    """No patrols at all: the form prints blank to write on."""
    _keep_patrol_store(log, {"changed": {}, "removed": [], "added": [], "blank": True, "ahead": _patrol_store(log)["ahead"]})


def patrol_reset(log):
    """Forget every change: back to the usual rounds."""
    _keep_patrol_store(log, {"changed": {}, "removed": [], "added": [], "blank": False, "ahead": _patrol_store(log)["ahead"]})


def patrol_slot(log, now=None):
    """Where a patrol being added goes, as (start, finish) in minutes, or a Problem if the shift is full.
    During the shift it is the quarter of an hour just gone (that is when a patrol gets written up); on a shift
    that is over - or not begun - a quarter of an hour in the longest stretch without a patrol. The controller
    then sets the times."""
    m, rows = log["meta"], log["rows"]
    gone = int(((now or datetime.datetime.now()) - datetime.datetime.combine(
        datetime.date.fromisoformat(m["date_iso"]), datetime.time())).total_seconds() // 60)
    live = m["start_min"] + 20 <= gone < m["end_min"]
    limit = gone if live else m["end_min"]            # (a shift not begun, or only just: anywhere in it)
    busy = []                                         # the stretches already taken, inside the part of the shift gone by, run together
    for s, f in sorted((max(r["start"], m["start_min"]), min(r["finish"], limit)) for r in rows):
        if f > s and busy and s <= busy[-1][1]:
            busy[-1][1] = max(busy[-1][1], f)
        elif f > s:
            busy.append([s, f])
    clear = lambda s, f: all(f <= x or s >= y for x, y in busy)
    if live and clear(max(m["start_min"], limit - 15), limit):
        return max(m["start_min"], limit - 15), limit
    edges = [m["start_min"]] + [t for stretch in busy for t in stretch] + [limit]
    gaps = [(edges[i + 1] - edges[i], edges[i]) for i in range(0, len(edges), 2)]
    room, at = max(gaps)
    if room < 5:
        raise Problem("There is no room for another patrol - change the times of the ones listed, or remove one.")
    length = min(15, room)
    start = at + (room - length) // 2
    start = max(at, min(start // 5 * 5, at + room - length)) if room >= 25 else start
    return start, start + length


def next_on_duty(date, roster, who, a):
    """Who takes the control room over from this controller: the next one to come on after their shift began."""
    later = []
    for ahead in (0, 1):
        for s in roster.duty(date + datetime.timedelta(days=ahead)):
            if s["name"] != who and s["start"] + ahead * DAY > a:
                later.append((s["start"] + ahead * DAY, s["name"]))
    return min(later)[1] if later else ""


def build_shift(date, roster, name=None, start=None, end=None):
    """The Shift Log for one controller's shift. The heading is always filled in, and the incoming officer is
    whoever the roster has taking over (unless that was changed by hand); occurrences, handover notes and
    supervisor are whatever has been typed in the window, else blank. Every occurrence has a lasting id."""
    who, a, b = desk_shift(date, roster, name, start, end)
    file_name = re.sub(r'[\\/:*?"<>|]', "", who)
    day = date.strftime("%a %d %b %Y")
    meta = _shift_meta(date, who, a, b)
    stem = "%s %s shift" % (date.isoformat(), file_name)
    typed = load_edits(stem)

    def when(entry):                                  # in time order; entries without a time last, as typed
        try:
            return shift_clock(entry["time"], meta, late=GRACE, early=EARLY)
        except Problem:
            return 10 ** 6

    saved = [e for e in typed.get("entries", []) if isinstance(e, dict)] if isinstance(typed.get("entries"), list) else []
    word = lambda value: value if isinstance(value, str) else ""
    entries, taken, seen = [], {word(e.get("id")) for e in saved}, set()
    for e in saved:
        e = {"id": word(e.get("id")), "time": word(e.get("time")), "area": word(e.get("area")), "text": word(e.get("text"))}
        if not e["id"] or e["id"] in seen:            # saved before lines had ids (or, in a damaged file, the same id twice)
            if not (e["id"] or e["time"] or e["area"] or e["text"]):
                continue                              # an empty line left behind by an older version
            e["id"] = next("e%d" % n for n in range(1, 10 ** 4) if "e%d" % n not in taken)
            taken.add(e["id"])
        seen.add(e["id"])
        entries.append(e)
    entries.sort(key=when)
    relief = next_on_duty(date, roster, who, a)
    # A quiet shift says so, and the two halves of the form are settled separately (asked for on 2026-10-12):
    #   the table   - no line typed: NO MAJOR OCCURRENCE across it; any line typed: just the lines.
    #   the handover - notes typed: the notes. None typed: NTR if the table is empty too, but AS PER OCCURRENCES ABOVE
    #                  if lines were typed - "nothing to report" under a list of occurrences would contradict it.
    handover = word(typed.get("handover"))
    return {"title": "Shift Log - %s - %s" % (day, file_name), "stem": stem, "day": day, "meta": meta, "entries": entries,
            "quiet": "" if entries else NO_OCCURRENCE,
            "handover_default": "" if handover.strip() else SEE_ABOVE if entries else NOTHING_TO_REPORT,
            "handover": handover, "supervisor": word(typed.get("supervisor")),
            "incoming": typed["incoming"] if isinstance(typed.get("incoming"), str) else relief, "incoming_roster": relief}


def save_shift(log, entries, handover="", supervisor="", incoming=""):
    """Keep what was typed on a Shift Log. With nothing typed at all the file is removed."""
    path = os.path.join(EDITS, log["stem"] + ".json")
    data = {"entries": [{"id": e["id"], "time": e["time"], "area": e["area"], "text": e["text"]} for e in entries],
            "handover": handover.strip(), "supervisor": supervisor.strip()}
    if incoming.strip() != log.get("incoming_roster", ""):    # only a name changed by hand is kept; else the roster decides
        data["incoming"] = incoming.strip()
    if not any(data.values()) and "incoming" not in data:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(EDITS, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


def new_entry_id(log):
    return "e%d" % (max([int(e["id"][1:]) for e in log["entries"] if e["id"][1:].isdigit()] + [0]) + 1)


def _write(path, write, files, kind="pdf"):
    try:
        write(path)
        files[kind] = path
    except PermissionError:
        files["problems"].append("%s is open in another program - close it there and make the log again" % os.path.basename(path))
    except OSError as e:
        files["problems"].append("%s could not be written: %s" % (os.path.basename(path), e))


def write_outputs(log, out_dir=None):
    """Write the PDF and the spreadsheet for a built check call log. Returns {'pdf', 'xlsx', 'problems'}.
    Everything is written directly by forms.py; no other program is started."""
    out_dir = out_dir or LOGS
    os.makedirs(out_dir, exist_ok=True)
    files = {"pdf": None, "xlsx": None, "problems": []}
    _write(os.path.join(out_dir, log["title"] + ".pdf"), lambda p: forms.write_pdf(log["meta"], log["calls"], p), files)
    _write(os.path.join(out_dir, log["title"] + ".xlsx"), lambda p: forms.write_xlsx(log["meta"], log["calls"], p), files, "xlsx")
    return files


def write_patrols(log, out_dir=None):
    out_dir = out_dir or LOGS
    os.makedirs(out_dir, exist_ok=True)
    files = {"pdf": None, "problems": []}
    _write(os.path.join(out_dir, log["title"] + ".pdf"), lambda p: forms.write_patrol_pdf(log["meta"], log["rows"], p), files)
    return files


def write_shift(log, out_dir=None):
    out_dir = out_dir or LOGS
    os.makedirs(out_dir, exist_ok=True)
    files = {"pdf": None, "problems": []}
    _write(os.path.join(out_dir, log["title"] + ".pdf"), lambda p: forms.write_shift_pdf(log, p), files)
    return files


def write_pack(logs, out_dir=None):
    """Every form made for one shift in a single PDF, to print in one go. logs: {"calls": .., "patrols": .., "shift": ..}."""
    out_dir = out_dir or LOGS
    os.makedirs(out_dir, exist_ok=True)
    pages = []
    if "calls" in logs:
        pages += forms.call_pages(logs["calls"]["meta"], logs["calls"]["calls"])
    if "patrols" in logs:
        pages += forms.patrol_pages(logs["patrols"]["meta"], logs["patrols"]["rows"])
    if "shift" in logs:
        pages += forms.shift_pages(logs["shift"])
    files = {"pdf": None, "problems": []}
    shift_name = next(iter(logs.values()))["title"].split(" - ", 1)[1]
    _write(os.path.join(out_dir, "All Logs - %s.pdf" % shift_name), lambda p: forms.write_pages(pages, p), files)
    return files


def summary(log):
    m = log["meta"]
    return "%s  |  %s  |  shift %s  |  %d calls  (%d other shifts that day are on another controller's log)" % (
        log["day"], m["supervisor"], m["shift"], m["total"], log["before"] + log["after"])


def main():
    ap = argparse.ArgumentParser(description="Build the Daily Check Call Log for one or more dates.")
    ap.add_argument("dates", nargs="+", help="03/10, 03/10/2026, 2026-10-03, today, yesterday, fri, sat, sun ...")
    ap.add_argument("--name", help="controller (default %s); a first name is enough" % CONTROLLERS[0])
    ap.add_argument("--start", help="controller shift start HH:MM (default: read from the roster)")
    ap.add_argument("--end", help="controller shift end HH:MM (default: read from the roster)")
    ap.add_argument("--no-download", action="store_true", help="use the roster copy saved last time")
    ap.add_argument("--dry", action="store_true", help="print the list only, write no files")
    ap.add_argument("--open", action="store_true", help="open the PDF when done")
    ap.add_argument("--out", help="folder for the PDF/xlsx (default: Logs)")
    ap.add_argument("--seed", type=int, help="change the random call times")
    ap.add_argument("--patrols", action="store_true", help="also write the Campus Patrol Log")
    ap.add_argument("--shift-log", action="store_true", help="also write the Shift Log (heading filled in, the rest blank)")
    args = ap.parse_args()
    if bool(args.start) != bool(args.end):
        ap.error("--start and --end go together")
    sys.stdout.reconfigure(errors="replace")
    try:
        dates = parse_dates(args.dates, datetime.date.today())
        if args.no_download and os.path.exists(ROSTER_PATH):
            print("Roster: saved copy (not re-downloaded)")
        else:
            print("Roster: " + download_roster())
        roster = Roster()
    except Problem as e:
        print("PROBLEM: %s" % e)
        return 1
    ok = True
    for d in dates:
        for wanted, make, write in ((args.patrols, build_patrols, write_patrols), (args.shift_log, build_shift, write_shift)):
            if wanted and not args.dry:
                try:
                    extra = write(make(d, roster, args.name, args.start, args.end), args.out)
                    print("\n".join(["saved: " + extra["pdf"]] if extra["pdf"] else []) + "".join("\nPROBLEM: " + p for p in extra["problems"]))
                except Problem as e:
                    print("\nPROBLEM: %s" % e)
        try:
            log = build(d, roster, args.name, args.start, args.end, args.seed)
        except Problem as e:
            ok = False
            print("\nPROBLEM: %s" % e)
            continue
        print("\n=== %s ===" % summary(log))
        print(" No  Call   Roster shift   Name                      Venue")
        for c in log["calls"]:
            print("%3d  %-5s  %-13s  %-24s  %s" % (c["no"], c["call_text"] or "-", c["shift"], c["name"], c["venue"]))
        for n in log["notes"]:
            print("NOTE: " + n)
        if args.dry:
            continue
        files = write_outputs(log, args.out)
        for p in files["problems"]:
            ok = False
            print("PROBLEM: " + p)
        for kind in ("pdf", "xlsx"):
            if files[kind]:
                print("saved: " + files[kind])
        if args.open and files["pdf"]:
            os.startfile(files["pdf"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
