"""Synthetic persistence and contact-integrity regressions; no Tk, network or private data."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wa_beta as beta
import wa_contacts as contacts
import wa_ui as ui

checks = []
A, B, C = "353870000001", "353870000002", "353870000003"
NAME, OTHER = "Alex Example", "Blair Other"
SCOPE, KEY = "synthetic-shift", "Alex Example|Example Hall|20:00-close"
RECORD = SCOPE + "|" + KEY


def check(label, value, detail=None):
    checks.append(bool(value))
    if not value:
        print("FAIL  %s %r" % (label, detail))


def refused(label, action, error=ValueError):
    try:
        action()
    except error:
        check(label, True)
    else:
        check(label, False, "unexpectedly accepted")


for text in ("a" * 4096, "👍" * 2048, "a" * 4094 + "👍", "Line one\nLine two"):
    check("valid browser-sized draft is unchanged", beta.validate_text(text) == text)
for text in ("a" * 4097, "👍" * 2049, "a" * 4095 + "👍", "", " \n\t", "Hi\0there", "\ud800", "\udfff", None):
    refused("unsupported or over-budget draft cannot be reviewed", lambda text=text: beta.validate_text(text))

for phone in ("tel:+353870000001%0A23", "tel:+353870000001%0D23", "+353870000001\t23",
              "+353870000001\u202823", "+353870000001\u008523"):
    refused("phone control separator cannot concatenate a new recipient", lambda phone=phone: contacts.normalize_phone(phone))
check("ordinary nonbreaking phone spacing remains supported", contacts.normalize_phone("+353\u00a087\u00a0000\u00a00001") == A)
bad_card = contacts.parse_vcards("BEGIN:VCARD\nFN:Alex Example\nTEL:tel:+353870000001%0A23\nEND:VCARD")
check("encoded newline telephone stays unresolved at import", bad_card["candidates"][0]["phones"] == []
      and contacts.match_candidates(bad_card["candidates"], [NAME])[0]["status"] == "needs_number_review")

with tempfile.TemporaryDirectory(prefix="ccl-beta-deep-") as directory:
    path = Path(directory) / "state.json"
    store = ui.Store(str(path))

    def seed(data):
        path.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
        return path.read_bytes()

    # Validation of all changes precedes the single replace, including a malformed late record.
    for record in ("legacy draft text", ["recoverable private value"], None, 7):
        before = seed({"drafts": {RECORD: record}, "contacts": {NAME: A}, "private_extra": {"keep": True}})
        refused("malformed individual draft is preserved", lambda: store.patch_many([
            ("contacts", NAME, B), ("drafts", RECORD, {"reviewed": None})]))
        check("late invalid record leaves every saved field byte-for-byte intact", path.read_bytes() == before)
    before = seed({"drafts": [], "contacts": {NAME: A}})
    refused("malformed section prevents contact transaction", lambda: store.contact(NAME, B, scope=SCOPE, key=KEY, replace=True))
    check("invalid section cannot leave a partial contact change", path.read_bytes() == before)

    for data in ([], None, "saved text"):
        before = seed(data)
        refused("malformed root is never replaced", lambda: store.patch("preferences", "sound", False))
        check("malformed root bytes survive attempted update", path.read_bytes() == before)
    path.write_text('{"drafts":', encoding="utf-8")
    before = path.read_bytes()
    refused("truncated JSON is never replaced", lambda: store.patch("preferences", "sound", False))
    check("truncated JSON bytes remain available for recovery", path.read_bytes() == before)

    original = {"contacts": {NAME: A}, "chat_names": {NAME: "Alex Chat"},
                "drafts": {RECORD: {"text": "Keep this draft", "reviewed": "old", "opened": "old", "extra": 9}},
                "private_extra": {"keep": True}}
    before = seed(original)
    with patch.object(ui.os, "replace", side_effect=OSError("synthetic replace failure")):
        refused("failed replace reports contact transaction failure",
                lambda: store.contact(NAME, B, chat="New Alex Chat", scope=SCOPE, key=KEY, replace=True), OSError)
    check("failed replace preserves original file and all contact fields", path.read_bytes() == before)
    check("failed replace leaves no partial temporary file", list(Path(directory).iterdir()) == [path])

    def partial_dump(data, stream, **kwargs):
        stream.write('{"incomplete":')
        raise OSError("synthetic disk full")

    with patch.object(ui.json, "dump", side_effect=partial_dump):
        refused("partial temporary write is surfaced", lambda: store.patch("preferences", "sound", False), OSError)
    check("partial temporary write cannot corrupt original data", path.read_bytes() == before)
    check("partial temporary write is cleaned up", list(Path(directory).iterdir()) == [path])

    with patch.object(store, "write", wraps=store.write) as writer:
        data = store.contact(NAME, B, chat="New Alex Chat", scope=SCOPE, key=KEY, replace=True)
    check("verified contact transaction uses one complete write", writer.call_count == 1)
    check("contact number and chat change together", data["contacts"][NAME] == B and data["chat_names"][NAME] == "New Alex Chat")
    check("contact transaction invalidates review while retaining draft text and unknown fields",
          data["drafts"][RECORD] == {"text": "Keep this draft", "reviewed": None, "opened": None, "extra": 9}
          and data["private_extra"] == original["private_extra"])
    check("contact transaction persists matching pending input", data["contact_inputs"][RECORD] == "+" + B)

    # Review dialogs can be open while another explicit action changes the saved state.
    for previous in (A, "invalid saved value", ["unresolved value"]):
        before = seed({"contacts": {NAME: previous}})
        refused("import never overwrites a newly existing target, even invalid", lambda: store.contact(NAME, B))
        check("refused import keeps existing target intact", path.read_bytes() == before)
    seed({"contacts": {}})
    store.contact(OTHER, B)
    before = path.read_bytes()
    for replacement in (False, True):
        refused("import/manual commit rechecks current shared-number conflicts",
                lambda replacement=replacement: store.contact(NAME, B, replace=replacement))
        check("shared-number refusal never adds or changes contacts", path.read_bytes() == before)

    for replacement in (False, True):
        before = seed({"contacts": {}, "chat_names": {OTHER: "Alex Chat"}})
        refused("different guard cannot acquire another saved chat name",
                lambda replacement=replacement: store.contact(NAME, A, chat="ALÉX CHAT", replace=replacement))
        check("chat-name conflict does not partially save the number", path.read_bytes() == before)
    before = seed({"contacts": {NAME: A}, "chat_names": {NAME: "Alex Chat", OTHER: "Alex Chat"}})
    refused("manual phone-only save also checks existing alias conflicts", lambda: store.contact(NAME, B, replace=True))
    check("phone-only conflict leaves saved state unchanged", path.read_bytes() == before)

    alternate = "  ＡＬＥＸ   ＥＸＡＭＰＬＥ  "
    seed({"contacts": {NAME: A, alternate: B}, "chat_names": {NAME: "Old Chat", alternate: "Older Chat"}, "unrelated": 5})
    data = store.contact(NAME, C, chat="Verified Alex Chat", replace=True)
    check("explicit replacement resolves all equivalent full-name spellings without deleting keys",
          data["contacts"] == {NAME: C, alternate: C} and data["chat_names"] == {NAME: "Verified Alex Chat", alternate: "Verified Alex Chat"}
          and data["unrelated"] == 5)
    view = ui.BetaUI.__new__(ui.BetaUI)  # exercise the lookup methods without constructing Tk
    view.data = data
    check("UI phone and chat lookup share the transaction's Unicode identity",
          view.phone_for(" alex example ") == C and view.chat_for(" alex example ") == "Verified Alex Chat")

    before = seed({"contacts": {NAME: A}, "drafts": {RECORD: ["recoverable invalid record"]}})
    refused("atomic picker transaction refuses malformed draft before any contact or alias write",
            lambda: store.contact(NAME, B, chat="Alex Chat", scope=SCOPE, key=KEY, replace=True))
    check("malformed draft preserves complete contact transaction state", path.read_bytes() == before)
    for chat in ("👍" * 61, "bad\0chat", "\ud800"):
        refused("invalid chat names cannot partially change contact data",
                lambda chat=chat: store.contact(NAME, B, chat=chat, replace=True))
        check("invalid chat leaves original file unchanged", path.read_bytes() == before)
    seed({})
    check("chat at browser's exact emoji length budget can be saved",
          store.contact(NAME, A, chat="👍" * 60)["chat_names"][NAME] == "👍" * 60)

catalog = [{"name": NAME, "phones": None}, {"name": NAME, "phones": A}, None,
           {"name": NAME, "phones": [A, "bad"]}, {"name": NAME, "phones": ["+353 87 000 0001"]},
           {"name": OTHER, "phones": [B]}]
before = copy.deepcopy(catalog)
choices, invalid = ui.catalog_choices(catalog)
check("one malformed catalog entry cannot hide other usable contacts", choices == [(NAME, A), (OTHER, B)] and invalid == 4, (choices, invalid))
check("catalog validation preserves original malformed and unknown data", catalog == before)
check("catalog filtering still narrows valid choices", ui.catalog_choices(catalog, "alex")[0] == [(NAME, A)])

# A repeated local hour must not end a live overnight shift early, and spring's skipped hour must not extend its endpoint.
for day, next_day in (("2026-10-24", dt.datetime(2026, 10, 25, 1, 30)), ("2026-03-28", dt.datetime(2026, 3, 29, 2, 30))):
    log = {"meta": {"supervisor": "Lena Drayton", "date_iso": day, "start_min": 1080, "end_min": 1800}}
    check("DST overnight remains eligible on local control-room clock", beta.actionable_shift(log, next_day))
    if day == "2026-10-24":
        check("both occurrences of the repeated local hour remain in the same shift", beta.actionable_shift(log, next_day.replace(fold=1)))
    check("DST overnight endpoint is exclusive", not beta.actionable_shift(log, next_day.replace(hour=6, minute=0)))

print("%d of %d checks passed" % (sum(checks), len(checks)))
raise SystemExit(0 if all(checks) else 1)
