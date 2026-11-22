"""Isolated invisible WhatsApp beta UI behavior, persistence and minimum-size layout.

All contacts/messages are synthetic, beta storage is scratch-only, and every opener/sound is mocked.
The existing rig blocks activation and makes all test windows transparent and click-through.
"""
import copy
import datetime
import json
import os
import sys
from tkinter import ttk

from drive6 import Rig, HOME, WORK
from drive import quiet_state

sys.path.insert(0, os.path.join(HOME, "tool"))
import rostercolour
import wa_beta
import wa_ui
import whatsapp

STATE_PATH = os.path.join(WORK, "beta-ui-private.json")
os.environ["CCL_BETA_STATE"] = STATE_PATH
FIXTURE = os.path.join(HOME, "tool", "tests", "roster-2026-10-19.xlsx")
DAY = "2026-10-23"
events, opens, sounds, roster_writes = [], [], [], []
requests, replies, composer = [], {}, {}
bridge = {"ready": True}
reader = {"state": "off", "why": "Synthetic reader offline"}
config = {"controllers": ["Lena Drayton"], "trusted": ["Beta Guard 05"], "draft_to": [], "phones": {}}
whatsapp.settings = lambda: copy.deepcopy(config)
whatsapp.reader_state = lambda now=None: (reader["state"], reader["why"])
whatsapp.last_beat = lambda: (0, {})
whatsapp.events_between = lambda start, end: list(events)
whatsapp._read_json = lambda path, default: copy.deepcopy(default)
whatsapp.load_aliases = lambda controller=None: {chat: name for name, chat in wa_ui.Store(STATE_PATH).read().get("chat_names", {}).items()}
rostercolour.send = lambda *args, **kwargs: roster_writes.append((args, kwargs)) or {"ok": False, "why": "test only"}
wa_ui.BetaUI.open_url = lambda self, url: opens.append(url)
wa_ui.BetaUI.bridge_state = lambda self: dict(bridge)


def enqueue(self, kind, chat, text=None):
    action_id = "mock-action-%d" % (len(requests) + 1)
    requests.append(dict(id=action_id, kind=kind, chat=chat, text=text))
    replies[action_id] = {"status": "pending"}
    return action_id


wa_ui.BetaUI.enqueue = enqueue
wa_ui.BetaUI.action_status = lambda self, action_id: dict(replies[action_id])
total = []


def synthetic_shift(rig):
    rig.app.auto_run = lambda: None
    if rig.app.tick_job:
        rig.root.after_cancel(rig.app.tick_job)
        rig.app.tick_job = None
    rig.show("Lena Drayton", DAY)
    template = copy.deepcopy(rig.calls()[0])
    calls = []
    for number in range(1, 45):
        call = copy.deepcopy(template)
        call.update(name="Beta Guard %02d" % (1 if number == 2 else number),
                    site="Synthetic Venue %02d" % number, venue="Synthetic Venue %02d" % number,
                    shift="02:00-close" if number == 2 else "22:00-close", start=1560 if number == 2 else 1320,
                    call=1200, call_text="20:00", due="20:00", response="Ok", default_response="Ok",
                    ahead=False, edited=False, timed=False, heard={}, no=number, strike=False)
        if number == 3:
            call.update(heard={"how": "voice", "response": "", "said": "0:30", "at": "21:00"}, response="")
        if number == 4:
            call.update(heard={"how": "reply", "response": "Ok", "said": "Yes", "at": "21:00"})
        calls.append(call)
    log = rig.app.pack["calls"]
    log["calls"], log["notes"] = calls, []
    log["meta"].update(supervisor="Lena Drayton", date_iso=DAY, start_min=990, end_min=1800,
                       total=44, pending=0, early=0, ahead=False)
    rig.app.refresh()
    rig.app.beta.show()
    rig.root.bell = lambda *args: sounds.append("bell")
    rig.pump(100)
    return rig.app.beta


def enter_text(rig, beta, text):
    beta.editor.delete("1.0", "end")
    beta.editor.insert("1.0", text)
    beta.editor.edit_modified(True)
    beta.text_changed()
    rig.pump(30)


def box(widget):
    return (widget.winfo_rootx(), widget.winfo_rooty(), widget.winfo_width(), widget.winfo_height())


def inside(outer, inner):
    x, y, width, height = box(outer)
    a, b, w, h = box(inner)
    return inner.winfo_ismapped() and w > 2 and h > 2 and x <= a and y <= b and a + w <= x + width and b + h <= y + height


def contrast(root, first, second):
    def luminance(colour):
        channels = [value / 65535 for value in root.winfo_rgb(colour)]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
        return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))
    values = sorted((luminance(first), luminance(second)))
    return (values[1] + .05) / (values[0] + .05)


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def acknowledge(beta, status):
    pending = beta.bridge_pending
    if beta.bridge_job:
        beta.root.after_cancel(beta.bridge_job)
        beta.bridge_job = None
    replies[pending["id"]] = {"status": status}
    request = next(r for r in requests if r["id"] == pending["id"])
    if status == "drafted":
        composer[request["chat"]] = request["text"]
    beta.check_bridge()


def transition_regressions(rig, beta, first):
    """Exercise real transition callbacks with synthetic data and temporary write failure."""
    app = rig.app
    beta.show(first["key"])
    beta.flush()
    old_packs, old_dates, old_day, old_name = app.packs, app.dates, app.day, app.name
    old_scope = beta.scope
    next_day = old_dates[0] + datetime.timedelta(days=1)
    next_pack = copy.deepcopy(app.pack)
    next_pack["date"] = next_day
    next_pack["calls"]["stem"] = old_scope + "-next-day"
    next_pack["calls"]["meta"]["date_iso"] = next_day.isoformat()

    def restore():
        app.packs, app.dates, app.day, app.name = old_packs, old_dates, old_day, old_name
        app.refresh()
        beta.show(first["key"])

    # A delayed save failure must veto the action before changing any selected context.
    app.packs, app.dates = [old_packs[old_day], next_pack], [old_dates[0], next_day]
    app.day = 0
    pending_text = "Unsaved draft must stay with its original shift."
    beta.editor.delete("1.0", "end")
    beta.editor.insert("1.0", pending_text)
    beta.editor.edit_modified(True)  # no event-loop turn: settle must capture this synchronously
    saved_write = beta.store.write

    def unavailable(*args, **kwargs):
        raise OSError("Synthetic beta write failure")

    beta.store.write = unavailable
    try:
        for label, action in (("controller", lambda: app.set_name("Lena Other")),
                              ("date", lambda: app.set_dates([next_day])),
                              ("shown day", lambda: app.show_day(1))):
            action()
            rig.check("failed beta save blocks %s transition" % label,
                      app.name == old_name and app.day == 0 and app.pack is old_packs[old_day]
                      and app.dates == [old_dates[0], next_day] and beta.scope == old_scope)
            rig.check("failed %s transition preserves unsaved draft text" % label,
                      beta.editor.get("1.0", "end-1c") == pending_text and beta.dirty)
    finally:
        beta.store.write = saved_write
    app.set_name("Lena Other")
    rig.check("recovered write saves the original draft before controller transition",
              app.name == "Lena Other" and beta.store.read()["drafts"][beta.record_id(old_scope, first["key"])]
              .get("text") == pending_text)
    restore()

    # Modeless pickers cannot use a later selected controller/date for their old row.
    for label in ("controller", "date"):
        beta.choose_contact()
        window = beta.import_windows[-1]
        tree = next(w for w in descendants(window) if isinstance(w, ttk.Treeview))
        tree.selection_set(tree.get_children()[0])
        save_button = next(w for w in descendants(window) if isinstance(w, wa_ui.Button) and w.text.startswith("I verified"))
        if label == "controller":
            app.set_name("Lena Other")
        else:
            app.packs, app.dates, app.day = [old_packs[old_day], next_pack], [old_dates[0], next_day], 0
            app.show_day(1)
        before = copy.deepcopy(beta.store.read())
        error = None
        try:
            save_button.command()
        except Exception as exc:
            error = repr(exc)
        rig.check("stale contact picker after %s change exits without error" % label, error is None, error)
        rig.check("stale contact picker after %s change writes nothing" % label, beta.store.read() == before)
        if window.winfo_exists():
            window.destroy()
        restore()

    # An ACK belongs to its original guard/date even if another row is now on screen.
    other = beta.rows[2]
    beta.phone_var.set("+" + beta.phone_for(first["name"]))
    beta.flush()
    beta.open_chat()
    beta.show(other["key"])
    acknowledge(beta, "opened")
    words = beta.message.cget("text")
    rig.check("delayed open-chat acknowledgment identifies its original recipient",
              first["name"] in words and DAY in words and "selected chat" not in words, words)
    rig.check("delayed open-chat acknowledgment keeps the newer selection", beta.selected == other["key"])
    beta.show(first["key"])
    beta.review()
    signature = beta.signature(first)
    beta.open_draft()
    pending = copy.deepcopy(beta.bridge_pending)
    before_requests = len(requests)
    before_data, before_composer = copy.deepcopy(beta.store.read()), copy.deepcopy(composer)
    cancelled, saved_cancel = [], beta.cancel_action

    def cancel_transition(action_id):
        cancelled.append(action_id)
        replies[action_id] = {"status": "cancelled", "detail": "Synthetic queued request stopped before WhatsApp was touched."}
        return dict(replies[action_id])

    beta.cancel_action = cancel_transition
    try:
        app.packs, app.dates, app.day = [old_packs[old_day], next_pack], [old_dates[0], next_day], 0
        app.show_day(1)
        rig.check("changing the shown shift cancels its queued guard draft",
                  cancelled == [pending["id"]] and beta.bridge_pending is None and len(requests) == before_requests)
        rig.check("shift cancellation preserves both shifts' drafts and the existing WhatsApp composer",
                  beta.store.read() == before_data and composer == before_composer
                  and beta.store.read()["drafts"][beta.record_id(old_scope, first["key"])].get("opened") != signature)
        # A delayed result from that revoked request is not a new instruction to prepare or send.
        beta.check_bridge(reply={"status": "drafted", "id": pending["id"]})
        rig.check("a late draft acknowledgment after cancellation cannot mark either shift prepared",
                  beta.store.read() == before_data and composer == before_composer and beta.bridge_pending is None)
    finally:
        beta.cancel_action = saved_cancel
    restore()


rig = Rig("wa-beta-ui", now="2026-10-23 21:40", roster=FIXTURE,
          settings={"auto_green_for": [], "preview": False, "chooser": False})
try:
    beta = synthetic_shift(rig)
    first, second = beta.rows[:2]
    first_key = first["key"]
    rig.check("beta store is the explicit scratch file", os.path.normcase(beta.store.path) == os.path.normcase(STATE_PATH), beta.store.path)
    rig.check("all 44 call previews are distinct", len(beta.rows) == 44 and len({r["key"] for r in beta.rows}) == 44)
    rig.check("one guard's two assignments retain separate per-call drafts", first["name"] == second["name"] and first["key"] != second["key"])
    rig.check("overnight assignment retains next-day metadata", second["start_date"] == "2026-10-24" and second["start_time"] == "02:00")
    rig.check("default draft is concise", beta.editor.get("1.0", "end-1c") == "All okay for Synthetic Venue 01?")
    rig.check("reply counts distinguish evidence, trust, review and waiting",
              wa_beta.status_counts(beta.rows) == dict(confirmed=1, trusted=1, review=1, no_reply=41), wa_beta.status_counts(beta.rows))
    beta.search_var.set("venue 03")
    rig.check("Find narrows the queue to the matching venue", list(beta.item_keys.values()) == [beta.rows[2]["key"]])
    beta.search_var.set("absent synthetic guard")
    rig.check("no search match disables recipient actions and explains the empty view",
              beta.selected is None and not beta.open_draft_button.enabled and not beta.send_reviewed_button.enabled
              and beta.queue_empty.winfo_manager() == "place")
    beta.clear_search()
    beta.set_filter("confirmed")
    rig.check("Confirmed filter keeps reply evidence distinct from trusted rules",
              list(beta.item_keys.values()) == [beta.rows[3]["key"]])
    item = beta.tree.get_children()[0]
    rig.check("a missing contact does not hide the confirmed response", beta.tree.item(item,"values")[2].startswith("Confirmed"))
    beta.show(first_key)
    beta.evidence.tag_add("sel", "1.0", "1.4")
    beta.show_selected()
    rig.check("unchanged reply details keep the user's text selection", bool(beta.evidence.tag_ranges("sel")))
    rig.check("message button does not present zero new alerts as zero replies", beta.messages_button.text == "Messages & drafts")
    rig.check("startup never opens WhatsApp, sounds or writes roster", not opens and not requests and not sounds and not roster_writes)
    rig.check("auto sending always starts off", beta.sending_on is False)
    rig.check("offline reader is clearly shown", "not connected" in beta.banner_title.cget("text"))
    rig.check("missing contact keeps draft opening disabled", not beta.open_draft_button.enabled)
    beta.open_draft()
    rig.check("direct action without a verified contact opens nothing", not opens and not requests)

    old_catalog = {"version": 7, "private_extra": {"keep": True}, "imports": [{"source": "previous"}],
                   "candidates": [{"name": "Archived Person", "phones": ["353870000111"], "issues": [], "keep_note": "original"},
                                  {"name": "Beta Guard 01", "phones": ["353870000112"], "issues": []}]}
    beta.catalog_store().write(copy.deepcopy(old_catalog))
    beta.retain_catalog({"candidates": [dict(old_catalog["candidates"][0], card=1),
                                        {"name": "New Imported Person", "phones": ["353870000113"], "issues": []}],
                         "issues": []}, "synthetic-partial.vcf")
    catalog = beta.catalog_store().read()
    rig.check("partial import keeps the full prior catalog and deduplicates overlapping candidates",
              len(catalog["candidates"]) == 3 and catalog["candidates"][:2] == old_catalog["candidates"], catalog["candidates"])
    rig.check("partial import preserves unrelated fields and previous import history",
              catalog["version"] == 7 and catalog["private_extra"] == {"keep": True}
              and len(catalog["imports"]) == 2 and catalog["imports"][0] == old_catalog["imports"][0])
    rig.check("catalog retention never verifies a contact or opens a chat automatically", not beta.contacts() and not requests and not opens)

    # Preserve a scrolled queue and explicit selection across the same heartbeat data.
    beta.selected = beta.rows[25]["key"]
    beta.render_queue()
    beta.show_selected()
    beta.tree.yview_moveto(0.6)
    rig.pump(50)
    selected, scroll = beta.selected, beta.tree.yview()
    beta.refresh()
    rig.pump(50)
    rig.check("unchanged heartbeat preserves selected assignment", beta.selected == selected)
    rig.check("unchanged heartbeat preserves queue scroll", abs(beta.tree.yview()[0] - scroll[0]) < .02, (scroll, beta.tree.yview()))
    beta.show(first_key)
    rig.pump(30)

    beta.phone_var.set("+353870000001")
    beta.save("chat_names", first["name"], first["name"])
    beta.save_contact()
    rig.check("contact saves only for selected exact roster name", beta.contacts() == {first["name"]: "353870000001"}, beta.contacts())
    rig.check("saving contact still requires review", not beta.open_draft_button.enabled)
    beta.review()
    rig.check("exact saved recipient and text become reviewed", beta.draft_record(first).get("reviewed") == beta.signature(first))
    rig.check("reviewed draft preparation needs no auto-send mode", beta.open_draft_button.enabled and beta.sending_on is False)
    # 8 Nov: Lena asked to really send - one at a time, off at every start, only when he presses Send
    rig.check("Sending starts off: no Send for a reviewed message", beta.sending_on is False and not beta.send_reviewed_button.enabled)
    beta.toggle_sending()
    rig.check("switching Sending on enables Send for this reviewed guard", beta.sending_on is True and beta.send_reviewed_button.enabled
              and "Sending: on" in beta.send_button.text and first["name"].split()[0] in beta.send_reviewed_button.text)
    beta.send_reviewed()
    rig.check("pressing Send makes exactly one send request, with the reviewed text and his verified phone",
              len(requests) == 1 and requests[-1]["kind"] == "send" and requests[-1]["text"] == beta.draft_record(first).get("text")
              and beta.phone_for(first["name"]) == "353870000001", requests[-1:])
    beta.cancel_pending()
    requests.clear()
    beta.toggle_sending()
    rig.check("...and Sending switches off again", beta.sending_on is False and not beta.send_reviewed_button.enabled)
    beta.open_draft()
    rig.check("reviewed draft makes one explicit bridge request without OS opening", len(requests) == 1 and requests[-1]["kind"] == "draft" and not opens)
    rig.check("unacknowledged request is not recorded as prepared", not beta.draft_record(first).get("opened") and beta.bridge_pending is not None)
    beta.open_draft()
    rig.check("another press cannot submit a duplicate in-bullseye action", len(requests) == 1)
    acknowledge(beta, "drafted")
    rig.check("acknowledged unsent draft records preparation, never sent", beta.draft_record(first).get("opened") == beta.signature(first)
              and "press Send yourself" in beta.message.cget("text") and not beta.draft_record(first).get("sent"))
    beta.show(second["key"])
    beta.review()
    beta.open_draft()
    rig.check("second assignment remains a separate reviewed request", len(requests) == 2 and requests[-1]["text"] != requests[0]["text"])
    acknowledge(beta, "existing_draft")
    rig.pump(30)
    rig.check("existing composer text is preserved and no replacement is retried", composer[first["name"]] == requests[0]["text"] and len(requests) == 2)
    rig.check("second assignment is not falsely recorded as prepared", not beta.draft_record(second).get("opened") and "preserved" in beta.error.lower())
    beta.show(first_key)
    prior_error = "Previous synthetic action failed"
    beta.problem(prior_error)
    beta.open_chat()
    acknowledge(beta, "opened")
    rig.check("verified chat-open acknowledgment clears the previous action error", beta.error == "" and "verified" in beta.message.cget("text") and first["name"] in beta.message.cget("text"))
    rig.check("successful chat-open acknowledgment refreshes the stale error banner",
              prior_error not in beta.banner_detail.cget("text") and "offline" in beta.banner_detail.cget("text"))

    changed = "All okay for Synthetic Venue 01? Please confirm."
    enter_text(rig, beta, changed)
    rig.check("typing after review immediately disables draft opening", not beta.open_draft_button.enabled)
    beta.refresh()
    rig.check("refresh preserves pending draft text", beta.editor.get("1.0", "end-1c") == changed)
    beta.flush()
    rig.check("saved draft mutation invalidates prior review", beta.draft_record(first).get("text") == changed and not beta.draft_record(first).get("reviewed"))
    beta.problem("Synthetic previous bulk save failure")
    beta.save_all_previews()
    saved_drafts = beta.store.read().get("drafts", {})
    rig.check("save-all creates 44 separate previews without replacing edits", len(saved_drafts) == 44 and beta.draft_record(first)["text"] == changed)
    rig.check("successful bulk recovery clears the old error and updates draft counts immediately",
              not beta.error and "Synthetic previous bulk save failure" not in beta.banner_detail.cget("text")
              and "44 local drafts" in beta.summary.cget("text"))
    beta.review()
    beta.phone_var.set("+353870000002")
    rig.check("typing a contact change invalidates reviewed destination", not beta.open_draft_button.enabled)
    beta.phone_var.set("+353870000001")
    rig.check("changing a number back still requires a fresh review", not beta.open_draft_button.enabled)
    beta.review()
    beta.phone_var.set("+353870000002")
    beta.save_contact()
    rig.check("saving a changed contact clears old review", not beta.draft_record(first).get("reviewed") and not beta.open_draft_button.enabled)
    beta.review()
    beta.store.patch("unrelated", "keep", {"value": 7})
    beta.store.patch("contacts", first["name"], "353870000003")
    before = len(requests)
    beta.open_draft()
    rig.check("external contact change blocks the stale reviewed draft", len(requests) == before)
    beta.refresh()
    beta.phone_var.set("+353870000003")
    beta.review()
    beta.store.patch("chat_names", first["name"], "Different verified chat")
    before = len(requests)
    beta.open_draft()
    rig.check("chat alias changes invalidate the reviewed recipient", len(requests) == before)
    beta.store.patch("chat_names", first["name"], first["name"])
    beta.refresh()
    beta.review()

    # An assignment's changed start also invalidates its old review signature.
    original_start = rig.calls()[0]["start"]
    rig.calls()[0]["start"] += 15
    beta.refresh()
    rig.check("roster start change invalidates exact review", not beta.open_draft_button.enabled)
    rig.calls()[0]["start"] = original_start
    beta.refresh()

    # Draft text and unverified contact input survive rebuilding widgets for the other theme.
    enter_text(rig, beta, changed + " Thanks.")
    beta.phone_var.set("+353870000004")
    rig.app.switch_theme()
    rig.pump(150)
    beta = rig.app.beta
    rig.check("theme change preserves draft text", beta.editor.get("1.0", "end-1c") == changed + " Thanks.")
    rig.check("theme change preserves unverified contact input", beta.phone_var.get() == "+353870000004")
    rig.check("unverified contact remains unsaved across theme change", beta.phone_for(first["name"]) == "353870000003")
    beta.phone_var.set("+353870000003")
    beta.review()
    draft_before_undo = beta.editor.get("1.0", "end-1c")
    log_undo_count = len(rig.app.undone)
    beta.editor.edit_separator()
    beta.editor.insert("end", " [draft edit]")
    beta.editor.edit_modified(True)
    beta.text_changed()
    rig.key(rig.root, "<Control-z>")
    rig.check("Ctrl+Z in workspace undoes draft text rather than log edits",
              beta.editor.get("1.0", "end-1c") == draft_before_undo and len(rig.app.undone) == log_undo_count)
    rig.key(rig.root, "<Control-y>")
    rig.check("Ctrl+Y in workspace restores only the draft edit", beta.editor.get("1.0", "end-1c") == draft_before_undo + " [draft edit]"
              and len(rig.app.undone) == log_undo_count)
    rig.key(rig.root, "<Control-z>")

    for theme in ("light", "dark"):
        if rig.app.skin.theme != theme:
            rig.app.switch_theme()
        beta = rig.app.beta
        beta.show(first_key)
        style = ttk.Style(rig.root)
        rig.check("%s: custom header uses a theme that renders explicit colours" % theme, style.theme_use() == "clam", style.theme_use())
        for state in ((), ("active",)):
            foreground = style.lookup("Beta.Treeview.Heading", "foreground", state)
            background = style.lookup("Beta.Treeview.Heading", "background", state)
            ratio = contrast(rig.root, foreground, background)
            rig.check("%s: header text contrast is readable in %s state" % (theme, state or "normal"), ratio >= 4.5, (foreground, background, ratio))
        thumb = style.lookup("Beta.Vertical.TScrollbar", "background")
        track = style.lookup("Beta.Vertical.TScrollbar", "troughcolor")
        arrow = style.lookup("Beta.Vertical.TScrollbar", "arrowcolor")
        rig.check("%s: scrollbar thumb is distinct from its track" % theme, contrast(rig.root, thumb, track) >= 3, (thumb, track, contrast(rig.root, thumb, track)))
        rig.check("%s: scrollbar arrow is distinct from its thumb" % theme, contrast(rig.root, arrow, thumb) >= 3, (arrow, thumb, contrast(rig.root, arrow, thumb)))
        beta.choose_contact()
        contact_window = beta.import_windows[-1]
        rig.pump(30)
        scrollbars = [w for w in descendants(beta.workspace) if isinstance(w, ttk.Scrollbar)]
        scrollbars += [w for w in descendants(contact_window) if isinstance(w, ttk.Scrollbar)]
        rig.check("%s: queue, editor and contact picker all use themed scrollbars" % theme,
                  len(scrollbars) == 3 and all(w.cget("style") == "Beta.Vertical.TScrollbar" for w in scrollbars), [w.cget("style") for w in scrollbars])
        contact_state = quiet_state(contact_window)
        rig.check("%s: contact picker stays invisible and nonactivating" % theme,
                  contact_state["alpha"] == 0 and contact_state["click_through"] and contact_state["no_activate"] and not contact_state["foreground"], contact_state)
        contact_window.destroy()
        minimum = rig.root.minsize()
        for width, height in (minimum, (rig.app.px(1480), rig.app.px(960))):
            rig.root.geometry("%dx%d+-2600+40" % (width, height))
            rig.pump(180)
            for name in ("tree", "editor", "search_entry", "phone_entry", "review_button", "open_chat_button", "open_draft_button", "send_reviewed_button"):
                widget = getattr(beta, name)
                rig.check("%s %dx%d: %s remains visible within the window" % (theme, width, height, name), inside(rig.root, widget), (box(rig.root), box(widget)))
                if isinstance(widget, wa_ui.Button):
                    rig.check("%s %dx%d: %s is not cropped" % (theme, width, height, name),
                              widget.winfo_height() >= widget.winfo_reqheight() and widget.winfo_width() >= widget.winfo_reqwidth(),
                              (box(widget), widget.winfo_reqwidth(), widget.winfo_reqheight()))
            for name in ("messages_button", "drafts_button", "sound_button", "send_button"):
                widget = getattr(beta, name)
                rig.check("%s %dx%d: %s banner control fits" % (theme, width, height, name), inside(rig.root, widget)
                          and widget.winfo_width() >= widget.winfo_reqwidth() and widget.winfo_height() >= widget.winfo_reqheight(),
                          (box(widget), widget.winfo_reqwidth(), widget.winfo_reqheight()))
            rig.check("%s %dx%d: beta summary stays visible" % (theme, width, height), inside(rig.root, beta.summary))
            for key, button in beta.filter_buttons.items():
                rig.check("%s %dx%d: %s filter count fits" % (theme,width,height,key), inside(rig.root,button)
                          and button.winfo_width() >= button.winfo_reqwidth())

    # Notifications use only synthetic authoritative incoming messages. Startup, replay and offline samples are silent.
    rig.clock.set("2026-10-23 21:40")
    reader.update(state="on", why="")
    beta.hide()
    beta.poll()
    rig.check("reader activation establishes a silent notification baseline", not beta.feed and not sounds)
    rig.clock.set("2026-10-23 21:41")
    events.append(dict(kind="bubble", chat=first["name"], direction="incoming", ours=False,
                       id="synthetic-arrival-1", text="All good 👍", date="23/10/2026", shown="21:41",
                       seen=int(rig.clock.at.timestamp() * 1000)))
    beta.poll()
    rig.check("new matched incoming bubble appears once in the feed", len(beta.feed) == 1 and beta.unread == 1, (beta.feed, beta.unread))
    beta.poll()
    rig.check("replayed incoming bubble does not duplicate the notification", len(beta.feed) == 1 and beta.unread == 1)
    rig.check("quiet test harness never emits a real sound", not sounds)
    beta.toggle_sound()
    rig.check("sound preference switches off and is saved", not beta.sound_on and beta.store.read()["preferences"]["sound"] is False)
    reader.update(state="off", why="Synthetic reader offline")
    rig.clock.set("2026-10-23 21:42")
    events.append(dict(events[-1], id="offline-arrival", shown="21:42", seen=int(rig.clock.at.timestamp() * 1000)))
    beta.poll()
    rig.check("offline samples do not create arrival notifications", len(beta.feed) == 1)
    reader.update(state="on", why="")
    beta.poll()
    rig.check("reconnect history also stays silent", len(beta.feed) == 1)

    beta.show(first_key)
    rig.clock.set("2026-10-24 07:00")
    beta.refresh()
    rig.check("finished shift retains previews but cannot open a draft", len(beta.rows) == 44 and not beta.current_live() and not beta.open_draft_button.enabled)
    rig.clock.set("2026-10-23 04:00")
    beta.refresh()
    beta.review()
    rig.check("tonight's upcoming shift permits reviewed draft preparation", beta.draft_eligible() and not beta.current_live() and beta.open_draft_button.enabled)
    rig.clock.set("2026-10-22 23:00")
    beta.refresh()
    rig.check("a further future shift remains preview only", not beta.draft_eligible() and not beta.open_draft_button.enabled)
    rig.clock.set("2026-10-23 21:40")
    rig.app.name = "Lena Other"
    beta.refresh()
    rig.check("stale Lena log cannot enable beta for another selected controller", not beta.current_live() and not beta.rows and not beta.banner.winfo_manager())
    rig.app.name = "Lena Drayton"
    beta.refresh()
    beta.show(first_key)
    rig.check("returning to exact controller restores previews without auto sending", len(beta.rows) == 44 and not beta.sending_on)
    transition_regressions(rig, beta, first)
    enter_text(rig, beta, "Draft retained when closing this test window.")
    rig.check("no action sent to real or fake roster", not roster_writes)
    state = quiet_state(rig.root)
    rig.check("beta UI retains invisible nonactivating window protection", state["alpha"] == 0 and state["click_through"] and state["no_activate"] and not state["foreground"], state)
    rig.check("no callback errors in beta interaction and layouts", not rig.errors, rig.errors[:1])
finally:
    total += rig.close()

rig = Rig("wa-beta-ui", now="2026-10-23 21:40", roster=FIXTURE, keep=True,
          settings={"auto_green_for": [], "preview": False, "chooser": False})
try:
    beta = synthetic_shift(rig)
    rig.check("closing and reopening retains the saved draft", beta.editor.get("1.0", "end-1c") == "Draft retained when closing this test window.")
    rig.check("reopening does not promote unsaved contact input", beta.phone_for(beta.rows[0]["name"]) == "353870000003")
    rig.check("saved sound preference survives reopening", beta.sound_on is False)
    rig.check("automatic sending is still off after reopening", beta.sending_on is False)
    rig.check("beta writes preserve unrelated saved fields", beta.store.read().get("unrelated") == {"keep": {"value": 7}})
    rig.check("reopening has no startup sound", not sounds)
    rig.check("no callback errors after reopening", not rig.errors, rig.errors[:1])
finally:
    total += rig.close()

print("%d of %d checks passed" % (sum(total), len(total)))
raise SystemExit(0 if all(total) else 1)
