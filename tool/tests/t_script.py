"""The script that goes into the roster (rostercolour.SCRIPT, version 4), run for real - by Windows' own script host
(cscript, JScript) against a fake roster that behaves the way Google's does where it matters: dates in the
spreadsheet's own time zone, Irish summer time and its changes, getRange taking ranges, small letters, R1C1 and other
tabs (as Google's does), a value that is not what is shown, merged cells.
What must hold, whatever is sent: it never writes any text; it only ever sets a fill of green (#92d050) or white
(#ffffff), only on the one cell asked for, only if that cell shows exactly the name asked for and that is a name,
only under today's date - or yesterday's before noon - on the control room's clock, only on a white, yellow or green
cell that is not merged (white only from green), never in the header rows, and never without the key.

Also here: the first version (tests\\roster-script-v1.gs, on the roster until the new one is put in), run the same way;
the stand-in the window's checks use (fakescript.py) must answer every request exactly as each real version does; and
the not-a-name rules must refuse none of the people on the pinned roster. Nothing here touches the real roster."""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("CCL_TOOL", os.path.dirname(HERE)))
sys.path.insert(0, HERE)
import rostercolour  # noqa: E402
import fakescript  # noqa: E402

WORK = os.path.join(tempfile.gettempdir(), "ccl-tests", "script-%d" % os.getpid())
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)
KEY = "k3y-for-the-test-only-0123456789abcdef"
G, W, Y = "#92d050", "#ffffff", "#ffff00"
results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print("%s  %s  %s" % ("PASS" if ok else "FAIL", label, str(detail)[:400]))


# ---- the clock: Irish time (UTC+1 in summer: last Sunday of March 01:00 UTC to last Sunday of October 01:00 UTC) ----
def last_sunday(y, m):
    nxt = datetime.date(y + (m == 12), m % 12 + 1, 1) - datetime.timedelta(days=1)
    return nxt - datetime.timedelta(days=(nxt.weekday() + 1) % 7)


def summer(utc):
    y = utc.year
    return datetime.datetime.combine(last_sunday(y, 3), datetime.time(1)) <= utc < datetime.datetime.combine(last_sunday(y, 10), datetime.time(1))


def utc_of(local):
    """'2026-10-24 05:30' in Dublin -> that instant in UTC (naive)."""
    t = datetime.datetime.strptime(local, "%Y-%m-%d %H:%M")
    guess = t - datetime.timedelta(hours=1)
    return guess if summer(guess) else t


def local_of(utc):
    return utc + datetime.timedelta(hours=1) if summer(utc) else utc


TODAY = datetime.date(2026, 10, 24)                    # a Saturday in Irish summer time: UTC and Dublin differ
day = lambda n: TODAY + datetime.timedelta(days=n)
serial = lambda d: (d - datetime.date(1899, 12, 30)).days

# ---- the fake roster ----
HEAD = {"FI": ("date", day(-2)), "FJ": ("date", day(-1)), "FK": ("date", day(0)), "FL": ("date", day(1)), "FS": ("date", day(7)),
        "GA": ("serial", serial(day(0))), "GB": ("serial", serial(day(-1))), "GC": ("serial", serial(day(1))), "GD": ("text", "Fri"),
        "GF": ("serial", serial(day(0)) + 0.75), "GG": ("serial", serial(day(-1)) + 0.6),
        "ZA": ("date", datetime.date(2027, 3, 27)), "ZB": ("date", datetime.date(2027, 3, 28)), "ZC": ("date", datetime.date(2027, 3, 29))}
CELLS = {  # ref: (shown, fill[, value when it is not the text])
    "FK1": ("Sat", "#00b0f0"), "GA1": ("Saturday", W), "GA2": ("Sat 24 Oct", W),
    "FK74": ("Lena Drayton", W), "FK75": ("John Smith", G), "FK76": ("Ann Lee", Y), "FK77": ("Ruben Corbett", "#ff9900"),
    "FK68": ("Kilcarran", "#000000"), "FK69": ("06.00-18.00", W), "FK78": ("Red Name", "#ff0000"), "FK79": ("", W),
    "FK80": ("Lena Drayton", W), "FL74": ("Lena Drayton", W), "FJ74": ("Soren Rask", W), "FI74": ("Henry Marlow", G),
    "FS74": ("Basil Janek", Y), "GA74": ("Serial Today", W), "GB74": ("Serial Yesterday", W), "GC74": ("Serial Tomorrow", W),
    "GD74": ("Text Header", W), "GE74": ("No Header", W), "FK81": ("  Spaced   Name ", W), "FK82": ("O'Ferreira \"Big\" Joe", W),
    "FK83": ("Grey Name", "#666666"), "FK84": ("Still Yellow", Y), "FK85": ("Afternoon Name", W),
    "FJ75": ("Night Eleven", W), "FJ76": ("Night Noon", W), "FJ77": ("Night Midnight", W), "FJ78": ("Yellow Yesterday", Y),
    "FK86": ("Late Evening", W), "FK87": ("Just Past Midnight", W), "FK66": ("Saturday", "#00b0f0"), "FK88": ("Light Grey", "#999999"),
    "FK89": ("Pink Name", "#ff00ff"), "FK90": ("Was Empty", W), "FK91": ("Double  Space", W), "FK92": ("Yellow To White", Y),
    "FK93": ("Merged Name", W), "FK94": ("18:00", W, 0.75), "FK95": ("Sat 24 Oct", W, 46319), "FK96": ("Oct 24", W, 46319),
    "FK97": ("-close", W), "FK98": ("?? -close", W), "FK99": ("TBC", W), "FK100": ("reminder", Y), "FK101": ("T.B.C. - Close", W),
    "FK102": ("IMPORTANT NOTE", W), "FK103": ("Important: no staff", W), "FK104": ("Saturday", W), "FK105": ("Note.", W),
    "FK106": ("Lena Drayton (TBC)", W), "FK107": ("Seema Notes", W), "FK108": ("Monday Mbeki", W), "FK109": ("Timothy Close", W),
    "FK110": ("Tobias", W), "FK111": ("Notebook Smith", W), "FK112": ("Readie Joe", W), "FK113": ("Carl  Fielding", W),
    "FK114": ("TRUE", W, True), "FK115": ("Yellow Merged", Y), "FK116": ("22:00-Close", W), "FK117": ("Late Shift Team", "#00b0f0"),
    "GF74": ("Frac Today", W), "GG74": ("Frac Yesterday", W),
    # version 4 (14 Nov): Yusuf Eklund's cell was #fbfbfb - a few shades off white is white; real other colours are not
    "FK118": ("Yusuf Eklund", "#fbfbfb"), "FK119": ("Near Green", "#93d151"), "FK120": ("Pale Yellow", "#fff2cc"),
    "FK121": ("Light Grey Two", "#d9d9d9"), "FK122": ("Near White Two", "#f3f3f3"),
    "ZA74": ("Sat27 Name", W), "ZB74": ("Sun28 Name", W), "ZC74": ("Mon29 Name", W),
}
MERGED = {"FK93", "FK115"}

# ---- the requests: dicts as the window builds them (or raw bodies), what must come back, and the writes allowed ----
def req(**q):
    return dict({"key": KEY}, **q)


def C(label, body, want, writes=(), at="2026-10-24 05:30", tz="Europe/Dublin", setup="", utc=None, fake=True):
    """One request. want: fields the answer must have (None: must NOT be there). writes: [(cell, colour)].
    at: the time in Dublin; utc: or the instant in UTC ("2027-03-28 23:30"); tz: the spreadsheet's own zone;
    setup: JScript run first (it is undone after the case); fake: compare with the stand-in too."""
    return {"label": label, "body": body, "want": want, "writes": list(writes), "at": at, "tz": tz, "setup": setup, "utc": utc,
            "fake": fake and isinstance(body, dict) and not setup and tz == "Europe/Dublin" and utc is None}


CASES = [
    # -- the key, and requests that are not requests --
    C("ping with the key: says it is version 4", req(action="ping"), {"ok": True, "sheet": "Sheet1", "version": 4}),
    C("ping with a wrong key", req(action="ping", key=KEY + "x"), {"ok": False, "why": "wrong key", "version": None}),
    C("ping with no key", {"action": "ping"}, {"ok": False, "why": "wrong key"}, fake=False),
    C("ping with an empty key", req(action="ping", key=""), {"ok": False, "why": "wrong key"}),
    C("ping with the key padded", req(action="ping", key=" " + KEY), {"ok": False, "why": "wrong key"}),
    C("the key as a list", req(action="ping", key=[KEY]), {"ok": False, "why": "wrong key"}, fake=False),
    C("green with a wrong key", req(action="green", cell="FK74", name="Lena Drayton", key="nope"), {"ok": False, "why": "wrong key"}),
    C("not JSON at all", "this is not json", {"ok": False}),
    C("an empty body", "", {"ok": False}),
    C("JSON that is a list", "[1, 2]", {"ok": False, "why": "wrong key"}),
    C("JSON that is null", "null", {"ok": False, "why": "wrong key"}),
    C("JSON that is a number", "5", {"ok": False, "why": "wrong key"}),
    C("not strict JSON (a name for the key, as a script would read it)", '{key: KEY, action: "ping"}', {"ok": False, "version": None}),
    # -- green and white on today's names --
    C("green: today's white name", req(action="green", cell="FK74", name="Lena Drayton"), {"ok": True, "before": W, "after": G}, [("FK74", G)]),
    C("green again: already green, nothing written", req(action="green", cell="FK74", name="Lena Drayton"), {"ok": True, "before": G, "after": G}),
    C("white: back to white", req(action="white", cell="FK74", name="Lena Drayton"), {"ok": True, "before": G, "after": W}, [("FK74", W)]),
    C("white again: nothing written", req(action="white", cell="FK74", name="Lena Drayton"), {"ok": True, "before": W, "after": W}),
    C("check: says what it would do, writes nothing", req(action="check", cell="FK74", name="Lena Drayton"), {"ok": True, "before": W, "after": W}),
    C("green: a yellow name (confirmed by the controller)", req(action="green", cell="FK76", name="Ann Lee"), {"ok": True, "before": Y, "after": G}, [("FK76", G)]),
    C("white: that one back to white (not yellow)", req(action="white", cell="FK76", name="Ann Lee"), {"ok": True, "before": G, "after": W}, [("FK76", W)]),
    C("white: a name already green on the roster", req(action="white", cell="FK75", name="John Smith"), {"ok": True, "before": G, "after": W}, [("FK75", W)]),
    C("white on a YELLOW name: refused - white only undoes a green", req(action="white", cell="FK92", name="Yellow To White"),
      {"ok": False, "why": "changed", "before": Y}),
    C("white on a yellow name with no colour expected: refused all the same", req(action="white", cell="FK92", name="Yellow To White", was=""),
      {"ok": False, "why": "changed", "before": Y}),
    C("check on a yellow name: allowed, writes nothing", req(action="check", cell="FK92", name="Yellow To White"), {"ok": True, "before": Y, "after": Y}),
    # -- the name --
    C("name in other letters (case)", req(action="green", cell="FK74", name="lena drayton"), {"ok": False, "why": "moved"}),
    C("only part of the name", req(action="green", cell="FK74", name="Lena"), {"ok": False, "why": "moved"}),
    C("someone else's name - and the answer says nothing of the cell", req(action="green", cell="FK74", name="Soren Rask"),
      {"ok": False, "why": "moved", "shown": None, "before": None, "day": None}),
    C("a wrong name on an orange cell says moved, not its colour", req(action="green", cell="FK77", name="Somebody Else"), {"ok": False, "why": "moved", "before": None}),
    C("no name", req(action="green", cell="FK74", name=""), {"ok": False, "why": "moved"}),
    C("name missing", req(action="green", cell="FK74"), {"ok": False, "why": "moved"}),
    C("an empty cell, with no name", req(action="green", cell="FK79", name=""), {"ok": False, "why": "moved"}),
    C("spaces in the name are not a difference", req(action="green", cell="FK81", name="Spaced Name"), {"ok": True, "after": G}, [("FK81", G)]),
    C("a name sent as the PC reads it (inner double space kept)", req(action="green", cell="FK91", name="Double  Space", was=W), {"ok": True, "after": G}, [("FK91", G)]),
    C("quotes and apostrophes in a name", req(action="green", cell="FK82", name="O'Ferreira \"Big\" Joe"), {"ok": True, "after": G}, [("FK82", G)]),
    # -- the colour the window expects --
    C("green, expecting white - but the cell is yellow now: refused as changed", req(action="green", cell="FK84", name="Still Yellow", was=W),
      {"ok": False, "why": "changed", "before": Y}),
    C("white, expecting green - but the cell is white: refused as changed", req(action="white", cell="FK74", name="Lena Drayton", was=G), {"ok": False, "why": "changed", "before": W}),
    C("green, expecting white, and it is white: done", req(action="green", cell="FK74", name="Lena Drayton", was=W), {"ok": True, "after": G}, [("FK74", G)]),
    C("white, expecting green (in capitals), and it is green: done", req(action="white", cell="FK74", name="Lena Drayton", was="#92D050"), {"ok": True, "after": W}, [("FK74", W)]),
    C("was sent empty: no colour check (the window always sends was)", req(action="green", cell="FK90", name="Was Empty", was=""), {"ok": True, "after": G}, [("FK90", G)]),
    C("a colour sent along is ignored", req(action="green", cell="FK80", name="Lena Drayton", colour="#000000", value="x"), {"ok": True, "after": G}, [("FK80", G)]),
    # -- not a name: times, dates, numbers, start times not filled in, notes, headings --
    C("a time cell, asked for by its own text", req(action="green", cell="FK69", name="06.00-18.00"), {"ok": False, "why": "not a name"}),
    C("a time cell with a capital in it ('22:00-Close')", req(action="green", cell="FK116", name="22:00-Close"), {"ok": False, "why": "not a name"}),
    C("a time kept as a number, shown as 18:00", req(action="green", cell="FK94", name="18:00"), {"ok": False, "why": "not a name"}),
    C("a date shown as 'Sat 24 Oct' (a date, not text)", req(action="green", cell="FK95", name="Sat 24 Oct"), {"ok": False, "why": "not a name"}),
    C("a date shown as 'Oct 24'", req(action="green", cell="FK96", name="Oct 24"), {"ok": False, "why": "not a name"}),
    C("TRUE (a tick box, not text)", req(action="green", cell="FK114", name="TRUE"), {"ok": False, "why": "not a name"}, fake=False),
    C("a start time not filled in: '-close'", req(action="green", cell="FK97", name="-close"), {"ok": False, "why": "not a name"}),
    C("a start time not filled in: '?? -close'", req(action="green", cell="FK98", name="?? -close"), {"ok": False, "why": "not a name"}),
    C("a start time not filled in: 'TBC'", req(action="green", cell="FK99", name="TBC"), {"ok": False, "why": "not a name"}),
    C("a start time not filled in: 'T.B.C. - Close'", req(action="green", cell="FK101", name="T.B.C. - Close"), {"ok": False, "why": "not a name"}),
    C("a note with no capital letter: 'reminder'", req(action="green", cell="FK100", name="reminder"), {"ok": False, "why": "not a name"}),
    C("a note: 'IMPORTANT NOTE' (white)", req(action="green", cell="FK102", name="IMPORTANT NOTE"), {"ok": False, "why": "not a name"}),
    C("a note: 'Important: no staff'", req(action="green", cell="FK103", name="Important: no staff"), {"ok": False, "why": "not a name"}),
    C("a note: 'Note.'", req(action="green", cell="FK105", name="Note."), {"ok": False, "why": "not a name"}),
    C("a weekday heading on a white cell lower down", req(action="green", cell="FK104", name="Saturday"), {"ok": False, "why": "not a name"}),
    C("a person: 'Lena Drayton (TBC)'", req(action="green", cell="FK106", name="Lena Drayton (TBC)"), {"ok": True, "after": G}, [("FK106", G)]),
    C("a person: 'Seema Notes'", req(action="green", cell="FK107", name="Seema Notes"), {"ok": True, "after": G}, [("FK107", G)]),
    C("a person: 'Monday Mbeki'", req(action="green", cell="FK108", name="Monday Mbeki"), {"ok": True, "after": G}, [("FK108", G)]),
    C("a person: 'Timothy Close'", req(action="green", cell="FK109", name="Timothy Close"), {"ok": True, "after": G}, [("FK109", G)]),
    C("a person: 'Tobias' (one word)", req(action="green", cell="FK110", name="Tobias"), {"ok": True, "after": G}, [("FK110", G)]),
    C("a person: 'Notebook Smith' (starts like a note word)", req(action="green", cell="FK111", name="Notebook Smith"), {"ok": True, "after": G}, [("FK111", G)]),
    C("a person: 'Readie Joe'", req(action="green", cell="FK112", name="Readie Joe"), {"ok": True, "after": G}, [("FK112", G)]),
    C("a person: 'Carl  Fielding' (two spaces on the roster)", req(action="green", cell="FK113", name="Carl  Fielding"), {"ok": True, "after": G}, [("FK113", G)]),
    # -- merged cells --
    C("a merged cell is left alone", req(action="green", cell="FK93", name="Merged Name"), {"ok": False, "why": "merged"}),
    C("...a yellow one too, even only checked", req(action="check", cell="FK115", name="Yellow Merged"), {"ok": False, "why": "merged"}),
    # -- the day --
    C("tomorrow's column", req(action="green", cell="FL74", name="Lena Drayton"), {"ok": False, "why": "not today", "day": "2026-10-25"}),
    C("next week's column (yellow)", req(action="green", cell="FS74", name="Basil Janek"), {"ok": False, "why": "not today"}),
    C("two days ago", req(action="white", cell="FI74", name="Henry Marlow"), {"ok": False, "why": "not today", "day": "2026-10-22"}),
    C("yesterday before noon (a night shift past midnight)", req(action="green", cell="FJ74", name="Soren Rask"), {"ok": True, "after": G}, [("FJ74", G)]),
    C("yesterday after noon: refused (the night is long over)", req(action="white", cell="FJ74", name="Soren Rask"), {"ok": False, "why": "not today"}, at="2026-10-24 15:30"),
    C("yesterday at 11:30: still the night shift", req(action="green", cell="FJ75", name="Night Eleven"), {"ok": True, "after": G}, [("FJ75", G)], at="2026-10-24 11:30"),
    C("yesterday at 12:00 exactly: refused", req(action="green", cell="FJ76", name="Night Noon"), {"ok": False, "why": "not today"}, at="2026-10-24 12:00"),
    C("yesterday at 12:30: refused", req(action="green", cell="FJ76", name="Night Noon"), {"ok": False, "why": "not today"}, at="2026-10-24 12:30"),
    C("yesterday at 00:30: the night shift past midnight", req(action="green", cell="FJ77", name="Night Midnight"), {"ok": True, "after": G}, [("FJ77", G)], at="2026-10-24 00:30"),
    C("yesterday's YELLOW name, white asked: refused (white only undoes a green)", req(action="white", cell="FJ78", name="Yellow Yesterday"), {"ok": False, "why": "changed"}),
    C("today after noon is still fine", req(action="green", cell="FK85", name="Afternoon Name"), {"ok": True, "after": G}, [("FK85", G)], at="2026-10-24 15:30"),
    C("today at 23:30: the evening of the night shift", req(action="green", cell="FK86", name="Late Evening"), {"ok": True, "after": G}, [("FK86", G)], at="2026-10-24 23:30"),
    C("today at 00:30 Dublin (23:30 UTC the day before)", req(action="green", cell="FK87", name="Just Past Midnight"), {"ok": True, "after": G}, [("FK87", G)], at="2026-10-24 00:30"),
    C("tomorrow at 23:30: still refused", req(action="green", cell="FL74", name="Lena Drayton"), {"ok": False, "why": "not today"}, at="2026-10-24 23:30"),
    C("a date kept as a number: today", req(action="green", cell="GA74", name="Serial Today"), {"ok": True, "after": G}, [("GA74", G)]),
    C("a date kept as a number: yesterday (before noon)", req(action="green", cell="GB74", name="Serial Yesterday"), {"ok": True, "after": G}, [("GB74", G)]),
    C("a date kept as a number: tomorrow", req(action="green", cell="GC74", name="Serial Tomorrow"), {"ok": False, "why": "not today"}),
    C("a number date with a time part (24 Oct 18:00) is still the 24th", req(action="green", cell="GF74", name="Frac Today"), {"ok": True, "after": G}, [("GF74", G)]),
    C("a number date of yesterday 14:24, at 15:30: yesterday, refused", req(action="green", cell="GG74", name="Frac Yesterday"), {"ok": False, "why": "not today", "day": "2026-10-23"},
      at="2026-10-24 15:30"),
    C("a column whose row 2 is words", req(action="green", cell="GD74", name="Text Header"), {"ok": False, "why": "not today", "day": ""}),
    C("a column with nothing in row 2", req(action="green", cell="GE74", name="No Header"), {"ok": False, "why": "not today", "day": ""}),
    # -- the spreadsheet's own time zone does not move the day (it is the control room's clock that counts) --
    C("sheet on GMT, Sat 12:30 Dublin: Friday's column is refused", req(action="check", cell="FJ74", name="Soren Rask"), {"ok": False, "why": "not today"},
      at="2026-10-24 12:30", tz="Etc/GMT"),
    C("sheet on GMT, Sat 00:30 Dublin: Saturday's column is today", req(action="check", cell="FK74", name="Lena Drayton"), {"ok": True}, at="2026-10-24 00:30", tz="Etc/GMT"),
    C("sheet on New York time, Sat 15:00 Dublin: Friday's column is refused", req(action="check", cell="FJ74", name="Soren Rask"), {"ok": False, "why": "not today"},
      at="2026-10-24 15:00", tz="America/New_York"),
    C("sheet on New York time, Sat 07:00 Dublin: Saturday's column is today", req(action="check", cell="FK74", name="Lena Drayton"), {"ok": True}, at="2026-10-24 07:00",
      tz="America/New_York"),
    C("sheet on Karachi time, Fri 21:00 Dublin: Saturday's column (tomorrow) is refused", req(action="check", cell="FK74", name="Lena Drayton"), {"ok": False, "why": "not today"},
      at="2026-10-23 21:00", tz="Asia/Karachi"),
    # -- the clocks changing --
    C("Sun 25 Oct 01:30 (after the clocks go back): Saturday's column is yesterday, before noon", req(action="check", cell="FK74", name="Lena Drayton"), {"ok": True},
      utc="2026-10-25 01:30"),
    C("Mon 29 Mar 2027 00:30 (after the clocks go forward): Sunday's column is yesterday", req(action="check", cell="ZB74", name="Sun28 Name"), {"ok": True},
      utc="2027-03-28 23:30"),
    C("...and Saturday's column (two days ago) is refused", req(action="check", cell="ZA74", name="Sat27 Name"), {"ok": False, "why": "not today"}, utc="2027-03-28 23:30"),
    C("...and Monday's column is today", req(action="check", cell="ZC74", name="Mon29 Name"), {"ok": True}, utc="2027-03-28 23:30"),
    # -- colours --
    C("v4: a near-white name (#fbfbfb) is white: it turns green", req(action="green", cell="FK118", name="Yusuf Eklund", was=W),
      {"ok": True, "before": W, "after": G}, writes=[("FK118", G)]),
    C("v4: a near-green name (#93d151) is green: it turns white", req(action="white", cell="FK119", name="Near Green", was=G),
      {"ok": True, "before": G, "after": W}, writes=[("FK119", W)]),
    C("v4: the window's expected colour may itself be a few shades off", req(action="green", cell="FK122", name="Near White Two", was="#fafafa"),
      {"ok": True, "before": W, "after": G}, writes=[("FK122", G)]),
    C("v4: pale yellow (#fff2cc) is a different colour: left alone", req(action="green", cell="FK120", name="Pale Yellow"),
      {"ok": False, "why": "coloured", "before": "#fff2cc"}),
    C("v4: light grey (#d9d9d9) is a different colour: left alone", req(action="green", cell="FK121", name="Light Grey Two"),
      {"ok": False, "why": "coloured", "before": "#d9d9d9"}),
    C("an orange name is left alone", req(action="green", cell="FK77", name="Ruben Corbett"), {"ok": False, "why": "coloured", "before": "#ff9900"}),
    C("a red name is left alone", req(action="white", cell="FK78", name="Red Name"), {"ok": False, "why": "coloured"}),
    C("a grey name is left alone", req(action="green", cell="FK83", name="Grey Name"), {"ok": False, "why": "coloured"}),
    C("a light grey name is left alone", req(action="green", cell="FK88", name="Light Grey"), {"ok": False, "why": "coloured"}),
    C("a pink name is left alone", req(action="white", cell="FK89", name="Pink Name"), {"ok": False, "why": "coloured"}),
    C("a black venue label is left alone", req(action="green", cell="FK68", name="Kilcarran"), {"ok": False, "why": "coloured"}),
    C("the repeated heading (blue) lower down is left alone", req(action="green", cell="FK66", name="Saturday"), {"ok": False}),
    C("words on the heading blue are left alone too", req(action="green", cell="FK117", name="Late Shift Team"), {"ok": False, "why": "coloured", "before": "#00b0f0"}),
    # -- the header rows: refused by the row rule itself --
    C("row 1 (blue): refused by the row rule", req(action="green", cell="FK1", name="Sat"), {"ok": False, "why": "not a cell with a name: FK1"}),
    C("row 1, a WHITE weekday: refused by the row rule", req(action="green", cell="GA1", name="Saturday"), {"ok": False, "why": "not a cell with a name: GA1"}),
    C("row 2 shown as words: refused by the row rule", req(action="green", cell="GA2", name="Sat 24 Oct"), {"ok": False, "why": "not a cell with a name: GA2"}),
    # -- not one cell: refused by the cell rule (the fake roster's getRange takes all of these, as Google's does) --
    C("a range of cells", req(action="green", cell="FK74:FK80", name="Lena Drayton"), {"ok": False, "why": "not a cell: FK74:FK80"}),
    C("a whole column", req(action="green", cell="FK:FK", name="Sat"), {"ok": False, "why": "not a cell: FK:FK"}),
    C("small letters", req(action="green", cell="fk74", name="Lena Drayton"), {"ok": False, "why": "not a cell: fk74"}),
    C("small letters in row 1", req(action="green", cell="ga1", name="Saturday"), {"ok": False, "why": "not a cell: ga1"}),
    C("R1C1 for row 1", req(action="green", cell="R1C183", name="Saturday"), {"ok": False, "why": "not a cell: R1C183"}),
    C("this tab named, row 1", req(action="green", cell="Sheet1!GA1", name="Saturday"), {"ok": False, "why": "not a cell: Sheet1!GA1"}),
    C("another tab", req(action="green", cell="Sheet2!FK74", name="Lena Drayton"), {"ok": False, "why": "not a cell: Sheet2!FK74"}),
    C("a space after the cell", req(action="green", cell="FK74 ", name="Lena Drayton"), {"ok": False, "why": "not a cell: FK74 "}),
    C("a formula", req(action="green", cell="=FK74", name="Lena Drayton"), {"ok": False, "why": "not a cell: =FK74"}),
    C("four letters", req(action="green", cell="FKKK74", name="Lena Drayton"), {"ok": False, "why": "not a cell: FKKK74"}),
    C("a row far too big", req(action="green", cell="FK1234567", name="Lena Drayton"), {"ok": False, "why": "not a cell: FK1234567"}),
    C("row 0", req(action="green", cell="FK0", name="x"), {"ok": False, "why": "not a cell: FK0"}),
    C("no cell", req(action="green", name="Lena Drayton"), {"ok": False, "why": "not a cell: undefined"}, fake=False),
    C("a cell given as a number", req(action="green", cell=74, name="Lena Drayton"), {"ok": False, "why": "not a cell: 74"}, fake=False),
    C("a cell given as a list of two", req(action="green", cell=["FK74", "FK75"], name="Lena Drayton"), {"ok": False}, fake=False),
    # -- actions --
    C("asked for yellow", req(action="yellow", cell="FK74", name="Lena Drayton"), {"ok": False, "why": "unknown action"}),
    C("asked for yellow, with a wrong name: refused before the cell is read", req(action="yellow", cell="FK74", name="Nobody"), {"ok": False, "why": "unknown action"}),
    C("asked to clear", req(action="clear", cell="FK74", name="Lena Drayton"), {"ok": False, "why": "unknown action"}),
    C("asked to write a value", req(action="setValue", cell="FK74", name="Lena Drayton", value="hacked"), {"ok": False, "why": "unknown action"}),
    C("GREEN in capitals", req(action="GREEN", cell="FK74", name="Lena Drayton"), {"ok": False, "why": "unknown action"}),
    C("no action", req(cell="FK74", name="Lena Drayton"), {"ok": False, "why": "unknown action"}, fake=False),
    C("a 'was' that cannot be read is refused, nothing written", req(action="green", cell="FK80", name="Lena Drayton", was={"toString": 1}), {"ok": False}, fake=False),
]


def js(value):
    return json.dumps(value)


MOCKS = r"""
var LOG = [], CONSOLE = [], TABS = [[0, 'Sheet1']], SHEET_TZ = 'Europe/Dublin', NOW = 0;
var CELLS = {}, HEAD = {}, MERGED = {};
var JSON = {                      // strict, as Apps Script's: json2.js's own test of what is JSON, then read it
  parse: function (s) {
    s = String(s);
    var probe = s.replace(/\\(?:["\\\/bfnrt]|u[0-9a-fA-F]{4})/g, '@')
                 .replace(/"[^"\\\n\r]*"|true|false|null|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?/g, ']')
                 .replace(/(?:^|:|,)(?:\s*\[)+/g, '');
    if (!/\S/.test(s) || !/^[\],:{}\s]*$/.test(probe)) throw new SyntaxError('Unexpected token in JSON');
    return eval('(' + s + ')');
  },
  stringify: function (o) {
    if (o === null) return 'null';
    if (typeof o === 'string') return '"' + o.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n') + '"';
    if (typeof o === 'number' || typeof o === 'boolean') return String(o);
    var parts = []; for (var k in o) { if (o.hasOwnProperty(k) && o[k] !== undefined) parts.push(JSON.stringify(k) + ':' + JSON.stringify(o[k])); }
    return '{' + parts.join(',') + '}';
  }
};
function pad(n) { return (n < 10 ? '0' : '') + n; }
function lastSunday(y, m) { var d = new Date(Date.UTC(y, m + 1, 0)); return d.getUTCDate() - d.getUTCDay(); }
function nthSunday(y, m, n) { var d = new Date(Date.UTC(y, m, 1)); return 1 + (7 - d.getUTCDay()) % 7 + 7 * (n - 1); }
function offset(tz, t) {                         // minutes east of UTC at the instant t, as Google's zones have it
  var y = new Date(t).getUTCFullYear();
  if (tz === 'UTC' || tz === 'GMT' || tz === 'Etc/GMT' || tz === 'Etc/UTC') return 0;
  if (tz === 'Asia/Karachi') return 300;
  if (tz === 'Europe/Dublin' || tz === 'Europe/London') {
    return t >= Date.UTC(y, 2, lastSunday(y, 2), 1) && t < Date.UTC(y, 9, lastSunday(y, 9), 1) ? 60 : 0;
  }
  if (tz === 'America/New_York') {
    return t >= Date.UTC(y, 2, nthSunday(y, 2, 2), 7) && t < Date.UTC(y, 10, nthSunday(y, 10, 1), 6) ? -240 : -300;
  }
  throw new Error('time zone not known to the fake: ' + tz);
}
var Utilities = { formatDate: function (d, tz, fmt) {
  var l = new Date(d.getTime() + offset(tz, d.getTime()) * 60000);
  if (fmt === 'H') return String(l.getUTCHours());
  if (fmt !== 'yyyy-MM-dd') throw new Error('format ' + fmt);
  return l.getUTCFullYear() + '-' + pad(l.getUTCMonth() + 1) + '-' + pad(l.getUTCDate()); } };
function midnightIn(tz, y, m, d) {               // a date cell, as Google hands it over: midnight in the spreadsheet's zone
  var guess = Date.UTC(y, m, d);
  return new Date(guess - offset(tz, guess - offset(tz, guess) * 60000) * 60000);
}
function colNum(s) { s = s.toUpperCase(); var n = 0; for (var i = 0; i < s.length; i++) n = n * 26 + s.charCodeAt(i) - 64; return n; }
function letters(n) { var s = ''; while (n > 0) { var m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); } return s; }
function Range(tab, cells) { this.tab = tab; this.cells = cells; this.row = cells[0][0]; this.col = cells[0][1]; this.ref = this.refOf(cells[0]); }
Range.prototype.refOf = function (rc) { return (this.tab === 'Sheet1' ? '' : this.tab + '!') + letters(rc[1]) + rc[0]; };
function parseA1(tab, a) {                        // what Google's getRange takes: A1, ranges, whole columns, R1C1, another tab
  var s = String(a), m = /^(?:'?([^'!]+)'?!)?([\s\S]*)$/.exec(s), cells = [], r, c;
  if (m[1]) tab = m[1];
  s = m[2];
  if ((m = /^R([0-9]+)C([0-9]+)$/i.exec(s))) { cells.push([Number(m[1]), Number(m[2])]); }
  else if ((m = /^([A-Za-z]{1,3})([0-9]+)(?::([A-Za-z]{1,3})([0-9]+))?$/.exec(s))) {
    var r1 = Number(m[2]), c1 = colNum(m[1]), r2 = m[4] ? Number(m[4]) : r1, c2 = m[3] ? colNum(m[3]) : c1;
    for (r = r1; r <= r2; r++) for (c = c1; c <= c2; c++) cells.push([r, c]);
  } else if ((m = /^([A-Za-z]{1,3}):([A-Za-z]{1,3})$/.exec(s))) {
    for (r = 1; r <= 130; r++) for (c = colNum(m[1]); c <= colNum(m[2]); c++) cells.push([r, c]);
  } else throw new Error('Range not found: ' + a);
  for (var i = 0; i < cells.length; i++) if (cells[i][0] < 1 || cells[i][0] > 1000) throw new Error('Range out of the grid: ' + a);
  return new Range(tab, cells);
}
Range.prototype.getDisplayValue = function () { return CELLS[this.ref] ? CELLS[this.ref].text : ''; };
Range.prototype.getValue = function () {
  if (this.row === 2 && this.tab === 'Sheet1') {
    var h = HEAD[letters(this.col)];
    if (!h) return '';
    return h[0] === 'date' ? midnightIn(SHEET_TZ, h[1], h[2], h[3]) : h[1];
  }
  var cell = CELLS[this.ref];
  return !cell ? '' : cell.value !== undefined ? cell.value : cell.text; };
Range.prototype.getBackground = function () { return CELLS[this.ref] ? CELLS[this.ref].bg : '#ffffff'; };
Range.prototype.setBackground = function (c) {
  for (var i = 0; i < this.cells.length; i++) { var ref = this.refOf(this.cells[i]);
    LOG.push('bg ' + ref + ' ' + c); if (!CELLS[ref]) CELLS[ref] = {text: '', bg: '#ffffff'}; CELLS[ref].bg = c; }
  return this; };
Range.prototype.isPartOfMerge = function () { for (var i = 0; i < this.cells.length; i++) if (MERGED[this.refOf(this.cells[i])]) return true; return false; };
Range.prototype.getColumn = function () { return this.col; };
Range.prototype.getRow = function () { return this.row; };
var TRAPS = ['setValue', 'setValues', 'setFormula', 'clear', 'clearContent', 'clearFormat', 'setFontColor', 'setFontLine', 'setNote',
             'setBackgrounds', 'setBackgroundRGB', 'setBackgroundObject', 'merge', 'breakApart', 'deleteCells', 'insertCells'];
for (var t = 0; t < TRAPS.length; t++) (function (name) { Range.prototype[name] = function () { LOG.push('WRITE ' + name + ' ' + this.ref); return this; }; })(TRAPS[t]);
function Tab(id, name) { this.id = id; this.name = name; }
Tab.prototype.getSheetId = function () { return this.id; };
Tab.prototype.getName = function () { return this.name; };
Tab.prototype.getParent = function () { return SS; };
Tab.prototype.getRange = function (a, b) { if (b !== undefined) return new Range(this.name, [[a, b]]); return parseA1(this.name, a); };
var SS = { getSheets: function () { var out = []; for (var i = 0; i < TABS.length; i++) out.push(new Tab(TABS[i][0], TABS[i][1])); return out; },
           getSpreadsheetTimeZone: function () { return SHEET_TZ; } };
var SpreadsheetApp = { getActiveSpreadsheet: function () { return SS; } };
var console = { log: function (s) { CONSOLE.push(String(s)); } };
var ContentService = { MimeType: {JSON: 'application/json'}, createTextOutput: function (t) {
  return { text: t, mime: '', setMimeType: function (m) { this.mime = m; return this; } }; } };
"""


def harness(script, cases, extra=""):
    """A JScript file: the fake roster, the script, and every case; each prints one line."""
    lines = [MOCKS]
    for col, (kind, v) in HEAD.items():
        if kind == "date":
            lines.append("HEAD[%s] = ['date', %d, %d, %d];" % (js(col), v.year, v.month - 1, v.day))
        else:
            lines.append("HEAD[%s] = [%s, %s];" % (js(col), js(kind), js(v)))
    for ref, cell in CELLS.items():
        value = "" if len(cell) < 3 else ", value: %s" % js(cell[2])
        lines.append("CELLS[%s] = {text: %s, bg: %s%s};" % (js(ref), js(cell[0]), js(cell[1]), value))
    for ref in MERGED:
        lines.append("MERGED[%s] = 1;" % js(ref))
    lines.append(script)
    lines.append("function nowDate() { return new Date(NOW); }")
    lines.append(extra)
    lines.append(r"""
function run(i, body, tz, now, setup) {
  SHEET_TZ = tz; NOW = now; TABS = [[0, 'Sheet1']];
  if (setup) eval(setup);
  var before = LOG.length, said = CONSOLE.length, reply, answer;
  try { reply = doPost({postData: {contents: body}}); answer = reply.text; } catch (e) { answer = 'THREW ' + e.message; }
  WScript.Echo('CASE\t' + i + '\t' + answer + '\t' + (reply ? reply.mime : '') + '\t' + LOG.slice(before).join('|') + '\t' +
               CONSOLE.slice(said).join('|'));
}
""")
    for i, c in enumerate(cases):
        body = c["body"] if isinstance(c["body"], str) else json.dumps(c["body"])
        now = datetime.datetime.strptime(c["utc"], "%Y-%m-%d %H:%M") if c["utc"] else utc_of(c["at"])
        stamp = int((now - datetime.datetime(1970, 1, 1)).total_seconds() * 1000)
        lines.append("run(%d, %s, %s, %d, %s);" % (i, js(body), js(c["tz"]), stamp, js(c["setup"])))
    lines.append(r"""
var r1, r2, r3;
try { r1 = doPost({}).text; } catch (e) { r1 = 'THREW ' + e.message; }
try { r2 = doPost(undefined).text; } catch (e) { r2 = 'THREW ' + e.message; }
try { r3 = doPost({postData: null}).text; } catch (e) { r3 = 'THREW ' + e.message; }
WScript.Echo('BARE\t' + r1 + '\t' + r2 + '\t' + r3);
WScript.Echo('ALL\t' + LOG.join('|') + '\t' + CONSOLE.length);
""")
    return "\n".join(lines)


def run_js(name, text):
    path = os.path.join(WORK, name + ".js")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    proc = subprocess.run(["cscript.exe", "//nologo", "//E:jscript", "//T:90", path], capture_output=True, text=True, timeout=150,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc, proc.stdout.splitlines()


def answers(out):
    got = {}
    for l in out:
        if l.startswith("CASE\t"):
            p = l.split("\t")
            got[int(p[1])] = {"answer": p[2], "mime": p[3], "writes": p[4], "said": p[5] if len(p) > 5 else ""}
    return got


def parsed(text):
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else {"unreadable": text}
    except ValueError:
        return {"unreadable": text}


# ==== 1. version 4, the script to be put in ====
SCRIPT = rostercolour.SCRIPT % {"key": KEY}
proc, out = run_js("v3", harness(SCRIPT, CASES))
check("version 4 runs under the script host without a syntax error", proc.returncode == 0 and any(l.startswith("ALL") for l in out),
      (proc.returncode, proc.stderr[-400:], out[-3:]))
got = answers(out)
for i, c in enumerate(CASES):
    g = got.get(i, {"answer": "(no answer)", "mime": "", "writes": "", "said": ""})
    reply = parsed(g["answer"])
    want_log = "|".join("bg %s %s" % w for w in c["writes"])
    ok = (all(reply.get(k) == v if v is not None else k not in reply for k, v in c["want"].items()) and g["writes"] == want_log
          and g["mime"] == "application/json" and len([s for s in g["said"].split("|") if s]) == len(c["writes"]))
    check(c["label"], ok, "%s writes=%r log=%r" % (g["answer"], g["writes"], g["said"]))
bare = next((l.split("\t") for l in out if l.startswith("BARE\t")), [""] * 4)
check("a call with no body at all is refused, never thrown", all(x.startswith("{") and '"ok":false' in x for x in bare[1:4]), bare[1:4])
everything = next((l.split("\t") for l in out if l.startswith("ALL\t")), ["", "", "?"])
writes = [w for w in everything[1].split("|") if w]
allowed = {ref for c in CASES for ref, _ in c["writes"]}
check("over all of it: no text written, nothing cleared, every fill green or white",
      writes and all(w.startswith("bg ") and w.split(" ")[2] in (G, W) for w in writes), [w for w in writes if not w.startswith("bg ") or w.split(" ")[2] not in (G, W)])
check("...only cells that were asked for and allowed", {w.split(" ")[1] for w in writes} == allowed, sorted({w.split(" ")[1] for w in writes} ^ allowed))
check("...and every change (and only a change) is listed in the script's log (Apps Script > Executions)", everything[2] == str(len(writes)),
      everything[2:])
check("the key in the script is the one given, and appears once", SCRIPT.count(KEY) == 1)
check("the script uses no service it does not need (no mail, no web, no Drive, no other spreadsheet)",
      not any(w in rostercolour.SCRIPT for w in ("MailApp", "GmailApp", "UrlFetchApp", "DriveApp", "openById", "openByUrl", "eval(", "Function(")))

# ==== 2. the stand-in answers exactly as version 4 does ====
model = fakescript.Script(TODAY, KEY, version=4)
for ref, cell in CELLS.items():
    model.text[ref], model.fill[ref] = cell[0], cell[1]
    if len(cell) > 2:
        model.numbers.add(ref)
model.merged = set(MERGED)
for col, (kind, v) in HEAD.items():
    model.day[col] = v if kind == "date" else (datetime.date(1899, 12, 30) + datetime.timedelta(days=int(v)) if kind == "serial" else None)
differ = []
compared = 0
for i, c in enumerate(CASES):
    if not c["fake"]:
        continue
    local = datetime.datetime.strptime(c["at"], "%Y-%m-%d %H:%M")
    model.today, model.hour = local.date(), local.hour
    b = c["body"]
    mine = model.answer(b.get("key"), b.get("action"), b.get("cell", ""), b.get("name", ""), b.get("was", ""))
    real = parsed(got.get(i, {}).get("answer", ""))
    compared += 1
    if mine != real:
        differ.append((c["label"], real, mine))
check("the stand-in the window's checks use answers %d requests exactly as version 4 does" % compared, compared > 80 and not differ, differ[:3])

# ==== 3. version 1 (on the roster now) and its stand-in ====
v1 = open(os.path.join(HERE, "roster-script-v1.gs"), encoding="utf-8").read()
V1 = v1.replace("  var now = new Date();\n", "  var now = nowDate();\n") % {"key": KEY}
v1_cases = [c for c in CASES if c["fake"]]
proc1, out1 = run_js("v1", harness(V1, v1_cases))
got1 = answers(out1)
check("version 1 (the one on the roster now) runs under the script host", proc1.returncode == 0 and len(got1) == len(v1_cases), (proc1.returncode, proc1.stderr[-300:]))
old = fakescript.Script(TODAY, KEY, version=1)
old.text, old.fill, old.numbers, old.day = dict(model.text), {ref: cell[1] for ref, cell in CELLS.items()}, set(model.numbers), dict(model.day)
old.serials = {col: v for col, (kind, v) in HEAD.items() if kind == "serial"}
differ1 = []
for i, c in enumerate(v1_cases):
    local = datetime.datetime.strptime(c["at"], "%Y-%m-%d %H:%M")
    old.today, old.hour = local.date(), local.hour
    b = c["body"]
    mine = old.answer(b.get("key"), b.get("action"), b.get("cell", ""), b.get("name", ""), b.get("was", ""))
    real = parsed(got1.get(i, {}).get("answer", ""))
    if mine != real:
        differ1.append((c["label"], real, mine))
check("the stand-in for version 1 answers %d requests exactly as version 1 does" % len(v1_cases), not differ1, differ1[:3])
lax = {c["label"]: parsed(got1[i]["answer"]) for i, c in enumerate(v1_cases) if i in got1}
check("(what version 1 still allows that version 3 does not: yesterday after noon, a time not filled in, white on yellow)",
      lax["yesterday after noon: refused (the night is long over)"].get("ok") and lax["a start time not filled in: 'TBC'"].get("ok")
      and lax["white on a YELLOW name: refused - white only undoes a green"].get("ok"), "")

# ==== 4. not-a-name: none of the people on the pinned roster, all of its notes and headings ====
import calllog  # noqa: E402
roster = calllog.Roster(os.path.join(HERE, "roster-2026-10-19.xlsx"))
people = set()
for serial_ in list(roster._date_cols):
    date = calllog.EPOCH + datetime.timedelta(days=int(serial_))
    try:
        _, shifts, _ = roster.shifts(date)
    except calllog.Problem:
        continue
    people.update(s["name"] for s in shifts)
notes = ["-close", "?? -close", "TBC", "tbc", "T.B.C", "T.B.C. - Close", "reminder", "IMPORTANT NOTE", "Important reminder", "Reminder 5pm",
         "NOTE: no staff", "Notes", "Cancelled", "VACANT", "Closed", "See comment", "Please call", "FYI", "Sat", "Saturday", "MONDAY", "cancelled",
         "close", "Time", "???"]
probe = rostercolour.SCRIPT % {"key": KEY}
lines = [MOCKS, probe, "var P = %s, N = %s, out = [];" % (js(sorted(people)), js(notes)),
         "for (var i = 0; i < P.length; i++) if (notAName(tidy(P[i]), P[i])) out.push('PERSON ' + P[i]);",
         "for (var j = 0; j < N.length; j++) if (!notAName(tidy(N[j]), N[j])) out.push('NOTE ' + N[j]);",
         "WScript.Echo('NAMES\\t' + P.length + '\\t' + out.join('|'));"]
proc2, out2 = run_js("names", "\n".join(lines))
row = next((l.split("\t") for l in out2 if l.startswith("NAMES\t")), ["", "0", "?"])
wrong = [w for w in row[2].split("|") if w] if len(row) > 2 else ["(no answer)"]
check("the not-a-name rules refuse none of the %d people on the pinned roster, and every one of %d notes and headings" % (len(people), len(notes)),
      len(people) > 100 and not wrong and proc2.returncode == 0, (row[1], wrong[:6], proc2.stderr[-200:]))
check("...the stand-in's rules agree, name for name", all(not fakescript.not_a_name(fakescript.tidy(p)) for p in people)
      and all(fakescript.not_a_name(fakescript.tidy(n)) for n in notes))
import itertools  # noqa: E402
import re  # noqa: E402


def spelled(pattern):
    """Every word one alternative of the script's NOTE matches: 'notes?' -> note, notes; 'tb[acd]' -> tba, tbc, tbd."""
    parts = re.findall(r"\\.|\[[^\]]*\]|.\??", pattern)
    options = []
    for p in parts:
        if p.startswith("\\"):
            options.append([p[1]])
        elif p.startswith("["):
            options.append(list(p[1:-1]))
        elif p.endswith("?"):
            options.append([p[0], ""])
        else:
            options.append([p])
    return ["".join(w) for w in itertools.product(*options)]


words = re.search(r"var NOTE = /\^\[[^\]]*\]\*\(([^)]*)\)", rostercolour.SCRIPT).group(1).split("|")
spoken = sorted({w for alt in words for w in spelled(alt)})
check("every note word in the script (%d) is one the PC's roster reader knows - so it can never refuse a name the window sends" % len(spoken),
      len(spoken) > 40 and all(w in calllog.NOTE_WORDS for w in spoken), [w for w in spoken if w not in calllog.NOTE_WORDS])
mirror = re.search(r"\]\*\(\?:([^)]*)\)", fakescript.NOTE.pattern).group(1).split("|")
check("...and the stand-in has the very same list", mirror == words, sorted(set(mirror) ^ set(words)))

shutil.rmtree(WORK, ignore_errors=True)
print("\n%d of %d checks passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
