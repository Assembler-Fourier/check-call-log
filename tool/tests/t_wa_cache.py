"""Overlay retry/invalidation and matching regressions with synthetic scratch files and no services or Tk."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import calllog as c
import whatsapp as wa

checks = []


def check(label, ok):
    checks.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + label)


DAY = dt.date(2026, 11, 7)
MID = dt.datetime.combine(DAY, dt.time())
NOW = MID + dt.timedelta(hours=20)
CALL = dict(name="Alex Example", site="Example Hall", venue="Example Hall", start=1200, shift="20:00-close")
KEY = c.edit_key(CALL)
LOG = dict(stem="synthetic-cache", meta=dict(date_iso=DAY.isoformat(), supervisor="Lena Drayton", start_min=1080, end_min=1800), calls=[CALL])
SHIFT = dict(waiting=True, since="18:00", since_at="2026-11-07T18:00:00")


def event(ours, text, clock):
    hour, minute = map(int, clock.split(":"))
    return dict(kind="line", chat=CALL["name"], text=text, what="text", ours=ours,
                direction="outgoing" if ours else "incoming", shown=clock, truncated=False,
                seen=int((MID + dt.timedelta(hours=hour, minutes=minute, seconds=7)).timestamp() * 1000))


with tempfile.TemporaryDirectory(prefix="ccl-wa-cache-") as scratch:
    here = Path(scratch)
    with patch.multiple(wa, LOGS=str(here / "logs"), SETTINGS=str(here / "settings.json"),
                        NAMES=str(here / "names.json"), BETA=str(here / "beta.json")), patch.object(c, "EDITS", str(here / "edits")), patch.object(wa, "live", return_value=True):
        overlay = Path(wa.heard_file(LOG["stem"]))
        overlay.parent.mkdir()

        def setup(events):
            wa._last.clear()
            wa._commits_cache.clear()
            for path in (here / "logs").glob("*.jsonl"):
                path.unlink()
            (here / "settings.json").write_text(json.dumps(dict(controllers=["Lena Drayton"], trusted=[])), encoding="utf-8")
            overlay.write_text(json.dumps({"__shift__": SHIFT}), encoding="utf-8")
            wa.record(events)

        yes = [event(True, "All okay for Example Hall?", "19:00"), event(False, "Yes", "19:01")]
        setup(yes)
        previous = overlay.read_bytes()
        with patch.object(wa, "_write_json", side_effect=OSError("synthetic overlay write failure")):
            try:
                wa.update(LOG, NOW)
                failed = False
            except OSError:
                failed = True
        check("failed overlay write remains visible to its caller", failed)
        check("failed overlay write preserves the previous file", overlay.read_bytes() == previous)
        changed = wa.update(LOG, NOW)
        check("unchanged input retries after a failed overlay write", changed and wa.load_heard(LOG["stem"]).get(KEY, {}).get("response") == "Ok")
        check("successful unchanged overlay is still cached", wa.update(LOG, NOW) is False)

        # Another window/recovery can replace a structurally valid overlay while all source inputs stay unchanged.
        good = overlay.read_bytes()
        stale = json.dumps({"__shift__": SHIFT, KEY: {"how": "asked", "response": ""}}).encode()
        overlay.write_bytes(stale)
        check("valid stale overlay replacement is repaired despite unchanged shift metadata",
              wa.update(LOG, NOW) and overlay.read_bytes() == good)
        check("repaired unchanged output returns to the fast path", wa.update(LOG, NOW) is False)
        overlay.write_bytes(stale)
        with patch.object(wa, "_write_json", side_effect=OSError("synthetic repair write failure")):
            try:
                wa.update(LOG, NOW)
                failed = False
            except OSError:
                failed = True
        check("failed repair preserves the replacement rather than claiming success", failed and overlay.read_bytes() == stale)
        check("a failed repair is retried even with an older successful cache entry",
              wa.update(LOG, NOW) and overlay.read_bytes() == good)

        setup([event(False, "Sorry I cannot come tonight", "13:00")])
        wa.update(LOG, NOW)
        check("ordinary early message is outside a nontrusted person's window", KEY not in wa.load_heard(LOG["stem"]))
        (here / "settings.json").write_text(json.dumps(dict(controllers=["Lena Drayton"], trusted=[CALL["name"]])), encoding="utf-8")
        changed = wa.update(LOG, NOW)
        row = wa.load_heard(LOG["stem"]).get(KEY, {})
        check("trusted setting change rechecks an earlier cancellation", changed and row.get("response") == "" and row.get("how") == "check")

        setup(yes)
        wa.update(LOG, NOW)
        shifted = copy.deepcopy(LOG)
        shifted["calls"][0]["start"] = 23 * 60
        changed = wa.update(shifted, NOW)
        check("changed numeric start invalidates the question window even with unchanged display labels", changed and KEY not in wa.load_heard(LOG["stem"]))

        setup(yes)
        wa.update(LOG, NOW)
        moved = copy.deepcopy(LOG)
        moved["meta"]["start_min"] = 20 * 60
        with patch.object(wa, "heard", wraps=wa.heard) as reclassify:
            changed = wa.update(moved, MID + dt.timedelta(hours=21))
        check("changed controller window rechecks evidence while keeping same-day coverage", reclassify.call_count == 1
              and wa.load_heard(LOG["stem"])[KEY]["response"] == "Ok")

        early = [dict(event(True, "All okay for Example Hall?", "00:10"), kind="bubble", date="07/11/2026", id="early-q"),
                 dict(event(False, "Yes", "00:30"), kind="bubble", date="07/11/2026", id="early-y")]
        setup(early)
        check("update reads same-day midnight evidence before the former two-am cutoff", wa.update(LOG, NOW)
              and wa.load_heard(LOG["stem"])[KEY].get("reply_at") == "2026-11-07T00:30")
        setup([yes[0], event(False, "🤙", "19:01")])
        wa.update(LOG, NOW)
        check("an unconfigured symbolic reply remains reviewable", wa.load_heard(LOG["stem"])[KEY]["response"] == "")
        (here / "settings.json").write_text(json.dumps(dict(controllers=["Lena Drayton"], trusted=[], confirmation_emojis={CALL["name"]:["🤙"]})), encoding="utf-8")
        check("saving an exact personal emoji rule invalidates cache and confirms the scoped reply", wa.update(LOG, NOW)
              and wa.load_heard(LOG["stem"])[KEY]["response"] == "Ok")
        (here / "settings.json").write_text(json.dumps(dict(controllers=["Lena Drayton"], trusted=[])), encoding="utf-8")
        check("removing the personal rule rechecks the same reply", wa.update(LOG, NOW)
              and wa.load_heard(LOG["stem"])[KEY]["response"] == "")

        setup(yes)
        wa.update(LOG, NOW)
        Path(wa.NAMES).write_text(json.dumps({CALL["name"]: "Different Person"}), encoding="utf-8")
        changed = wa.update(LOG, NOW)
        check("a changed explicit alias cannot reuse the old confirmation", changed and KEY not in wa.load_heard(LOG["stem"]))

        def baseline_match(chats, people, taught=None):
            # The prior uncached comparison arithmetic, retained as an independent result oracle.
            def uncached(want, have, *unused):
                if not want or not have:
                    return 0.0
                values = [max(wa.difflib.SequenceMatcher(None, w, h).ratio() for h in have) for w in want]
                return sum(v if v >= .75 else 0 for v in values) / len(want)
            with patch.object(wa, "_covered", uncached):
                return wa.match_chats(chats, people, taught)

        cases = [(["Alex Example", "Alex Exampel"], ["Alex Example"], {}),
                 (["Alex Example", "Alex Exampel", "Blair Sample"], ["Alex Example", "Blair Sample"], {"Alex Example": "Alex Example"}),
                 (["Alex Example", "Alex Example Manager"], ["Alex Example", "Alexa Example"], {}),
                 (["ALEX   EXAMPLE", "Émile Sample"], ["Alex Example", "Emile Sample"], {})]
        for n, (chats, people, taught) in enumerate(cases):
            check("cached name comparisons preserve ambiguity and taught identity %d" % n,
                  wa.match_chats(chats, people, taught) == baseline_match(chats, people, taught))

        Path(wa.NAMES).write_text("{}", encoding="utf-8")
        base = wa.heard(LOG, yes)
        unrelated = [dict(e, chat="Unrelated Manager", id="manager-" + str(n)) for n, e in enumerate(yes)]
        check("unrelated conversations cannot alter roster replies", wa.heard(LOG, yes + unrelated) == base)
        ambiguous = [dict(e, chat="Alex Example Other") for e in yes]
        check("all chat names are considered before dropping unmatched history", KEY not in wa.heard(LOG, yes + ambiguous))
        quarantined = [dict(yes[1], read_id="a" * 32, text="I cannot come", shown="19:02", seen=event(False, "", "19:02")["seen"])]
        check("partial same-person history still revokes automatic confidence", wa.heard(LOG, yes + quarantined).get(KEY, {}).get("response") != "Ok")

print("%d of %d checks passed" % (sum(checks), len(checks)))
raise SystemExit(0 if all(checks) else 1)
