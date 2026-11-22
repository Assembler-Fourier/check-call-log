"""Narrow header and status layout, using the isolated invisible window rig. No live roster or messages."""
import os
import sys
import time
import tkinter.font as tkfont

from drive6 import Rig, HOME

total = []
fixture = os.path.join(HOME, "tool", "tests", "roster-2026-10-19.xlsx")


def rect(widget):
    return (widget.winfo_rootx(), widget.winfo_rooty(), widget.winfo_width(), widget.winfo_height())


def contains(outer, inner):
    x, y, w, h = rect(outer)
    a, b, c, d = rect(inner)
    return x <= a and y <= b and a + c <= x + w and b + d <= y + h


for theme in ("light", "dark"):
    rig = Rig("layout-" + theme, now="2026-10-23 21:40", theme=theme, roster=fixture,
              settings={"auto_green_for": [], "chooser": True, "preview": False})
    watchdog = rig.root.after(30000, rig.app.on_close)
    try:
        rig.app.auto_run = lambda: None       # this test exercises presentation, never a colour request
        rig.show("Lena Drayton", "2026-10-23")
        saved = []
        rig.mod.save_settings = lambda data: saved.append(dict(data))
        choosing = rig.app.choosing
        small = rig.root.minsize()[0]
        for width in (small, rig.app.px(1480), small):
            rig.root.geometry("%dx960+-2600+40" % width)
            rig.pump(180)
            for state in ("off", "on", "waiting"):
                rig.app.auto_for = set() if state == "off" else {"Lena Drayton"}
                rig.app.quiet_until = time.time() + 60 if state == "waiting" else 0
                rig.app.show_auto()
                rig.pump(100)
                button = rig.app.buttons["auto"]
                rig.check("%s %d: full Auto green %s label fits" % (theme, width, state),
                          button.winfo_width() >= int(button["width"]), (button.text, button.winfo_width(), button["width"]))
            a, b = rect(rig.app.tally), rect(rig.app.files_label)
            separated = a[0] + a[2] + rig.app.px(8) <= b[0] or a[1] + a[3] <= b[1]
            rig.check("%s %d: call count and file status have separate space" % (theme, width), separated, (a, b))
            for label, widget in (("call count", rig.app.tally), ("file status", rig.app.files_label)):
                rig.check("%s %d: %s stays within its status frame" % (theme, width, label),
                          contains(rig.app.file_status, widget), (rect(rig.app.file_status), rect(widget)))
                rig.check("%s %d: %s stays within the summary panel" % (theme, width, label),
                          contains(rig.app.summary_panel.inner, widget), (rect(rig.app.summary_panel.inner), rect(widget)))
                font = tkfont.Font(font=widget.cget("font"))
                text_width = font.measure(widget.cget("text")) + 2 * int(widget.cget("padx"))
                text_height = font.metrics("linespace") + 2 * int(widget.cget("pady"))
                rig.check("%s %d: full %s text fits horizontally and vertically" % (theme, width, label),
                          text_width <= widget.winfo_width() and text_height <= widget.winfo_height(),
                          (widget.cget("text"), text_width, text_height, widget.winfo_width(), widget.winfo_height()))
            rig.check("%s %d: summary panel height follows stacked status" % (theme, width),
                      int(rig.app.summary_panel["height"]) == rig.app.px(148 if rig.app.status_stacked else 124))
            for key in ("print", "folder"):
                button = rig.app.buttons[key]
                rig.check("%s %d: %s button remains fully visible" % (theme, width, key),
                          button.winfo_width() >= int(button["width"]), (button.winfo_width(), button["width"]))
            rig.check("%s %d: resize preserves the chooser preference" % (theme, width), rig.app.choosing == choosing and not saved)
            if width > small:
                rig.check("%s: wide window restores the full chooser label" % theme,
                          rig.app.buttons["change"].text == "Hide controller and day", rig.app.buttons["change"].text)
        wa = rig.mod.whatsapp
        withdrawn = wa.story({"name": "Test Guard", "response": "Ok", "heard": {
            "response": "", "how": "check", "taken_back": True, "first": "Yeah all good",
            "said": "Wrong text sorry", "at": "21:41"}})
        rig.check("%s: withdrawn evidence keeps both quotes without claiming a roster colour" % theme,
                  all(text in withdrawn for text in ("Yeah all good", "Wrong text sorry", "21:41", "review the response"))
                  and "white" not in withdrawn.lower() and "green" not in withdrawn.lower(), withdrawn)
        waiting = wa.story({"name": "Test Guard", "call_text": "21:40", "response": "", "default_response": ""})
        rig.check("%s: no-reply evidence does not promise the name is white" % theme,
                  "waiting for confirmation" in waiting and "white" not in waiting.lower(), waiting)
        rig.check("%s: no Tk callback errors" % theme, not rig.errors, rig.errors[:1])
    finally:
        try:
            rig.root.after_cancel(watchdog)
        except Exception:
            pass
        total += rig.close()

print("\n%d of %d checks passed" % (sum(total), len(total)))
sys.exit(0 if all(total) else 1)
