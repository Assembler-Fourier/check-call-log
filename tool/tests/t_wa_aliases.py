"""Exact contact identity across Store, alias loading and roster matching. No Tk, files or network."""
import copy
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wa_ui
import whatsapp as wa

checks = []
NAME, OTHER, CHAT = "Alex Example", "Blair Other", "Verified contact ✅"
ALTERNATE = "  ＡＬＥＸ   ＥＸＡＭＰＬＥ  "

def check(label, value):
    checks.append(bool(value))
    if not value:
        print("FAIL", label)

def aliases(names, saved=None, controller="Lena Drayton"):
    with patch.object(wa, "_read_json", side_effect=lambda path, default: copy.deepcopy(saved or {}) if path == wa.NAMES else {"chat_names": copy.deepcopy(names)}):
        return wa.load_aliases(controller)

normal = aliases({NAME: CHAT})
check("ordinary saved aliases keep original spelling and exact chat label", normal == {CHAT: NAME})
check("ordinary mapping still resolves its exact roster name", wa.match_chats([CHAT], [NAME], normal) == {CHAT: NAME})
alternate = aliases({ALTERNATE: CHAT})
check("single equivalent saved key resolves to actual roster spelling", wa.match_chats([CHAT], [NAME], alternate) == {CHAT: NAME})
check("only exact normalized full names qualify", wa.match_chats([CHAT], ["Alex Examples"], alternate) == {})
check("normalization does not strip accents or fuzzy-match taught identities", wa.match_chats([CHAT], ["Aléx Example"], alternate) == {})

# Use the production atomic-contact transformation with an in-memory read/write boundary.
original = {"contacts": {NAME: "353870000001", ALTERNATE: "353870000002"},
            "chat_names": {NAME: "Old label", ALTERNATE: "Older label"}, "private_extra": {"keep": True}}
store = wa_ui.Store("unused-synthetic-state.json")
with patch.object(store, "read", return_value=copy.deepcopy(original)), patch.object(store, "write", side_effect=lambda data: data):
    repaired = store.contact(NAME, "353870000003", chat=CHAT, replace=True)
taught = aliases(repaired["chat_names"])
check("contact repair preserves all equivalent saved keys", set(repaired["chat_names"]) == {NAME, ALTERNATE})
check("contact repair still preserves unrelated data", repaired["private_extra"] == original["private_extra"])
check("reader accepts equivalent keys produced by the real contact transaction", taught == {CHAT: NAME})
check("repaired equivalent keys match the actual current roster", wa.match_chats([CHAT], [NAME], taught) == {CHAT: NAME})
reverse = aliases({ALTERNATE: CHAT, NAME: CHAT})
check("saved insertion order cannot change roster matching", wa.match_chats([CHAT], [NAME], reverse) == {CHAT: NAME})
check("repeated call rows for one exact guard are still one identity", wa.match_chats([CHAT], [NAME, NAME], reverse) == {CHAT: NAME})

check("different people sharing one saved alias remain unresolved", CHAT not in aliases({NAME: CHAT, OTHER: CHAT}))
check("accent-different saved guard identities remain conflicting", CHAT not in aliases({NAME: CHAT, "Aléx Example": CHAT}))
check("normalization cannot choose between two nonliteral roster spellings", wa.match_chats([CHAT], [NAME, "ALEX EXAMPLE"], {CHAT: ALTERNATE}) == {})

preferred = aliases({NAME: CHAT, OTHER: CHAT}, {CHAT: OTHER})
check("existing taught mapping remains authoritative over conflicting beta aliases", preferred == {CHAT: OTHER})
check("existing literal roster target retains its explicit precedence", wa.match_chats([CHAT], [NAME, OTHER], preferred) == {CHAT: OTHER})
absent = aliases({NAME: NAME}, {NAME: "Absent Guard"})
check("absent authoritative target never falls through to fuzzy chat matching", wa.match_chats([NAME], [NAME], absent) == {})
check("exact decorated chat labels are not normalized or guessed", wa.match_chats([CHAT.lower()], [NAME], normal) == {})

check("equivalent full Lena controller name uses the same UI identity rule", aliases({NAME: CHAT}, controller="  ＬＥＮＡ  ＤＲＡＹＴＯＮ ") == normal)
for controller in (None, "Lena", "Lena Drayton Jr", "Other Controller"):
    check("beta aliases remain restricted to exact full controller", aliases({NAME: CHAT}, controller=controller) == {})
check("non-Lena controller retains only existing taught mappings", aliases({NAME: CHAT}, {"Legacy label": OTHER}, "Other Controller") == {"Legacy label": OTHER})

print("%d of %d checks passed" % (sum(checks), len(checks)))
raise SystemExit(0 if all(checks) else 1)
