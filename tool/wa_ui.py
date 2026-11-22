"""Lena's local WhatsApp beta workspace. Preparing/opening a draft never sends it.

The roster and WhatsApp classification stay in their existing modules. This view owns
only private draft/contact preferences and an in-memory arrival feed.
"""
import datetime
import hashlib
import json
import os
import time
import tkinter as tk
import unicodedata
import urllib.parse
import uuid
from tkinter import filedialog, ttk

import calllog
import wa_beta
import wa_contacts
import wa_read
import whatsapp
from widgets import Button

SELF_TEST_TEXT = "Check Call Logs beta test. Please ignore this test message."
WAIT_GUIDANCE = "Starting can take about 30 seconds. Keep the WhatsApp tab visible and do not click or type in it until this finishes."
QUEUE_FILTERS = (("all", "All"), ("waiting", "Waiting"), ("review", "Review"), ("confirmed", "Confirmed"))


def search_words(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(c) and c not in "'’")


def queue_view(rows, selected_filter="all", query=""):
    """Filter only the presentation, keeping original identities and roster order intact."""
    status = {"waiting": "no_reply", "review": "review", "confirmed": "confirmed"}.get(selected_filter)
    terms = search_words(query).split()
    return [row for row in rows if (status is None or row["status"] == status)
            and all(term in search_words(" ".join(str(row.get(k, "")) for k in
                ("name", "venue", "start_time", "status_label"))) for term in terms)]


def readonly_text(widget, text):
    """Do not replace unchanged text: preserve the reader's selection and scroll position."""
    if widget.get("1.0", "end-1c") == text:
        return
    position = widget.yview()
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", text)
    widget.configure(state="disabled")
    if position:
        widget.yview_moveto(position[0])


def cache_path():
    # Tests must never touch the user's beta drafts, even before a fixture is loaded.
    if os.environ.get("CCL_BETA_STATE"):
        return os.environ["CCL_BETA_STATE"]
    if os.environ.get("CCL_NO_DOWNLOAD"):
        base = os.environ.get("CCL_EDITS") or calllog.EDITS
    else:
        base = calllog.CACHE
    return os.path.join(base, "whatsapp-beta.json")


class Store:
    """Merge each owned field into the latest file; preserve unknown and unrelated data."""
    def __init__(self, path):
        self.path = path

    def read(self):
        try:
            with open(self.path, encoding="utf-8-sig") as stream:
                data = json.load(stream)
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise ValueError("The saved WhatsApp beta file is not an object; it has not been replaced.")
        return data

    def patch(self, section, key, value):
        return self.patch_many(((section, key, value),))

    @staticmethod
    def merge(data, section, key, value):
        """Validate the original record before changing a private field in memory."""
        group = data.setdefault(section, {})
        if not isinstance(group, dict):
            raise ValueError("The saved WhatsApp %s section cannot be changed safely." % section)
        if isinstance(value, dict):
            if key in group and not isinstance(group[key], dict):
                raise ValueError("The saved WhatsApp %s record is invalid; it has not been replaced." % section)
            group[key] = dict(group.get(key, {}), **value)
        else:
            group[key] = value

    def patch_many(self, changes):
        data = self.read()
        for section, key, value in changes:
            self.merge(data, section, key, value)
        return self.write(data)

    def previews(self, scope, rows):
        """Create missing previews with one atomic write, keeping every existing draft record."""
        data, saved, kept = self.read(), 0, 0
        drafts = data.get("drafts", {})
        if not isinstance(drafts, dict):
            raise ValueError("The saved WhatsApp drafts section cannot be changed safely.")
        for row in rows:
            key = scope + "|" + row["key"]
            record = drafts.get(key, {})
            if not isinstance(record, dict):
                raise ValueError("A saved draft record is invalid; no previews were changed.")
            if "text" in record:
                kept += 1
            else:
                self.merge(data, "drafts", key, {"text": row["text"], "reviewed": None, "opened": None})
                saved += 1
            drafts = data.get("drafts", {})
        return (self.write(data) if saved else data), saved, kept

    def contact(self, name, phone, *, chat=None, scope=None, key=None, replace=False):
        """Validate current identities, then commit a complete reviewed contact change once."""
        identity = wa_beta.normalize_name(name)
        canonical = wa_beta.normalize_phone(phone)
        if not identity or not canonical:
            raise ValueError("Choose one full roster name and valid phone number.")
        data = self.read()
        contacts = data.get("contacts", {})
        aliases = data.get("chat_names", {})
        if not isinstance(contacts, dict) or not isinstance(aliases, dict):
            raise ValueError("The saved contact or chat-name section is invalid; it has not been replaced.")
        equivalent = [saved for saved in contacts if wa_beta.normalize_name(saved) == identity]
        if equivalent and not replace:
            raise ValueError("A contact is already saved for %s. Reopen Contacts to review it; import has not replaced it." % name)
        if any(wa_beta.normalize_name(saved) != identity and wa_beta.normalize_phone(value) == canonical
               for saved, value in contacts.items()):
            raise ValueError("This number is already saved for a different roster name. Review that conflict before saving.")
        effective_chat = chat
        if chat is None:
            current_aliases = [value.strip() if isinstance(value, str) else value for saved, value in aliases.items()
                               if wa_beta.normalize_name(saved) == identity]
            if any(not isinstance(value, str) or not value.strip() for value in current_aliases) or len(set(current_aliases)) > 1:
                raise ValueError("Conflicting chat names are saved for this guard. Choose and verify one contact before saving.")
            effective_chat = current_aliases[0] if current_aliases else None
        if effective_chat is not None:
            try:
                chat_units = len(effective_chat.encode("utf-16-le")) // 2 if isinstance(effective_chat, str) else 0
            except UnicodeError:
                chat_units = 0
            if not isinstance(effective_chat, str) or not effective_chat.strip() or not 0 < chat_units <= 120 or any(ord(c) < 32 for c in effective_chat):
                raise ValueError("Choose a chat name of at most 120 characters without unsupported characters; most emoji count as two.")
            effective_chat = effective_chat.strip()
            if any(wa_beta.normalize_name(saved) != identity and isinstance(value, str) and whatsapp.fold(value) == whatsapp.fold(effective_chat)
                   for saved, value in aliases.items()):
                raise ValueError("This chat name is already saved for a different roster name. Review that conflict before saving.")
            if chat is not None:
                chat = effective_chat
        # Explicit replacement applies to every equivalent full-name spelling, never to another identity.
        for saved in set(equivalent + [name]):
            self.merge(data, "contacts", saved, canonical)
        if chat is not None:
            equivalent_aliases = [saved for saved in aliases if wa_beta.normalize_name(saved) == identity]
            for saved in set(equivalent_aliases + [name]):
                self.merge(data, "chat_names", saved, chat)
        if scope is not None or key is not None:
            if not isinstance(scope, str) or not scope or not isinstance(key, str) or not key:
                raise ValueError("The selected draft has no valid shift identity.")
            record = scope + "|" + key
            self.merge(data, "drafts", record, {"reviewed": None, "opened": None})
            self.merge(data, "contact_inputs", record, "+" + canonical)
        return self.write(data)

    def write(self, data):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temporary = self.path + ".%d.tmp" % os.getpid()
        try:
            with open(temporary, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=True, indent=2)
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        return data


def catalog_choices(candidates, query=""):
    """Keep usable choices from a partly malformed catalog without altering its saved records."""
    if not isinstance(candidates, list):
        raise ValueError("The saved contact catalog is invalid.")
    terms, offered, unique, invalid = query.casefold().split(), [], set(), 0
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("name"), str) or not candidate["name"].strip() \
                or not isinstance(candidate.get("phones"), list):
            invalid += 1
            continue
        name = candidate["name"]
        numbers = [wa_beta.normalize_phone(number) for number in candidate["phones"]]
        if not numbers or not all(numbers):
            invalid += 1
        if not all(term in name.casefold() for term in terms):
            continue
        for phone in numbers:
            if phone and (name, phone) not in unique:
                unique.add((name, phone))
                offered.append((name, phone))
    return offered, invalid


class BetaUI:
    def __init__(self, app, now=None):
        self.app, self.root = app, app.root
        self.clock = now or datetime.datetime.now
        self.opener = os.startfile
        self.store = Store(cache_path())
        self.data, self.error = {}, ""
        self.reload_data()
        prefs = self.data.get("preferences", {})
        prefs = prefs if isinstance(prefs, dict) else {}
        self.drafts_on, self.sound_on = prefs.get("drafts", True) is not False, prefs.get("sound", True) is not False
        self.sending_on = False                    # deliberately never persisted
        self.opened, self.scope, self.selected = False, None, None
        self.rows, self.feed, self.unread = [], [], 0
        self.tracker = wa_beta.NotificationTracker()
        self.filter = "all"
        self.query = ""
        self.loading, self.dirty, self.save_job = False, False, None
        self.edit_scope, self.edit_key, self.edit_context = None, None, None
        self.pending_text, self.pending_phone = None, ""
        self.banner = self.workspace = self.editor = None
        self.connection = ("off", "Waiting for the reader's first report")
        self.last_live = False
        self.import_windows = []
        saved_inputs = self.data.get("contact_inputs", {})
        self.contact_inputs = dict(saved_inputs) if isinstance(saved_inputs, dict) else {}
        self.contact_dirty, self.invalidated = set(), set()
        self.bridge_job, self.bridge_pending = None, None
        self.self_test_window = self.self_test_status = None
        self.self_test_open_button = self.self_test_draft_button = self.self_test_send_button = None
        self.self_test_position_job = None
        self.read_job = None
        self.read_plan = None
        self.read_window = self.read_detail = None
        self.read_position_job = None
        self.read_coordinator = wa_read.ReadCoordinator(
            lambda *args, **kwargs: self.enqueue_read(*args, **kwargs),
            lambda action_id: self.action_status(action_id),
            lambda action_id: self.cancel_action(action_id))

    def reload_data(self):
        try:
            self.data = self.store.read()
            self.check_pending_guard()
            return True
        except (OSError, ValueError) as exc:
            self.error = "Could not read beta drafts: %s" % exc
            self.cancel_guard_pending("Saved contacts or drafts could not be read. Review them before starting another WhatsApp action.")
            return False

    def save(self, section, key, value):
        return self.save_many(((section, key, value),))

    def save_many(self, changes):
        try:
            self.data = self.store.patch_many(changes)
            self.error = ""
            self.check_pending_guard()
            return True
        except (OSError, ValueError) as exc:
            self.problem("Could not save beta changes: %s" % exc)
            return False

    def problem(self, text):
        self.error = text
        if getattr(self, "message", None) is not None and self.message.winfo_exists():
            self.message.configure(text=text, fg=self.app.skin["amber"])
        if self.banner is not None and self.banner.winfo_exists():
            self.refresh_banner(manage_layout=False)

    def inform(self, text):
        if getattr(self, "message", None) is not None and self.message.winfo_exists():
            self.message.configure(text=text, fg=self.app.skin["ink2"])

    @staticmethod
    def focus_next(event, backward=False):
        target = event.widget.tk_focusPrev() if backward else event.widget.tk_focusNext()
        if target is not None:
            target.focus_set()
        return "break"

    def log(self):
        pack = self.app.pack
        return pack.get("calls") if pack else None

    def controller(self):
        log = self.log()
        if not wa_beta.allowed_controller(self.app.name):
            return self.app.name
        return (log.get("meta") or {}).get("supervisor", self.app.name) if log else self.app.name

    def now(self):
        return self.clock()

    def contacts(self):
        raw = self.data.get("contacts", {})
        return raw if isinstance(raw, dict) else {}

    def phone_for(self, name):
        matches = [wa_beta.normalize_phone(v) for k, v in self.contacts().items()
                   if wa_beta.normalize_name(k) == wa_beta.normalize_name(name)]
        return matches[0] if matches and all(matches) and len(set(matches)) == 1 else ""

    def chat_for(self, name):
        names = self.data.get("chat_names", {})
        values = [value.strip() if isinstance(value, str) else "" for saved, value in names.items()
                  if wa_beta.normalize_name(saved) == wa_beta.normalize_name(name)] if isinstance(names, dict) else []
        return values[0] if values and all(values) and len(set(values)) == 1 else ""

    def record_id(self, scope, key):
        return scope + "|" + key

    def draft_record(self, row):
        group = self.data.get("drafts", {})
        value = group.get(self.record_id(self.scope, row["key"]), {}) if isinstance(group, dict) and self.scope else {}
        return value if isinstance(value, dict) else {}

    def text_for(self, row):
        value = self.draft_record(row).get("text", row["text"])
        return value if isinstance(value, str) else row["text"]

    def context(self, row):
        log = self.log() or {}
        meta = log.get("meta", {})
        return [self.scope, row["key"], row["venue"], row["start_date"], row["start_time"],
                meta.get("supervisor"), meta.get("start_min"), meta.get("end_min")]

    def signature(self, row, text=None, phone=None):
        bits = self.context(row) + [self.chat_for(row["name"]), self.text_for(row) if text is None else text,
                                   self.phone_for(row["name"]) if phone is None else phone]
        return hashlib.sha256(json.dumps(bits, ensure_ascii=True).encode("utf-8")).hexdigest()

    def selected_row(self):
        return next((row for row in self.rows if row["key"] == self.selected), None)

    def label(self, parent, text="", role="caption", tone="ink2", **kwargs):
        skin = self.app.skin
        return tk.Label(parent, text=text, font=skin.font(role), bg=skin["panel"], fg=skin[tone], **kwargs)

    def button(self, parent, text, command, **kwargs):
        return Button(parent, self.app.skin, text, command, **kwargs)

    def build_banner(self, parent):
        skin, px = self.app.skin, self.app.px
        self.banner = tk.Frame(parent, bg=skin["panel"], highlightthickness=1, highlightbackground=skin["line"])
        self.banner.pack(fill="x", padx=px(16), pady=(0, px(6)))
        top = tk.Frame(self.banner, bg=skin["panel"])
        top.pack(fill="x", padx=px(10), pady=(px(4), 0))
        self.banner_title = self.label(top, "WhatsApp · Reader offline", "body", "amber", anchor="w")
        self.banner_title.pack(side="left")
        self.messages_button = self.button(top, "Messages & drafts", self.toggle_workspace, icon="phone", kind="plain")
        self.messages_button.pack(side="right", padx=(px(5), 0))
        self.self_test_button = self.button(top, "Test my chat", self.show_self_test, kind="quiet")
        self.self_test_button.pack(side="right", padx=(px(5), 0))
        controls = tk.Frame(self.banner, bg=skin["panel"])
        controls.pack(fill="x", padx=px(10))
        self.auto_read_button = self.button(controls, "Auto read: off", self.toggle_auto_read, kind="plain")
        self.auto_read_button.pack(side="left")
        self.read_now_button = self.button(controls, "Full check", self.read_now, kind="plain")
        self.read_now_button.pack(side="left", padx=(px(4), 0))
        self.read_details_button = self.button(controls, "How reading works", self.show_read_details, kind="quiet")
        self.read_details_button.pack(side="left", padx=(px(4), 0))
        self.sound_button = self.button(controls, "Sound: on", self.toggle_sound, kind="quiet")
        self.sound_button.pack(side="right", padx=(px(4), 0))
        self.send_button = self.button(controls, "Sending: manual", self.toggle_sending, kind="quiet")
        self.send_button.pack(side="right", padx=(px(4), 0))
        self.drafts_button = self.button(controls, "Drafts: on", self.toggle_drafts, kind="quiet")
        self.drafts_button.pack(side="right", padx=(px(4), 0))
        self.read_progress = self.label(self.banner, anchor="w", justify="left")
        self.read_progress.pack(fill="x", padx=px(12), pady=(0, px(2)))
        self.read_progress.bind("<Configure>", lambda e: self.read_progress.configure(wraplength=max(1, e.width)))
        self.banner_detail = self.label(self.banner, anchor="w", justify="left")
        self.banner_detail.pack(fill="x", padx=px(12), pady=(0, px(6)))
        self.banner_detail.bind("<Configure>", lambda e: self.banner_detail.configure(wraplength=max(1, e.width)))

    def make_read_plan(self):
        # Read saved identities afresh; unsaved contact text is never a navigation target.
        log = self.log() if wa_beta.allowed_controller(self.controller()) else None
        try:
            data = self.store.read()
            return wa_read.build_plan(log, data.get("contacts", {}), data.get("chat_names", {}),
                                      self.now(), settings=whatsapp.settings(), activity=self.read_activity(log),
                                      edits=calllog.load_edits(log["stem"]) if log else {})
        except (OSError, ValueError, TypeError) as exc:
            return {"eligible": False, "scope": None, "fingerprint": None, "targets": [], "issues": [],
                    "reason": "Reading is paused because the saved shift/contact data could not be read: %s" % exc}

    def read_activity(self, log):
        """Read changed local event files, not all chat history on every 400ms UI tick."""
        if not isinstance(log, dict) or not isinstance(log.get("meta"), dict) or not log["meta"].get("date_iso"):
            return {}
        now = self.now()
        if not wa_beta.actionable_shift(log, now):
            return {}
        day = datetime.date.fromisoformat(log["meta"]["date_iso"])
        start = datetime.datetime.combine(day, datetime.time()) - datetime.timedelta(hours=16)
        key = (log.get("stem"), start.date(), now.date())
        cached = getattr(self, "_read_activity_cache", None)
        stamp = time.monotonic()
        if cached and cached["key"] == key and stamp - cached["checked"] < 2:
            return cached["activity"]
        revision, cursor = [], start.date()
        while cursor <= now.date():
            path = whatsapp._day_file(cursor)
            try:
                stat = os.stat(path)
                revision.append((path, stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                revision.append((path, 0, 0))
            cursor += datetime.timedelta(days=1)
        # Reconsider the time boundary periodically even if a file was written slightly ahead of now.
        revision = (tuple(revision), int(now.timestamp()) // 30)
        if not cached or cached["key"] != key or cached["revision"] != revision:
            activity = wa_read.activity_signals(whatsapp.events_between(start, now))
        else:
            activity = cached["activity"]
        self._read_activity_cache = dict(key=key, checked=stamp, revision=revision, activity=activity)
        return activity

    def enqueue_read(self, kind, chat, *, phone, since):
        if os.environ.get("CCL_NOOPEN"):
            raise ValueError("WhatsApp reading actions are disabled in this isolated test window.")
        if kind != "read" or self.bridge_pending:
            raise ValueError("Finish the current manual WhatsApp action before reading chats.")
        return whatsapp.enqueue_action("read", chat, phone=phone, since=since)

    def start_read(self, continuous=False, force_full=False):
        if self.bridge_pending:
            self.pause_reads("Finish the current manual WhatsApp action, then start reading again.")
            return
        self.pause_reads("Starting a new read pass.")
        self.read_plan = self.make_read_plan()
        try:
            self.read_coordinator.start(self.read_plan, continuous=continuous, force_full=force_full)
        except (OSError, ValueError) as exc:
            self.read_coordinator.stop(str(exc))
        self.read_tick()

    def replies_follow_auto_read(self):
        """20 Nov: "if autoread is off just do it plainly" - the log uses WhatsApp replies only while Auto read is on."""
        # Called only when HE presses Auto read (not on the brief stop/start inside a Full check, which would make the
        # log flicker between plain and WhatsApp): on if reading really started, off when he turned it off.
        state = self.read_coordinator.status()
        on = bool(state.get("enabled") and state.get("continuous"))
        if on != whatsapp.replies_on():
            whatsapp.set_replies(on)
            try:
                self.app.rebuild()
                self.app.refresh()
            except Exception:
                pass

    def toggle_auto_read(self):
        state = self.read_coordinator.status()
        if state.get("enabled") and state.get("continuous"):
            self.pause_reads("Auto read is off. Reading will not restart until you choose it.")
        else:
            self.start_read(continuous=True)
        self.replies_follow_auto_read()

    def read_now(self):
        state = self.read_coordinator.status()
        if state.get("enabled") and not state.get("continuous"):
            self.pause_reads("Reading stopped. Choose Full check or turn Auto read on when needed.")
        else:
            self.start_read(continuous=bool(state.get("enabled") and state.get("continuous")), force_full=True)

    def pause_reads(self, reason):
        if self.read_job:
            self.root.after_cancel(self.read_job)
            self.read_job = None
        self.read_coordinator.stop(reason)
        self.refresh_read_status()

    def read_tick(self):
        self.read_job = None
        if self.app.gone:
            self.read_coordinator.stop("Application closed.")
            return
        self.read_plan = self.make_read_plan()
        try:
            state = self.read_coordinator.tick(self.read_plan, self.bridge_state())
        except Exception:
            state = self.read_coordinator.stop("Reading stopped because its result could not be checked. Inspect WhatsApp, then restart explicitly.")
        self.refresh_read_status()
        if state.get("enabled"):
            self.read_job = self.root.after(400, self.read_tick)

    def read_summary(self):
        state = self.read_coordinator.status()
        calls = (self.log() or {}).get("calls", [])
        confirmed = sum(isinstance(c.get("heard"), dict) and c["heard"].get("response") == "Ok"
                        and not c["heard"].get("taken_back") and not c["heard"].get("direction_unknown") for c in calls)
        summary = "Chats visited %d/%d · replies confirmed %d/%d" % (state.get("read", 0), state.get("total", 0), confirmed, len(calls))
        if state.get("continuous"):
            visited = sum(item.get("last_attempt_at") is not None for item in state.get("coverage", []))
            summary = "Chats visited %d/%d · latest pass %d/%d · replies confirmed %d/%d" % (
                visited, state.get("roster_total", 0), state.get("read", 0), state.get("total", 0), confirmed, len(calls))
        if state.get("enabled") and state.get("continuous"):
            summary = "Auto read · " + summary
            if state.get("not_needed"):
                summary += " · %d not due for another check" % state["not_needed"]
        if state.get("reason") and (state.get("state") == "paused" or str(state["reason"]).startswith("Waiting")):
            summary += " · " + state["reason"]
        elif state.get("current"):
            summary += " · reading " + state["current"]
        elif state.get("state") == "waiting":
            summary += " · " + (state.get("reason") or "Watching for changes; no full pass is scheduled.")
        elif state.get("state") == "complete":
            summary += " · pass complete"
        elif state.get("reason"):
            summary += " · " + state["reason"]
        else:
            summary += " · automatic reading is off"
        if state.get("issues"):
            summary += " · %d need attention (How reading works)" % len(state["issues"])
        if state.get("unread_opened"):
            summary += " · %d chat%s had unread messages - read them in WhatsApp (How reading works)" % (
                len(state["unread_opened"]), "" if len(state["unread_opened"]) == 1 else "s")
        return summary

    def refresh_read_status(self):
        state = self.read_coordinator.status()
        if getattr(self, "auto_read_button", None) is not None and self.auto_read_button.winfo_exists():
            active = bool(state.get("enabled"))
            automatic = active and state.get("continuous")
            self.auto_read_button.label("Auto read: " + ("on" if automatic else "off"))
            self.auto_read_button.light(automatic)
            self.read_now_button.label("Stop check" if active and not automatic else "Full check")
            allowed = self.draft_eligible()
            self.auto_read_button.able(active or allowed)
            self.read_now_button.able(active or allowed)
            self.read_progress.configure(text=self.read_summary(), fg=self.app.skin["amber" if state.get("state") == "paused" or state.get("issues")
                                                                             or state.get("unread_opened") else "ink2"])
        if self.read_detail is not None and self.read_detail.winfo_exists():
            help_lines = ["AUTO READ", "First checks the roster, then watches for changed messages and upcoming calls. "
                     "Confirmed text replies are not opened again every two minutes. Waiting or partial checks retry less often, "
                     "and stop routine retries 90 minutes after that assignment starts.", "",
                     "FULL CHECK", "Visits every verified roster chat once, including people already confirmed. "
                     "If Auto read is on, it returns to watching afterward. Use Auto read: off to stop automatic reading.", "",
                     "THUMBS-UP REACTIONS", "Changes to an old reaction may not appear in the chat list. These get a freshness check "
                     "at most every 15 minutes until 90 minutes after the assignment starts. Full check can recheck them at any time.", "",
                     "CALL TIMES", "A verified guard reply supplies Time of call. Without an exact reply clock, the scheduled time remains. "
                     "Your manual corrections take priority. Attached reactions do not have an invented time.", "",
                     "A visited chat is not a confirmed reply.",
                     "Keep WhatsApp visible. While you use WhatsApp, reading waits and then carries on by itself.", ""]
            lines = [self.read_summary(), ""]
            unread = state.get("unread_opened", [])
            if unread:
                lines += ["Opening these chats marked their messages as read in WhatsApp. Read them there:"] + [
                    "%s: %d unread message%s (at %s)" % (u.get("name", "Contact"), u.get("count", 1), "" if u.get("count", 1) == 1 else "s",
                                                        time.strftime("%H:%M", time.localtime(u.get("at", 0)))) for u in unread] + [""]
            issues = state.get("issues", [])
            lines += ["Needs attention:"] + ["%s: %s" % (item.get("name", "Contact"), item.get("reason", "Review this chat")) for item in issues] if issues else ["No contact or capture issues reported yet."]
            if state.get("coverage"):
                lines += ["", "LAST CHECKS"]
                for item in state["coverage"]:
                    checked = item.get("last_checked_at")
                    when = time.strftime("%H:%M", time.localtime(checked)) if checked is not None else "not completed"
                    suffix = " · latest attempt partial" if item.get("partial") else ""
                    if item.get("needs_attention"):
                        suffix += " · review in WhatsApp; routine retries have stopped"
                    lines.append("%s: %s%s" % (item.get("name", "Contact"), when, suffix))
            if state.get("next_due_at") is not None:
                lines += ["", "Next scheduled check: " + time.strftime("%H:%M", time.localtime(state["next_due_at"])) + ". New activity can be checked sooner."]
            lines += ["", *help_lines]
            readonly_text(self.read_detail, "\n".join(lines))

    def show_read_details(self):
        if self.read_window is not None and self.read_window.winfo_exists():
            self.refresh_read_status()
            return
        skin, px = self.app.skin, self.app.px
        window = self.read_window = tk.Toplevel(self.root)
        window.title("How WhatsApp reading works")
        window.transient(self.root)
        window.configure(bg=skin["panel"])
        wide, tall = px(680), px(510)
        x = max(px(16), min(self.root.winfo_rootx() + px(80), self.root.winfo_screenwidth() - wide - px(16)))
        y = max(px(16), min(self.root.winfo_rooty() + px(60), self.root.winfo_screenheight() - tall - px(60)))
        window.geometry("%dx%d+%d+%d" % (wide, tall, x, y))
        window.minsize(px(420), px(260))
        window.protocol("WM_DELETE_WINDOW", self.close_read_details)
        window.bind("<Escape>", lambda e: self.close_read_details() or "break")
        body = tk.Frame(window, bg=skin["panel"])
        body.pack(fill="both", expand=True, padx=px(14), pady=px(12))
        self.read_detail = tk.Text(body, wrap="word", font=skin.font("caption"), bg=skin["panel"], fg=skin["ink"],
                                   relief="flat", highlightthickness=0, state="disabled", cursor="arrow")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.read_detail.yview, style="Beta.Vertical.TScrollbar")
        self.read_detail.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.read_detail.pack(fill="both", expand=True)
        self.refresh_read_status()
        def finish_position():
            self.read_position_job = None
            if self.read_window is window and window.winfo_exists():
                window.geometry("+%d+%d" % (x, y))
        self.read_position_job = window.after_idle(finish_position)

    def close_read_details(self):
        if self.read_position_job:
            self.read_window.after_cancel(self.read_position_job)
            self.read_position_job = None
        if self.read_window is not None and self.read_window.winfo_exists():
            self.read_window.destroy()
        self.read_window = self.read_detail = None

    def build_workspace(self, parent):
        skin, px = self.app.skin, self.app.px
        self.workspace = tk.Frame(parent, bg=skin["panel"], highlightthickness=1, highlightbackground=skin["line"])
        heading = tk.Frame(self.workspace, bg=skin["panel"])
        heading.pack(fill="x", padx=px(12), pady=(px(4), px(2)))
        self.button(heading, "Back to logs", self.hide, kind="plain").pack(side="right")
        self.save_all_button = self.button(heading, "Create local drafts", self.save_all_previews, kind="plain")
        self.save_all_button.pack(side="right", padx=(0, px(5)))
        self.heading = self.label(heading, "Messages & drafts", "subtitle", "ink", anchor="w")
        self.heading.pack(side="left")
        self.summary = self.label(self.workspace, anchor="w", justify="left")
        self.summary.pack(fill="x", padx=px(14), pady=(0, px(2)))
        self.summary.bind("<Configure>", lambda e: self.summary.configure(wraplength=max(1, e.width)))
        content = tk.Frame(self.workspace, bg=skin["panel"])
        content.pack(fill="both", expand=True, padx=px(12))
        content.columnconfigure(0, weight=4, minsize=px(340))
        content.columnconfigure(1, weight=6, minsize=px(430))
        content.rowconfigure(0, weight=1)
        left = tk.Frame(content, bg=skin["panel"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(12)))
        right = tk.Frame(content, bg=skin["panel"])
        right.grid(row=0, column=1, sticky="nsew")
        search = tk.Frame(left, bg=skin["panel"])
        search.pack(fill="x", pady=(0, px(4)))
        self.label(search, "Find", anchor="w").pack(side="left", padx=(0, px(5)))
        self.search_var = tk.StringVar(value=self.query)
        self.search_entry = tk.Entry(search, textvariable=self.search_var, font=skin.font("body"), bg=skin["control"], fg=skin["ink"],
                                     insertbackground=skin["ink"], relief="flat", highlightthickness=1, highlightbackground=skin["edge"], width=12)
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<Escape>", lambda e: self.clear_search() or "break")
        self.search_var.trace_add("write", self.search_changed)
        self.button(search, "Clear", self.clear_search, kind="quiet").pack(side="right", padx=(px(3), 0))
        filters = tk.Frame(left, bg=skin["panel"])
        filters.pack(fill="x")
        self.filter_buttons = {}
        for key, title in QUEUE_FILTERS:
            button = self.button(filters, title, lambda key=key: self.set_filter(key), kind="quiet")
            self.filter_buttons[key] = button
        def arrange_filters(event=None):
            width = event.width if event is not None else filters.winfo_width()
            buttons = list(self.filter_buttons.values())
            columns = 4 if width >= sum(b.winfo_reqwidth() for b in buttons) else 2
            for i, button in enumerate(buttons):
                button.grid(row=i // columns, column=i % columns, sticky="w")
        filters.bind("<Configure>", arrange_filters)
        arrange_filters()
        style = ttk.Style(self.root)
        # Windows' native ttk header/scrollbar elements ignore our dark colours.
        # Clam ships with Tk and draws these elements using the explicit palette.
        # These are the app's only ttk widgets; its canvas controls stay unchanged.
        if style.theme_use() != "clam":
            style.theme_use("clam")
        style.configure("Beta.Treeview", background=skin["control"], fieldbackground=skin["control"], foreground=skin["ink"],
                        rowheight=px(29), font=skin.font("caption"), borderwidth=0,
                        bordercolor=skin["line"], lightcolor=skin["control"], darkcolor=skin["control"])
        style.configure("Beta.Treeview.Heading", background=skin["raised"], foreground=skin["ink"], font=skin.font("caption"),
                        bordercolor=skin["line"], lightcolor=skin["raised"], darkcolor=skin["raised"], relief="flat", padding=(px(3), px(3)))
        style.map("Beta.Treeview.Heading", background=[("pressed", skin["pressed"]), ("active", skin["hover"])],
                  foreground=[("disabled", skin["ink3"]), ("!disabled", skin["ink"])])
        style.map("Beta.Treeview", background=[("selected", skin["accent"])], foreground=[("selected", skin["on_accent"])])
        style.configure("Beta.Vertical.TScrollbar", background=skin["strong"], troughcolor=skin["control"],
                        bordercolor=skin["line"], arrowcolor=skin["base"], lightcolor=skin["strong"], darkcolor=skin["strong"],
                        arrowsize=px(12), gripcount=0)
        style.map("Beta.Vertical.TScrollbar", background=[("disabled", skin["edge"]), ("pressed", skin["ink3"]), ("active", skin["ink2"])],
                  arrowcolor=[("disabled", skin["ink2"]), ("!disabled", skin["base"])])
        table = tk.Frame(left, bg=skin["panel"])
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("guard", "at", "state"), show="headings", selectmode="browse", style="Beta.Treeview", height=7)
        self.item_keys = {}
        self.tree.tag_configure("missing", foreground=skin["amber"])
        for name, title, width in (("guard", "Guard / venue", 180), ("at", "Start", 55), ("state", "Status", 95)):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=px(width), minwidth=px(40), stretch=name != "at")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview, style="Beta.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.queue_empty = self.label(table, "No calls match this view.\nClear Find or choose All.", justify="center", wraplength=px(300))
        self.tree.bind("<<TreeviewSelect>>", self.choose)
        self.label(left, "New incoming this session", anchor="w").pack(fill="x", pady=(px(8), px(3)))
        self.feed_list = tk.Listbox(left, height=3, bg=skin["control"], fg=skin["ink2"], font=skin.font("caption"),
                                   relief="flat", highlightthickness=0, selectbackground=skin["accent"], selectforeground=skin["on_accent"])
        self.feed_list.pack(fill="x", pady=(0, px(6)))
        self.feed_list.bind("<<ListboxSelect>>", self.choose_feed)
        self.recipient = self.label(right, "Select a call", "body", "ink", anchor="w", justify="left")
        self.recipient.pack(fill="x")
        self.assignment = self.label(right, anchor="w", justify="left")
        self.assignment.pack(fill="x", pady=(0, px(3)))
        self.evidence = tk.Text(right, height=2, wrap="word", font=skin.font("caption"), bg=skin["panel"], fg=skin["ink2"],
                                relief="flat", highlightthickness=0, takefocus=1, cursor="arrow")
        self.evidence.pack(fill="x", pady=(0, px(2)))
        for label in (self.recipient, self.assignment):
            label.bind("<Configure>", lambda e: e.widget.configure(wraplength=max(1, e.width)))
        self.label(right, "Local draft preview", anchor="w").pack(fill="x")
        text_frame = tk.Frame(right, bg=skin["panel"])
        text_frame.pack(fill="both", expand=True, pady=(px(2), px(2)))
        # This expanding, scrollable editor may contract to one line at the minimum window height.
        # Keep the explicit review/open actions fully visible before allocating extra editing space.
        self.editor = tk.Text(text_frame, height=1, wrap="word", font=skin.font("body"), bg=skin["control"], fg=skin["ink"],
                              insertbackground=skin["ink"], relief="flat", bd=0, highlightthickness=1, highlightbackground=skin["edge"],
                              selectbackground=skin["accent"], selectforeground=skin["on_accent"], undo=True)
        text_scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.editor.yview, style="Beta.Vertical.TScrollbar")
        self.editor.configure(yscrollcommand=text_scroll.set)
        text_scroll.pack(side="right", fill="y")
        self.editor.pack(fill="both", expand=True)
        self.editor.bind("<<Modified>>", self.text_changed)
        self.editor.bind("<FocusOut>", lambda e: self.flush())
        self.editor.bind("<Control-z>", lambda e: self.edit_history(False))
        self.editor.bind("<Control-y>", lambda e: self.edit_history(True))
        for widget in (self.editor, self.evidence):
            widget.bind("<Tab>", self.focus_next)
            widget.bind("<Shift-Tab>", lambda e: self.focus_next(e, True))
            widget.bind("<ISO_Left_Tab>", lambda e: self.focus_next(e, True))
        self.label(right, "Verified contact number (international or Irish local)", anchor="w").pack(fill="x")
        contact = tk.Frame(right, bg=skin["panel"])
        contact.pack(fill="x", pady=(px(2), px(4)))
        self.phone_var = tk.StringVar(value=self.pending_phone)
        self.phone_entry = tk.Entry(contact, textvariable=self.phone_var, font=skin.font("body"), bg=skin["control"], fg=skin["ink"],
                                    insertbackground=skin["ink"], relief="flat", highlightthickness=1, highlightbackground=skin["edge"], width=15)
        self.phone_entry.pack(side="left", fill="x", expand=True, padx=(0, px(4)))
        self.phone_var.trace_add("write", self.phone_changed)
        self.button(contact, "Save contact", self.save_contact).pack(side="left")
        self.open_chat_button = self.button(contact, "Open chat", self.open_chat, icon="phone", kind="quiet")
        self.open_chat_button.pack(side="left", padx=(px(3), 0))
        chat_row = tk.Frame(right, bg=skin["panel"])
        chat_row.pack(fill="x")
        self.button(chat_row, "Contacts", self.choose_contact, kind="quiet").pack(side="right")
        self.button(chat_row, "Import", self.import_vcard, kind="quiet").pack(side="right")
        self.chat_label = self.label(chat_row, anchor="w", justify="left")
        self.chat_label.pack(side="left", fill="x", expand=True)
        self.chat_label.bind("<Configure>", lambda e: self.chat_label.configure(wraplength=max(1, e.width)))
        self.draft_state = self.label(right, anchor="w", justify="left")
        self.draft_state.pack(fill="x", pady=(px(3), 0))
        self.actions = tk.Frame(right, bg=skin["panel"])
        self.actions.pack(fill="x", pady=(px(4), 0))
        self.review_button = self.button(self.actions, "Mark reviewed", self.review, icon="check")
        self.review_button.pack(side="left")
        self.open_draft_button = self.button(self.actions, "Prepare in WhatsApp", self.open_draft, kind="plain")
        self.open_draft_button.pack(side="left", padx=(px(4), 0))
        self.send_reviewed_button = self.button(self.actions, "Send", self.send_reviewed, kind="accent")
        self.send_reviewed_button.pack(side="left", padx=(px(4), 0))
        helper = self.label(right, "Review this text and contact first. Prepare puts an unsent draft in WhatsApp. "
                                   "Send sends one message and needs Sending on.", anchor="w", justify="left")
        helper.pack(fill="x", pady=(px(3), px(5)))
        helper.bind("<Configure>", lambda e: helper.configure(wraplength=max(1, e.width)))
        self.message = self.label(self.workspace, self.error, anchor="w", justify="left", tone="amber" if self.error else "ink2")
        self.message.pack(fill="x", padx=px(14), pady=(px(4), px(8)))
        self.message.bind("<Configure>", lambda e: self.message.configure(wraplength=max(1, e.width)))
        self.edit_key = None                         # refill newly created widgets from the saved model

    def before_rebuild(self):
        self.pending_phone = self.phone_var.get() if getattr(self, "phone_var", None) is not None else ""
        if not self.flush():
            return False
        if self.read_coordinator.status().get("enabled"):
            self.pause_reads("Reading paused because the window layout changed. Start reading again when ready.")
        self.close_read_details()
        self.close_self_test()
        return True

    def toggle_drafts(self):
        if self.save("preferences", "drafts", not self.drafts_on):
            self.drafts_on = not self.drafts_on
            if not self.drafts_on and self.bridge_pending and self.bridge_pending["kind"] == "draft":
                self.cancel_pending()
            else:
                self.inform("Draft preparation is on. Review each draft before preparing it in WhatsApp." if self.drafts_on else
                            "Draft preparation is paused. Switch Drafts on to edit or prepare a draft.")
            self.refresh_banner()
            self.show_selected()

    def toggle_sound(self):
        if self.save("preferences", "sound", not self.sound_on):
            self.sound_on = not self.sound_on
            self.refresh_banner()

    def toggle_sending(self):
        """On for this session only - it is off every time the app starts (Lena's choice, 8 Nov)."""
        self.sending_on = not self.sending_on
        if not self.sending_on and self.bridge_pending and self.bridge_pending["kind"] == "send":
            self.cancel_pending()
            self.refresh_banner()
            self.show_selected()
            return                         # retain the bridge's possibly-sent outcome
        if self.show():
            self.inform("Sending is ON until the app closes: a reviewed message goes only when you press Send for that guard, "
                        "one at a time." if self.sending_on else
                        "Sending is off. You can still prepare drafts and press Send in WhatsApp yourself.")
        self.refresh_banner()
        self.show_selected()

    def disable_sending(self):
        """Revoke outstanding send work when the selected shift loses permission."""
        self.sending_on = False
        if self.bridge_pending and self.bridge_pending["kind"] == "send":
            self.cancel_pending()

    def current_live(self):
        log = self.log()
        if not log or not wa_beta.allowed_controller(self.controller()):
            return False
        try:
            day = datetime.date.fromisoformat(log["meta"]["date_iso"])
            base = datetime.datetime.combine(day, datetime.time())
            return base + datetime.timedelta(minutes=log["meta"]["start_min"]) <= self.now() < base + datetime.timedelta(minutes=log["meta"]["end_min"])
        except (KeyError, TypeError, ValueError):
            return False

    def draft_eligible(self):
        log = self.log()
        return bool(log and wa_beta.allowed_controller(self.controller()) and wa_beta.actionable_shift(log, self.now()))

    def toggle_workspace(self):
        self.hide() if self.opened else self.show()

    def show(self, key=None):
        if not wa_beta.allowed_controller(self.controller()) or not self.app.settle():
            return False
        first_open = not self.opened
        self.opened, self.unread = True, 0
        if key is not None:
            self.filter, self.selected = "all", key
            self.loading = True
            self.query = ""
            if getattr(self, "search_var", None) is not None:
                self.search_var.set("")
            self.loading = False
        self.refresh()
        if first_open or key is not None:
            self.tree.focus_set()
            item = next((item for item, selected in self.item_keys.items() if selected == self.selected), None)
            if item is not None:
                self.tree.see(item)
        return True

    def hide(self):
        if not self.flush():
            return False
        self.opened = False
        self.present()
        self.refresh_banner()
        grid = self.app.grids.get(self.app.tab)
        if grid is not None:
            grid.body.focus_set()
        return True

    def present(self):
        if self.opened and wa_beta.allowed_controller(self.controller()):
            self.app.body.pack_forget()
            self.workspace.pack(fill="both", expand=True, padx=self.app.px(16), pady=(0, self.app.px(10)))
        else:
            self.workspace.pack_forget()
            if not self.app.body.winfo_manager():
                self.app.body.pack(fill="both", expand=True, padx=self.app.px(16), pady=(0, self.app.px(10)))
        if hasattr(self.app, "help_label"):
            self.app.help_label.configure(text="Find a guard or venue with Ctrl+F. Draft text saves locally. Tab moves between controls; Ctrl+Z undoes draft text." if self.opened else
                                          "Type in a cell: Enter moves on, Esc puts it back. Ctrl+Z undoes log edits.")
        if self.banner is not None and not self.banner.winfo_manager():
            self.refresh_banner()                  # layout now has a packed body/workspace to anchor before

    def refresh_banner(self, manage_layout=True):
        if self.banner is None:
            return
        allowed = wa_beta.allowed_controller(self.controller())
        if manage_layout:
            if allowed:
                if not self.banner.winfo_manager():
                    anchor = next((widget for widget in (self.app.body, self.workspace)
                                   if widget is not None and widget.winfo_manager() == "pack"), None)
                    if anchor is not None:
                        self.banner.pack(fill="x", padx=self.app.px(16), pady=(0, self.app.px(6)), before=anchor)
                    # Both can be temporarily unpacked while switching workspaces.
                    # present() retries after the visible workspace is packed.
            else:
                self.banner.pack_forget()
        state, why = self.connection
        at, status = whatsapp.last_beat()
        beat = time.strftime("%H:%M:%S", time.localtime(at)) if at else "none yet"
        ready = self.bridge_state()
        reason = (ready.get("reason") or "waiting for WhatsApp Web").rstrip(". ")
        # (8 Nov review: one cause was said twice, "..", and jargon - "Reader report", "Draft tools")
        background = (" · at the last report WhatsApp was not on screen; show WhatsApp for drafts"
                      if state == "on" and status.get("visible") == "hidden" and ready.get("reason_code") != "hidden" else "")
        self.banner_title.configure(text="WhatsApp " + ("connected" if state == "on" else "not connected"),
                                    fg=self.app.skin["accent" if state == "on" else "amber"])
        details = "Last report %s%s. " % (beat, background)
        details += "Drafts ready." if ready.get("ready") else "Drafts not ready: %s." % reason
        if state != "on" and why and why.rstrip(". ") not in reason:
            details += " " + why.rstrip(". ") + "."
        if not self.current_live():
            if self.draft_eligible():
                details += " Upcoming shift today: draft preparation only."
            else:
                details += " Preview only: this shift is outside the draft-preparation window."
        self.banner_detail.configure(text=details + ("\n" + self.error if self.error else ""),
                                     fg=self.app.skin["amber" if self.error else "ink2"])
        self.drafts_button.label("Drafts: " + ("on" if self.drafts_on else "off"))
        self.sound_button.label("Sound: " + ("on" if self.sound_on else "off"))
        self.send_button.label("Sending: " + ("on" if self.sending_on else "off"))
        self.send_button.light(self.sending_on)
        self.messages_button.label("Messages & drafts" + (" · %d new" % self.unread if self.unread else ""))
        self.messages_button.light(self.opened)
        self.update_self_test()
        self.refresh_read_status()

    def refresh(self):
        if not self.flush():
            self.disable_sending()
            return
        log = self.log()
        scope = log.get("stem") if log and wa_beta.allowed_controller(self.controller()) else None
        if scope != self.scope:
            if not self.flush():
                self.problem("Save the current beta draft before changing shifts.")
                self.disable_sending()
                return
            if self.read_coordinator.status().get("enabled"):
                self.pause_reads("Reading paused because the shown shift changed. Start reading for the new shift explicitly.")
            self.disable_sending()
            self.scope, self.selected = scope, None
            self.feed, self.unread = [], 0
            self.edit_key = None
            if scope is None:
                self.opened = False
        if not self.current_live():
            self.disable_sending()
        self.reload_data()
        try:
            self.rows = wa_beta.draft_rows(log, edits=calllog.load_edits(scope), trusted=whatsapp.settings()["trusted"]) if scope else []
        except (ValueError, OSError) as exc:
            self.rows = []
            self.problem(str(exc))
        self.connection = whatsapp.reader_state()
        self.refresh_banner()
        button = self.app.buttons.get("whatsapp")
        if button:
            mine = wa_beta.allowed_controller(self.controller())
            if mine and not button.winfo_manager() and self.app.buttons.get("fill") is not None:
                button.pack(side="left", padx=(0, self.app.px(2)), after=self.app.buttons["fill"])
            elif not mine and button.winfo_manager():
                button.pack_forget()                  # (20 Nov: WhatsApp is Lena's only - no name on anyone else's log)
            button.able(mine and bool(self.rows))
        self.present()
        if self.workspace is not None:
            counts = wa_beta.status_counts(self.rows)
            self.heading.configure(text="Messages & drafts · " + ((log.get("meta") or {}).get("date_iso", "Lena") if log else "Lena"))
            saved = sum(isinstance(self.draft_record(row).get("text"), str) for row in self.rows)
            self.summary.configure(text="%d calls · %d local drafts · %d confirmed · %d trusted · %d waiting · %d need review"
                                   % (len(self.rows), saved, counts["confirmed"], counts["trusted"], counts["no_reply"], counts["review"]))
            self.save_all_button.able(bool(self.rows and self.drafts_on))
            self.render_queue()
            self.render_feed()
            self.show_selected()

    def set_filter(self, value):
        if self.flush():
            self.filter = value
            self.render_queue()
            self.show_selected()

    def focus_search(self):
        if self.opened and getattr(self, "search_entry", None) is not None:
            self.search_entry.focus_set()
            self.search_entry.selection_range(0, "end")
            return "break"

    def clear_search(self):
        self.search_var.set("")
        self.search_entry.focus_set()

    def search_changed(self, *args):
        if self.loading:
            return
        if not self.flush():
            self.loading = True
            try:
                self.search_var.set(self.query)
            finally:
                self.loading = False
            return
        self.query = self.search_var.get()
        self.render_queue()
        self.show_selected()

    def render_queue(self):
        visible = queue_view(self.rows, self.filter, self.query)
        self.loading = True
        try:
            old_y = self.tree.yview()
            old_keys = tuple(getattr(self, "item_keys", {}).values())
            same = old_keys == tuple(row["key"] for row in visible)
            if not same:
                self.tree.delete(*self.tree.get_children())
            self.item_keys = {}
            for i, row in enumerate(visible):
                item = str(i)
                self.item_keys[item] = row["key"]
                missing = not self.phone_for(row["name"]) or not self.chat_for(row["name"])
                values = (row["name"] + " · " + row["venue"], row["start_time"], row["status_label"] + (" · contact needed" if missing else ""))
                if same:
                    self.tree.item(item, values=values, tags=("missing",) if missing else ())
                else:
                    self.tree.insert("", "end", iid=item, values=values, tags=("missing",) if missing else ())
            if self.selected not in self.item_keys.values():
                self.selected = visible[0]["key"] if visible else None
            item = next((i for i, key in self.item_keys.items() if key == self.selected), None)
            if item is not None:
                self.tree.selection_set(item)
                if not same:
                    self.tree.see(item)
            if same and old_y:
                self.tree.yview_moveto(old_y[0])
            for key, title in QUEUE_FILTERS:
                button = self.filter_buttons[key]
                button.label("%s %d" % (title, len(queue_view(self.rows, key, self.query))))
                button.light(key == self.filter)
            if visible:
                self.queue_empty.place_forget()
            else:
                self.queue_empty.place(relx=.5, rely=.5, anchor="center")
        finally:
            self.loading = False

    def choose(self, event=None):
        if self.loading:
            return
        picked = self.tree.selection()
        key = self.item_keys.get(picked[0]) if picked else None
        if key == self.selected:
            return
        if not self.flush():
            self.render_queue()
            return
        self.selected = key
        self.show_selected()

    def show_selected(self):
        if self.editor is None:
            return
        row = self.selected_row()
        key = row["key"] if row else None
        if (self.edit_scope, self.edit_key) != (self.scope, key):
            if not self.flush():
                return
            self.loading = True
            try:
                self.editor.configure(state="normal")
                self.editor.delete("1.0", "end")
                self.editor.insert("1.0", self.text_for(row) if row else "")
                self.editor.edit_modified(False)
                self.editor.edit_reset()
                phone = ("+" + self.phone_for(row["name"])) if row and self.phone_for(row["name"]) else ""
                self.phone_var.set(self.contact_inputs.get(self.record_id(self.scope, key), phone) if key else "")
                self.edit_scope, self.edit_key = self.scope, key
                self.edit_context = self.context(row) if row else None
                self.pending_text, self.dirty = None, False
            finally:
                self.loading = False
        self.recipient.configure(text=row["name"] if row else "No matching call")
        self.assignment.configure(text=("%s · %s at %s" % (row["venue"], row["start_date"], row["start_time"])) if row else "Clear Find or choose All to see the shift's calls.")
        call = next((c for c in (self.log() or {}).get("calls", []) if row and calllog.edit_key(c) == row["key"]), None)
        evidence = (whatsapp.story(call) or "No reply evidence recorded.") if call else ""
        # Detailed words remain readable here; the grid hint is intentionally shorter.
        readonly_text(self.evidence, (row["status_label"] + ": " + evidence) if row else "")
        text = self.editor.get("1.0", "end-1c") if row else ""
        saved_phone = self.phone_for(row["name"]) if row else ""
        entered_phone = wa_beta.normalize_phone(self.phone_var.get()) if row else ""
        contact_saved = bool(saved_phone and entered_phone == saved_phone)
        signature = self.signature(row, text=text) if row else None
        record = self.draft_record(row) if row else {}
        reviewed = bool(row and contact_saved and record.get("reviewed") == signature and not self.dirty
                        and (self.scope, key) not in self.invalidated)
        chat = self.chat_for(row["name"]) if row else ""
        self.chat_label.configure(text="WhatsApp chat: " + (chat or "Not verified yet"))
        state = "Missing contact" if not saved_phone else "Missing verified chat name" if not chat else "Save contact changes" if not contact_saved else "Reviewed" if reviewed else "Ready to review"
        if reviewed and record.get("opened") == signature:
            state = "Last verified in WhatsApp as an unsent draft"
        if reviewed and record.get("sent") == signature:
            state = "Sent from here at %s" % (record.get("sent_at") or "")[11:16]
        self.draft_state.configure(text=state if row else "", fg=self.app.skin["amber" if not contact_saved else "ink2"])
        self.editor.configure(state="normal" if row and self.drafts_on else "disabled")
        self.review_button.able(bool(row and self.drafts_on and contact_saved and text.strip()))
        self.open_chat_button.able(bool(row and saved_phone and chat and self.draft_eligible() and not self.bridge_pending))
        self.open_draft_button.able(bool(reviewed and chat and self.drafts_on and self.draft_eligible() and not self.bridge_pending))
        already_sent = bool(reviewed and record.get("sent") == signature)
        self.send_reviewed_button.label(("Send to %s" % row["name"].split()[0]) if row else "Send")
        self.send_reviewed_button.able(bool(reviewed and chat and self.sending_on and self.current_live()
                                            and not self.bridge_pending and not already_sent))

    def edit_history(self, redo):
        if not self.drafts_on or self.root.focus_get() is self.phone_entry:
            return "break"
        try:
            self.editor.edit_redo() if redo else self.editor.edit_undo()
        except tk.TclError:
            pass
        return "break"                             # never undo a different log behind this workspace

    def text_changed(self, event=None):
        if not self.editor.edit_modified():
            return
        self.editor.edit_modified(False)
        if self.loading or self.edit_key is None:
            return
        self.pending_text, self.dirty = self.editor.get("1.0", "end-1c"), True
        self.cancel_guard_pending("The draft text changed. Review it again before starting another WhatsApp action.", self.edit_scope, self.edit_key)
        if self.save_job:
            self.root.after_cancel(self.save_job)
        self.save_job = self.root.after(350, self.flush)
        self.show_selected()

    def phone_changed(self, *args):
        if not self.loading:
            self.pending_phone = self.phone_var.get()
            if self.scope and self.selected:
                key = self.record_id(self.scope, self.selected)
                self.contact_inputs[key] = self.pending_phone
                self.contact_dirty.add(key)
            self.invalidated.add((self.scope, self.selected))
            self.cancel_guard_pending("The contact input changed. Save and review it before starting another WhatsApp action.", self.scope, self.selected)
            if self.save_job:
                self.root.after_cancel(self.save_job)
            self.save_job = self.root.after(350, self.flush)
            self.show_selected()

    def flush(self):
        if self.save_job:
            self.root.after_cancel(self.save_job)
            self.save_job = None
        if self.editor is not None and self.editor.winfo_exists() and self.edit_key is not None and self.editor.edit_modified():
            self.pending_text, self.dirty = self.editor.get("1.0", "end-1c"), True
            self.editor.edit_modified(False)
        changed = self.dirty or bool(self.contact_dirty)
        changes = []
        if self.dirty:
            if self.edit_scope is None or self.edit_key is None:
                return False
            self.cancel_guard_pending("The draft text changed. Review it again before starting another WhatsApp action.", self.edit_scope, self.edit_key)
            changes.append(("drafts", self.record_id(self.edit_scope, self.edit_key),
                            {"text": self.pending_text, "reviewed": None, "opened": None}))
        for key in self.contact_dirty:
            changes.extend((("contact_inputs", key, self.contact_inputs[key]),
                            ("drafts", key, {"reviewed": None, "opened": None})))
        if changes:
            if not self.save_many(changes):
                return False
            self.dirty = False
            self.contact_dirty.clear()
        if changed and self.editor is not None and self.editor.winfo_exists():
            self.show_selected()
        return True

    def save_all_previews(self):
        if not self.drafts_on or not self.flush():
            return
        if not self.scope:
            return
        try:
            self.data, saved, kept = self.store.previews(self.scope, self.rows)
        except (OSError, ValueError) as exc:
            self.problem("Could not create local drafts: %s" % exc)
            return
        self.error = ""
        pending = self.bridge_pending
        self.check_pending_guard()
        self.refresh()
        if not self.error and (pending is None or self.bridge_pending is pending):
            self.message.configure(text="Created %d local drafts; kept %d existing drafts. This action does not change WhatsApp." % (saved, kept), fg=self.app.skin["ink2"])

    def save_contact(self):
        row = self.selected_row()
        phone = wa_beta.normalize_phone(self.phone_var.get())
        if not row or not phone:
            self.problem("Enter one valid international or Irish phone number for the selected guard.")
            return
        if not self.flush():
            return
        if self.commit_contact(row["name"], phone, scope=self.scope, key=row["key"], replace=True):
            self.message.configure(text="Contact saved for this exact roster name. Review the draft again before opening it.", fg=self.app.skin["ink2"])
            self.show_selected()

    def commit_contact(self, name, phone, *, chat=None, scope=None, key=None, replace=False):
        try:
            self.data = self.store.contact(name, phone, chat=chat, scope=scope, key=key, replace=replace)
        except (OSError, ValueError) as exc:
            self.problem("Could not save contact: %s" % exc)
            return False
        self.error = ""
        self.check_pending_guard()
        if scope is not None and key is not None:
            record = self.record_id(scope, key)
            self.contact_inputs[record] = "+" + wa_beta.normalize_phone(phone)
            self.contact_dirty.discard(record)
            self.invalidated.add((scope, key))
            if self.scope == scope and self.selected == key:
                self.loading = True
                try:
                    self.phone_var.set(self.contact_inputs[record])
                finally:
                    self.loading = False
        return True

    def review(self):
        row = self.selected_row()
        if not row or not self.flush():
            return
        text = self.editor.get("1.0", "end-1c")
        try:
            wa_beta.validate_text(text)
        except ValueError as exc:
            self.problem(str(exc))
            return
        if not self.phone_for(row["name"]) or wa_beta.normalize_phone(self.phone_var.get()) != self.phone_for(row["name"]):
            self.problem("Save and verify this guard's contact before marking the draft reviewed.")
            return
        if self.save("drafts", self.record_id(self.scope, row["key"]), {"text": text, "reviewed": self.signature(row, text=text), "opened": None}):
            self.invalidated.discard((self.scope, row["key"]))
            self.show_selected()

    def open_url(self, url):
        if os.environ.get("CCL_NOOPEN"):
            raise ValueError("Opening WhatsApp is disabled in this isolated test window.")
        self.opener(url)                            # no clipboard, keystrokes or send automation

    def bridge_state(self):
        if getattr(getattr(self, "app", None), "wa_receiver_elsewhere", False):
            return {"ready": False, "reason_code": "receiver_elsewhere", "age": None, "capability": None,
                    "owner": None, "busy": False, "pending": 0,
                    "reason": "Another Check Call Logs window is using WhatsApp actions. Use that window, "
                              "or close it and wait for this window to connect."}
        check = getattr(whatsapp, "bridge_status", None)
        return check() if check else {"ready": False, "reason": "Update the WhatsApp reader to enable draft preparation."}

    def enqueue(self, kind, chat, text=None):
        if os.environ.get("CCL_NOOPEN"):
            raise ValueError("WhatsApp actions are disabled in this isolated test window.")
        row = self.selected_row()
        phone = self.phone_for(row["name"]) if row else None
        return whatsapp.enqueue_action(kind, chat, text=text, phone=phone)

    def action_status(self, action_id):
        return whatsapp.action_status(action_id)

    def self_phone(self):
        # Reuse the same explicit saved-self allowlist as the old self-link helper;
        # only its validated number is used. No external protocol is opened here.
        try:
            link = wa_beta.self_link(whatsapp.settings(), controller=self.controller())
        except ValueError as exc:
            if "draft_to" in str(exc):
                raise ValueError("Your saved self-contact is not allowed for this test yet. No request was made.") from exc
            raise
        return urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)["phone"][0]

    def enqueue_self(self, kind, phone, text=None):
        if os.environ.get("CCL_NOOPEN"):
            raise ValueError("WhatsApp actions are disabled in this isolated test window.")
        return whatsapp.enqueue_action(kind, wa_beta.CONTROLLER, text=text, phone=phone)

    def show_self_test(self):
        if not wa_beta.allowed_controller(self.controller()) or not self.app.settle():
            return
        if self.self_test_window is not None and self.self_test_window.winfo_exists():
            self.update_self_test()
            return
        skin, px = self.app.skin, self.app.px
        window = self.self_test_window = tk.Toplevel(self.root)
        window.title("Test my WhatsApp chat")
        window.transient(self.root)
        window.configure(bg=skin["panel"])
        wide, tall = px(540), px(420)
        # Tk's default transient position can leave the title bar off-screen.
        # Place this dialog beside its parent, retaining room for the window frame.
        x = max(px(16), min(self.root.winfo_rootx() + px(120), self.root.winfo_screenwidth() - wide - px(16)))
        y = max(px(16), min(self.root.winfo_rooty() + px(80), self.root.winfo_screenheight() - tall - px(60)))
        window.geometry("%dx%d+%d+%d" % (wide, tall, x, y))
        window.minsize(px(440), px(390))
        window.protocol("WM_DELETE_WINDOW", self.close_self_test)
        window.bind("<Escape>", lambda e: self.close_self_test() or "break")
        body = tk.Frame(window, bg=skin["panel"])
        body.pack(fill="both", expand=True, padx=px(18), pady=px(14))
        self.label(body, "Test only: " + wa_beta.CONTROLLER, "body", "ink", anchor="w").pack(fill="x")
        self.self_test_connection = self.label(body, anchor="w", justify="left")
        self.self_test_connection.pack(fill="x", pady=(px(4), px(8)))
        preview = self.label(body, SELF_TEST_TEXT, "body", "ink", anchor="w", justify="left")
        preview.pack(fill="x", pady=(0, px(8)))
        note = self.label(body, "This fixed preview goes only to your saved, allowed self-contact. A different existing draft is preserved. Only Send test message sends it.", anchor="w", justify="left")
        note.pack(fill="x")
        controls = tk.Frame(body, bg=skin["panel"])
        controls.pack(fill="x", pady=px(10))
        self.self_test_open_button = self.button(controls, "Open my chat", lambda: self.run_self_test("open"))
        self.self_test_open_button.pack(side="left")
        self.self_test_draft_button = self.button(controls, "Prepare test draft", lambda: self.run_self_test("draft"))
        self.self_test_draft_button.pack(side="left", padx=(px(6), 0))
        self.self_test_send_button = self.button(controls, "Send test message", lambda: self.run_self_test("send"))
        self.self_test_send_button.pack(side="left", padx=(px(6), 0))
        status_area = tk.Frame(body, bg=skin["panel"])
        status_area.pack(fill="both", expand=True)
        self.self_test_status = tk.Text(status_area, height=3, wrap="word", font=skin.font("caption"),
                                        bg=skin["panel"], fg=skin["ink2"], relief="flat", highlightthickness=0,
                                        takefocus=1, cursor="arrow", state="disabled")
        scroll = ttk.Scrollbar(status_area, orient="vertical", command=self.self_test_status.yview, style="Beta.Vertical.TScrollbar")
        self.self_test_status.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.self_test_status.pack(fill="both", expand=True)
        self.self_test_status.bind("<Tab>", self.focus_next)
        self.self_test_status.bind("<Shift-Tab>", lambda e: self.focus_next(e, True))
        for label in (self.self_test_connection, preview, note):
            label.bind("<Configure>", lambda e, item=label: item.configure(wraplength=max(1, e.width)))
        self.self_test_words = "Review the preview, then choose an action. Sending remains manual."
        self.update_self_test()
        # Windows can replace/map a transient's native frame after the first
        # geometry request. Apply its position once after that initial idle work.
        def finish_position():
            self.self_test_position_job = None
            if self.self_test_window is window and window.winfo_exists():
                window.geometry("+%d+%d" % (x, y))
        self.self_test_position_job = window.after_idle(finish_position)

    def update_self_test(self):
        if self.self_test_window is None or not self.self_test_window.winfo_exists():
            return
        try:
            phone = self.self_phone()
            state = self.bridge_state()
            reason = "Saved self-contact ending %s. %s" % (phone[-4:], "Drafts ready." if state.get("ready") else
                     "Drafts not ready: " + (state.get("reason") or "waiting for WhatsApp Web"))
            ready = bool(state.get("ready"))
            # Chrome slows a WhatsApp tab that is not on screen, and the reader refuses to work in one (7 Nov live:
            # a request started there ran out of time half-way). Said here, where he presses the buttons. The
            # buttons stay on: the report can be up to 30 s old, and the reader checks again itself.
            hidden = ready and (whatsapp.last_beat()[1] or {}).get("visible") == "hidden"
            if hidden:
                reason += " WhatsApp is not on screen: show its tab (beside this window is fine) before pressing these."
        except (ValueError, KeyError, OSError) as exc:
            reason, ready, hidden = str(exc), False, False
        self.self_test_connection.configure(text=reason, fg=self.app.skin["ink2" if ready and not hidden else "amber"])
        self.self_test_open_button.able(ready and not self.bridge_pending)
        self.self_test_draft_button.able(ready and self.drafts_on and not self.bridge_pending)
        if getattr(self, "self_test_send_button", None) is not None:
            self.self_test_send_button.able(ready and self.sending_on and not self.bridge_pending)
        self.self_test_status.configure(state="normal")
        self.self_test_status.delete("1.0", "end")
        self.self_test_status.insert("1.0", getattr(self, "self_test_words", ""))
        self.self_test_status.configure(state="disabled")

    def save_self_result(self, attempt, status, request_id=None):
        """Record only diagnostic metadata, never the number, preview or chat contents."""
        record = dict(attempt, status=status, request_id=request_id,
                      completed_at=None if status == "pending" else self.now().isoformat(timespec="seconds"))
        # UI actions are serial, but guard the current marker against a delayed old ACK.
        latest = self.data.get("diagnostics", {}).get("self_test", {}) if isinstance(self.data.get("diagnostics", {}), dict) else {}
        changes = [("self_test_history", attempt["attempt_id"], record)]
        if status == "pending" or not isinstance(latest, dict) or latest.get("attempt_id") == attempt["attempt_id"]:
            changes.append(("diagnostics", "self_test", record))
        return self.save_many(changes)

    def run_self_test(self, kind):
        if kind not in ("open", "draft", "send"):
            return
        if self.read_coordinator.status().get("enabled"):
            self.pause_reads("Reading paused for your manual WhatsApp action. Start reading again when ready.")
        if self.bridge_pending:
            self.self_test_words = "Wait for the current WhatsApp request to finish. " + WAIT_GUIDANCE
            self.update_self_test()
            return
        _, beat = whatsapp.last_beat()
        attempt = {"attempt_id": uuid.uuid4().hex, "kind": kind,
                   "started_at": self.now().isoformat(timespec="seconds"), "reader_version": str(beat.get("version") or "unknown")}
        if not self.save_self_result(attempt, "pending"):
            return
        try:
            phone = self.self_phone()              # validate the current controller/config again at click time
            if kind == "draft" and not self.drafts_on:
                raise ValueError("Switch Drafts on before preparing the self-test draft.")
            if kind == "send" and not self.sending_on:
                raise ValueError("Switch Sending on first (it is off every time the app starts).")
            state = self.bridge_state()
            if not state.get("ready"):
                raise ValueError(state.get("reason") or "Draft tools are not ready. Reload the reader and WhatsApp tab first.")
            action_id = self.enqueue_self(kind, phone, text=SELF_TEST_TEXT if kind in ("draft", "send") else None)
        except (ValueError, OSError) as exc:
            self.save_self_result(attempt, "blocked")
            self.self_test_words = str(exc)
            self.problem(self.self_test_words)
            self.update_self_test()
            return
        self.bridge_pending = {"id": action_id, "kind": kind, "self_test": True, "attempt": attempt,
                               "name": wa_beta.CONTROLLER, "venue": "self-test", "started": time.monotonic()}
        if not self.save_self_result(attempt, "pending", action_id):
            self.cancel_pending()
            return
        self.self_test_words = ("Sending the test message to your own chat. " + WAIT_GUIDANCE if kind == "send" else
                                "Waiting for WhatsApp Web to verify your %s. " % ("unsent draft" if kind == "draft" else "chat")
                                + WAIT_GUIDANCE + " Nothing will be sent.")
        self.error = ""
        self.inform(self.self_test_words)
        self.refresh_banner()
        self.update_self_test()
        self.show_selected()
        self.bridge_job = self.root.after(400, self.check_bridge)

    def cancel_action(self, action_id):
        return whatsapp.cancel_action(action_id)

    def cancel_guard_pending(self, reason, scope=None, key=None):
        """Revoke only the bound guard action; another row and the explicit self test are independent."""
        pending = getattr(self, "bridge_pending", None)
        if not pending or pending.get("self_test"):
            return False
        if scope is not None and (scope, key) != (pending.get("scope"), pending.get("key")):
            return False
        self.cancel_pending(reason=reason)
        return True

    def pending_guard_problem(self, pending):
        """Validate current saved data and the current log, never the old displayed row list."""
        log = self.log()
        if not log or not wa_beta.allowed_controller(self.controller()) or log.get("stem") != pending.get("scope") \
                or self.scope != pending.get("scope") or not self.draft_eligible():
            return "The shown shift or controller changed. Review the new shift before starting another WhatsApp action."
        if pending["kind"] == "send" and (not self.sending_on or not self.current_live()) \
                or pending["kind"] == "draft" and not self.drafts_on:
            return "This WhatsApp action is no longer enabled for the current shift."
        try:
            matches = [row for row in wa_beta.draft_rows(log) if row["key"] == pending.get("key")]
            if len(matches) != 1:
                return "The requested roster row changed or is ambiguous. Review it before starting another WhatsApp action."
            row = matches[0]
            if self.context(row) != pending.get("context") or self.phone_for(row["name"]) != pending.get("phone") \
                    or self.chat_for(row["name"]) != pending.get("chat"):
                return "The assignment or verified contact changed. Review it before starting another WhatsApp action."
            wa_beta.guard_link(log, row["key"], self.contacts(), now=self.now(), enabled=True)
            if self.signature(row, text=pending.get("text")) != pending.get("binding"):
                return "The draft or its saved contact changed. Review it before starting another WhatsApp action."
            if pending["kind"] in ("draft", "send") and (self.text_for(row) != pending.get("text")
                    or self.draft_record(row).get("reviewed") != pending.get("signature")
                    or (pending["scope"], pending["key"]) in self.invalidated):
                return "The requested message is no longer the exact reviewed draft. Review it again before continuing."
        except (KeyError, TypeError, ValueError, AttributeError):
            return "The saved contact, draft or roster context could not be verified. Review it before continuing."
        return None

    def check_pending_guard(self):
        pending = getattr(self, "bridge_pending", None)
        if pending and not pending.get("self_test"):
            reason = self.pending_guard_problem(pending)
            if reason:
                self.cancel_guard_pending(reason)

    def cancel_pending(self, reply=None, reason=None):
        if not self.bridge_pending:
            return
        if self.bridge_job:
            self.root.after_cancel(self.bridge_job)
            self.bridge_job = None
        if reply is None:
            try:
                reply = self.cancel_action(self.bridge_pending["id"])
            except Exception:
                reply = {"status": "unknown", "detail": "Cancellation could not be verified. " +
                         ("The message may already have been sent; check the chat. " if self.bridge_pending["kind"] == "send" else "Inspect WhatsApp before trying again. ") +
                         "Nothing will be retried automatically."}
        if reason and reply.get("status") not in ("opened", "drafted", "already_draft", "sent"):
            reply = dict(reply, detail=reason + " " + str(reply.get("detail") or "The request was stopped; inspect WhatsApp before trying again."))
        self.check_bridge(reply=reply)

    def close_self_test(self):
        if self.self_test_position_job:
            self.self_test_window.after_cancel(self.self_test_position_job)
            self.self_test_position_job = None
        if self.bridge_pending and self.bridge_pending.get("self_test"):
            self.cancel_pending()
        if self.self_test_window is not None and self.self_test_window.winfo_exists():
            self.self_test_window.destroy()
        self.self_test_window = self.self_test_status = None
        self.self_test_open_button = self.self_test_draft_button = self.self_test_send_button = None

    def start_bridge(self, kind, row, text=None, signature=None):
        if self.read_coordinator.status().get("enabled"):
            self.pause_reads("Reading paused for your manual WhatsApp action. Start reading again when ready.")
        if self.bridge_pending:
            raise ValueError("Wait for the current WhatsApp action to finish.")
        chat = self.chat_for(row["name"])
        if not chat:
            raise ValueError("Save this guard's exact verified WhatsApp chat name before opening it.")
        state = self.bridge_state()
        if not state.get("ready"):
            raise ValueError(state.get("reason") or "The WhatsApp reader is not ready for draft preparation.")
        action_id = self.enqueue(kind, chat, text=text)
        self.error = ""
        self.bridge_pending = {"id": action_id, "kind": kind, "scope": self.scope, "key": row["key"],
                               "name": row["name"], "venue": row["venue"], "chat": chat,
                               "date": (self.log().get("meta") or {}).get("date_iso", ""),
                               "phone": self.phone_for(row["name"]), "context": self.context(row), "text": text,
                               "binding": self.signature(row, text=text),
                               "signature": signature, "started": time.monotonic()}
        if kind == "send":
            self.message.configure(text="%s: sending the reviewed message in WhatsApp. " % self.action_context(self.bridge_pending)
                                   + WAIT_GUIDANCE, fg=self.app.skin["ink2"])
        else:
            self.message.configure(text=("%s: waiting for WhatsApp Web to verify the %s. " %
                                   (self.action_context(self.bridge_pending), "draft" if kind == "draft" else "chat"))
                                   + WAIT_GUIDANCE + " No message will be sent.", fg=self.app.skin["ink2"])
        self.refresh_banner()
        self.show_selected()
        self.bridge_job = self.root.after(400, self.check_bridge)

    @staticmethod
    def action_context(pending):
        """The recipient and shift that were requested, even if another row is now on show."""
        return " · ".join(str(pending.get(key) or "") for key in ("name", "venue", "date") if pending.get(key))

    def check_bridge(self, reply=None):
        self.bridge_job = None
        pending = self.bridge_pending
        if not pending or self.app.gone:
            return
        try:
            reply = self.action_status(pending["id"]) if reply is None else reply
            status = reply.get("status", "pending")
        except Exception as exc:
            reply, status = {"detail": str(exc)}, "unknown"
        if status in ("pending", "queued", "claimed", "running") and not pending.get("self_test"):
            if not self.reload_data() or self.bridge_pending is not pending:
                return
        if status in ("pending", "queued", "claimed", "running") and time.monotonic() - pending["started"] < 100:
            self.bridge_job = self.root.after(400, self.check_bridge)
            return
        self.bridge_pending = None
        context = self.action_context(pending)
        if status in ("pending", "queued", "claimed", "running"):
            # A missing ACK is not proof nothing happened. Revoke outstanding work;
            # the bridge reports unknown if insertion may already have been authorized.
            try:
                reply = self.cancel_action(pending["id"])
                status = reply.get("status", "unknown")
            except Exception:
                status = "unknown"
        if pending.get("self_test") and not self.save_self_result(pending["attempt"], status, pending["id"]):
            self.self_test_words = self.error
            self.update_self_test()
            self.show_selected()
            return
        detail = reply.get("detail")
        detail = " ".join(detail.split())[:800] if isinstance(detail, str) else ""
        if status in ("drafted", "already_draft") and pending["kind"] == "draft":
            if not pending.get("self_test") and not self.save("drafts", self.record_id(pending["scope"], pending["key"]), {"opened": pending["signature"]}):
                self.show_selected()
                return
            words = "%s: WhatsApp Web verified the unsent draft. Review it there and press Send yourself." % context
            if detail:
                words += " " + detail
            self.error = ""
            self.inform(words)
        elif status == "sent" and pending["kind"] == "send":
            at = self.now().strftime("%H:%M")
            if not pending.get("self_test") and not self.save("drafts", self.record_id(pending["scope"], pending["key"]),
                                                              {"sent": pending["signature"], "sent_at": self.now().isoformat(timespec="seconds")}):
                self.show_selected()
                return
            self.error = ""
            words = ("%s: sent at %s - WhatsApp shows the message in the chat." % (context, at) if not pending.get("self_test") else
                     "Test message sent to your own chat at %s - WhatsApp shows it in the chat." % at)
            self.inform(words)
        elif status == "opened" and pending["kind"] == "open":
            self.error = ""
            words = "%s: The chat is open and verified in WhatsApp. Any draft already there was left alone; nothing was sent." % context
            if detail:
                words += " " + detail
            self.inform(words)
        else:
            requested = {"open": "open this chat", "send": "send this message"}.get(pending["kind"], "prepare this draft")
            messages = {
                "existing_draft": "This chat already contains a different draft. It was preserved. Review it in WhatsApp before preparing another.",
                "not_found": "The exact saved chat was not found. Check its verified chat name; the request was not completed.",
                "ambiguous_chat": "More than one chat matches this name. Verify the correct contact in WhatsApp; the request was not completed.",
                "group_chat": "This appears to be a group chat. This request requires a verified individual chat.",
                "blocked": "WhatsApp could not safely %s. Check the WhatsApp Web tab." % requested,
                "expired": "The WhatsApp request expired. Its completion was not verified; inspect WhatsApp before trying again.",
                "failed": "WhatsApp did not confirm the requested action. Inspect the chat before trying again.",
                "cancelled": "The request was cancelled. Cancellation does not remove text already in WhatsApp.",
                "user_active": "You were using WhatsApp, so nothing was done. Try again when you have finished there.",
            }
            fallback = messages.get(status, "The result is not certain. Inspect the chat in WhatsApp (its message box) and select All chats; nothing will be retried automatically.")
            # The reader/bridge know which step stopped. Keep that specific reason instead
            # of preceding it with a generic, sometimes contradictory explanation.
            words = context + ": " + (detail if detail and (status in messages or status == "unknown") else fallback)
            self.problem(words)
        if pending.get("self_test"):
            self.self_test_words = words
        self.refresh_banner()
        self.show_selected()

    def open_chat(self):
        row = self.selected_row()
        if not row:
            return
        try:
            if not self.draft_eligible():
                raise ValueError("Open chats only for Lena's live shift or the upcoming shift starting today.")
            if not self.reload_data():
                return
            wa_beta.guard_link(self.log(), row["key"], self.contacts(), now=self.now(), text=None, enabled=True)
            self.start_bridge("open", row)
        except (ValueError, OSError) as exc:
            self.problem(str(exc))

    def open_draft(self):
        row = self.selected_row()
        if not row or not self.flush():
            return
        try:
            if not self.reload_data():
                return
            text = self.editor.get("1.0", "end-1c")
            signature = self.signature(row, text=text)
            if not self.draft_eligible() or not self.drafts_on or (self.scope, row["key"]) in self.invalidated or self.draft_record(row).get("reviewed") != signature or wa_beta.normalize_phone(self.phone_var.get()) != self.phone_for(row["name"]):
                raise ValueError("Review this exact text and saved contact before opening the draft.")
            wa_beta.guard_link(self.log(), row["key"], self.contacts(), now=self.now(), text=text, enabled=True)
            self.start_bridge("draft", row, text=text, signature=signature)
        except (ValueError, OSError) as exc:
            self.problem(str(exc))

    def send_reviewed(self):
        """Send ONE reviewed message to this guard - only because he pressed Send, only with Sending switched on."""
        row = self.selected_row()
        if not row or not self.flush():
            return
        try:
            if not self.reload_data():
                return
            text = self.editor.get("1.0", "end-1c")
            signature = self.signature(row, text=text)
            if not self.sending_on:
                raise ValueError("Switch Sending on first (it is off every time the app starts).")
            if not self.current_live() or (self.scope, row["key"]) in self.invalidated or self.draft_record(row).get("reviewed") != signature \
                    or wa_beta.normalize_phone(self.phone_var.get()) != self.phone_for(row["name"]):
                raise ValueError("Mark this exact text reviewed, with the saved contact, before sending it.")
            if self.draft_record(row).get("sent") == signature:
                raise ValueError("This exact message was already sent from here. Change the text and review it again to send another.")
            wa_beta.guard_link(self.log(), row["key"], self.contacts(), now=self.now(), text=text, enabled=True)
            self.start_bridge("send", row, text=text, signature=signature)
        except (ValueError, OSError) as exc:
            self.problem(str(exc))

    def import_vcard(self):
        path = filedialog.askopenfilename(parent=self.root, title="Review contacts from a vCard", filetypes=[("vCard contacts", "*.vcf"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig") as stream:
                parsed = wa_contacts.parse_vcards(stream.read())
            self.retain_catalog(parsed, path)
            aliases = {chat: name for name, chat in self.data.get("chat_names", {}).items() if isinstance(chat, str)}
            all_candidates = wa_contacts.match_candidates(parsed["candidates"], sorted({row["name"] for row in self.rows}), aliases=aliases, existing=self.contacts())
            candidates = [record for record in all_candidates if record.get("roster_name") or record.get("status") == "ambiguous_name"]
        except (OSError, ValueError, UnicodeError) as exc:
            self.problem("Could not read these contacts: %s" % exc)
            return
        skin, px = self.app.skin, self.app.px
        window = tk.Toplevel(self.root)              # modeless, no grab, no forced focus
        self.import_windows.append(window)
        window.title("Review contact import")
        window.configure(bg=skin["panel"])
        window.geometry("%dx%d" % (px(740), px(420)))
        self.label(window, "Select verified matches, then save. Existing or ambiguous numbers are not replaced automatically.", anchor="w", justify="left", wraplength=px(700)).pack(fill="x", padx=px(12), pady=px(10))
        tree = ttk.Treeview(window, columns=("name", "phone", "status"), show="headings", selectmode="extended", style="Beta.Treeview")
        for name, title in (("name", "Roster name / contact"), ("phone", "Phone"), ("status", "Review")):
            tree.heading(name, text=title)
            tree.column(name, width=px(220))
        tree.pack(fill="both", expand=True, padx=px(12))
        for index, record in enumerate(candidates):
            tree.insert("", "end", iid=str(index), values=(record.get("roster_name") or record.get("name"), record.get("phone") or ", ".join(record.get("phones", [])), record.get("status")))
        note = self.label(window, "%d roster-related contacts shown; %d unrelated contacts skipped. Nothing saved yet." % (len(candidates), len(all_candidates) - len(candidates)), anchor="w")
        note.pack(fill="x", padx=px(12), pady=px(5))

        def apply_selected():
            saved = refused = 0
            for item in tree.selection():
                record = candidates[int(item)]
                if record.get("status") != "ready" or not record.get("roster_name") or not record.get("phone"):
                    continue
                # Read and revalidate the current complete saved state inside each atomic commit.
                if self.commit_contact(record["roster_name"], record["phone"], replace=False):
                    saved += 1
                else:
                    refused += 1
            note.configure(text="%d selected contacts saved; %d changed or conflicting matches need review. Unresolved entries need a number checked by hand." % (saved, refused))
            self.show_selected()

        self.button(window, "Save selected ready matches", apply_selected).pack(anchor="e", padx=px(12), pady=px(8))

    def catalog_store(self):
        return Store(os.path.join(os.path.dirname(self.store.path), "whatsapp-contacts.json"))

    def retain_catalog(self, parsed, source):
        """Keep all supplied contacts privately; only explicit roster matches are applied."""
        store = self.catalog_store()
        data = store.read()
        previous = data.setdefault("candidates", [])
        if not isinstance(previous, list):
            raise ValueError("The existing contact catalog is invalid and has not been replaced.")
        identity = lambda c: json.dumps([c.get("name"), c.get("phones"), c.get("issues")], sort_keys=True, ensure_ascii=True)
        known = {identity(c) for c in previous if isinstance(c, dict)}
        for candidate in parsed["candidates"]:
            if identity(candidate) not in known:
                previous.append(candidate)
                known.add(identity(candidate))
        imports = data.setdefault("imports", [])
        if not isinstance(imports, list):
            raise ValueError("The contact import history is invalid and has not been replaced.")
        imports.append({"path": source, "at": self.now().isoformat(), "count": len(parsed["candidates"]), "issues": parsed.get("issues", [])})
        data.setdefault("version", 1)
        store.write(data)

    def choose_contact(self):
        row = self.selected_row()
        if not row or not self.flush():
            return
        picker_scope, picker_context = self.scope, self.context(row)
        try:
            data = self.catalog_store().read()
            candidates = data.get("candidates", [])
            if not isinstance(candidates, list):
                raise ValueError("The saved contact catalog is invalid.")
        except (OSError, ValueError) as exc:
            self.problem("Could not read the contact catalog: %s" % exc)
            return
        skin, px = self.app.skin, self.app.px
        window = tk.Toplevel(self.root)
        self.import_windows.append(window)
        window.title("Verify a contact for " + row["name"])
        window.configure(bg=skin["panel"])
        window.geometry("%dx%d" % (px(760), px(460)))
        window.minsize(px(580), px(350))
        self.label(window, "Verify the person, exact WhatsApp chat name and number for " + row["name"] + ". Choosing a contact does not open or send anything.",
                   anchor="w", justify="left", wraplength=px(720)).pack(fill="x", padx=px(12), pady=px(8))
        search = tk.StringVar(value=row["name"].split()[0])
        entry = tk.Entry(window, textvariable=search, font=skin.font("body"), bg=skin["control"], fg=skin["ink"], insertbackground=skin["ink"])
        entry.pack(fill="x", padx=px(12), pady=(0, px(5)))
        frame = tk.Frame(window, bg=skin["panel"])
        frame.pack(fill="both", expand=True, padx=px(12))
        tree = ttk.Treeview(frame, columns=("name", "phone"), show="headings", selectmode="browse", style="Beta.Treeview")
        for key, title in (("name", "Contact name"), ("phone", "Phone to verify")):
            tree.heading(key, text=title)
            tree.column(key, width=px(320))
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview, style="Beta.Vertical.TScrollbar")
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        note = self.label(window, anchor="w", justify="left", wraplength=px(720))
        note.pack(fill="x", padx=px(12), pady=px(6))
        offered = {}

        def filter_contacts(*args):
            tree.delete(*tree.get_children())
            offered.clear()
            choices, invalid = catalog_choices(candidates, search.get())
            for name, phone in choices:
                item = str(len(offered))
                offered[item] = (name, phone)
                tree.insert("", "end", iid=item, values=(name, "+" + phone))
            note.configure(text="%d matching numbers from %d saved contacts; %d invalid entries need review. Matches are suggestions, not verified identities."
                           % (len(offered), len(candidates), invalid))

        def apply_contact():
            current_row = next((candidate for candidate in self.rows if candidate["key"] == row["key"]), None)
            if not picker_scope or self.scope != picker_scope or (self.log() or {}).get("stem") != picker_scope or \
                    not wa_beta.allowed_controller(self.controller()) or current_row is None or self.context(current_row) != picker_context:
                note.configure(text="The shown shift or assignment changed. Reopen Contacts for the current shift before saving.")
                return                          # a stale modeless picker must not write any private data
            if not self.flush():
                note.configure(text="Save the current draft before applying this contact.")
                return
            picked = tree.selection()
            if not picked:
                note.configure(text="Select one number and verify that it belongs to this roster guard.")
                return
            name, phone = offered[picked[0]]
            if self.commit_contact(row["name"], phone, chat=name, scope=picker_scope, key=row["key"], replace=True):
                note.configure(text="Saved the selected contact and chat name. Review the exact draft again before preparing it in WhatsApp.")
                self.show_selected()
            else:
                note.configure(text="Contact changes were not saved. " + self.error)

        search.trace_add("write", filter_contacts)
        filter_contacts()
        self.button(window, "I verified this contact — save", apply_contact).pack(anchor="e", padx=px(12), pady=(0, px(10)))

    def render_feed(self):
        lines = tuple("%s · %s: %s" % (message["time"], message["name"], message["text"].replace("\n", " ")) for message in reversed(self.feed[-100:]))
        if not lines:
            lines = ("New incoming replies appear here. Earlier messages stay in each call's details.",)
        if self.feed_list.get(0, "end") != lines:
            self.feed_list.delete(0, "end")
            self.feed_list.insert("end", *lines)

    def choose_feed(self, event=None):
        picked = self.feed_list.curselection()
        if picked and self.feed:
            message = list(reversed(self.feed[-100:]))[picked[0]]
            row = next((r for r in self.rows if r["name"] == message["name"]), None)
            if row:
                self.show(row["key"])

    def poll(self):
        self.refresh()
        if not self.scope or not self.current_live():
            self.last_live = False
            return
        live = self.connection[0] == "on"
        if live and not self.last_live:
            self.tracker = wa_beta.NotificationTracker()   # reconnect history establishes a silent baseline
        self.last_live = live
        now = self.now()
        events = whatsapp.events_between(now - datetime.timedelta(days=1), now)
        people = {row["name"] for row in self.rows}
        chats = {e["chat"] for e in events if not e.get("group")}
        matched = whatsapp.match_chats(sorted(chats), people, whatsapp.load_aliases(self.controller()))
        arrivals = self.tracker.sample(events, controller=self.controller(), scope=self.scope, matched_chats=matched,
                                       self_chats=(wa_beta.CONTROLLER,), now=now)
        if arrivals and live:
            self.feed.extend(arrivals)
            self.feed = self.feed[-100:]
            self.unread += 0 if self.opened else len(arrivals)
            if self.sound_on and not os.environ.get("CCL_NOOPEN") and not os.environ.get("CCL_QUIET"):
                self.root.bell()                    # one sound for the complete batch
            self.render_feed()
            self.refresh_banner()

    def close(self):
        self.pause_reads("Application closed. Automatic reading starts off next time.")
        self.close_read_details()
        # This app owns the local queue. Revoke every outstanding request before exit;
        # neither cancellation nor closing the app clears an existing WhatsApp draft.
        try:
            replies = whatsapp.cancel_actions()
        except Exception:
            replies = []
        if self.bridge_pending:
            reply = next((item for item in replies if item.get("id") == self.bridge_pending["id"]), None)
            self.cancel_pending(reply=reply)
        self.close_self_test()
        return self.flush()
