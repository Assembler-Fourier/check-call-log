"""Drive the version 6 window through its event loop, transparent, click-through and never active.

    from drive6 import Rig
    rig = Rig("case-name", now="2026-10-05 20:06")       # the clock the window and the tool see
    rig.show("Henry Marlow", "2026-10-05")              # choose the controller and the day(s); the logs are on show at once
    rig.tab("shift"); rig.press("add_line")               # a tab; a button by its name in app.buttons
    rig.click("shift", 0, "text"); rig.type("Fire panel checked", "<Return>")
    rig.close()

Hand corrections and files go to scratch folders (edits-<name>, out-<name>); the user's settings are never written; the
window can never take the keyboard (see drive.py). It is the real app6.pyw from the tool folder.
"""
import datetime as _real
import importlib.machinery
import importlib.util
import os
import re
import shutil
import sys

from drive import Clock, HOME, WORK, foreground_is_mine, never_activate, quiet_root

sys.dont_write_bytecode = True


class Rig:
    def __init__(self, name, now=None, theme="light", keep=False, app="app.pyw", roster=None, settings=None):
        """roster: a roster copy to read instead of the tool's saved one (it is only read). settings: what the window
        starts with as its saved settings - by default Auto green off for everyone (a check switches it on itself)."""
        self.out, self.edits = os.path.join(WORK, "out-" + name), os.path.join(WORK, "edits-" + name)
        if not keep:
            for d in (self.out, self.edits):
                shutil.rmtree(d, ignore_errors=True)
        os.environ.update(CCL_OUT=self.out, CCL_NO_DOWNLOAD="1", CCL_NOOPEN="1", CCL_QUIET="1", CCL_GEOMETRY="1240x960+-2600+40", CCL_EDITS=self.edits)
        for stale in ("CCL_AUTORUN", "CCL_TAB", "CCL_DONE_FILE", "CCL_THEME", "CCL_QUIT_AFTER"):
            os.environ.pop(stale, None)
        loader = importlib.machinery.SourceFileLoader("app6_" + name.replace("-", "_"), os.path.join(HOME, "tool", app))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.mod = importlib.util.module_from_spec(spec)
        loader.exec_module(self.mod)
        self.calllog = self.mod.calllog
        self.calllog.EDITS = self.edits
        if roster is None:                            # (the public demo copy: its fixed demo roster, never the cached
            template = os.path.join(HOME, "demo", "roster-template.xlsx")    # one, which moves a week at a time)
            roster = template if os.path.exists(template) else None
        if roster:
            self.calllog.ROSTER_PATH = roster
            self.calllog.Roster.__init__.__defaults__ = (roster,)
            self.calllog.roster_signature.__defaults__ = (roster,)   # (its default was fixed to the cached copy)
        self.mod.save_settings = lambda data: None
        self.mod.load_settings = lambda: dict(settings) if settings is not None else {"auto_green_for": []}
        self.clock = None
        if now:
            self.clock = Clock(_real.datetime.strptime(now, "%Y-%m-%d %H:%M"))
            self.mod.datetime = self.calllog.datetime = self.clock.module
            import widgets
            widgets.datetime = self.clock.module
        import tkinter as tk
        never_activate()
        tk.Misc.bell = lambda widget, displayof=0: None
        self.root = quiet_root(tk)
        self.errors, self.results = [], []

        def went_wrong(exc, val, tb):
            import traceback
            self.errors.append("".join(traceback.format_exception(exc, val, tb)))
            print("  !! EXCEPTION IN A TK CALLBACK:\n" + self.errors[-1])

        self.root.report_callback_exception = went_wrong
        self.app = self.mod.App(self.root, theme)
        self.wait(lambda: self.app.view is not None, 30)
        self.pump(300)
        if foreground_is_mine():
            self.root.destroy()
            raise SystemExit("the test window came to the front - stopped")

    # ---- time and the event loop ----
    def pump(self, ms=200):
        import time
        end = time.time() + ms / 1000.0
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)

    def wait(self, cond, seconds=30):
        import time
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def at(self, text):
        """Move the clock, and let the window notice the minute has turned."""
        self.clock.set(text)
        # We are substituting for the scheduled tick. Leaving it queued creates
        # orphan timers each time the fake clock moves, including after teardown.
        if self.app.tick_job:
            self.root.after_cancel(self.app.tick_job)
        self.app.tick()
        self.pump(120)

    # ---- what a person does ----
    def show(self, name, *days):
        self.app.set_name(name)
        self.app.set_dates([_real.date.fromisoformat(d) for d in days])
        self.pump(200)
        return bool(self.app.packs) and all(self.app.meta(p) for p in self.app.packs)

    def tab(self, kind):
        self.press(self.app.tabs[kind])

    def press(self, control):
        """Click a button (by its name in app.buttons) or any control."""
        control = self.app.buttons[control] if isinstance(control, str) else control
        x, y = control.winfo_width() // 2, control.winfo_height() // 2
        control.event_generate("<Enter>", x=x, y=y)
        control.event_generate("<Button-1>", x=x, y=y)
        self.pump(40)
        if control.winfo_exists():
            control.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.pump(200)

    def grid(self, kind):
        return self.app.grids[kind]

    def click(self, kind, row, field, ctrl=False):
        """Click a cell: row is its place among the rows of that table (rules between rows are not counted)."""
        grid = self.grid(kind)
        x, y = grid.cell_on_screen(grid.keys[row], field)
        self.pump(40)
        x, y = grid.cell_on_screen(grid.keys[row], field)
        grid.body.event_generate("<Button-1>", x=x, y=y, state=4 if ctrl else 0)
        self.pump(200)

    def box(self):
        """The cell open for typing, as (entry widget, key, field), or None."""
        return next((g.box for g in self.app.grids.values() if g.box), None)

    def type(self, text, *keys):
        """Replace what is in the open cell with text, then press keys such as "<Return>", "<Tab>", "<Escape>"."""
        box = self.box()
        if not box:
            print("  !! no cell is open to type %r in" % (text,))
            return False
        if text is not None:
            box[0].delete(0, "end")
            box[0].insert(0, text)
        self.pump(40)
        for key in keys:
            target = self.box()
            if not target:
                break
            self.key(target[0], key)
            self.pump(200)
        return True

    def key(self, widget, sequence, char=""):
        """Press a key in a widget by running the widget's own binding for it, exactly as Tk would (Tk throws generated
        key events away unless this program holds the keyboard, which a test must never take)."""
        import tkinter
        script = widget.bind(sequence)
        if not script.strip():
            script = self.root.bind(sequence)        # a shortcut of the window itself
            if not script.strip():
                return False
        name = sequence.strip("<>").split("-")[-1]
        script = re.sub(r"%(.)", lambda m: {"W": str(widget), "K": char or name, "A": char or "{}", "#": "0", "s": "0", "%": "%"}.get(m.group(1), "??"), script)
        try:
            widget.tk.eval(script)
        except tkinter.TclError as e:                 # 'break' outside a loop is how a binding says "handled"
            if "break" not in str(e):
                raise
        self.pump(120)
        return True

    def click_away(self):
        """The keyboard leaves the open cell (a click elsewhere): what is typed is saved."""
        box = self.box()
        if box:
            self.key(box[0], "<FocusOut>")
        self.pump(150)

    # ---- what is on show ----
    def rows(self, kind):
        return self.grid(kind).values()

    def calls(self, day=None):
        return self.app.packs[self.app.day if day is None else day]["calls"]["calls"]

    def patrols(self, day=None):
        return [(r["start_text"], r["finish_text"], r["area"], r["notes"]) for r in self.app.packs[self.app.day if day is None else day]["patrols"]["rows"]]

    def entries(self, day=None):
        return [(e["time"], e["area"], e["text"]) for e in self.app.packs[self.app.day if day is None else day]["shift"]["entries"]]

    def said(self):
        return self.app.said

    def files(self, day=None):
        pack = self.app.packs[self.app.day if day is None else day]
        state = self.app.written(pack)
        return state["paths"] if state else {}

    def pdf_text(self, path):
        import zlib
        data = open(path, "rb").read()
        text = b"".join(zlib.decompress(m.group(1)) for m in re.finditer(rb"stream\n(.*?)\nendstream", data, re.S))
        return " ".join(t.decode("cp1252").replace("\\(", "(").replace("\\)", ")") for t in re.findall(rb"\((.*?)(?<!\\)\) Tj", text))

    # ---- reporting ----
    def check(self, label, ok, detail=""):
        self.results.append(bool(ok))
        print("%s  %s  %s" % ("PASS" if ok else "FAIL", label, str(detail)[:230]))
        return bool(ok)

    def close(self):
        try:
            self.app.on_close()
            self.pump(200)
        except Exception as e:
            self.check("window teardown completed without exception", False, repr(e))
        if self.root._ccl_tcl_errors:
            self.check("no Tcl background errors", False, self.root._ccl_tcl_errors[0])
        return self.results
