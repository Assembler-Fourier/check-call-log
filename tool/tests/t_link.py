"""The tool's side of "turn names green on the roster" (rostercolour.py), without the internet: which links it takes,
the key it keeps, how it reads colours, and how it talks to the script - against a stand-in for Google on this PC
(127.0.0.1 only) that answers the way Apps Script does (a redirect, then the answer), and the ways that can go wrong."""
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time

TOOL = os.environ.get("CCL_TOOL", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, TOOL)
WORK = os.path.join(tempfile.gettempdir(), "ccl-tests", "link")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)
os.environ["CCL_ROSTER_LINK"] = os.path.join(WORK, "roster-link.json")      # never the real one
import calllog  # noqa: E402
import rostercolour as rc  # noqa: E402

calllog.EDITS = os.path.join(WORK, "edits")
results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print("%s  %s  %s" % ("PASS" if ok else "FAIL", label, str(detail)[:300]))


check("the link file is the scratch one", rc.CONFIG == os.path.join(WORK, "roster-link.json"), rc.CONFIG)

# ---- links ----
good = ["https://script.google.com/macros/s/AKfycbz1RTx-abc_DEF123/exec",
        "https://script.google.com/a/macros/northwind.ie/s/AKfycbz1RTx-abc_DEF123/exec"]
bad = ["http://script.google.com/macros/s/AKfycbz/exec", "https://script.google.com/macros/s/AKfycbz/dev",
       "https://script.google.com.evil.example/macros/s/AKfycbz/exec", "https://evil.example/script.google.com/macros/s/x/exec",
       "https://script.google.com/macros/s/AKfycbz/exec?x=1", " https://script.google.com/macros/s/AKfycbz/exec",
       "https://script.google.com/macros/s//exec", "https://docs.google.com/spreadsheets/d/1NwDemoRosterSheetIdNotARealOne0000000000000/edit",
       "script.google.com/macros/s/AKfycbz/exec", "", "https://script.google.com/macros/s/AK fy/exec"]
check("a web app link from Apps Script is taken (also a Workspace one)", all(rc.URL_RE.match(u) for u in good))
check("nothing else is: http, /dev, look-alike hosts, extra bits, the roster's own link", not any(rc.URL_RE.match(u) for u in bad),
      [u for u in bad if rc.URL_RE.match(u)])
try:
    rc.send({"url": "", "key": "x" * 32}, "ping")
    refused = False
except calllog.Problem as e:
    refused = "not switched on" in str(e)
check("with no link, nothing is sent and it says it is not switched on", refused)

# ---- the key ----
link = rc.load()
check("a key is made the first time and kept", len(link["key"]) >= 24 and link["url"] == "" and os.path.exists(rc.CONFIG))
check("...and the same key comes back next time", rc.load()["key"] == link["key"])
rc.save(dict(link, url=good[0]))
check("a saved link comes back with the same key", rc.load() == {"url": good[0], "key": link["key"]})
check("no file is left half-written", not os.path.exists(rc.CONFIG + ".part"))
with open(rc.CONFIG, "w", encoding="utf-8-sig") as f:          # (as PowerShell would save it: with a BOM)
    json.dump({"url": good[0], "key": link["key"]}, f)
check("a link file saved with a BOM is read as it is", rc.load() == {"url": good[0], "key": link["key"]})
with open(rc.CONFIG, "w", encoding="utf-8") as f:
    f.write("{broken")
try:
    rc.load()
    refused = False
except calllog.Problem as e:
    refused = "could not be read" in str(e)
check("a damaged link file is refused - and NOT written over (its key is the one in the roster's script)",
      refused and open(rc.CONFIG, encoding="utf-8").read() == "{broken")
os.remove(rc.CONFIG)
fresh = rc.load()
check("no file at all: a new key is made", fresh["url"] == "" and len(fresh["key"]) >= 24 and fresh["key"] != link["key"])
check("two keys made are never the same", len({__import__("secrets").token_urlsafe(24) for _ in range(50)}) == 50)

# ---- colours as the roster copy gives them ----
check("green is recognised however it is written", all(rc.is_green(f) for f in ("FF92D050", "ff92d050", "#92d050", "92D050")) and
      not any(rc.is_green(f) for f in (None, "", "theme0", "FFFFFF00", "FFA0D050", "#92d05")))
check("14 Nov: a few shades off is the same colour (FF92D051 is green, FFFBFBFB white); further off is not",
      rc.is_green("FF92D051") and rc.css("FFFBFBFB") == "#ffffff" and rc.changeable("FFFBFBFB")
      and rc.css("FFD9D9D9") == "#d9d9d9" and not rc.changeable("FFD9D9D9") and not rc.changeable("FFFFF2CC"))
check("colours read as the script sees them", [rc.css(f) for f in (None, "theme0", "FFFFFFFF", "FFFFFF00", "FF92D050", "FFFF9900", "theme5", "nonsense")] ==
      ["#ffffff", "#ffffff", "#ffffff", "#ffff00", "#92d050", "#ff9900", "", ""])
check("only white, yellow and green names may be changed", [rc.changeable(f) for f in (None, "theme0", "FFFFFFFF", "FFFFFF00", "FF92D050", "FFFF9900", "FFFF0000", "FF666666", "FF000000", "theme5")] ==
      [True, True, True, True, True, False, False, False, False, False])
check("notes name only the unusual colours", [calllog.fill_word(f) for f in (None, "theme0", "FFFFFFFF", "FFFFFF00", "FF92D050", "FFFF9900", "FFFF0000", "FF666666", "theme5")] ==
      ["", "", "", "", "", "orange", "red", "grey", "coloured differently"])

# ---- talking to the script: a stand-in for Google on 127.0.0.1 ----
seen, mode = [], {"answer": {"ok": True, "sheet": "Sheet1"}, "how": "redirect"}


class Google(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        seen.append((self.path, self.headers.get("Content-Type"), body))
        how = mode["how"]
        if how == "slow":
            time.sleep(3)
        if how in ("redirect", "slow"):
            self.send_response(302)
            self.send_header("Location", "/macros/echo?user_content_key=abc")
            self.end_headers()
            return
        self.send_response(500 if how == "error" else 200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write({"html": b"<html><body>Sign in - Google Accounts</body></html>", "garbage": b"\x00\x01 not json", "error": b"oops"}.get(how, b""))

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(mode["answer"]).encode())


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Google)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/macros/s/TEST/exec" % server.server_port
real_re = rc.URL_RE
rc.URL_RE = __import__("re").compile(r"^http://127\.0\.0\.1:\d+/macros/s/TEST/exec$")
test_link = {"url": url, "key": "k" * 32}
answer = rc.send(test_link, "green", "FK74", "Lena Drayton")
check("a request goes out as JSON with exactly the key, the action, the cell and the name",
      seen and seen[-1][1] == "application/json" and json.loads(seen[-1][2]) == {"key": "k" * 32, "action": "green", "cell": "FK74", "name": "Lena Drayton", "was": ""}, seen[-1:])
check("...Google's redirect is followed and the answer read", answer == {"ok": True, "sheet": "Sheet1"}, answer)
mode["answer"] = {"ok": False, "why": "moved", "shown": "Someone Else"}
check("a refusal comes back as it was given", rc.send(test_link, "green", "FK74", "Lena Drayton")["why"] == "moved")
for how, words in (("html", "Who has access"), ("garbage", "could not be read"), ("error", "could not be reached")):
    mode["how"] = how
    try:
        rc.send(test_link, "ping")
        said = "(no problem raised)"
    except calllog.Problem as e:
        said = str(e)
    check("an answer that is %s says so plainly" % {"html": "a sign-in page", "garbage": "rubbish", "error": "a server error"}[how], words in said, said)
mode["how"], rc.TIMEOUT = "slow", 1
start = time.time()
try:
    rc.send(test_link, "ping")
    said = ""
except calllog.Problem as e:
    said = str(e)
check("a script that does not answer in time gives up, it does not hang", "could not be reached" in said and time.time() - start < 6, (said, time.time() - start))
server.shutdown()
server.server_close()
rc.TIMEOUT, mode["how"] = 30, "redirect"
try:
    rc.send(test_link, "ping")
    said = ""
except calllog.Problem as e:
    said = str(e)
check("no network at all: a plain message, no crash", "could not be reached" in said, said)
rc.URL_RE = real_re

# ---- the setup files ----
rc.SETUP = os.path.join(WORK, "Roster script")
folder = rc.write_setup({"url": "", "key": "the-key-" + "z" * 24})
script = open(os.path.join(folder, "Roster script.gs"), encoding="utf-8").read()
how_to = open(os.path.join(folder, "How to switch it on.txt"), encoding="utf-8").read()
check("the script written out carries this PC's key, once", script.count("the-key-" + "z" * 24) == 1 and "%(key)s" not in script)
check("...and never writes text: no setValue, no clear, no insert, no delete", not any(w in script for w in ("setValue", "clear", "insert", "delete", "setFormula", "appendRow", "copyTo", "moveTo")))
check("...sets a fill in one place only, to green or white", script.count("setBackground(") == 1 and "setBackground(to)" in script)
check("the steps say: Anyone, Execute as Me, ends in /exec, today only", all(w in how_to for w in ("Who has access: Anyone", "Execute as: Me", "/exec", "today")))

print("\n%d of %d checks passed" % (sum(results), len(results)))
shutil.rmtree(WORK, ignore_errors=True)
sys.exit(0 if all(results) else 1)
