"""Check a standalone bounded dispatcher without hiding events or errors.

Synthetic Tk timers only, no app, roster or WhatsApp. All windows retain the
existing transparent/click-through/no-activation protection. The existing rigs
retain root.update: this helper changed geometry scheduling and is not adopted.
This does not prove that a blocking native callback can be interrupted.
"""
import contextlib
import io
import time
import tkinter as tk

import drive

results = []


def check(label, good, detail=""):
    results.append(bool(good))
    print("%s  %s  %s" % ("PASS" if good else "FAIL", label, detail))


root = drive.quiet_root(tk)
python_errors = []
root.report_callback_exception = lambda cls, value, trace: python_errors.append((cls, str(value)))
drive.pump_for(root, 50)
try:
    for label, settle_ms in (("plain helper", 0), ("settling helper", 150)):
        calls, active, job = [], [True], [None]
        root.geometry("400x240+-2600+40")
        panel = tk.Frame(root)
        panel.pack(fill="both", expand=True)
        geometry = tk.Label(panel, text="Geometry must receive idle time")
        geometry.pack()
        concurrent_idle = []
        root.after_idle(lambda: concurrent_idle.append(True))

        def producer():
            calls.append(time.monotonic())
            if active[0]:
                job[0] = root.after(0, producer)

        job[0] = root.after(0, producer)
        started = time.monotonic()
        drive.pump_for(root, 60)
        elapsed = time.monotonic() - started
        check(label + " pump returns while zero-delay producer remains pending",
              0 < len(calls) and elapsed < .75 and str(job[0]) in root.tk.call("after", "info"), (len(calls), round(elapsed, 4)))
        check(label + " idle callback runs alongside continuously ready timers", bool(concurrent_idle))
        check(label + " geometry is laid out while the timer producer is still active",
              geometry.winfo_ismapped() and geometry.winfo_width() >= geometry.winfo_reqwidth()
              and geometry.winfo_height() >= geometry.winfo_reqheight(),
              (geometry.winfo_width(), geometry.winfo_height(), geometry.winfo_reqwidth(), geometry.winfo_reqheight()))
        before = len(calls)
        started = time.monotonic()
        outcome = drive.wait_for(root, lambda: False, seconds=.04, settle_ms=settle_ms)
        check(label + " wait deadline expires despite continuously ready events",
              outcome is False and len(calls) > before and time.monotonic() - started < .75)
        ready = []
        root.after(10, lambda: ready.append(True))
        check(label + " wait still delivers a timed completion among recurring events",
              drive.wait_for(root, lambda: bool(ready), seconds=.5, settle_ms=settle_ms))
        active[0] = False
        if job[0]:
            root.after_cancel(job[0])
        panel.destroy()
        drive.pump_for(root, 20)

    seen, idle = [], []
    for number in range(180):
        root.after(0, lambda number=number: seen.append(number))
    root.after_idle(lambda: idle.append(True))
    check("finite callbacks and idle work are eventually delivered without dropping entries",
          drive.wait_for(root, lambda: len(seen) == 180 and bool(idle), .5)
          and sorted(seen) == list(range(180)))
    root.after(5000, lambda: None)
    started = time.monotonic()
    drive.event_slice(root, time.monotonic() + .3)
    check("an empty ready queue never waits for a future timer", time.monotonic() - started < .1)

    def bad_python():
        raise ValueError("intentional harness Python error")

    root.after(0, bad_python)
    drive.pump_for(root, 30)
    check("Python callback errors still reach report_callback_exception",
          python_errors == [(ValueError, "intentional harness Python error")], python_errors)
    # Capture only this deliberately induced diagnostic; the actual bgerror hook
    # and its retained error list remain unchanged, and unexpected errors fail below.
    captured = io.StringIO()
    with contextlib.redirect_stderr(captured):
        root.tk.call("after", 0, "error {intentional harness Tcl error}")
        drive.pump_for(root, 30)
    check("Tcl background errors still reach the original diagnostic hook",
          len(root._ccl_tcl_errors) == 1 and "intentional harness Tcl error" in root._ccl_tcl_errors[0]
          and "FAIL Tcl background error" in captured.getvalue())
    state = drive.quiet_state(root)
    check("harness keeps its original invisible nonactivating window protection",
          state["alpha"] == 0 and state["click_through"] and state["no_activate"] and not state["foreground"], state)
finally:
    for pending in root.tk.call("after", "info"):
        root.after_cancel(pending)
    root.destroy()

print("%d of %d checks passed" % (sum(results), len(results)))
raise SystemExit(0 if all(results) else 1)
