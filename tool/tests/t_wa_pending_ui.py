"""Pure pending-action lifecycle tests: real controllers/queue, fake widgets/store, no Tk or I/O."""
import copy
import datetime as dt
from pathlib import Path
import sys
from types import SimpleNamespace

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wa_beta
import wa_bridge
import wa_ui

checks = []
NOW = dt.datetime(2026, 11, 8, 20)
NAME, OTHER = "Alex Example", "Blair Example"
PHONE, OTHER_PHONE = "353870000001", "353870000002"


def check(label, value):
    checks.append(bool(value))
    if not value:
        print("FAIL", label)


class Store:
    def __init__(self, data):
        self.data, self.error = copy.deepcopy(data), None

    def read(self):
        if self.error:
            raise self.error
        return copy.deepcopy(self.data)

    def patch_many(self, changes):
        data = self.read()
        for section, key, value in changes:
            wa_ui.Store.merge(data, section, key, value)
        self.data = copy.deepcopy(data)
        return data

    def contact(self, name, phone, **kwargs):
        data = self.read()
        data["contacts"][name] = phone
        if kwargs.get("chat") is not None:
            data["chat_names"][name] = kwargs["chat"]
        self.data = copy.deepcopy(data)
        return data


class Widget:
    def __init__(self, text=""):
        self.text, self.modified, self.config = text, False, {}

    def get(self, *args):
        return self.text

    def edit_modified(self, value=None):
        if value is not None:
            self.modified = value
        return self.modified

    def winfo_exists(self):
        return True

    def configure(self, **values):
        self.config.update(values)


def harness(kind="draft"):
    b = wa_ui.BetaUI.__new__(wa_ui.BetaUI)
    calls = [dict(name=name, site="Example Hall", venue="Example Hall", shift="20:00-close",
                  start=1200, response="", call_text="19:00") for name in (NAME, OTHER)]
    log = dict(stem="2026-11-08 synthetic", meta=dict(date_iso="2026-11-08", supervisor="Lena Drayton",
               start_min=1020, end_min=1800), calls=calls)
    b.app = SimpleNamespace(name="Lena Drayton", pack={"calls":log}, gone=False, skin={"ink2":"black","amber":"orange"})
    b.clock = lambda:NOW
    b.scope = log["stem"]
    b.rows = wa_beta.draft_rows(log)
    row = b.rows[0]
    b.selected = row["key"]
    b.data = dict(contacts={NAME:PHONE, OTHER:OTHER_PHONE}, chat_names={NAME:"Alex Saved",OTHER:"Blair Saved"}, drafts={})
    record = b.record_id(b.scope,row["key"])
    b.data["drafts"][record] = {"text":row["text"]}
    b.data["drafts"][record]["reviewed"] = b.signature(row)
    b.store = Store(b.data)
    b.editor, b.phone_var, b.message = Widget(row["text"]), Widget("+"+PHONE), Widget()
    b.edit_scope, b.edit_key, b.edit_context = b.scope, row["key"], b.context(row)
    b.pending_text, b.pending_phone = None, ""
    b.dirty, b.loading = False, False
    b.contact_dirty, b.invalidated, b.contact_inputs = set(), set(), {}
    b.sending_on = b.drafts_on = True
    b.bridge_pending = b.bridge_job = b.save_job = None
    b.error, b.notices, b.cancelled, b.scheduled = "", [], [], []
    b.root = SimpleNamespace(after=lambda *args:(b.scheduled.append(args) or "job"), after_cancel=lambda job:None)
    b.refresh_banner = b.show_selected = b.update_self_test = lambda:None
    b.problem = lambda text:(setattr(b,"error",text),b.notices.append(text))
    b.inform = b.notices.append
    b.read_coordinator = SimpleNamespace(status=lambda:{"enabled":False})
    b.save_self_result = lambda *args:True
    b.q = wa_bridge.ActionQueue(lambda:NOW.timestamp())
    b.client = dict(tab=3,document="synthetic-document",owner="3:synthetic-document",capability=wa_bridge.CAPABILITY)
    b.q.touch(b.client,dict(at=NOW.timestamp()*1000,pane=True,rows=2,paused=False,useHere=False,visible="visible"))
    b.bridge_state = b.q.readiness
    b.enqueue = lambda kind,chat,text=None:b.q.enqueue(kind,chat,text=text,phone=b.phone_for(b.selected_row()["name"]))
    b.action_status = b.q.status
    b.cancel_action = lambda key:(b.cancelled.append(key) or b.q.cancel(key))
    text = row["text"] if kind in ("draft","send") else None
    b.start_bridge(kind,row,text=text,signature=b.signature(row) if text is not None else None)
    return b


def grant(b, step):
    pending = b.bridge_pending
    return b.q.authorize(dict(id=pending["id"],owner=b.client["owner"],step=step))


def mutate_record(b, **changes):
    key = b.record_id(b.scope,b.rows[0]["key"])
    b.store.data["drafts"][key].update(changes)


b = harness()
original = copy.deepcopy(b.bridge_pending)
check("request retains exact reviewed phone, text and assignment context",
      original.get("phone") == PHONE and original.get("text") == "All okay for Example Hall?"
      and original.get("context") == b.context(b.rows[0]) and original.get("binding") == original["signature"])
b.check_bridge()
check("unchanged saved review keeps its queued action and polling", not b.cancelled and b.bridge_pending == original and b.bridge_job)

for error in (PermissionError("synthetic locked data"),ValueError("synthetic malformed data")):
    b = harness("send"); key = b.bridge_pending["id"]
    b.store.error = error
    b.check_bridge()
    check("failed fresh data read revokes a queued guard request", b.cancelled == [key] and b.bridge_pending is None and b.q.status(key)["status"] == "cancelled")
    check("failed read preserves cached contacts and typed text", b.data["contacts"][NAME] == PHONE and b.editor.text == "All okay for Example Hall?")

for field,value in (("phone","353870000003"),("chat","Different saved chat"),("text","Changed message"),("review",None),
                    ("site","Different Hall"),("start",1260),("end",1740),("removed",True),("controller","Another Controller"),
                    ("scope","different shift"),("disabled",True)):
    b = harness("send"); key = b.bridge_pending["id"]
    log = b.app.pack["calls"]
    if field == "phone": b.store.data["contacts"][NAME] = value
    elif field == "chat": b.store.data["chat_names"][NAME] = value
    elif field == "text": mutate_record(b,text=value)
    elif field == "review": mutate_record(b,reviewed=value)
    elif field == "site": log["calls"][0].update(site=value,venue=value)
    elif field == "start": log["calls"][0]["start"] = value
    elif field == "end": log["meta"]["end_min"] = value
    elif field == "removed": log["calls"].pop(0)
    elif field == "controller": b.app.name = value
    elif field == "scope": log["stem"] = value
    elif field == "disabled": b.sending_on = False
    b.check_bridge()
    check("ongoing request revalidates fresh " + field, b.cancelled == [key] and b.bridge_pending is None)

b = harness("open"); key = b.bridge_pending["id"]
b.store.data["contacts"][NAME] = "353870000003"
b.reload_data()
check("an open-only action is also revoked when its verified phone changes", b.cancelled == [key] and b.bridge_pending is None)

b = harness(); key = b.bridge_pending["id"]
b.store.data["contacts"][OTHER] = "353870000004"
b.check_bridge()
check("another person's independent contact change does not revoke this review", not b.cancelled and b.bridge_pending["id"] == key)
b.app.pack["calls"]["calls"][0]["heard"] = {"response":"Ok"}
b.check_bridge()
check("ordinary newly read confirmation does not invalidate draft context", not b.cancelled and b.bridge_pending["id"] == key)

for callback in ("text_changed","phone_changed","flush"):
    b = harness("send"); key = b.bridge_pending["id"]
    if callback in ("text_changed","flush"):
        b.editor.text, b.editor.modified = "New unreviewed text", True
    else:
        b.phone_var.text = "+353870000003"
    getattr(b,callback)()
    check("bound input immediately revokes through " + callback, b.cancelled == [key] and b.bridge_pending is None)

b = harness(); key = b.bridge_pending["id"]
b.selected = b.rows[1]["key"]
b.edit_key, b.edit_context = b.selected, b.context(b.rows[1])
b.editor.text, b.editor.modified = "Other guard's local edit", True
b.text_changed()
b.phone_var.text = "+353870000004"
b.phone_changed()
b.check_bridge()
check("editing another selected row leaves the original bound request alone", not b.cancelled and b.bridge_pending["id"] == key)

b = harness("send"); key = b.bridge_pending["id"]
b.q.lease(b.client); grant(b,"open"); grant(b,"send")
b.store.data["contacts"][NAME] = "353870000003"
b.check_bridge()
check("late invalidation revokes grants but honestly keeps may-already-sent uncertainty",
      b.q.status(key)["status"] == "unknown" and b.bridge_pending is None
      and any("may already have been sent" in text for text in b.notices))
check("late invalidation cannot obtain another send grant", b.q.authorize(dict(id=key,owner=b.client["owner"],step="send")) is None)

b = harness("send"); key = b.bridge_pending["id"]
b.cancel_action = lambda key:(_ for _ in ()).throw(OSError("synthetic cancellation failure"))
b.editor.text, b.editor.modified = "Changed after request", True
b.text_changed()
check("failed cancellation does not promise that a send was prevented",
      b.bridge_pending is None and any("may already have been sent" in text and "Nothing will be retried" in text for text in b.notices))

b = harness("send"); key = b.bridge_pending["id"]
b.q.lease(b.client); grant(b,"open"); grant(b,"send")
check("synthetic verified send ACK is accepted", b.q.acknowledge(dict(id=key,owner=b.client["owner"],status="sent")))
b.store.data["contacts"][NAME] = "353870000003"
b.reload_data()
check("already verified completion remains sent rather than falsely cancelled", b.q.status(key)["status"] == "sent" and any("sent at" in text for text in b.notices))
record = b.store.data["drafts"][b.record_id(b.scope,b.rows[0]["key"])]
check("completion records the original reviewed signature, not the changed contact", record.get("sent") != b.signature(b.rows[0]))

b = harness("open"); b.bridge_pending["self_test"] = True
key = b.bridge_pending["id"]
b.store.error = ValueError("synthetic malformed beta file")
b.reload_data()
b.editor.modified = True; b.text_changed()
check("guard data or input changes do not cancel an independent self-test", not b.cancelled and b.bridge_pending["id"] == key)

b = harness(); key = b.bridge_pending["id"]
b.commit_contact(NAME,"353870000003")
check("saving a changed bound contact immediately revokes the action", b.cancelled == [key] and b.bridge_pending is None)

print("%d of %d checks passed" % (sum(checks),len(checks)))
raise SystemExit(0 if all(checks) else 1)
