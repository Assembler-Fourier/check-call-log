"""Drive the real window through its event loop, transparent, click-through and never active.

    from drive import Rig
    rig = Rig("case-name", now="2026-10-05 20:06")      # the clock the window and the tool see (None = real time)
    rig.make("Henry Marlow", "2026-10-05")              # press Make logs and wait
    rig.tab("shift"); rig.press(rig.app.shift_buttons[0]) # click a button
    rig.click(rig.app.stree, row=0, column="text")        # click a table cell
    rig.type("Fire panel checked", "<Return>")            # type into the open cell, then a key
    rig.close()

Output files and hand corrections go to scratch folders (out-<name>, edits-<name>); the user's settings are never written.
The window is the real app.pyw from the tool folder, so this tests what he runs.
"""
import datetime as _real
import importlib.machinery
import importlib.util
import io
import os
import tempfile
import shutil
import sys
import time as _time
import types
import _tkinter

sys.dont_write_bytecode = True                        # leave no .pyc behind in the tool folder
HOME = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The window made here must never take the keyboard from whoever is using the PC. Tk activates the first window a
# process makes, and Windows sometimes lets it come to the front even parked off-screen (seen by the 5.0 review).
# So activation of THIS test process's own windows is refused, with a hook that lives only in this thread of this
# process and is gone when it exits. It touches nothing outside the test process.
import ctypes as _ct  # noqa: E402
from ctypes import wintypes as _wt  # noqa: E402

_user, _kernel = _ct.WinDLL("user32"), _ct.WinDLL("kernel32")
_HOOKPROC = _ct.WINFUNCTYPE(_ct.c_ssize_t, _ct.c_int, _wt.WPARAM, _wt.LPARAM)
_user.SetWindowsHookExW.argtypes, _user.SetWindowsHookExW.restype = [_ct.c_int, _HOOKPROC, _ct.c_void_p, _wt.DWORD], _ct.c_void_p
_user.CallNextHookEx.argtypes, _user.CallNextHookEx.restype = [_ct.c_void_p, _ct.c_int, _wt.WPARAM, _wt.LPARAM], _ct.c_ssize_t
_user.GetForegroundWindow.restype = _ct.c_void_p
_user.GetWindowThreadProcessId.argtypes = [_ct.c_void_p, _ct.POINTER(_ct.c_ulong)]
_kernel.GetCurrentThreadId.restype = _wt.DWORD
refused = []                                          # activations turned away


def _cbt(code, wparam, lparam):
    if code in (5, 9):                                # HCBT_ACTIVATE, HCBT_SETFOCUS
        refused.append(code)
        return 1
    return _user.CallNextHookEx(None, code, wparam, lparam)


_cbt_proc, _hook = _HOOKPROC(_cbt), None


def never_activate():
    global _hook
    if not _hook:
        _hook = _user.SetWindowsHookExW(5, _cbt_proc, None, _kernel.GetCurrentThreadId())       # WH_CBT, this thread only


def foreground_is_mine():
    """Read only: does the window the user is typing in belong to this test process?"""
    pid = _ct.c_ulong(0)
    hwnd = _user.GetForegroundWindow()
    if hwnd:
        _user.GetWindowThreadProcessId(hwnd, _ct.byref(pid))
    return pid.value == os.getpid()


def _quiet_style(window):
    """Only the native frame of this process's own Tk window is changed."""
    _user.GetParent.argtypes, _user.GetParent.restype = [_wt.HWND], _wt.HWND
    _user.GetWindowLongW.argtypes, _user.GetWindowLongW.restype = [_wt.HWND, _ct.c_int], _ct.c_long
    _user.SetWindowLongW.argtypes, _user.SetWindowLongW.restype = [_wt.HWND, _ct.c_int, _ct.c_long], _ct.c_long
    hwnd = _user.GetParent(window.winfo_id()) or window.winfo_id()
    style = _user.GetWindowLongW(hwnd, -20)
    _user.SetWindowLongW(hwnd, -20, style | 0x20 | 0x80 | 0x08000000)  # transparent, tool window, no activate
    return hwnd


def _quiet_window(window):
    window.withdraw()
    window.attributes("-alpha", 0.0)
    window.update_idletasks()
    _quiet_style(window)
    # wm_transient may replace a Toplevel's native frame. Alpha remains zero;
    # reapply the click-through/no-activate styles to the replacement on mapping.
    window.bind("<Map>", lambda event: _quiet_style(window) if event.widget is window else None, add="+")
    window.deiconify()


def quiet_state(window):
    """Read the actual opacity/styles/foreground state, without repairing them."""
    hwnd = _user.GetParent(window.winfo_id()) or window.winfo_id()
    style = _user.GetWindowLongW(hwnd, -20)
    return {"alpha": float(window.attributes("-alpha")), "click_through": bool(style & 0x20),
            "no_activate": bool(style & 0x08000000), "foreground": foreground_is_mine()}


def quiet_root(tk):
    """Hide the root AND every test Toplevel before either can paint.

    Negative coordinates alone are insufficient on a multi-monitor desktop. Keep
    the activation hook; every native frame is transparent and click-through.
    """
    never_activate()
    if not getattr(tk.Toplevel, "_ccl_quiet", False):
        original_init = tk.Toplevel.__init__

        def quiet_toplevel(window, *args, **kwargs):
            original_init(window, *args, **kwargs)
            _quiet_window(window)                   # before the caller can pump/map the newly created dialog

        tk.Toplevel.__init__ = quiet_toplevel
        tk.Toplevel._ccl_quiet = True
    # Native message boxes bypass tk.Toplevel. An unexpected one must fail a test,
    # not put a visible modal on the work desktop. Expected confirmations are
    # explicitly replaced by the individual rig/check as before.
    from tkinter import messagebox

    def unexpected_message(*args, **kwargs):
        raise AssertionError("unexpected native message box in automated check")

    for name in ("showerror", "showwarning", "showinfo"):
        setattr(messagebox, name, unexpected_message)
    root = tk.Tk()
    # Tcl's own bgerror dialog bypasses Python Toplevel/messagebox and waits in
    # a native modal loop. Record the complete error instead; every rig fails it.
    errors = root._ccl_tcl_errors = []

    def background_error(message, detail):
        errors.append(str(message) + "\n" + str(detail))
        print("FAIL Tcl background error:\n" + errors[-1], file=sys.stderr, flush=True)

    root.createcommand("_ccl_test_bgerror", background_error)
    root.tk.call("proc", "bgerror", "message", "global errorInfo; _ccl_test_bgerror $message $errorInfo")
    _quiet_window(root)
    return root


HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(tempfile.gettempdir(), "ccl-tests")         # throwaway output; the checks themselves live in tool\tests
if not isinstance(sys.stdout, io.TextIOWrapper) or sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def event_slice(root, deadline, max_events=64):
    """Dispatch ready Tcl events briefly, then return to the harness's Python deadline.

    Tcl's update drains until no event remains; an after(0) producer can prevent it
    returning. DoOneEvent keeps every event category, including idle/error handling,
    but yields between dispatches. A single callback or native call can still block:
    the external process timeout remains necessary and is not replaced by this cap.
    """
    end, dispatched = min(deadline, _time.monotonic() + .01), 0
    # A bounded slice may end before ALL_EVENTS reaches its idle phase. Give
    # geometry/redraw work a turn even while timers keep the ready queue busy.
    # Unlike update_idletasks, this dispatch does not drain newly added idle work.
    if max_events > 0 and _time.monotonic() < end:
        dispatched += bool(root.tk.dooneevent(_tkinter.IDLE_EVENTS | _tkinter.DONT_WAIT))
    while dispatched < max_events and _time.monotonic() < end:
        if not root.tk.dooneevent(_tkinter.ALL_EVENTS | _tkinter.DONT_WAIT):
            break
        dispatched += 1
    return dispatched


def pump_for(root, ms):
    end = _time.monotonic() + ms / 1000.0
    while _time.monotonic() < end:
        event_slice(root, end)
        remaining = end - _time.monotonic()
        if remaining > 0:
            _time.sleep(min(.01, remaining))


def wait_for(root, cond, seconds, settle_ms=0):
    end = _time.monotonic() + seconds
    while _time.monotonic() < end:
        event_slice(root, end)
        if cond():
            if settle_ms:
                pump_for(root, settle_ms)
            return True
        remaining = end - _time.monotonic()
        if remaining > 0:
            _time.sleep(min(.02, remaining))
    return False


class Clock:
    """A stand-in for the datetime module with a clock that can be set and moved."""

    def __init__(self, now):
        clock = self
        self.at = now

        class DateTime(_real.datetime):
            @classmethod
            def now(cls, tz=None):
                t = clock.at
                return cls(t.year, t.month, t.day, t.hour, t.minute, t.second)

        class Date(_real.date):
            @classmethod
            def today(cls):
                return cls(clock.at.year, clock.at.month, clock.at.day)

        self.module = types.SimpleNamespace(datetime=DateTime, date=Date, time=_real.time, timedelta=_real.timedelta)

    def set(self, text):
        self.at = _real.datetime.strptime(text, "%Y-%m-%d %H:%M")


class Rig:
    def __init__(self, name, now=None, keep=False, theme=None):
        self.out, self.edits = os.path.join(WORK, "out-" + name), os.path.join(WORK, "edits-" + name)
        if not keep:
            for d in (self.out, self.edits):
                shutil.rmtree(d, ignore_errors=True)
        os.environ.update(CCL_OUT=self.out, CCL_NO_DOWNLOAD="1", CCL_NOOPEN="1", CCL_GEOMETRY="+-2600+40")
        for stale in ("CCL_AUTORUN", "CCL_TAB", "CCL_DONE_FILE"):
            os.environ.pop(stale, None)
        loader = importlib.machinery.SourceFileLoader("app_" + name.replace("-", "_"), HOME + r"\tool\app5.pyw")
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.mod = importlib.util.module_from_spec(spec)
        loader.exec_module(self.mod)
        self.calllog, self.forms = self.mod.calllog, self.mod.forms
        self.calllog.EDITS = self.edits
        self.mod.save_settings = lambda data: None
        self.asked = []                               # "are you sure?" questions the window put: answered yes, and kept here
        self.mod.messagebox.askokcancel = lambda title, text, **kw: self.asked.append(text) or True
        self.clock = None
        if now:
            self.clock = Clock(_real.datetime.strptime(now, "%Y-%m-%d %H:%M"))
            self.mod.datetime = self.calllog.datetime = self.clock.module
        self.closes = []                              # every time a typing box was shut, and what shut it (for when a case goes wrong)
        shut = self.mod.CellEditor.close

        def close(cells, cancel=False, move=None, to=None):
            if cells.box:
                import traceback
                self.closes.append("%s=%r cancel=%s move=%s via %s" % (cells.box[2], cells.box[0].get(), cancel, move, " < ".join(
                    f.name for f in reversed(traceback.extract_stack(limit=7)[:-1]))))
            return shut(cells, cancel=cancel, move=move, to=to)

        self.mod.CellEditor.close = close
        if theme:                                     # what main() does with the look chosen last time
            self.mod.use_theme(theme)
        import tkinter as tk
        self.tk = tk
        never_activate()
        tk.Misc.bell = lambda widget, displayof=0: None      # a refused value rings the PC's bell: not during a test
        self.root = quiet_root(tk)
        self.errors = []                              # anything that went wrong inside a Tk callback

        def went_wrong(exc, val, tb):
            import traceback
            self.errors.append("".join(traceback.format_exception(exc, val, tb)))
            print("  !! EXCEPTION IN A TK CALLBACK:\n" + self.errors[-1])

        self.root.report_callback_exception = went_wrong
        self.app = self.mod.App(self.root)
        self.pump(600)
        self.results = []
        if foreground_is_mine():                      # it must never happen: stop rather than hold the keyboard
            self.root.destroy()
            raise SystemExit("the test window came to the front - stopped")

    # ---- time and the event loop ----
    def pump(self, ms=250):
        """Let the window work for a while: redraws, resizes, timers - everything a real screen would do."""
        import time
        end = time.time() + ms / 1000.0
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)

    def wait(self, cond, seconds=40):
        import time
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            if cond():
                self.pump(150)
                return True
            time.sleep(0.02)
        return False

    def at(self, text):
        """Move the clock."""
        self.clock.set(text)

    # ---- what a person does ----
    def make(self, name, *days, docs=("calls", "patrols", "shift")):
        for kind, var in self.app.docs.items():
            var.set(kind in docs)
        self.app.set_name(name)
        self.app.set_dates([_real.date.fromisoformat(d) for d in days])
        self.press(self.app.go)
        ok = self.wait(lambda: not self.app.busy)
        self.pump(300)
        return ok and bool(self.app.packs)

    def tab(self, kind):
        self.app.tabs[kind].event_generate("<Button-1>")
        self.pump(250)

    def press(self, button):
        """Click one of the window's buttons."""
        button.event_generate("<Enter>")
        button.event_generate("<Button-1>")
        self.pump(60)
        if button.winfo_exists():                     # the look button makes the whole window again, itself included
            button.event_generate("<ButtonRelease-1>")
        self.pump(250)

    def click(self, tree, row, column):
        """Click a cell: row is its position among the real rows of the table (headings and hints excluded)."""
        cells = self.editor_of(tree)
        items = [i for i in tree.get_children() if i in cells.keys]
        tree.see(items[row])
        self.pump(60)
        x, y, w, h = tree.bbox(items[row], column)
        tree.event_generate("<Button-1>", x=x + w // 2, y=y + h // 2)
        tree.event_generate("<ButtonRelease-1>", x=x + w // 2, y=y + h // 2)
        self.pump(250)

    def select(self, tree, row):
        """Click a row in a column that is not editable, to select it (as before pressing Remove)."""
        cells = self.editor_of(tree)
        items = [i for i in tree.get_children() if i in cells.keys]
        x, y, w, h = tree.bbox(items[row], tree["columns"][0])
        tree.event_generate("<Button-1>", x=x + w // 2, y=y + h // 2)
        tree.event_generate("<ButtonRelease-1>", x=x + w // 2, y=y + h // 2)
        self.pump(200)

    def editor_of(self, tree):
        return {id(self.app.tree): self.app.call_cells, id(self.app.ptree): self.app.patrol_cells, id(self.app.stree): self.app.shift_cells}[id(tree)]

    def box(self):
        """The cell open for typing, as (entry widget, what row it is, which field), or None."""
        for cells in (self.app.call_cells, self.app.patrol_cells, self.app.shift_cells):
            if cells.box:
                return cells.box
        return None

    def type(self, text, *keys):
        """Replace what is in the open cell with text, then press keys such as "<Return>", "<Tab>", "<Escape>"."""
        box = self.box()
        if not box:
            print("  !! no cell is open to type %r in. The last boxes were shut by:\n     %s" % (text, "\n     ".join(self.closes[-3:])))
            return False
        if text is not None:
            box[0].delete(0, "end")
            box[0].insert(0, text)
        self.pump(60)
        for key in keys:
            target = self.box()
            if not target:
                break
            self.key(target[0], key)
            self.pump(250)
        return True

    def key(self, widget, sequence):
        """Press a key in a widget (or take the keyboard away from it, with "<FocusOut>").

        Tk throws generated key events away unless this program holds the keyboard, and this window must never take
        the keyboard from whoever is using the PC. So the widget's own binding for the key is run directly, exactly
        as Tk would run it: the same script, with the event's fields filled in. No binding for the key: nothing happens."""
        import re
        import tkinter
        script = widget.bind(sequence)
        if not script.strip():
            return False
        name = sequence.strip("<>").split("-")[-1]
        script = re.sub(r"%(.)", lambda m: {"W": str(widget), "K": name, "A": "{}", "#": "0", "%": "%"}.get(m.group(1), "??"), script)
        try:
            widget.tk.eval(script)
        except tkinter.TclError as e:                 # 'break' outside a loop is how a binding says "handled"
            if "break" not in str(e):
                raise
        return True

    def click_away(self):
        """Click on an empty part of the window (the notes panel), as people do to finish typing."""
        box = self.box()
        self.app.notes.focus_set()
        self.app.notes.event_generate("<Button-1>", x=5, y=5)
        self.pump(120)
        if box and self.box() and self.box()[0] is box[0]:      # the keyboard was never really here, so say it has left
            self.key(box[0], "<FocusOut>")
        self.pump(200)

    # ---- what is on show ----
    def rows(self, tree):
        cells = self.editor_of(tree)
        return [tuple(tree.item(i, "values")) for i in tree.get_children() if i in cells.keys]

    def patrols(self, day=0):
        return [(r["start_text"], r["finish_text"], r["area"], r["notes"]) for r in self.app.packs[day]["patrols"]["rows"]]

    def entries(self, day=0):
        return [(e["time"], e["area"], e["text"]) for e in self.app.packs[day]["shift"]["entries"]]

    def status(self):
        return self.app.status.get()

    def pdf_text(self, path):
        import re
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
            self.pump(300)
            if not self.app.gone:
                self.wait(lambda: self.app.gone, 30)
        except Exception as e:
            self.check("window teardown completed without exception", False, repr(e))
        if self.root._ccl_tcl_errors:
            self.check("no Tcl background errors", False, self.root._ccl_tcl_errors[0])
        return self.results
