"""Invisible automatic-read UI integration with fake bridge actions and scratch data only."""
import copy
import os
import sys

from drive6 import Rig, HOME, WORK
from drive import quiet_state

sys.path.insert(0, os.path.join(HOME, "tool"))
import rostercolour
import wa_read
import wa_ui
import whatsapp

STATE = os.path.join(WORK, "read-ui-private.json")
os.environ["CCL_BETA_STATE"] = STATE
if os.path.exists(STATE):
    os.remove(STATE)
FIXTURE = os.path.join(HOME, "tool", "tests", "roster-2026-10-19.xlsx")
DAY = "2026-10-23"
requests, replies, cancellations, roster_writes = [], {}, [], []
clock = [100.0]
ready = {"ready": True, "owner": "3:synthetic-document-1", "busy": False, "capability": "drafts-v4"}
configuration = {"controllers": ["Lena Drayton"], "trusted": [], "draft_to": [], "phones": {}}
whatsapp.settings = lambda: copy.deepcopy(configuration)
whatsapp.reader_state = lambda now=None: ("on", "")
whatsapp.last_beat = lambda: (0, {"version": "synthetic-reader", "visible": "visible"})
whatsapp.events_between = lambda *args: []
whatsapp.load_aliases = lambda controller=None: {}
whatsapp._read_json = lambda path, default: copy.deepcopy(default)
rostercolour.send = lambda *args, **kwargs: roster_writes.append(True) or {"ok": False}
wa_ui.BetaUI.bridge_state = lambda self: dict(ready)


def enqueue(kind, chat, **fields):
    key = "synthetic-read-%d" % len(requests)
    requests.append(dict(id=key, kind=kind, chat=chat, **fields))
    replies[key] = {"id": key, "status": "queued"}
    return key


def cancel(key):
    cancellations.append(key)
    replies[key] = {"id": key, "status": "cancelled", "detail": "Synthetic cancellation"}
    return dict(replies[key])


wa_ui.BetaUI.enqueue_read = lambda self, kind, chat, **fields: enqueue(kind, chat, **fields)
wa_ui.BetaUI.enqueue = lambda self, kind, chat, text=None: enqueue(kind, chat, text=text)
wa_ui.BetaUI.action_status = lambda self, key: dict(replies[key])
wa_ui.BetaUI.cancel_action = lambda self, key: cancel(key)
whatsapp.cancel_actions = lambda: [cancel(key) for key, value in list(replies.items()) if value["status"] == "queued"]


def tick(beta):
    if beta.read_job:
        beta.root.after_cancel(beta.read_job)
        beta.read_job = None
    beta.read_tick()


def read_reply(request, *, complete=True):
    return {"id": request["id"], "status": "read", "reading": {"messages": 3, "incoming": 2, "outgoing": 1,
            "complete": complete, "oldest": request["since"] - 60000 if complete else request["since"] + 1000,
            "newest": request["since"] + 60000, "reason": "since_reached" if complete else "limit"}}


def fits(outer, inner):
    x, y = inner.winfo_rootx(), inner.winfo_rooty()
    return (inner.winfo_ismapped() and inner.winfo_width() >= inner.winfo_reqwidth()
            and inner.winfo_height() >= inner.winfo_reqheight()
            and outer.winfo_rootx() <= x and outer.winfo_rooty() <= y
            and x + inner.winfo_width() <= outer.winfo_rootx() + outer.winfo_width()
            and y + inner.winfo_height() <= outer.winfo_rooty() + outer.winfo_height())


rig = Rig("wa-read-ui", now=DAY + " 21:40", roster=FIXTURE,
          settings={"auto_green_for": [], "preview": False, "chooser": False})
try:
    app, beta = rig.app, rig.app.beta
    whatsapp.set_replies(True)                  # (20 Nov: these checks are about WhatsApp mode - as with Auto read on)
    app.auto_run = lambda: None
    if app.tick_job:
        rig.root.after_cancel(app.tick_job)
        app.tick_job = None
    rig.show("Lena Drayton", DAY)
    template = copy.deepcopy(rig.calls()[0])
    calls = []
    for index, name in enumerate(("Read Guard Alpha", "Read Guard Alpha", "Read Guard Bravo"), 1):
        call = copy.deepcopy(template)
        call.update(name=name, site="Example Hall %d" % index, venue="Example Hall %d" % index,
                    shift="22:00-close", start=1320, call=1200, call_text="20:00", due="20:00",
                    response="Ok", default_response="Ok", no=index, ahead=False, edited=False,
                    timed=False, heard={}, strike=False)
        calls.append(call)
    log = app.pack["calls"]
    log["calls"], log["notes"] = calls, []
    log["meta"].update(supervisor="Lena Drayton", date_iso=DAY, start_min=990, end_min=1800,
                       total=3, pending=0, early=0, ahead=False)
    beta.store.write({"contacts": {"Read Guard Alpha": "353870000001"},
                      "chat_names": {"Read Guard Alpha": "Read Alpha Verified"},
                      "preferences": {"drafts": False, "sound": False},
                      "drafts": {"unrelated": {"text": "Keep this existing private preview"}}})
    beta.read_coordinator = wa_read.ReadCoordinator(
        lambda *args, **kwargs: beta.enqueue_read(*args, **kwargs),
        lambda key: beta.action_status(key), lambda key: beta.cancel_action(key), clock=lambda: clock[0],
        wall_clock=lambda: beta.now().timestamp() + clock[0] - 100)
    app.refresh()
    baseline = beta.store.read()
    rig.check("read controls are on the main log without opening Messages", not beta.opened and beta.auto_read_button.winfo_manager() == "pack")
    rig.check("startup never starts browsing or persists automatic reading", not requests and not beta.read_coordinator.status()["enabled"] and beta.auto_read_button.text == "Auto read: off")
    beta.read_now_button.command()
    rig.check("one pass uses only the saved exact alias and canonical phone", len(requests) == 1 and requests[0]["kind"] == "read" and requests[0]["chat"] == "Read Alpha Verified" and requests[0]["phone"] == "353870000001" and "text" not in requests[0])
    rig.check("duplicate person is visited once and missing contact is listed", beta.read_coordinator.status()["total"] == 1 and any(item["name"] == "Read Guard Bravo" for item in beta.read_coordinator.status()["issues"]))
    rig.check("running full check exposes an immediate Stop control", beta.read_now_button.text == "Stop check")
    replies[requests[-1]["id"]] = read_reply(requests[-1])
    tick(beta)
    tick(beta)
    rig.check("completed pass reports visits separately from confirmation", beta.read_coordinator.status()["read"] == 1 and "replies confirmed 0/3" in beta.read_progress.cget("text") and "Chats visited 1/1" in beta.read_progress.cget("text"))
    rig.check("one pass never silently becomes periodic", not beta.read_coordinator.status()["enabled"] and beta.read_job is None)
    beta.show_read_details()
    rig.pump(50)
    rig.check("missing contact details are readable and contain no phone", "Read Guard Bravo" in beta.read_detail.get("1.0", "end") and "353870000001" not in beta.read_detail.get("1.0", "end"))
    rig.check("reading help explains both modes, reaction limits and actual reply clocks",
              all(words in beta.read_detail.get("1.0", "end") for words in ("AUTO READ", "FULL CHECK", "LAST CHECKS", "15 minutes", "verified guard reply")))
    beta.read_detail.tag_add("sel", "1.0", "1.4")
    beta.refresh_read_status()
    rig.check("unchanged reading updates preserve text selected for copying", bool(beta.read_detail.tag_ranges("sel")))
    beta.close_read_details()
    calls[0]["heard"] = {"response": "Ok", "how": "reply", "said": "Yes", "at": "21:00"}
    beta.refresh_read_status()
    rig.check("only actual reply evidence increases confirmed count", "replies confirmed 1/3" in beta.read_progress.cget("text"))
    already_checked = len(requests)
    beta.auto_read_button.command()
    rig.check("explicit Auto on starts watching with Full check still available", beta.read_coordinator.status()["continuous"] and beta.auto_read_button.text == "Auto read: on" and beta.read_now_button.text == "Full check")
    rig.check("Auto reuses the full check just completed instead of immediately rereading it", len(requests) == already_checked)
    before_full = len(requests)
    beta.read_now_button.command()
    rig.check("Full check while Auto is on forces a read and keeps automatic monitoring enabled", len(requests) == before_full + 1 and beta.read_coordinator.status()["continuous"])
    replies[requests[-1]["id"]] = read_reply(requests[-1], complete=False)
    tick(beta)
    tick(beta)
    rig.check("partial history is shown as needing attention", any("limit" in item["reason"].lower() or "partial" in item["reason"].lower() for item in beta.read_coordinator.status()["issues"]))
    rig.check("continuous pass waits between rounds instead of immediate navigation", beta.read_coordinator.status()["state"] == "waiting")
    count = len(requests)
    clock[0] += 121
    tick(beta)
    rig.check("a partial result does not restart a full pass every two minutes", len(requests) == count)
    clock[0] += 1100
    tick(beta)
    rig.check("an unresolved active call is rechecked after its longer retry interval", len(requests) == count + 1)
    pending = requests[-1]["id"]
    beta.auto_read_button.command()
    rig.check("Auto off cancels the outstanding read and its Tk timer", pending in cancellations and not beta.read_coordinator.status()["enabled"] and beta.read_job is None)
    rig.check("Auto off turns the WhatsApp replies off: the log is plain again (20 Nov)",
              not whatsapp.replies_on() and app.pack["calls"] is not log)
    whatsapp.set_replies(True)                 # (the rest of these checks drive the hand-made shift again)
    app.pack["calls"] = log
    app.refresh()
    count = len(requests)
    clock[0] += 121
    tick(beta)
    rig.check("stopped reading never auto-resumes", len(requests) == count)
    beta.start_read(continuous=True, force_full=True)
    pending = requests[-1]["id"]
    beta.selected = beta.rows[0]["key"]
    beta.start_bridge("open", beta.rows[0])
    rig.check("manual chat opening cancels automatic reading first", pending in cancellations and not beta.read_coordinator.status()["enabled"] and requests[-1]["kind"] == "open")
    beta.cancel_pending()
    beta.start_read(continuous=True, force_full=True)
    ready["owner"] = "3:synthetic-document-2"
    tick(beta)
    rig.check("reader document replacement pauses without resuming", not beta.read_coordinator.status()["enabled"] and beta.read_job is None)
    ready["owner"] = "3:synthetic-document-1"
    ready.update(ready=False, reason="Show WhatsApp on screen before reading.")
    count = len(requests)
    beta.start_read(continuous=True, force_full=True)
    rig.check("known hidden or unavailable page does not start a new read", len(requests) == count and not beta.read_coordinator.status()["enabled"] and "Show WhatsApp" in beta.read_progress.cget("text"))
    ready.update(ready=True, reason="")
    beta.start_read(continuous=True, force_full=True)
    previous_scope = log["stem"]
    log["stem"] = "synthetic-other-shift"
    beta.refresh()
    rig.check("changing the shown shift pauses its reading queue", not beta.read_coordinator.status()["enabled"] and beta.read_job is None)
    log["stem"] = previous_scope
    beta.refresh()
    rig.check("automatic reading never modifies contacts drafts or preferences", beta.store.read() == baseline)
    for theme in ("light", "dark"):
        if app.skin.theme != theme:
            app.switch_theme()
        rig.root.geometry("980x660+-2600+40")
        beta.refresh_banner()
        rig.pump(120)
        controls = (beta.auto_read_button, beta.read_now_button, beta.read_details_button,
                    beta.drafts_button, beta.send_button, beta.sound_button, beta.self_test_button, beta.messages_button)
        rig.check(theme + " minimum log layout keeps all read and draft controls visible", all(fits(rig.root, button) for button in controls))
    beta.start_read(continuous=True, force_full=True)
    pending = requests[-1]["id"]
    beta.close()
    rig.check("application close cancels the read and all repeat timers", pending in cancellations and beta.read_job is None and not beta.read_coordinator.status()["enabled"])
    quiet = quiet_state(rig.root)
    rig.check("read UI checks remained invisible and never activated", quiet["alpha"] == 0 and quiet["click_through"] and quiet["no_activate"] and not quiet["foreground"])
    rig.check("no roster writes or Python callback errors", not roster_writes and not rig.errors, rig.errors[:1])
finally:
    results = rig.close()

print("%d of %d checks passed" % (sum(results), len(results)))
raise SystemExit(0 if all(results) else 1)
