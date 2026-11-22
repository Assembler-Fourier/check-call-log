"""Are the script's checks strong enough? Break the roster's script (rostercolour.SCRIPT) on purpose, one small way at a
time - drop a rule, loosen one, swap one - and run t_script.py on each broken copy: every break must be caught (some
check fails). A break that passes every check is a gap in the checks. Breaks that change nothing that can be seen
are listed in SAME, with why. Each break runs in its own scratch folder (its own copy of the tool, its own %TEMP%), a
few at a time; nothing here touches the real roster or the tool's own files."""
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
BASE = os.path.join(tempfile.gettempdir(), "ccl-tests", "mutate-%d" % os.getpid())
shutil.rmtree(BASE, ignore_errors=True)
os.makedirs(BASE)
with open(os.path.join(TOOL, "rostercolour.py"), encoding="utf-8") as f:
    SOURCE = f.read()

BREAKS = {
    # the key and the request
    "no key check": [("    if (!q || q.key !== KEY) throw new Error('wrong key');\n", "")],
    "key: any start of it will do": [("q.key !== KEY", "KEY.indexOf(String(q.key)) !== 0")],
    "the ping says no version": [("out = {ok: true, sheet: sheet.getName(), version: VERSION};", "out = {ok: true, sheet: sheet.getName()};")],
    # the cell
    "no cell rule": [("      if (!m) throw new Error('not a cell: ' + q.cell);\n", "      if (!m) m = [String(q.cell), '', String(q.cell).replace(/^[^0-9]*/, '')];\n")],
    "cell rule without its end": [("[1-9][0-9]{0,5})$/.exec", "[1-9][0-9]{0,5})/.exec")],
    "cell rule without its start": [("/^([A-Z]{1,3})([1-9]", "/([A-Z]{1,3})([1-9]")],
    "cell rule in any case": [("[1-9][0-9]{0,5})$/.exec", "[1-9][0-9]{0,5})$/i.exec")],
    "no row rule": [("      if (!(Number(m[2]) >= 3)) throw new Error('not a cell with a name: ' + q.cell);\n", "")],
    "row rule from row 2": [("Number(m[2]) >= 3", "Number(m[2]) >= 2")],
    "the cell as sent, not as read": [("sheet.getRange(m[0])", "sheet.getRange(String(q.cell))")],
    # the action
    "no action rule": [("      if (q.action !== 'green' && q.action !== 'white' && q.action !== 'check') throw new Error('unknown action');\n", "")],
    "white makes green": [("var to = q.action === 'white' ? WHITE : GREEN;", "var to = GREEN;")],
    "any other action makes white": [("var to = q.action === 'white' ? WHITE : GREEN;", "var to = q.action === 'green' ? GREEN : WHITE;"),
                                     ("      if (q.action !== 'green' && q.action !== 'white' && q.action !== 'check') throw new Error('unknown action');\n", "")],
    "check writes": [("} else if (q.action === 'check' || before === to) {", "} else if (before === to) {")],
    "writes when nothing changes": [("} else if (q.action === 'check' || before === to) {", "} else if (q.action === 'check') {")],
    # the name
    "names in any case": [("if (!want || shown !== want) {", "if (!want || shown.toLowerCase() !== want.toLowerCase()) {")],
    "the start of a name will do": [("if (!want || shown !== want) {", "if (!want || shown.indexOf(want) !== 0) {")],
    "an empty name will do": [("if (!want || shown !== want) {", "if (shown !== want) {")],
    "the name sent as it is": [("var want = tidy(q.name || '');", "var want = String(q.name || '');")],
    "the cell as it is": [("var shown = tidy(cell.getDisplayValue());", "var shown = String(cell.getDisplayValue());")],
    "moved says what the cell shows": [("out = {ok: false, why: 'moved'};", "out = {ok: false, why: 'moved', shown: shown};")],
    "the colour before the name": [("      if (!want || shown !== want) {\n        out = {ok: false, why: 'moved'};",
                                    "      if (!MAY_CHANGE.hasOwnProperty(String(cell.getBackground()).toLowerCase())) {\n        out = {ok: false, why: 'coloured', before: String(cell.getBackground()).toLowerCase()};\n"
                                    "      } else if (!want || shown !== want) {\n        out = {ok: false, why: 'moved'};")],
    "the value, not what is shown": [("var shown = tidy(cell.getDisplayValue());", "var shown = tidy(cell.getValue());")],
    # not a name
    "no not-a-name rule": [("      } else if (notAName(shown, cell.getValue())) {", "      } else if (false) {")],
    "numbers and dates allowed": [("typeof value !== 'string' || ", "")],
    "a leading digit allowed": [("/^[0-9]/.test(shown) || ", "")],
    "no capital letter allowed": [("shown === shown.toLowerCase() ||", "")],
    "start times not filled in allowed": [("HOLE.test(shown) || ", "")],
    "notes allowed": [("NOTE.test(shown) || ", "")],
    "weekday headings allowed": [(" || WEEKDAY.test(shown);", ";")],
    "a note word anywhere": [("var NOTE = /^[\\s!-\\/:-@\\[-`{-~]*(", "var NOTE = /(")],
    "a note word as part of a word": [("\\.*(?![A-Za-z0-9_.'\\u2019]|[^\\x00-\\x7f])/i;", "/i;")],
    # merged
    "merged cells allowed": [("        } else if (cell.isPartOfMerge()) {", "        } else if (false) {")],
    # the day
    "no day rule": [("        if (!todayOrYesterday(day)) {", "        if (false) {")],
    "any day is today": [("  var now = nowDate();\n  var today", "  return true;\n  var now = nowDate();\n  var today")],
    "yesterday all day": [(" && Number(Utilities.formatDate(now, ZONE, 'H')) < 12;", ";")],
    "yesterday until 13:00": [("'H')) < 12;", "'H')) < 13;")],
    "yesterday until 12:59": [("'H')) < 12;", "'H')) <= 12;")],
    "tomorrow, not yesterday": [("Number(p[2]) - 1)", "Number(p[2]) + 1)")],
    "yesterday as 24 hours ago": [("new Date(Date.UTC(Number(p[0]), Number(p[1]) - 1, Number(p[2]) - 1)), 'UTC'", "new Date(now.getTime() - 86400000), ZONE")],
    "today by the spreadsheet's zone": [("var today = Utilities.formatDate(now, ZONE, 'yyyy-MM-dd');", "var today = Utilities.formatDate(now, SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone(), 'yyyy-MM-dd');")],
    "noon by UTC": [("Number(Utilities.formatDate(now, ZONE, 'H'))", "Number(Utilities.formatDate(now, 'UTC', 'H'))")],
    "the real clock": [("  var now = nowDate();\n  var today", "  var now = new Date();\n  var today")],
    "dates in row 2 read in UTC": [("Utilities.formatDate(v, sheet.getParent().getSpreadsheetTimeZone(), 'yyyy-MM-dd')", "Utilities.formatDate(v, 'UTC', 'yyyy-MM-dd')")],
    "number dates rounded": [("Math.floor(v)", "Math.round(v)")],
    "number dates a day out": [("Date.UTC(1899, 11, 30)", "Date.UTC(1899, 11, 31)")],
    "today only before the evening": [("  if (day === today) return true;", "  if (day === today && Number(Utilities.formatDate(now, ZONE, 'H')) < 18) return true;")],
    # colours
    "no colour rule": [("        } else if (!MAY_CHANGE.hasOwnProperty(before)) {", "        } else if (false) {")],
    "orange allowed": [("var MAY_CHANGE = {'#ffffff': 1, '#ffff00': 1, '#92d050': 1};", "var MAY_CHANGE = {'#ffffff': 1, '#ffff00': 1, '#92d050': 1, '#ff9900': 1};")],
    "the heading blue allowed": [("var MAY_CHANGE = {'#ffffff': 1, '#ffff00': 1, '#92d050': 1};", "var MAY_CHANGE = {'#ffffff': 1, '#ffff00': 1, '#92d050': 1, '#00b0f0': 1};")],
    "light grey allowed": [("var MAY_CHANGE = {'#ffffff': 1, '#ffff00': 1, '#92d050': 1};", "var MAY_CHANGE = {'#ffffff': 1, '#ffff00': 1, '#92d050': 1, '#999999': 1};")],
    "only four colours refused": [("        } else if (!MAY_CHANGE.hasOwnProperty(before)) {", "        } else if ({'#ff9900': 1, '#ff0000': 1, '#666666': 1, '#000000': 1}.hasOwnProperty(before)) {")],
    "no expected-colour rule": [("        } else if (q.was && before !== everyday(q.was)) {", "        } else if (false) {")],
    "expected colour in exact letters": [("before !== everyday(q.was)", "before !== String(q.was)")],
    # version 4 (14 Nov): a few shades off white/yellow/green is that colour - and only a few
    "no near-colour rule": [("    if (near) return keys[k];", "")],
    "far too generous near-colour": [("var NEAR = 12;", "var NEAR = 60;")],
    "the cell's colour taken exactly": [("        var before = everyday(cell.getBackground());", "        var before = String(cell.getBackground()).toLowerCase();")],
    "an empty expected colour refuses": [("q.was && before !==", "q.was !== undefined && before !==")],
    "white on yellow allowed": [("        } else if (q.action === 'white' && before === YELLOW) {", "        } else if (false) {")],
    # the log
    "no log of a change": [("          console.log('Check Call Log: ' + m[0] + ' ' + shown + ' ' + before + ' -> ' + to);\n", "")],
}
# breaks that cannot be seen from outside, and why (they are run too, and reported, but do not fail this check)
SAME = {
    "the cell as sent, not as read": "the cell rule has already made sure they are the same text",
}


def make(name, edits):
    folder = os.path.join(BASE, "tool-%d" % abs(hash(name)))
    os.makedirs(os.path.join(folder, "tests"), exist_ok=True)
    for mod in ("calllog.py", "forms.py", "xl.py", "whatsapp.py", "wa_bridge.py", "wa_beta.py"):  # include the local import chain
        shutil.copy(os.path.join(TOOL, mod), folder)
    src = SOURCE
    for old, new in edits:
        n = src.count(old)
        if n != 1:
            raise SystemExit("break %r: %r is in the script %d times (the script has changed - update this check)" % (name, old[:70], n))
        src = src.replace(old, new)
    with open(os.path.join(folder, "rostercolour.py"), "w", encoding="utf-8") as f:
        f.write(src)
    return folder


def run(name, folder):
    tmp = os.path.join(folder, "temp")
    os.makedirs(tmp, exist_ok=True)
    env = dict(os.environ, CCL_TOOL=folder, TEMP=tmp, TMP=tmp, PYTHONDONTWRITEBYTECODE="1")
    try:
        p = subprocess.run([sys.executable, os.path.join(HERE, "t_script.py")], capture_output=True, text=True, timeout=300, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = p.stdout.splitlines()
        summary = next((l for l in reversed(lines) if re.fullmatch(r"\d+ of \d+ checks passed", l)), "")
        return p.returncode, [l for l in lines if l.startswith("FAIL  ")], summary, p.stderr
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return "timeout", [], "", stderr


def caught(code, fails, stderr):
    # A process crash, missing dependency or timeout is not evidence that an assertion caught this break.
    import_failed = re.search(r"^(?:\w+\.)?(?:ImportError|ModuleNotFoundError):", stderr, re.MULTILINE)
    return isinstance(code, int) and code != 0 and bool(fails) and not import_failed


def passed(code, fails, summary, stderr):
    counts = re.fullmatch(r"(\d+) of (\d+) checks passed", summary)
    return code == 0 and not fails and not stderr.strip() and counts is not None and int(counts[1]) == int(counts[2]) > 0


jobs = {"(no break)": []}
jobs.update(BREAKS)
folders = {n: make(n, e) for n, e in jobs.items()}
with concurrent.futures.ThreadPoolExecutor(4) as pool:
    done = dict(zip(jobs, pool.map(lambda n: run(n, folders[n]), list(jobs))))
results = []
for name in jobs:
    code, fails, summary, stderr = done[name]
    detected = caught(code, fails, stderr)
    if name == "(no break)":
        ok = passed(code, fails, summary, stderr)
        print("%s  the script as it is passes every check  %s" % ("PASS" if ok else "FAIL", summary))
    elif name in SAME:
        ok = detected or passed(code, fails, summary, stderr)
        print("%s  %-40s %s  (%s)" % ("SAME" if ok else "FAIL", name, "caught" if detected else "not caught", SAME[name]))
    else:
        ok = detected
        detail = ("caught by: " + fails[0][6:90]) if detected else (
            "CHILD FAILURE - not a valid mutation result" if code != 0 else "NOT CAUGHT - a gap in the checks")
        print("%s  broken: %-40s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        print("  child exit: %s; summary: %s" % (code, summary or "missing"))
        for line in stderr.strip().splitlines()[-20:]:
            print("  child stderr: " + line)
    results.append(ok)
shutil.rmtree(BASE, ignore_errors=True)
print("\n%d of %d checks passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
