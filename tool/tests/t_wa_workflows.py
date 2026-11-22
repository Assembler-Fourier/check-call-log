"""Real invisible beta workflows, with synthetic contacts and every external action mocked.

Exercises the actual shortcut bindings and self-test/ACK/cancellation handlers. No live
WhatsApp requests, roster writes, sound, clipboard use or application activation.
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
import wa_ui
import whatsapp

STATE = os.path.join(WORK, "beta-workflows-private.json")
os.environ["CCL_BETA_STATE"] = STATE
if os.path.exists(STATE):
    os.remove(STATE)
FIXTURE = os.path.join(HOME, "tool", "tests", "roster-2026-10-19.xlsx")
DAY, SELF_PHONE = "2026-10-23", "353870000099"
config = {"controllers": ["Lena Drayton"], "trusted": [], "draft_to": ["Lena Drayton"],
          "phones": {"Lena Drayton": SELF_PHONE}}
bridge = {"ready": True}
requests, replies, cancellations, roster_writes = [], {}, [], []
cancel_result = {"status": "cancelled"}
whatsapp.settings = lambda: copy.deepcopy(config)
whatsapp.reader_state = lambda now=None: ("on", "")
whatsapp.last_beat = lambda: (0, {"version": "synthetic-reader-1.3"})
whatsapp.events_between = lambda *args: []
whatsapp._read_json = lambda path, default: copy.deepcopy(default)
whatsapp.load_aliases = lambda controller=None: {}
rostercolour.send = lambda *args, **kwargs: roster_writes.append((args, kwargs)) or {"ok": False}
wa_ui.BetaUI.bridge_state = lambda self: dict(bridge)


def request(kind, chat, phone, text):
    action_id = "synthetic-%d" % (len(requests) + 1)
    requests.append(dict(id=action_id, kind=kind, chat=chat, phone=phone, text=text))
    replies[action_id] = {"status": "pending"}
    return action_id


wa_ui.BetaUI.enqueue_self = lambda self, kind, phone, text=None: request(kind, "Lena Drayton", phone, text)
wa_ui.BetaUI.enqueue = lambda self, kind, chat, text=None: request(kind, chat, self.phone_for(self.selected_row()["name"]), text)
wa_ui.BetaUI.action_status = lambda self, action_id: dict(replies[action_id])


def cancel(action_id):
    cancellations.append(action_id)
    replies[action_id] = dict(cancel_result, id=action_id)
    return dict(replies[action_id])


wa_ui.BetaUI.cancel_action = lambda self, action_id: cancel(action_id)
whatsapp.cancel_actions = lambda: [cancel(item["id"]) for item in requests if replies[item["id"]]["status"] == "pending"]


def acknowledge(beta, status, **extra):
    if beta.bridge_job:
        beta.root.after_cancel(beta.bridge_job)
        beta.bridge_job = None
    replies[beta.bridge_pending["id"]] = dict(status=status, **extra)
    beta.check_bridge()


def full_visible(outer, inner):
    x, y = inner.winfo_rootx(), inner.winfo_rooty()
    return (inner.winfo_ismapped() and inner.winfo_width() >= inner.winfo_reqwidth()
            and inner.winfo_height() >= inner.winfo_reqheight()
            and outer.winfo_rootx() <= x and outer.winfo_rooty() <= y
            and x + inner.winfo_width() <= outer.winfo_rootx() + outer.winfo_width()
            and y + inner.winfo_height() <= outer.winfo_rooty() + outer.winfo_height())


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


rig = Rig("wa-workflows", now=DAY + " 21:40", roster=FIXTURE,
          settings={"auto_green_for": [], "preview": False, "chooser": False})
try:
    app, beta = rig.app, rig.app.beta
    app.auto_run = lambda: None
    if app.tick_job:
        rig.root.after_cancel(app.tick_job)
        app.tick_job = None
    rig.show("Lena Drayton", DAY)
    template = copy.deepcopy(rig.calls()[0])
    calls = []
    for index, name in enumerate(("Fixture Guard Alpha", "Fixture Guard Bravo"), 1):
        call = copy.deepcopy(template)
        call.update(name=name, site="Example Hall", venue="Example Hall", shift="22:00-close", start=1320,
                    call=1200, call_text="20:00", due="20:00", response="Ok", default_response="Ok", no=index,
                    ahead=False, edited=False, timed=False, heard={}, strike=False)
        calls.append(call)
    log = app.pack["calls"]
    log["calls"], log["notes"] = calls, []
    log["meta"].update(supervisor="Lena Drayton", date_iso=DAY, start_min=990, end_min=1800,
                       total=2, pending=0, early=0, ahead=False)
    app.refresh()
    beta.show()
    first = beta.rows[0]
    rig.check("new self-test does nothing on startup", not requests and not beta.data.get("diagnostics"))
    rig.check("summary distinguishes generated preview from saved work", "0 local drafts" in beta.summary.cget("text"), beta.summary.cget("text"))

    # Real root bindings must not mutate a hidden log while the beta workspace is on show.
    confirmations = []
    old_confirm = app.confirm
    app.confirm = lambda chosen: confirmations.extend(chosen)
    rig.key(rig.root, "<Control-g>")
    app.confirm = old_confirm
    rig.check("Ctrl+G in Messages cannot colour the hidden grid", not confirmations and "Return to Check calls" in beta.message.cget("text"))
    old_entries = copy.deepcopy(app.pack["shift"]["entries"])
    app.tab = "shift"
    rig.key(rig.root, "<Control-n>")
    rig.check("Ctrl+N in Messages cannot add a hidden shift entry", app.pack["shift"]["entries"] == old_entries and "Return to logs" in beta.message.cget("text"))
    rig.key(rig.root, "<Control-Key-2>")
    rig.check("Ctrl+2 visibly returns to Patrols", not beta.opened and app.tab == "patrols" and bool(app.body.winfo_manager()))
    beta.show()
    rig.key(rig.root, "<Control-l>")
    rig.check("Ctrl+L visibly opens the controller/day chooser", not beta.opened and app.choosing)
    app.toggle_chooser()
    beta.show()
    rig.check("workspace footer describes draft-local undo", "Ctrl+Z undoes draft text" in app.help_label.cget("text"))

    # Invoke actual Tab bindings with a focus spy; the invisible rig deliberately refuses OS focus.
    moves = []
    class Target:
        def focus_set(self):
            moves.append(True)
    original_next, original_prev = beta.editor.tk_focusNext, beta.evidence.tk_focusPrev
    beta.editor.tk_focusNext = lambda: Target()
    beta.evidence.tk_focusPrev = lambda: Target()
    before = beta.editor.get("1.0", "end-1c")
    rig.key(beta.editor, "<Tab>")
    rig.key(beta.evidence, "<Shift-Tab>")
    beta.editor.tk_focusNext, beta.evidence.tk_focusPrev = original_next, original_prev
    rig.check("Tab and Shift+Tab escape both text areas without inserting a tab", len(moves) == 2 and beta.editor.get("1.0", "end-1c") == before)

    beta.connection = ("on", "")
    bridge.update(ready=False, reason="Reload the synthetic reader")
    beta.refresh_banner()
    rig.check("reader connection does not imply draft readiness", "WhatsApp connected" in beta.banner_title.cget("text") and "Drafts not ready" in beta.banner_detail.cget("text"))
    previous_problem = "Earlier synthetic draft stopped."
    beta.problem(previous_problem)
    rig.check("an error is shown below current readiness instead of replacing it",
              "Drafts not ready" in beta.banner_detail.cget("text")
              and beta.banner_detail.cget("text").endswith("\n" + previous_problem))
    bridge.update(ready=True, reason="")
    beta.connection = ("off", "The synthetic reader report expired")
    beta.refresh_banner()
    rig.check("an earlier error cannot hide a later connection or readiness change",
              "not connected" in beta.banner_title.cget("text") and "Drafts ready" in beta.banner_detail.cget("text")
              and "report expired" in beta.banner_detail.cget("text") and previous_problem in beta.banner_detail.cget("text"))
    beta.error, beta.connection = "", ("on", "")
    bridge.update(ready=False, reason="Reload the synthetic reader")
    beta.refresh_banner()
    beta.banner.pack_forget()
    beta.workspace.pack_forget()                    # the log body is already hidden by Messages
    beta.problem("Synthetic failure during workspace transition.")
    rig.check("an error refreshes banner text without packing against an unpacked body",
              not beta.banner.winfo_manager() and not app.body.winfo_manager()
              and "workspace transition" in beta.banner_detail.cget("text")
              and "Drafts not ready" in beta.banner_detail.cget("text"))
    beta.present()
    packed = beta.banner.master.pack_slaves()
    rig.check("completed workspace layout restores the banner before the visible Messages workspace",
              beta.banner in packed and beta.workspace in packed and packed.index(beta.banner) < packed.index(beta.workspace))
    beta.error = ""
    beta.refresh_banner()
    original_geometry = rig.root.geometry()
    screen_width, screen_height = rig.root.winfo_screenwidth(), rig.root.winfo_screenheight()
    # This own-process root remains transparent and nonactivating. An edge-positioned
    # parent exercises clamping rather than accepting an arbitrary default location.
    rig.root.geometry("+%d+%d" % (screen_width - 80, screen_height - 100))
    rig.pump(80)
    placement_parent_before = (rig.root.geometry(), rig.root.winfo_rootx(), rig.root.winfo_rooty())
    beta.show_self_test()
    cancelled_position = beta.self_test_position_job
    beta.close_self_test()
    rig.pump(60)
    rig.check("closing a self-test before placement cancels its pending callback",
              beta.self_test_position_job is None and cancelled_position not in rig.root.tk.call("after", "info")
              and not rig.root._ccl_tcl_errors, rig.root._ccl_tcl_errors)
    beta.show_self_test()
    rig.pump(120)
    dialog = beta.self_test_window
    dx, dy, dw, dh = dialog.winfo_rootx(), dialog.winfo_rooty(), dialog.winfo_width(), dialog.winfo_height()
    print("PLACEMENT_DIAGNOSTIC", {"requested_parent": (screen_width - 80, screen_height - 100),
          "parent_before": placement_parent_before,
          "parent_after": (rig.root.geometry(), rig.root.winfo_rootx(), rig.root.winfo_rooty()),
          "dialog_geometry": dialog.geometry(), "dialog_rendered": (dx, dy, dw, dh)})
    rig.check("self-test opens near its edge-positioned parent with its title and body on screen",
              dx >= 0 and dy >= app.px(20) and dx + dw <= screen_width and dy + dh <= screen_height
              and dx + dw / 2 >= screen_width / 2 and dy + dh / 2 >= screen_height / 2,
              (dx, dy, dw, dh, screen_width, screen_height))
    rig.root.geometry(original_geometry)
    rig.pump(80)
    rig.check("self-test controls stay disabled while draft tools are unavailable", not beta.self_test_open_button.enabled and not beta.self_test_draft_button.enabled)
    beta.run_self_test("draft")
    diag = beta.store.read()["diagnostics"]["self_test"]
    rig.check("offline self-test saves a blocked current attempt without an action", diag["status"] == "blocked" and not requests)
    bridge.update(ready=True, reason="")
    beta.update_self_test()
    rig.check("self-test identifies only configured self, masking phone", "0099" in beta.self_test_connection.cget("text") and SELF_PHONE not in beta.self_test_connection.cget("text"))
    rig.check("WhatsApp on screen: no warning", "not on screen" not in beta.self_test_connection.cget("text"))
    # 7 Nov live: the tab was hidden (Chrome behind the app) and a self-test ran out of time half-way
    shown_beat = whatsapp.last_beat
    whatsapp.last_beat = lambda: (0, {"version": "synthetic-reader-1.3", "visible": "hidden"})
    beta.update_self_test()
    rig.check("WhatsApp not on screen: the panel says to show it, in amber, where the buttons are",
              "not on screen" in beta.self_test_connection.cget("text") and beta.self_test_connection.cget("fg") == beta.app.skin["amber"])
    rig.check("...the buttons stay usable (the report may be old; the reader checks again itself)",
              beta.self_test_open_button.enabled and beta.self_test_draft_button.enabled)
    saved_error, beta.error = beta.error, None        # (an earlier step left a deliberate error on the banner)
    beta.refresh_banner()
    rig.check("...and the banner reports the last hidden state without promising background reading", "show WhatsApp for drafts" in beta.banner_detail.cget("text") and "reading works" not in beta.banner_detail.cget("text"),
              (beta.banner_detail.cget("text"), getattr(beta, "error", None)))
    beta.connection = ("off", "Reader report expired")
    beta.refresh_banner()
    rig.check("offline banner does not claim hidden-tab reading is working", "not connected" in beta.banner_title.cget("text") and "tab not on screen" not in beta.banner_detail.cget("text") and "reading works" not in beta.banner_detail.cget("text"))
    beta.connection = ("on", "")
    beta.error = saved_error
    whatsapp.last_beat = shown_beat
    beta.update_self_test()
    beta.refresh_banner()

    saved_drafts = copy.deepcopy(beta.store.read().get("drafts", {}))
    beta.error = "An old cancelled attempt"
    beta.run_self_test("draft")
    rig.check("a new accepted self-test clears the stale error banner", not beta.error and "An old cancelled attempt" not in beta.banner_detail.cget("text"))
    rig.check("self-test waiting text explains the delay and uninterrupted visible WhatsApp requirement",
              all(words in beta.self_test_status.get("1.0", "end-1c") for words in ("30 seconds", "tab visible", "do not click or type")))
    pending = beta.store.read()["diagnostics"]["self_test"]
    rig.check("self-test queues fixed unsent text to exact allowed self only", requests[-1] == dict(id=requests[-1]["id"], kind="draft", chat="Lena Drayton", phone=SELF_PHONE, text=wa_ui.SELF_TEST_TEXT))
    rig.check("new self-test current record is pending, not old success", pending["status"] == "pending" and pending["completed_at"] is None and pending["request_id"] == requests[-1]["id"])
    before_requests = len(requests)
    beta.run_self_test("draft")
    rig.check("duplicate self-test clicks do not enqueue another request", len(requests) == before_requests)
    acknowledge(beta, "drafted")
    current = beta.store.read()["diagnostics"]["self_test"]
    rig.check("verified self-test saves terminal status and reader version", current["status"] == "drafted" and current["completed_at"] and current["reader_version"] == "synthetic-reader-1.3")
    rig.check("self-test never changes roster draft records", beta.store.read().get("drafts", {}) == saved_drafts)
    diagnostics = json.dumps({"current": current, "history": beta.store.read()["self_test_history"]})
    rig.check("diagnostic evidence has no preview text or number", SELF_PHONE not in diagnostics and wa_ui.SELF_TEST_TEXT not in diagnostics)
    rig.check("verified draft wording never claims a send", "press Send yourself" in beta.self_test_status.get("1.0", "end-1c"))
    first_attempt = current["attempt_id"]
    beta.run_self_test("open")
    current = beta.store.read()["diagnostics"]["self_test"]
    rig.check("a new request replaces the current success with a fresh pending attempt", current["status"] == "pending" and current["attempt_id"] != first_attempt)
    acknowledge(beta, "opened")
    rig.check("open-my-chat sends no text and preserves existing draft", requests[-1]["text"] is None and "was left alone" in beta.self_test_status.get("1.0", "end-1c"))

    beta.run_self_test("draft")
    cleanup_warning = "The draft was verified. The chat search was preserved; clear it manually."
    acknowledge(beta, "drafted", detail=cleanup_warning)
    rig.check("verified draft keeps its success and exposes a search-cleanup warning", beta.store.read()["diagnostics"]["self_test"]["status"] == "drafted" and cleanup_warning in beta.self_test_status.get("1.0", "end-1c"))
    beta.run_self_test("open")
    precise_reason = "Cancelled before the reader started. Nothing was changed in WhatsApp."
    acknowledge(beta, "cancelled", detail=precise_reason)
    rig.check("precise cancellation is not contradicted by a generic partial-action warning", precise_reason in beta.self_test_status.get("1.0", "end-1c") and "may already be open" not in beta.self_test_status.get("1.0", "end-1c"))

    config["draft_to"] = []
    before_requests = len(requests)
    beta.run_self_test("draft")
    rig.check("removing self allowlist blocks a previously enabled self-test", len(requests) == before_requests and beta.store.read()["diagnostics"]["self_test"]["status"] == "blocked")
    config["draft_to"] = ["Lena Drayton"]
    app.name = "Another Controller"
    beta.run_self_test("open")
    rig.check("stale self-test dialog cannot act for another controller", len(requests) == before_requests)
    app.name = "Lena Drayton"
    beta.error = ""
    beta.run_self_test("draft")
    acknowledge(beta, "existing_draft")
    rig.check("existing self draft is reported preserved without retry", len(requests) == before_requests + 1 and "preserved" in beta.self_test_status.get("1.0", "end-1c").lower())

    beta.run_self_test("draft")
    pending_id = beta.bridge_pending["id"]
    beta.toggle_drafts()
    rig.check("Drafts off revokes pending draft and records cancellation", not beta.drafts_on and beta.bridge_pending is None and pending_id in cancellations and beta.store.read()["diagnostics"]["self_test"]["status"] == "cancelled")
    rig.check("cancellation does not claim to erase WhatsApp text", "does not remove text" in beta.self_test_status.get("1.0", "end-1c"))
    before_requests = len(requests)
    beta.run_self_test("draft")
    rig.check("Drafts off cannot be bypassed by the self-test handler", len(requests) == before_requests)
    beta.run_self_test("open")
    rig.check("Drafts off still permits explicit read-only self-chat opening", len(requests) == before_requests + 1 and requests[-1]["text"] is None)
    beta.close_self_test()
    rig.check("closing self-test dialog cancels its pending open request", beta.bridge_pending is None and beta.self_test_window is None)
    beta.toggle_drafts()
    beta.show_self_test()
    beta.run_self_test("draft")
    cancel_result["status"] = "unknown"
    beta.close_self_test()
    rig.check("late cancellation records uncertainty rather than false cancellation", beta.store.read()["diagnostics"]["self_test"]["status"] == "unknown" and "Inspect" in beta.error)
    cancel_result["status"] = "cancelled"
    beta.error = ""

    # Real Save and picker callbacks must retain all verified fields when one atomic write fails.
    beta.show(first["key"])
    beta.save("contacts", first["name"], "353870000001")
    beta.save("chat_names", first["name"], "Fixture Guard Alpha Chat")
    beta.phone_var.set("+353870000002")
    beta.flush()
    original_write = beta.store.write
    def unavailable(*args, **kwargs):
        raise OSError("Synthetic contact write failure")
    before_state = beta.store.read()
    beta.store.write = unavailable
    try:
        beta.save_contact()
        rig.check("manual Save reports atomic failure and retains verified contact", beta.store.read() == before_state and "Could not save contact" in beta.error)
        rig.check("manual Save failure retains pending number for recovery", beta.phone_var.get() == "+353870000002")
    finally:
        beta.store.write = original_write
    beta.save_contact()
    rig.check("manual Save succeeds after storage recovery without promoting review", beta.phone_for(first["name"]) == "353870000002" and not beta.draft_record(first).get("reviewed"))
    beta.catalog_store().write({"candidates": [{"name": "Fixture Guard Alpha Verified", "phones": ["353870000003"]}]})
    beta.choose_contact()
    picker = beta.import_windows[-1]
    choices = next(widget for widget in descendants(picker) if isinstance(widget, ttk.Treeview))
    choices.selection_set(choices.get_children()[0])
    button = next(widget for widget in descendants(picker) if isinstance(widget, wa_ui.Button) and widget.text.startswith("I verified"))
    before_state = beta.store.read()
    beta.store.write = unavailable
    try:
        button.command()
        rig.check("picker Save failure cannot partially change alias or number", beta.store.read() == before_state and "Could not save contact" in beta.error)
    finally:
        beta.store.write = original_write
    button.command()
    rig.check("picker Save recovers with one consistent contact and alias", beta.phone_for(first["name"]) == "353870000003" and beta.chat_for(first["name"]) == "Fixture Guard Alpha Verified" and not beta.draft_record(first).get("reviewed"))
    picker.destroy()
    beta.error = ""
    beta.start_bridge("open", first)
    rig.check("guard-chat waiting text gives the same visible-tab and no-interaction guidance",
              all(words in beta.message.cget("text") for words in ("30 seconds", "tab visible", "do not click or type")))
    acknowledge(beta, "opened")

    # Reader status can be connected while preparation is blocked; long messages must wrap.
    for theme in ("light", "dark"):
        if app.skin.theme != theme:
            app.switch_theme()
        beta.show()
        rig.root.geometry("980x660+-2600+40")
        beta.connection = ("on", "")
        bridge.update(ready=False, reason="Reload the reader and WhatsApp tab to enable draft preparation")
        beta.error = "Earlier draft attempt stopped; check WhatsApp before trying again."
        beta.refresh_banner()
        rig.pump(150)
        rig.check(theme + " minimum layout keeps every banner action complete", all(full_visible(rig.root, button) for button in
                  (beta.drafts_button, beta.send_button, beta.sound_button, beta.self_test_button, beta.messages_button)))
        geometry = {name: (widget.winfo_rooty() - rig.root.winfo_rooty(), widget.winfo_height(), widget.winfo_reqheight())
                    for name, widget in (("banner", beta.banner), ("progress", beta.read_progress), ("detail", beta.banner_detail),
                                         ("workspace", beta.workspace), ("editor", beta.editor), ("action", beta.open_draft_button), ("message", beta.message))}
        print("MINIMUM_LAYOUT", theme, geometry, flush=True)
        rig.check(theme + " minimum layout keeps reviewed-draft action complete", full_visible(rig.root, beta.open_draft_button), geometry["action"])
        rig.check(theme + " banner title is fully visible beside actions", beta.banner_title.winfo_width() >= beta.banner_title.winfo_reqwidth())
    beta.error = ""
    bridge.update(ready=True, reason="")
    beta.show_self_test()
    beta.run_self_test("draft")
    before_requests = len(requests)
    acknowledge(beta, "unknown", detail="Inspect the composer and return to All chats manually.")
    rig.check("uncertain bridge recovery instructions are visible without retry", "return to All chats manually" in beta.error and len(requests) == before_requests)
    beta.self_test_words = ("The action result is uncertain. Inspect your WhatsApp composer; existing text may already be present. " * 6)
    beta.update_self_test()
    beta.self_test_window.geometry("440x390+-2600+40")
    rig.pump(100)
    rig.check("minimum self-test dialog keeps both actions fully visible", full_visible(beta.self_test_window, beta.self_test_open_button) and full_visible(beta.self_test_window, beta.self_test_draft_button))
    natural = (beta.self_test_status.winfo_width(), beta.self_test_status.winfo_height(), beta.self_test_status.yview())
    # Six sentences may fit completely at this font/DPI. Force overflow rather than
    # treating a correctly visible paragraph as a broken scrollbar.
    beta.self_test_words *= 8
    beta.update_self_test()
    rig.pump(70)
    overflow = (beta.self_test_status.winfo_width(), beta.self_test_status.winfo_height(), beta.self_test_status.yview())
    rig.check("long self-test status remains scrollable instead of clipping actions", beta.self_test_status.winfo_height() >= 25 and beta.self_test_status.yview()[1] < 1.0, (natural, overflow))
    beta.self_test_status.yview_moveto(1.0)
    rig.pump(30)
    rig.check("self-test result can scroll all the way to its end", beta.self_test_status.yview()[0] > 0 and beta.self_test_status.yview()[1] == 1.0, beta.self_test_status.yview())
    beta.run_self_test("open")
    rig.check("explicit application close cancels outstanding local work", beta.close() and beta.bridge_pending is None and beta.store.read()["diagnostics"]["self_test"]["status"] == "cancelled")
    rig.check("self-test history retains prior attempts", len(beta.store.read()["self_test_history"]) >= 8)
    state = quiet_state(rig.root)
    rig.check("workflow tests retain invisible nonactivating protection", state["alpha"] == 0 and state["click_through"] and state["no_activate"] and not state["foreground"])
    rig.check("no live roster actions or callback errors", not roster_writes and not rig.errors, rig.errors[:1])
    # Exercise the real delayed-cursor lifecycle; do not discard Tcl errors after destruction.
    aimed = []
    app.aim = lambda: aimed.append(True)
    app.schedule_aim()
    superseded = app.aim_job
    app.schedule_aim()
    pending = rig.root.tk.call("after", "info")
    rig.check("a replacement delayed cursor move cancels the old timer", superseded not in pending and app.aim_job in pending)
    rig.pump(200)
    rig.check("delayed cursor move runs once and clears its handle", aimed == [True] and app.aim_job is None)
    app.schedule_aim()
    closing_aim = app.aim_job
finally:
    results = rig.close()

rig.check("window close cancels the deferred cursor before destroying its Tcl command",
          app.aim_job is None and closing_aim not in rig.root.tk.call("after", "info")
          and aimed == [True] and not rig.root._ccl_tcl_errors and not rig.errors
          and app.gone and not rig.root.tk.call("info", "commands", str(rig.root)),
          (rig.root._ccl_tcl_errors, rig.errors))

print("%d of %d checks passed" % (sum(results), len(results)))
raise SystemExit(0 if all(results) else 1)
