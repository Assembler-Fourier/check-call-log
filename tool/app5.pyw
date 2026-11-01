"""Window for the control-room logs. Opened by the desktop shortcut or "Make Call Log.bat".

Left: whose shift and which day (it opens on whoever is at the desk now). One button then makes the
Daily Check Call Log, the Campus Patrol Log and the Shift Log for that shift; each has its own tab on
the right. Times and patrols can be corrected by clicking them, and every change is written to the
PDF at once. All the roster reading and file writing is done by calllog.py and forms.py; this file
is only the window. It starts no other program.
"""
import calendar
import datetime
import json
import os
import queue
import re
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import font as tkfont, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import calllog  # noqa: E402
import forms  # noqa: E402

SETTINGS = os.path.join(calllog.CACHE, "settings.json")
ICON = os.path.join(HERE, "app.ico")
VERSION = "5.1"
ROSTER_FRESH = 300                # seconds a downloaded roster is reused before fetching it again
FONT, FONT_BOLD = "Segoe UI", "Segoe UI Semibold"
ICON_FONT = "Segoe MDL2 Assets"            # Windows' own icon font; on a PC without it the buttons simply have no icons
# Two looks, light and dark. Every name below becomes a module-level colour (see use_theme) that the widgets read as
# they are built; changing the look switches the table and builds the widgets again (App.build).
THEMES = {
    "light": dict(
        NAVY="#14263d", NAVY_SOFT="#a9b8cc", HEAD_RULE="#3a4d68", HEAD_HOVER="#223650",          # the band across the top
        ACCENT="#1f6feb", ACCENT_HOVER="#1a5fd0", ACCENT_DOWN="#174fb0", ACCENT_OFF="#a9c4f5", ACCENT_OFF_INK="#ffffff",
        ON_ACCENT="#ffffff",
        TINT="#e6eefc", TINT_EDGE="#9db8ea", LINK_HOVER="#dbe6fa",     # the quiet "this one is chosen" look; solid blue is kept for the main button
        PAPER="#f1f3f6", CARD="#ffffff", LINE="#d6dce4", INNER="#e3e7ee", FIELD="#c9d1dc", BAR="#e9edf2",
        INK="#1b2330", BODY="#47515f", MUTED="#647081", FAINT="#8a94a3", TODO="#6b7686",
        HINT="#5b6677",                                                # small print that still has to be readable at 4 a.m.
        HOVER="#f3f6fb", BAND="#e9eff8", HEAD="#f7f8fa", SELECT="#d6e4fb", QUIET_HOVER="#e3e8ef",
        OFF="#f6f7f9", OFF_INK="#a3acb9", PILL="#e8edf4",
        GOOD="#1a7f5a", GOOD_TINT="#e3f5ec", WARN="#b76e00", BAD="#b3261e", BAD_TINT="#fdecea", EDITED="#fff6d6", LIVE="#2da44e",
        NIGHT_BAR="#14263d", DAY_BAR="#e0a100", OTHER_BAR="#dfe5ee",   # the calendar's shift bars; someone else's shift on the day's bar
        MARK="#14263d", TRACK="#d5dbe6", TRACK_GONE="#9db4d6", TICK="#aab4c2", CALL_TODO="#aab8cc", EVENT="#c26a00",   # the shift strip
        THUMB="#c3cbd6", THUMB_ON="#9aa5b4",
        SUNK="#e3e8ef", SHADOW="#c3cad3", PAGE="#ffffff", PAGE_EDGE="#b8c0ca", PAGE_GREY="#dedede"),     # the sheet of paper in a preview
    "dark": dict(
        NAVY="#0a1018", NAVY_SOFT="#8d9db4", HEAD_RULE="#263247", HEAD_HOVER="#1a2636",
        ACCENT="#2f6fe8", ACCENT_HOVER="#3d7df2", ACCENT_DOWN="#8db6ff", ACCENT_OFF="#243755", ACCENT_OFF_INK="#7f8da3",
        ON_ACCENT="#ffffff",
        TINT="#1a2c49", TINT_EDGE="#35568c", LINK_HOVER="#213a61",
        PAPER="#0f151e", CARD="#171f2b", LINE="#283445", INNER="#232e3e", FIELD="#3a475b", BAR="#121a25",
        INK="#e7ecf3", BODY="#bcc6d4", MUTED="#96a3b6", FAINT="#6c7a8e", TODO="#8793a6",
        HINT="#9ba8ba",
        HOVER="#1e2938", BAND="#1c2635", HEAD="#1d2736", SELECT="#234577", QUIET_HOVER="#222e3f",
        OFF="#1a222e", OFF_INK="#566375", PILL="#253142",
        GOOD="#41b883", GOOD_TINT="#15362a", WARN="#e3a53f", BAD="#f47b70", BAD_TINT="#3d1f1e", EDITED="#3b3420", LIVE="#3fb950",
        NIGHT_BAR="#7f9fe0", DAY_BAR="#e0a100", OTHER_BAR="#2a3647",
        MARK="#e7ecf3", TRACK="#283445", TRACK_GONE="#48618c", TICK="#52607a", CALL_TODO="#4b5a73", EVENT="#f0a63a",
        THUMB="#354256", THUMB_ON="#4f6079",
        SUNK="#0b1017", SHADOW="#05080c", PAGE="#e6e3db", PAGE_EDGE="#3a475b", PAGE_GREY="#c9c6be"),     # paper a little dimmed, ink still black
}
THEME = "light"
globals().update(THEMES[THEME])


def use_theme(name):
    """Make "light" or "dark" the look that widgets built from now on will have."""
    global THEME
    THEME = name if name in THEMES else "light"
    globals().update(THEMES[THEME])


AVATARS = ("#3b6ea8", "#4a8c7a", "#8a6bb0", "#b07a3b", "#5f7388", "#a85b6e")
ICONS = {"phone": "\ue717", "calendar": "\ue787", "folder": "\ue8b7", "print": "\ue749", "refresh": "\ue72c", "add": "\ue710",
         "delete": "\ue74d", "cancel": "\ue711", "check": "\ue73e", "person": "\ue77b", "clock": "\ue823", "warning": "\ue7ba",
         "info": "\ue946", "document": "\ue8a5", "pdf": "\uea90", "table": "\uf0e2", "clear": "\ue894", "walk": "\ue726",
         "list": "\ue8fd", "sync": "\ue895", "prev": "\ue76b", "next": "\ue76c", "moon": "\ue708", "sun": "\ue706"}
KINDS = (("calls", "Check calls", calllog.build, calllog.write_outputs),
         ("patrols", "Patrols", calllog.build_patrols, calllog.write_patrols),
         ("shift", "Shift log", calllog.build_shift, calllog.write_shift))


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        os.makedirs(calllog.CACHE, exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def card(parent, title=None, icon=None):
    """A white panel with a hairline border; returns (panel, inside)."""
    outer = tk.Frame(parent, bg=CARD, highlightbackground=LINE, highlightthickness=1)
    if title:
        head = tk.Frame(outer, bg=CARD)
        head.pack(fill="x", padx=14, pady=(11, 6))
        if ICONS.get(icon):
            tk.Label(head, text=ICONS[icon], font=(ICON_FONT, 9), fg=FAINT, bg=CARD).pack(side="left", padx=(0, 6))
        tk.Label(head, text=title.upper(), font=(FONT_BOLD, 8), fg=MUTED, bg=CARD).pack(side="left")
    inner = tk.Frame(outer, bg=CARD)
    inner.pack(fill="both", expand=True, padx=14, pady=(0 if title else 12, 12))
    return outer, inner


def pointer_inside(widget):
    """Is the mouse over this widget or anything inside it? (Moving onto a child must not count as leaving.)"""
    under = widget.winfo_containing(*widget.winfo_pointerxy())
    while under is not None:
        if under is widget:
            return True
        under = under.master
    return False


def round_bar(canvas, x0, x1, y, h, fill):
    """A horizontal bar with rounded ends."""
    if x1 - x0 <= h:
        canvas.create_oval(x0, y, x0 + h, y + h, fill=fill, outline="")
        return
    canvas.create_rectangle(x0 + h / 2.0, y, x1 - h / 2.0, y + h, fill=fill, outline="")
    canvas.create_oval(x0, y, x0 + h, y + h, fill=fill, outline="")
    canvas.create_oval(x1 - h, y, x1, y + h, fill=fill, outline="")


class Button(tk.Frame):
    """A flat button with an optional icon. kind: 'primary' (filled blue), 'plain' (the card's colour with an edge),
    'tint' (pale blue, for the thing most often wanted), 'quiet' (bare), 'link' (bare, blue) or 'head' (on the top band)."""

    @staticmethod
    def looks(kind):
        """The colours of one kind of button in the look in use now."""
        #        back    text       icon       edge    hover back    hover edge    off back    off text
        return {"primary": (ACCENT, ON_ACCENT, ON_ACCENT, ACCENT, ACCENT_HOVER, ACCENT_HOVER, ACCENT_OFF, ACCENT_OFF_INK),
                "plain": (CARD, INK, MUTED, FIELD, HOVER, ACCENT, OFF, OFF_INK),
                "tint": (TINT, ACCENT_DOWN, ACCENT_DOWN, TINT_EDGE, LINK_HOVER, ACCENT, OFF, OFF_INK),
                "quiet": (None, BODY, MUTED, None, QUIET_HOVER, QUIET_HOVER, None, OFF_INK),
                "link": (None, ACCENT_DOWN, ACCENT_DOWN, None, LINK_HOVER, LINK_HOVER, None, OFF_INK),
                "head": (None, NAVY_SOFT, NAVY_SOFT, None, HEAD_HOVER, HEAD_HOVER, None, OFF_INK)}[kind]

    def __init__(self, parent, text, command, icon=None, kind="plain", size=10, pad=(12, 5), back=None):
        back = back or CARD
        base, self.fg, self.icon_fg, edge, self.hover_bg, self.hover_edge, off, self.off_fg = self.looks(kind)
        self.bg, self.edge, self.off_bg = base or back, edge or back, off or back
        self.off_edge = INNER if kind in ("plain", "tint") else self.off_bg
        self.command, self.enabled = command, True
        super().__init__(parent, bg=self.bg, highlightthickness=1, highlightbackground=self.edge, cursor="hand2")
        self.inner = tk.Frame(self, bg=self.bg)
        self.inner.pack(padx=pad[0], pady=pad[1])
        self.glyph = None
        if ICONS.get(icon):
            self.glyph = tk.Label(self.inner, text=ICONS[icon], font=(ICON_FONT, size), bg=self.bg, fg=self.icon_fg)
            self.glyph.pack(side="left", padx=(0, 7))
        self.label = tk.Label(self.inner, text=text, font=(FONT_BOLD if kind == "primary" else FONT, size), bg=self.bg, fg=self.fg)
        self.label.pack(side="left")
        for w in [self, self.inner, self.label] + ([self.glyph] if self.glyph else []):
            w.bind("<Button-1>", lambda e: self.command() if self.enabled else None)
            w.bind("<Enter>", lambda e: self.paint(True))
            w.bind("<Leave>", lambda e: self.paint(pointer_inside(self)))

    def configure(self, cnf=None, **kw):
        """Like any widget's configure, plus state='normal'/'disabled' and text=..."""
        if isinstance(cnf, dict):
            kw, cnf = dict(cnf, **kw), None
        mine = "state" in kw or "text" in kw
        if "state" in kw:
            self.enabled = kw.pop("state") != "disabled"
            self.paint()
        if "text" in kw:
            self.label.configure(text=kw.pop("text"))
        return None if mine and not kw else super().configure(cnf, **kw)

    config = configure

    def paint(self, hover=False):
        if not self.enabled:
            bg, edge, fg, icon_fg = self.off_bg, self.off_edge, self.off_fg, self.off_fg
        elif hover:
            bg, edge, fg, icon_fg = self.hover_bg, self.hover_edge, self.fg, self.icon_fg
        else:
            bg, edge, fg, icon_fg = self.bg, self.edge, self.fg, self.icon_fg
        super().configure(bg=bg, highlightbackground=edge, cursor="hand2" if self.enabled else "")
        self.inner.configure(bg=bg)
        self.label.configure(bg=bg, fg=fg)
        if self.glyph:
            self.glyph.configure(bg=bg, fg=icon_fg)


class Check(tk.Frame):
    """A tick box drawn to match the rest: blue with a white tick when on."""

    def __init__(self, parent, text, variable, back=None, size=10):
        back = back or PAPER
        super().__init__(parent, bg=back, cursor="hand2")
        self.var = variable
        self.box = tk.Canvas(self, width=16, height=16, bg=back, highlightthickness=0)
        self.box.pack(side="left", padx=(0, 6))
        label = tk.Label(self, text=text, font=(FONT, size), bg=back, fg=INK)
        label.pack(side="left")
        for w in (self, self.box, label):
            w.bind("<Button-1>", lambda e: self.var.set(not self.var.get()))
        self.watch = variable.trace_add("write", lambda *a: self.draw())
        self.bind("<Destroy>", self.unwatch)         # the variable outlives this box when the window is made again
        self.draw()

    def unwatch(self, event):
        if event.widget is self:
            try:
                self.var.trace_remove("write", self.watch)
            except tk.TclError:
                pass

    def draw(self):
        self.box.delete("all")
        if self.var.get():
            self.box.create_rectangle(1, 1, 15, 15, fill=ACCENT, outline=ACCENT)
            self.box.create_line(4, 8, 7, 11, 12, 5, fill=ON_ACCENT, width=2, capstyle="round", joinstyle="round")
        else:
            self.box.create_rectangle(1, 1, 15, 15, fill=CARD, outline=FAINT)


class Chip(tk.Frame):
    """A choice: white when idle, tinted with a blue edge when it is the one chosen.
    It can carry an initials badge on the left and a green dot (someone on shift now)."""

    def __init__(self, parent, text, command, avatar=None, dot=False, side="center"):
        super().__init__(parent, bg=CARD, highlightthickness=1, highlightbackground=LINE, cursor="hand2")
        self.chosen = False
        self.inner = tk.Frame(self, bg=CARD)
        self.inner.pack(padx=8, pady=4, anchor=side)
        self.parts = [self.inner]
        if avatar:                                    # (initials, colour)
            badge = tk.Canvas(self.inner, width=24, height=24, bg=CARD, highlightthickness=0)
            badge.create_oval(1, 1, 23, 23, fill=avatar[1], outline="")
            badge.create_text(12, 12, text=avatar[0], fill="white", font=(FONT_BOLD, 8))
            badge.pack(side="left", padx=(0, 7))
            self.parts.append(badge)
        self.label = tk.Label(self.inner, text=text, font=(FONT, 10), bg=CARD, fg=INK, pady=2)
        self.label.pack(side="left")
        self.dot = tk.Canvas(self.inner, width=8, height=8, bg=CARD, highlightthickness=0)
        self.dot.create_oval(1, 1, 7, 7, fill=LIVE, outline="")
        self.parts += [self.label, self.dot]
        self.mark(dot)
        for w in [self] + self.parts:
            w.bind("<Button-1>", lambda e: command())
            w.bind("<Enter>", lambda e: self.paint(True))
            w.bind("<Leave>", lambda e: self.paint(pointer_inside(self)))

    def mark(self, on):
        """Show or hide the green dot."""
        self.dot.pack(side="left", padx=(6, 0)) if on else self.dot.pack_forget()

    def paint(self, hover=False):
        bg = TINT if self.chosen else HOVER if hover else CARD
        self.configure(bg=bg, highlightbackground=ACCENT if self.chosen else TINT_EDGE if hover else LINE)
        for w in self.parts:
            w.configure(bg=bg)
        self.label.configure(fg=ACCENT_DOWN if self.chosen else INK)

    def choose(self, on):
        self.chosen = on
        self.paint()


class Tab(tk.Frame):
    """One tab in the strip: icon, name, a count in a small pill, and a blue bar underneath when it is on show."""

    def __init__(self, parent, text, icon, command):
        super().__init__(parent, bg=CARD, cursor="hand2")
        row = tk.Frame(self, bg=CARD)
        row.pack(padx=14, pady=(10, 7))
        self.glyph = tk.Label(row, text=ICONS.get(icon, ""), font=(ICON_FONT, 10), bg=CARD, fg=MUTED)
        if ICONS.get(icon):
            self.glyph.pack(side="left", padx=(0, 7))
        self.label = tk.Label(row, text=text, font=(FONT_BOLD, 10), bg=CARD, fg=MUTED)
        self.label.pack(side="left")
        self.pill = tk.Canvas(row, width=28, height=18, bg=CARD, highlightthickness=0)
        self.bar = tk.Frame(self, bg=CARD, height=3)
        self.bar.pack(fill="x")
        self.active, self.count = False, None
        for w in (self, row, self.glyph, self.label, self.pill):
            w.bind("<Button-1>", lambda e: command())
            w.bind("<Enter>", lambda e: self.paint(True))
            w.bind("<Leave>", lambda e: self.paint(pointer_inside(self)))

    def choose(self, on):
        self.active = on
        self.paint()

    def set_count(self, count):
        """A number to show beside the name, or None for no pill."""
        self.count = count
        self.paint()

    def paint(self, hover=False):
        fg = INK if self.active or hover else MUTED
        self.label.configure(fg=fg)
        self.glyph.configure(fg=ACCENT if self.active else fg)
        self.bar.configure(bg=ACCENT if self.active else CARD)
        if self.count is None:
            self.pill.pack_forget()
            return
        text = str(self.count)
        wide = 20 + 7 * len(text)
        self.pill.configure(width=wide)
        self.pill.delete("all")
        round_bar(self.pill, 0, wide, 0, 18, TINT if self.active else PILL)
        self.pill.create_text(wide / 2.0, 9, text=text, font=(FONT_BOLD, 9), fill=ACCENT_DOWN if self.active else BODY)
        self.pill.pack(side="left", padx=(8, 0))


class Calendar(tk.Frame):
    """A month grid drawn on a canvas. The chosen controller's shift days are tinted with a bar underneath
    (dark for a night shift, amber for a day shift); a chosen day is a blue disc; today has a ring.
    The rows stretch a little to use spare height, and the key to the marks sits right under the last row."""
    ROW, TALLEST, TOP, KEY = 29, 42, 18, 22

    def __init__(self, parent, on_pick, shift_kind):
        super().__init__(parent, bg=CARD)
        self.on_pick, self.shift_kind = on_pick, shift_kind       # shift_kind(date) -> "day", "night" or None
        self.today = datetime.date.today()
        self.year, self.month = self.today.year, self.today.month
        self.chosen, self.cells, self.row = [], {}, self.ROW
        bar = tk.Frame(self, bg=CARD)
        bar.pack(fill="x")
        for name, plain, step, side in (("prev", "‹", -1, "left"), ("next", "›", 1, "right")):
            nav = tk.Label(bar, text=ICONS.get(name, plain), font=(ICON_FONT, 9) if ICONS.get(name) else (FONT, 14, "bold"),
                           bg=CARD, fg=MUTED, cursor="hand2", padx=9, pady=4)
            nav.pack(side=side)
            nav.bind("<Button-1>", lambda e, step=step: self.turn(step))
        self.title = tk.Label(bar, font=(FONT_BOLD, 10), bg=CARD, fg=INK)
        self.title.pack(expand=True)
        self.grid_ = tk.Canvas(self, bg=CARD, highlightthickness=0, width=280, height=self.TOP + 6 * self.ROW + self.KEY)
        self.grid_.pack(fill="both", expand=True)
        self.grid_.bind("<Configure>", lambda e: self.draw())
        self.grid_.bind("<Button-1>", self.click)
        self.draw()

    def turn(self, step):
        month = self.month - 1 + step
        self.year, self.month = self.year + month // 12, month % 12 + 1
        self.draw()

    def show(self, chosen):
        """Mark the chosen days. The month on view only changes when a newly chosen day is not in it."""
        chosen = list(chosen)
        if chosen and chosen != self.chosen and not any((d.year, d.month) == (self.year, self.month) for d in chosen):
            self.year, self.month = chosen[-1].year, chosen[-1].month
        self.chosen = chosen
        self.draw()

    def draw(self):
        c = self.grid_
        c.delete("all")
        self.title.configure(text=datetime.date(self.year, self.month, 1).strftime("%B %Y"))
        weeks = calendar.Calendar(0).monthdatescalendar(self.year, self.month)
        cw = max(c.winfo_width(), 140) / 7.0
        room = (c.winfo_height() - self.TOP - self.KEY) / float(len(weeks))
        row = self.row = min(self.TALLEST, max(self.ROW, room))
        for col, wd in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            c.create_text((col + 0.5) * cw, 8, text=wd, font=(FONT, 8), fill=HINT)
        self.cells = {}
        for r, week in enumerate(weeks):
            for col, d in enumerate(week):
                if d.month != self.month:
                    continue
                self.cells[(r, col)] = d
                x0, y0 = col * cw, self.TOP + r * row
                cx, cy = x0 + cw / 2.0, y0 + row / 2.0 - 2
                kind, picked = self.shift_kind(d), d in self.chosen
                if kind:
                    c.create_rectangle(x0 + 2, y0 + 1, x0 + cw - 2, y0 + row - 1, fill=TINT, outline="")
                    c.create_rectangle(x0 + 8, y0 + row - 4, x0 + cw - 8, y0 + row - 1,
                                       fill=NIGHT_BAR if kind == "night" else DAY_BAR, outline="")
                if picked:
                    c.create_oval(cx - 11, cy - 11, cx + 11, cy + 11, fill=ACCENT, outline="")
                elif d == self.today:
                    c.create_oval(cx - 11, cy - 11, cx + 11, cy + 11, outline=ACCENT, width=2)
                c.create_text(cx, cy, text=str(d.day), font=(FONT_BOLD if kind or picked else FONT, 10),
                              fill=ON_ACCENT if picked else INK if kind else MUTED)
        # what the marks mean
        x, y = 2, self.TOP + len(weeks) * row + 12
        for colour, text in ((NIGHT_BAR, "night"), (DAY_BAR, "day shift")):
            c.create_rectangle(x, y - 1, x + 12, y + 2, fill=colour, outline="")
            x = c.bbox(c.create_text(x + 16, y, text=text, anchor="w", font=(FONT, 8), fill=HINT))[2] + 12
        c.create_oval(x, y - 5, x + 10, y + 5, outline=ACCENT, width=2)
        x = c.bbox(c.create_text(x + 14, y, text="today", anchor="w", font=(FONT, 8), fill=HINT))[2] + 12
        c.create_oval(x, y - 3, x + 7, y + 4, fill=LIVE, outline="")
        c.create_text(x + 11, y, text="on now", anchor="w", font=(FONT, 8), fill=HINT)

    def click(self, event):
        cw = max(self.grid_.winfo_width(), 140) / 7.0
        d = self.cells.get((int((event.y - self.TOP) // self.row), int(event.x // cw)))
        if d:
            self.on_pick([d], add=bool(event.state & 0x0004))     # Ctrl adds another day


class Timeline(tk.Canvas):
    """The shift drawn as a time axis: blue bars for the check calls (gathered into five-minute slots, taller
    when more fall together), a green block for each patrol, hours underneath, and a marker for the time now."""

    def __init__(self, parent):
        super().__init__(parent, bg=CARD, highlightthickness=0, height=58, width=300)
        self.data = None       # {"date", "start", "end" (minutes from that date's midnight), "calls": [(minute, made?)], "patrols": [(a, b)]}
        self.bind("<Configure>", lambda e: self.draw())

    def show(self, data):
        self.data = data
        self.draw()

    def draw(self):
        self.delete("all")
        w = self.winfo_width()
        if w < 120:
            return
        if not self.data or self.data["end"] <= self.data["start"]:
            self.create_text(w / 2.0, 29, text="The shift is drawn here once its logs are made: the calls, the patrols, the shift log and the time now.",
                             font=(FONT, 9), fill=HINT)
            return
        d = self.data
        a, b, x0, x1, y = d["start"], d["end"], 16.0, w - 16.0, 31
        x = lambda minute: x0 + (min(max(minute, a), b) - a) * (x1 - x0) / float(b - a)
        now = (datetime.datetime.now() - datetime.datetime.combine(d["date"], datetime.time())).total_seconds() / 60.0
        self.create_rectangle(x0, y, x1, y + 3, fill=TRACK, outline="")
        if now > a:                                   # the part of the shift already gone
            self.create_rectangle(x0, y, x(now), y + 3, fill=TRACK_GONE, outline="")
        for hour in range(int(-(-a // 60)), int(b // 60) + 1):
            self.create_line(x(hour * 60), y + 7, x(hour * 60), y + 10, fill=TICK)
            if hour % 2 == 0:
                self.create_text(x(hour * 60), y + 19, text="%02d" % (hour % 24), font=(FONT_BOLD if hour % 24 == 0 else FONT, 8), fill=HINT)
        for start, finish in d["patrols"]:
            self.create_rectangle(x(start), y - 3, max(x(finish), x(start) + 6), y + 6, fill=GOOD, outline="")
        slots = {}                                    # five-minute slot -> [how many calls, all of them made?]
        for minute, made in d["calls"]:
            slot = slots.setdefault(int(minute // 5), [0, True])
            slot[0], slot[1] = slot[0] + 1, slot[1] and made
        for slot, (count, made) in slots.items():
            tall = 5 if count == 1 else 8 if count == 2 else 12
            self.create_rectangle(x(slot * 5), y - 5 - tall, x(slot * 5) + 4, y - 5, fill=ACCENT if made else CALL_TODO, outline="")
        for minute in d.get("events", ()):            # a line in the shift log: a small diamond on the axis
            xe = x(minute)
            self.create_polygon(xe, y - 4, xe + 5, y + 1, xe, y + 6, xe - 5, y + 1, fill=EVENT, outline=CARD)
        # what the marks mean, top right
        self.create_text(w - 16, 7, text="patrol", anchor="e", font=(FONT, 8), fill=HINT)
        self.create_rectangle(w - 66, 3, w - 52, 11, fill=GOOD, outline="")
        self.create_text(w - 76, 7, text="check calls", anchor="e", font=(FONT, 8), fill=HINT)
        self.create_rectangle(w - 140, 2, w - 136, 12, fill=ACCENT, outline="")
        self.create_text(w - 150, 7, text="shift log", anchor="e", font=(FONT, 8), fill=HINT)
        self.create_polygon(w - 204, 2, w - 199, 7, w - 204, 12, w - 209, 7, fill=EVENT, outline="")
        if a <= now <= b:
            xn = x(now)
            self.create_line(xn, y - 19, xn, y + 10, fill=MARK)
            self.create_polygon(xn - 4, y - 23, xn + 4, y - 23, xn, y - 17, fill=MARK, outline="")
            self.create_text(min(max(xn, 38), w - 270), 7, text="now " + datetime.datetime.now().strftime("%H:%M"),
                             font=(FONT_BOLD, 8), fill=MARK)


class CanvasPage:
    """The same page forms.py draws on for the PDF, drawn on a canvas instead (millimetres -> pixels)."""

    def __init__(self, canvas, scale, ox, oy):
        self.c, self.s, self.ox, self.oy = canvas, scale, ox, oy

    def line(self, x1, y1, x2, y2, weight):
        self.c.create_line(self.ox + x1 * self.s, self.oy + y1 * self.s, self.ox + x2 * self.s, self.oy + y2 * self.s,
                           width=max(1, round(weight * self.s)), fill="#000000")

    def box(self, x, y, w, h, weight=None, grey=None):
        at = (self.ox + x * self.s, self.oy + y * self.s, self.ox + (x + w) * self.s, self.oy + (y + h) * self.s)
        if grey is not None:
            self.c.create_rectangle(*at, fill=PAGE_GREY, outline="")
        if weight:
            self.c.create_rectangle(*at, outline="#000000", width=max(1, round(weight * self.s)))

    def text(self, x, y, h, text, font="F1", size=10.5, align="left", room=None, angle=0):
        size = forms.fit(text, font, size, room)
        face = ("Arial", -max(6, round(size * 0.3528 * self.s))) + {"F1": (), "F2": ("bold",), "F3": ("italic",)}[font]
        self.c.create_text(self.ox + x * self.s, self.oy + (y + h / 2) * self.s, text=text, font=face, fill="#000000",
                           anchor={"left": "w", "right": "e", "center": "center"}[align], angle=-angle)


class Preview(tk.Frame):
    """A sheet of paper showing a form as it will print: fitted to the width, scrolled with the mouse wheel."""

    def __init__(self, parent):
        super().__init__(parent, bg=SUNK, highlightbackground=LINE, highlightthickness=1)
        self.canvas = tk.Canvas(self, bg=SUNK, highlightthickness=0, width=200, height=90)
        bar = ttk.Scrollbar(self, orient="vertical", style="Slim.Vertical.TScrollbar", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.draw, self.hint = None, ""
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

    def show(self, draw, hint=""):
        """draw(page) paints the form; None shows the hint instead."""
        self.draw, self.hint = draw, hint
        self.redraw()

    def redraw(self):
        c = self.canvas
        c.delete("all")
        wide = c.winfo_width()
        if wide < 60:
            return
        if not self.draw:
            c.configure(scrollregion=(0, 0, wide, 10))
            c.create_text(wide / 2, 60, text=self.hint, font=(FONT, 10), fill=HINT, width=wide - 60, justify="center")
            return
        margin = 14
        scale = (wide - 2 * margin) / 210.0
        tall = 297 * scale
        c.create_rectangle(margin + 3, margin + 3, wide - margin + 3, margin + tall + 3, fill=SHADOW, outline="")
        c.create_rectangle(margin, margin, wide - margin, margin + tall, fill=PAGE, outline=PAGE_EDGE)
        self.draw(CanvasPage(c, scale, margin, margin))
        c.configure(scrollregion=(0, 0, wide, tall + 2 * margin))


class CellEditor:
    """Click-to-type on some columns of a table. The owner says what a cell holds and saves what was typed.

    Enter saves and moves on - down the column (enter="down") or along the row (enter="along"), Tab and Shift+Tab
    move along the row and on to the next one, the arrow keys move up and down, Esc leaves the cell as it was.
    A row is known by its key, never by where it sits, so the box follows a row that moves when the list is
    put back in time order. A value that cannot be right keeps the box open, with the reason shown beside it."""

    def __init__(self, tree, fields, text_of, commit, status, busy, enter="down", left=None):
        # fields: the columns that can be typed in, left to right (a field is named after its column);
        # text_of(key, field) -> str; commit(key, field, text) -> why the text cannot be right, or None;
        # left(key) is told when the box leaves a row altogether
        self.tree, self.order, self.text_of, self.commit, self.status, self.busy = tree, list(fields), text_of, commit, status, busy
        self.enter, self.left = enter, left or (lambda key: None)
        self.keys = {}                               # table row -> what it stands for (set whenever the table is redrawn)
        self.box = None                              # (entry, key, field) while a cell is open
        self.tip = None                              # (label, timer) while a reason is on show
        self.refused = None                          # why the value typed last was turned down, if it was
        tree.bind("<Button-1>", self.click)
        tree.before_scroll = self.close
        tree.bind("<MouseWheel>", lambda e: self.close(), add="+")
        tree.bind("<Configure>", lambda e: self.reposition(), add="+")

    def item(self, key):
        return next((i for i, k in self.keys.items() if k == key), None)

    def rows(self):
        """The rows that stand for something, top to bottom."""
        return [self.keys[i] for i in self.tree.get_children() if i in self.keys]

    def click(self, event):
        column = self.tree.identify_column(event.x)
        field = self.tree.column(column, "id") if column else None
        key = self.keys.get(self.tree.identify_row(event.y))
        wanted = field in self.order and key is not None and self.tree.identify_region(event.x, event.y) == "cell" and not self.busy()
        self.close(to=key if wanted else None)       # clicking away keeps what was typed
        if not wanted:
            return None
        self.open(key, field)
        return "break"

    def open(self, key, field, text=None):
        """Put a typing box over a cell (text: what to show in it instead of the cell's own value). True if it opened."""
        item = self.item(key)
        if not item:
            return False
        self.tree.see(item)
        self.tree.update_idletasks()
        at = self.tree.bbox(item, field)
        if not at:
            return False
        side = "left" if str(self.tree.column(field, "anchor")) == "w" else "center"
        entry = tk.Entry(self.tree, font=(FONT, 10), justify=side, relief="flat", bg=CARD, fg=INK, insertbackground=INK,
                         selectbackground=ACCENT, selectforeground=ON_ACCENT, highlightthickness=2, highlightbackground=ACCENT,
                         highlightcolor=ACCENT)
        entry.insert(0, self.text_of(key, field) if text is None else text)
        entry.select_range(0, "end")
        entry.icursor("end")
        entry.place(x=at[0], y=at[1], width=at[2], height=at[3])
        entry.focus_set()
        self.box = (entry, key, field)
        self.tree.selection_set(item)                # the row being typed in is the chosen one (Remove acts on it)
        for event, move in (("<Return>", "enter"), ("<KP_Enter>", "enter"), ("<Tab>", "next"), ("<Shift-Tab>", "back"),
                            ("<Down>", "down"), ("<Up>", "up")):
            entry.bind(event, lambda e, move=move: self.close(move=move) or "break")
        entry.bind("<Escape>", lambda e: self.close(cancel=True) or "break")
        entry.bind("<FocusOut>", lambda e: self.close())
        entry.bind("<Key>", lambda e: self.hide_tip())             # typing again takes the reason away
        return True

    def close(self, cancel=False, move=None, to=None):
        """Shut the open cell, saving it unless cancelled, then go where the key that was pressed says (move).
        to: the row a click is about to open the box on, so the owner is not told that row was left.
        False if the value was refused; with a move (a key, or "hold" = just save it) the box then stays open, saying why."""
        if not self.box:
            return True
        entry, key, field = self.box
        self.box = None
        text = entry.get()
        entry.destroy()
        self.hide_tip()
        # where the key leads is settled now, from the list as it stood when the key was pressed: the change about
        # to be saved may put the rows in another order, and "the next row" must still mean the one that was next
        target = self.target(key, field, move) if move else None
        self.refused = None
        if not cancel:
            problem = self.commit(key, field, text)
            if problem:
                problem = self.refused = problem[:1].upper() + problem[1:]
                self.status(problem)
                self.tree.bell()
                if move and self.open(key, field, text):     # stay in the cell, with what was typed
                    self.show_tip(problem, key, field)
                    return False
                self.show_tip(problem, key, field)
        if (target[0] if target else to) != key:
            self.left(key)
            if not self.item(key):                   # the row went too (a new line nothing was typed in): so does the note
                self.hide_tip()
        if not (target and self.open(*target)) and (move or cancel):
            self.tree.focus_set()                    # typing ended with a key: the table keeps the keyboard, so one
        return not self.refused                      # more Enter does not reach the window's "make the logs" key

    def target(self, key, field, move):
        """The cell a key leads to from this one, as (key, field), or None to stop typing."""
        rows = self.rows()
        if move == "hold" or key not in rows or field not in self.order:
            return None
        at, col = rows.index(key), self.order.index(field)
        if move in ("down", "up") or (move == "enter" and self.enter == "down"):
            at += -1 if move == "up" else 1
        elif move == "back":
            at, col = (at, col - 1) if col else (at - 1, len(self.order) - 1)
        elif col + 1 < len(self.order):              # along the row
            col += 1
        elif move == "enter":                        # Enter in the last cell of a row: that row is done
            return None
        else:                                        # Tab in the last cell: on to the next row
            at, col = at + 1, 0
        return (rows[at], self.order[col]) if 0 <= at < len(rows) else None

    def reposition(self):
        """Keep the box over its cell when the table is redrawn or resized underneath it."""
        if not self.box:
            return
        entry, key, field = self.box
        try:
            item = self.item(key)
            if not item:
                self.close(cancel=True)              # its row is no longer there
                return
            self.tree.update_idletasks()
            if not self.box or self.box[0] is not entry:      # closed while the table settled
                return
            at = self.tree.bbox(item, field)
            if not at:
                self.tree.see(item)
                self.tree.update_idletasks()
                at = self.tree.bbox(item, field)
            if at:                                   # (a table that is not on show has no place for it: the box waits where it is)
                entry.place(x=at[0], y=at[1], width=at[2], height=at[3])
                entry.lift()
                if self.tree.selection() != (item,):
                    self.tree.selection_set(item)
        except tk.TclError:
            self.box = None

    def show_tip(self, text, key, field):
        """Say why a value was refused in a small note right under the cell (above it at the bottom of the table)."""
        self.hide_tip()
        try:
            item = self.item(key)
            self.tree.update_idletasks()
            at = self.tree.bbox(item, field) if item else None
            if not at:
                return
            wide, tall = self.tree.winfo_width(), self.tree.winfo_height()
            tip = tk.Label(self.tree, text=text, font=(FONT, 9), bg=BAD_TINT, fg=BAD, padx=7, pady=2, justify="left",
                           wraplength=max(120, wide - 24), highlightthickness=1, highlightbackground=BAD)
            y = at[1] + at[3] + 2
            if y + tip.winfo_reqheight() > tall:
                y = max(0, at[1] - tip.winfo_reqheight() - 2)
            tip.place(x=max(2, min(at[0], wide - tip.winfo_reqwidth() - 2)), y=y)
            tip.lift()
            self.tip = (tip, self.tree.after(7000, self.hide_tip))
        except tk.TclError:
            self.tip = None

    def hide_tip(self):
        if self.tip:
            tip, timer = self.tip
            self.tip = None
            try:
                self.tree.after_cancel(timer)
                tip.destroy()
            except tk.TclError:
                pass


class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.packs = []                              # what the last run made: one dict per day (see work())
        self.busy = self.closing = self.gone = False
        self.made_roster = None                      # the roster copy the logs on show were built from
        self.made_as = ""                            # the controller name the last run was started with
        self.view, self.view_at = None, -1.0         # the roster the window draws from (window thread only)
        self.roster, self.roster_at, self.roster_note = None, 0.0, ""   # the latest download (worker threads, under the lock)
        self.roster_lock = threading.Lock()          # one download at a time
        self.touched_name = self.touched_date = False
        self.out_dir = os.environ.get("CCL_OUT") or None
        if os.environ.get("CCL_EDITS"):               # automated checks keep their hand corrections apart from the real ones
            calllog.EDITS = os.environ["CCL_EDITS"]
        settings = load_settings()

        # open on whoever is at the desk, by the usual week; corrected from the roster once it has loaded
        self.name, first_day = calllog.at_the_desk(datetime.datetime.now(), calllog.usual_duty)
        self.dates = [first_day]

        root.title("Control Room Logs %s - NORTHWIND Security" % VERSION)
        try:
            root.iconbitmap(default=ICON)
        except tk.TclError:
            pass
        if ICON_FONT not in tkfont.families():       # no icon font on this PC: plain text buttons, nothing else changes
            ICONS.clear()
        self.scale = root.winfo_fpixels("1i") / 96.0
        wide = min(self.px(1200), root.winfo_screenwidth() - 60)
        tall = min(self.px(960), root.winfo_screenheight() - 90)      # tall enough for the form previews, never taller than the screen
        root.geometry("%dx%d" % (wide, tall))
        root.minsize(min(self.px(1060), wide), min(self.px(720), tall))

        # everything the window knows apart from its widgets lives here, so a change of look can make the widgets afresh
        self.tab = "calls"
        self.fresh = None                            # a shift log line just added that nothing has been typed in yet: (day, id)
        self.extras_job, self.filling = None, False
        self.patrol_day, self.day_rows = None, {}    # the day the patrol buttons last worked on; table rows that stand for a whole day
        self.status, self.status_kind, self.status_shown = tk.StringVar(value="Ready."), None, ("", "ok")
        self.roster_info = tk.StringVar(value="Roster: loading...")
        self.other, self.picked = tk.StringVar(), tk.StringVar()
        self.headline = tk.StringVar(value="Nothing made yet")
        self.subline = tk.StringVar(value="Check the controller and the day on the left, then press Make logs.")
        self.next_call = tk.StringVar()                # shown on a line of its own, only when there is something to say
        self.supervisor, self.incoming = tk.StringVar(), tk.StringVar()
        wanted = settings.get("docs") or [k for k, _, _, _ in KINDS]
        self.docs = {kind: tk.BooleanVar(value=kind in wanted) for kind, _, _, _ in KINDS}
        self.auto_open = tk.BooleanVar(value=settings.get("auto_open", True))
        self.said = [("info", "Tinted days on the calendar are the chosen controller's shifts; Ctrl+click adds another day.", None),
                     ("info", "The roster is only ever read: a fresh copy is downloaded and nothing is changed on it.", None),
                     ("info", "Anything that has not happened yet is left blank on the forms.", None),
                     ("info", "This tool starts no other program: it writes the PDFs and the spreadsheet itself.", None)]
        self.build()
        self.status.trace_add("write", self.paint_status)
        self.roster_info.trace_add("write", self.paint_status)
        self.other.trace_add("write", self.other_typed)
        for var in (self.supervisor, self.incoming):
            var.trace_add("write", self.extras_typed)

        root.bind("<Return>", self.enter_pressed)
        for n, (kind, _, _, _) in enumerate(KINDS, 1):   # Ctrl+1, Ctrl+2, Ctrl+3 switch between the forms
            root.bind("<Control-Key-%d>" % n, lambda e, kind=kind: self.show_tab(kind))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.poll_job = root.after(120, self.poll)
        self.tick_job = root.after(60000 - int(time.time() * 1000) % 60000 + 200, self.tick)      # just after the minute turns
        root.after(80, self.title_bar)                # once the window is on the screen
        self.load_roster()
        if os.environ.get("CCL_GEOMETRY"):            # the rest of __init__ is for automated checks of the window
            root.geometry(os.environ["CCL_GEOMETRY"])
        if os.environ.get("CCL_QUIT_AFTER", "").isdigit():       # a window opened for a check never outstays it
            root.after(int(os.environ["CCL_QUIT_AFTER"]) * 1000, self.on_close)
        auto = os.environ.get("CCL_AUTORUN")          # "name|date date"; "|" alone keeps what the window opened on
        if auto and "|" in auto:
            n, d = auto.split("|", 1)
            if n:
                self.set_name(n)
            if d:
                self.set_dates(calllog.parse_dates(d.split(), datetime.date.today()))
            if os.environ.get("CCL_TAB") in self.pages:
                self.show_tab(os.environ["CCL_TAB"])
            root.after(int(os.environ.get("CCL_AUTORUN_DELAY", "400")), self.start)

    def build(self):
        """Make every widget, in the colours of the look in use now. Run once at the start and again when the look
        is changed: what the window knows is kept on the App itself, so the new widgets show exactly what the old did."""
        root = self.root
        month = (self.cal.year, self.cal.month) if hasattr(self, "cal") else None
        view = {}                                     # where each table was scrolled to, and which of its rows were chosen
        for cells, table in (("call_cells", "tree"), ("patrol_cells", "ptree"), ("shift_cells", "stree")):
            if hasattr(self, cells):
                old, keys = getattr(self, table), getattr(self, cells).keys
                getattr(self, cells).hide_tip()
                view[cells, table] = (old.yview()[0], {keys[i] for i in old.selection() if i in keys})
        for w in root.winfo_children():
            w.destroy()
        root.configure(bg=PAPER)
        self.style()

        # ---- the band across the top: badge, name, the look, date and clock ----
        head = tk.Frame(root, bg=NAVY)
        head.pack(fill="x")
        tk.Frame(root, bg=ACCENT, height=2).pack(fill="x")
        badge = tk.Canvas(head, width=36, height=36, bg=NAVY, highlightthickness=0)
        badge.create_polygon(18, 3, 31, 8, 31, 19, 27, 27, 18, 33, 9, 27, 5, 19, 5, 8, fill=ACCENT, outline="#5b9bff")
        badge.create_line(11, 18, 16, 23, 25, 12, fill="white", width=3, capstyle="round", joinstyle="round")
        badge.pack(side="left", padx=(22, 12), pady=9)
        tk.Label(head, text="NORTHWIND SECURITY", font=(FONT_BOLD, 13), bg=NAVY, fg="white").pack(side="left")
        tk.Frame(head, bg=HEAD_RULE, width=1, height=18).pack(side="left", padx=14)
        tk.Label(head, text="Control room logs", font=(FONT, 11), bg=NAVY, fg=NAVY_SOFT).pack(side="left")
        self.clock = tk.Label(head, font=(FONT_BOLD, 15), bg=NAVY, fg="white")
        self.clock.pack(side="right", padx=(12, 22))
        self.today_label = tk.Label(head, font=(FONT, 10), bg=NAVY, fg=NAVY_SOFT)
        self.today_label.pack(side="right")
        tk.Frame(head, bg=HEAD_RULE, width=1, height=18).pack(side="right", padx=14)
        dark = THEME == "dark"                        # the button offers the other look
        self.theme_button = Button(head, "Light" if dark else "Dark", lambda: root.after(1, self.switch_theme), icon="sun" if dark else "moon",
                                   kind="head", size=9, pad=(10, 4), back=NAVY)
        self.theme_button.pack(side="right")
        self.show_clock()

        # ---- the strip along the bottom: what happened last, and how fresh the roster is ----
        foot = tk.Frame(root, bg=BAR, highlightbackground=LINE, highlightthickness=1)
        foot.pack(side="bottom", fill="x")
        self.status_icon = tk.Label(foot, font=(ICON_FONT, 10), bg=BAR, fg=GOOD, width=2)
        self.status_icon.pack(side="left", padx=(12, 2), pady=5)
        tk.Label(foot, textvariable=self.status, font=(FONT, 9), bg=BAR, fg=INK, anchor="w").pack(side="left")
        self.progress = ttk.Progressbar(foot, mode="indeterminate", length=self.px(120), style="Slim.Horizontal.TProgressbar")
        Button(foot, "Refresh", lambda: self.load_roster(force=True), icon="refresh", kind="link", size=9, pad=(8, 2),
               back=BAR).pack(side="right", padx=(4, 10))
        tk.Label(foot, textvariable=self.roster_info, font=(FONT, 9), bg=BAR, fg=BODY).pack(side="right")
        self.roster_dot = tk.Canvas(foot, width=10, height=10, bg=BAR, highlightthickness=0)
        self.roster_dot.pack(side="right", padx=(0, 6))
        self.paint_status()

        body = tk.Frame(root, bg=PAPER)
        body.pack(fill="both", expand=True, padx=16, pady=12)
        side = tk.Frame(body, bg=PAPER)
        side.pack(side="left", fill="y")
        main = tk.Frame(body, bg=PAPER)
        main.pack(side="left", fill="both", expand=True, padx=(14, 0))

        # ---- left: whose shift ----
        who, inner = card(side, "Controller", "person")
        who.pack(fill="x")
        self.pills = {}
        for i, name in enumerate(calllog.CONTROLLERS):
            initials = "".join(word[0] for word in name.split()[:2]).upper()
            chip = Chip(inner, name, lambda name=name: self.set_name(name), avatar=(initials, AVATARS[i % len(AVATARS)]), side="w")
            chip.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0, 6) if i % 2 == 0 else 0, pady=(0, 6))
            self.pills[name] = chip
        inner.columnconfigure(0, weight=1, uniform="who")
        inner.columnconfigure(1, weight=1, uniform="who")
        other = tk.Frame(inner, bg=CARD)
        other.grid(row=9, column=0, columnspan=2, sticky="ew")
        tk.Label(other, text="Someone else", font=(FONT, 9), bg=CARD, fg=MUTED).pack(side="left")
        ttk.Entry(other, textvariable=self.other, width=18).pack(side="left", padx=(8, 0), fill="x", expand=True)

        # the button and the list of what it makes are packed from the bottom so they always stay in view
        pick = tk.Frame(side, bg=PAPER)
        pick.pack(side="bottom", fill="x")
        for kind, label, _, _ in KINDS:
            Check(pick, label, self.docs[kind], back=PAPER, size=9).pack(side="left", padx=(0, 12))
        self.go = Button(side, "Make logs", self.start, icon="document", kind="primary", size=11, pad=(16, 10))
        self.go.pack(side="bottom", fill="x", pady=(10, 8))
        self.go.configure(state="disabled" if self.busy else "normal")

        # ---- left: which day ----
        day, inner = card(side, "Day of shift", "calendar")
        day.pack(fill="both", expand=True, pady=(10, 0))
        tk.Label(inner, text="Suggested", font=(FONT, 9), bg=CARD, fg=MUTED).pack(anchor="w")
        self.chip_box = tk.Frame(inner, bg=CARD)
        self.chip_box.pack(fill="x", pady=(3, 4))
        self.chips = []
        # packed from the bottom up, so the calendar takes whatever height is spare
        self.dutybar = tk.Canvas(inner, bg=CARD, highlightthickness=0, width=280, height=38)
        self.dutybar.pack(side="bottom", fill="x")
        self.dutybar.bind("<Configure>", lambda e: self.draw_duty())
        tk.Label(inner, text="CONTROL ROOM THAT DAY", font=(FONT_BOLD, 8), bg=CARD, fg=MUTED, anchor="w").pack(side="bottom", fill="x", pady=(6, 4))
        tk.Label(inner, textvariable=self.picked, font=(FONT_BOLD, 9), bg=CARD, fg=INK, anchor="w",
                 wraplength=self.px(290), justify="left").pack(side="bottom", fill="x")
        self.cal = Calendar(inner, self.set_dates, self.shift_kind)
        self.cal.pack(fill="both", expand=True)
        if month:                                     # (the days count as already chosen, or it would turn back to their month)
            self.cal.year, self.cal.month, self.cal.chosen = month[0], month[1], list(self.dates)

        # ---- right: the shift at a glance ----
        top, inner = card(main)
        top.pack(fill="x")
        row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x")
        # buttons are packed first so a long date gives way, not the buttons
        Button(row, "Folder", self.open_folder, icon="folder").pack(side="right")
        self.open_xlsx = Button(row, "Spreadsheet", lambda: self.open_files("xlsx"), icon="table")
        self.open_xlsx.pack(side="right", padx=(0, 8))
        self.open_all = Button(row, "All in one", self.open_pack, icon="print")
        self.open_all.pack(side="right", padx=(0, 8))
        self.open_pdf = Button(row, "Open PDF", lambda: self.open_files("pdf"), icon="pdf")
        self.open_pdf.pack(side="right", padx=(0, 8))
        tk.Label(row, textvariable=self.headline, font=(FONT_BOLD, 16), bg=CARD, fg=INK, anchor="w").pack(side="left")
        row = self.facts_row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x", pady=(3, 0))
        self.facts = tk.Frame(row, bg=CARD)          # who, when, who takes over, and a pill saying where the shift stands
        self.facts.pack(side="left")
        tk.Label(row, textvariable=self.subline, font=(FONT, 10), bg=CARD, fg=MUTED, anchor="w").pack(side="left")
        self.next_label = tk.Label(inner, textvariable=self.next_call, font=(FONT_BOLD, 9), bg=CARD, fg=ACCENT_DOWN, anchor="w")
        tk.Frame(inner, bg=INNER, height=1).pack(fill="x", pady=(8, 2))
        self.timeline = Timeline(inner)
        self.timeline.pack(fill="x")

        # ---- right: the three forms, as tabs on one panel ----
        deck = tk.Frame(main, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        deck.pack(fill="both", expand=True, pady=10)
        strip = tk.Frame(deck, bg=CARD)
        strip.pack(fill="x")
        self.tabs, self.pages = {}, {}
        for kind, label, _, _ in KINDS:
            self.tabs[kind] = Tab(strip, label, {"calls": "phone", "patrols": "walk", "shift": "list"}[kind],
                                  lambda kind=kind: self.show_tab(kind))
            self.tabs[kind].pack(side="left", padx=(4 if kind == "calls" else 0, 0))
        Check(strip, "Open the PDF when it is ready", self.auto_open, back=CARD, size=9).pack(side="right", padx=14)
        tk.Frame(deck, bg=INNER, height=1).pack(fill="x")
        stack = tk.Frame(deck, bg=CARD)
        stack.pack(fill="both", expand=True)

        def tools(page, buttons, hint="", legend=False):
            """The row at the top of a tab: its buttons (or, with none, a line on how the table works). Returns the buttons."""
            band = tk.Frame(page, bg=CARD)
            band.pack(fill="x", padx=12, pady=(10, 8))
            made = []
            for label, icon, kind, command in buttons:
                made.append(Button(band, label, command, icon=icon, kind=kind))
                made[-1].configure(state="disabled")
                made[-1].pack(side="left", padx=(0, 8))
            if legend:                                # rows changed by hand are shaded, ones filled in ahead are blue: say so
                tk.Label(band, text="filled in ahead", font=(FONT, 9), bg=CARD, fg=ACCENT_DOWN).pack(side="right", padx=(14, 0))
                tk.Label(band, text="changed by hand", font=(FONT, 9), bg=CARD, fg=HINT).pack(side="right")
                tk.Frame(band, bg=EDITED, width=14, height=12, highlightthickness=1, highlightbackground=FIELD).pack(side="right", padx=(14, 5))
            if hint:
                tk.Label(band, text=hint, font=(FONT, 9), bg=CARD, fg=HINT).pack(side="left")
            return made

        def caption(page, text):
            """A line of small print under a table saying how to use it."""
            tk.Label(page, text=text, font=(FONT, 9), bg=CARD, fg=HINT, anchor="w").pack(fill="x", padx=14, pady=(5, 0))

        warn = lambda message: self.tell(message, "warn")
        busy = lambda: self.busy

        # check calls: the table
        page = self.pages["calls"] = tk.Frame(stack, bg=CARD)
        self.call_buttons = tools(page, (("Fill in the rest of the shift", "clock", "tint", self.fill_ahead),),
                                  "Click a time or a response to change it  -  Enter goes down the list", legend=True)
        holder = tk.Frame(page, bg=CARD)
        holder.pack(fill="both", expand=True)
        self.tree = self.table(holder, (("no", "No.", 46, "center"), ("name", "Employee name", 210, "w"), ("venue", "Venue", 200, "w"),
                                        ("time", "Time of call", 96, "center"), ("resp", "Response", 84, "center"), ("shift", "Their shift", 112, "w")),
                               stretch=("name", "venue"))
        self.call_cells = CellEditor(self.tree, ("time", "resp"), self.call_text, self.commit_call, warn, busy)

        # patrols: buttons, a short table, and the form as it will print
        page = self.pages["patrols"] = tk.Frame(stack, bg=CARD)
        self.patrol_buttons = tools(page, (("Add a patrol", "add", "tint", self.add_patrol), ("Remove", "delete", "plain", self.remove_patrol),
                                           ("Clear all", "clear", "plain", self.clear_patrols),
                                           ("Usual rounds", "refresh", "plain", self.usual_rounds)), legend=True)
        holder = tk.Frame(page, bg=CARD)
        holder.pack(fill="x")
        self.ptree = self.table(holder, (("no", "No.", 46, "center"), ("officer", "Officer name", 170, "w"), ("area", "Campus / area", 150, "w"),
                                         ("start", "Patrol start", 96, "center"), ("finish", "Patrol finish", 96, "center"), ("notes", "Notes / incidents", 200, "w")),
                                stretch=("officer", "notes"), height=4)
        self.patrol_cells = CellEditor(self.ptree, ("area", "start", "finish", "notes"), self.patrol_text, self.commit_patrol, warn, busy,
                                       enter="along")
        caption(page, "Click a time, area or note to change it  -  Enter moves along the row  -  Esc leaves it as it was")
        self.patrol_view = Preview(page)
        self.patrol_view.pack(fill="both", expand=True, pady=(8, 0))

        # shift log: occurrences, handover notes, and the form as it will print
        page = self.pages["shift"] = tk.Frame(stack, bg=CARD)
        self.shift_buttons = tools(page, (("Add an entry", "add", "tint", self.add_entry), ("Remove", "delete", "plain", self.remove_entry),
                                          ("Clear entries", "clear", "plain", self.clear_entries)))
        holder = tk.Frame(page, bg=CARD)
        holder.pack(fill="x")
        self.stree = self.table(holder, (("time", "Time", 70, "center"), ("area", "Location / area", 190, "w"),
                                         ("text", "Occurrence / action taken", 420, "w")), stretch=("text",), height=3)
        self.shift_cells = CellEditor(self.stree, ("time", "area", "text"), self.shift_text, self.commit_shift, warn, busy,
                                      enter="along", left=self.entry_left)
        caption(page, "Click a cell to type  -  Enter moves along the row  -  Esc leaves it as it was  -  what is left empty prints blank")
        extra = tk.Frame(page, bg=CARD)
        extra.pack(fill="x", padx=12, pady=(8, 0))
        names = tk.Frame(extra, bg=CARD)
        names.pack(side="right", anchor="n", padx=(14, 0))
        self.extra_boxes = []
        for at, (label, var) in enumerate((("Supervisor", self.supervisor), ("Incoming officer", self.incoming))):
            tk.Label(names, text=label, font=(FONT, 9), bg=CARD, fg=MUTED).grid(row=at, column=0, sticky="w", pady=2)
            box = ttk.Entry(names, textvariable=var, width=22, state="disabled")
            box.grid(row=at, column=1, padx=(8, 0), pady=2)
            self.extra_boxes.append(box)
        tk.Label(extra, text="Handover notes", font=(FONT, 9), bg=CARD, fg=MUTED).pack(side="left", anchor="n", padx=(0, 8), pady=2)
        self.handover = tk.Text(extra, height=3, wrap="word", font=(FONT, 10), bg=CARD, fg=INK, insertbackground=INK, relief="flat",
                                padx=8, pady=5, selectbackground=ACCENT, selectforeground=ON_ACCENT,
                                highlightthickness=1, highlightbackground=FIELD, highlightcolor=ACCENT, state="disabled")
        self.handover.pack(side="left", fill="x", expand=True)
        self.handover.bind("<KeyRelease>", self.extras_typed)
        self.handover.bind("<FocusOut>", lambda e: self.save_extras())
        self.shift_view = Preview(page)
        self.shift_view.pack(fill="both", expand=True, pady=(8, 0))

        # ---- right: things to know about the logs just made ----
        notes = tk.Frame(main, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        notes.pack(fill="x")
        self.notes = tk.Text(notes, height=4, wrap="word", font=(FONT, 10), bg=CARD, fg=INK, relief="flat", padx=12, pady=8,
                             spacing1=2, spacing3=2, selectbackground=ACCENT, selectforeground=ON_ACCENT)
        self.notes.pack(fill="x")
        self.notes.tag_configure("bad", foreground=BAD)
        self.notes.tag_configure("head", font=(FONT_BOLD, 10))
        for kind, colour in (("info", MUTED), ("warn", WARN), ("stop", BAD), ("ok", GOOD)):
            self.notes.tag_configure("icon-" + kind, font=(ICON_FONT, 10), foreground=colour)

        self.paint_notes()
        self.show_tab(self.tab)
        self.draw_tables()
        self.refresh_choices()
        self.fill_extras()
        for (cells, table), (top, chosen) in view.items():
            tree, keys = getattr(self, table), getattr(self, cells).keys
            tree.selection_set([i for i, k in keys.items() if k in chosen])
            root.after_idle(lambda tree=tree, top=top: tree.yview_moveto(top) if tree.winfo_exists() else None)

    def title_bar(self, tries=20):
        """Ask Windows for a dark title bar with the dark look. It is this window's own frame only: no setting of the PC is touched."""
        try:
            import ctypes
            user, dwm = ctypes.WinDLL("user32"), ctypes.WinDLL("dwmapi")
            user.GetParent.restype, user.GetParent.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
            dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
            user.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
            frame = user.GetParent(self.root.winfo_id())
            if not frame:                             # the window is not on the screen yet: ask again in a moment
                if tries and not self.gone:
                    self.root.after(150, lambda: self.title_bar(tries - 1))
                return
            value = ctypes.c_int(1 if THEME == "dark" else 0)
            if (dwm.DwmSetWindowAttribute(frame, 20, ctypes.byref(value), 4) == 0
                    or dwm.DwmSetWindowAttribute(frame, 19, ctypes.byref(value), 4) == 0):
                user.SetWindowPos(frame, None, 0, 0, 0, 0, 0x0037)        # same place, same size: just draw the frame again
        except Exception:
            pass                                      # an older Windows: the title bar stays as it is

    def switch_theme(self):
        """Dark for the night shift, light for the day. The window is made again as it stands, and the choice is remembered."""
        if self.busy:
            self.tell("The logs are still being made - change the look once they are done.", "warn")
            return
        if not self.settle():                         # keep whatever is being typed
            return
        use_theme("light" if THEME == "dark" else "dark")
        self.build()
        self.title_bar()
        self.keep_settings()
        self.tell("Dark look on - easier on the eyes at night." if THEME == "dark" else "Light look on.", "ok")

    def keep_settings(self):
        if os.environ.get("CCL_THEME"):               # a look forced for a check of the window is not the user's choice
            return
        save_settings({"auto_open": bool(self.auto_open.get()), "docs": [k for k, _, _, _ in KINDS if self.docs[k].get()], "theme": THEME})

    # ---- look ----
    def px(self, n):
        return int(n * self.scale)

    def style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Treeview", font=(FONT, 10), rowheight=self.px(26), background=CARD, fieldbackground=CARD, foreground=INK,
                    bordercolor=CARD, lightcolor=CARD, darkcolor=CARD, borderwidth=0)
        s.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", INK)])
        s.configure("Treeview.Heading", font=(FONT_BOLD, 9), background=HEAD, foreground=MUTED, relief="flat",
                    bordercolor=HEAD, lightcolor=HEAD, darkcolor=HEAD, padding=(2, 6))
        s.map("Treeview.Heading", background=[("active", HEAD)])
        s.configure("TEntry", fieldbackground=CARD, foreground=INK, insertcolor=INK, bordercolor=FIELD, lightcolor=CARD, darkcolor=CARD,
                    selectbackground=ACCENT, selectforeground=ON_ACCENT, padding=4)
        s.map("TEntry", bordercolor=[("focus", ACCENT)], fieldbackground=[("disabled", OFF)], foreground=[("disabled", OFF_INK)])
        # a slim scroll bar: no arrows, just a thumb
        s.layout("Slim.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        s.configure("Slim.Vertical.TScrollbar", background=THUMB, troughcolor=PAPER, bordercolor=PAPER, lightcolor=THUMB,
                    darkcolor=THUMB, gripcount=0, width=10, arrowsize=10, relief="flat")
        s.map("Slim.Vertical.TScrollbar", background=[("active", THUMB_ON)])
        s.configure("Slim.Horizontal.TProgressbar", troughcolor=LINE, background=ACCENT, bordercolor=LINE,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=6)

    def table(self, parent, columns, stretch, height=10):
        """A table filling its parent, with a slim scroll bar that only shows when there is something to scroll."""
        tree = ttk.Treeview(parent, columns=[c[0] for c in columns], show="headings", height=height)
        for key, title, width, anchor in columns:
            tree.heading(key, text=title, anchor=anchor)
            tree.column(key, width=self.px(width), anchor=anchor, stretch=key in stretch)
        tree.before_scroll = lambda: None            # its CellEditor closes an open cell here, so the box never sits over the wrong row
        bar = ttk.Scrollbar(parent, orient="vertical", style="Slim.Vertical.TScrollbar",
                            command=lambda *a: (tree.before_scroll(), tree.yview(*a)))

        def scrolled(lo, hi):
            bar.set(lo, hi)
            bar.grid() if float(lo) > 0 or float(hi) < 1 else bar.grid_remove()

        tree.configure(yscrollcommand=scrolled)
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        tree.tag_configure("log", font=(FONT_BOLD, 10), background=TINT)
        tree.tag_configure("band", background=BAND)
        tree.tag_configure("todo", foreground=TODO)
        tree.tag_configure("edited", background=EDITED)
        tree.tag_configure("next", font=(FONT_BOLD, 10), foreground=ACCENT_DOWN)
        tree.tag_configure("ahead", foreground=ACCENT_DOWN)       # filled in although it is still to come
        return tree

    def say(self, text, tag=None, clear=True):
        if clear:
            self.said = []
        self.said.append(("text", text, tag))
        self.paint_notes()

    def note(self, kind, text, clear=False):
        """One line in the notes panel with an icon in front: kind is 'info', 'warn', 'stop' or 'ok'."""
        if clear:
            self.said = []
        self.said.append((kind, text, None))
        self.paint_notes()

    def paint_notes(self):
        """Write out everything said so far (kept in self.said, so the panel can be made again in another look)."""
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        for kind, text, tag in self.said:
            if kind == "text":
                self.notes.insert("end", text, tag or ())
                continue
            glyph = ICONS.get({"info": "info", "warn": "warning", "stop": "cancel", "ok": "check"}[kind])
            if glyph:
                self.notes.insert("end", glyph + "  ", ("icon-" + kind,))
            self.notes.insert("end", text + "\n", ("bad",) if kind == "stop" else ())
        self.notes.configure(state="disabled")

    def tell(self, text, kind="ok"):
        """Put a line in the status bar: kind 'ok', 'warn' or 'busy' picks the icon."""
        self.status_kind = kind
        self.status.set(text)

    def paint_status(self, *_):
        """Icons for the status bar: how the last thing went, and how fresh the roster is."""
        text = self.status.get()
        if self.status_kind:
            kind = self.status_kind
        elif self.status_shown[0] == text:            # only the roster line changed: leave the icon as it was
            kind = self.status_shown[1]
        else:
            low = text.lower()
            kind = "warn" if any(w in low for w in ("could not", "nothing was made", "not updated", "is outside", "not a time", "no room",
                                                    "first, then", "saved, but", "cannot", "has not happened", "no time yet")) \
                else "busy" if text.endswith("...") else "ok"
        self.status_kind, self.status_shown = None, (text, kind)
        glyph, colour = {"ok": ("check", GOOD), "warn": ("warning", WARN), "busy": ("sync", ACCENT)}[kind]
        self.status_icon.configure(text=ICONS.get(glyph, ""), fg=colour)
        roster = self.roster_info.get()
        fresh = GOOD if "updated" in roster and "not" not in roster else BAD if "not updated" in roster or "could not" in roster else WARN
        self.roster_dot.delete("all")
        self.roster_dot.create_oval(1, 1, 9, 9, fill=fresh, outline="")

    def show_clock(self):
        now = datetime.datetime.now()
        self.clock.configure(text=now.strftime("%H:%M"))
        self.today_label.configure(text=now.strftime("%A ") + str(now.day) + now.strftime(" %B %Y"))

    def show_tab(self, kind):
        if not self.settle():
            return
        self.tab = kind
        for k, page in self.pages.items():
            page.pack_forget()
            self.tabs[k].choose(k == kind)
        self.pages[kind].pack(fill="both", expand=True)
        self.refresh_buttons()

    def settle(self, keep=True):
        """Finish anything half-typed (an open cell, the handover box) so it is saved before the next action.
        False if a typed value cannot be right: its cell stays open saying why, and the action waits for it to be
        put right (or Esc). keep=False, when the window is closing, lets such a value go."""
        for name in ("call_cells", "patrol_cells", "shift_cells"):
            cells = getattr(self, name, None)
            if cells and not cells.close(move="hold" if keep else None) and keep:
                return False
        if getattr(self, "extras_job", None):
            self.save_extras()
        return True

    def enter_pressed(self, event):
        """Enter makes the logs - after choosing who and when on the left - but not from the tables and boxes on the right."""
        if event.widget not in [self.tree, self.ptree, self.stree, self.handover, self.notes] + self.extra_boxes:
            self.start()

    def refresh_buttons(self):
        has = lambda what: any(p.get(self.tab + "_files", {}).get(what) for p in self.packs)
        on = lambda yes: "normal" if yes and not self.busy else "disabled"
        self.open_pdf.configure(state="normal" if has("pdf") else "disabled")
        self.open_xlsx.configure(state="normal" if has("xlsx") else "disabled")
        self.open_all.configure(state="normal" if any(p.get("all_files", {}).get("pdf") for p in self.packs) else "disabled")
        going = self.still_going()
        self.call_buttons[0].configure(state=on(going), text="Leave the rest of the shift blank" if going and all(
            self.filled_ahead(self.packs[i]) for i in going) else "Fill in the rest of the shift")
        for b in self.patrol_buttons:
            b.configure(state=on(any("patrols" in p for p in self.packs)))
        shift = any("shift" in p for p in self.packs)
        for b in self.shift_buttons + self.extra_boxes + [self.handover]:
            b.configure(state=on(shift))

    def show_summary(self):
        """The panel at the top right: who, when, who takes over, where the shift stands, and the shift drawn as a strip.
        With several days made it shows the one that is on now, else the latest."""
        for w in self.facts.winfo_children():
            w.destroy()
        self.next_call.set("")
        self.next_label.pack_forget()
        if not self.packs:
            self.timeline.show(None)
            return
        now = datetime.datetime.now()
        gone_for = lambda p: (now - datetime.datetime.combine(p["date"], datetime.time())).total_seconds() / 60.0
        meta_of = lambda p: next(p[k] for k, _, _, _ in KINDS if k in p)["meta"]
        pack = next((p for p in self.packs if meta_of(p)["start_min"] <= gone_for(p) <= meta_of(p)["end_min"]), self.packs[-1])
        m, gone = meta_of(pack), gone_for(pack)
        who = m["supervisor"]
        relief = pack["shift"]["incoming"] if "shift" in pack else calllog.next_on_duty(pack["date"], self.made_roster, who, m["start_min"])
        self.subline.set("")
        facts = [("person", who), ("clock", m["shift"].replace("-", " - ")), ("next", relief + " takes over" if relief else "")]
        if len(self.packs) > 1:
            facts.insert(0, ("calendar", pack["date"].strftime("%a %d %b")))
        for icon, text in facts:
            if text:
                item = tk.Frame(self.facts, bg=CARD)
                item.pack(side="left", padx=(0, 16))
                if ICONS.get(icon):
                    tk.Label(item, text=ICONS[icon], font=(ICON_FONT, 9), bg=CARD, fg=FAINT).pack(side="left", padx=(0, 5))
                tk.Label(item, text=text, font=(FONT, 10), bg=CARD, fg=BODY).pack(side="left")
        live = m["start_min"] <= gone <= m["end_min"]
        words, back, ink = (("Not started yet", TINT, ACCENT_DOWN) if gone < m["start_min"] else
                            ("On shift now", GOOD_TINT, GOOD) if live else ("Shift finished", PILL, BODY))
        wide = tkfont.Font(family=FONT_BOLD, size=9).measure(words) + 22
        pill = tk.Canvas(self.facts, width=wide, height=20, bg=CARD, highlightthickness=0)
        round_bar(pill, 0, wide, 0, 20, back)
        pill.create_text(wide / 2.0, 10, text=words, font=(FONT_BOLD, 9), fill=ink)
        pill.pack(side="left")
        calls = pack["calls"]["calls"] if "calls" in pack else []
        self.timeline.show({"date": pack["date"], "start": m["start_min"], "end": m["end_min"],
                            "calls": [(c["call"], bool(c["call_text"]) and not c["ahead"]) for c in calls],
                            "patrols": [(r["start"], r["finish"]) for r in pack["patrols"]["rows"]] if "patrols" in pack else [],
                            "events": self.event_minutes(pack)})
        # the logs are as fresh as the last time they were made: say so rather than show a "next call" that has passed
        blank = [c for c in calls if not c["call_text"]]
        passed = [c for c in blank if c["call"] < gone]
        if live and passed:
            self.next_call.set("%d call%s have come due since these logs were made - press Make logs to bring them up to date."
                               % (len(passed), "" if len(passed) == 1 else "s"))
        elif live and blank:
            self.next_call.set("Next call due %s  -  %s, %s" % (blank[0]["due"], blank[0]["name"], blank[0]["venue"]))
        elif live and any(c["ahead"] for c in calls):
            early = [c for c in calls if c["ahead"]]
            self.next_call.set("Next call to make: due %s  -  %s, %s   (%d filled in ahead)" % (early[0]["due"], early[0]["name"], early[0]["venue"], len(early)))
        if self.next_call.get():
            self.next_label.pack(fill="x", pady=(4, 0), after=self.facts_row)

    def event_minutes(self, pack):
        """When the lines of the shift log happened, in minutes from that day's midnight (lines without a time are left out)."""
        out = []
        for e in pack["shift"]["entries"] if "shift" in pack else []:
            try:
                out.append(calllog.shift_clock(e["time"], pack["shift"]["meta"], late=calllog.GRACE, early=calllog.EARLY))
            except calllog.Problem:
                pass
        return out

    # ---- who and when ----
    def duty(self, date):
        """That day's control-room shifts: from the roster once it is loaded, else the usual week."""
        return self.view.duty(date) if self.view else calllog.usual_duty(date)

    def draw_duty(self):
        """Who has the control room on the chosen day, as a bar running from the first shift's start round the clock."""
        c = self.dutybar
        c.delete("all")
        w = c.winfo_width()
        if w < 100:
            return
        day = self.dates[-1] if self.dates else datetime.date.today()
        shifts = sorted(((s["start"], s["end"] + (calllog.DAY if s["end"] <= s["start"] else 0), s["name"]) for s in self.duty(day)))
        if not shifts:
            c.create_text(1, 12, anchor="w", text="Nobody is on the roster for %s." % day.strftime("%a %d %b"), font=(FONT, 9), fill=HINT)
            return
        a = shifts[0][0]
        b = max(max(s[1] for s in shifts), a + 60)
        x = lambda minute: 1 + (minute - a) * (w - 2) / float(b - a)
        for start, end, name in shifts:
            mine = calllog.same_person(self.name, name)
            c.create_rectangle(x(start), 1, x(end), 21, fill=ACCENT if mine else OTHER_BAR, outline=CARD, width=2)
            if x(end) - x(start) > 50:
                c.create_text((x(start) + x(end)) / 2.0, 11, text=name.split()[0], font=(FONT_BOLD, 8), fill=ON_ACCENT if mine else INK)
            c.create_text(x(start) + 1, 31, anchor="w", text=calllog.fmt(start).zfill(5), font=(FONT, 8), fill=HINT)
        c.create_text(w - 1, 31, anchor="e", text=calllog.fmt(b).zfill(5), font=(FONT, 8), fill=HINT)

    def shift_kind(self, date):
        """'day' or 'night' if the chosen controller is in the control room that date, else None."""
        for s in self.duty(date):
            if calllog.same_person(self.name, s["name"]):
                return "day" if s["start"] < 16 * 60 else "night"
        return None

    def on_now(self, name):
        """Is this controller on shift at this moment?"""
        now = datetime.datetime.now()
        for back in (1, 0):
            d = now.date() - datetime.timedelta(days=back)
            midnight = datetime.datetime.combine(d, datetime.time())
            for s in self.duty(d):
                end = s["end"] + (calllog.DAY if s["end"] <= s["start"] else 0)
                if calllog.same_person(name, s["name"]) and midnight + datetime.timedelta(minutes=s["start"]) <= now \
                        <= midnight + datetime.timedelta(minutes=end):
                    return True
        return False

    def recent_shifts(self, limit=4):
        """This controller's latest shift days that have started, newest first, as (date, on now?)."""
        now, out = datetime.datetime.now(), []
        for back in range(35):
            d = now.date() - datetime.timedelta(days=back)
            midnight = datetime.datetime.combine(d, datetime.time())
            for s in self.duty(d):
                start = midnight + datetime.timedelta(minutes=s["start"])
                end = midnight + datetime.timedelta(minutes=s["end"] + (calllog.DAY if s["end"] <= s["start"] else 0))
                if calllog.same_person(self.name, s["name"]) and start <= now:
                    out.append((d, now <= end))
                    break
            if len(out) == limit:
                break
        return out

    def set_name(self, name, typed=False):
        self.name = name
        self.touched_name = True
        if not typed and self.other.get():
            self.other.set("")
        if not self.touched_date:                    # follow the controller to their latest shift
            recent = self.recent_shifts(1)
            self.dates = [recent[0][0] if recent else datetime.date.today()]
        self.refresh_choices()

    def other_typed(self, *_):
        text = self.other.get().strip()
        if text:
            self.set_name(text, typed=True)
        elif self.name not in self.pills:             # the box was emptied: back to whoever is at the desk
            self.touched_name = False
            self.follow_the_desk()
            self.refresh_choices()

    def set_dates(self, dates, add=False, keep=False):
        if add:
            dates = [d for d in self.dates if d not in dates] if all(d in self.dates for d in dates) and len(self.dates) > 1 \
                else self.dates + [d for d in dates if d not in self.dates]
        self.dates = sorted(dates)
        self.touched_date = self.touched_date or not keep
        self.refresh_choices()

    def refresh_choices(self):
        """Redraw everything on the left from self.name / self.dates."""
        for name, chip in self.pills.items():
            chip.choose(name == self.name)
            chip.mark(self.on_now(name))
        for chip in self.chips:
            chip.destroy()
        self.chips = []
        today = datetime.date.today()
        options = [(d.strftime("%a %d %b") + ("  -  today" if d == today and not live else ""), [d], live) for d, live in self.recent_shifts()]
        if not options:
            options = [("Today", [today], False), ("Yesterday", [today - datetime.timedelta(days=1)], False)]
        weekend = sorted(d for d, _ in self.recent_shifts(7) if d.weekday() >= 4)[-3:]
        weekend = [d for d in weekend if (weekend[-1] - d).days <= 2] if weekend else []
        if len(weekend) >= 2:
            first = weekend[0].strftime("%a %d" if weekend[0].month == weekend[-1].month else "%a %d %b")
            options.append(("Weekend  %s - %s" % (first, weekend[-1].strftime("%a %d %b")), weekend, False))
        singles = sum(1 for _, dates, _ in options if len(dates) == 1)
        for i, (label, dates, live) in enumerate(options):
            chip = Chip(self.chip_box, label, lambda dates=dates: self.set_dates(dates), dot=live)
            if len(dates) == 1:
                chip.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0, 6) if i % 2 == 0 else 0, pady=(0, 6))
            else:                                    # a several-day choice gets a full row under the single days
                chip.grid(row=(singles + 1) // 2 + i - singles, column=0, columnspan=2, sticky="ew", pady=(0, 6))
            chip.choose(dates == self.dates)
            self.chips.append(chip)
        self.chip_box.columnconfigure(0, weight=1, uniform="chip")
        self.chip_box.columnconfigure(1, weight=1, uniform="chip")
        self.cal.show(self.dates)
        self.draw_duty()
        self.picked.set("%s  -  %s" % (self.name, ", ".join(d.strftime("%a %d %b %Y") for d in self.dates)))

    def follow_the_desk(self):
        """Until someone has chosen otherwise: the controller at the desk now, and their shift's day."""
        if not self.touched_name:
            # once the roster is in, only believe it when it has somebody on shift right now
            who = calllog.at_the_desk(datetime.datetime.now(), self.duty, fallback=self.view is None)
            if who:
                self.name = who[0]
                if not self.touched_date:
                    self.dates = [who[1]]
        elif not self.touched_date:
            recent = self.recent_shifts(1)
            if recent:
                self.dates = [recent[0][0]]

    def tick(self):
        """Just after each minute turns: the clock, the 'now' marker, the on-shift dots and - until someone
        chooses otherwise - who is at the desk. A new day redraws the suggestions too."""
        try:
            today = datetime.date.today()
            new_day, self.cal.today = today != self.cal.today, today
            self.show_clock()
            was = (self.name, list(self.dates))
            if not (self.busy or self.packs):
                self.follow_the_desk()
            if new_day or (self.name, self.dates) != was:
                self.refresh_choices()
            else:
                for name, chip in self.pills.items():
                    chip.mark(self.on_now(name))
            if self.packs and not self.busy:
                self.show_summary()                   # move the marker, update where the shift stands
        except Exception:
            if not self.gone:
                self.note("stop", "Something went wrong while updating the clock:\n" + traceback.format_exc())
        finally:
            if not self.gone:
                self.tick_job = self.root.after(60000 - int(time.time() * 1000) % 60000 + 200, self.tick)

    # ---- the roster ----
    def load_roster(self, force=False):
        def run():
            try:
                self.fetch_roster(force, show_saved=True)
            except calllog.Problem:
                pass                                 # already reported through the queue
        threading.Thread(target=run, daemon=True).start()

    def fetch_roster(self, force=False, show_saved=False):
        """Worker thread: hand the window an up-to-date roster. Returns (roster, note)."""
        with self.roster_lock:
            if self.roster and not force and time.time() - self.roster_at < ROSTER_FRESH:
                return self.roster, self.roster_note
            try:
                have = os.path.exists(calllog.ROSTER_PATH)
                if show_saved and have and not self.roster:      # show the saved copy at once, then update it
                    self.queue.put(("roster", calllog.Roster(), "saved copy, updating...", 0.0))
                fresh = True
                if os.environ.get("CCL_NO_DOWNLOAD") and have:
                    note = "saved copy"
                elif calllog.download_roster().startswith("downloaded"):
                    note = "updated " + time.strftime("%H:%M")
                else:
                    note, fresh = "could not update - using the saved copy", False
                roster = calllog.Roster()
            except calllog.Problem as e:
                self.queue.put(("roster_failed", str(e)))
                raise
            except Exception as e:
                self.queue.put(("roster_failed", "the roster could not be read: %s" % e))
                raise calllog.Problem("the roster could not be read: %s" % e)
            stamp = time.time()
            self.roster, self.roster_at, self.roster_note = roster, stamp if fresh else 0.0, note
            self.queue.put(("roster", roster, note, stamp))
            return roster, note

    # ---- the run ----
    def start(self):
        if self.busy:
            return
        if not self.settle():                         # keep a correction that is still being typed (one that cannot be right is put right first)
            return
        name, dates = self.name.strip(), list(self.dates)
        docs = [k for k, _, _, _ in KINDS if self.docs[k].get()]
        if not name or not dates or not docs:
            messagebox.showinfo("Control Room Logs", "Choose the controller, the day of the shift and at least one log to make.")
            return
        self.keep_settings()
        self.busy = True
        self.packs = []
        self.patrol_day = None
        self.made_as = self.name
        self.go.configure(state="disabled")
        self.draw_tables()
        self.fill_extras()
        self.say("")
        self.headline.set("Working...")
        self.subline.set("%s  -  %s" % (name, ", ".join(d.strftime("%a %d %b") for d in dates)))
        self.status.set("Getting the latest roster...")
        threading.Thread(target=self.work, args=(name, dates, docs), daemon=True).start()

    def work(self, name, dates, docs):
        """Runs off the window's thread; reports back through the queue.
        Each day becomes a pack: {"date", "calls": log, "calls_files": {...}, "patrols": ..., "shift": ...}."""
        put = self.queue.put
        try:
            roster, note = self.fetch_roster()
            self.made_roster = roster                 # corrections are rebuilt from the same roster copy
            if note.startswith("could not update"):
                put(("problem", "The roster could not be downloaded just now, so this uses the copy saved earlier."))
            for d in dates:
                if self.closing:
                    break
                put(("status", "Making %s..." % d.strftime("%a %d %b")))
                pack, said = {"date": d}, set()
                for kind, label, build, write in KINDS:
                    if kind not in docs:
                        continue
                    try:
                        pack[kind] = build(d, roster, name)
                        pack[kind + "_files"] = write(pack[kind], self.out_dir)
                    except calllog.Problem as e:
                        if str(e) not in said:
                            put(("problem", str(e)))
                        said.add(str(e))
                    except Exception:                 # one form going wrong must not cost the others
                        pack.pop(kind, None)
                        pack.pop(kind + "_files", None)
                        put(("problem", "The %s for %s could not be made:\n%s" % (label.lower(), d.strftime("%a %d %b"), traceback.format_exc())))
                made = {k: pack[k] for k, _, _, _ in KINDS if k in pack}
                if len(made) > 1:                     # everything for the shift in one file, to print in one go
                    pack["all_files"] = calllog.write_pack(made, self.out_dir)
                if made:
                    put(("pack", pack))
        except calllog.Problem as e:
            put(("problem", str(e)))
        except Exception:
            put(("problem", "Something went wrong:\n" + traceback.format_exc()))
        put(("done",))

    def poll(self):
        """Hand the worker threads' messages to the window. One bad message must never stop this."""
        try:
            while not self.gone:
                msg = self.queue.get_nowait()
                try:
                    self.handle(msg)
                except Exception:
                    if self.gone:
                        return
                    self.note("stop", "Something went wrong:\n" + traceback.format_exc())
                    if msg[0] == "done":              # never leave the window stuck on "Working..."
                        self.busy = False
                        self.go.configure(state="normal")
        except queue.Empty:
            pass
        if self.gone:                                 # the window was closed by the message just handled
            return
        if self.busy and not self.progress.winfo_ismapped():        # the one moving thing: a bar while logs are being made
            self.progress.pack(side="left", padx=10)
            self.progress.start(14)
        elif not self.busy and self.progress.winfo_ismapped():
            self.progress.stop()
            self.progress.pack_forget()
        self.poll_job = self.root.after(120, self.poll)

    def handle(self, msg):
        kind = msg[0]
        if kind == "status":
            self.tell(msg[1], "busy")
        elif kind == "roster":
            if msg[3] < self.view_at:                 # an older copy arriving late
                return
            self.view, self.view_at = msg[1], msg[3]
            self.roster_info.set("Roster: " + msg[2])
            if not self.busy and not self.packs:      # the roster knows about swaps and cover
                self.follow_the_desk()
            self.refresh_choices()
        elif kind == "roster_failed":
            self.roster_info.set("Roster: not updated")
            if not self.busy:
                self.tell(msg[1][:1].upper() + msg[1][1:], "warn")
        elif kind == "problem":
            self.note("stop", msg[1][:1].upper() + msg[1][1:])
        elif kind == "pack":
            pack = msg[1]
            self.packs.append(pack)
            self.draw_tables()
            first = next(pack[k] for k, _, _, _ in KINDS if k in pack)
            if len(self.dates) > 1:
                self.say("%s\n" % first["day"], "head", clear=False)
            if "calls" in pack:
                self.note("info", "Control room that day: %s" % pack["calls"]["meta"]["control_room"])
                for n in pack["calls"]["notes"]:
                    self.note("warn", n[:1].upper() + n[1:])
            for k in [k for k, _, _, _ in KINDS] + ["all"]:
                for p in pack.get(k + "_files", {}).get("problems", []):
                    self.note("stop", p[:1].upper() + p[1:])
        elif kind == "done":
            self.finish()

    def finish(self):
        self.busy = False
        self.go.configure(state="normal")
        self.refresh_buttons()
        self.fill_extras()
        if self.packs:
            firsts = [next(p[k] for k, _, _, _ in KINDS if k in p) for p in self.packs]
            who = firsts[0]["meta"]["supervisor"]
            if self.name == self.made_as and self.name != who:   # a typed first name: show the full name it matched
                self.name = who
                self.other.set("" if who in self.pills else who)
                self.refresh_choices()
            day = self.packs[0]["date"]
            self.headline.set(day.strftime("%A ") + str(day.day) + day.strftime(" %B %Y") if len(self.packs) == 1
                              else "%d days made  (%s)" % (len(self.packs), ", ".join(f["day"][:10] for f in firsts)))
            self.show_summary()
            files = sum(1 for p in self.packs for k in ("calls", "patrols", "shift", "all") for what in ("pdf", "xlsx")
                        if p.get(k + "_files", {}).get(what))
            self.tell("Made %d files at %s." % (files, datetime.datetime.now().strftime("%H:%M")), "ok")
            self.note("ok", "Click a call time, a response, a patrol or a shift log line to change it; "
                            "every change is written to the PDFs at once.")
            if not any(self.tab in p for p in self.packs):        # show a tab that has something in it
                self.show_tab(next(k for k, _, _, _ in KINDS if any(k in p for p in self.packs)))
            if self.auto_open.get() and not os.environ.get("CCL_NOOPEN") and not self.closing:
                self.open_files("pdf")
        else:
            self.headline.set("Nothing was made")
            self.subline.set("See the message below.")
            self.tell("Nothing was made.", "warn")
        marker = os.environ.get("CCL_DONE_FILE")      # automated checks wait for this
        if marker:
            with open(marker, "w", encoding="utf-8") as f:
                f.write("\n".join(p[k + "_files"][what] for p in self.packs for k, _, _, _ in KINDS if k in p
                                  for what in ("pdf", "xlsx") if p[k + "_files"].get(what)))
        if self.closing:
            self.on_close()

    # ---- the tables ----
    def draw_tables(self):
        """Show every pack in the three tables, the previews and the strip at the top; remember which row is which.
        A cell open for typing stays open over its own row, and the rows that were chosen stay chosen."""
        many = len(self.packs) > 1
        now = datetime.datetime.now()
        at = self.shift_pack()
        self.day_rows = {}                            # patrol table: a day's heading and its "no patrols" line -> that day
        for tree, cells in ((self.tree, self.call_cells), (self.ptree, self.patrol_cells), (self.stree, self.shift_cells)):
            top = tree.yview()[0]
            chosen = {cells.keys[i] for i in tree.selection() if i in cells.keys}
            tree.delete(*tree.get_children())
            cells.keys = {}
            for idx, pack in enumerate(self.packs):
                if tree is self.tree and "calls" in pack:
                    log = pack["calls"]
                    if many:                          # several days in one go: a heading row per day
                        tree.insert("", "end", tags=("log",), values=("", log["day"], "%d calls" % log["meta"]["total"], "", "", log["meta"]["shift"]))
                    gone = (now - datetime.datetime.combine(pack["date"], datetime.time())).total_seconds() / 60.0
                    live = log["meta"]["start_min"] <= gone <= log["meta"]["end_min"]
                    band, hour, waiting = False, None, True
                    for c in log["calls"]:
                        if c["call"] // 60 != hour:   # calls of the same clock hour share a shade, so the night reads in blocks
                            band, hour = not band, c["call"] // 60
                        tags = ["edited"] if c["edited"] else ["band"] if band else []
                        if c["ahead"]:
                            tags.append("ahead")
                        if not c["call_text"]:
                            tags.append("next" if live and waiting else "todo")
                            waiting = False
                        item = tree.insert("", "end", tags=tags, values=(
                            c["no"], c["name"], c["venue"], c["call_text"] or "due " + c["due"], c["response"], re.sub(r"(\d)\.(\d)", r"\1:\2", c["shift"])))
                        cells.keys[item] = (idx, calllog.edit_key(c))
                elif tree is self.ptree and "patrols" in pack:
                    log = pack["patrols"]
                    if many:
                        self.day_rows[tree.insert("", "end", tags=("log",), values=("", log["day"], "", "", "", log["meta"]["shift"]))] = idx
                    for n, r in enumerate(log["rows"], 1):
                        item = tree.insert("", "end", tags=(("edited",) if r["edited"] else ("band",) if n % 2 == 0 else ()) + (
                            ("ahead",) if r["ahead"] else ()), values=(n, r["officer"], r["area"], r["start_text"], r["finish_text"], r["notes"]))
                        cells.keys[item] = (idx, r["id"])
                    if not log["rows"]:
                        self.day_rows[tree.insert("", "end", tags=("todo",), values=(
                            "", "No patrols listed", "", "", "", "A round is listed once it is done"))] = idx
                elif tree is self.stree and idx == at:
                    for n, e in enumerate(pack["shift"]["entries"]):
                        item = tree.insert("", "end", tags=("band",) if n % 2 else (), values=(e["time"], e["area"], e["text"]))
                        cells.keys[item] = (idx, e["id"])
                    if not pack["shift"]["entries"]:
                        tree.insert("", "end", tags=("todo",), values=("", "", "Nothing typed - the table prints blank to write on"))
            tree.selection_set([i for i, k in cells.keys.items() if k in chosen])
            tree.yview_moveto(top)
        self.ptree.configure(height=min(max(len(self.ptree.get_children()) + 1, 3), 8))
        self.stree.configure(height=min(max(len(self.stree.get_children()) + 1, 2), 8))
        counts = {"calls": sum(p["calls"]["meta"]["total"] for p in self.packs if "calls" in p),
                  "patrols": sum(len(p["patrols"]["rows"]) for p in self.packs if "patrols" in p),
                  "shift": len(self.packs[at]["shift"]["entries"]) if at is not None else 0}
        for kind, _, _, _ in KINDS:
            made = any(kind in p for p in self.packs)
            self.tabs[kind].set_count(counts[kind] if made and (kind != "shift" or counts[kind]) else None)
        hint = "Press Make logs to see the form here." if not self.packs else "Not made this time - tick it under the button to include it."
        shown = self.patrol_day if self.patrol_day is not None and self.patrol_day < len(self.packs) and "patrols" in self.packs[self.patrol_day] else None
        patrol = self.packs[shown]["patrols"] if shown is not None else next((p["patrols"] for p in self.packs if "patrols" in p), None)
        self.patrol_view.show((lambda g: forms.draw_patrols(g, patrol["meta"], patrol["rows"])) if patrol else None, hint)
        shift = next((p["shift"] for p in self.packs if "shift" in p), None)
        self.shift_view.show((lambda g: forms.draw_shift(g, shift["meta"], shift)) if shift else None, hint)
        self.show_summary()
        self.refresh_buttons()
        for cells in (self.call_cells, self.patrol_cells, self.shift_cells):
            cells.reposition()

    def day_of(self, idx):
        return self.packs[idx]["date"]

    # ---- correcting a call time or response ----
    def call_for(self, key):
        return next((c for c in self.packs[key[0]]["calls"]["calls"] if calllog.edit_key(c) == key[1]), None)

    def call_text(self, key, field):
        call = self.call_for(key)
        return "" if call is None else call["call_text" if field == "time" else "response"]

    def commit_call(self, key, field, text):
        """Save a typed time or response, rebuild that log and rewrite its PDF and spreadsheet.
        Returns a message if the typed value cannot be right."""
        pack, call, text, value = self.packs[key[0]], self.call_for(key), text.strip(), None
        log = pack["calls"]
        if call is None:
            return None
        try:
            if field == "time":
                value = calllog.read_time(text, log) if text else None
                if value == (call["call_text"] or None) or (value is None and not call["timed"]):
                    return None                       # nothing changed
                calllog.save_edit(log, call, "time", value)
            else:
                if text == call["response"]:
                    return None
                if not call["call_text"]:
                    raise calllog.Problem("That call has no time yet - give it a time first.")
                calllog.save_edit(log, call, "response", text if text and text != calllog.RESPONSE else None)
            pack["calls"] = calllog.build(pack["date"], self.made_roster, log["meta"]["supervisor"])
            again = self.call_for(key)
            if field == "time" and value is None and again and again["edited"] and not again["call_text"]:
                calllog.save_edit(log, again, "response", None)      # the row is blank again: a response typed with that time goes too
                pack["calls"] = calllog.build(pack["date"], self.made_roster, log["meta"]["supervisor"])
        except calllog.Problem as e:
            return str(e)
        files = pack["calls_files"] = calllog.write_outputs(pack["calls"], self.out_dir)
        self.repack(key[0])
        self.draw_tables()
        same_minute = field == "time" and value and sum(1 for c in pack["calls"]["calls"] if c["call_text"] == value) > 1
        again = self.call_for(key)
        self.saved(files, "Saved. Note: another call has the same time." if same_minute
                   else "Saved. Note: %s is still to come - the call is logged ahead of being made." % value if field == "time" and value and again and again["ahead"]
                   else "Saved - the PDF and spreadsheet are updated.")
        return None

    def saved(self, files, message):
        for p in files["problems"]:
            self.note("stop", p[:1].upper() + p[1:])
        self.tell("Your change is saved, but a file could not be updated - see the note below." if files["problems"] else message,
                  "warn" if files["problems"] or "Note:" in message or "run past" in message else "ok")

    # ---- filling in ahead ----
    def meta_of(self, pack):
        return next(pack[k] for k, _, _, _ in KINDS if k in pack)["meta"]

    def still_going(self):
        """The days on show whose shift is not over yet (there is something still to come on them)."""
        return [i for i, p in enumerate(self.packs) if self.minutes_gone(i) < self.meta_of(p)["end_min"]]

    def filled_ahead(self, pack):
        return bool(pack.get("calls", {"meta": {}})["meta"].get("ahead") or pack.get("patrols", {}).get("ahead"))

    def fill_ahead(self):
        """Fill in what is still to come on the shifts that are not over - every call time with "Ok", every usual
        round - or, pressed again, leave it blank. The controller's own choice for the shift (asked for on
        2026-10-11): the sheet is complete now, and whatever turns out different is corrected afterwards."""
        if self.busy or not self.settle():
            return
        going = self.still_going()
        if not going:
            self.tell("Every shift on show is over, so nothing is still to come.", "warn")
            return
        on = not all(self.filled_ahead(self.packs[i]) for i in going)
        problems, calls, rounds = [], 0, 0
        for i in going:
            pack = self.packs[i]
            who = self.meta_of(pack)["supervisor"]
            for kind, _, build, write in KINDS[:2]:   # the check calls and the patrols; a shift log is only ever what was typed
                if kind in pack:
                    calllog.fill_ahead(pack[kind], on)
                    pack[kind] = build(pack["date"], self.made_roster, who)
                    pack[kind + "_files"] = write(pack[kind], self.out_dir)
                    problems += pack[kind + "_files"]["problems"]
            calls += pack["calls"]["meta"]["early"] if "calls" in pack else 0
            rounds += sum(1 for r in pack["patrols"]["rows"] if r["ahead"]) if "patrols" in pack else 0
            self.repack(i)
        self.draw_tables()
        count = lambda n, word: "%d %s%s" % (n, word, "" if n == 1 else "s")
        self.saved({"problems": problems}, "The rest of the shift is blank again: only what has happened is filled in." if not on else
                   "Filled in to the end of the shift. Note: %s and %s are ahead of the clock (in blue) - change any that turn out different."
                   % (count(calls, "call"), count(rounds, "round")))

    # ---- the patrols ----
    def chosen(self, cells, tree):
        """The rows a button acts on: the ones picked in the list, and the one being typed in."""
        keys = [cells.keys[i] for i in tree.selection() if i in cells.keys]
        if cells.box and cells.box[1] not in keys:
            keys.append(cells.box[1])
        return keys

    def minutes_gone(self, idx):
        """Minutes from the midnight that day began with until now."""
        return int((datetime.datetime.now() - datetime.datetime.combine(self.day_of(idx), datetime.time())).total_seconds() // 60)

    def patrol_row(self, key):
        return next((r for r in self.packs[key[0]]["patrols"]["rows"] if r["id"] == key[1]), None)

    def patrol_text(self, key, field):
        row = self.patrol_row(key)
        return "" if row is None else row[field + "_text"] if field in ("start", "finish") else row[field]

    def day_said(self, idx, word=" on "):
        """' on Sat 03 Oct' while several days are on show, so it is plain which day's log is meant; else nothing."""
        return word + self.packs[idx]["patrols"]["day"][:10] if sum(1 for p in self.packs if "patrols" in p) > 1 else ""

    def patrol_pack(self):
        """The day the patrol buttons act on - or None, with the reason in the status bar, when that cannot be told.
        In order: the day of the rows chosen or being typed in; a day whose heading or "no patrols" line was
        clicked; the day last worked on; the shift that is on now; the only day there is. It never guesses
        between several days: a button that wipes a log must not land on the wrong one."""
        days = [i for i, p in enumerate(self.packs) if "patrols" in p]
        picked = {day for day, _ in self.chosen(self.patrol_cells, self.ptree)}
        picked |= {self.day_rows[i] for i in self.ptree.selection() if i in self.day_rows}
        live = [i for i in days if self.packs[i]["patrols"]["meta"]["start_min"] <= self.minutes_gone(i) <= self.packs[i]["patrols"]["meta"]["end_min"]]
        if len(picked) == 1:
            day = picked.pop()
        elif len(picked) > 1:
            self.tell("Rows of more than one day are chosen - click a row of the one day you mean.", "warn")
            return None
        elif self.patrol_day in days:
            day = self.patrol_day
        elif len(live) == 1 or len(days) == 1:
            day = (live or days)[0]
        else:
            if days:
                self.tell("Several days are on show - click a row of the day you mean first.", "warn")
                self.root.bell()
            return None
        self.patrol_day = day
        return day

    def redo_patrols(self, idx, message="Saved - the patrol log PDF is updated.", row=None, covered=()):
        """After a change to that day's patrols: build the log again from what is saved, rewrite the PDF, redraw.
        row: the patrol just added or changed, covered: the usual rounds that were off the list before it - so
        that whatever else the change did (a usual round it took the place of, an overlap) is said too."""
        pack = self.packs[idx]
        log = pack["patrols"] = calllog.build_patrols(pack["date"], self.made_roster, pack["patrols"]["meta"]["supervisor"])
        files = pack["patrols_files"] = calllog.write_patrols(log, self.out_dir)
        self.patrol_day = idx
        self.repack(idx)
        self.draw_tables()
        mine = next((r for r in log["rows"] if r["id"] == row), None)
        gone = [c for c in log["covered"] if c not in covered] if row else []
        short = message.split(" - ")[0].rstrip(".") + "."
        if gone:
            message = "%s Note: the usual round at %s was at the same time, so it is off the list." % (short, " and ".join(gone))
        elif mine and any(r is not mine and r["start"] < mine["finish"] and mine["start"] < r["finish"] for r in log["rows"]):
            message = "%s Note: this patrol overlaps another one on the list." % short
        elif mine and mine["ahead"]:
            message = "%s Note: %s is still to come - the patrol is logged ahead of being walked." % (short, mine["finish_text"])
        self.saved(files, message)

    def commit_patrol(self, key, field, text):
        """Save what was typed in one cell of a patrol. Returns why, if it cannot be right."""
        row, text = self.patrol_row(key), text.strip()
        if row is None:
            return None
        log, message = self.packs[key[0]]["patrols"], "Saved - the patrol log PDF is updated."
        try:
            if field in ("start", "finish"):
                if not text:
                    raise calllog.Problem("A patrol needs a start and a finish - Remove takes the whole patrol out.")
                at = calllog.shift_clock(calllog.read_time(text, log), log["meta"])
                if at == row[field]:
                    return None
                change = {field: calllog.fmt(at).zfill(5)}
                if field == "finish":
                    if at < row["start"]:
                        raise calllog.Problem("A patrol cannot finish before it starts (%s)." % row["start_text"])
                    change["finish_by"] = "hand"      # a finish typed by hand stays put when the start is corrected later
                elif not (row["finish_typed"] and at < row["finish"]):
                    # the finish goes with the start, keeping the patrol as long as it was - as far as the shift allows
                    length = row["finish"] - row["start"]
                    finish = min(at + (length if length >= 5 else 15), log["meta"]["end_min"])
                    if finish <= at:
                        raise calllog.Problem("%s leaves the patrol no time before the shift ends." % calllog.fmt(at))
                    change.update(finish=calllog.fmt(finish).zfill(5), finish_by="")
                    message = "Saved. The finish moved with it, to %s." % change["finish"]
            elif text == row[field]:
                return None
            else:
                change = {field: text}
        except calllog.Problem as e:
            return str(e)
        covered = list(log["covered"])
        calllog.patrol_set(log, key[1], **change)
        self.redo_patrols(key[0], message, key[1], covered)
        return None

    def add_patrol(self):
        if not self.settle():
            return
        idx = self.patrol_pack()
        if idx is None or self.busy:
            return
        pack = self.packs[idx]                        # the list as it stands this minute: a round may have finished since it was drawn
        log = pack["patrols"] = calllog.build_patrols(pack["date"], self.made_roster, pack["patrols"]["meta"]["supervisor"])
        try:
            start, finish = calllog.patrol_slot(log)
        except calllog.Problem as e:
            self.redo_patrols(idx)
            self.tell(str(e), "warn")
            self.root.bell()
            return
        covered = list(log["covered"])
        new = calllog.patrol_add(log, calllog.fmt(start).zfill(5), calllog.fmt(finish).zfill(5))
        self.redo_patrols(idx, "Patrol added%s - set when it started and finished; Enter moves along the row." % self.day_said(idx), new, covered)
        self.patrol_cells.open((idx, new), "start")

    def remove_patrol(self):
        picked = self.chosen(self.patrol_cells, self.ptree)
        self.patrol_cells.close(cancel=True)          # whatever was half-typed in a patrol that is going is not wanted
        if not self.settle():
            return
        if not picked or self.busy:
            self.tell("Click a patrol in the list first, then Remove.", "warn")
            return
        days = sorted({day for day, _ in picked})     # rows of more than one day may be chosen: each day's log is done
        for day in days:
            calllog.patrol_remove(self.packs[day]["patrols"], [row for d, row in picked if d == day])
            self.redo_patrols(day, "Removed. Usual rounds brings back a round that was taken out.")

    def clear_patrols(self):
        if not self.settle():
            return
        idx = self.patrol_pack()
        if idx is None or self.busy:
            return
        log = self.packs[idx]["patrols"]
        if any(r["edited"] for r in log["rows"]) and not messagebox.askokcancel("Control Room Logs", (
                "Clear every patrol on the log%s?\n\nThe ones typed or changed by hand cannot be brought back.") % self.day_said(idx, " for ")):
            return
        calllog.patrol_clear(log)
        self.redo_patrols(idx, "All patrols cleared%s - the form prints blank to write on. Usual rounds puts the three rounds back." % self.day_said(idx))

    def usual_rounds(self):
        if not self.settle():
            return
        idx = self.patrol_pack()
        if idx is None or self.busy:
            return
        log = self.packs[idx]["patrols"]
        if any(r["edited"] for r in log["rows"]) and not messagebox.askokcancel("Control Room Logs", (
                "Go back to the usual rounds%s?\n\nEvery patrol typed or changed by hand on that log is forgotten.") % self.day_said(idx, " for ")):
            return
        calllog.patrol_reset(log)
        self.redo_patrols(idx, "Back to the usual rounds%s - each is listed once it is done." % self.day_said(idx))

    # ---- the shift log ----
    def shift_pack(self):
        """The day the shift log tab is filling in: the first one made."""
        return next((i for i, p in enumerate(self.packs) if "shift" in p), None)

    def shift_entry(self, key):
        return next((e for e in self.packs[key[0]]["shift"]["entries"] if e["id"] == key[1]), None)

    def shift_text(self, key, field):
        entry = self.shift_entry(key)
        return entry[field] if entry else ""

    def fill_extras(self):
        """Put the saved handover notes and names of the day on show into their boxes (after a run, not while typing)."""
        at = self.shift_pack()
        log = self.packs[at]["shift"] if at is not None else {"handover": "", "supervisor": "", "incoming": ""}
        self.filling = True
        self.handover.configure(state="normal")
        self.handover.delete("1.0", "end")
        self.handover.insert("1.0", log["handover"])
        self.supervisor.set(log["supervisor"])
        self.incoming.set(log["incoming"])
        self.filling = False
        self.refresh_buttons()

    def extras_typed(self, *_):
        """Typing in the handover box or a name: save shortly after the typing stops."""
        if self.filling or self.shift_pack() is None:
            return
        if self.extras_job:
            self.root.after_cancel(self.extras_job)
        self.extras_job = self.root.after(700, self.save_extras)

    def save_extras(self):
        if self.extras_job:
            self.root.after_cancel(self.extras_job)
            self.extras_job = None
        at = self.shift_pack()
        if at is None or self.busy or self.filling:
            return
        log = self.packs[at]["shift"]
        typed = (self.handover.get("1.0", "end").strip(), self.supervisor.get().strip(), self.incoming.get().strip())
        if typed != (log["handover"], log["supervisor"], log["incoming"]):
            self.redo_shift(None, at)

    def redo_shift(self, entries, idx=None, message=None):
        """Keep these lines (None: as they are) with whatever is in the boxes, rewrite the PDF, redraw."""
        idx = self.shift_pack() if idx is None else idx
        if idx is None or self.busy:
            return
        pack = self.packs[idx]
        calllog.save_shift(pack["shift"], pack["shift"]["entries"] if entries is None else entries,
                           self.handover.get("1.0", "end"), self.supervisor.get(), self.incoming.get())
        pack["shift"] = calllog.build_shift(pack["date"], self.made_roster, pack["shift"]["meta"]["supervisor"])
        files = pack["shift_files"] = calllog.write_shift(pack["shift"], self.out_dir)
        self.repack(idx)
        self.draw_tables()
        lines = len(forms.wrap(pack["shift"]["handover"], "F1", 10.5, forms.WIDTH - 8))
        self.saved(files, "Saved, but the handover notes run past the six lines on the form - shorten them." if lines > 6
                   else message or "Saved - the shift log PDF is updated.")

    def commit_shift(self, key, field, text):
        """Save what was typed in one cell of a shift log line. Returns why, if it cannot be right."""
        entry, text = self.shift_entry(key), text.strip()
        if entry is None or text == entry[field]:
            return None
        log = self.packs[key[0]]["shift"]
        try:
            if field == "time" and text:              # a line may be a little before the shift starts or after it ends
                text = calllog.read_time(text, log, early=calllog.EARLY, late=calllog.GRACE).zfill(5)
        except calllog.Problem as e:
            return str(e)
        if text == entry[field]:
            return None
        if self.fresh == key:
            self.fresh = None                         # something was typed in it: a real line now
        self.redo_shift([dict(e, **{field: text}) if e["id"] == key[1] else e for e in log["entries"]], key[0])
        return None

    def add_entry(self):
        if not self.settle():
            return
        at = self.shift_pack()
        if at is None or self.busy:
            return
        log = self.packs[at]["shift"]
        gone = self.minutes_gone(at)                  # during the shift a new line starts at the time now
        live = log["meta"]["start_min"] - calllog.EARLY <= gone <= log["meta"]["end_min"] + calllog.GRACE
        new = {"id": calllog.new_entry_id(log), "time": calllog.fmt(gone).zfill(5) if live else "", "area": "", "text": ""}
        self.redo_shift(log["entries"] + [new], at, "New line - type %s; Enter moves along the row." % (
            "the place and what happened" if live else "the time, the place and what happened"))
        self.fresh = (at, new["id"])
        self.shift_cells.open(self.fresh, "area" if live else "time")

    def entry_left(self, key):
        """The typing box has left a shift log line: a new line that nothing was typed in is taken away again."""
        if key != self.fresh:
            return
        self.fresh = None
        entry, why = self.shift_entry(key), self.shift_cells.refused
        if entry and not entry["area"] and not entry["text"]:
            self.redo_shift([e for e in self.packs[key[0]]["shift"]["entries"] if e["id"] != key[1]], key[0],
                            "Nothing was typed, so the new line was taken away.")
            if why:                                   # what was typed was not accepted: say that, not "nothing was typed"
                self.tell("%s - so the new line was taken away." % why.rstrip("."), "warn")

    def remove_entry(self):
        picked = self.chosen(self.shift_cells, self.stree)
        self.shift_cells.close(cancel=True)           # whatever was half-typed in a line that is going is not wanted
        if not self.settle():
            return
        if not picked or self.busy:
            self.tell("Click a line in the list first, then Remove.", "warn")
            return
        idx = picked[0][0]
        drop = {line for day, line in picked if day == idx}
        self.redo_shift([e for e in self.packs[idx]["shift"]["entries"] if e["id"] not in drop], idx, "Removed - the shift log PDF is updated.")

    def clear_entries(self):
        if not self.settle():
            return
        at = self.shift_pack()
        if at is None or self.busy:
            return
        if self.packs[at]["shift"]["entries"] and not messagebox.askokcancel(
                "Control Room Logs", "Clear every line typed on this shift log?\n\nThey cannot be brought back. The handover notes stay."):
            return
        self.redo_shift([], at, "Lines cleared - the table prints blank to write on.")

    # ---- opening and closing ----
    def repack(self, idx):
        """Rewrite the all-in-one PDF of a day after one of its forms changed."""
        pack = self.packs[idx]
        made = {k: pack[k] for k, _, _, _ in KINDS if k in pack}
        if len(made) > 1:
            pack["all_files"] = calllog.write_pack(made, self.out_dir)

    def open_pack(self):
        if not self.settle():
            return
        for p in self.packs:
            if p.get("all_files", {}).get("pdf"):
                self.launch(p["all_files"]["pdf"])

    def open_files(self, what):
        if not self.settle():
            return
        for p in self.packs:
            path = p.get(self.tab + "_files", {}).get(what)
            if path:
                self.launch(path)

    def open_folder(self):
        folder = self.out_dir or calllog.LOGS
        os.makedirs(folder, exist_ok=True)
        self.launch(folder)

    def launch(self, path):
        try:
            os.startfile(path)
        except OSError as e:
            self.status.set("Could not open %s: %s" % (os.path.basename(path), e))

    def on_close(self):
        """Let a log in progress finish, then go. Nothing else is running: the tool starts no other program."""
        self.settle(keep=False)                       # keep whatever is being typed
        if self.busy:
            self.closing = True
            self.status.set("Finishing the log in progress, then closing...")
            return
        self.gone = True
        for cells in (self.call_cells, self.patrol_cells, self.shift_cells):
            cells.hide_tip()
        for job in (self.poll_job, self.tick_job, self.extras_job):      # nothing is left waiting to run
            if job:
                self.root.after_cancel(job)
        self.root.destroy()


def never_activate():
    """For automated checks only (CCL_QUIET): refuse to become the active window, so a window opened to be pictured
    cannot take the keyboard from whoever is using the PC. A hook on this one thread of this one process."""
    import ctypes
    from ctypes import wintypes
    user, kernel = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
    proc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user.SetWindowsHookExW.argtypes, user.SetWindowsHookExW.restype = [ctypes.c_int, proc, ctypes.c_void_p, wintypes.DWORD], ctypes.c_void_p
    user.CallNextHookEx.argtypes, user.CallNextHookEx.restype = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM], ctypes.c_ssize_t
    kernel.GetCurrentThreadId.restype = wintypes.DWORD
    never_activate.hook = proc(lambda code, w, l: 1 if code in (5, 9) else user.CallNextHookEx(None, code, w, l))   # HCBT_ACTIVATE, HCBT_SETFOCUS
    user.SetWindowsHookExW(5, never_activate.hook, None, kernel.GetCurrentThreadId())                                # WH_CBT


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)                                   # crisp text on high-DPI screens
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NORTHWIND.CheckCallLog")   # own taskbar icon
    except Exception:
        pass
    use_theme(os.environ.get("CCL_THEME") or load_settings().get("theme"))      # the look chosen last time
    if os.environ.get("CCL_QUIET"):                   # a window opened for an automated check must never take the keyboard
        never_activate()
    root = tk.Tk()
    try:
        App(root)
    except Exception:
        messagebox.showerror("Control Room Logs", "The window could not start:\n\n" + traceback.format_exc())
        raise
    root.mainloop()


if __name__ == "__main__":
    main()
