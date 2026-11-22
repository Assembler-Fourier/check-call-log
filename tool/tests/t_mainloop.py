"""Confirmed Auto green through the real Tk mainloop and isolated fake service.

Run serially with the GUI checks, with an external deadline as well as the Tk
deadline below: powershell -NoProfile -File run_all.ps1 -Tests t_mainloop -TimeoutSeconds 60
This standalone check is intentionally outside the default suite. It verifies
normal application event-loop behavior; it does not explain or repair a native
wait previously seen in the test harness's repeated root.update() calls.
"""
import hashlib
from pathlib import Path
import time

import greenrig as g
from drive import quiet_state


def main():
    # The controller has WhatsApp configured but no trusted-policy exceptions.
    # CCL_NO_DOWNLOAD disables the real reader/server; all replies and saved
    # manual responses are isolated in this rig's scratch paths.
    g.calllog.whatsapp.is_for = lambda name: True
    g.calllog.whatsapp.trusted_names = lambda: set()
    fixture_before = hashlib.sha256(Path(g.FIXTURE).read_bytes()).digest()
    rig = g.rig_for("native-mainloop", "2026-10-23 20:06")
    g.calllog.whatsapp.set_replies(True)        # (20 Nov: replies - and their evidence rule - apply with Auto read on)
    rig.show("Lena Drayton", "2026-10-23")
    app, root, fake = rig.app, rig.root, rig.fake
    candidates = [c for c in rig.calls()
                  if c["call_text"] and not c["ahead"] and c["response"] == "Ok" and not c.get("strike")
                  and g.rostercolour.css(c["fill"]) == "#ffffff"]
    unconfirmed = {c["ref"] for c in candidates[:2]}
    expected = {c["ref"] for c in candidates[2:]}
    for call in candidates[2:]:
        g.calllog.save_edit(app.pack["calls"], call, "response", "Ok")
    app.rebuild()
    app.refresh()
    before_fill, before_text = dict(fake.fill), dict(fake.text)
    state = {"started": False, "completed": False, "timed_out": False,
             "heartbeats": 0, "quiet": True, "quiet_samples": 0,
             "completion_since": None}

    def sample_quiet():
        observed = quiet_state(root)
        state["quiet_samples"] += 1
        state["quiet"] = state["quiet"] and observed["alpha"] == 0.0 \
            and observed["click_through"] and observed["no_activate"] and not observed["foreground"]

    def heartbeat():
        state["heartbeats"] += 1
        sample_quiet()
        if not state["quiet"]:
            root.quit()
            return
        root.after(100, heartbeat)

    def monitor():
        if rig.errors or root._ccl_tcl_errors:
            root.quit()
            return
        done = state["started"] and not app.sending and not app.auto_busy \
            and len(fake.sent) == len(expected) and not app.auto_due()
        if done:
            if state["completion_since"] is None:
                state["completion_since"] = time.monotonic()
            elif time.monotonic() - state["completion_since"] >= 0.75:
                # Allow the normal next-auto timer to run after the final reply;
                # the service must remain idle instead of resending completed work.
                state["completed"] = True
                root.quit()
                return
        else:
            state["completion_since"] = None
        root.after(100, monitor)

    def timed_out():
        state["timed_out"] = True
        root.quit()

    def start():
        state["started"] = True
        app.buttons["auto"].press()  # ordinary button command, normal auto_run/poll callbacks

    def no_manual_pump(*args, **kwargs):
        raise AssertionError("batch verification must use root.mainloop, not Rig.pump/wait")

    # Setup has finished. From this point all batch progress comes through the
    # application's normal mainloop, not a synthetic pump or settle helper.
    rig.pump = rig.wait = no_manual_pump
    root.after(0, start)
    root.after(50, heartbeat)
    root.after(100, monitor)
    root.after(25000, timed_out)
    try:
        root.mainloop()
        sample_quiet()
        sent = list(fake.sent)
        changed = {ref for ref, fill in fake.fill.items() if before_fill.get(ref) != fill}
        rig.check("fixture stages eighteen explicit confirmations and two generated Ok responses",
                  len(candidates) == 20 and len(expected) == 18 and len(unconfirmed) == 2)
        rig.check("normal mainloop completed the batch before its deadline",
                  state["started"] and state["completed"] and not state["timed_out"],
                  {"completed": state["completed"], "timed_out": state["timed_out"], "sent": len(sent)})
        rig.check("every eligible call and no other call was requested exactly once",
                  len(sent) == len(expected) and {row[1] for row in sent} == expected
                  and all(row[0] == "green" for row in sent))
        rig.check("only eligible cells changed, all to green",
                  changed == expected and all(fake.fill[ref] == "#92d050" for ref in expected))
        rig.check("generated Ok responses without evidence stayed white",
                  all(fake.fill[ref] == "#ffffff" for ref in unconfirmed))
        rig.check("the fake service's roster text stayed unchanged", fake.text == before_text)
        rig.check("normal callbacks send only one request at a time", fake.maxflight == 1 and fake.inflight == 0)
        rig.check("independent UI timer kept running during the batch", state["heartbeats"] > 1,
                  {"heartbeats": state["heartbeats"]})
        rig.check("test window stayed transparent, click-through, and never active",
                  state["quiet"] and state["quiet_samples"] > 1)
        rig.check("app consumed all replies and has no due requests",
                  not app.sending and not app.auto_busy and app.queue.empty() and not app.auto_due())
        rig.check("no Python or Tcl callback errors", not rig.errors and not root._ccl_tcl_errors,
                  {"python_errors": len(rig.errors), "tcl_errors": len(root._ccl_tcl_errors)})
        rig.check("the pinned roster fixture stayed unchanged",
                  hashlib.sha256(Path(g.FIXTURE).read_bytes()).digest() == fixture_before)
    finally:
        # No Rig.close(): it pumps update() after closing. Use the ordinary app
        # close handler directly, after mainloop has returned.
        app.on_close()
    print("%d of %d checks passed" % (sum(rig.results), len(rig.results)))
    return 0 if rig.results and all(rig.results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
