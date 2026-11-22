"""The WhatsApp reader's side in Check Call Log: reading replies, matching chat names, the receiver, the overlay.

The fake data is real: the exchanges are last Saturday's (31 Oct 2026), as read from WhatsApp Web - including
the ones that trip a naive rule (a "Sorry I cant" that answered a different question, a thumbs-up instead of a
word, a voice note, a guard who checks in without being asked). Everything is written to a scratch folder; no
message is ever sent and nothing in the user's data is touched.
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import whatsapp as wa  # noqa: E402
import calllog  # noqa: E402

sys.stdout.reconfigure(errors="replace")             # the console cannot print a thumbs-up
WORK = os.path.join(tempfile.gettempdir(), "ccl-tests", "whatsapp")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)
wa.LOGS, wa.LINK = os.path.join(WORK, "logs"), os.path.join(WORK, "link.json")
calllog.EDITS = wa.EDITS = os.path.join(WORK, "edits")
wa.SETTINGS, wa.NAMES = os.path.join(WORK, "whatsapp.json"), os.path.join(WORK, "names.json")
wa.READER = os.path.join(WORK, "reader")
wa.PORT = 18765                                       # never the real window's port
results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print("%s  %s  %s" % ("PASS" if ok else "FAIL", label, str(detail)[:260]))


# ---- what a reply says ----
print("what a reply says")
for text, want in (("Yes", "yes"), ("Ok", "yes"), ("Okay", "yes"), ("Yeap", "yes"), ("yeap", "yes"), ("Yeah", "yes"),
                   ("All good", "yes"), ("All okay thanks", "yes"), ("Yeah , All good Thank you", "yes"),
                   ("Yes all good", "yes"), ("All good sir", "yes"), ("Confirm", "yes"), ("Confirmed thanks", "yes"),
                   ("Copied", "yes"), ("NTR", "yes"), ("I am ready", "yes"), ("Y,es", "yes"), ("All good this endx", "yes"),
                   ("Good afternoon!! Hope you\u2019re all well!! Yeah, getting ready and on my way", "yes"),
                   ("\U0001F44D", "yes"), ("Hi 12 at 22.00 All good Have a good night", "unclear"),  # needs roster context
                   ("Yeah ill sort it out tonight", "unclear"),          # not a plain yes: he reads it
                   ("All good, no issues", "yes"), ("Yes all good no problems", "yes"), ("Ok no bother", "yes"),
                   ("Good evening", "unclear"), ("Hi good evening sir", "unclear"),
                   ("Ok, I don't think I can make it tonight", "no"), ("Ok I aint coming", "no"),
                   ("Ok, venue isnt open yet", "no"), ("Ok but I havent got my uniform", "no"),
                   ("Ok, nu pot veni", "no"), ("Ok bhai aaj nahi aa sakta", "no"),
                   ("Ok, não vou conseguir hoje", "no"), ("Can´t make it, all good otherwise", "no"),
                   ("Yes?", "unclear"), ("Ok?", "unclear"), ("Ok I'm at home, didnt know I was working", "no"),
                   ("Sorry I cant", "no"), ("Sorry not available", "no"), ("No brother. Sorry", "no"),
                   ("Sorry for late response, had phone on charge.", "unclear"),   # the apology is fine; the rest he reads
                   ("I\u2019m trying, but it\u2019s not looking good at the moment.", "no"),
                   ("5 pm till close?", "unclear"), ("Can you send me the list for K9", "unclear"), ("", "unclear")):
    got = wa.judge(text)
    check("%r -> %s" % (text, want), got == want, got)

for text in ("لا أستطيع الحضور", "OK لا أستطيع الحضور", "👍 لا أستطيع الحضور",
             "Yes 我不能来", "Ok я не могу прийти", "OK नहीं आ सकता", "OK ١٢"):
    check("unreadable script cannot disappear into an affirmative: %r" % text, wa.judge(text) == "unclear")
for text in ("Yés", "Ók", "All góod", "👍🏿", "✅"):
    check("ordinary accents and affirmative emoji remain supported: %r" % text, wa.judge(text) == "yes")

# ---- which of our messages is a check call ----
print("\nwhich of our messages is a check call")
for text, want in (("All okay for the meadows?", "the meadows"), ("Ok for bullseye club?", "bullseye club"),
                   ("All good for 12 Harbour St?", "12 Harbour St"), ("Ok for Mema\u2019s?", "Mema\u2019s"),
                   ("All okay for Lucerne? And can you work in Bullseye Club tomorrow @ 5pm", "Lucerne"),
                   ("Is everything okay for the jug at 5pm", "the jug at 5pm"),
                   ("Hi, would you be available to work at 12 Harbour Street tomorrow from 10 pm until close?", None),
                   ("Would it be ok for you to cover K9 tomorrow?", None), ("Tomorrow Bullseye Club 5pm Confirmed?", None),
                   ("Time Change for K9 Tonight 22:00-close Confirm?", None), ("?", None),
                   ("Hey Tavin would you be available to work a shift Tonight in K9 from 22:30-close?", None),
                   ("All good for Saturday?", None), ("Ok for Sunday at the Jug?", None), ("All okay for K9 tommorow?", None),
                   ("Ok for K9 tmr?", None), ("All good for 2moro?", None), ("Ok for next Friday at the Jug?", None),
                   ("Would you be ok for K9 on Saturday?", None), ("Are you ok for an extra shift at K9 Sunday?", None),
                   ("Is everything ok at home?", None), ("Ok in 10 mins I'll ring you", None), ("Ok at 22:30 then", None)):
    got = wa.question_venue(text)
    check("%r -> %r" % (text[:50], want), got == want, got)

# ---- chat names to roster names ----
print("\nchat names to roster names")
people = ["Craig Ashdown", "Austin Baird", "Gavin Xu", "Dante Takeda", "Julian Birch", "Ravel Devan",
          "Lorcan Ellery", "Lorcan Halden", "Lorcan Carrl", "Jasper Albrecht", "Dale Merrick", "Ruben Corbett"]
chats = ["Craig Orvel Ahsdown", "Mbeltoni Austin Baird", "GAVIN XU Xu VANTAGGIO", "Takeda Both Dante Nov\u201925 Ind",
         "Juilan Birch", "Ravel Devan Pellam Both May\u201926", "Lorcan Ellery", "Lorcan Both Sept\u201926 Halden",
         "Lorcan Carrl", "Jaspher Albrecht", "Dhale Merick", "Ruben Corbett Sept\u201919 Apr\u201925",
         "Rosters 2023 Current Group \u2705", "Portlane Site Phone", "Lorcan"]
got = wa.match_chats(chats, people)
check("every guard found under the phone's own name for him", all(got.get(c) == p for c, p in zip(chats, people)),
      {c: got.get(c) for c in chats[:12]})
check("a group chat and a site phone are nobody", "Rosters 2023 Current Group \u2705" not in got and "Portlane Site Phone" not in got)
check("a bare 'Lorcan' is not guessed between three Muhammads", "Lorcan" not in got, got.get("Lorcan"))
check("a name taught by hand wins", wa.match_chats(["Portlane Site Phone"], ["Arlo Jarvis"],
                                                  {"Portlane Site Phone": "Arlo Jarvis"}) == {"Portlane Site Phone": "Arlo Jarvis"})

# ---- a night's replies, read the way the reader sees them ----
print("\na night's replies")
DAY = datetime.date(2026, 10, 31)


def call(name, site, start, venue=None):
    h, m = divmod(start, 60)
    return {"name": name, "site": site, "venue": venue or site, "shift": "%02d:%02d-close" % (h % 24, m), "start": start}


def ev(chat, ours, text, hhmm, what="text", snapshot=False, emoji=None, day=DAY, to=None, group=False):
    h, m = map(int, hhmm.split(":"))
    seen = datetime.datetime.combine(day, datetime.time(h, m)) + datetime.timedelta(seconds=7)
    e = {"kind": "line", "chat": chat, "ours": ours, "text": text, "shown": hhmm, "seen": int(seen.timestamp() * 1000),
         "what": what, "snapshot": snapshot}
    if emoji:
        e["emoji"] = emoji
    if to:
        e["to"] = to
    if group:
        e["group"] = True
    return e


calls = [call("Ruben Corbett", "The Meadows", 19 * 60), call("Dhale Acheson", "The Jug", 20 * 60),
         call("Dale Merrick", "K9 / Red Door", 21 * 60, "K9/Red Door"), call("Milo Padgett", "12 Harbour st", 22 * 60),
         call("Dante Takeda", "12 Harbour st", 22 * 60), call("Sana Ainsley", "Loft", 22 * 60),
         call("Julian Birch", "Perch Kilcarran", 22 * 60, "The Perch"),
         call("Hector Nash", "Bullseye Club Darts / Pats", 19 * 60), call("Hector Nash", "12 Harbour st", 25 * 60),
         call("Carl Fielding", "Lucerne", 20 * 60), call("Tavin Moreau", "K9 / Red Door", 22 * 60 + 30, "K9/Red Door"),
         call("Maren Valdez", "K9 / Red Door", 22 * 60 + 30, "K9/Red Door")]
log = {"calls": calls, "meta": {"date_iso": DAY.isoformat(), "supervisor": "Lena Drayton", "start_min": 18 * 60, "end_min": 30 * 60},
       "stem": "2026-10-31 Lena Drayton"}
events = [
    ev("Ruben Corbett Sept\u201919 Apr\u201925", True, "All okay for the meadows?", "18:10"),
    ev("Ruben Corbett Sept\u201919 Apr\u201925", False, "Yeap", "18:32"),
    ev("Ruben Corbett Sept\u201919 Apr\u201925", True, "Hi, would you be available to work at 12 Harbour Street tomorrow from 10 pm until close?", "18:36"),
    ev("Ruben Corbett Sept\u201919 Apr\u201925", False, "Sorry I cant", "18:37"),
    ev("Dhale Acheson Newest Number", True, "Ok for the jug?", "18:57"),
    ev("Dhale Acheson Newest Number", False, "", "18:58", what="reaction", emoji="\U0001F44D", to="Ok for the jug?"),
    ev("Dhale Merick", True, "All okay for K9?", "19:58"),
    ev("Dhale Merick", False, "0:03", "20:17", what="voice"),
    ev("Milo Padgett", False, "Hi 12 at 22.00 All good Have a good night", "20:51"),
    ev("Takeda Both Dante Nov\u201925 Ind", True, "All good for 12 Harbour St?", "20:55"),
    ev("Takeda Both Dante Nov\u201925 Ind", True, "?", "21:21"),
    ev("Takeda Both Dante Nov\u201925 Ind", False, "Y,es", "21:21"),
    ev("Sana Tamsin Ainsley", True, "All okay for Loft?", "20:55"),
    ev("Sana Tamsin Ainsley", False, "Sorry for late response, had phone on charge.", "21:21"),
    ev("Juilan Birch", True, "All good for The Perch?", "20:56"),
    ev("Hector Nash", True, "Ok for bullseye club?", "18:10"),
    ev("Hector Nash", False, "Good afternoon!! Hope you\u2019re all well!! Yeah, getting ready and on my way", "18:11"),
    ev("Carl Fielding", True, "All okay for Lucerne? And can you work in Bullseye Club tomorrow @ 5pm", "18:31"),
    ev("Carl Fielding", False, "Yes", "18:32"),
    ev("Tavin Moreau", True, "Would it be ok for you to cover K9 tomorrow?", "20:40"),
    ev("Tavin Moreau", False, "Yes", "20:41"),
    ev("Maren Valdez", False, "Yes", "20:57", snapshot=True),
]
found = wa.heard(log, events)
key = {(c["name"], c["site"]): calllog.edit_key(c) for c in calls}
h = lambda name, site: found.get(key[(name, site)], {})
check("Ruben: tonight's 'Yeap' counts, not the 'Sorry I cant' that answered tomorrow's offer",
      h("Ruben Corbett", "The Meadows").get("response") == "Ok" and h("Ruben Corbett", "The Meadows").get("said") == "Yeap",
      h("Ruben Corbett", "The Meadows"))
check("...the question anchor stays 18:10, separate from the guard's reply clock", h("Ruben Corbett", "The Meadows").get("time") == "18:10"
      and h("Ruben Corbett", "The Meadows").get("reply_at") == DAY.isoformat()+"T18:32")
check("Acheson: a thumbs-up on the question is a yes", h("Dhale Acheson", "The Jug").get("how") == "reaction"
      and h("Dhale Acheson", "The Jug").get("response") == "Ok", h("Dhale Acheson", "The Jug"))
check("Merick: a voice note is shown to be listened to, and stays white",
      h("Dale Merrick", "K9 / Red Door").get("how") == "voice" and h("Dale Merrick", "K9 / Red Door").get("response") == "",
      h("Dale Merrick", "K9 / Red Door"))
check("Milo: checking in without being asked goes green (the user's rule)",
      h("Milo Padgett", "12 Harbour st").get("how") == "checkin" and h("Milo Padgett", "12 Harbour st").get("response") == "Ok",
      h("Milo Padgett", "12 Harbour st"))
check("Dante: our chaser '?' is not a reply, his 'Y,es' is", h("Dante Takeda", "12 Harbour st").get("response") == "Ok"
      and h("Dante Takeda", "12 Harbour st").get("time") == "20:55", h("Dante Takeda", "12 Harbour st"))
check("Sana: 'sorry for late response' is shown, not counted", h("Sana Ainsley", "Loft").get("how") == "check"
      and h("Sana Ainsley", "Loft").get("response") == "", h("Sana Ainsley", "Loft"))
check("Birch: asked, no answer yet - waiting, with the time asked", h("Julian Birch", "Perch Kilcarran")
      == {"time": "20:56", "how": "asked", "response": ""}, h("Julian Birch", "Perch Kilcarran"))
check("Hector: the Bullseye Club call is answered...", h("Hector Nash", "Bullseye Club Darts / Pats").get("response") == "Ok",
      h("Hector Nash", "Bullseye Club Darts / Pats"))
check("...and his 01:00 shift at 12 is still waiting (the 18:10 question was for Bullseye Club)",
      h("Hector Nash", "12 Harbour st") == {}, h("Hector Nash", "12 Harbour st"))
check("Carl: a check call with an offer after it still counts", h("Carl Fielding", "Lucerne").get("response") == "Ok")
check("Tavin: a 'Yes' to a shift offer is never a check-in", h("Tavin Moreau", "K9 / Red Door") == {}, h("Tavin Moreau", "K9 / Red Door"))
check("Maren: a 'Yes' only seen on screen when the reader started (no question seen) is not counted",
      h("Maren Valdez", "K9 / Red Door") == {}, h("Maren Valdez", "K9 / Red Door"))

# ---- everything else the company phone carries (Lena, 2 Nov: "how it differentitates") ----
print("\nwhat the company phone also carries")
calls2 = [call("Tavin Moreau", "K9 / Red Door", 22 * 60 + 30, "K9/Red Door"), call("Gideon O'Kellan", "12 Harbour st", 19 * 60),
          call("Lorcan Ellery", "K9 / Red Door", 22 * 60, "K9/Red Door"), call("Idris Kalden", "K9 / Red Door", 22 * 60, "K9/Red Door"),
          call("Faris Khan", "K9 / Red Door", 22 * 60 + 30, "K9/Red Door"), call("Maren Valdez", "K9 / Red Door", 22 * 60 + 30, "K9/Red Door"),
          call("Yusuf Eklund", "Lucerne", 22 * 60), call("Nthan Chaudry", "Loft", 22 * 60), call("Rory Orwell", "K9 / Red Door", 22 * 60, "K9/Red Door")]
log2 = dict(log, calls=calls2)
key2 = {c["name"]: calllog.edit_key(c) for c in calls2}
phone = [
    ev("Greta Lyndon", False, "Hi, I WONT be available this week. Family emergency back home.", "20:29"),   # not rostered tonight
    ev("+353 87 555 0101", False, "Hello, I am looking for security work, please find my CV attached", "19:10"),  # a stranger
    ev("Tavin Moreau", True, "All okay for K9?", "21:30"),
    ev("Tavin Moreau", False, "Yes all good", "21:33", group=True),                  # a group renamed after him
    ev("Tavin Moreau", True, "Would it be ok for you to cover K9 tomorrow?", "23:10"),
    ev("Tavin Moreau", False, "Yes", "23:12"),                                      # answers the offer, not tonight
    ev("Lorcan Ellery", True, "All okay for K9?", "20:56"),
    ev("Lorcan Ellery Khan Both Jan’25", False, "Yes all good", "20:58"),      # a second Lorcan Ellery
    ev("Idris Kalden", True, "All okay for K9?", "20:55"),
    ev("Idris Kalden", True, "Can you do Bullseye Club on Sunday 5pm?", "21:40"),
    ev("Idris Kalden", False, "", "21:45", what="reaction", emoji="\U0001F44D", to="Can you do Bullseye Club on Sunday 5pm?"),
    ev("Faris Khan", True, "All okay for K9?", "21:00"),
    ev("Faris Khan", False, "Yes, and can I have next week off?", "21:04"),
    ev("Maren Valdez", True, "All okay for K9?", "21:00"),
    ev("Maren Valdez", False, "Yes all good, can I get my payslip?", "21:02"),
    ev("Gideon O'Kellan", False, "Sorry lads, sick tonight - can't make it to 12", "17:10"),   # a trusted senior, unasked
    ev("Yusuf Aug’26 Khan", True, "Can you cover Lucerne tonight? 22:00", "16:00"),
    ev("Yusuf Aug’26 Khan", False, "Ok", "19:30"),                                  # a yes to this afternoon's offer
    ev("Nthan Chaudry", False, "Can I swap Sunday with someone?", "20:10"),                 # asked nothing yet; not a yes
    ev("Rory Orwell", True, "All okay for K9?", "20:55"),
    ev("Rory Orwell", False, "Can you send me the list for K9", "20:57"),                  # something different
]
got2 = wa.heard(log2, phone)
h2 = lambda name: got2.get(key2[name], {})
check("a holiday request from someone not on tonight's log touches nothing", all("Greta" not in k for k in got2))
check("a CV from an unknown number touches nothing", len([k for k in got2 if "+353" in k]) == 0)
check("Tavin: a 'Yes' in a group renamed after him does not count", h2("Tavin Moreau").get("response") == "", h2("Tavin Moreau"))
check("Tavin: ...nor does his 'Yes' to a later shift offer - he is still waiting", h2("Tavin Moreau").get("how") == "asked", h2("Tavin Moreau"))
check("two chats both look like Lorcan Ellery: nobody guesses, he stays white", h2("Lorcan Ellery") == {}, h2("Lorcan Ellery"))
check("Idris: a thumbs-up on a different message is not a yes to the check call", h2("Idris Kalden").get("response") == "", h2("Idris Kalden"))
check("Faris: 'Yes, and can I have next week off?' is shown for you to read, not green",
      h2("Faris Khan").get("response") == "" and h2("Faris Khan").get("how") == "check", h2("Faris Khan"))
check("Maren: 'Yes all good, can I get my payslip?' is shown for you to read, not green",
      h2("Maren Valdez").get("response") == "" and h2("Maren Valdez").get("how") == "check", h2("Maren Valdez"))
check("Gideon (trusted, never asked): 'sick tonight, can't make it' is recorded - it must stop him going green",
      h2("Gideon O'Kellan").get("how") == "check" and "sick" in h2("Gideon O'Kellan").get("said", ""), h2("Gideon O'Kellan"))
check("Yusuf: an 'Ok' to this afternoon's offer is not a check-in for tonight", h2("Yusuf Eklund").get("response") != "Ok", h2("Yusuf Eklund"))
check("Nthan: a question of his own, not asked anything yet, is shown - not green",
      h2("Nthan Chaudry").get("how") == "check" and h2("Nthan Chaudry").get("response") == "", h2("Nthan Chaudry"))
check("Rory: a different first reply ('send me the list') is shown, not green",
      h2("Rory Orwell").get("how") == "check" and h2("Rory Orwell").get("response") == "", h2("Rory Orwell"))

# ---- the second review's cases (6 Nov) ----
print("\nthe second review's cases")
two = [call("Hector Nash", "Bullseye Club Darts / Pats", 20 * 60), call("Hector Nash", "12 Harbour st", 22 * 60),
       call("Milo Padgett", "Bullseye Club Darts / Pats", 20 * 60), call("Milo Padgett", "12 Harbour st", 22 * 60),
       call("Ruben Corbett", "The Meadows", 19 * 60), call("Gideon O'Kellan", "12 Harbour st", 19 * 60),
       call("Idris Kalden", "K9 / Red Door", 22 * 60, "K9/Red Door")]
log3 = dict(log, calls=two)
k3 = {(c["name"], c["site"]): calllog.edit_key(c) for c in two}


def bubble(chat, ours, text, hhmm, i=0):
    e = ev(chat, ours, text, hhmm)
    e.update(kind="bubble", date=DAY.strftime("%d/%m/%Y"), i=i)
    return e


def run3(events, trusted=None):
    got = wa.heard(log3, events, None, trusted)
    return lambda name, site: got.get(k3[(name, site)], {})


h3 = run3([ev("Hector Nash", True, "All okay for 12 Harbour st?", "20:30"), ev("Hector Nash", False, "Yes", "20:31")])
check("two shifts: a question naming 12 answers the 12 call...", h3("Hector Nash", "12 Harbour st").get("response") == "Ok")
check("...and not his Bullseye Club call", h3("Hector Nash", "Bullseye Club Darts / Pats") == {}, h3("Hector Nash", "Bullseye Club Darts / Pats"))
h3 = run3([ev("Hector Nash", True, "All okay for Bullseye Club?", "19:00"), ev("Hector Nash", True, "All okay for 12 Harbour st?", "21:00"),
           ev("Hector Nash", False, "Yes", "21:01")])
check("an unanswered question, then one about the other venue: the 'Yes' answers the second only",
      h3("Hector Nash", "Bullseye Club Darts / Pats").get("response") == "" and h3("Hector Nash", "12 Harbour st").get("response") == "Ok",
      (h3("Hector Nash", "Bullseye Club Darts / Pats"), h3("Hector Nash", "12 Harbour st")))
h3 = run3([ev("Milo Padgett", False, "Hi 12 at 22.00 All good", "19:30")])
check("a check-in naming 12 goes to the 12 call only", h3("Milo Padgett", "12 Harbour st").get("how") == "checkin"
      and h3("Milo Padgett", "Bullseye Club Darts / Pats") == {}, (h3("Milo Padgett", "12 Harbour st"), h3("Milo Padgett", "Bullseye Club Darts / Pats")))
h3 = run3([ev("Hector Nash", True, "Ok for bullseye club?", "19:30"),
           ev("Hector Nash", True, "Ok for bullseye club?", "19:30", snapshot=True),
           bubble("Hector Nash", True, "Ok for bullseye club?", "19:30"), ev("Hector Nash", False, "Yes", "19:35")])
check("one question seen three ways (line, reload, open chat) answers one shift, not both",
      h3("Hector Nash", "Bullseye Club Darts / Pats").get("response") == "Ok" and h3("Hector Nash", "12 Harbour st") == {},
      h3("Hector Nash", "12 Harbour st"))
h3 = run3([ev("Idris Kalden", True, "All okay for K9?", "20:55"), ev("Idris Kalden", True, "Can you work Sunday at Bullseye Club?", "21:05"),
           bubble("Idris Kalden", False, "Yes", "21:05")])
check("a 'Yes' known only to the minute, in the same minute as our next message, is not counted",
      h3("Idris Kalden", "K9 / Red Door").get("response") == "", h3("Idris Kalden", "K9 / Red Door"))
h3 = run3([ev("Ruben Corbett", True, "All okay for the meadows?", "18:10"),
           ev("Ruben Corbett", False, "", "18:12", what="reaction", emoji="\U0001F44D", to="All okay for the meadows on Sunday")])
check("a thumbs-up on a longer, different message is not a yes", h3("Ruben Corbett", "The Meadows").get("response") == "")
h3 = run3([ev("Ruben Corbett", False, "My car broke down, might not make it", "18:00"), ev("Ruben Corbett", True, "Ok in 10 mins I'll ring you", "18:01"),
           ev("Ruben Corbett", False, "Ok thanks", "18:02")])
check("our 'Ok in 10 mins I'll ring you' is not a check call, and his 'Ok thanks' is not a check-in",
      h3("Ruben Corbett", "The Meadows").get("response") != "Ok", h3("Ruben Corbett", "The Meadows"))
h3 = run3([ev("Gideon O'Kellan", True, "Can you do Sunday?", "09:00"), ev("Gideon O'Kellan", False, "Sorry lads, sick tonight - can't make it to 12", "15:30")],
          trusted={wa.fold("Gideon O'Kellan")})
check("a trusted senior's 'sick' is caught even early in the day and after a message of ours",
      h3("Gideon O'Kellan", "12 Harbour st").get("how") == "check", h3("Gideon O'Kellan", "12 Harbour st"))

# ---- what eleven exported chats taught (6 Nov: 16,381 messages, 3,381 check calls) ----
print("\nwhat the exported chats taught")
perch = {"venue": "The Perch", "site": "Perch Kilcarran"}
for text, c, want in (("On route thank you", None, "yes"), ("Copied on route", None, "yes"), ("Almost there", None, "yes"),
                      ("On duty", None, "yes"), ("Yes, just outside \U0001F44D", None, "yes"), ("Kkay", None, "yes"),
                      ("All good sorry I missed your message", None, "yes"), ("All good. Sorry phone on silent", None, "yes"),
                      ("All giid", None, "yes"), ("All good \U0001F60A\nHave a nice day \U0001F60A", None, "yes"),
                      ("I'm already at work", None, "yes"), ("Copied all in order thanx", None, "yes"),
                      ("In perch", perch, "yes"), ("Sorry for late reply in perch", perch, "yes"),
                      ("In perch", None, "unclear"),                       # without the call, "perch" is just a word
                      ("On route to K9", perch, "unclear"),                # another venue's name: he reads it
                      ("All good this end 8pm start", perch, "unclear"),   # a start time usually means the roster is wrong
                      ("On route but it's very very far", None, "unclear"),
                      ("On my way, 20 minutes late", None, "no"), ("Traffic is very bad, I'm on the way but I will be late", None, "no"),
                      ("Sorry was driving did not see message", None, "no")):
    got = wa.judge(text, c)
    check("%r -> %s" % (text[:40], want), got == want, got)
# the second batch (7 Nov: 15 guards, 5,258 check calls) - Conrad Calder's "Yes indeed", and times against the roster
perch8 = dict(perch, start=20 * 60)
k9 = {"venue": "K9/Red Door", "site": "K9 / Red Door", "start": 22 * 60 + 30}
for text, c, want in (("Yes indeed", None, "yes"), ("Sure am", None, "yes"), ("Aye", None, "yes"), ("All peaches", None, "yes"),
                      ("All gravy baby", None, "yes"), ("I am", None, "yes"), ("We're on", None, "yes"), ("Yes imdeed", None, "yes"),
                      ("I need it", None, "unclear"), ("I need to know", None, "unclear"),   # "need" is not "indeed"
                      ("I am sick", None, "no"), ("Sure, but I'll be late", None, "no"), ("Not sure", None, "no"),
                      ("Slept through my alarm", None, "unclear"), ("Just up", None, "unclear"),
                      ("All good this end 8pm start", perch8, "yes"),       # the roster's own start: a confirmation
                      ("All good this end 9pm start", perch8, "unclear"),   # another start: the roster may be wrong
                      ("22.30 yes indeed", k9, "yes"), ("Yes indeed\n22.30", k9, "yes"), ("10:30 pm all good", k9, "yes"),
                      ("Yeah, still a 22.30 start?", k9, "unclear"), ("Nessa said 19.30", k9, "unclear"),
                      ("All good 23.00 start", k9, "unclear"), ("Yes, be there in 10 mins", k9, "unclear")):
    got = wa.judge(text, c)
    check("%r -> %s" % (text[:40], want), got == want, got)
for text, want in (("All good ?", wa.ANY), ("All Good?", wa.ANY), ("All good for tonight?", wa.ANY), ("Ok for work today?", wa.ANY),
                   ("All good for 12 o’clock start?", wa.ANY), ("All good ? 21.00", wa.ANY), ("All good? You in K9?", wa.ANY),
                   ("Everything ok? Will you be on time?", wa.ANY),
                   ("All good for 12?", "12"), ("All good for 12 @ 10pm?", "12 @ 10pm"), ("All oky for The Meadows?", "The Meadows"),
                   ("Hi Gavin,\nAll good for Voltmart please ?", "Voltmart please"), ("All\nGood for Ashbury ?", "Ashbury"),
                   ("All good K9 @ 22.00 ?", "K9 @ 22.00"), ("All good Brennans Mill Street?", "Brennans Mill Street"),
                   ("All good? Can you do K9 tomorrow?", None), ("All good \U0001F44D", None), ("All good", None),
                   ("Ok,are you there?", None), ("It’s all good man!", None), ("All good thanks?", None)):
    got = wa.question_venue(text)
    check("%r -> %r" % (text[:40], want), got == want, got)
check("a question naming no venue is for any call", wa.venue_matches(wa.ANY, {"venue": "The Jug", "site": "The Jug"}))

L = "Ruben Corbett", "The Meadows"                       # on at 19:00
h3 = run3([ev("Ruben Corbett", True, "All good ?", "18:10"), ev("Ruben Corbett", False, "Yes", "18:12")])
check("'All good ?' with no venue, answered 'Yes': green", h3(*L).get("response") == "Ok", h3(*L))
h3 = run3([ev("Ruben Corbett", True, "All okay for the meadows?", "18:10"), ev("Ruben Corbett", True, "Voice call, No answer", "18:25"),
           ev("Ruben Corbett", False, "All good sir", "18:31")])
check("ringing him after asking keeps the question open: the reply after the missed call counts", h3(*L).get("response") == "Ok", h3(*L))
h3 = run3([ev("Ruben Corbett", True, "All good for the meadows?", "18:10"), ev("Ruben Corbett", True, "At 7pm", "18:11"),
           ev("Ruben Corbett", False, "All good", "18:14")])
check("the time added a moment later ('At 7pm') is the same question", h3(*L).get("response") == "Ok", h3(*L))
h3 = run3([ev("Ruben Corbett", True, "All good for the meadows?", "18:10"),
           ev("Ruben Corbett", True, "You deleted this message.", "18:11", what="deleted"), ev("Ruben Corbett", False, "Yes", "18:20")])
check("a message of ours deleted straight after does not close the question", h3(*L).get("response") == "Ok", h3(*L))
h3 = run3([ev("Ruben Corbett", True, "All good for the meadows?", "18:10"), ev("Ruben Corbett", True, "Roster for next week", "18:12"),
           ev("Ruben Corbett", False, "All okay", "18:20")])
check("a new subject in between ('Roster for next week') still closes it: that 'All okay' could be for the roster",
      h3(*L).get("response") == "", h3(*L))
h3 = run3([ev("Ruben Corbett", True, "All good for the meadows?", "18:10"), ev("Ruben Corbett", False, "Yeah all good", "18:12"),
           ev("Ruben Corbett", False, "Wrong text sorry", "18:13")])
check("'Yeah all good' then 'Wrong text sorry': taken back, shown, not green",
      h3(*L).get("response") == "" and h3(*L).get("how") == "check" and "Wrong" in h3(*L).get("said", ""), h3(*L))
check("...and marked taken back, with what he said first (so Auto green can turn the roster white again)",
      h3(*L).get("taken_back") is True and h3(*L).get("first") == "Yeah all good", h3(*L))
h3 = run3([ev("Ruben Corbett", True, "All good for the meadows?", "18:10"), ev("Ruben Corbett", False, "All good sir", "18:12"),
           ev("Ruben Corbett", False, "I will be 10 minutes late", "18:40")])
check("'All good' then 'I will be 10 minutes late': shown, so he knows", h3(*L).get("response") == "" and "late" in h3(*L).get("said", ""), h3(*L))
h3 = run3([ev("Ruben Corbett", True, "All good for the meadows?", "18:10"), ev("Ruben Corbett", False, "All good", "18:12"),
           ev("Ruben Corbett", False, "Can you please put me off tomorrow instead of Sunday", "18:20")])
check("'All good' then a request about another day: tonight's yes stands", h3(*L).get("response") == "Ok", h3(*L))
h3 = run3([ev("Ruben Corbett", False, "all good", "17:35"),
           ev("Ruben Corbett", False, "Hello, I've had swelling in my foot since this afternoon, I can't come", "17:38")])
check("a check-in taken back straight after is shown, not green",
      h3(*L).get("response") == "" and "swelling" in h3(*L).get("said", ""), h3(*L))
check("...marked taken back too", h3(*L).get("taken_back") is True and h3(*L).get("first") == "all good", h3(*L))
check("a first reply that was never a yes is not 'taken back'",
      run3([ev("Ruben Corbett", True, "All good for the meadows?", "18:10"), ev("Ruben Corbett", False, "Sorry I cant", "18:12")])(*L).get("taken_back") is None)
check("on hover, a yes taken back says both", "Yeah all good" in wa.story({"name": "Ruben", "heard": {"how": "check", "response": "", "said": "Wrong text sorry",
                                                                                                   "at": "18:13", "taken_back": True, "first": "Yeah all good"}}))
h3 = run3([ev("Hector Nash", True, "All good for tonight?", "18:30"), ev("Hector Nash", False, "Yes", "18:31"),
           ev("Hector Nash", True, "All okay for 12 Harbour st?", "21:00")])
check("two shifts: 'All good for tonight?' answers the first; the second keeps its own question",
      h3("Hector Nash", "Bullseye Club Darts / Pats").get("response") == "Ok"
      and h3("Hector Nash", "12 Harbour st").get("how") == "asked" and h3("Hector Nash", "12 Harbour st").get("time") == "21:00",
      (h3("Hector Nash", "Bullseye Club Darts / Pats"), h3("Hector Nash", "12 Harbour st")))
h3 = run3([ev("Ruben Corbett", True, "All good for the meadows?", "18:10"), ev("Ruben Corbett", False, "Voice call, 1 min", "18:15"),
           ev("Ruben Corbett", False, "On route thank you", "18:20")])
check("his call back is not his answer; the message after it is", h3(*L).get("response") == "Ok" and h3(*L).get("said") == "On route thank you", h3(*L))

# ---- the receiver ----
print("\nthe receiver")
check("not set up: no receiver, and no secret is made up behind the add-on's back", wa.link() is None and wa.start() is None)
wa.make_link()                                       # setup's part: made once, never replaced
token_before = wa.link()["token"]
server = wa.start()
check("it starts on 127.0.0.1", server is not None and server.server_address[0] == "127.0.0.1", server and server.server_address)
check("a second one cannot take the same port", wa.start() is None)
check("it answers the add-on's ping", wa.ping())
token = wa.link()["token"]


def post(body, tok=token, origin="chrome-extension://abcdefghijklmnop", raw=None):
    data = raw if raw is not None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d/wa" % wa.PORT, data=data, method="POST",
                                 headers={"X-Reader-Token": tok, "Origin": origin, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None


now = datetime.datetime.now()
now = now.replace(second=0, microsecond=0) - datetime.timedelta(minutes=2)
live_events = [ev("Rory Orwell", True, "All okay for K9?", now.strftime("%H:%M"), day=now.date()),
               ev("Rory Orwell", False, "Yes all good", (now + datetime.timedelta(minutes=1)).strftime("%H:%M"), day=now.date())]
BEAT = {"pane": True, "rows": 18, "visible": "visible", "at": int(time.time() * 1000)}
status, body = post({"events": live_events, "beat": BEAT})
check("the add-on's events are kept without a legacy-client action", status == 200 and body == {"ok": True, "kept": 2, "action": None}, (status, body))
check("...and read back", [e["text"] for e in wa.events_between(now - datetime.timedelta(minutes=5), now + datetime.timedelta(minutes=5))]
      == ["All okay for K9?", "Yes all good"])
check("...and its heartbeat makes the reader live", wa.live())
check("a wrong secret is refused", post({"events": live_events}, tok="nope")[0] == 404)
check("a web page (not an extension) is refused", post({"events": live_events}, origin="https://example.com")[0] == 404)
check("junk is refused", post(None, raw=b"{not json")[0] == 400)
check("too much at once is refused", post(None, raw=b"x" * (600 * 1024))[0] == 413)
status, body = post({"events": [{"kind": "line", "chat": "x" * 5000, "seen": 1, "text": "y" * 5000, "evil": "<script>"}, "nonsense", 7]})
check("only what the reader may send is kept, cut to size", status == 200 and body["kept"] == 1, body)
with urllib.request.urlopen("http://127.0.0.1:%d/wa/hello?nonce=%s" % (wa.PORT, "ab" * 16), timeout=3) as r:
    hello = json.loads(r.read().decode())
check("it proves it holds the secret before the add-on sends anything (and never shows it)",
      hello.get("proof") == wa.proof(token, "ab" * 16) and token not in json.dumps(hello))
try:
    urllib.request.urlopen("http://127.0.0.1:%d/wa/hello?nonce=xyz" % wa.PORT, timeout=3)
    bad_nonce = False
except urllib.error.HTTPError as e:
    bad_nonce = e.code == 400
check("...and a malformed challenge is refused", bad_nonce)
check("starting the window never replaces the secret the add-on holds", wa.link()["token"] == token_before)
status, body = post({"events": [ev("Rory Orwell", False, "broken \ud83d emoji", now.strftime("%H:%M"), day=now.date()),
                                 ev("Rory Orwell", False, "fine", now.strftime("%H:%M"), day=now.date())]})
check("a message with a broken character is kept escaped and does not block the rest", status == 200 and body["kept"] == 2, (status, body))
for beat, label in ((dict(BEAT, at=int(time.time() * 1000) - 3600 * 1000), "an hour-old heartbeat replayed"),
                    (dict(BEAT, useHere=True), "WhatsApp Web open elsewhere ('Use here')"),
                    (dict(BEAT, pane=False, rows=0), "logged out / no chat list"),
                    (dict(BEAT, paused=True), "searching or filtered")):
    post({"events": [], "beat": beat})
    state, why = wa.reader_state()
    check("not 'on' with %s - and says why" % label, state == "off" and why, why)
post({"events": [], "beat": dict(BEAT, at=int(time.time() * 1000))})
check("...and on again with a real one", wa.live())
server.shutdown()
server.server_close()

# ---- the overlay calllog.build() reads ----
print("\nthe overlay")
os.makedirs(wa.EDITS, exist_ok=True)
nowlog = {"calls": [call("Rory Orwell", "K9 / Red Door", now.hour * 60 + now.minute + 60, "K9/Red Door")],
          "meta": {"date_iso": now.date().isoformat(), "supervisor": "Lena Drayton",
                   "start_min": now.hour * 60 + now.minute - 60, "end_min": now.hour * 60 + now.minute + 600}, "stem": "test-shift"}
check("with the reader live during the shift, the overlay is written", wa.update(nowlog) is True and wa.load_heard("test-shift"))
check("...and Rory's reply is in it", any(v.get("response") == "Ok" for k, v in wa.load_heard("test-shift").items() if k != "__shift__"),
      wa.load_heard("test-shift"))
check("...and doing it again changes nothing", wa.update(nowlog) is False)
henry = dict(nowlog, meta=dict(nowlog["meta"], supervisor="Henry Marlow"), stem="henry-shift")
check("another controller's log is left exactly as it was (just Lena for now)", wa.update(henry) is False
      and not os.path.exists(wa.heard_file("henry-shift")))
wa._beat["at"] = 0
with open(os.path.join(wa.LOGS, "beat.json"), "w") as f:
    json.dump({"at": 0, "status": {}}, f)
fresh = dict(nowlog, stem="no-reader-shift")
check("with the reader never live this shift, nothing is written - the log works as it always has",
      wa.update(fresh) is False and wa.load_heard("no-reader-shift") == {})

past = dict(nowlog, stem="2026-10-24 Lena Drayton", meta=dict(nowlog["meta"], date_iso="2026-10-24", start_min=1080, end_min=1800))
kept_answers = {"__shift__": {"waiting": True, "since": "18:05"}, "x|y|z": {"response": "Ok", "how": "reply", "said": "Yes", "time": "18:10"}}
with open(wa.heard_file(past["stem"]), "w", encoding="utf-8") as f:
    json.dump(kept_answers, f)
check("a shift that is over is final: opening it a week later never rebuilds it from deleted day logs",
      wa.update(past) is False and wa._read_json(wa.heard_file(past["stem"]), {}) == kept_answers)
with open(wa.heard_file("damaged"), "w", encoding="utf-8") as f:
    f.write('{"__shift__": null, "a|b|c": "nonsense"}')
check("a damaged overlay is ignored, never stops the log", wa.load_heard("damaged") == {})
os.makedirs(wa.LOGS, exist_ok=True)
for day in ("2026-10-20", "2026-10-26", "2026-10-27"):
    open(os.path.join(wa.LOGS, day + ".jsonl"), "w").close()
wa.purge(today=datetime.date(2026, 11, 2))
check("the purge keeps exactly 7 days of messages (26 Oct is 7 days before 2 Nov: gone)",
      sorted(n for n in os.listdir(wa.LOGS) if n.endswith(".jsonl") and n < "2026-11") == ["2026-10-27.jsonl"], sorted(os.listdir(wa.LOGS)))
aged = wa._read_json(wa.heard_file(past["stem"]), {})
check("...and takes the guards' words out of old shifts, keeping what was decided",
      "said" not in aged["x|y|z"] and aged["x|y|z"]["response"] == "Ok", aged)

# ---- the real log, built from the roster ----
print("\nthe real log")
roster = calllog.Roster(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "demo", "roster-template.xlsx"))
after = datetime.datetime(2026, 11, 1, 7, 0)
plain = calllog.build(DAY, roster, "Lena Drayton", now=after)
made = [c for c in plain["calls"] if c["call_text"]]
check("without the reader, the log is exactly as before: every made call Ok", made and all(c["response"] == "Ok" for c in made),
      len(made))
first = lambda name: next(c for c in plain["calls"] if c["name"] == name)
ruben, gideon, edgar, carl = first("Ruben Corbett"), first("Gideon O'Kellan"), first("Edgar Haddad"), first("Carl Fielding")
overlay = {"__shift__": {"waiting": True, "since": "18:05"},
           calllog.edit_key(ruben): {"time": "18:10", "how": "reply", "response": "Ok", "said": "Yeap", "at": "18:32", "reply_at": DAY.isoformat()+"T18:32", "reply_source": "bubble"},
           calllog.edit_key(edgar): {"time": "18:02", "how": "voice", "response": "", "said": "voice", "at": "18:20", "reply_at": DAY.isoformat()+"T18:20", "reply_source": "bubble"},
           calllog.edit_key(carl): {"time": "18:31", "how": "reply", "response": "Ok", "said": "Yes", "at": "18:32", "reply_at": DAY.isoformat()+"T18:32", "reply_source": "bubble"}}
os.makedirs(calllog.EDITS, exist_ok=True)
with open(os.path.join(calllog.EDITS, plain["stem"] + " whatsapp.json"), "w", encoding="utf-8") as f:
    json.dump(overlay, f)
calllog.save_edit(plain, carl, "response", "No answer")          # a hand correction
log = calllog.build(DAY, roster, "Lena Drayton", now=after)
got = {c["name"]: c for c in log["calls"]}
check("Ruben answered: Ok, at his actual reply time (18:32)", got["Ruben Corbett"]["response"] == "Ok"
      and got["Ruben Corbett"]["call_text"] == "18:32", (got["Ruben Corbett"]["response"], got["Ruben Corbett"]["call_text"]))
check("Gideon O'Kellan is trusted: Ok with no message", got["Gideon O'Kellan"]["response"] == "Ok")
check("Edgar sent a voice note: no Ok yet - for Lena to listen to", got["Edgar Haddad"]["response"] == ""
      and got["Edgar Haddad"]["heard"].get("how") == "voice")
check("a guard nobody heard from waits (white), never 'No answer'",
      all(c["response"] == "" for c in log["calls"] if c["call_text"] and not c["heard"] and not calllog.whatsapp.trusted(c["name"])
          and not c["edited"]))
check("a hand correction beats WhatsApp (Carl typed No answer)", got["Carl Fielding"]["response"] == "No answer")
check("the log says what WhatsApp gave", any(n.startswith("WhatsApp replies") for n in log["notes"]),
      [n for n in log["notes"] if n.startswith("WhatsApp")])
check("Henry's log for the same day is untouched", all(c["response"] == "Ok" for c in calllog.build(DAY, roster, "Henry Marlow", now=after)["calls"]
                                                       if c["call_text"]))
print("\nwhat the window shows (7 Nov: the words were cut off behind 'replied:')")
mk = lambda **kw: dict({"name": "Ravel Devan", "call_text": "21:32", "response": "", "default_response": "", "heard": {}}, **kw)
check("his own words, in quotes, amber - no 'replied:' eating the narrow column",
      wa.cell_hint(mk(heard={"how": "check", "said": "I will be 10 minutes late", "at": "21:35"})) == ("“I will be 10 minutes late”", "amber"))
check("asked with no captured answer says Waiting; the hover retains when asked", wa.cell_hint(mk(heard={"how": "asked", "time": "21:33"})) == ("Waiting", "ink3")
      and "21:33" in wa.story(mk(heard={"how": "asked", "time": "21:33"})))
check("a voice note / a photo: amber, short (what to do is said on hover)", wa.cell_hint(mk(heard={"how": "voice"})) == ("voice note", "amber")
      and wa.cell_hint(mk(heard={"how": "media"})) == ("photo", "amber") and "listen" in wa.story(mk(heard={"how": "voice", "at": "21:29"})))
check("made, no captured reply, reader on: 'reply not read' in grey",
      wa.cell_hint(mk()) == ("reply not read", "ink3"))
check("no hint once there is a response (Ok from WhatsApp, or typed), nor before the call is made, nor with the reader off",
      wa.cell_hint(mk(response="Ok")) is None and wa.cell_hint(mk(call_text="")) is None and wa.cell_hint(mk(default_response="Ok")) is None)
s = wa.story(mk(response="Ok", heard={"how": "reply", "response": "Ok", "said": "Yes indeed", "at": "21:34"}))
check("on hover, a green from WhatsApp shows the words that made it", "Yes indeed" in s and "21:34" in s and "Ok" in s, s)
s = wa.story(mk(heard={"how": "check", "response": "", "said": "All good this end 9pm start", "at": "21:35"}))
check("...and a message left to read says why", "9pm start" in s and "not a plain yes" in s, s)
check("...a check-in says he was not asked", "without being asked" in wa.story(mk(response="Ok", heard={"how": "checkin", "response": "Ok", "said": "All good", "at": "19:30"})))
check("...nothing heard explains waiting without claiming a roster colour",
      "waiting for confirmation" in wa.story(mk()) and "white" not in wa.story(mk()))
check("...and with the reader off there is nothing to say", wa.story(mk(default_response="Ok", response="Ok")) is None)
preview_only = mk(heard={"how": "check", "direction_unknown": True, "source": "line", "said": "Yes", "at": "21:35"})
check("an unverified preview asks to verify the reply instead of implying it is absent",
      wa.cell_hint(preview_only) == ("verify reply", "amber")
      and "Only a chat-list preview was read; sender unverified." in wa.story(preview_only)
      and "Open this WhatsApp conversation to read the actual reply." in wa.story(preview_only))
for source in ("bubble", None):
    uncertain = mk(heard={"how": "check", "direction_unknown": True, "source": source, "said": "Yes", "at": "21:35"})
    check("unknown bubble or legacy provenance does not falsely claim preview-only capture: %s" % source,
          wa.cell_hint(uncertain) == ("verify reply", "amber") and "sender is unverified" in wa.story(uncertain)
          and "Only a chat-list preview" not in wa.story(uncertain))
check("asked status reports missing capture without claiming no WhatsApp reply exists",
      "No verified reply captured yet" in wa.story(mk(heard={"how": "asked", "time": "21:33"}))
      and "no reply yet" not in wa.story(mk(heard={"how": "asked", "time": "21:33"})))
check("empty capture directs review without claiming WhatsApp is empty",
      "No verified reply captured yet" in wa.story(mk()) and "Open this WhatsApp conversation" in wa.story(mk())
      and "nothing on WhatsApp" not in wa.story(mk()))
# 8 Nov clarification supersedes the old question-time floor: use this guard's actual reply,
# even before the scheduled mark. The question remains context, never the displayed reply clock.
late = next(c for c in plain["calls"] if c["start"] % 60 == 30 and c["start"] >= 21 * 60)
fmt = lambda m: "%02d:%02d" % divmod(m % 1440, 60)
for asked in (late["start"] - 93, late["start"] - 50):
    with open(os.path.join(calllog.EDITS, plain["stem"] + " whatsapp.json"), "w", encoding="utf-8") as f:
        json.dump({"__shift__": {"waiting": True, "since": "18:05"},
                   calllog.edit_key(late): {"time": fmt(asked), "how": "reply", "response": "Ok", "said": "Yes", "at": fmt(asked + 1),
                                           "reply_at": DAY.isoformat()+"T"+fmt(asked+1), "reply_source": "bubble"}}, f)
    row = next(c for c in calllog.build(DAY, roster, "Lena Drayton", now=after)["calls"] if calllog.edit_key(c) == calllog.edit_key(late))
    check("the guard's actual reply %s replaces generation, even before the mark" % fmt(asked+1),
          row["call_text"] == fmt(asked+1) and row["due"] == fmt(late["start"]-calllog.LEAD), row["call_text"])
os.remove(os.path.join(calllog.EDITS, plain["stem"] + " whatsapp.json"))
# 14 Nov (Lena): "fill in rest of shift ... should fill all the okay as well - it leaves the blanks for me these days
# because of the whatsapp thing". With the shift filled in, a call only WAITING (nothing heard / asked, no answer)
# gets Ok. 20 Nov: so does one where the guard said something (sick, a voice note, a yes taken back) - "fill up all".
quiet, sick_one, asked_one = [c for c in plain["calls"] if c["call_text"] and not calllog.whatsapp.trusted(c["name"])][:3]
with open(os.path.join(calllog.EDITS, plain["stem"] + " whatsapp.json"), "w", encoding="utf-8") as f:
    json.dump({"__shift__": {"waiting": True, "since": "17:00", "since_at": DAY.isoformat() + "T17:00:00"},
               calllog.edit_key(sick_one): {"time": "18:20", "how": "check", "response": "", "said": "Sorry I'm sick tonight", "at": "18:25"},
               calllog.edit_key(asked_one): {"time": "18:20", "how": "asked", "response": ""}}, f)
rows = lambda: {calllog.edit_key(c): c for c in calllog.build(DAY, roster, "Lena Drayton", now=after)["calls"]}
before_fill = rows()
check("reader on, shift not filled in: waiting calls are blank (as before)",
      before_fill[calllog.edit_key(quiet)]["response"] == "" and before_fill[calllog.edit_key(asked_one)]["response"] == "")
calllog.fill_ahead(plain, True)
filled = rows()
check("'Fill in the rest of the shift': a call only waiting on WhatsApp gets Ok", filled[calllog.edit_key(quiet)]["response"] == "Ok",
      filled[calllog.edit_key(quiet)]["response"])
check("...so does one asked with no answer yet", filled[calllog.edit_key(asked_one)]["response"] == "Ok")
check("...and so does the guard who said 'sick tonight' (20 Nov: 'fill up all ... even if it has error')",
      filled[calllog.edit_key(sick_one)]["response"] == "Ok", filled[calllog.edit_key(sick_one)]["response"])
calllog.fill_ahead(plain, False)
check("...and switching it off puts the waiting blanks back", rows()[calllog.edit_key(quiet)]["response"] == "")
check("...the sick one's blank too", rows()[calllog.edit_key(sick_one)]["response"] == "")
# 20 Nov (Lena): "if autoread is off just do it plainly" - with the replies off, the log is the plain one of before
# WhatsApp: the generated Ok everywhere, nothing heard, no hint, no "reply not read".
calllog.whatsapp.set_replies(False)
try:
    bare = rows()
    three = [bare[calllog.edit_key(c)] for c in (quiet, sick_one, asked_one)]
    check("Auto read off: every made call has the generated Ok, even the sick one and the one asked",
          all(c["response"] == "Ok" and c["default_response"] == "Ok" for c in three), [c["response"] for c in three])
    check("...nothing WhatsApp heard is carried, and no cell hint", all(not c.get("heard") for c in bare.values())
          and all(not calllog.whatsapp.cell_hint(c) for c in bare.values()))
finally:
    calllog.whatsapp.set_replies(True)
check("...and back on, the replies decide again", rows()[calllog.edit_key(sick_one)]["response"] == "")
os.remove(os.path.join(calllog.EDITS, plain["stem"] + " whatsapp.json"))
calllog.save_edit(plain, carl, "response", None)
edgar_now = next(c for c in log["calls"] if c["name"] == "Edgar Haddad")
check("typing Ok in a call waiting for WhatsApp is stored as Ok (it used to do nothing at all)",
      edgar_now["default_response"] == "" and calllog.response_to_save(edgar_now, "ok") == "Ok")
check("...while with no reader, Ok typed is still just 'the generated Ok'", calllog.response_to_save(first("Edgar Haddad"), "OK") is None)
check("...and a blank puts the generated one back", calllog.response_to_save(edgar_now, "  ") is None)


def with_overlay(data, when=after):
    with open(os.path.join(calllog.EDITS, plain["stem"] + " whatsapp.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)
    return {c["name"]: c for c in calllog.build(DAY, roster, "Lena Drayton", now=when)["calls"]}


early = with_overlay({"__shift__": {"waiting": True, "since": "18:05"},
                      calllog.edit_key(edgar): {"time": "17:35", "how": "asked", "response": ""}})
check("a question sent before Lena came on (17:35) does not move the call outside his shift",
      early["Edgar Haddad"]["call_text"] == edgar["call_text"] and not early["Edgar Haddad"]["heard_time"],
      early["Edgar Haddad"]["call_text"])
late = with_overlay({"__shift__": {"waiting": True, "since": "21:00", "since_at": "2026-10-31T21:00:00"}})
before9 = [c for c in late.values() if c["call_text"] and c["call"] < 21 * 60]
after9 = [c for c in late.values() if c["call_text"] and c["call"] >= 21 * 60 and not calllog.whatsapp.trusted(c["name"])]
check("the reader came on at 21:00: calls made before it keep their Ok (it never saw them)",
      before9 and all(c["response"] == "Ok" for c in before9), len(before9))
check("...and calls after it wait for a real reply", after9 and all(c["response"] == "" for c in after9), len(after9))
birch = next(c for c in plain["calls"] if "Birch" in c["name"])
queued = with_overlay({"__shift__": {"waiting": True, "since": "21:00", "since_at": "2026-10-31T21:00:00"},
                       calllog.edit_key(ruben): {"time": "18:10", "how": "check", "response": "", "said": "Sorry, I'm sick, can't make it"},
                       calllog.edit_key(birch): {"time": "20:56", "how": "asked", "response": ""}})
check("a refusal heard before the window opened (the add-on queued it) still shows - never the old Ok",
      queued["Ruben Corbett"]["response"] == "", queued["Ruben Corbett"]["response"])
check("...and a question whose real time is before the window opened does not make the call Ok",
      queued[birch["name"]]["response"] == "", queued[birch["name"]]["response"])
sick = with_overlay({"__shift__": {"waiting": True, "since": "18:05"},
                     calllog.edit_key(gideon): {"time": "17:10", "how": "check", "response": "", "said": "sick tonight"}})
check("a trusted senior who said he is sick is not assumed Ok", sick["Gideon O'Kellan"]["response"] == "")
mid = calllog.build(DAY, roster, "Lena Drayton", now=datetime.datetime(2026, 10, 31, 20, 0))
calllog.fill_ahead(mid, True)
ahead = with_overlay({"__shift__": {"waiting": True, "since": "18:05"}}, when=datetime.datetime(2026, 10, 31, 20, 0))
filled = [c for c in ahead.values() if c["ahead"]]
check("'Fill in the rest of the shift' still fills every call ahead with Ok", filled and all(c["response"] == "Ok" for c in filled),
      len(filled))
calllog.fill_ahead(mid, False)
os.remove(os.path.join(calllog.EDITS, plain["stem"] + " whatsapp.json"))

# ---- drafts ----
print("\ndrafts")
check("an Irish number the Irish way becomes WhatsApp's form", wa.number("087 123 4567") == "353871234567")
check("...and +353 / 00353 too", wa.number("+353 87 123 4567") == wa.number("00353871234567") == "353871234567")
check("the text is fully encoded, so & ? # cannot cut it short",
      wa.draft_link("0871234567", "All okay for K9 & Co? #1") == "whatsapp://send?phone=353871234567&text=All%20okay%20for%20K9%20%26%20Co%3F%20%231")
opened = []
with open(wa.SETTINGS, "w", encoding="utf-8") as f:
    json.dump({"draft_to": ["Lena Drayton"], "phones": {"Lena Drayton": "087 000 0000"}}, f)
wa.draft("Lena Drayton", "All okay for The Jug?", opener=opened.append)
check("a draft for Lena opens his chat with the text in the box (nothing is sent)", opened == [
      "whatsapp://send?phone=353870000000&text=All%20okay%20for%20The%20Jug%3F"], opened)
try:
    wa.draft("Ruben Corbett", "All okay for the meadows?", opener=opened.append)
    refused = False
except ValueError:
    refused = True
check("anyone not on the draft list is refused - for now only Lena", refused and len(opened) == 1)
check("the usual question is written from the call", wa.check_text({"venue": "The Jug", "site": "The Jug"}) == "All okay for The Jug?")

# ---- recovered 7 Nov review: a courteous reply is not necessarily a confirmation ----
evening = {"venue": "The Jug", "site": "The Jug", "start": 20 * 60}
for text in ("Sorry for late reply", "Sorry was driving", "Sorry for not answering on time",
             "Thank you for asking", "Good night", "Goodnight", "Have a good night", "Enjoy your shift", "30", "100",
             "Yes at 11", "All good 9", "All good 8am start", "All good 08:00", "All good 24:10",
             "All good 9:75", "All good 13pm", "All good 0am", "\U0001F44D?"):
    check("review: no confirmation from %r" % text, wa.judge(text, evening) != "yes", wa.judge(text, evening))
for text in ("All good, sorry for late reply", "All good. Have a good night", "On my way", "Of course",
             "All good 8pm start", "All good 20:00", "100% all good", "Goodnight all good"):
    check("review: clear confirmation preserved %r" % text, wa.judge(text, evening) == "yes", wa.judge(text, evening))
for text in ("Ok I'm away tonight", "Ok im of tonight", "Actually I'm sick, can someone cover me?", "I can't come?"):
    check("review: absence is negative %r" % text, wa.judge(text, evening) == "no", wa.judge(text, evening))
for text, start, want in (("All good 12am", 0, "yes"), ("All good 12pm", 0, "unclear"),
                          ("All good 12pm", 12 * 60, "yes"), ("All good 12am", 12 * 60, "unclear"),
                          ("All good 00:30", 24 * 60 + 30, "yes"), ("All good 12:30pm", 24 * 60 + 30, "unclear")):
    check("review: full clock %r for %s" % (text, start), wa.judge(text, dict(evening, start=start)) == want)
check("review: numeric venue and correct time still confirm with roster context",
      wa.judge("Hi 12 at 22.00 All good Have a good night", {"venue": "12 Harbour St", "start": 22 * 60}) == "yes")
valid_reply = [ev("Ruben Corbett", True, "All good for the meadows?", "18:10"),
               ev("Ruben Corbett", False, "All good", "18:12")]
for shown in ("45:12", "24:10", "9:75", "99:99", "Yesterday", ""):
    damaged = dict(ev("Unrelated Person", False, "Yes", "18:11", snapshot=True), shown=shown)
    check("review: invalid snapshot %r is rejected" % shown, wa._clean(damaged) is None and wa._at(damaged) is None)
    check("review: invalid unrelated clock does not block tonight's replies %r" % shown,
          run3(valid_reply + [damaged])(*L).get("response") == "Ok")
for stamp in (float("nan"), float("inf"), 10 ** 100):
    damaged = dict(valid_reply[1], seen=stamp)
    check("review: damaged seen timestamp is rejected", wa._clean(damaged) is None and wa._at(damaged) is None)
damaged_bubble = dict(valid_reply[1], kind="bubble", date="31/99/2026", shown="18:12")
check("review: invalid historic bubble does not become a live yes", wa._clean(damaged_bubble) is None)
check("review: invalid heartbeat timestamp cannot claim reader is live", "at" not in wa._clean_beat({"at": float("inf")}))

# Reaction snapshots are retained across the actual isolated append/read boundary.
react_seen = int(datetime.datetime(2026, 11, 7, 22, 0).timestamp() * 1000)
react_parent = dict(kind="bubble", chat="Alex Example", ours=True, direction="outgoing", group=False,
                    text="All okay for Example Hall?", what="text", date="07/11/2026", shown="18:00",
                    seen=react_seen, id="synthetic-reaction-parent", read_id="d" * 32, view_id="e" * 32, i=1)
react_item = dict(parent_id=react_parent["id"], emoji="👍", actor="contact", actor_name=react_parent["chat"], at=None)
present = dict(react_parent, reactions={"complete": True, "items": [react_item]})
removed = dict(react_parent, seen=react_seen + 1000, reactions={"complete": True, "items": []})
check("reaction state changes both survive isolated event recording", wa.record([present, removed]) == 2)
reloaded = [e for e in wa.events_between(datetime.datetime(2026, 11, 7, 21, 59), datetime.datetime(2026, 11, 7, 22, 2))
            if e.get("id") == react_parent["id"]]
check("event dedup does not erase same-parent reaction removal", len(reloaded) == 2
      and reloaded[0]["reactions"]["items"][0]["at"] is None and reloaded[1]["reactions"]["items"] == [], reloaded)
react_call = dict(name="Alex Example", venue="Example Hall", site="Example Hall", start=1200, shift="20:00-close")
react_log = dict(meta={"date_iso": "2026-11-07"}, calls=[react_call])
react_commits = {"d" * 32: dict(chat=react_parent["chat"], since=react_seen - 6 * 3600000, created=react_seen, completed=react_seen + 60000)}
reloaded_result = wa.heard(react_log, reloaded, read_commits=react_commits).get(calllog.edit_key(react_call), {})
check("newest empty reaction remains removed after disk reload", reloaded_result.get("response") == ""
      and reloaded_result.get("reaction_removed") and reloaded_result.get("taken_back"), reloaded_result)

print("\n%d of %d checks passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
