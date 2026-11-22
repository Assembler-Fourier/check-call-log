"""Synthetic pure contact-import checks. No phone, file, browser or network operations."""
import copy
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wa_contacts as contacts
import whatsapp

results = []
A, B = "353870000001", "353870000002"


def check(label, actual, expected=True):
    okay = actual == expected
    results.append(okay)
    if not okay:
        print("FAIL %s: %r != %r" % (label, actual, expected))


def rejected(value):
    try:
        contacts.normalize_phone(value)
    except (ValueError, UnicodeError):
        return True
    return False


def card(name="Test Guard", numbers=(A,), other=""):
    return "BEGIN:VCARD\nVERSION:3.0\nFN:%s\n%s%sEND:VCARD\n" % (
        name, "".join("TEL:%s\n" % n for n in numbers), other)


def parse(text):
    return contacts.parse_vcards(text)["candidates"]


def matched(text=None, names=("Test Guard",), aliases=None, existing=None):
    return contacts.match_candidates(parse(text or card()), names, aliases=aliases, existing=existing)


for value, want in (("+353 87 000 0001", A), ("00353-87-000-0001", A), ("087 000 0001", A),
                    (A, A), ("(01) 234 5678", "35312345678"), ("+1 (202) 555-0100", "12025550100"),
                    ("tel:+353-87-000-0001", A), ("TEL:%2B353870000001", A),
                    (" +353\u00a087\u00a0000\u00a00001 ", A)):
    got = contacts.normalize_phone(value)
    check("accepted normalization %s" % repr(value), got, want)
    check("canonical value remains compatible with existing WhatsApp normalizer", whatsapp.number(got), got)

for value in (None, "", "1234567", "+0123456789", "000123456789", "+353(0)870000001",
              "3530870000001", "087000000123", "+1234567890123456", "+353870000001 ext 9",
              "tel:+353870000001;ext=9", "tel:870000001;phone-context=+353", "0870000001/0870000002",
              "0870000001,0870000002", "++353870000001", "CALL-ME-NOW", "tel:%ZZ353870000001",
              "\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669"):
    check("unsafe or ambiguous number is rejected: %r" % value, rejected(value))

plain = parse(card())[0]
check("basic vCard name", plain["name"], "Test Guard")
check("basic vCard phone", plain["phones"], [A])
check("basic vCard has no review issue", plain["issues"], [])
check("parser records original card ordinal", plain["card"], 1)

unfolded = "\ufeffBEGIN:VCARD\r\nVERSION:4.0\r\nFN:Test \r\n Guard\r\nitem1.TEL;TYPE=\"CELL,VOICE\";VALUE=uri:tel:+35387\r\n\t0000001\r\nEND:VCARD\r\n"
got = parse(unfolded)[0]
check("space-folded FN is unfolded", got["name"], "Test Guard")
check("tab-folded grouped TEL URI is unfolded", got["phones"], [A])
check("common grouped TYPE and VALUE parameters are tolerated", got["issues"], [])
got = parse("BEGIN:VCARD\nN:Family;Given;Middle;Dr.;Jr.\nTEL;CELL:" + A + "\nEND:VCARD")[0]
check("N fallback uses given/additional/family with prefix/suffix", got["name"], "Dr. Given Middle Family Jr.")
check("bare CELL parameter is tolerated", got["issues"], [])
got = parse("BEGIN:VCARD\nN:Family\\;Part;Given\\,Name;;;\nTEL:" + A + "\nEND:VCARD")[0]
check("escaped semicolon does not split an N component", got["name"], "Given,Name Family;Part")
got = parse(card("First\\nSecond\\, Name\\; Part\\\\End"))[0]
check("FN escape sequences are decoded without guessing", got["name"], "First Second, Name; Part\\End")
got = parse(card("Display Name", other="N:Other;Fallback;;;\n"))[0]
check("FN takes priority over N fallback", got["name"], "Display Name")
got = parse(card("", other="N:Family;Given;;;\n"))[0]
check("empty FN can use N", got["name"], "Given Family")
got = parse(card(other="FN:Test Guard\n"))[0]
check("identical repeated FN remains unambiguous", got["issues"], [])
got = parse(card(other="FN:Different Guard\n"))[0]
check("conflicting FN never chooses the first", got["name"], "")
check("conflicting FN is reported", "multiple_names" in got["issues"])
check("conflicting names remain available for review", len(got["name_options"]), 2)
got = parse(card(numbers=(A, "+353 87 000 0001")))[0]
check("same number in two formats is one choice", got["phones"], [A])
check("deduplicated number does not require a false choice", "multiple_numbers" not in got["issues"])
got = parse(card(numbers=(A, B)))[0]
check("multiple distinct numbers are retained", got["phones"], [A, B])
check("multiple numbers explicitly need review", "multiple_numbers" in got["issues"])
got = parse(card(numbers=(A, "not a phone")))[0]
check("invalid number does not discard valid candidate", got["phones"], [A])
check("invalid telephone ordinal is reported without raw data", got["invalid_phone_fields"], [2])
check("invalid phone is reported", "invalid_number" in got["issues"])
check("missing TEL is reported", "missing_number" in parse(card(numbers=()))[0]["issues"])
check("all invalid TEL is reported", "no_valid_number" in parse(card(numbers=("bad",)))[0]["issues"])
check("missing name is reported", "missing_name" in parse(card(name=""))[0]["issues"])
check("FAX requires review, not an automatic WhatsApp candidate", "fax_number_requires_review" in parse(card(numbers=(), other="TEL;TYPE=FAX:" + A + "\n"))[0]["issues"])
check("unsupported encoded name is explicitly reported", "unsupported_encoding" in parse("BEGIN:VCARD\nFN;ENCODING=QUOTED-PRINTABLE:Test=20Guard\nTEL:" + A + "\nEND:VCARD")[0]["issues"])
check("unclosed vCard is retained for review", "unclosed_card" in parse(card().replace("END:VCARD\n", ""))[0]["issues"])
check("nested BEGIN cannot silently merge contacts", len(parse(card().replace("END:VCARD\n", "") + card("Other Guard", (B,)))), 2)
check("malformed content line is reported", "malformed_line" in parse(card(other="BROKEN FIELD\n"))[0]["issues"])
check("empty input reports no cards", contacts.parse_vcards("")["issues"], [{"card": None, "code": "no_cards"}])
check("multiple vCards maintain input order", [r["name"] for r in parse(card() + card("Other Guard", (B,)))], ["Test Guard", "Other Guard"])

row = matched()[0]
check("exact full name match is ready", row["status"], "ready")
check("one ready phone is proposed", row["phone"], A)
check("exact match identifies its method", row["match_kind"], "exact")
check("fold supports accents and punctuation", matched(card("T\u00e9st O'Guard"), names=("Test OGuard",))[0]["status"], "ready")
check("short or fuzzy name does not match", matched(card("Test"))[0]["status"], "unmatched")
check("small spelling error does not match", matched(card("Test Gaurd"))[0]["status"], "unmatched")
check("explicit alias can match", matched(card("Contact Nickname"), aliases={"Contact Nickname": "Test Guard"})[0]["status"], "ready")
check("alias match records its method", matched(card("Contact Nickname"), aliases={"Contact Nickname": "Test Guard"})[0]["match_kind"], "alias")
check("alias pointing outside tonight's roster is not accepted", matched(card("Contact Nickname"), aliases={"Contact Nickname": "Absent Guard"})[0]["status"], "ambiguous_name")
check("conflicting folded aliases remain unresolved", matched(card("Nickname"), names=("Test Guard", "Other Guard"), aliases={"Nickname": "Test Guard", "NICKNAME": "Other Guard"})[0]["status"], "ambiguous_name")
check("explicit alias conflicting with exact name needs review", matched(names=("Test Guard", "Other Guard"), aliases={"Test Guard": "Other Guard"})[0]["status"], "ambiguous_name")
check("fold-colliding roster names are never guessed", matched(card("Test Guard"), names=("Test Guard", "T\u00e9st Guard"))[0]["status"], "ambiguous_name")
check("same roster name in two calls is one person", matched(names=("Test Guard", "Test Guard"))[0]["status"], "ready")
check("multiple phones have no selected phone", matched(card(numbers=(A, B)))[0]["phone"], None)
check("multiple phones require review", matched(card(numbers=(A, B)))[0]["status"], "needs_number_review")
check("a valid plus invalid TEL still requires review", matched(card(numbers=(A, "bad")))[0]["status"], "needs_number_review")
check("existing identical phone is not proposed again", matched(existing={"Test Guard": "+353 87 000 0001"})[0]["status"], "already_saved")
check("existing different phone is preserved", matched(existing={"Test Guard": B})[0]["status"], "existing_contact")
check("even an invalid saved phone cannot be silently overwritten", matched(existing={"Test Guard": "bad"})[0]["status"], "existing_contact")
check("number already assigned to another saved person requires review", matched(existing={"Other Guard": A})[0]["status"], "existing_number")
rows = matched(card() + card())
check("duplicate contact cards are not silently chosen", [r["status"] for r in rows], ["duplicate_contact", "duplicate_contact"])
rows = matched(card() + card("Other Guard", (A,)), names=("Test Guard", "Other Guard"))
check("number shared by two names is not assigned automatically", [r["status"] for r in rows], ["shared_number", "shared_number"])
check("unmatched contact has no target or chosen number", (matched(card("Absent Guard"))[0]["roster_name"], matched(card("Absent Guard"))[0]["phone"]), (None, None))

candidates = parse(card() + card("Other Guard", (B,)))
names = ["Test Guard", "Other Guard"]
aliases = {"Nickname": "Test Guard"}
existing = {"Other Guard": B}
before = copy.deepcopy((candidates, names, aliases, existing))
contacts.match_candidates(candidates, names, aliases, existing)
check("matching never mutates caller data or existing mappings", (candidates, names, aliases, existing), before)

print("%d of %d checks passed" % (sum(results), len(results)))
raise SystemExit(0 if all(results) else 1)
