"""Bounded, in-memory queue for reviewed open/draft/send and scoped read requests. No I/O or UI."""
import copy
import math
import re
import secrets
import threading
import time
import wa_beta

CAPABILITY = "drafts-v4"
FINAL = {"opened", "drafted", "already_draft", "existing_draft", "not_found", "ambiguous_chat",
         "group_chat", "blocked", "expired", "failed", "unknown", "cancelled", "read", "user_active", "sent"}
READ_REASONS = {"since_reached", "history_start", "limit", "timeout", "no_messages", "history_unavailable"}


def reading_summary(value, since, now_ms):
    """Validate metadata only; the trusted worker separately verifies local event delivery."""
    fields = {"messages", "incoming", "outgoing", "complete", "oldest", "newest", "reason"}
    if not isinstance(value, dict) or set(value) != fields or type(since) is not int:
        return None
    if any(type(value[k]) is not int or not 0 <= value[k] <= 1200 for k in ("messages", "incoming", "outgoing")):
        return None
    total, oldest, newest = value["messages"], value["oldest"], value["newest"]
    if value["incoming"] + value["outgoing"] > total or type(value["complete"]) is not bool or \
            not isinstance(value["reason"], str) or value["reason"] not in READ_REASONS:
        return None
    if total == 0:
        if oldest is not None or newest is not None or value["complete"]:
            return None
    elif type(oldest) is not int or type(newest) is not int or not 0 <= oldest <= newest <= now_ms + 5000:
        return None
    if value["complete"] and (value["reason"] != "since_reached" or oldest > since):
        return None
    if value["reason"] == "since_reached" and not value["complete"]:
        return None
    if value["reason"] == "no_messages" and total != 0:
        return None
    return copy.deepcopy(value)


class ActionQueue:
    def __init__(self, clock=time.time):
        self.clock, self.lock = clock, threading.RLock()
        self.client, self.client_at, self.usable, self.connected = None, 0, False, False
        self.unavailable_reason = "Open WhatsApp Web in Chrome. If it is already open, reload the add-on and then reload the WhatsApp tab."
        self.unavailable_code = "offline"
        self.actions = {}

    @staticmethod
    def _client(value):
        if not isinstance(value, dict) or value.get("capability") != CAPABILITY:
            return None
        tab, document, owner = value.get("tab"), value.get("document"), value.get("owner")
        if type(tab) is not int or tab < 0 or not isinstance(document, str) or not re.fullmatch(r"[A-Za-z0-9-]{8,100}", document):
            return None
        if owner != "%s:%s" % (tab, document):
            return None
        return {"tab": tab, "document": document, "owner": owner, "capability": CAPABILITY}

    @staticmethod
    def _interrupted_send_detail(action):
        if "send" in action["grants"]:
            return "The message may already have been sent; check the chat. It will not be sent again automatically."
        if "draft" in action["grants"]:
            return "Send was not allowed, but the reviewed text may be in the chat's message box. Check it before retrying."
        return "Send was not allowed. This request will not be replayed."

    def _expire(self):
        now = self.clock()
        for action in self.actions.values():
            if action["status"] == "running" and now >= action["leased_at"] + (35 if action["kind"] == "read" else 25):
                detail = ("No verified reading result; reading stopped and will not retry automatically."
                          if action["kind"] == "read" else "No verified sending result. " + self._interrupted_send_detail(action)
                          if action["kind"] == "send" else "No verified result; check the existing WhatsApp draft before retrying.")
                action.update(status="unknown", detail=detail)
            elif action["status"] == "queued" and now * 1000 >= action["expires"]:
                action.update(status="expired", detail="The request expired without opening or typing.")
        for key in list(self.actions):
            if self.actions[key]["status"] in FINAL and now * 1000 - self.actions[key]["created"] > 600000:
                del self.actions[key]

    def touch(self, client, beat):
        with self.lock:
            self._expire()
            parsed = self._client(client)
            at = beat.get("at") if isinstance(beat, dict) else None
            now = self.clock()
            if parsed is None:
                self.usable = self.connected = False
                self.unavailable_reason = "Reload the Check Call Log add-on in Chrome, then reload the WhatsApp tab to enable drafts."
                self.unavailable_code = "old_reader"
                self.readiness()
                return False
            if type(at) not in (int, float) or not math.isfinite(at) or not -5 <= now - at / 1000 < 90:
                self.usable = self.connected = False
                self.unavailable_reason = "WhatsApp's last update is not current. Show the WhatsApp tab and wait for a fresh update."
                self.unavailable_code = "stale"
                self.readiness()
                return False
            if self.client and parsed["owner"] != self.client["owner"]:
                for action in self.actions.values():
                    if action["status"] in ("queued", "running"):
                        detail = "The WhatsApp tab changed; this request will not be replayed."
                        if action["status"] == "running" and action["kind"] == "send":
                            detail = "The WhatsApp tab changed. " + self._interrupted_send_detail(action)
                        action.update(status="unknown" if action["status"] == "running" else "blocked", detail=detail)
            self.client, self.client_at = parsed, min(now, at / 1000)
            self.connected = (beat.get("pane") is True and type(beat.get("rows")) is int and beat["rows"] > 0
                              and type(beat.get("paused")) is bool and beat.get("useHere") is False
                              and beat.get("visible") == "visible")
            self.usable = self.connected and beat.get("paused") is False
            if beat.get("useHere") is True:
                self.unavailable_reason = "WhatsApp Web is open somewhere else. Press Use here in the WhatsApp tab."
                self.unavailable_code = "disconnected"
            elif beat.get("pane") is not True or type(beat.get("rows")) is not int or beat["rows"] <= 0:
                self.unavailable_reason = "WhatsApp Web has no chat list yet. Show its tab and wait for it to load; sign in if asked."
                self.unavailable_code = "no_list"
            elif beat.get("visible") == "hidden":
                self.unavailable_reason = "WhatsApp is not on screen. Show its tab beside Check Call Log before opening a chat or preparing a draft."
                self.unavailable_code = "hidden"
            elif beat.get("paused") is True:
                self.unavailable_reason = "WhatsApp's search box or a chat filter is in use. Clear the search with its X and select All chats, then try again."
                self.unavailable_code = "search"
            else:
                self.unavailable_reason = "WhatsApp's latest update is incomplete. Show its tab and wait for a fresh update."
                self.unavailable_code = "incomplete"
            # Never leave an undelivered request waiting behind a known unavailable
            # chat list. Running work keeps its separate permission/ACK rules: its
            # own search can legitimately produce a paused heartbeat.
            self.readiness()
            return True

    def readiness(self):
        with self.lock:
            self._expire()
            age = self.clock() - self.client_at if self.client else None
            ready = self.client is not None and 0 <= age < 90 and self.usable
            reason = "ready" if ready else self.unavailable_reason
            reason_code = "ready" if ready else self.unavailable_code
            if self.client and not 0 <= age < 90:
                reason = "No recent update from WhatsApp Web. Show its tab and wait for a fresh update; reload it only if it stays disconnected."
                reason_code = "stale"
            if not ready:
                for action in self.actions.values():
                    if action["status"] == "queued":
                        action.update(status="blocked", detail=reason + " This action had not started; nothing was opened or typed by it.")
            pending = sum(a["status"] in ("queued", "running") for a in self.actions.values())
            return {"ready": ready, "reason": reason, "age": round(age, 1) if age is not None else None,
                    "capability": CAPABILITY if self.client else None,
                    "owner": self.client["owner"] if self.client else None, "busy": pending > 0, "pending": pending,
                    "reason_code": reason_code}

    def enqueue(self, kind, chat, text=None, ttl=90, phone=None, since=None):
        if kind not in ("open", "draft", "read", "send"):
            raise ValueError("Only opening a chat, reading messages, preparing a draft or sending one reviewed message is supported.")
        try:
            chat_units = len(chat.encode("utf-16-le")) // 2 if isinstance(chat, str) else 0
        except UnicodeEncodeError:
            raise ValueError("Choose one exact observed chat name.") from None
        if not isinstance(chat, str) or not chat.strip() or chat_units > 120 or any(ord(c) < 32 for c in chat):
            raise ValueError("Choose one exact observed chat name.")
        if kind in ("draft", "send"):
            wa_beta.validate_text(text)
        if kind == "send" and (not isinstance(phone, str) or not re.fullmatch(r"[1-9][0-9]{8,14}", phone)):
            raise ValueError("Sending needs this guard's saved, verified phone number.")
        if kind not in ("draft", "send") and text is not None:
            raise ValueError("An open-chat or reading request cannot carry draft text.")
        if type(ttl) not in (int, float) or not 5 <= ttl <= 120:
            raise ValueError("Request lifetime must be between 5 and 120 seconds.")
        if phone is not None and (not isinstance(phone, str) or not re.fullmatch(r"[1-9][0-9]{8,14}", phone)):
            raise ValueError("Optional verified phone must be international digits.")
        if kind == "read" and (phone is None or type(since) is not int or not max(0, int(self.clock() * 1000) - 172800000) <= since <= int(self.clock() * 1000) + 5000):
            raise ValueError("Reading requires a verified phone and a valid starting time.")
        if kind != "read" and since is not None:
            raise ValueError("Only reading requests carry a starting time.")
        with self.lock:
            status = self.readiness()
            if not status["ready"]:
                raise ValueError(status["reason"])
            if sum(a["status"] in ("queued", "running") for a in self.actions.values()) >= 8:
                raise ValueError("Finish the existing draft requests first.")
            now, key = self.clock(), secrets.token_hex(16)
            action = {"id": key, "kind": kind, "chat": chat.strip(), "text": text, "phone": phone, "since": since,
                      "created": int(now * 1000), "expires": int((now + ttl) * 1000),
                      "owner": self.client["owner"], "status": "queued", "detail": "Waiting for WhatsApp.",
                      "grants": {}}
            self.actions[key] = action
            return key

    def lease(self, client):
        with self.lock:
            self._expire()
            parsed = self._client(client)
            if not parsed or not self.readiness()["ready"] or parsed != self.client:
                return None
            if any(a["status"] == "running" for a in self.actions.values()):
                return None
            for action in self.actions.values():
                if action["status"] == "queued" and action["owner"] == parsed["owner"]:
                    action.update(status="running", leased_at=self.clock(), detail="Opening WhatsApp; awaiting verification.")
                    result = {k: copy.deepcopy(action[k]) for k in ("id", "kind", "chat", "text", "phone", "created", "expires", "owner")}
                    if action["kind"] == "read":
                        result["since"] = action["since"]
                    return result
            return None                              # a leased request is NEVER delivered a second time

    def authorize(self, value):
        """One short-lived grant immediately before each DOM mutation; no retries or enqueue route."""
        if not isinstance(value, dict) or not isinstance(value.get("id"), str) or value.get("step") not in ("search", "open", "profile", "draft", "read", "send"):
            return None
        with self.lock:
            self._expire()
            action = self.actions.get(value["id"])
            now, step = self.clock(), value["step"]
            if not action or action["status"] != "running" or action["owner"] != value.get("owner") or \
                    not self.client or self.client["owner"] != action["owner"] or not self.connected or \
                    not 0 <= now - self.client_at < 90 or now * 1000 >= action["expires"] or "send" in action["grants"]:
                return None
            if step != "read" and step in action["grants"] or step == "search" and action["grants"] or \
                    step == "profile" and ("open" not in action["grants"] or "draft" in action["grants"] or "read" in action["grants"]) or \
                    step == "read" and (action["kind"] != "read" or "open" not in action["grants"] or action.get("read_grants", 0) >= 24) or \
                    step == "draft" and (action["kind"] not in ("draft", "send") or "open" not in action["grants"]) or \
                    step == "send" and (action["kind"] != "send" or "open" not in action["grants"]):
                return None
            expires = min(action["expires"], int((now + 2) * 1000))
            action["grants"][step] = expires
            if step == "read":
                action["read_grants"] = action.get("read_grants", 0) + 1
            return {"ok": True, "id": action["id"], "owner": action["owner"], "step": step, "expires": expires}

    def cancel(self, key):
        with self.lock:
            self._expire()
            action = self.actions.get(key) if isinstance(key, str) else None
            if action and action["status"] in ("queued", "running"):
                final_step = action["kind"] if action["kind"] in ("draft", "read", "send") else "open"
                uncertain = final_step in action["grants"]
                if uncertain and action["kind"] == "send":
                    detail = "Stopped after Send was allowed. The message may already have been sent; check the chat. It will not be sent again automatically."
                elif action["kind"] == "send" and "draft" in action["grants"]:
                    detail = "Stopped before Send. The message may be in the chat's message box, NOT sent; check it."
                elif uncertain and action["kind"] == "read":
                    detail = "Reading stopped after a reading step was allowed. Some messages may already have been read; no message text was typed."
                elif uncertain:
                    detail = "Stopped after the final step was allowed. The chat or draft may already be present; check WhatsApp before trying again."
                elif "open" in action["grants"]:
                    detail = "Stopped after the chat was allowed to open. It may now be open, but no message text was typed. Clear any remaining search and select All chats."
                elif "search" in action["grants"]:
                    detail = "Stopped after searching was allowed. The search box may still hold the name; clear it and select All chats. No message text was typed."
                else:
                    detail = "Stopped before WhatsApp was touched. Nothing in WhatsApp was changed by this action."
                action.update(status="unknown" if uncertain else "cancelled", detail=detail)
            return self.status(key)

    def cancel_all(self):
        with self.lock:
            self._expire()
            keys = [key for key, action in self.actions.items() if action["status"] in ("queued", "running")]
            return [self.cancel(key) for key in keys]

    def acknowledge(self, value, commit_read=None):
        if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not isinstance(value.get("status"), str):
            return False
        with self.lock:
            self._expire()
            action = self.actions.get(value.get("id"))
            status = value.get("status")
            if not action or value.get("owner") != action["owner"] or status not in FINAL:
                return False
            if status == "sent" and (action["kind"] != "send" or "send" not in action["grants"]):
                return False
            if status in ("opened", "drafted", "already_draft"):
                step = "draft" if status == "drafted" else "open"
                kind = "open" if status == "opened" else "draft"
                if action["kind"] != kind or step not in action["grants"]:
                    return False
            reading = None
            if status == "read":
                if action["kind"] != "read" or "read" not in action["grants"]:
                    return False
                reading = reading_summary(value.get("reading"), action["since"], int(self.clock() * 1000))
                if reading is None:
                    return False
            elif "reading" in value:
                return False
            if action["status"] in FINAL:
                return action["status"] == status and (status != "read" or action.get("reading") == reading)
            if action["status"] != "running":
                return False
            if reading is not None and reading["complete"] and commit_read is not None:
                receipt = {key: action[key] for key in ("id", "chat", "since", "created")}
                receipt["completed"] = int(self.clock() * 1000)
                try:
                    if commit_read(receipt) is not True:
                        return False
                except Exception:
                    return False                    # failed persistence never releases positive evidence
            detail = value.get("detail", "")
            action.update(status=status, detail=detail[:800] if isinstance(detail, str) else "")
            if reading is not None:
                action["reading"] = reading
            unread = value.get("unread_before")
            if action["kind"] == "read" and "open" in action["grants"] and type(unread) is int and 0 < unread < 1000:
                # Opening may clear a badge even when the subsequent read is interrupted.
                action["unread_before"] = unread
            return True

    def status(self, key):
        with self.lock:
            self.readiness()                          # settle a queued action as soon as its tab is unavailable
            action = self.actions.get(key) if isinstance(key, str) else None
            if not action:
                return {"id": key, "status": "unknown", "detail": "Request is no longer available; check WhatsApp."}
            result = {k: action[k] for k in ("id", "status", "detail", "created", "expires")}
            if "reading" in action:
                result["reading"] = copy.deepcopy(action["reading"])
            if "unread_before" in action:
                result["unread_before"] = action["unread_before"]
            return result
