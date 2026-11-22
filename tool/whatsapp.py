"""The guards' WhatsApp replies to check calls - read, never sent.

The Chrome add-on in ..\\reader watches WhatsApp Web's chat list in the control room's own Chrome and hands
every change to this module over a private connection on this PC (127.0.0.1, with a secret token). This
module keeps those changes in a small log per day (cache\\whatsapp\\YYYY-MM-DD.jsonl) and works out, for
each call on a controller's log, "our question, then their first reply":

- a reply from the fixed list (Yes, Ok, Yeap, All good, Copied, Confirm, NTR, a thumbs-up...) answers it Ok;
- a guard who checks in without being asked ("Hi 12 at 22.00 All good") is Ok too - the user's choice;
- anything else (a voice note, a photo, "sorry I can't", a question back) is shown for the controller to
  judge, and the name stays white;
- nothing seen: the name stays white ("waiting"). Silence is never turned into "No answer".

What it never does: send, type, click or change anything in WhatsApp; write to the roster; touch the
keyboard, mouse or clipboard. It starts no program - the receiver is a thread of the window that uses it.
The one thing that opens anything is draft(): a WhatsApp link, only when asked, only to a number on the
draft list, and it fills the message box - a person presses Send.

What it hands to calllog.build() is one small file per shift, edits\\<stem> whatsapp.json, which only exists
once the reader has been live during that shift. Without it, the log works exactly as it always has.

    python whatsapp.py setup                  make the add-on's config (port and secret) in ..\\reader
    python whatsapp.py status                 is the receiver up, when did the add-on last report
    python whatsapp.py match sat [--name lena]   what WhatsApp says about that shift's calls
    python whatsapp.py draft "Lena Drayton" --text "All okay for The Jug?"
"""
import argparse
import datetime
import difflib
import functools
import hmac
import http.server
import json
import os
import re
import secrets
import socket
import socketserver
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(HERE, "cache")
EDITS = os.path.join(HERE, "edits")
LOGS = os.path.join(CACHE, "whatsapp")                 # what the add-on reported, a file per day
LINK = os.path.join(CACHE, "whatsapp-link.json")       # the port and the secret the add-on must show
SETTINGS = os.path.join(HERE, "whatsapp.json")         # the user's choices (his data, never cleared)
NAMES = os.path.join(HERE, "whatsapp-names.json")      # chat names taught by hand -> roster names (his data)
READER = os.path.join(ROOT, "reader")                  # the Chrome add-on
PORT = 8765
KEEP_DAYS = 7                                          # replies are personal data: a week, then gone
LIVE_SECONDS = 150                                     # a heartbeat older than this means the reader is off
DEFAULT_BETA = os.path.join(CACHE, "whatsapp-beta.json")
BETA = DEFAULT_BETA                                  # tests may point this at an isolated private fixture
import wa_bridge
import wa_beta
_actions = wa_bridge.ActionQueue()
DEFAULTS = {
    "controllers": ["Lena Drayton"],                   # whose logs wait for a reply ("just for Lena right now")
    "trusted": ["Gideon O'Kellan", "Ernest Chandler", "Glen Haskell"],   # never messaged: Ok with no reply
    "draft_to": ["Lena Drayton"],                      # draft() opens a message only for these, for now
    "phones": {},                                      # name -> number, international digits (353...)
    "confirmation_emojis": {},                         # exact full roster name -> user-confirmed standalone replies
}

# --- reading the messages -------------------------------------------------------------------------------
# Our check-call question, as it is really written: "All okay for The Jug?", "Ok for Nona's?", "All good for
# 12 Harbour St?", "Is everything okay for the jug at 5pm" - and sometimes with a shift offer after it.
QUESTION = re.compile(r"(?:^|[\s,.!])(?:is\s+)?(?:all\s+(?:good|ok|okay|oky|okey|right|fine)|everything\s+(?:good|ok|okay|oky|okey|alright|fine)"
                      r"|ok|okay|oky)\s+(?P<prep>for|at|in)\s+(?P<venue>[^?.!\n]{2,60})", re.I)
# The question as often really sent, with no venue at all: "All good ?", "All okay?", "Everything ok?" - and "All good
# for tonight?", "Ok for work today?", "All good for 12 o'clock start?". Read off eleven exported chats (3,340 check
# calls, Nov 2026): about one in eighteen named no venue. Such a question is for whatever he is on tonight.
BARE = re.compile(r"^(?:is\s+)?(?:all|everything)\s+(?:good|ok|okay|oky|okey|alright|right|fine)$")
NO_VENUE = {"work", "today", "tonight", "tonite", "your", "the", "shift", "yea", "yeah", "please", "pls", "now", "start",
            "oclock", "this", "evening", "morning", "o", "clock", "am", "pm"}
ANY = "*"                                              # question_venue() for a question that names no venue
GREETING_LINE = re.compile(r"^(?:hi|hello|hey|good\s+(?:morning|afternoon|evening))(?:\s+\w+){0,2}$")
# Deciding "yes" by a list of bad words always misses one ("Ok, I don't think I can make it", "Ok, nu pot veni",
# "Good evening"). So it works the other way: a reply is a yes only when EVERY word in it is a known yes-or-filler
# word and at least one says yes. Anything else - another language, a "but", a question - goes to the controller.
NEGATIVE = re.compile(r"\b(?:sorry|cant|cannot|unable|sick|wont|dont|doesnt|didnt|isnt|arent|wasnt|aint|havent|hasnt|"
                      r"couldnt|wouldnt|nah|nope|never|problem|issue|emergency|late|delay(?:ed)?|not|cancel(?:led)?|off|"
                      r"hospital|leave|no|nu|nao|nahi|nie|ne)\b", re.I)
YES_WORDS = {"yes", "yess", "yesss", "yeah", "yea", "yeh", "yep", "yeap", "yup", "ya", "yh", "ok", "okay", "okey", "oky",
             "okk", "k", "kk", "alright", "fine", "grand", "copied", "copy", "confirm", "confirmed", "ntr", "ready", "sorted",
             "perfect", "good", "here", "onsite", "arrived", "y", "kkay", "goo", "giid", "goid", "godd", "gud", "goood",
             "reached", "copiied",
             # Conrad Calder's and others' own ways of saying it (the second batch of exported chats, 7 Nov)
             "indeed", "aye", "ye", "yee", "yaha", "yahaa", "sure", "brilliant", "wonderful", "lovely", "fantastic",
             "fabulous", "peaches", "gravy", "dandy", "rosy"}
FILLER = {"all", "im", "i", "am", "is", "its", "it", "everything", "we", "are", "thanks", "thank", "you", "thx", "ty",
          "cheers", "sir", "bro", "brother", "boss", "mate", "lads", "man", "buddy", "boys", "hi", "hello", "hey", "dear",
          "and", "so", "very", "too", "there", "now", "tonight", "today", "on", "at", "in", "the", "a", "for", "this", "end", "just",
          "site", "venue", "door", "doors", "shift", "see", "later", "great", "well", "set", "safe", "been", "got", "hope",
          "youre", "getting", "my", "way", "coming", "heading", "soon", "already", "also", "u", "ur", "your", "be", "will",
          "to", "be", "absolutely", "100", "x", "xx", "lol", "thankyou", "tnx", "class", "sound",
          "al", "as", "endx", "thanx", "thankx", "thnk", "thnkx", "nice", "pretty", "sweet", "gorgeous", "baby", "guys",
          "pal"}
# Things that say "I'm there / on my way / all fine" in more words - every one of them read in the real chats ("On
# route thank you" alone came back 42 times). Taken out before the bad-word check, so "Sorry for late reply, all
# good" is a yes but "On my way, 20 minutes late" is still a no. Politeness and apologies are separate: they
# can accompany a confirmation, but never supply one themselves. Times must agree with the roster.
REASSURING = re.compile(r"\b(?:no\s+issues?|no\s+problems?|no\s+bother|nothing\s+to\s+report|not\s+a\s+problem|"
                        r"on\s+(?:my|the|his)\s+way|on\s+way|on\s+route|en\s+route|on\s+(?:the\s+)?site|on\s+duty|"
                        r"almost\s+(?:there|here|onsite|on\s+site|arrived)|nearly\s+there|just\s+outside|"
                        r"already\s+(?:at\s+)?(?:here|there|in|onsite|on\s+site|work|site)|on\s+route\s+to\s+(?:work|site)|"
                        r"on\s+location|^(?:yes\s+|yeah\s+|sure\s+)?i\s+am$|^we\s?re\s+on$|(?:all\s+)?in\s+order|as\s+rostered|of\s+course)\b")
POLITE = re.compile(r"\b(?:have\s+a\s+(?:good|nice|great|lovely)\s+(?:night|one|evening|day|afternoon|weekend|shift)|"
                        r"enjoy\s+your\s+(?:night|day|evening|shift)|thank\s+you\s+for\s+asking|good\s*night|"
                        r"sorry\s+(?:for\s+)?(?:the\s+)?(?:late|delayed?)\s+(?:reply|response|answer)|"
                        r"sorry\s+(?:i\s+)?missed\s+your\s+(?:message|text|msg)|sorry\s+(?:i\s+)?was\s+driving|"
                        r"sorry\s+(?:my\s+)?phone\s+(?:was\s+)?on\s+silent|sorry\s+for\s+not\s+answering(?:\s+on\s+time)?)\b")
GREETING = re.compile(r"\bgood\s*(?:morning|afternoon|evening|day)\b")
ABSENT = re.compile(r"\b(?:im|i\s+am|we\s+are|were)\s+(?:away|of)\b|\b(?:away|absent)\s+(?:tonight|today|now)\b")
YES_EMOJI = ("\U0001F44D", "\U0001F44C", "✅", "✔")     # thumbs up, OK hand, check mark (button), check mark
NOISE = {"both", "ind", "inde", "new", "newest", "number", "nights", "indeed", "door", "do", "not", "send", "roster",
         "no", "trans", "d", "1", "the", "site", "phone"}


def fold(text):
    """Lower case, no accents, no apostrophes or commas inside words ("Y,es" -> "yes", "can't" / "can´t" -> "cant")."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"(?<=\w)[,'’`](?=\w)", "", text.lower())
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


def _fold_keep_marks(text):
    # accents dropped by NFKD lose the apostrophe forms too; do the apostrophes on the raw text first
    raw = re.sub(r"(?<=\w)\s?[´′’'`](?=\w)", "", (text or "").lower())
    return fold(raw)


def _unreadable_text(text):
    """Letters/numbers discarded by ASCII folding are unknown content, not harmless empty text.

    Decomposed Latin accents retain their base letters; emoji and punctuation are not script words.
    """
    return any(not ch.isascii() and ch.isalnum() for ch in unicodedata.normalize("NFKD", text or ""))


def has_yes_emoji(text):
    return any(e in (text or "") for e in YES_EMOJI)


def _known(word, vocab):
    if word in vocab:
        return True
    # Only learned affirmative/thanks typos are fuzzy. Filler such as "way" must not admit "away".
    targets = vocab & (YES_WORDS | {"thanks", "thank", "thankyou", "cheers", "getting"})
    return len(word) >= 4 and any(abs(len(word) - len(v)) <= 1 and difflib.SequenceMatcher(None, word, v).ratio() >= 0.8
                                  for v in targets if len(v) >= 3)


TIME = re.compile(r"\b(\d{1,2})(?:\s+(\d{2}))?\s*(am|pm|h|hrs?)\b|\b(\d{1,2})\s+(\d{2})\b")


def _times(words):
    """Explicit times in a folded reply, in 24-hour minutes. Invalid clocks return -1 and cannot match.

    A bare hour without am/pm or minutes is left for a person; it can also be a venue number.
    """
    out = []
    for m in TIME.finditer(words):
        h, mi = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(5))
        h, mi = int(h), int(mi or 0)
        suffix = m.group(3)
        if suffix in ("am", "pm"):
            value = ((h % 12) + (12 if suffix == "pm" else 0)) * 60 + mi if 1 <= h <= 12 and mi < 60 else -1
        else:
            value = h * 60 + mi if h < 24 and mi < 60 else -1
        out.append((value, m.span()))
    return out


def judge(text, call=None):
    """What a guard's message says, by fixed rules: 'yes', 'no' (something to read) or 'unclear'.

    With the call, the words of its own venue count as filler too: "In perch", "Onsite at K9", "All good reached
    kilbride". Another venue's name is still a word it does not know."""
    if _unreadable_text(text):
        return "unclear"
    if has_yes_emoji(text) and not fold(text):
        return "unclear" if "?" in (text or "") else "yes"  # a bare thumbs-up, not a question
    words = _fold_keep_marks(text)
    if not words and not has_yes_emoji(text):
        return "unclear"
    calm = REASSURING.search(words)
    rest = GREETING.sub(" ", POLITE.sub(" ", REASSURING.sub(" ", words)))
    if NEGATIVE.search(rest) or ABSENT.search(rest):
        return "no"
    if "?" in (text or ""):
        return "unclear"                               # a negative question above still withdraws an earlier yes
    here = set()
    if call:
        here = set(fold("%s %s" % (call.get("venue", ""), call.get("site", ""))).split()) - {"the"}
    here -= {"st", "street", "bar", "pub", "club", "and", "of", "a"}
    if call and call.get("start") is not None:
        # A time he writes is checked against the roster's start: the same time ("22.30 yes indeed", "All good this
        # end 8pm start" for an 8pm start) is a yes; another time ("9pm start" for an 8pm, "Nessa said 19.30") is for
        # the controller to read - it usually means the roster is wrong.
        start = int(call["start"]) % 1440
        found = _times(rest)
        if any(t != start for t, _ in found):
            return "unclear"
        for _, (a, b) in reversed(found):
            rest = rest[:a] + " " + rest[b:]
        rest = re.sub(r"\bstart(?:ing|s)?\b", " ", rest) if found else rest
    if here and re.search(r"\b(?:in|at|reached|arrived)\s+(?:the\s+)?(?:%s)\b" % "|".join(map(re.escape, sorted(here))), rest):
        calm = True                                    # "In perch", "Onsite at K9": he is at his own venue
    left = [w for w in rest.split() if w not in here]
    if not all(_known(w, YES_WORDS | FILLER) for w in left):
        return "unclear"                               # a word it does not know: he reads it, it does not guess
    if calm or has_yes_emoji(text) or any(_known(w, YES_WORDS) and w not in FILLER for w in left):
        return "yes"
    return "unclear"


OTHER_DAY = re.compile(r"\b(?:tom+or+ow+|tmr+w?|tmrw|2mo?r+o?w?|tom|next|weekend|monday|tuesday|wednesday|thursday|friday|"
                       r"saturday|sunday|mon|tues?|wed|thurs?|fri|sat|sun|available|cover|extra|swap|instead)\b", re.I)


def question_venue(text):
    """The venue named in our check-call question, or None if the message is not one.

    Only the first sentence counts: "All okay for Lucerne? And can you work in Bullseye Club tomorrow" is a
    check call with an offer after it, but "Would it be ok for you to cover K9 tomorrow?", "All good for Saturday?"
    and "Ok in 10 mins I'll ring you" are not - a "Yes" to those must never turn tonight's name green. Which venue
    it names is checked against the call by heard() (venue_matches).
    """
    if _unreadable_text(text):
        return None
    text = (text or "").replace("‎", "").strip()
    text = re.sub(r"(?i)\b(all|everything)\s*\n\s*(?=good|ok)", r"\1 ", text)         # "All\nGood for Ashbury ?"
    lines = text.split("\n")
    while len(lines) > 1 and GREETING_LINE.match(fold(lines[0])):                    # "Hi Gavin,\nAll good for ..."
        lines = lines[1:]
    text = "\n".join(lines)
    first = re.split(r"[?\n]", text, maxsplit=1)[0]
    if OTHER_DAY.search(first) or re.search(r"\b(?:would|are|r)\s+(?:you|u)\b", first, re.I):
        return None
    m = QUESTION.search(" " + first)
    if m:
        venue = m.group("venue").strip()
        words = fold(venue).split()
        # "for tonight", "for work today", "for 12 o'clock start": a question, but it names no venue
        # (a bare number is a time only beside "o'clock" or "start": "for 12 ?" is 12 Harbour St)
        clock = any(w in ("oclock", "clock", "start") for w in words)
        if m.group("prep").lower() == "for" and words and all(
                w in NO_VENUE or re.fullmatch(r"\d+(?:am|pm|h|hrs?)", w) or (clock and w.isdigit()) for w in words):
            return ANY
        # not a venue: "you", "home", or a time ("Ok in 10 mins I'll ring you", "Ok at 22:30 then") - but "12 Harbour St" is
        if re.match(r"(?:you|u|home)\b|\d+\s*(?:mins?|minutes?|hours?|hrs?|h|pm|am)\b|\d{1,2}[:.]\d{2}", venue, re.I):
            return None
        return venue
    # "All good ?" - asked, with a question mark, and nothing in the message about another day or an offer
    if "?" in text[len(first):len(first) + 1] and BARE.match(fold(first)) and not OTHER_DAY.search(text) \
            and not re.search(r"\b(?:would|are|r|can|could)\s+(?:you|u)\b", text, re.I):
        return ANY
    # "All good K9 @ 22.00 ?", "All good Brennans Mill Street?" - the venue with no "for"
    m = re.match(r"\s*(?:all|everything)\s+(?:good|ok|okay|oky)\s+(?P<venue>[^?\n]{2,60})\?", text, re.I)
    if m and not OTHER_DAY.search(text) and not re.match(r"(?:you|u|thanks?|thank|man|sir|bro|mate|here|this)\b",
                                                         m.group("venue").strip(), re.I):
        return m.group("venue").strip()
    return None


def venue_matches(named, call):
    """Does the venue named in a question mean this call's venue? ("K9" -> "K9 / Red Door", "the meadows" ->
    "The Meadows", "Voltmart Elmhurst" -> "ELMHURST"). A question that names no venue (ANY) is for any call."""
    if named == ANY:
        return True
    stop = {"the", "st", "street", "bar", "pub", "club", "and", "of"}
    said = set(fold(named).split()) - stop
    have = set(fold("%s %s" % (call.get("venue", ""), call.get("site", ""))).replace("o connell", "oconnell").split()) - stop
    if not said or not have:
        return False
    return any(w in have or any(len(w) >= 4 and difflib.SequenceMatcher(None, w, h).ratio() >= 0.85 for h in have) for w in said)


# --- names: WhatsApp chat names are the phone's contact names ------------------------------------------
def tokens(name):
    words = fold(name).split()
    return [w for w in words if w not in NOISE and not re.fullmatch(r"[a-z]{3,4}\d{2}", w) and not w.isdigit()]


def name_score(person, chat):
    """How well a roster name is covered by a chat name: every roster word found (allowing small slips)."""
    return _covered(tokens(person), tokens(chat))


def match_chats(chats, people, taught=None):
    """{chat name: roster name} for the people on a log. A doubtful match is left out: a wrong person going
    green is the one failure that matters, so anything unsure stays white for the controller.

    Doubt runs both ways. One chat that could be two people is nobody's; and one person reached by two chats
    (a group renamed after him, a second contact with the same names, a manager called "Ellery Lorcan")
    is matched to neither, unless a chat was taught by hand - otherwise a "Yes" in the wrong chat answers for him.
    """
    taught = taught or {}
    out, guessed = {}, {}
    want = {p: tokens(p) for p in people}              # worked out once: this runs every few seconds
    # Chat labels repeatedly share words (and roster names share surnames). Keep exact SequenceMatcher
    # results only for this invocation: bounded work without caching identity decisions across roster edits.
    compare = functools.lru_cache(maxsize=8192)(lambda a, b: difflib.SequenceMatcher(None, a, b).ratio())
    exact_names = {}
    for person in want:
        exact_names.setdefault(wa_beta.normalize_name(person), set()).add(person)
    for chat in chats:
        if chat in taught:
            if taught[chat] in people:
                out[chat] = taught[chat]
            else:
                equivalent = exact_names.get(wa_beta.normalize_name(taught[chat]), set())
                if len(equivalent) == 1:
                    out[chat] = next(iter(equivalent))  # retain the actual roster spelling, never fuzzy-match a taught target
            continue
        have = tokens(chat)
        ranked = sorted(((_covered(want[p], have, compare), p) for p in people), reverse=True)
        if not ranked:
            continue
        top, who = ranked[0]
        second = ranked[1][0] if len(ranked) > 1 else 0.0
        if top >= 0.8 and top - second >= 0.15:
            guessed.setdefault(who, []).append(chat)
    taught_people = set(out.values())
    for who, found in guessed.items():
        if len(found) == 1 and who not in taught_people:
            out[found[0]] = who
    return out


def _covered(want, have, compare=None):
    """name_score() for words already split - every roster word found in the chat name, allowing small slips."""
    if not want or not have:
        return 0.0
    compare = compare or (lambda a, b: difflib.SequenceMatcher(None, a, b).ratio())
    got = 0.0
    for w in want:
        best = max(compare(w, h) for h in have)
        got += best if best >= 0.75 else 0
    return got / len(want)


# --- settings and the files the add-on fills ------------------------------------------------------------
def _read_json(path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:
            got = json.load(f)
        return got if isinstance(got, type(default)) else default
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part"
    with open(part, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    # Windows refuses to replace a file someone is reading at that instant (this window, an antivirus scan). That
    # lasts milliseconds: try again briefly instead of losing the save (8 Nov review: a good read became "unknown").
    for attempt in range(12):
        try:
            os.replace(part, path)
            return
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(0.03 + 0.01 * attempt)


def settings():
    got = _read_json(SETTINGS, {})
    out = dict(DEFAULTS)
    for k, v in got.items():
        if k in DEFAULTS and isinstance(v, type(DEFAULTS[k])):
            out[k] = v
    return out


def is_for(controller):
    """Does this controller's log wait for WhatsApp replies?"""
    want = fold(controller or "")
    return any(fold(c) == want or (want and fold(c).split()[0] == want.split()[0]) for c in settings()["controllers"])


def trusted(name, names=None):
    return fold(name) in (names if names is not None else trusted_names())


def trusted_names():
    """The trusted list, folded - read once per log, not once per call."""
    return {fold(t) for t in settings()["trusted"]}


def _confirmation_emoji(text):
    """One configured symbolic reply, with presentation/skin variants; never discard words or extra emoji."""
    if not isinstance(text, str) or not text or text != text.strip():
        return None
    try:
        if len(text.encode("utf-16-le")) // 2 > 16:
            return None
    except UnicodeEncodeError:
        return None
    if not any(unicodedata.category(ch) == "So" for ch in text) or any(
            unicodedata.category(ch) not in ("So", "Sk", "Mn", "Cf") for ch in text):
        return None
    return "".join(ch for ch in text if ch not in ("\ufe0e", "\ufe0f") and not "\U0001f3fb" <= ch <= "\U0001f3ff")


def _confirmation_rules(value):
    if not isinstance(value, dict):
        return {}
    out, conflicts = {}, set()
    for name, items in value.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 160 or any(ord(ch) < 32 for ch in name):
            continue
        if not isinstance(items, (list, tuple)) or not 0 < len(items) <= 8:
            continue
        who = wa_beta.normalize_name(name)
        symbols = tuple(sorted({symbol for item in items if (symbol := _confirmation_emoji(item))}))
        if symbols:
            if who in out and out[who] != symbols:
                conflicts.add(who)
            out[who] = symbols
    return {who: symbols for who, symbols in out.items() if who not in conflicts}


def load_confirmation_emojis():
    """Read the user's exact-person rules once; heard() takes them explicitly and remains pure."""
    return _confirmation_rules(settings().get("confirmation_emojis"))


def _beta_file():
    if os.environ.get("CCL_BETA_STATE"):
        return os.environ["CCL_BETA_STATE"]            # same first-priority override as the beta UI
    if BETA != DEFAULT_BETA:
        return BETA
    if os.environ.get("CCL_NO_DOWNLOAD"):
        return os.path.join(_edits_dir(), "whatsapp-beta.json")
    if os.path.dirname(NAMES) != HERE:                # the existing isolated WhatsApp tests redirect NAMES
        return os.path.join(os.path.dirname(NAMES), "whatsapp-beta.json")
    return BETA


def load_aliases(controller=None):
    """Explicit chat -> guard aliases. Existing NAMES wins; beta aliases apply only to Lena's full name."""
    saved = _read_json(NAMES, {})
    out = {k: v for k, v in saved.items() if isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()} \
        if isinstance(saved, dict) else {}
    if not wa_beta.allowed_controller(controller):
        return out
    beta = _read_json(_beta_file(), {})
    names = beta.get("chat_names") if isinstance(beta, dict) else None
    if not isinstance(names, dict) or any(not isinstance(name, str) or not name.strip() or
                                        not isinstance(chat, str) or not chat.strip() or len(chat) > 120 or
                                        any(ord(c) < 32 for c in name + chat)
                                        for name, chat in names.items()):
        return out
    inverse = {}
    for name, chat in names.items():
        inverse.setdefault(chat, {}).setdefault(wa_beta.normalize_name(name), name)
    for chat, guards in inverse.items():
        if chat not in out and len(guards) == 1:
            out[chat] = next(iter(guards.values()))
    return out


def bridge_status():
    return _actions.readiness()


_replies_on = True                                     # the window turns this off while Auto read is off (20 Nov)


def set_replies(on):
    """Use WhatsApp replies in the logs (Auto read on) or not (Auto read off: the log is plain, as before WhatsApp)."""
    global _replies_on
    _replies_on = bool(on)


def replies_on():
    return _replies_on


def replies_for(controller):
    """WhatsApp replies decide this controller's calls: his shift (whatsapp.json controllers) and Auto read on."""
    return _replies_on and is_for(controller)


def enqueue_action(kind, exact_chat, text=None, ttl=90, phone=None, since=None):
    """Queue an explicitly enabled open/draft/read request, or 'send': ONE reviewed message he pressed Send for."""
    return _actions.enqueue(kind, exact_chat, text, ttl, phone, since)


def action_status(action_id):
    return _actions.status(action_id)


def cancel_action(action_id):
    return _actions.cancel(action_id)


def cancel_actions():
    return _actions.cancel_all()


def link():
    """The port and secret shared with the add-on, or None if `whatsapp.py setup` has not made them.

    Never made or replaced here: a new secret silently cuts the add-on off (its config.js holds the old one), so
    only setup() writes it, and the window says when it is missing.
    """
    got = _read_json(LINK, {})
    if isinstance(got.get("token"), str) and len(got["token"]) >= 32 and isinstance(got.get("port"), int):
        return got
    return None


def make_link():
    """setup's part: the secret is made once and then kept."""
    got = link()
    if got is None:
        if os.path.exists(LINK):
            raise ValueError("%s is damaged - delete it and run setup again (then reload the add-on)" % LINK)
        got = {"port": PORT, "token": secrets.token_urlsafe(32)}
        _write_json(LINK, got)
    return got


def write_reader_config():
    """The add-on's own copy of the port and secret (..\\reader\\config.js). Run again after any change, then
    reload the add-on in chrome://extensions."""
    got = make_link()
    os.makedirs(READER, exist_ok=True)
    with open(os.path.join(READER, "config.js"), "w", encoding="utf-8") as f:
        f.write("// Written by tool\\whatsapp.py setup - do not edit by hand.\n"
                "var READER = {port: %d, token: %s};\n" % (got["port"], json.dumps(got["token"])))
    return os.path.join(READER, "config.js")


_lock = threading.Lock()
_coverage_lock = threading.Lock()
_beat = {"at": 0.0, "status": {}}                      # the last heartbeat this process received


def read_coverage_file():
    """Keep receipts beside the event logs, including tests' explicitly isolated LOGS."""
    return os.path.join(os.path.dirname(LOGS), "whatsapp-read-coverage.json")


def _valid_read_receipt(value):
    return (isinstance(value, dict) and isinstance(value.get("chat"), str) and bool(value["chat"].strip())
            and len(value["chat"]) <= 120
            and all(type(value.get(k)) is int for k in ("since", "created", "completed"))
            and 0 <= value["since"] <= value["created"] + 5000 <= value["completed"] + 5000)


_commits_cache = {}                                    # path -> ((mtime_ns, size), parsed receipts)


def load_read_commits():
    """Malformed/missing receipts fail closed; this never manufactures completion.

    Read under the same lock as the save (a read in the middle of a save made the save fail on Windows), and parsed
    again only when the file really changed - the window asks every few seconds."""
    path = read_coverage_file()
    with _coverage_lock:
        try:
            st = os.stat(path)
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        cached = _commits_cache.get(path)
        if stamp is not None and cached and cached[0] == stamp:
            return dict(cached[1])
        got = _read_json(path, {})
        out = {key: value for key, value in got.items() if isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key)
               and _valid_read_receipt(value)}
        if stamp is not None:
            _commits_cache[path] = (stamp, dict(out))
        return out


def commit_read_coverage(receipt):
    """Called only by validated complete read ACKs, before the queue reports success.

    No message text/phone is saved here. Preserve malformed existing state for review;
    replacing it with an empty object could silently discard previous evidence.
    """
    if not isinstance(receipt, dict) or not isinstance(receipt.get("id"), str) or \
            not re.fullmatch(r"[a-f0-9]{32}", receipt["id"]) or not _valid_read_receipt(receipt):
        raise ValueError("Invalid automatic-reading receipt.")
    path = read_coverage_file()
    with _coverage_lock:
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig") as source:
                saved = json.load(source)
            if not isinstance(saved, dict) or any(not isinstance(k, str) or not re.fullmatch(r"[a-f0-9]{32}", k)
                                                 or not _valid_read_receipt(v) for k, v in saved.items()):
                raise ValueError("Automatic-reading coverage needs review; the existing file was preserved.")
        else:
            saved = {}
        value = {key: receipt[key] for key in ("chat", "since", "created", "completed")}
        if receipt["id"] in saved and saved[receipt["id"]] != value:
            raise ValueError("Automatic-reading receipt already has a different result.")
        saved[receipt["id"]] = value
        # a week, like the messages themselves: older receipts cover events that are already deleted
        cutoff = int(time.time() * 1000) - KEEP_DAYS * 86400000
        saved = {k: v for k, v in saved.items() if v["completed"] >= cutoff or k == receipt["id"]}
        _write_json(path, saved)
    return True


def _read_event_committed(event, commits):
    """A completed snapshot proves only its covered time, never later unseen history."""
    key = event.get("read_id")
    receipt = commits.get(key) if isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key) and isinstance(commits, dict) else None
    if not _valid_read_receipt(receipt) or receipt["chat"] != event.get("chat"):
        return False
    at = _at(event)
    return (at is not None and type(event.get("seen")) in (int, float) and 0 <= event["seen"] <= receipt["completed"]
            and receipt["since"] <= int(at.timestamp() * 1000) <= receipt["completed"])


def _day_file(day):
    return os.path.join(LOGS, "%s.jsonl" % day.isoformat())


def _direction(e):
    """Only explicit direction is evidence. Older readers supplied a boolean; missing never means incoming."""
    direction = e.get("direction")
    ours = e.get("ours")
    if "direction" in e:
        if direction not in ("incoming", "outgoing"):
            return "unknown"
        if isinstance(ours, bool) and ours != (direction == "outgoing"):
            return "unknown"
        return direction
    return ("outgoing" if ours else "incoming") if isinstance(ours, bool) else "unknown"


def _with_direction(e, direction):
    out = dict(e, direction=direction)
    out.pop("ours", None)
    if direction != "unknown":
        out["ours"] = direction == "outgoing"
    return out


def _truncated(e):
    """A retained prefix cannot prove an affirmative or the meaning of our question.

    Old readers silently capped at 300 UTF-16 units. A boundary-length legacy
    message is therefore incomplete unless a newer reader explicitly says false.
    """
    if "truncated" in e and type(e["truncated"]) is not bool:
        return True
    text = e.get("text", "")
    units = len(text.encode("utf-16-le", errors="surrogatepass")) // 2 if isinstance(text, str) else 0
    return e.get("truncated") is True or units > 300 or (units == 300 and e.get("truncated") is not False)


def _reaction_snapshot(e):
    """Validate a parent-bound observation. Its missing occurrence time must stay missing."""
    value = e.get("reactions")
    if (not isinstance(value, dict) or set(value) != {"complete", "items"} or type(value.get("complete")) is not bool
            or not isinstance(value.get("items"), list) or len(value["items"]) > 2
            or e.get("kind") != "bubble" or e.get("group") is not False
            or not isinstance(e.get("id"), str) or not 0 < len(e["id"]) <= 200):
        return None
    actors, items = set(), []
    for item in value["items"]:
        if (not isinstance(item, dict) or set(item) != {"parent_id", "emoji", "actor", "actor_name", "at"}
                or item.get("parent_id") != e["id"] or item.get("at") is not None
                or item.get("actor") not in ("contact", "self") or item["actor"] in actors
                or item.get("actor_name") != (e.get("chat") if item["actor"] == "contact" else "You")):
            return None
        emoji = item.get("emoji")
        try:
            valid = (isinstance(emoji, str) and emoji == emoji.strip() and 0 < len(emoji.encode("utf-16-le")) // 2 <= 16
                     and not re.search(r"[\x00-\x1f\x7f]", emoji))
        except UnicodeEncodeError:
            valid = False
        if not valid:
            return None
        actors.add(item["actor"])
        items.append(dict(item))
    return {"complete": value["complete"], "items": items}


def _affirmative_reaction(emoji):
    return bool(re.fullmatch(r"(?:[👍👌][\U0001f3fb-\U0001f3ff]?|[✅✔])[\ufe0e\ufe0f]?", emoji or ""))


def _reaction_states(direct, commits):
    """Newest explicit automatic snapshot wins; passive metadata omission is not a removal."""
    parents, out = {}, {}
    for e in direct:
        if e.get("kind") == "bubble" and e.get("id"):
            parents.setdefault((e["chat"], e["id"]), []).append(e)
    for key, copies in parents.items():
        if not any("reactions" in e for e in copies):
            continue
        snapshots = [e for e in copies if "reactions" in e or _read_event_committed(e, commits)]
        latest = max(e["seen"] for e in snapshots)
        fresh = [e for e in snapshots if e["seen"] == latest]
        previous = any(_read_event_committed(e, commits) and (s := _reaction_snapshot(e)) and s["complete"]
                       and any(i["actor"] == "contact" and _affirmative_reaction(i["emoji"]) for i in s["items"]) for e in snapshots)
        # A removal also starts a new episode: an old timely reaction cannot lend its timing to a later re-addition.
        # The latest complete absence before this snapshot is the lower bound; it is never an invented reaction time.
        empties = [v["seen"] for v in snapshots if _read_event_committed(v, commits) and (s := _reaction_snapshot(v))
                   and s["complete"] and not any(i["actor"] == "contact" and _affirmative_reaction(i["emoji"]) for i in s["items"])
                   and v["seen"] < latest]
        e = fresh[0]
        signatures = {(json.dumps(_reaction_snapshot(v), sort_keys=True), _identity(v), _read_event_committed(v, commits)) for v in fresh}
        value = _reaction_snapshot(e)
        state = {"mode": "unknown", "previous": bool(previous), "identity": _identity(e), "emoji": "",
                 "empty_seen": max(empties) if empties else None}
        if len(signatures) == 1 and _read_event_committed(e, commits) and value and value["complete"]:
            contact = next((i for i in value["items"] if i["actor"] == "contact"), None)
            state["mode"] = "positive" if contact and _affirmative_reaction(contact["emoji"]) else "contact" if contact else "self" if value["items"] else "empty"
            state["emoji"] = contact["emoji"] if contact else ""
        out[key] = state
    return out


def _clean(e):
    """Keep only what the reader may send, with lengths capped: nothing on this PC trusts the page."""
    if not isinstance(e, dict):
        return None
    out = {}
    if "read_id" in e:
        if not isinstance(e["read_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", e["read_id"]):
            return None                                # never strip an invalid quarantine tag into legacy evidence
        out["read_id"] = e["read_id"]
    # DOM order is evidence only with an exact stable message ID and an integer row index.
    if (isinstance(e.get("view_id"), str) and re.fullmatch(r"[a-f0-9]{32}", e["view_id"])
            and isinstance(e.get("id"), str) and 0 < len(e["id"]) <= 200
            and type(e.get("i")) is int and 0 <= e["i"] <= 10000 and e.get("kind") == "bubble"):
        out["view_id"] = e["view_id"]
    if _truncated(e):
        out["truncated"] = True                         # includes clipping performed by this receiver itself
    elif e.get("truncated") is False:
        out["truncated"] = False
    if "reactions" in e:
        out["reactions"] = _reaction_snapshot(e) or {"complete": False, "items": []}
    if e.get("media_type") == "sticker" and e.get("what") == "media":
        out["media_type"] = "sticker"
    for key, kind, cap in (("kind", str, 12), ("chat", str, 120), ("text", str, 300), ("shown", str, 16),
                           ("date", str, 12), ("what", str, 12), ("emoji", str, 8), ("to", str, 120),
                           ("id", str, 200)):
        v = e.get(key)
        if isinstance(v, kind):
            out[key] = v[:cap]
    for key in ("ours", "snapshot", "missed", "read", "group"):
        if isinstance(e.get(key), bool):
            out[key] = e[key]
    for key in ("seen", "i", "unread"):
        if isinstance(e.get(key), (int, float)) and not isinstance(e.get(key), bool):
            try:
                out[key] = int(e[key])
            except (ValueError, OverflowError):
                return None
    if out.get("kind") not in ("line", "bubble") or not out.get("chat") or "seen" not in out:
        return None
    if _at(out) is None:
        return None
    return _with_direction(out, _direction(e))


def _clean_beat(beat):
    """The heartbeat's own report, whitelisted like an event."""
    if not isinstance(beat, dict):
        return {}
    out = {}
    for key in ("pane", "paused", "useHere", "focused"):
        if isinstance(beat.get(key), bool):
            out[key] = beat[key]
    for key in ("rows", "chats", "missed", "checks", "at"):
        if isinstance(beat.get(key), (int, float)) and not isinstance(beat.get(key), bool):
            try:
                out[key] = int(beat[key])
            except (ValueError, OverflowError):
                continue
    for key, cap in (("visible", 12), ("version", 12)):
        if isinstance(beat.get(key), str):
            out[key] = beat[key][:cap]
    return out


def record(events, beat=None):
    """Keep what the add-on reported: events appended to the day's log, the heartbeat in memory.

    Written ASCII-escaped and one event at a time: a message holding something odd (a broken emoji, a lone
    surrogate) is skipped on its own instead of failing the batch - the add-on would otherwise send the same batch
    again every 30 seconds and nothing newer would ever get through."""
    now = time.time()
    kept = [c for c in (_clean(e) for e in (events or [])) if c]
    lines = {}
    for e in kept:
        try:
            day = datetime.datetime.fromtimestamp(e["seen"] / 1000.0).date()
            lines.setdefault(day, []).append(json.dumps(e, ensure_ascii=True))
        except (ValueError, OverflowError, OSError):
            continue
    with _lock:
        if beat is not None:
            _beat["at"], _beat["status"] = now, _clean_beat(beat)
            _write_json(os.path.join(LOGS, "beat.json"), {"at": now, "status": _beat["status"]})
        if lines:
            os.makedirs(LOGS, exist_ok=True)
        for day, rows in lines.items():
            with open(_day_file(day), "a", encoding="utf-8") as f:
                f.write("".join(r + "\n" for r in rows))
    return sum(len(r) for r in lines.values())


def last_beat():
    """When the add-on last reported (epoch seconds) and what it said - from this process or the file."""
    got = _read_json(os.path.join(LOGS, "beat.json"), {})
    at = max(_beat["at"], got.get("at", 0) if isinstance(got.get("at"), (int, float)) else 0)
    status = _beat["status"] if _beat["at"] >= (got.get("at") or 0) else got.get("status", {})
    return at, status if isinstance(status, dict) else {}


def reader_state(now=None):
    """('on', '') when the add-on is really reading, else ('off', why). A heartbeat alone is not enough: it has
    to be recent by its own clock too (the worker could be replaying an old one), and it has to say the chat
    list is there - logged out, 'Use here' or a search open means the reader is blind, whatever it sends."""
    now = now or time.time()
    at, status = last_beat()
    if not at or now - at >= LIVE_SECONDS:
        return "off", "no word from the add-on"
    own = status.get("at")
    if not isinstance(own, int) or now - own / 1000.0 >= LIVE_SECONDS:
        return "off", "the add-on's last report is old"
    if status.get("useHere"):
        return "off", "WhatsApp Web is open somewhere else - press Use here in the pinned tab"
    if not status.get("pane") or not status.get("rows"):
        return "off", "WhatsApp Web shows no chat list (logged out or still loading)"
    if status.get("paused"):
        return "off", "WhatsApp Web is searching or filtered - set it back to All"
    return "on", ""


def live(now=None):
    return reader_state(now)[0] == "on"


def events_between(start, end):
    """Every event seen from start to end (datetimes), oldest first, duplicates dropped."""
    out, seen = [], set()
    day = start.date()
    while day <= end.date():
        try:
            with open(_day_file(day), encoding="utf-8") as f:
                for line in f:
                    try:
                        e = _clean(json.loads(line))
                    except ValueError:
                        continue
                    if not e:
                        continue
                    key = (e["kind"], e["chat"], e.get("text"), e.get("shown"), e.get("date"), _direction(e),
                           e.get("what"), e.get("media_type"), e.get("id"), e.get("read_id"), e.get("view_id"), _truncated(e),
                           json.dumps(e.get("reactions"), sort_keys=True),
                           e.get("i") if e["kind"] == "bubble" else e["seen"] // 1000)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(e)
        except OSError:
            pass
        day += datetime.timedelta(days=1)
    lo, hi = start.timestamp() * 1000, end.timestamp() * 1000
    return [e for e in out if lo <= e["seen"] <= hi]


def purge(today=None):
    """Staff's messages are kept KEEP_DAYS and then deleted: the day logs go, and the words of each reply are
    taken out of older shifts' overlays (what was decided - Ok, the times - stays, for reprinting the log)."""
    today = today or datetime.date.today()
    old = lambda iso: (today - datetime.date.fromisoformat(iso)).days >= KEEP_DAYS
    try:
        for name in os.listdir(LOGS):
            m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.jsonl", name)
            if m and old(m.group(1)):
                try:
                    os.remove(os.path.join(LOGS, name))
                except OSError:
                    pass
    except OSError:
        pass
    try:
        names = os.listdir(_edits_dir())
    except OSError:
        return
    for name in names:
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2}) .* whatsapp\.json", name)
        if not m or not old(m.group(1)):
            continue
        path = os.path.join(_edits_dir(), name)
        got = _read_json(path, {})
        if any(isinstance(v, dict) and "said" in v for v in got.values()):
            for v in got.values():
                if isinstance(v, dict):
                    v.pop("said", None)
            try:
                _write_json(path, got)
            except OSError:
                pass


# --- the receiver: a thread of the window, on 127.0.0.1 only ---------------------------------------------
class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "CheckCallLog"

    def log_message(self, *args):                       # under pythonw there is no stderr to write to
        pass

    def _answer(self, code, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self):
        token = self.headers.get("X-Reader-Token", "")
        origin = self.headers.get("Origin", "")
        return hmac.compare_digest(token, self.server.token) and (not origin or origin.startswith("chrome-extension://"))

    def do_GET(self):
        # /wa/hello?nonce=N lets the add-on check, BEFORE it sends the secret or any message, that this really is
        # Check Call Log: only something holding the secret can answer HMAC(secret, N). Without it, whatever
        # program sat on this port while the window was closed would be handed both.
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/wa/hello":
            nonce = urllib.parse.parse_qs(url.query).get("nonce", [""])[0]
            if not re.fullmatch(r"[0-9a-f]{32,64}", nonce):
                return self._answer(400, {"ok": False})
            return self._answer(200, {"ok": True, "app": "Check Call Log", "proof": proof(self.server.token, nonce)})
        if url.path != "/wa/ping" or not self._allowed():
            return self._answer(404, {"ok": False})
        self._answer(200, {"ok": True, "app": "Check Call Log"})

    def _drain(self, size):
        """Read what was sent before answering, whatever the answer: on Windows, closing a connection with
        unread data in it breaks it, and the sender never learns it was refused."""
        got, left = [], min(max(size, 0), 2 * 1024 * 1024)
        while left > 0:
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                break
            got.append(chunk)
            left -= len(chunk)
        return b"".join(got)

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = -1
        raw = self._drain(size)
        if self.path not in ("/wa", "/wa/action", "/wa/authorize") or not self._allowed():
            return self._answer(404, {"ok": False})
        if not 0 < size <= 512 * 1024:
            return self._answer(413, {"ok": False})
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._answer(400, {"ok": False})
        if not isinstance(body, dict):
            return self._answer(400, {"ok": False})
        if self.path == "/wa/action":
            accepted = _actions.acknowledge(body, commit_read=commit_read_coverage)
            return self._answer(200 if accepted else 400, {"ok": accepted})
        if self.path == "/wa/authorize":
            grant = _actions.authorize(body)
            return self._answer(200 if grant else 409, grant or {"ok": False})
        try:
            got = record(body.get("events"), body.get("beat"))
            if isinstance(body.get("beat"), dict):
                _actions.touch(body.get("client"), body["beat"])
        except Exception:                               # (a full disk, a locked file: say so, never die)
            return self._answer(503, {"ok": False})
        self._answer(200, {"ok": True, "kept": got, "action": _actions.lease(body.get("client"))})


def proof(token, nonce):
    return hmac.new(token.encode("utf-8"), nonce.encode("ascii"), "sha256").hexdigest()


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = False                         # on Windows reuse would let a second program share the port
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)        # (not HTTPServer's: no name lookup)


def start():
    """Start the receiver on 127.0.0.1 in a daemon thread. Returns the server, or None when it cannot: not set
    up, or the port is taken (another Check Call Log window already listens - this one reads the same day logs -
    or some other program; ping() tells which)."""
    got = link()
    if got is None:
        return None
    try:
        server = _Server(("127.0.0.1", got["port"]), _Handler)
    except OSError:
        return None
    server.token = got["token"]
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True).start()
    return server


def ping():
    """Is a Check Call Log receiver answering on this PC? It must prove it holds the secret before the secret is
    sent anywhere - so a stranger on the port learns nothing from a check."""
    got = link()
    if got is None:
        return False
    nonce = secrets.token_hex(16)
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/wa/hello?nonce=%s" % (got["port"], nonce), timeout=2) as r:
            answer = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(answer, dict) and hmac.compare_digest(str(answer.get("proof", "")), proof(got["token"], nonce))


# --- from the events to the calls -----------------------------------------------------------------------
_AT_CACHE = {}                                         # (seen, kind, date, shown, snapshot) -> _at's answer


def _at(e):
    """When a message happened, or None for a damaged/unknown clock. Never date an old reply as 'now'.

    Remembered by the only fields it reads: the analysis asks for the same messages' times ~10x each (8 Nov profile:
    107,000 calls and 1.2 s of text-to-date parsing for 9,500 events, growing with every reading pass)."""
    try:
        key = (e["seen"], e["kind"], e.get("date"), e.get("shown"), bool(e.get("snapshot")))
        return _AT_CACHE[key]
    except KeyError:
        if not isinstance(e, dict) or "seen" not in e or "kind" not in e:
            return _at_uncached(e)
    except TypeError:                                  # an unhashable field: work it out the slow way
        return _at_uncached(e)
    value = _at_uncached(e)
    if len(_AT_CACHE) > 200000:
        _AT_CACHE.clear()
    _AT_CACHE[key] = value
    return value


def _at_uncached(e):
    try:
        seen = datetime.datetime.fromtimestamp(e["seen"] / 1000.0)
        if e["kind"] == "bubble":
            return datetime.datetime.strptime("%s %s" % (e["date"], e["shown"]), "%d/%m/%Y %H:%M")
        if e.get("snapshot"):
            if not re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", e.get("shown", "")):
                return None
            h, m = map(int, e["shown"].split(":"))
            at = seen.replace(hour=h, minute=m, second=0, microsecond=0)
            return at - datetime.timedelta(days=1) if at > seen + datetime.timedelta(minutes=1) else at
        return seen
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None


def _hhmm(dt):
    return dt.strftime("%H:%M")


def _venue_score(said, call):
    words = set(fold(said).split()) - {"the", "st", "street"}
    names = set(fold("%s %s" % (call.get("venue", ""), call.get("site", ""))).split()) - {"the", "st", "street"}
    return len(words & names) / float(len(words) or 1)


REPLY_HOURS = 3                                        # how long after the question a reply can still answer it
OWN_QUIET_HOURS = 12                                   # a check-in must not follow anything of ours for this long
LOOK_BACK_HOURS = 3 + OWN_QUIET_HOURS                  # so update() reads this far back before a shift starts


def _same_call_question(named, call, question=None):
    """Renewed context needs a definite venue, not the legacy alias matcher's shared word."""
    if named == ANY:
        return True
    names = [call.get("venue", ""), call.get("site", "")]
    if question is not None:
        names.append(question_venue(question.get("text")))
    return bool(named) and fold(named) in {fold(value) for value in names if value}


def _specific_venue(named, call):
    """Early questions need every named venue token, not a fuzzy/shared-word match."""
    if not named or named == ANY:
        return False
    stop = {"the", "st", "street", "bar", "pub", "club", "and", "of"}
    want = set(fold(named).split()) - stop
    have = set(fold("%s %s" % (call.get("venue", ""), call.get("site", ""))).split()) - stop
    return bool(want) and want <= have


def _chaser(e, call, question=None):
    """Our nudge that keeps the question open ("?", "hello?", the same question again) - not a new subject. A question
    about another venue or another day is a new subject and closes the window."""
    if _truncated(e) or _unreadable_text(e.get("text")):
        return False
    if is_call(e):
        return True                                    # ringing him about it ("Voice call, No answer") is chasing too
    raw = (e.get("text") or "").strip()
    words = fold(raw)
    if e.get("what") == "deleted" or words in ("you deleted this message", "this message was deleted"):
        return True                                    # a message of ours taken back said nothing new
    if e.get("what", "text") != "text":
        return False                                   # a voice duration is not a time; unreadable content may be a new subject
    if re.fullmatch(r"[?!.\s]+", raw) or words in (
            "hello", "hi", "hey", "u there", "you there", "any update", "update", "hello there",
            "please reply", "please reply asap", "reply please", "respond asap please", "please respond"):
        return True
    # the time added a moment later: "At 8pm", "Sorry 7pm*", "@ 10pm", "21.00 start" - still the same question
    if re.fullmatch(r"(?:sorry\s+)?(?:at\s+|for\s+)?\d{1,2}(?:\s+\d{2})?\s*(?:am|pm|h|hrs?)?(?:\s+start)?(?:\s+tonight)?", words):
        return True
    named = question_venue(e.get("text"))
    return _same_call_question(named, call, question)


CALL = re.compile(r"^(?:missed\s+)?(?:voice|video|group)\s+call\b|^(?:incoming|outgoing)\s+(?:voice\s+|video\s+)?call\b|"
                  r"^call\s+(?:back|ended)\b|^tap\s+to\s+call\s+back\b", re.I)


def is_call(e):
    """A call shown in the chat ("Voice call", "Missed voice call", "Video call, 1 min") - not a message."""
    return e.get("what") == "call" or bool(CALL.match((e.get("text") or "").replace("‎", "").strip()))


TAKEBACK_MONTH = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
                  r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
TAKEBACK_DAY = re.compile(
    r"\b(?:tom+or+ow+|tmr+w?|tmrw|2mo?r+o?w?|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"(?:next|this)\s+(?:week|weekend)|weekend|christmas|festive|"
    r"(?:%s)\s+\d{1,2}(?:st|nd|rd|th)?|\d{1,2}(?:st|nd|rd|th)?\s+(?:%s)|"
    r"(?:for|in|during)\s+(?:%s)|(?:on|the|off)\s+\d{1,2}(?:st|nd|rd|th))\b"
    % (TAKEBACK_MONTH, TAKEBACK_MONTH, TAKEBACK_MONTH), re.I)
CURRENT_SHIFT = re.compile(r"\b(?:tonight|tonite|today|now|this\s+(?:evening|afternoon|morning|shift))\b", re.I)


def _another_day(text, day=None, seen_day=None):
    """Only an actual day/date request exempts a later no. Cover, available, Tom and '1st bus' are not dates.

    A date in a separate clause cannot hide tonight's cancellation ('can't come, can do Sunday instead').
    Dates equal to this call's day are not another shift either.
    """
    if CURRENT_SHIFT.search(text):
        return False
    def dates_in(part):
        # 'not available until Monday' describes the present absence, not a request for Monday off.
        return [m for m in TAKEBACK_DAY.finditer(part)
                if not re.search(r"\b(?:until|till|through|by)\s*$", part[:m.start()], re.I)]

    dates = dates_in(text)
    if not dates:
        return False
    if day:
        for match in dates:
            word = match.group().lower()
            if word == day.strftime("%A").lower():
                return False
            if seen_day and day.isocalendar()[:2] == seen_day.isocalendar()[:2] and (
                    word == "this week" or (word in ("weekend", "this weekend") and day.weekday() >= 5)):
                return False
            if re.fullmatch(r"tom+or+ow+|tmr+w?|tmrw|2mo?r+o?w?", word) and seen_day \
                    and seen_day + datetime.timedelta(days=1) == day:
                return False
            number = re.search(r"\d{1,2}", word)
            month = re.search(r"\b(?:%s)\b" % TAKEBACK_MONTH, word)
            if month and not number and month.group()[:3] == day.strftime("%b").lower():
                return False
            if number and int(number.group()) == day.day:
                if not month or month.group()[:3] == day.strftime("%b").lower():
                    return False
    clauses = re.split(r"([,;.!?\n]+|\b(?:but|and)\b|(?=\bi\s+(?:can|could)\s+(?:do|work)\b))", text, flags=re.I)
    date_context = False
    adversative = False
    for clause in clauses:
        words = _fold_keep_marks(clause)
        if words in ("but", "and"):
            adversative = words == "but"
            continue
        if not words:
            continue
        if dates_in(clause):
            date_context = True
        elif _explicit_takeback(clause) and (not date_context or (
                adversative and not re.search(r"\b(?:that\s+day|those\s+(?:days|shifts)|then)\b", words))):
            return False
        adversative = False
    return True


def _same_shift_followup(e, call, question=None):
    """After a yes, a thanks or a narrow arrival question keeps take-backs open. A new subject still closes it."""
    if _truncated(e) or _unreadable_text(e.get("text")):
        return False
    if _chaser(e, call, question):
        return True
    if e.get("what", "text") != "text":
        return False
    raw = e.get("text") or ""
    words = _fold_keep_marks(raw)
    if not words:
        return has_yes_emoji(raw)
    if re.fullmatch(r"(?:ok|okay|grand|perfect|cheers|thanks|thank\s+you|got\s+it|noted|great|sorted)"
                    r"(?:\s+(?:thanks|thank\s+you|mate|sir|bro|boss|a\s+lot|very\s+much))*", words):
        return True
    if TAKEBACK_DAY.search(raw) or re.search(r"\b(?:cover|extra|swap|instead|available)\b", words):
        return False
    if re.fullmatch(r"(?:are\s+you|r\s+u|you)\s+(?:there|on\s+site)(?:\s+(?:now|yet))?|"
                    r"have\s+you\s+arrived(?:\s+yet)?|will\s+you\s+be\s+on\s+time|"
                    r"where\s+are\s+you|what\s+time\s+will\s+you\s+(?:arrive|get\s+there)", words):
        return True
    match = re.fullmatch(r"(?:are\s+you|r\s+u|you)\s+(?:in|at)\s+(.+)", words)
    if match:
        place = set(match.group(1).split()) - {"the", "now", "yet"}
        venue = set(fold("%s %s" % (call.get("venue", ""), call.get("site", ""))).split())
        return bool(place) and place <= venue
    return False


WITHDRAW_AFTER_START = 90                              # minutes after the start a clear "can't come" still takes a yes back


def _takeback_events(events, yes, call, question=None, until=None, day=None, context_until=None):
    """A separate, bounded window for withdrawing an already established yes, never for accepting a new yes.

    Up to our next new subject, anything he says can take the yes back (_undone decides). After it - a roster, a
    timesheet reminder, his second venue's question - a later message might be answering that instead, so only a
    CLEAR cancellation of tonight counts, up to `until` (start + WITHDRAW_AFTER_START). Before 8 Nov a new subject
    ended the check entirely, so "All good", our "Roster for next week", then "I'll be late" stayed green (a real
    chat, 22/09)."""
    begins = _at(yes)
    close = context_until if context_until is not None else begins + datetime.timedelta(hours=REPLY_HOURS)
    for e in events:
        if _direction(e) == "outgoing" and _at(e) > begins and not _same_shift_followup(e, call, question):
            close = min(close, _at(e))
            break
    inside = [e for e in events if _direction(e) != "outgoing" and _within(e, begins, close)
              and e.get("what") not in ("deleted", "reaction") and not is_call(e)]
    if until is None or until <= close:
        return inside
    seen = {_identity(e) for e in inside}
    later = [e for e in events if _direction(e) == "incoming" and close <= _at(e) <= until and _identity(e) not in seen
             and e.get("what", "text") in ("text", "media") and not is_call(e)
             and _explicit_takeback(e.get("text") or "") and not _another_day(e.get("text") or "", day, _at(e).date())]
    return inside + later


def _shift_uncertain(text):
    """A few explicit shift doubts/problems need a look even without a negative word; unrelated questions do not."""
    words = _fold_keep_marks(text)
    return bool(re.search(
        r"\b(?:am\s+i\s+working|am\s+i\s+(?:in|on)\s+(?:tonight|today|this\s+evening|the\s+roster)|"
        r"do\s+i\s+(?:work|start)|what\s+time\s+(?:do\s+i|does\s+(?:my|the)\s+shift)\s+start|"
        r"(?:i\s+)?thought\s+(?:i\s+was|im|i\s+am)\s+(?:off|starting|working)|"
        r"just\s+woke\s+up|slept\s+through\s+(?:my|the)\s+alarm|"
        r"(?:my\s+)?car\s+(?:has\s+)?broke[n]?\s+down|stuck\s+in\s+traffic|running\b[^.!?]*\bbehind)\b", words))


def _explicit_takeback(text):
    """A current cancellation/problem, as distinct from a generic negative in a future request or photo caption."""
    words = _fold_keep_marks(text)
    return _shift_uncertain(text) or bool(ABSENT.search(words)) or bool(re.search(
        r"\b(?:(?:cant|cannot|wont|unable\s+to)\s+(?:come|make\s+it|work|do\s+it)|"
        r"not\s+(?:coming|available|well|needed)|sick|unwell|running\s+late|minutes?\s+late|"
        r"(?:im|i\s+am|ill\s+be|i\s+will\s+be|we\s+are)\s+(?:really\s+|very\s+)?(?:late|off|absent)|"
        r"wrong\s+(?:text|message)|something\s+came\s+up)\b", words))


def _conflicting_reply(e, call=None, day=None):
    """Known trouble or unreadable context conflicts with an affirmative; unrelated requests do not."""
    if _direction(e) == "unknown" or _truncated(e) or _unreadable_text(e.get("text")):
        return True
    text = e.get("text") or ""
    if _another_day(text, day, _at(e).date()):
        return False
    if e.get("what") == "voice":
        return True
    if e.get("what") == "media":
        return _explicit_takeback(text)                 # an ordinary arrival photo is not a cancellation
    words = _fold_keep_marks(text)
    negatives = NEGATIVE.findall(GREETING.sub(" ", POLITE.sub(" ", REASSURING.sub(" ", words))))
    if "?" in text and negatives and set(negatives) == {"sorry"} and _benign_after_yes(text):
        return False
    if e.get("what", "text") != "text":
        return False
    verdict = judge(text.replace("?", " "), call)
    if verdict == "no" or _shift_uncertain(text):
        return True
    # 8 Nov review: deciding by bad words always misses one ("Family situation, need to go home", another language).
    # So after a yes, more from him keeps the yes only when it is a yes again or plainly harmless; anything else
    # goes back to the controller to read.
    return not (verdict == "yes" or _benign_after_yes(text) or _signed_arrival_after_yes(text, call))


def _signed_arrival_after_yes(text, call):
    """A whole same-venue arrival clause may end with this sender's self-signature.

    This only preserves an existing yes. It does not teach identity matching or make a new initial reply yes.
    Keep every surname token exact; a first-name prefix or short final-e spelling can be a signature variant.
    """
    if not isinstance(call, dict) or "?" in (text or "") or _unreadable_text(text) or _explicit_takeback(text):
        return False
    name, words = fold(call.get("name", "")).split(), _fold_keep_marks(text).split()
    if len(name) < 2 or len(words) <= len(name) or words[-len(name)+1:] != name[1:]:
        return False
    first = words[-len(name)]
    short = (3 <= len(first) < len(name[0]) and (name[0].startswith(first)
             or (first.endswith("e") and len(first) >= 4 and name[0].startswith(first[:-1]))))
    if first != name[0] and not short:
        return False
    body = " ".join(words[:-len(name)])
    places = {re.sub(r"^the\s+", "", fold(call.get(key, ""))) for key in ("site", "venue")}
    arrival = (r"(?:(?:i am|im|i have|ive)\s+)?(?:(?:just|now)\s+)?"
               r"(?:on\s+(?:the\s+)?site(?:\s+at)?|onsite(?:\s+at)?|arrived(?:\s+at)?|here\s+at|reached)\s+(?:the\s+)?")
    return (any(place and re.fullmatch(arrival + re.escape(place), body) for place in places)
            and judge(body, call) == "yes")


BENIGN_AFTER_YES = re.compile(
    r"^(?:(?:ok|okay|k|grand|perfect|cheers|thanks|thank\s+you|thx|ty|ta|nice|great|sound|noted|got\s+it|sorted|lol|haha+"
    r"|see\s+you(?:\s+(?:later|soon|then|there))?|good\s*night|night|have\s+a\s+good\s+(?:night|one|shift)|you\s+too"
    r"|mate|sir|bro|boss|man|a\s+lot|very\s+much|x+)\s*)+$")
_ASK_ITEM = (r"(?:(?:the|my|your|our|a|tonights|todays)\s+)*(?:(?:staff|contact|office)\s+)?"
             r"(?:names?|list|roster|rota|rosta|timesheet|time\s+sheet|sheet|location|address|pin|code|"
             r"parking|uniform|radio|payslip|pay|wages|number|email)")
BENIGN_ASK = re.compile(
    r"^(?:(?:sorry(?:\s+about\s+that)?|please)\s+)*"
    r"(?:(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?(?:send|give|share)\s+(?:(?:me|us)\s+)?" + _ASK_ITEM +
    r"|(?:can|could|may)\s+(?:i|we)\s+(?:get|have)\s+" + _ASK_ITEM +
    r"|(?:what|where)\s+(?:is|are)\s+" + _ASK_ITEM +
    r"|where\s+(?:can|do|should)\s+(?:i|we)\s+park"
    r"|(?:am|are)\s+(?:i|we)\s+on\s+" + _ASK_ITEM +
    r"|(?:who\s+is|whos)\s+(?:working|on|in)\s+with\s+me)"
    r"(?:\s+(?:please|thanks|thank\s+you|mate|sir|boss))*$")


def _benign_after_yes(text):
    """A follow-up that plainly does not change tonight: thanks / see you / a smiley, or asking for the list, the
    names, the location, the timesheet... (never when it also carries a cancellation or a doubt)."""
    if _unreadable_text(text) or _explicit_takeback(text) or _shift_uncertain(text):
        return False
    words = _fold_keep_marks(text)
    if not words:
        return True                                     # emoji / punctuation only
    # A harmless request must account for the whole message. A keyword such as 'list' or 'send' cannot hide
    # an otherwise unrecognised problem in another clause, including an apologetic question.
    return bool(BENIGN_AFTER_YES.fullmatch(words)) or bool(BENIGN_ASK.fullmatch(words))


def _minute_conflict(events, yes, call=None, day=None):
    """Different messages in one minute have no inferred before/after order, even when a Yes sorts first."""
    return next((e for e in events if _identity(e) != _identity(yes)
                 and _message_key(e)[-1] == _message_key(yes)[-1]
                 and _direction(e) != "outgoing" and e.get("what") not in ("deleted", "reaction")
                 and not is_call(e) and _conflicting_reply(e, call, day)), None)


def _undone(after, yes, call=None, day=None):
    """A later no, explicit shift doubt or voice note needs the controller."""
    return next((e for e in after if _at(e) > _at(yes) and e is not yes and not is_call(e)
                 and _conflicting_reply(e, call, day)), None)


def _same_message(a, b):
    """Is a reaction's 'to' (as WhatsApp shows it, perhaps cut short) exactly the message b?"""
    x, y = fold(a), fold(b)
    return bool(x) and bool(y) and (x == y or (len(x) >= 12 and y.startswith(x) and len(x) >= len(y) - 3))


def _message_key(e):
    # Keep punctuation and emoji: "Ok?" is not "Ok", and a thumbs-down is not a thumbs-up.
    text = " ".join(unicodedata.normalize("NFKC", e.get("text") or "").casefold().split())
    minute = _at(e).replace(second=0, microsecond=0)
    if e["kind"] == "line" and re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", e.get("shown", "")):
        # A preview can render seconds after the clock minute changes. Its displayed message clock still
        # identifies the bubble; the observation time must not turn it into a different incoming message.
        shown = _at(dict(e, snapshot=True))
        if shown is not None:
            minute = shown
    return (e["chat"], text, e.get("what", "text"), e.get("emoji", ""), e.get("media_type", ""),
            minute)


def _identity(e):
    """One collapsed message can answer only one shift; distinct bubble IDs remain distinct."""
    return (_direction(e), e.get("id", ""), *_message_key(e))


def _collapse(events, reactions=None):
    """Bubbles prove direction; previews can supply seconds only when attribution is unambiguous.

    Older list readers briefly reported an outgoing message as incoming before its ticks appeared. Opposite
    line-only observations are therefore uncertainty, not two messages. Real bubbles in both directions remain.
    """
    groups = {}
    for e in events:
        if _at(e) is None:
            continue                                   # malformed historical clocks never become a fresh confirmation
        groups.setdefault(_message_key(e), []).append(e)
    out = []
    for group in groups.values():
        if any(_truncated(e) for e in group):
            group = [dict(e, truncated=True) for e in group]  # a short preview cannot erase a known incomplete body
        bubbles = {}
        for e in group:
            if e["kind"] == "bubble" and _direction(e) != "unknown":
                bubbles.setdefault((_direction(e), e.get("id", "")), e)
        for direction in ("incoming", "outgoing"):
            if any(d == direction and mid for d, mid in bubbles):
                bubbles.pop((direction, ""), None)     # an old reader's ID-less copy is not an extra message
        if len(bubbles) > 1:
            # Identical real messages can travel both ways in one minute. A preview cannot identify which one.
            ids = {}
            for (direction, mid), e in bubbles.items():
                ids.setdefault(mid or direction, []).append(e)
            for copies in ids.values():
                direction = _direction(copies[0]) if len(copies) == 1 else "unknown"
                out.append(_with_direction(copies[0], direction))
            continue
        if bubbles:
            bubble = next(iter(bubbles.values()))
            direction = _direction(bubble)
            lines = [e for e in group if e["kind"] == "line" and not e.get("snapshot")
                     and _direction(e) == direction
                     and _at(e).replace(second=0, microsecond=0) == _message_key(e)[-1]]
            chosen = min(lines, key=_at) if lines else bubble
            chosen = _with_direction(chosen, direction)
            if bubble.get("id"):
                chosen["id"] = bubble["id"]
            out.append(chosen)
            continue
        directions = {_direction(e) for e in group}
        direction = next(iter(directions)) if len(directions) == 1 else "unknown"
        if directions == {"outgoing", "unknown"}:
            direction = "outgoing"                     # ticks eventually appearing resolves an unknown preview
        eligible = [e for e in group if _direction(e) == direction] or group
        chosen = min(eligible, key=lambda e: (e["kind"] != "line" or bool(e.get("snapshot")), _at(e)))
        chosen = _with_direction(chosen, direction)
        if chosen["kind"] == "line" and _at(chosen).replace(second=0, microsecond=0) != _message_key(chosen)[-1]:
            chosen["snapshot"] = True                 # delayed rendering is not a new message or a precise clock
        out.append(chosen)
    if reactions:
        for e in out:
            state = reactions.get((e["chat"], e.get("id")))
            if state:
                e["_attached_reaction"] = state if state["identity"] == _identity(e) else dict(state, mode="unknown")
    return out


def _needs_review(entry, e, yes=None):
    entry.pop("reply_at", None)
    entry.pop("reply_source", None)
    entry.update(how="check", response="", direction_unknown=True,
                 source=e.get("kind"), said=(e.get("text") or e.get("what") or "message")[:80], at=_hhmm(_at(e)))
    if yes is not None:
        entry.update(taken_back=True, first=(yes.get("text") or yes.get("emoji") or "")[:40])


def _set_reply_clock(entry, event):
    """Keep the selected incoming message's displayed clock, never a question/reaction observation time."""
    entry.pop("reply_at", None)
    entry.pop("reply_source", None)
    if _direction(event) != "incoming" or _truncated(event) or event.get("what") in ("reaction", "deleted") or is_call(event):
        return
    if event.get("kind") == "bubble":
        at, source = _at(event), "bubble"
    elif event.get("kind") == "line" and re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", event.get("shown", "")):
        at, source = _at(dict(event, snapshot=True)), "preview_clock"
    else:
        return
    if at is not None:
        entry.update(reply_at=at.isoformat(timespec="minutes"), reply_source=source)


def _within(e, lo, hi):
    """lo < message < hi, strictly. A message known only to the minute (a bubble) that falls in the same minute as a
    limit cannot be placed on either side of it, so it is left out (it stays for the controller to read)."""
    at = _at(e)
    if not lo < at < hi:
        return False
    if e["kind"] == "bubble" or e.get("snapshot"):
        minute = at.replace(second=0, microsecond=0)
        if minute == lo.replace(second=0, microsecond=0) or minute == hi.replace(second=0, microsecond=0):
            return False
    return True


def _attached_context_clear(q, events, call):
    """A reaction has no occurrence clock, so never place it after contradictory/newer subjects."""
    minute = _message_key(q)[-1]
    for e in events:
        if _identity(e) == _identity(q) or _message_key(e)[-1] < minute:
            continue
        if (_direction(e) == "outgoing" and not question_venue(e.get("text")) and _chaser(e, call, q)):
            continue                                   # a bare nudge/call adds no different subject
        return False
    return True


def _reply_for_question(q, evs, c, day, pair_ok, reply_until=None, confirmation_emojis=()):
    """Evaluate one question, keeping reply boundaries separate from later withdrawals."""
    asked, selected_yes = _at(q), None
    entry = {"time": _hhmm(asked), "how": "asked", "response": ""}
    # Early, dated, uniquely named questions may be answered through the assignment's boundary.
    # Ordinary questions/reactions retain their existing window; a new subject still closes either.
    reaction_close = asked + datetime.timedelta(hours=REPLY_HOURS)
    close = reply_until if reply_until is not None else reaction_close
    uncertain = None
    for e in evs:
        direction = _direction(e)
        if direction == "unknown" and _within(e, asked, close):
            uncertain = e
            close = _at(e)
            break
        if direction == "outgoing" and _at(e) > asked and e is not q and not _chaser(e, c, q):
            close = min(close, _at(e))
            break
    after = [e for e in evs if _direction(e) == "incoming" and (_within(e, asked, close) or pair_ok(q, e)) and e.get("what") != "deleted"
             and not is_call(e)]
    reaction = next((e for e in after if e.get("what") == "reaction" and _at(e) < reaction_close), None)
    first = next((e for e in after if e.get("what") != "reaction"), None)
    if reaction and not _truncated(reaction) and (first is None or _at(reaction) <= _at(first)) and has_yes_emoji(reaction.get("emoji", "")) \
            and _same_message(reaction.get("to", ""), q.get("text", "")):
        entry.update(how="reaction", response="Ok", said=reaction.get("emoji", ""), at=_hhmm(_at(reaction)))
    elif first is not None:
        kind = first.get("what", "text")
        verdict = "unclear" if _truncated(first) else judge(first.get("text"), c) if kind == "text" else kind
        if kind == "text" and not _truncated(first) and _confirmation_emoji(first.get("text")) in confirmation_emojis:
            verdict = "yes"                            # exact saved meaning, only as this person's answer to this question
        entry.update(said=(first.get("text") or kind)[:80], at=_hhmm(_at(first)),
                     how="reply" if verdict == "yes" else ("check" if kind == "text" else kind),
                     response="Ok" if verdict == "yes" else "")
        _set_reply_clock(entry, first)
        if _truncated(first):
            entry["text_truncated"] = True
        if kind == "media" and first.get("media_type") == "sticker":
            entry["media_type"] = "sticker"
    elif reaction is not None and _truncated(reaction):
        entry.update(how="check", response="", text_truncated=True, said=reaction.get("emoji", ""), at=_hhmm(_at(reaction)))
    elif uncertain is not None:
        _needs_review(entry, uncertain)
    if entry.get("response") == "Ok":
        yes = reaction if entry["how"] == "reaction" else first
        selected_yes = yes
        conflict = _minute_conflict(evs, yes, c, day)
        shift_start = datetime.datetime.combine(day, datetime.time()) + datetime.timedelta(minutes=c["start"] % 1440)
        until = shift_start + datetime.timedelta(minutes=WITHDRAW_AFTER_START)
        back = conflict or _undone(_takeback_events(evs, yes, c, q, until, day, reply_until), yes, c, day)
        if back is not None:
            # taken_back: the window's Auto green may already have turned him green on that yes - it turns
            # the name white again (a blank response alone never does)
            kind = back.get("what", "text")
            entry.update(how="check" if kind == "text" else kind, response="",
                         said=(back.get("text") or kind)[:80], at=_hhmm(_at(back)),
                         taken_back=True, first=(yes.get("text") or yes.get("emoji") or "")[:40])
            _set_reply_clock(entry, back)
            if _truncated(back):
                entry["text_truncated"] = True
            if kind == "media" and back.get("media_type") == "sticker":
                entry["media_type"] = "sticker"
            if _direction(back) == "unknown":
                _needs_review(entry, back, yes)
            if conflict is not None:
                entry["same_minute_conflict"] = True
    state = q.get("_attached_reaction")
    if state and entry.get("how") != "reply" and entry.get("response") != "Ok":
        late = (state.get("empty_seen") is not None and
                datetime.datetime.fromtimestamp(state["empty_seen"] / 1000.0) >= min(close, reaction_close))
        if state["mode"] == "positive" and entry["how"] == "asked" and late:
            # read with no thumbs-up after the reply window had closed: whatever came later is not an answer in time
            entry.update(how="check", response="", reaction_review=True, reaction_late=True, said=state["emoji"])
            if state["previous"]:
                entry["taken_back"] = True
            entry.pop("at", None)
        elif state["mode"] == "positive" and entry["how"] == "asked" and _attached_context_clear(q, evs, c):
            entry.update(how="reaction", response="Ok", said=state["emoji"], attached_reaction=True)
            entry.pop("at", None)                      # neither the question clock nor observation is a reaction time
            selected_yes = q                           # question identity/anchor only, never a fabricated reaction event
        elif state["previous"] or state["mode"] in ("positive", "unknown", "contact"):
            entry.update(how="check", response="", reaction_review=True,
                         reaction_removed=state["mode"] == "empty" and state["previous"])
            if state["previous"]:
                entry["taken_back"] = True
    elif state and entry.get("how") == "reaction" and not entry.get("attached_reaction") and state["mode"] != "positive":
        # An exact parent's newer empty/unknown snapshot supersedes an old timed reaction preview.
        entry.update(how="check", response="", reaction_review=True, taken_back=True,
                     reaction_removed=state["mode"] == "empty")
    return entry, selected_yes


def _minute_pair_prover(direct, commits):
    """Only a complete capture's ordered DOM snapshot can resolve equal minute clocks.

    Count all captured context, even quarantined context. Collapsed previews may carry useful
    seconds, but only original, committed bubbles can prove the order of the two messages.
    """
    minutes, proofs = {}, {}
    for e in _collapse(direct):
        minutes.setdefault((e["chat"], _message_key(e)[-1]), []).append(e)
    for e in direct:
        if (e.get("kind") == "bubble" and e.get("what", "text") in ("text", "media") and not _truncated(e)
                and isinstance(e.get("id"), str) and 0 < len(e["id"]) <= 200
                and isinstance(e.get("view_id"), str) and re.fullmatch(r"[a-f0-9]{32}", e["view_id"])
                and type(e.get("i")) is int and 0 <= e["i"] <= 10000
                and _read_event_committed(e, commits)):
            proofs.setdefault(_identity(e), []).append(e)

    def proven(q, reply):
        if (_direction(q) != "outgoing" or _direction(reply) != "incoming"
                or not q.get("id") or not reply.get("id") or q["id"] == reply["id"]
                or q.get("what", "text") != "text" or reply.get("what", "text") != "text"
                or _truncated(q) or _truncated(reply) or q["chat"] != reply["chat"]
                or _message_key(q)[-1] != _message_key(reply)[-1]):
            return False
        pair = minutes.get((q["chat"], _message_key(q)[-1]), ())
        main = {_identity(q), _identity(reply)}
        if len(pair) < 2 or not main <= {_identity(e) for e in pair}:
            return False
        trailing = [e for e in pair if _identity(e) not in main]
        if any(_direction(e) != "incoming" or e.get("what") != "media" or (e.get("text") or "").strip()
               or not e.get("id") or _truncated(e) for e in trailing):
            return False                              # captions, voice, unknowns and other replies still need review
        if len({e.get("id") for e in pair}) != len(pair):
            return False
        for a in proofs.get(_identity(q), ()):
            for b in proofs.get(_identity(reply), ()):
                if a["read_id"] != b["read_id"] or a["view_id"] != b["view_id"] or a["i"] >= b["i"]:
                    continue
                positions = {a["i"], b["i"]}
                for media in trailing:
                    choices = {p["i"] for p in proofs.get(_identity(media), ()) if p["read_id"] == a["read_id"]
                               and p["view_id"] == a["view_id"] and p["i"] > b["i"]}
                    if len(choices) != 1 or choices & positions:
                        break
                    positions.update(choices)
                else:
                    return True
        return False
    return proven


def _resolved_loading_previews(direct, commits):
    """Retire one known sidebar transport notice only after unambiguous complete replacement evidence.

    This exact supported WhatsApp notice is not a guard's answer. It has no old message ID,
    so require a later complete capture, a unique stable incoming text in that minute, and
    no competing message/context anywhere in the observations. Keep the source records intact.
    """
    def notice(e):
        return (e.get("kind") == "line" and _direction(e) == "unknown" and not e.get("id")
                and e.get("what", "text") == "text" and not _truncated(e)
                and (e.get("text") or "").strip() == "Waiting for this message. Check your phone.")
    if not any(notice(e) for e in direct):
        return direct
    batches = {}
    for e in direct:
        if e.get("kind") == "bubble" and _read_event_committed(e, commits):
            batches.setdefault((e["read_id"], e["chat"], _message_key(e)[-1]), []).append(e)
    replacements = {}
    for (read_id, chat, minute), batch in batches.items():
        observed = _collapse(batch)
        if len(observed) != 1:
            continue
        e = observed[0]
        receipt = commits[read_id]
        if (_direction(e) == "incoming" and e.get("what", "text") == "text" and e.get("text")
                and e.get("id") and not _truncated(e) and e["seen"] >= receipt["created"]):
            replacements.setdefault((chat, minute), []).append((e, receipt["created"]))
    out = []
    for e in direct:
        if notice(e):
            key = (e["chat"], _message_key(e)[-1])
            choices = [(replacement, created) for replacement, created in replacements.get(key, ()) if created > e["seen"]]
            if choices:
                context = _collapse([other for other in direct if other["chat"] == key[0]
                                     and _message_key(other)[-1] == key[1] and not notice(other)])
                if len(context) == 1 and any(_identity(context[0]) == _identity(replacement) for replacement, _ in choices):
                    continue
        out.append(e)
    return out


def _resolved_sticker_previews(direct, commits):
    """Match the known sidebar Sticker descriptor to a captured sticker, never to a photo/caption."""
    def preview(e):
        return (e.get("kind") == "line" and _direction(e) == "unknown" and not e.get("id")
                and e.get("what") == "media" and (e.get("text") or "").strip() == "Sticker" and not _truncated(e))
    if not any(preview(e) for e in direct):
        return direct
    candidates = {}
    for e in direct:
        if (e.get("kind") == "bubble" and e.get("id") and _direction(e) == "incoming"
                and e.get("what") == "media" and e.get("media_type") == "sticker" and not (e.get("text") or "").strip()
                and not _truncated(e) and _read_event_committed(e, commits)):
            candidates.setdefault((e["chat"], _message_key(e)[-1]), []).append(e)
    out = []
    for e in direct:
        if preview(e):
            key = (e["chat"], _message_key(e)[-1])
            choices = candidates.get(key, ())
            media = _collapse([o for o in direct if o["chat"] == key[0] and _message_key(o)[-1] == key[1]
                               and o.get("what") == "media" and not preview(o)])
            if (len(media) == 1 and choices and all(o.get("id") == media[0].get("id") for o in choices)
                    and any(_identity(media[0]) == _identity(o) and e["seen"] <= commits[o["read_id"]]["completed"] for o in choices)):
                continue
        out.append(e)
    return out


def heard(log, events, taught=None, trusted=None, read_commits=None, confirmation_emojis=None):
    """What WhatsApp says about each call on a log: {edit key: {...}} - see the module notes for the rules.
    Pure: the events and the log come in, nothing is read or written here.

    The company phone carries far more than check calls - shift offers, holiday requests, CVs, managers, groups -
    so a reply only answers a check call when ALL of this holds: the chat is one guard on tonight's log and nobody
    else (match_chats), it is not a group, our question names his venue and went into that same chat, and his
    message is the first thing after it - before we moved on to anything else, within its reply window - and every word
    of it is a plain yes (judge). A thumbs-up counts only on the question itself. Anything less certain is shown,
    never turned green. A dated early question naming exactly one assignment stays open until start+90 minutes;
    ordinary questions and unprompted check-ins keep their existing narrower bounds."""
    from calllog import edit_key                       # (late: calllog imports this module too)
    trusted = trusted if trusted is not None else set()
    confirmation_rules = _confirmation_rules(confirmation_emojis)
    calls = [c for c in log["calls"] if c.get("name")]
    date = datetime.date.fromisoformat(log["meta"]["date_iso"])
    midnight = datetime.datetime.combine(date, datetime.time())
    people = sorted({c["name"] for c in calls})
    direct = [e for e in events if not e.get("group") and _at(e) is not None]
    # Resolve identity against ALL labels first, so a second ambiguous chat still blocks a match.
    # Unmatched histories cannot answer a call and need no collapse/provenance/reaction processing.
    chats = match_chats(sorted({e["chat"] for e in direct}), people, taught)
    direct = [e for e in direct if e["chat"] in chats]
    cut_ids = {(e["chat"], e["id"]) for e in direct if e.get("id") and _truncated(e)}
    direct = [dict(e, truncated=True) if _truncated(e) or (e.get("chat"), e.get("id")) in cut_ids else e for e in direct]
    direct = _resolved_loading_previews(direct, read_commits)
    direct = _resolved_sticker_previews(direct, read_commits)
    pair_ok = _minute_pair_prover(direct, read_commits)
    incomplete = [e for e in direct if "read_id" in e and not _read_event_committed(e, read_commits)]
    # Filter before collapse: a later complete snapshot can resolve older partial copies
    # of the same bubble, while untagged passive/manual evidence retains its prior rules.
    approved = [e for e in direct if "read_id" not in e or _read_event_committed(e, read_commits)]
    dated_today = {_identity(e) for e in approved if e.get("kind") == "bubble" and e.get("date")
                   and _direction(e) == "outgoing" and _at(e).date() == date}
    events = _collapse(approved, _reaction_states(direct, read_commits))
    approved_ids, approved_bubbles, incomplete_by_person = {}, {}, {}
    for e in events:
        approved_ids.setdefault((_direction(e), _message_key(e)), set()).add(e.get("id", ""))
    # A precise preview may become the collapsed representative. Keep its actual
    # bubble's proof so repeated unknown sidebar copies remain duplicates.
    for e in approved:
        if e["kind"] == "bubble" and _direction(e) != "unknown" and e.get("id"):
            approved_bubbles.setdefault(_message_key(e), set()).add(e["id"])
    for e in incomplete:
        if chats.get(e["chat"]):
            incomplete_by_person.setdefault(chats[e["chat"]], []).append(e)

    def already_approved(e):
        ids = approved_ids.get((_direction(e), _message_key(e)), set())
        return ((bool(ids) and (not e.get("id") or "" in ids or e["id"] in ids))
                or bool(e.get("id") and e["id"] in approved_bubbles.get(_message_key(e), set()))
                or bool(e["kind"] == "line" and approved_bubbles.get(_message_key(e))))

    by_chat = {}
    for e in events:
        if chats.get(e["chat"]):
            by_chat.setdefault(e["chat"], []).append(e)
    for chat in by_chat:
        by_chat[chat].sort(key=lambda e: (_at(e), e.get("i", 0), e["seen"]))
    out = {}
    for person in people:
        mine = sorted((c for c in calls if c["name"] == person), key=lambda c: c["start"])
        personal_emojis = confirmation_rules.get(wa_beta.normalize_name(person), ())
        his_chats = [ch for ch, who in chats.items() if who == person and ch in by_chat]
        questions = [(e, _at(e), question_venue(e.get("text")), ch) for ch in his_chats for e in by_chat[ch]
                     if _direction(e) == "outgoing" and e.get("what", "text") == "text" and not _truncated(e) and question_venue(e.get("text"))]
        questions.sort(key=lambda q: q[1])
        used, own_used, reply_used = set(), set(), set()
        yes_at = {}
        for c in mine:
            start = midnight + datetime.timedelta(minutes=c["start"])
            lo, hi = start - datetime.timedelta(hours=3), start + datetime.timedelta(minutes=90)
            # A verified question earlier today can explicitly ask about tonight already. It must name
            # exactly one of this person's assignments; generic/stale/ambiguous questions gain no extra scope.
            early_ids = {_identity(q[0]) for q in questions if q[1] < lo and _identity(q[0]) in dated_today
                         and _specific_venue(q[2], c) and sum(_specific_venue(q[2], other) for other in mine) == 1}
            near = [q for q in questions if _identity(q[0]) not in used and
                    ((lo <= q[1] <= hi and venue_matches(q[2], c)) or _identity(q[0]) in early_ids)]
            near.sort(key=lambda q: q[2] == ANY)          # one that names his venue first; then "All good ?" (stable)
            entry, confirmed_at, confirmed_question = {}, None, None
            if near:
                q, asked, _, chat = near[0]
                confirmed_question = q
                used.add(_identity(q))
                entry, selected_yes = _reply_for_question(q, by_chat[chat], c, start.date(), pair_ok,
                                                        hi if _identity(q) in early_ids else None, personal_emojis)
                # With one assignment, a later matching question explicitly renews this call's
                # answer window. Never let an unanswered reminder erase an established reply.
                # Multi-assignment conversations keep one-question ownership; renewal is ambiguous there.
                if len(mine) == 1:
                    # The legacy venue matcher tolerates aliases/typos. Renewal must not extend a
                    # different named venue merely because it shares a word with this one.
                    renewed = sorted((item for item in near if item[3] == chat and item[1] > asked
                                      and _same_call_question(item[2], c, q)), key=lambda item: item[1])
                    for next_q, _, _, _ in renewed:
                        newer, newer_yes = _reply_for_question(next_q, by_chat[chat], c, start.date(), pair_ok,
                                                              hi if _identity(next_q) in early_ids else None, personal_emojis)
                        used.add(_identity(next_q))
                        if newer.get("how") == "asked":
                            continue
                        if (entry.get("response") == "Ok" or entry.get("taken_back")) and newer.get("response") != "Ok":
                            newer.update(taken_back=True, first=(entry.get("first") or entry.get("said") or "yes")[:40])
                        newer["time"] = _hhmm(asked)    # preserve the actual first check-call time
                        entry, selected_yes = newer, newer_yes
                        confirmed_question = next_q
                if selected_yes is not None:
                    confirmed_at = _at(selected_yes)
                    if entry.get("response") == "Ok" and _identity(selected_yes) in reply_used:
                        entry.update(response="", how="check", shared_reply=True, taken_back=True)
                    reply_used.add(_identity(selected_yes))
            elif len(his_chats) == 1:                   # not asked: what he said of his own accord
                evs = by_chat[his_chats[0]]
                is_trusted = fold(person) in trusted
                lo2 = start - datetime.timedelta(hours=OWN_QUIET_HOURS if is_trusted else 3)
                hi2 = start + datetime.timedelta(minutes=60)

                def quiet_before(e):
                    at = _at(e)
                    return not any(_direction(o) != "incoming" and o is not e
                                   and at - datetime.timedelta(hours=OWN_QUIET_HOURS) <= _at(o) <= at for o in evs)

                own = [e for e in evs if _direction(e) != "outgoing" and not e.get("snapshot")
                       and e.get("what") not in ("deleted", "reaction") and lo2 <= _at(e) <= hi2
                       and _identity(e) not in own_used and not is_call(e)]
                # A yes is a check-in (his rule: automatic) - only if it follows nothing of ours for OWN_QUIET_HOURS,
                # so a "Yes" to this afternoon's offer is not a check-in. Anything else he says - "sick tonight,
                # can't make it" - is shown; from a trusted senior it is caught however it came about, because it
                # is the one thing that must stop him being assumed Ok.
                pick = None
                for e in own:
                    if _direction(e) == "unknown":
                        pick = (e, False)               # a preview is useful context, but cannot identify the sender
                        break
                    kind = e.get("what", "text")
                    if kind == "text" and len(mine) > 1 and not venue_matches(e.get("text") or "", c) \
                            and any(venue_matches(e.get("text") or "", o) for o in mine if o is not c):
                        continue                       # it names his other venue tonight: it is that call's, not this one's
                    yes = kind == "text" and not _truncated(e) and judge(e.get("text"), c) == "yes"
                    if yes and quiet_before(e) and _at(e) >= start - datetime.timedelta(hours=3):
                        # two shifts tonight: a check-in that names a venue is for that venue's call; one that names
                        # none goes to the first call only (own_used stops it answering the second too)
                        names_this = venue_matches(e.get("text") or "", c)
                        names_other = any(venue_matches(e.get("text") or "", o) for o in mine if o is not c)
                        if len(mine) == 1 or names_this or not names_other:
                            pick = (e, True)
                            break
                    elif not yes and (is_trusted or (quiet_before(e) and _at(e) >= start - datetime.timedelta(hours=3))):
                        pick = (e, False)
                        break
                taken = None                           # the yes he took back, if he did
                if pick and pick[1]:
                    # taken back straight after ("all good" ... "I've had swelling in my foot"): shown, not green
                    e = pick[0]
                    conflict = _minute_conflict(evs, e, c, start.date())
                    back = conflict or _undone(_takeback_events(evs, e, c, None, start + datetime.timedelta(minutes=WITHDRAW_AFTER_START),
                                                                start.date()), e, c, start.date())
                    if back is not None:
                        own_used.add(_identity(e))
                        taken = (e.get("text") or "")[:40]
                        pick = (back, False)
                if pick:
                    e, yes = pick
                    own_used.add(_identity(e))
                    kind = e.get("what", "text")
                    entry = {"time": _hhmm(_at(e)), "how": "checkin" if yes else ("check" if kind == "text" else kind),
                             "response": "Ok" if yes else "", "said": (e.get("text") or kind)[:80], "at": _hhmm(_at(e))}
                    _set_reply_clock(entry, e)
                    if _truncated(e):
                        entry["text_truncated"] = True
                    if kind == "media" and e.get("media_type") == "sticker":
                        entry["media_type"] = "sticker"
                    if yes:
                        confirmed_at = _at(e)
                    if taken is not None:
                        entry.update(taken_back=True, first=taken)
                        if conflict is not None:
                            entry["same_minute_conflict"] = True
                    if _direction(e) == "unknown":
                        _needs_review(entry, e)
            partial = [e for e in incomplete_by_person.get(person, ())
                       if (entry.get("attached_reaction") or _direction(e) != "outgoing") and not is_call(e) and not already_approved(e)]
            if entry.get("response") == "Ok":
                if entry.get("attached_reaction"):
                    partial = [e for e in partial if confirmed_at is not None
                               and _message_key(e)[-1] >= confirmed_at.replace(second=0, microsecond=0)]
                else:
                    close = hi if confirmed_question is not None and _identity(confirmed_question) in early_ids else (
                        confirmed_at + datetime.timedelta(hours=REPLY_HOURS) if confirmed_at else hi)
                    for known in events:
                        if chats.get(known["chat"]) == person and _direction(known) == "outgoing" and confirmed_at is not None \
                                and _at(known) > confirmed_at and not _same_shift_followup(known, c, confirmed_question):
                            close = min(close, _at(known))
                    partial = [e for e in partial if confirmed_at is not None and _at(e) < close
                               and (confirmed_at <= _at(e) or _message_key(e)[-1] == confirmed_at.replace(second=0, microsecond=0))]
            else:
                partial = [e for e in partial if lo <= _at(e) <= hi]
            if partial:
                message = max(partial, key=lambda e: (_at(e), e.get("seen", 0)))
                was_confirmed = entry.get("response") == "Ok"
                entry = {"time": entry.get("time", _hhmm(_at(message))), "how": "check", "response": "",
                         "read_incomplete": True, "said": (message.get("text") or message.get("what") or "message")[:80],
                         "at": _hhmm(_at(message))}
                last_complete = max((receipt["completed"] for receipt in (read_commits or {}).values()
                                     if _valid_read_receipt(receipt) and receipt["chat"] == message["chat"]), default=None)
                if last_complete is not None and int(_at(message).timestamp() * 1000) > last_complete:
                    entry["read_reason"] = "new_context"
                if was_confirmed:
                    entry["taken_back"] = True          # lost automatic confidence, never claims the guard cancelled
            if entry:
                out[edit_key(c)] = entry
                yes_at[edit_key(c)] = confirmed_at if entry.get("response") == "Ok" else None
        # Two shifts tonight (8 Nov review): "Sorry I'm sick, can't work tonight at all" after both yeses used to turn
        # only the shift it fell nearest to white; the other stayed green. A clear cancellation that does not name his
        # other venue, and is not about another day, now withdraws every shift whose yes came before it (up to 90 min
        # after that shift's start).
        if len(mine) > 1:
            for c in mine:
                k = edit_key(c)
                entry, since = out.get(k), yes_at.get(k)
                if not entry or entry.get("response") != "Ok" or since is None:
                    continue
                start = midnight + datetime.timedelta(minutes=c["start"])
                until = start + datetime.timedelta(minutes=WITHDRAW_AFTER_START)
                for ch in his_chats:
                    back = next((e for e in by_chat[ch] if _direction(e) == "incoming" and e.get("what", "text") == "text"
                                 and not is_call(e) and _at(e) is not None and since < _at(e) <= until
                                 and _explicit_takeback(e.get("text") or "")
                                 and not _another_day(e.get("text") or "", start.date(), _at(e).date())
                                 and not any(venue_matches(e.get("text") or "", o) for o in mine if o is not c)), None)
                    if back is not None:
                        entry.update(how="check", response="", said=(back.get("text") or "")[:80], at=_hhmm(_at(back)),
                                     taken_back=True, first=(entry.get("said") or "")[:40])
                        _set_reply_clock(entry, back)
                        break
    return out


def _edits_dir():
    """calllog's folder for hand corrections - the checks point it at a scratch folder, and so does this."""
    mod = sys.modules.get("calllog")
    return getattr(mod, "EDITS", EDITS) if mod else EDITS


def heard_file(stem):
    return os.path.join(_edits_dir(), stem + " whatsapp.json")


def _shift_of(got):
    shift = got.get("__shift__") if isinstance(got, dict) else None
    return shift if isinstance(shift, dict) else {}


def load_heard(stem):
    """The overlay calllog.build() reads: {} when the reader was never live for this shift - or when the file is
    damaged in any way (a damaged file must never stop the log being made)."""
    got = _read_json(heard_file(stem), {})
    if not _shift_of(got).get("waiting"):
        return {}
    return {k: v for k, v in got.items() if k == "__shift__" or isinstance(v, dict)}


_last = {}                                             # overlay path -> (input signature, successfully persisted output)


def update(log, now=None):
    """Work out the log's replies and keep them for calllog.build(). Returns True if anything changed.

    - The overlay only starts once the reader is really live during the shift: no add-on, no change at all. When
      it starts it records the moment (since_at): calls made before then were never seen and keep the old Ok.
    - Once the shift is over (two hours after it ends) the overlay is final. It is never worked out again - the
      day logs it came from are deleted after a week, and doing it again then would wipe the night's answers.
    - Nothing is worked out again unless something it reads has changed (this runs every 15 s in the window).
    """
    meta = log["meta"]
    if not is_for(meta.get("supervisor")):
        return False
    now = now or datetime.datetime.now()
    date = datetime.date.fromisoformat(meta["date_iso"])
    midnight = datetime.datetime.combine(date, datetime.time())
    begins = midnight + datetime.timedelta(minutes=meta["start_min"])
    ends = midnight + datetime.timedelta(minutes=meta["end_min"])
    if now > ends + datetime.timedelta(hours=2):
        return False                                    # finished: final, whatever is or is not on disk now
    old = _read_json(heard_file(log["stem"]), {})
    shift = _shift_of(old)
    if not shift.get("waiting"):
        if not live(now.timestamp()) or not begins - datetime.timedelta(hours=2) <= now <= ends:
            return False                                # the reader was not live during this shift
        shift = {"waiting": True, "since": now.strftime("%H:%M"), "since_at": now.isoformat(timespec="seconds")}
    start, end = min(begins - datetime.timedelta(hours=LOOK_BACK_HOURS + 1), midnight), min(now, ends + datetime.timedelta(hours=2))
    days, day = [], start.date()
    while day <= end.date():
        try:
            st = os.stat(_day_file(day))
            days.append((day.isoformat(), st.st_size, st.st_mtime_ns))
        except OSError:
            days.append((day.isoformat(), 0, 0))
        day += datetime.timedelta(days=1)
    names_stamp = []
    for alias_path in (NAMES, _beta_file(), read_coverage_file(), SETTINGS):
        try:
            st = os.stat(alias_path)
            names_stamp.append((st.st_mtime_ns, st.st_size))
        except OSError:
            names_stamp.append((0, 0))
    call_sign = tuple(sorted((c.get("name", ""), c.get("site", ""), c.get("venue", ""),
                              c.get("shift", ""), c.get("start")) for c in log["calls"]))
    sign = (tuple(days), tuple(names_stamp), call_sign, json.dumps(shift, sort_keys=True),
            meta.get("date_iso"), meta.get("supervisor"), meta.get("start_min"), meta.get("end_min"))
    cache_key = os.path.abspath(heard_file(log["stem"]))
    # Another running window or a recovery restore can replace the overlay without touching its inputs.
    # It is already loaded above: only skip when both the inputs AND persisted result are still ours.
    if _last.get(cache_key) == (sign, old) and os.path.exists(cache_key):
        return False
    found = heard(log, events_between(start, end), load_aliases(meta.get("supervisor")), trusted_names(), load_read_commits(), load_confirmation_emojis())
    new = dict(found)
    new["__shift__"] = shift
    if new == old:
        _last[cache_key] = (sign, new)
        return False
    _write_json(heard_file(log["stem"]), new)
    _last[cache_key] = (sign, new)                     # a failed write must be retried even with unchanged inputs
    return True


# --- drafts: WhatsApp's own click-to-chat link; a person presses Send -------------------------------------
def number(text):
    """A phone number as WhatsApp wants it: international digits only (087 123 4567 -> 353871234567)."""
    digits = re.sub(r"\D", "", text or "")
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = "353" + digits[1:]                     # an Irish number written the Irish way
    return digits if 9 <= len(digits) <= 15 else ""


def draft_link(phone, text):
    return "whatsapp://send?phone=%s&text=%s" % (number(phone), urllib.parse.quote(text, safe=""))


def draft(name, text, opener=None):
    """Open WhatsApp with the message typed in the box for one person. Only names on the draft list, only
    with a number known for them, and only ever when the user asked. Nothing is sent."""
    conf = settings()
    if fold(name) not in {fold(n) for n in conf["draft_to"]}:
        raise ValueError("%s is not on the draft list (%s)" % (name, ", ".join(conf["draft_to"]) or "nobody"))
    phone = next((v for k, v in conf["phones"].items() if fold(k) == fold(name)), "")
    if not number(phone):
        raise ValueError("no phone number known for %s - add it to %s" % (name, SETTINGS))
    url = draft_link(phone, text)
    (opener or os.startfile)(url)
    return url


def cell_hint(call):
    """What the Response cell shows for a call that has no response yet, while the reader is on - (text, tone) or
    None. Short, because the column is narrow (at the window's usual 1480 px, about 15 letters): his own words in
    quotes (amber = read this), "voice note", "photo", "asked 21:33" (grey = waiting), "reply not read" (grey). The
    full story is in story(), on hover."""
    if call.get("response") or not call.get("call_text"):
        return None
    heard = call.get("heard") or {}
    how = heard.get("how")
    if heard.get("read_incomplete"):
        return "Recheck chat", "amber"
    if heard.get("text_truncated"):
        return "read full text", "amber"
    if heard.get("reaction_late"):
        return "late thumbs-up", "amber"
    if heard.get("reaction_review"):
        return "verify reaction", "amber"
    if heard.get("direction_unknown") or heard.get("shared_reply") or heard.get("same_minute_conflict"):
        return "verify reply", "amber"
    if how == "voice":
        return "voice note", "amber"                   # (what to do with it is said on hover: the column is narrow)
    if how == "media":
        return "sticker" if heard.get("media_type") == "sticker" else "photo", "amber"
    if how == "asked":
        return "Waiting", "ink3"
    if heard:
        return "“%s”" % (heard.get("said") or "").strip(), "amber"
    if call.get("default_response") == "":
        return "reply not read", "ink3"
    return None


def story(call):
    text = _story(call)
    if call.get("time_source") == "whatsapp_reply":
        stamp = (call.get("heard") or {}).get("reply_at", "")
        if isinstance(stamp, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", stamp):
            text = ((text + " ") if text else "") + "Time of call uses the guard's reply clock: %s on %s." % (stamp[11:], stamp[:10])
    elif text and call.get("time_source") == "manual":
        text += " Your manual time is kept."
    elif text and call.get("time_source") == "generated":
        text += " Time of call is scheduled; no verified reply clock yet."
    return text


def _story(call):
    """What WhatsApp gave for a hovered call. Explain the evidence without claiming a roster write succeeded."""
    heard = call.get("heard") or {}
    name, how, said, at = call.get("name", ""), heard.get("how"), (heard.get("said") or "").strip(), heard.get("at") or heard.get("time", "")
    if not heard:
        if call.get("default_response") == "" and call.get("call_text") and not call.get("response"):
            return "%s: No verified reply captured yet - waiting for confirmation. Open this WhatsApp conversation to read the reply, or type the response if you rang him." % name
        return None
    if heard.get("read_incomplete"):
        reason = ("A newer message was observed after the last verified read." if heard.get("read_reason") == "new_context" else
                  "Automatic reading has not verified enough conversation history.")
        return "%s: message at %s: “%s”. %s Recheck this chat to verify the reply and its context, or run a complete read pass." % (name, at, said, reason)
    if heard.get("text_truncated"):
        return "%s: message at %s: “%s”. Only part of the message was captured. Read the full message in WhatsApp before confirming the reply." % (name, at, said)
    if heard.get("reaction_review"):
        reason = ("The thumbs-up was added after the reply window - the question was still without it later on, so it "
                  "is not taken as a timely answer." if heard.get("reaction_late") else
                  "The earlier reaction on this question is no longer present." if heard.get("reaction_removed") else
                  "The reaction on this question or its surrounding context needs review.")
        return "%s: %s WhatsApp gives no reaction time; verify the conversation before confirming the response." % (name, reason)
    if heard.get("shared_reply"):
        return "%s: this reply already answers another assignment. Verify a separate reply for this call before confirming it." % name
    if heard.get("same_minute_conflict"):
        return "%s: conflicting messages share the same minute. Their order is not verified; review this conversation before confirming the reply." % name
    if heard.get("direction_unknown"):
        source = "Only a chat-list preview was read; sender unverified." if heard.get("source") == "line" else "The captured message's sender is unverified."
        return "%s: message at %s: “%s”. %s Open this WhatsApp conversation to read the actual reply." % (name, at, said, source)
    if how == "asked":
        return "%s: asked on WhatsApp at %s. No verified reply captured yet; open this WhatsApp conversation to read the reply." % (name, heard.get("time", ""))
    if how == "voice":
        return "%s sent a voice note at %s - listen on the phone, then type the response." % (name, at)
    if how == "media":
        if heard.get("media_type") == "sticker":
            return "%s sent a sticker at %s - review it on the phone, then type the response." % (name, at)
        return "%s sent a photo at %s - look on the phone, then type the response." % (name, at)
    if how == "reaction":
        if heard.get("attached_reaction"):
            return "%s reacted %s to this check-call question - taken as Ok. WhatsApp does not provide the reaction time." % (name, said)
        return "%s put %s on the question at %s - taken as Ok." % (name, said or "a thumbs-up", at)
    if how == "checkin":
        return "%s wrote at %s without being asked: “%s” - taken as Ok." % (name, at, said)
    if heard.get("response") == "Ok":
        return "%s answered on WhatsApp at %s: “%s” - taken as Ok." % (name, at, said)
    if heard.get("taken_back"):
        return "%s said “%s”, then at %s: “%s” - taken back; review the response." % (
            name, heard.get("first") or "yes", at, said)
    return "%s wrote at %s: “%s” - not a plain yes, so it is left for you to read." % (name, at, said)


def check_text(call):
    """The usual check-call question for a call: 'All okay for The Jug?'."""
    return "All okay for %s?" % (call.get("venue") or call.get("site"))


# --- the command line -------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="The WhatsApp reader's side in Check Call Log.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="write the add-on's config (port and secret)")
    sub.add_parser("status", help="is the receiver up; when did the add-on last report")
    m = sub.add_parser("match", help="what WhatsApp says about a shift's calls")
    m.add_argument("day")
    m.add_argument("--name", default="Lena Drayton")
    d = sub.add_parser("draft", help="open a WhatsApp draft (never sends)")
    d.add_argument("name")
    d.add_argument("--text", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "setup":
        print("wrote", write_reader_config())
        _write_json(SETTINGS, settings()) if not os.path.exists(SETTINGS) else None
        print("settings in", SETTINGS)
        return 0
    if args.cmd == "status":
        if link() is None:
            print("not set up: run  python tool\\whatsapp.py setup  and reload the add-on")
            return 1
        at, status = last_beat()
        state, why = reader_state()
        print("receiver answering:", ping())
        print("reader:", state, why)
        print("last report from the add-on:", time.strftime("%a %H:%M:%S", time.localtime(at)) if at else "never", status)
        return 0
    if args.cmd == "draft":
        print("opened", draft(args.name, args.text))
        return 0
    import calllog
    roster = calllog.Roster()                           # the copy saved last time; nothing is downloaded
    date = calllog.parse_dates([args.day], datetime.date.today())[0]
    log = calllog.build(date, roster, args.name)
    meta = log["meta"]
    midnight = datetime.datetime.combine(date, datetime.time())
    found = heard(log, events_between(min(midnight, midnight + datetime.timedelta(minutes=meta["start_min"] - 60 * (LOOK_BACK_HOURS + 1))),
                                      midnight + datetime.timedelta(minutes=meta["end_min"] + 120)), _read_json(NAMES, {}),
                  trusted_names(), load_read_commits(), load_confirmation_emojis())
    for c in log["calls"]:
        h = found.get(calllog.edit_key(c), {})
        print("%3s  %-22s %-22s %-6s %-8s %s" % (c["no"], c["name"][:22], c["venue"][:22], h.get("time", ""),
                                                h.get("how", "-"), h.get("said", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
