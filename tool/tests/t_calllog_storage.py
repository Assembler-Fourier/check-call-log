"""Correction persistence under real write failures, using synthetic files only; no roster, network or Tk."""
import builtins
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import calllog as c

checks = []


def check(label, value):
    checks.append(bool(value))
    print(("PASS " if value else "FAIL ") + label)


def refused(action):
    try:
        action()
    except c.Problem:
        return True
    return False


with tempfile.TemporaryDirectory(prefix="ccl-storage-") as scratch:
    c.EDITS = scratch
    log = {"stem": "synthetic", "calls": [], "incoming_roster": "Example Relief"}
    call = {"name": "Example Person", "site": "Example Venue", "shift": "20:00-close"}
    key = c.edit_key(call)
    path = Path(scratch) / "synthetic.json"
    original = json.dumps({key: {"time": "19:20", "response": "Manual decision"},
                           "Another saved row": {"response": "Keep this"}}, ensure_ascii=False).encode()
    save = lambda: c.save_edit(log, call, "response", "Ok")
    patrol = lambda: c.patrol_set(log, "u0", notes="Synthetic note")
    shift = lambda: c.save_shift(log, [{"id": "e1", "time": "20:00", "area": "Desk", "text": "Synthetic"}],
                                 incoming="Example Relief")
    path.write_bytes(original)
    save()
    saved = json.loads(path.read_bytes())
    check("a normal edit preserves another row and the time correction", saved[key] == {"time": "19:20", "response": "Ok"}
          and saved["Another saved row"] == {"response": "Keep this"})
    c.save_edit(log, call, "response", None)
    check("clearing one field retains the other correction", c.load_edits(log["stem"])[key] == {"time": "19:20"})

    for data in (b'{"unfinished":', b'[]', b'null', b'"wrong root"', b'\xff'):
        for name, action in (("call", save), ("fill ahead", lambda: c.fill_ahead(log, True)),
                             ("patrol", patrol), ("shift", shift)):
            path.write_bytes(data)
            check("damaged saved data blocks " + name + " without replacing it: " + repr(data),
                  refused(action) and path.read_bytes() == data)
    for bad in ([], "bad entry", None):
        data = json.dumps({key: bad, "Other row": {"response": "Keep"}}).encode()
        path.write_bytes(data)
        check("malformed target row is retained", refused(save) and path.read_bytes() == data)
    path.write_text(json.dumps({"Unknown old record": [1, 2]}), encoding="utf-8")
    save()
    check("an unrelated unknown record is preserved on a valid row edit", c.load_edits(log["stem"])["Unknown old record"] == [1, 2])

    for raw in ({"changed": {"u0": []}}, {"added": [{"id": "a1", "start": "wrong", "finish": "20:15"}]},
                {"removed": [1]}, {"blank": "yes"}, {"rows": [None]}):
        data = json.dumps(raw).encode()
        path.write_bytes(data)
        check("invalid patrol records are retained", refused(patrol) and path.read_bytes() == data)
    path.write_text(json.dumps({"rows": [{"start": "20:00", "finish": "20:15", "notes": "Old format"}],
                                "future_field": {"preserve": True}}), encoding="utf-8")
    c.patrol_set(log, "a1", notes="Updated")
    saved = c.load_edits(log["stem"])
    check("valid legacy patrols migrate without losing extra metadata", saved["added"][0]["notes"] == "Updated"
          and saved["future_field"] == {"preserve": True})
    c.patrol_reset(log)
    check("explicit patrol reset retains unrelated metadata", c.load_edits(log["stem"])["future_field"] == {"preserve": True})

    for raw in ({"entries": "wrong"}, {"entries": [None]}, {"entries": [{"text": []}]}, {"handover": []}):
        data = json.dumps(raw).encode()
        path.write_bytes(data)
        check("invalid shift records are retained", refused(shift) and path.read_bytes() == data)
    path.write_text(json.dumps({"entries": [], "future_field": {"preserve": True}}), encoding="utf-8")
    shift()
    check("shift edit preserves unrelated metadata", c.load_edits(log["stem"])["future_field"] == {"preserve": True})

    fixtures = (("call", original, save),
                ("patrol", b'{"changed":{"u0":{"notes":"Before"}}}', patrol),
                ("shift", b'{"entries":[],"handover":"Before"}', shift),
                ("undo", original, lambda: c.restore({log["stem"]: b'{"restored":true}'})))
    for name, data, action in fixtures:
        path.write_bytes(data)
        with patch.object(c.os, "replace", side_effect=PermissionError("synthetic locked destination")):
            failed = refused(action)
        check("failed replacement preserves the previous " + name + " file", failed and path.read_bytes() == data)
        check("failed " + name + " replacement cleans only its temporary file", list(Path(scratch).glob(".calllog-*.tmp")) == [])

    real_temp = c.tempfile.NamedTemporaryFile

    class BrokenWrite:
        def __init__(self, *args, **kwargs):
            self.file = real_temp(*args, **kwargs)
            self.name = self.file.name

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.file.__exit__(*args)

        def write(self, value):
            self.file.write(value[:5])
            raise OSError("synthetic disk write failure")

    path.write_bytes(original)
    with patch.object(c.tempfile, "NamedTemporaryFile", BrokenWrite):
        failed = refused(save)
    check("a partial temporary write cannot truncate the original correction", failed and path.read_bytes() == original)
    check("partial write temporary file is removed", list(Path(scratch).glob(".calllog-*.tmp")) == [])
    with patch.object(c.os, "fsync", side_effect=OSError("synthetic flush failure")):
        failed = refused(save)
    check("flush failure preserves original bytes", failed and path.read_bytes() == original)
    check("unencodable new data preserves the original", refused(lambda: c.save_edit(log, call, "response", "\ud800"))
          and path.read_bytes() == original)

    path.unlink()
    with patch.object(c.os, "replace", side_effect=OSError("synthetic new-file failure")):
        failed = refused(save)
    check("failed first save leaves no misleading correction file", failed and not path.exists()
          and list(Path(scratch).glob(".calllog-*.tmp")) == [])
    check("absent correction has a genuine absent snapshot", c.snapshot([log["stem"]]) == {log["stem"]: None})
    path.write_bytes(original)
    real_open = builtins.open

    def locked_open(file, *args, **kwargs):
        if os.fspath(file) == str(path):
            raise PermissionError("synthetic unreadable correction")
        return real_open(file, *args, **kwargs)

    with patch.object(builtins, "open", locked_open):
        check("snapshot cannot turn a read error into an absent-file undo", refused(lambda: c.snapshot([log["stem"]])))
        check("editing an unreadable file is refused", refused(save))
    check("read refusal preserves bytes", path.read_bytes() == original)
    before = c.snapshot([log["stem"], "second"])
    save()
    c.restore({"second": b'{"another":"saved"}'})
    after = c.snapshot([log["stem"], "second"])
    c.restore(before)
    check("undo restores exact bytes and absence", c.snapshot([log["stem"], "second"]) == before)
    c.restore(after)
    check("redo restores exact bytes for both files", c.snapshot([log["stem"], "second"]) == after)
    path.write_bytes(b'{"unfinished":')
    held = c.snapshot([log["stem"]])
    check("snapshot preserves damaged bytes rather than silently repairing them", held[log["stem"]] == b'{"unfinished":')

print("%d of %d checks passed" % (sum(checks), len(checks)))
raise SystemExit(0 if all(checks) else 1)
