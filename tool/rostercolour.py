"""Turn a person's name green on the roster when the controller has confirmed them (asked for on 2026-10-23), or back.

The roster stays read-only in every other way: this can change the FILL of ONE name cell, to green (92D050, the
colour the roster already uses for "confirmed") or back to white (not confirmed yet) - never any text, never any
other colour, and only on today's column (or yesterday's, for a night shift past midnight): the user's rules. It does so through a
small Google Apps Script that someone with edit access pastes into the roster once (SCRIPT, written out with a
how-to by write_setup). Google lets no program write to a sheet through a shared link, so the script is the one way
in that needs no password kept on this PC. The script itself refuses anything else: a wrong key, a cell that is not
a cell, a cell that no longer shows the name asked for or is not a name at all (a time, a date, a note, a heading),
a cell under any other day than today or yesterday before noon (on the control room's clock), any colour but white,
yellow or green, a merged cell, a cell that is not the colour the window expects, and white on a yellow.
The roster ran the first version from 2026-10-23 (tests\\roster-script-v1.gs); version 3 (2026-10-25) is the one
to put in - a ping says which one the roster runs (SCRIPT_VERSION).

Nothing here runs unless the controller presses the Confirmed box (or Ctrl+G) in the window; each press is one
HTTPS request, sent from a worker thread, with a time limit.
"""
import json
import os
import re
import secrets
import urllib.request

import calllog

GREEN = "#92d050"                                     # Excel's "Light Green": what the roster's confirmed names are filled with
WHITE = "#ffffff"                                     # ...and what today's names are until they are confirmed
CONFIG = os.environ.get("CCL_ROSTER_LINK") or os.path.join(calllog.CACHE, "roster-link.json")
SETUP = os.path.join(calllog.ROOT, "Roster script")  # the script to paste and how to switch it on
URL_RE = re.compile(r"^https://script\.google\.com/(?:a/macros/[\w.-]+/s|macros/s)/[\w-]+/exec$")       # (a/macros/<domain>/s: a Workspace account)
TIMEOUT = 30                                          # seconds one request may take


def is_green(fill):
    """A fill as the roster copy gives it ('FF92D050', 'theme0', None) or as the script gives it ('#92d050')."""
    return bool(fill) and str(fill).lower().lstrip("#")[-6:] == GREEN[1:]


def css(fill):
    """The roster copy's fill as the script sees colours: 'FFFF9900' -> '#ff9900'; none or theme0 (white) -> '#ffffff';
    any other theme colour -> '' (unknown)."""
    if fill in (None, "", "theme0"):
        return WHITE
    m = re.match(r"^#?(?:[0-9a-fA-F]{2})?([0-9a-fA-F]{6})$", str(fill))
    return "#" + m.group(1).lower() if m else ""


def changeable(fill):
    """Only a white, yellow or green name may be turned green or white; any other colour means something to whoever
    set it (orange, red, grey, a black venue label) and is left alone - the script refuses those too."""
    return css(fill) in (WHITE, "#ffff00", GREEN)


def load():
    """{"url": the web app link or "", "key": the shared key} - the key is made on first use and kept. A file that is
    there but cannot be read is never written over (its key is the one in the roster's script): calllog.Problem."""
    try:
        with open(CONFIG, encoding="utf-8-sig") as f:         # (-sig: a copy saved by PowerShell starts with a BOM)
            link = json.load(f)
    except FileNotFoundError:
        link = {"url": "", "key": secrets.token_urlsafe(24)}
        save(link)
        return link
    except (OSError, ValueError) as e:
        raise calllog.Problem("the roster link file (%s) could not be read: %s" % (os.path.basename(CONFIG), e))
    if not (isinstance(link, dict) and isinstance(link.get("key"), str) and len(link["key"]) >= 24):
        raise calllog.Problem("the roster link file (%s) has no key in it" % os.path.basename(CONFIG))
    return {"url": link.get("url") if isinstance(link.get("url"), str) else "", "key": link["key"]}


def save(link):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    part = CONFIG + ".part"
    with open(part, "w", encoding="utf-8") as f:
        json.dump(link, f, indent=1)
    os.replace(part, CONFIG)


def send(link, action, cell="", name="", was=""):
    """Ask the roster's script to do one thing: 'ping', 'green' or 'white'. was: the colour this window expects the
    cell to have now - the script refuses ('changed') if it has another (a yellow made since the copy was read).
    Returns its answer (a dict with ok, and before/after or why); raises calllog.Problem if it cannot be reached."""
    if not URL_RE.match(link.get("url", "")):
        raise calllog.Problem("turning names green on the roster is not switched on yet")
    body = json.dumps({"key": link["key"], "action": action, "cell": cell, "name": name, "was": was}).encode("utf-8")
    req = urllib.request.Request(link["url"], data=body, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:     # (Google answers with a redirect; urllib follows it)
            text = r.read(100000).decode("utf-8", "replace")
    except Exception as e:                            # no network, a link that is wrong, Google busy
        raise calllog.Problem("the roster could not be reached (%s)" % e)
    try:
        answer = json.loads(text)
    except ValueError:
        if "<html" in text.lower():                   # a sign-in page or Google's error page, not the script
            raise calllog.Problem("the link did not reach the script - in Apps Script, 'Who has access' must be 'Anyone'")
        raise calllog.Problem("the roster's script gave an answer that could not be read")
    if not isinstance(answer, dict):
        raise calllog.Problem("the roster's script gave an answer that could not be read")
    return answer


# ---- the script for the roster, and how to switch it on ----
SCRIPT_VERSION = 3                                    # what a ping of the script in the roster says it is (the first had none)
SCRIPT = r"""// NORTHWIND Security - Check Call Log: turns ONE name cell on this roster green (92D050) when the
// controller has confirmed that person, or back to white. It can do nothing else. It never writes any text, and it
// only colours a cell that:
//  - is one cell, in row 3 or below, on the roster's first tab;
//  - still shows the very name the window asked about, and is a name - not a time, a date, a note or a heading;
//  - is under today's date, or yesterday's before noon (a night shift past midnight), by the control room's clock;
//  - is white, yellow or green, is not merged with other cells, and is still the colour the window expects.
// "White" only undoes a green: a yellow (not confirmed yet) is never made white. Each change is listed under
// Apps Script > Executions. Version 3, 2026-10-25.
// How to put it in: see "How to switch it on.txt" next to this file on the control-room PC.
/** @OnlyCurrentDoc */                   // Google's permission then covers this roster only, no other spreadsheet
var VERSION = 3;
var KEY = '%(key)s';
var GREEN = '#92d050';
var WHITE = '#ffffff';
var YELLOW = '#ffff00';
var MAY_CHANGE = {'#ffffff': 1, '#ffff00': 1, '#92d050': 1};    // white, yellow, green: any other colour is left alone
var ZONE = 'Europe/Dublin';              // the control room's clock - whatever the spreadsheet's own setting says
// Never a person: a start time not filled in yet ('TBC', '-close', '?? -close'); a note to the roster's readers
// (it starts with a note word, as the PC's own roster reader knows them); a weekday heading.
var HOLE = /^[\s?.\-\u2013\u2014\/()]*((t\.?b\.?[acd]\.?|close|time)[\s?.\-\u2013\u2014\/()]*)+$/i;
var NOTE = /^[\s!-\/:-@\[-`{-~]*(important|notes?|reminders?|remind|remember|notice|attention|urgent|warning|please|pls|fyi|asap|tb[acd]|t\.b\.[acd]|cancel|cancell?ed|info|updated?|updates|comments?|message|msg|see|read|check|closed|never|forget|attn|pto|vacant|unconfirmed|provisional|postponed)\.*(?![A-Za-z0-9_.'\u2019]|[^\x00-\x7f])/i;
var WEEKDAY = /^(mon|tues?|wed|thu|thurs?|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\.?$/i;

function nowDate() {                     // (one place for the time now)
  return new Date();
}

function tidy(text) {                    // runs of spaces made one, none at either end
  return String(text).replace(/\s+/g, ' ').replace(/^ | $/g, '');
}

function notAName(shown, value) {        // a time, a date, a number, a note, a heading: never a person
  return typeof value !== 'string' || /^[0-9]/.test(shown) || shown === shown.toLowerCase() ||
         HOLE.test(shown) || NOTE.test(shown) || WEEKDAY.test(shown);
}

function dayOf(sheet, column) {          // the date in row 2 of a column, as yyyy-MM-dd ('' if there is none)
  var v = sheet.getRange(2, column).getValue();
  if (typeof v === 'number' && v > 30000 && v < 80000) {          // a date kept as a plain number: days since 1899-12-30
    return Utilities.formatDate(new Date(Date.UTC(1899, 11, 30) + Math.floor(v) * 86400000), 'UTC', 'yyyy-MM-dd');
  }
  if (v instanceof Date) return Utilities.formatDate(v, sheet.getParent().getSpreadsheetTimeZone(), 'yyyy-MM-dd');
  return '';
}

function todayOrYesterday(day) {         // today's column; yesterday's only until noon (a night shift past midnight)
  var now = nowDate();
  var today = Utilities.formatDate(now, ZONE, 'yyyy-MM-dd');
  if (day === today) return true;
  var p = today.split('-');              // yesterday as a day on the calendar, not 24 hours ago (one day in March has 23)
  var yesterday = Utilities.formatDate(new Date(Date.UTC(Number(p[0]), Number(p[1]) - 1, Number(p[2]) - 1)), 'UTC', 'yyyy-MM-dd');
  return day === yesterday && Number(Utilities.formatDate(now, ZONE, 'H')) < 12;
}

function doPost(e) {
  var out;
  try {
    var q = JSON.parse(e.postData.contents);
    if (!q || q.key !== KEY) throw new Error('wrong key');
    var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
    if (q.action === 'ping') {
      out = {ok: true, sheet: sheet.getName(), version: VERSION};
    } else {
      var m = /^([A-Z]{1,3})([1-9][0-9]{0,5})$/.exec(String(q.cell));
      if (!m) throw new Error('not a cell: ' + q.cell);
      if (!(Number(m[2]) >= 3)) throw new Error('not a cell with a name: ' + q.cell);
      if (q.action !== 'green' && q.action !== 'white' && q.action !== 'check') throw new Error('unknown action');
      var cell = sheet.getRange(m[0]);
      var shown = tidy(cell.getDisplayValue());
      var want = tidy(q.name || '');
      if (!want || shown !== want) {
        out = {ok: false, why: 'moved'};                 // (it says nothing about what the cell holds)
      } else if (notAName(shown, cell.getValue())) {
        out = {ok: false, why: 'not a name'};
      } else {
        var day = dayOf(sheet, cell.getColumn());
        var before = String(cell.getBackground()).toLowerCase();
        var to = q.action === 'white' ? WHITE : GREEN;
        if (!todayOrYesterday(day)) {
          out = {ok: false, why: 'not today', day: day};
        } else if (!MAY_CHANGE.hasOwnProperty(before)) {
          out = {ok: false, why: 'coloured', before: before};
        } else if (cell.isPartOfMerge()) {
          out = {ok: false, why: 'merged'};
        } else if (q.was && before !== String(q.was).toLowerCase()) {    // not the colour the window expects: changed since
          out = {ok: false, why: 'changed', before: before};
        } else if (q.action === 'white' && before === YELLOW) {          // white only undoes a green
          out = {ok: false, why: 'changed', before: before};
        } else if (q.action === 'check' || before === to) {
          out = {ok: true, before: before, after: q.action === 'check' ? before : to};
        } else {
          cell.setBackground(to);
          console.log('Check Call Log: ' + m[0] + ' ' + shown + ' ' + before + ' -> ' + to);
          out = {ok: true, before: before, after: to};
        }
      }
    }
  } catch (err) {
    out = {ok: false, why: String((err && err.message) || err)};
  }
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}
"""

HOW_TO = """How to switch on "turn names green on the roster"
====================================================

Once only, about five minutes, by someone who can EDIT the roster (signed in to Google in the browser).
Nothing is installed on this PC. The script you paste can only turn a name's box green or back - it never
changes any text on the roster.

1. Open the roster in the browser (the Google Sheet the control room uses).

2. In its menu: Extensions > Apps Script. A new tab opens with a file called Code.gs.

3. Open "Roster script.gs" (in this folder) with Notepad, select everything (Ctrl+A), copy (Ctrl+C).
   In Apps Script, select everything in Code.gs, paste over it (Ctrl+V), and click the Save icon.

4. Click Deploy (top right) > New deployment.
   - Click the gear beside "Select type" and choose "Web app".
   - Description: Check call log green
   - Execute as: Me
   - Who has access: Anyone
   - Click Deploy.

5. Google asks for permission: Authorize access > choose your account.
   If it says "Google hasn't verified this app": click Advanced > "Go to ... (unsafe)" > Allow.
   (That warning is shown for every script a person writes for themselves; this one is yours.)

6. Copy the "Web app URL" it shows (it ends in /exec).

7. In the Check Call Log window, click any Confirmed box (or press Ctrl+G). It asks for the link: paste it
   (Ctrl+V) and press Save. The window tests the link at once and says whether the roster answered.

After that: a click on a person's Confirmed box turns their name green on the roster; a second click turns it
back to white. Only today's names can be changed (the shift on now, past midnight too) - never tomorrow's or
later, never an older day; the script in the roster checks that as well. If the roster has changed under it
(rows added), the window says so and reads the roster again.


To put in a NEWER version of the script (the link stays the same)
------------------------------------------------------------------
1. Open "Roster script.gs" (this folder) with Notepad: Ctrl+A, Ctrl+C.
2. In the roster: Extensions > Apps Script. In Code.gs: Ctrl+A, Ctrl+V, then the Save icon.
3. Deploy > Manage deployments > the pencil (Edit) > Version: "New version" > Deploy.
   (NOT "New deployment" - that makes a new link, and the window would go on using the old one.)
   If Google asks for permission again, allow it as the first time.
4. Close the Check Call Log window and open it again. A few seconds after it opens, the line under the table
   says "The roster's script is up to date (version 3)". If it says the roster still runs an older version,
   step 3 did not happen: do it again.


If the key may have got out
---------------------------
(This folder's "Roster script.gs" or tool\\cache\\roster-link.json copied off this PC.) Put in a NEW key. The old
key stops working the moment the new version is deployed, and the link stays the same.
1. Close the Check Call Log window.
2. Delete tool\\cache\\roster-link.json (it holds the link and the old key; you paste the link again in step 5).
3. Open the window and click any Confirmed box (or press Ctrl+G). It writes a new "Roster script.gs" with a new
   key in this folder and asks for the link. Leave that box open.
4. Put the new script in as for a newer version above (steps 1-3).
5. Copy the Web app URL shown under Manage deployments (the same /exec link as before), paste it into the
   window's box and press Save and test.
Do not Archive first: an archived deployment cannot be given a new version, and its link is gone for good.


To switch it off at any time: in Apps Script, Deploy > Manage deployments > Archive.
To switch it on again after that: Deploy > New deployment gives a NEW link. The window only asks for a link when
it has none, so do steps 1, 2, 3 and 5 of "If the key may have got out", pasting the new link.
"""


def write_setup(link):
    """Write the script (with this PC's key in it) and the how-to into the "Roster script" folder."""
    os.makedirs(SETUP, exist_ok=True)
    with open(os.path.join(SETUP, "Roster script.gs"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(SCRIPT % {"key": link["key"]})
    with open(os.path.join(SETUP, "How to switch it on.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(HOW_TO)
    return SETUP
