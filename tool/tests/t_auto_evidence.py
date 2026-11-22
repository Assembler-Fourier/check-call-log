"""Pure checks of automatic-green evidence and initial reader status; no Tk, files or external services.

Compile the real App methods from their AST and give them synthetic collaborators. Neither the app nor its
window is instantiated, and every configuration/edit loader used by the methods is replaced in this process.
"""
import ast
import sys
import time
import types
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import calllog
import rostercolour
import whatsapp

tree = ast.parse((TOOL / "app.pyw").read_text(encoding="utf-8"))
app = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "App")
methods = [node for node in app.body if isinstance(node, ast.FunctionDef) and node.name in ("auto_due", "wa_watch", "commit_call")]
scope = dict(calllog=calllog, rostercolour=rostercolour, whatsapp=whatsapp, time=time, WA_EVERY=15)
exec(compile(ast.Module(body=methods, type_ignores=[]), "real-App-evidence-methods", "exec"), scope)

state = dict(configured=True, edits={}, trusted=set(), reader="off")
whatsapp.is_for = lambda name: state["configured"]
whatsapp.trusted_names = lambda: state["trusted"]
whatsapp.live = lambda now=None: state["reader"] == "on"
whatsapp.set_replies(True)                     # (20 Nov: replies count only with Auto read on - these checks are about that mode)
calllog.load_edits = lambda stem: state["edits"]
BASE = dict(name="Test Guard", site="Example Hall", venue="Example Hall", start=1200, shift="20:00-close",
            ref="A4", fill=rostercolour.WHITE, response="Ok", call_text="19:00", ahead=False, strike=False,
            heard={}, default_response="Ok", edited=False)
results = []


def check(label, actual, expected):
    okay = actual == expected
    results.append(okay)
    if not okay:
        print("FAIL %s: %r != %r" % (label, actual, expected))


def due(call=None, edit=None, configured=True, trusted=False, reader="off", hand=None):
    call = dict(BASE, **(call or {}))
    key = calllog.edit_key(call)
    state.update(configured=configured, edits={key: edit} if edit is not None else {},
                 trusted={whatsapp.fold(call["name"])} if trusted else set(), reader=reader)
    log = dict(stem="synthetic", calls=[call], meta=dict(supervisor="Test Controller"))
    fake = types.SimpleNamespace(auto_logs=lambda: [log], decisions=lambda stem: hand or {}, sending=set(),
                                 roster_colour=lambda c: c["fill"], tried={}, tried_target={})
    return [action for _, action, _, _ in scope["auto_due"](fake)]


check("never-connected reader: generated Ok is not confirmation", due(), [])
check("connected reader: generated pre-reader Ok is still not confirmation", due(reader="on"), [])
check("time-only correction does not confirm", due(call={"edited": True}, edit={"time": "19:05"}), [])
check("other hand correction does not confirm", due(call={"edited": True}, edit={"name": "Test Guard"}), [])
check("saved explicit Ok confirms while reader is off", due(edit={"response": "Ok"}), ["green"])
check("stored manual Ok respects the response normalization", due(edit={"response": " ok "}), ["green"])
check("saved assumed/default response has no provenance", due(edit={"response": None}), [])
check("arbitrary manual text cannot stand in for Ok", due(edit={"response": "Confirmed by phone"}), [])
check("same-shift recorded WhatsApp yes remains evidence offline",
      due(call={"heard": {"response": "Ok", "how": "reply"}}), ["green"])
check("asked but unanswered does not confirm", due(call={"heard": {"response": "", "how": "asked"}}), [])
check("uncertain reply does not confirm", due(call={"heard": {"response": "", "how": "check"}}), [])
check("trusted no-message policy survives offline", due(trusted=True), ["green"])
check("trusted name with contrary evidence is not assumed confirmed",
      due(call={"heard": {"response": "", "how": "check"}}, trusted=True), [])
check("explicit manual Ok can override an uncertain reader response",
      due(call={"heard": {"response": "", "how": "check"}}, edit={"response": "Ok"}), ["green"])
check("unconfigured controller retains legacy automatic behavior", due(configured=False), ["green"])
check("fill-ahead cannot turn green even with evidence",
      due(call={"ahead": True}, edit={"response": "Ok"}), [])
check("struck-through name remains protected", due(call={"strike": True}, edit={"response": "Ok"}), [])
check("future call remains protected", due(call={"call_text": ""}, edit={"response": "Ok"}), [])
check("explicit negative response still turns green back to white",
      due(call={"response": "No answer", "fill": rostercolour.GREEN}), ["white"])
check("WhatsApp withdrawal still turns green back to white",
      due(call={"response": "", "fill": rostercolour.GREEN, "heard": {"taken_back": True}}), ["white"])
check("uncertain sender cannot create an automatic confirmation",
      due(call={"heard": {"response": "", "how": "check", "direction_unknown": True}}), [])
check("new uncertain context revokes an earlier automatic confirmation",
      due(call={"response": "", "fill": rostercolour.GREEN,
                "heard": {"response": "", "direction_unknown": True, "taken_back": True}}), ["white"])
check("manual Green-box decision is preserved when reader confidence is revoked",
      due(call={"response": "", "fill": rostercolour.GREEN,
                "heard": {"response": "", "direction_unknown": True, "taken_back": True}},
          hand={calllog.edit_key(BASE): "green"}), [])
check("blank waiting response alone does not repaint existing green",
      due(call={"response": "", "fill": rostercolour.GREEN}), [])
check("manual Green-box decision remains authoritative",
      due(edit={"response": "Ok"}, hand={calllog.edit_key(BASE): "white"}), [])

# First off state is shown once; an unchanged offline state must not repeatedly replace the user's status.
messages = []
whatsapp.settings = lambda: {"controllers": ["Test Controller"]}
whatsapp.link = lambda: {"port": 1}
whatsapp.reader_state = lambda: (state["reader"], "no word from the add-on")
whatsapp.last_beat = lambda: (0, {})
fake = types.SimpleNamespace(wa_purged=time.time(), wa_server=object(), wa_said="", wa_live=None, wa_says=lambda: True,
                             wa_logs=lambda: [], gone=False, wa_watch=lambda: None,
                             say=lambda text, *args: messages.append(text),
                             root=types.SimpleNamespace(after=lambda delay, callback: "scheduled"))
state["reader"] = "off"
scope["wa_watch"](fake)
check("initial no-heartbeat state is explained", "WhatsApp reader not connected" in messages[-1], True)
check("initial status is emitted once", len(messages), 1)
scope["wa_watch"](fake)
check("unchanged offline state does not spam", len(messages), 1)
state["reader"] = "on"
scope["wa_watch"](fake)
check("connection transition is shown", "WhatsApp reader connected" in messages[-1], True)
state["reader"] = "off"
scope["wa_watch"](fake)
check("later lost connection still has an off status", "WhatsApp reader off since" in messages[-1], True)
messages.clear()
fake.wa_live, fake.wa_said = None, ""
whatsapp.link = lambda: None
scope["wa_watch"](fake)
check("first missing-setup diagnostic is not replaced by generic offline text",
      len(messages) == 1 and "not set up" in messages[0], True)

# An explicit Ok must be able to create its provenance, even when the generated label already says Ok.
row = dict(BASE, no=1)
key = calllog.edit_key(row)
log = dict(stem="synthetic", calls=[row], meta=dict(supervisor="Test Controller"))
state.update(configured=True, edits={})
changes, responses = [], []


def save_edit(log, call, field, value):
    entry = state["edits"].setdefault(calllog.edit_key(call), {})
    if value is None:
        entry.pop(field, None)
    else:
        entry[field] = value
    if not entry:
        state["edits"].pop(calllog.edit_key(call), None)


def change(what, action):
    changes.append(what)
    action()


calllog.save_edit = save_edit
fake = types.SimpleNamespace(pack={"calls": log}, call_for=lambda key: row, change=change, change_error=None,
                             response_changed=lambda call: responses.append(call["name"]))
check("typing generated Ok succeeds", scope["commit_call"](fake, key, "resp", "Ok"), None)
check("typing generated Ok persists explicit evidence", state["edits"].get(key), {"response": "Ok"})
check("manual confirmation uses the normal history action", len(changes), 1)
check("manual confirmation triggers response handling", len(responses), 1)
scope["commit_call"](fake, key, "resp", "OK")
check("repeating an existing manual Ok is idempotent", len(changes), 1)
scope["commit_call"](fake, key, "resp", "")
check("clearing removes explicit evidence despite unchanged generated label", state["edits"], {})
check("clearing confirmation also uses normal history", len(changes), 2)
state["configured"] = False
scope["commit_call"](fake, key, "resp", "Ok")
check("legacy unconfigured Ok remains a no-op", state["edits"], {})
check("legacy no-op adds no history entry", len(changes), 2)

print("%d of %d checks passed" % (sum(results), len(results)))
raise SystemExit(0 if all(results) else 1)
