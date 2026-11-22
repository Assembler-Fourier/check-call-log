"""Pure helpers for the Lena-only WhatsApp beta. No files, network, openers or sends.

The caller supplies the shown call log, saved edits/settings/contacts and local control-room time.
Links only prepare WhatsApp's own compose screen; a person must review it and press Send there.
Notification sampling is silent on first use and after a scope change. Minute-only message clocks
cannot distinguish a new same-minute message from startup history, so that first minute stays silent.
"""
import datetime
import math
import re
import unicodedata
import urllib.parse
from collections.abc import Mapping


CONTROLLER = "Lena Drayton"
STATUSES = ("confirmed", "trusted", "no_reply", "review")
STATUS_LABELS = {"confirmed": "Confirmed", "trusted": "Trusted", "no_reply": "Reply not read", "review": "Needs review"}


def _name(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()) if isinstance(value, str) else ""


def normalize_name(value):
    """Shared exact identity for private contact lookup, validation and replacement."""
    return _name(value)


def validate_text(text):
    """Return a valid draft, bounded like JavaScript strings (4096 UTF-16 code units)."""
    try:
        units = len(text.encode("utf-16-le")) // 2 if isinstance(text, str) else 0
    except UnicodeError:
        raise ValueError("Draft contains unsupported characters. Remove the invalid character and review it again.") from None
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Write a message before marking the draft reviewed.")
    if "\x00" in text:
        raise ValueError("Draft contains unsupported characters. Remove the null character and review it again.")
    if units > 4096:
        raise ValueError("Draft is too long. Maximum 4,096 characters; most emoji count as two.")
    return text


def allowed_controller(name):
    """Exact full name after case/Unicode/whitespace normalization; never a first-name or fuzzy match."""
    return _name(name) == _name(CONTROLLER)


def normalize_phone(value):
    """Validate phone syntax and return international ASCII digits, or ''. No registration lookup.

    Accept international +/00 prefixes and Irish local 0-prefixed numbers, with ordinary formatting.
    Letters, URLs, extensions and arbitrary text containing digits are refused.
    """
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or not re.fullmatch(r"\+?[0-9 ()\.\-]+", value):
        return ""
    if (value.startswith("+") or value.startswith("00")) and re.search(r"\(\s*0\s*\)", value):
        return ""
    digits = re.sub(r"[^0-9]", "", value)
    if value.startswith("+"):
        if digits.startswith("0"):
            return ""
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        if len(digits) not in (9, 10):
            return ""
        digits = "353" + digits[1:]
    return digits if re.fullmatch(r"[1-9][0-9]{8,14}", digits) and not digits.startswith("3530") else ""


def _edit_key(call):
    # This is calllog.edit_key's public row identity, without importing its file/data layer.
    return "%s|%s|%s" % (call["name"], call["site"], call["shift"])


def _date(log):
    try:
        return datetime.date.fromisoformat(log["meta"]["date_iso"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("The shown shift has no valid date.") from None


def _minute(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 3 * 1440


def _local_now(now):
    if not isinstance(now, datetime.datetime) or now.utcoffset() is not None:
        raise ValueError("Supply the current local control-room time without a timezone offset.")
    return now


def _status(call, edit, trusted):
    manual = edit.get("response") if isinstance(edit, Mapping) else None
    if isinstance(manual, str):
        if _name(manual) == "ok":
            return "confirmed"
        return "review" if manual.strip() else "no_reply"
    heard = call.get("heard") if isinstance(call.get("heard"), Mapping) else {}
    response = call.get("response", "")
    if isinstance(response, str) and response.strip() and _name(response) != "ok":
        return "review"
    if heard.get("direction_unknown") or heard.get("taken_back"):
        return "review"
    if _name(heard.get("response")) == "ok":
        return "confirmed"
    if heard and heard.get("how") != "asked":
        return "review"
    if not heard and _name(call["name"]) in trusted:
        return "trusted"
    return "no_reply"                                    # generated/default Ok is not confirmation evidence


def draft_rows(log, *, edits=None, trusted=()):
    """Return per-call previews for the shown Lena log, including calls with no saved phone.

    Row keys exactly match calllog.edit_key; scope drafts by log['stem'] separately when caching edits.
    `edits` is the saved per-row edit mapping, and `trusted` contains saved full names. Inputs are not changed.
    """
    if not isinstance(log, Mapping) or not allowed_controller((log.get("meta") or {}).get("supervisor")):
        return []
    day = _date(log)
    edits = edits if isinstance(edits, Mapping) else {}
    trusted = {_name(name) for name in trusted if _name(name)}
    rows = []
    for call in log.get("calls", ()):
        if not isinstance(call, Mapping) or not all(isinstance(call.get(k), str) and call[k].strip()
                                                   for k in ("name", "site", "shift")):
            continue
        key = _edit_key(call)
        venue = (call.get("venue") or call["site"]).strip()
        status = _status(call, edits.get(key), trusted)
        start_date = start_time = text = ""
        if _minute(call.get("start")) and venue:
            start = datetime.datetime.combine(day, datetime.time()) + datetime.timedelta(minutes=call["start"])
            start_date, start_time = start.date().isoformat(), start.strftime("%H:%M")
            text = "All okay for %s?" % venue
        else:
            status = "review"                         # do not invent a missing start or an overnight date
        heard = call.get("heard") if isinstance(call.get("heard"), Mapping) else {}
        label = STATUS_LABELS[status]
        if status == "review" and heard.get("read_incomplete"):
            label = "Recheck chat"
        elif status == "no_reply" and heard.get("how") == "asked":
            label = "Waiting for reply"
        rows.append(dict(key=key, name=call["name"], venue=venue, start_date=start_date, start_time=start_time,
                         call_time=call.get("call_text", ""), text=text,
                         status=status, status_label=label))
    return rows


def status_counts(rows):
    counts = dict.fromkeys(STATUSES, 0)
    for row in rows:
        if row.get("status") in counts:
            counts[row["status"]] += 1
    return counts


def _contact_phone(contacts, name):
    if not isinstance(contacts, Mapping):
        raise ValueError("No saved contacts are available.")
    matches = [normalize_phone(value) for key, value in contacts.items() if _name(key) == _name(name)]
    if not matches or not all(matches):
        raise ValueError("Save a valid phone number for %s first." % name)
    if len(set(matches)) != 1:
        raise ValueError("More than one saved phone number matches %s; choose one first." % name)
    if any(_name(key) != _name(name) and normalize_phone(value) == matches[0] for key, value in contacts.items()):
        raise ValueError("This number is also saved for a different person; review Contacts before opening the chat.")
    return matches[0]


def _link(phone, text):
    url = "whatsapp://send?phone=" + phone
    if text is not None:
        validate_text(text)
        url += "&text=" + urllib.parse.quote(text, safe="")
    return url


def self_link(settings, *, controller, target=CONTROLLER, text=None):
    """Return a self-test draft/open-chat URL only for saved Lena also explicitly on draft_to."""
    if not allowed_controller(controller) or not allowed_controller(target):
        raise ValueError("Self testing is available only for Lena Drayton's saved self-contact.")
    if not isinstance(settings, Mapping) or not isinstance(settings.get("draft_to"), (list, tuple, set)) \
            or not any(allowed_controller(name) for name in settings["draft_to"]):
        raise ValueError("Lena Drayton must be on the saved draft_to list before self testing.")
    return _link(_contact_phone(settings.get("phones"), CONTROLLER), text)


def actionable_shift(log, now):
    """Allow the live overnight shift or an upcoming shift starting today; never an ended or later-day shift."""
    if not isinstance(log, Mapping) or not allowed_controller((log.get("meta") or {}).get("supervisor")):
        return False
    try:
        now, day, meta = _local_now(now), _date(log), log["meta"]
        a, b = meta.get("start_min"), meta.get("end_min")
        if not _minute(a) or not _minute(b) or b <= a:
            return False
        midnight = datetime.datetime.combine(day, datetime.time())
        begins, ends = midnight + datetime.timedelta(minutes=a), midnight + datetime.timedelta(minutes=b)
        return begins <= now < ends or (now.date() == begins.date() and now < begins)
    except (TypeError, ValueError):
        return False


def guard_link(log, key, contacts, *, now, text=None, enabled=False):
    """Return a draft/open-chat URL for one exact row on the live or upcoming-today Lena shift.

    Opening prepared guard drafts is disabled unless the caller explicitly passes enabled=True. No send occurs.
    `now` is local naive datetime; calllog's start_min/end_min already extend overnight shifts past 24:00.
    """
    if enabled is not True:
        raise ValueError("Opening guard drafts is off. Review the beta setting first.")
    if not isinstance(log, Mapping) or not allowed_controller((log.get("meta") or {}).get("supervisor")):
        raise ValueError("Guard drafts are available only on Lena Drayton's shift.")
    if not actionable_shift(log, now):
        raise ValueError("Open guard drafts only for the live shift or a shift starting today.")
    matches = [c for c in log.get("calls", ()) if isinstance(c, Mapping)
               and all(isinstance(c.get(k), str) for k in ("name", "site", "shift")) and _edit_key(c) == key]
    if len(matches) != 1 or not matches[0]["name"].strip():
        raise ValueError("Select one unambiguous guard row from the shown shift.")
    return _link(_contact_phone(contacts, matches[0]["name"]), text)


def _message_at(event):
    try:
        return datetime.datetime.strptime(event["date"] + " " + event["shown"], "%d/%m/%Y %H:%M")
    except (KeyError, TypeError, ValueError):
        return None


def _event_key(event):
    if event.get("kind") != "bubble" or not isinstance(event.get("chat"), str):
        return None
    chat = event["chat"]
    if isinstance(event.get("id"), str) and event["id"].strip():
        return ("id", chat, event["id"])
    at = _message_at(event)
    if at is None or not isinstance(event.get("text", ""), str):
        return None
    # No DOM index: scrolling can reorder the same message. Keep emoji and punctuation intact.
    return ("message", chat, at.isoformat(), event.get("text", ""), str(event.get("what", "text")))


class NotificationTracker:
    """In-memory arrival tracker. The caller supplies roster-matched chats and saved self-chat aliases.

    Only genuinely new, recent, explicitly incoming dated bubbles qualify. Existing observations establish
    a silent baseline, including unknown/outgoing copies that may later be corrected. No UI or sound is invoked.
    """
    def __init__(self):
        self._scope = None
        self._baseline = None
        self._seen = set()

    def sample(self, events, *, controller, scope, matched_chats, self_chats=(), now):
        now = _local_now(now)
        if not allowed_controller(controller) or not scope:
            self._scope, self._baseline, self._seen = None, None, set()
            return []
        reset = self._scope != scope or self._baseline is None or now < self._baseline
        if reset:
            self._scope, self._baseline, self._seen = scope, now, set()
        matched_chats = matched_chats if isinstance(matched_chats, Mapping) else {}
        self_names = {_name(CONTROLLER)} | {_name(name) for name in self_chats if _name(name)}
        notifications = []
        for event in events:
            if not isinstance(event, Mapping):
                continue
            key = _event_key(event)
            if key is None or key in self._seen:
                continue
            self._seen.add(key)
            if reset or event.get("direction") != "incoming" or ("ours" in event and event["ours"] is not False) \
                    or any(event.get(flag) for flag in ("group", "snapshot", "draft", "history")):
                continue
            chat = event["chat"]
            name = CONTROLLER if _name(chat) in self_names else matched_chats.get(chat)
            if not isinstance(name, str) or not name.strip():
                continue
            text = event.get("text", "")
            if not isinstance(text, str) or re.match(r"^\s*draft\s*:", text, re.I):
                continue
            at, seen = _message_at(event), event.get("seen")
            if at is None or not isinstance(seen, (int, float)) or isinstance(seen, bool) or not math.isfinite(seen):
                continue
            if not self._baseline.replace(second=0, microsecond=0) < at <= now \
                    or (now - at).total_seconds() > 150 \
                    or not self._baseline.timestamp() * 1000 < seen <= now.timestamp() * 1000 + 1000:
                continue
            notifications.append(dict(key=key, chat=chat, name=name, text=text,
                                      date=at.date().isoformat(), time=at.strftime("%H:%M"), seen=seen))
        return notifications
