"""Pure, review-first contact import helpers. No file, settings, browser or network operations.

parse_vcards(text) returns candidates and diagnostics. match_candidates(...) proposes
exact-name/explicit-alias matches; it never chooses between numbers, guesses a name,
or replaces an existing saved contact. Only status="ready" has a proposed phone.
"""
import collections
import re
import urllib.parse

from whatsapp import fold


def normalize_phone(value):
    """Return WhatsApp-compatible international digits, or raise ValueError.

    Accept an international +/00 prefix, already-international digits, or a nine/
    ten-digit Irish national number beginning 0. Formatting punctuation is fine;
    extensions, multiple numbers, vanity letters and ambiguous local numbers are not.
    This validates the format, not ownership or whether WhatsApp serves the number.
    """
    if not isinstance(value, str):
        raise ValueError("phone must be text")
    text = value.strip()
    if text.lower().startswith("tel:"):
        text = urllib.parse.unquote(text[4:], errors="strict").strip()
    if any(ord(char) < 32 or ord(char) == 127 or char in "\u0085\u2028\u2029" for char in text):
        raise ValueError("phone number contains a control separator")
    if not text or not re.fullmatch(r"\+?[0-9\s().-]+", text):
        raise ValueError("unsupported phone characters or extension")
    international = text.startswith("+") or text.startswith("00")
    if international and re.search(r"\(\s*0\s*\)", text):
        raise ValueError("international number contains a national trunk prefix")
    digits = re.sub(r"[^0-9]", "", text)
    if text.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        if len(digits) not in (9, 10):
            raise ValueError("Irish national number must have nine or ten digits")
        digits = "353" + digits[1:]
    if not 9 <= len(digits) <= 15 or digits.startswith("0"):
        raise ValueError("international number must have nine to fifteen digits")
    if digits.startswith("3530"):
        raise ValueError("Irish international number retains its national prefix")
    return digits


def _unescape(value):
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m[1] in "nN" else m[1], value)


def _split_escaped(value, separator):
    parts, part, escaped = [], [], False
    for char in value:
        if escaped:
            part.extend(("\\", char))
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == separator:
            parts.append("".join(part))
            part = []
        else:
            part.append(char)
    if escaped:
        part.append("\\")
    return parts + ["".join(part)]


def _property(line):
    """Split outside quoted parameter values, retaining a TEL URI's own colon."""
    quoted, cut = False, None
    for i, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            cut = i
            break
    if cut is None or quoted:
        return None
    header, value = line[:cut], line[cut + 1:]
    parts = re.split(r';(?=(?:[^"]*"[^"]*")*[^"]*$)', header)
    name = parts[0].rsplit(".", 1)[-1].upper()
    parameters = {}
    for part in parts[1:]:
        key, separator, val = part.partition("=")
        if separator:
            parameters[key.upper()] = val.strip('"')
        else:                                         # vCard 2.x's bare CELL/VOICE parameter
            parameters["TYPE"] = parameters.get("TYPE", "") + "," + key
    return name, parameters, value


def parse_vcards(text):
    """Parse unfolded vCard FN/N/TEL fields into review candidates, without persistence.

    Result: {candidates: [{card, name, phones, issues, invalid_phone_fields,
                          name_options}], issues: [{card, code}]}.
    Phone values are distinct canonical digits. Issues contain codes, not private
    raw values. FN wins over N; conflicting FN values and multiple numbers remain
    explicitly unresolved. Unsupported encoded fields are reported, never guessed.
    """
    if not isinstance(text, str):
        raise TypeError("vCard input must be decoded text")
    lines = []
    for line in text.lstrip("\ufeff").splitlines():
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    candidates, diagnostics, current = [], [], None
    card_number = 0

    def finish(card, extra=None):
        issues = list(card["issues"])
        if extra:
            issues.append(extra)
        options = card["fn"] or card["n"]
        unique = list(dict.fromkeys(options))
        name = unique[0] if len(unique) == 1 else ""
        if not unique:
            issues.append("missing_name")
        elif len(unique) > 1:
            issues.append("multiple_names")
        phones = list(dict.fromkeys(card["phones"]))
        if not card["tel_count"]:
            issues.append("missing_number")
        elif not phones:
            issues.append("no_valid_number")
        if len(phones) > 1:
            issues.append("multiple_numbers")
        issues = list(dict.fromkeys(issues))
        candidates.append({"card": card["card"], "name": name, "phones": phones,
                           "issues": issues, "invalid_phone_fields": card["invalid"],
                           "name_options": unique if len(unique) > 1 else []})
        diagnostics.extend({"card": card["card"], "code": code} for code in issues)

    for line in lines:
        marker = line.strip().upper()
        if marker == "BEGIN:VCARD":
            if current is not None:
                finish(current, "unclosed_card")
            card_number += 1
            current = {"card": card_number, "fn": [], "n": [], "phones": [], "issues": [],
                       "tel_count": 0, "invalid": []}
            continue
        if marker == "END:VCARD":
            if current is not None:
                finish(current)
                current = None
            else:
                diagnostics.append({"card": None, "code": "unmatched_end"})
            continue
        if not line.strip() or current is None:
            continue
        prop = _property(line)
        if prop is None:
            current["issues"].append("malformed_line")
            continue
        name, params, value = prop
        if name not in ("FN", "N", "TEL"):
            continue
        if params.get("ENCODING", "").upper() not in ("", "8BIT"):
            current["issues"].append("unsupported_encoding")
            if name == "TEL":
                current["tel_count"] += 1
                current["invalid"].append(current["tel_count"])
            continue
        if name == "FN":
            full = " ".join(_unescape(value).split())
            if full:
                current["fn"].append(full)
        elif name == "N":
            fields = [_unescape(part) for part in _split_escaped(value, ";")]
            fields += [""] * max(0, 5 - len(fields))
            if len(fields) > 5:
                current["issues"].append("malformed_name")
                continue
            family, given, additional, prefix, suffix = fields
            full = " ".join((prefix + " " + given + " " + additional + " " + family + " " + suffix).split())
            if full:
                current["n"].append(full)
        else:
            current["tel_count"] += 1
            try:
                current["phones"].append(normalize_phone(_unescape(value)))
            except (ValueError, UnicodeError):
                current["issues"].append("invalid_number")
                current["invalid"].append(current["tel_count"])
            if "FAX" in {part.upper() for part in params.get("TYPE", "").split(",")}:
                current["issues"].append("fax_number_requires_review")
    if current is not None:
        finish(current, "unclosed_card")
    if not candidates:
        diagnostics.append({"card": None, "code": "no_cards"})
    return {"candidates": candidates, "issues": diagnostics}


def match_candidates(candidates, roster_names, aliases=None, existing=None):
    """Return review records; inputs are never changed and nothing is saved.

    aliases is an explicit contact-name -> roster-name mapping. Exact-fold name
    collisions, conflicting aliases, duplicate contact cards and numbers shared
    by different candidates require review. existing is a saved name -> phone
    mapping: even an invalid existing entry is preserved, never replaced.
    """
    roster = collections.defaultdict(set)
    for name in roster_names:
        if isinstance(name, str) and fold(name):
            roster[fold(name)].add(name)
    alias_targets = collections.defaultdict(set)
    for source, target in (aliases or {}).items():
        if isinstance(source, str) and isinstance(target, str):
            alias_targets[fold(source)].update(roster.get(fold(target), {None}))
    saved = collections.defaultdict(list)
    saved_numbers = collections.defaultdict(set)
    for name, phone in (existing or {}).items():
        if isinstance(name, str):
            saved[fold(name)].append(phone)
            try:
                saved_numbers[normalize_phone(phone)].add(fold(name))
            except (ValueError, UnicodeError):
                pass
    result = []
    for position, candidate in enumerate(candidates, 1):
        name = candidate.get("name", "")
        phones = list(candidate.get("phones", []))
        issues = list(candidate.get("issues", []))
        row = {"card": candidate.get("card", position), "name": name, "phones": phones,
               "issues": issues, "roster_name": None, "match_kind": None,
               "status": "unmatched", "phone": None}
        key = fold(name)
        exact, taught = roster.get(key, set()), alias_targets.get(key, set())
        targets = exact | taught
        if not key:
            row["status"] = "missing_name"
        elif len(targets) > 1 or None in targets:
            row["status"] = "ambiguous_name"
        elif targets:
            target = next(iter(targets))
            row.update(roster_name=target, match_kind="exact" if exact else "alias")
            previous = saved.get(fold(target), [])
            if previous:
                try:
                    same = len(phones) == 1 and all(normalize_phone(p) == phones[0] for p in previous)
                except (ValueError, UnicodeError):
                    same = False
                row["status"] = "already_saved" if same else "existing_contact"
            elif len(phones) != 1 or issues:
                row["status"] = "needs_number_review"
            else:
                try:
                    canonical = normalize_phone(phones[0])
                except (ValueError, UnicodeError):
                    row["status"] = "needs_number_review"
                else:
                    if saved_numbers.get(canonical, set()) - {fold(target)}:
                        row["status"] = "existing_number"
                    else:
                        row.update(status="ready", phone=canonical)
        result.append(row)
    by_target, by_phone = collections.defaultdict(list), collections.defaultdict(list)
    for row in result:
        if row["roster_name"]:
            by_target[row["roster_name"]].append(row)
        for phone in row["phones"]:
            by_phone[phone].append(row)
    for rows in by_target.values():
        if len(rows) > 1:
            for row in rows:
                if row["status"] == "ready":
                    row.update(status="duplicate_contact", phone=None)
    for rows in by_phone.values():
        if len({fold(row["name"]) for row in rows}) > 1:
            for row in rows:
                if row["status"] == "ready":
                    row.update(status="shared_number", phone=None)
    return result
