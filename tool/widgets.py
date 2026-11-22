"""The parts the window is made of, each drawn on a canvas from the skin: buttons, tabs, chips, rounded panels and
fields, notices, the shift ribbon, the month calendar and the page preview.

Rules they all keep (from the research behind the redesign, see CLAUDE.md): nothing moves, fades or makes a sound;
hover and pressed are instant; every control that can be clicked can also be reached with Tab and pressed with
Space or Enter, and shows a ring when the keyboard is on it; an icon never stands without its word; colour is
never the only sign of anything.
"""
import calendar
import datetime
import tkinter as tk


def inside(widget):
    """Is the mouse over this widget? (Moving on to something inside it must not count as leaving.)"""
    under = widget.winfo_containing(*widget.winfo_pointerxy())
    while under is not None:
        if under is widget:
            return True
        under = under.master
    return False


class Control(tk.Canvas):
    """What buttons, tabs and chips share: a canvas that takes the keyboard, is pressed by the mouse, Space or Enter,
    and redraws itself for rest / hover / pressed / off / keyboard-on-it."""

    def __init__(self, parent, skin, command, width, height, back):
        super().__init__(parent, width=width, height=height, bg=skin[back], highlightthickness=0, takefocus=1, cursor="hand2")
        self.skin, self.command, self.back = skin, command, back
        self.enabled, self.over, self.down, self.keyed = True, False, False, False
        self.bind("<Enter>", lambda e: self.state(over=True))
        self.bind("<Leave>", lambda e: self.state(over=False, down=False))
        self.bind("<Button-1>", lambda e: self.state(down=True))
        self.bind("<ButtonRelease-1>", self.release)
        self.bind("<FocusIn>", lambda e: self.state(keyed=True))
        self.bind("<FocusOut>", lambda e: self.state(keyed=False))
        for key in ("<space>", "<Return>", "<KP_Enter>"):
            self.bind(key, lambda e: self.press() or "break")

    def state(self, **change):
        for name, value in change.items():
            setattr(self, name, value)
        self.paint()

    def release(self, event):
        hit = self.down and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self.state(down=False)
        if hit:
            self.press()

    def press(self):
        if self.enabled and self.command:
            self.command()

    def able(self, on):
        """Switch the control on or off."""
        if on != self.enabled:
            self.enabled = on
            self.configure(cursor="hand2" if on else "", takefocus=1 if on else 0)
            self.paint()

    def ring(self, x0, y0, x1, y1, radius):
        """The mark that the keyboard is on this control (drawn in the margin kept for it)."""
        if self.keyed and self.enabled:
            px = self.skin.px
            self.skin.box(self, x0 - px(2), y0 - px(2), x1 + px(2), y1 + px(2), radius + px(2), None, self.skin["ring"], edge_width=px(2))


class Button(Control):
    """kind: 'accent' (the one main action), 'plain' (outlined), 'quiet' (bare until the mouse is on it).
    icon: a name from skin.ICONS; keys: the shortcut, printed on the button in fainter ink."""

    def __init__(self, parent, skin, text, command, icon=None, kind="plain", keys="", back="panel", height=32):
        self.text, self.icon, self.kind, self.keys = text, icon, kind, keys
        self.lit = False                              # shown as "on": the panel this button opens and closes is out
        self.h = skin.px(height)
        super().__init__(parent, skin, command, self.wide(skin, text), self.h + skin.px(6), back)
        self.paint()

    def wide(self, skin, text):
        px = skin.px
        glyph = px(16 + 8) if skin.icon(self.icon) else 0
        hint = px(10) + skin.measure("caption", self.keys) if self.keys else 0
        return px(12) + glyph + skin.measure("body", text) + hint + px(12) + px(6)

    def light(self, on):
        """Show the button as on (a soft tint with an edge) or as usual."""
        if on != self.lit:
            self.lit = on
            self.paint()

    def label(self, text):
        """Change what the button says."""
        if text != self.text:
            self.text = text
            self.configure(width=self.wide(self.skin, text))
            self.paint()

    def paint(self):
        skin, px = self.skin, self.skin.px
        self.delete("all")
        x0, y0, x1, y1 = px(3), px(3), int(self["width"]) - px(3), px(3) + self.h
        if self.lit and self.enabled:
            fill = skin["pressed"] if self.down else skin["tint"]
            edge, ink, faint = skin["tint_edge"], skin["ink"], skin["ink2"]
        elif self.kind == "accent":
            fill = skin["off_fill"] if not self.enabled else skin["accent_pressed"] if self.down else skin["accent_hover"] if self.over else skin["accent"]
            edge, ink, faint = None, skin["on_accent"] if self.enabled else skin["off"], skin["on_accent"]
        elif self.kind == "plain":
            fill = skin[self.back] if not self.enabled else skin["pressed"] if self.down else skin["hover"] if self.over else skin["control"]
            edge, ink, faint = skin["line"] if not self.enabled else skin["edge"], skin["ink"] if self.enabled else skin["off"], skin["ink3"]
        else:
            fill = None if not self.enabled or not (self.over or self.down) else skin["pressed"] if self.down else skin["hover"]
            edge, ink, faint = None, skin["ink2"] if self.enabled else skin["off"], skin["ink3"]
        if fill or edge:
            skin.box(self, x0, y0, x1, y1, px(4), fill, edge)
        self.ring(x0, y0, x1, y1, px(4))
        x, mid = x0 + px(12), (y0 + y1) // 2
        if skin.icon(self.icon):
            self.create_text(x + px(8), mid, text=skin.icon(self.icon), font=skin.font("icon"), fill=ink)
            x += px(16 + 8)
        self.create_text(x, mid - px(1), text=self.text, font=skin.font("body"), fill=ink, anchor="w")
        if self.keys:
            self.create_text(x1 - px(12), mid, text=self.keys, font=skin.font("caption"), fill=faint if self.enabled else skin["off"], anchor="e")


class Tab(Control):
    """One of the three forms: its name, how many rows it has, its shortcut; the one on show has the accent underline."""

    def __init__(self, parent, skin, text, command, keys="", back="base"):
        self.text, self.keys, self.count, self.chosen = text, keys, None, False
        super().__init__(parent, skin, command, 10, skin.px(40), back)
        self.paint()

    def show(self, chosen, count):
        if (chosen, count) != (self.chosen, self.count):
            self.chosen, self.count = chosen, count
            self.paint()

    def paint(self):
        skin, px = self.skin, self.skin.px
        self.delete("all")
        number = "" if self.count is None else str(self.count)
        wide = px(14) + skin.measure("strong", self.text) + (px(8) + skin.measure("caption", number) + px(12) if number else 0) + px(14)
        self.configure(width=wide)
        tall = px(40)
        if self.over and not self.chosen:
            skin.box(self, px(2), px(4), wide - px(2), tall - px(6), px(4), skin["hover"], None)
        ink = skin["ink"] if self.chosen else skin["ink2"]
        self.create_text(px(14), tall // 2 - px(2), text=self.text, font=skin.font("strong" if self.chosen else "body"), fill=ink, anchor="w")
        if number:
            x = px(14) + skin.measure("strong", self.text) + px(8)
            w = skin.measure("caption", number) + px(12)
            self.create_image(x, tall // 2 - px(2) - px(9), image=skin.shape(w, px(18), px(9), skin["tint"] if self.chosen else skin["sunk"], None), anchor="nw")
            self.create_text(x + w // 2, tall // 2 - px(2), text=number, font=skin.font("caption"), fill=skin["ink"] if self.chosen else skin["ink3"])
        if self.chosen:
            self.create_rectangle(px(12), tall - px(3), wide - px(12), tall, fill=skin["accent"], outline="")
        self.ring(px(4), px(5), wide - px(4), tall - px(7), px(4))


class Chip(Control):
    """A choice among a few (a controller, a shift, a day): tinted with an edge when it is the one chosen.
    dot: a filled dot before the words ("on shift now" - the words say so too). sub: a second, fainter part."""

    def __init__(self, parent, skin, text, command, sub="", dot=False, back="panel", wide=None, height=32):
        self.text, self.sub, self.dot, self.chosen, self.fixed = text, sub, dot, False, wide
        self.h = skin.px(height)
        super().__init__(parent, skin, command, 10, self.h + skin.px(6), back)
        self.bind("<Configure>", lambda e: self.paint())
        self.paint()

    def choose(self, on):
        if on != self.chosen:
            self.chosen = on
            self.paint()

    def paint(self):
        skin, px = self.skin, self.skin.px
        self.delete("all")
        need = px(12) + (px(14) if self.dot else 0) + skin.measure("body", self.text) + (px(10) + skin.measure("caption", self.sub) if self.sub else 0) + px(12) + px(6)
        if self.fixed is None and int(self["width"]) != need:
            self.configure(width=need)
        wide = max(self.winfo_width(), need) if self.fixed else need
        x0, y0, x1, y1 = px(3), px(3), wide - px(3), px(3) + self.h
        fill = skin["tint"] if self.chosen else skin["pressed"] if self.down else skin["hover"] if self.over else skin["control"]
        skin.box(self, x0, y0, x1, y1, px(4), fill, skin["tint_edge"] if self.chosen else skin["edge"])
        self.ring(x0, y0, x1, y1, px(4))
        x, mid = x0 + px(12), (y0 + y1) // 2
        if self.dot:
            self.create_image(x, mid - px(4), image=skin.mark("dot", px(8), skin["accent"]), anchor="nw")
            x += px(14)
        self.create_text(x, mid - px(1), text=self.text, font=skin.font("strong" if self.chosen else "body"), fill=skin["ink"], anchor="w")
        if self.sub:
            self.create_text(x1 - px(12), mid, text=self.sub, font=skin.font("caption"), fill=skin["ink3"], anchor="e")


class Panel(tk.Canvas):
    """A rounded panel holding other widgets: put them in .inner. back = the surface it sits on, fill = its own."""

    def __init__(self, parent, skin, back="base", fill="panel", edge="edge", pad=1, radius=8):
        super().__init__(parent, bg=skin[back], highlightthickness=0)
        self.skin, self.fill, self.edge, self.pad, self.radius = skin, fill, edge, skin.px(pad), skin.px(radius)
        self.inner = tk.Frame(self, bg=skin[fill])
        self.bind("<Configure>", lambda e: self.paint())

    def paint(self):
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("all")
        if w > 4 and h > 4:
            self.skin.box(self, 0, 0, w, h, self.radius, self.skin[self.fill], self.skin[self.edge] if self.edge else None)
            self.create_window(self.pad, self.pad, window=self.inner, anchor="nw", width=w - 2 * self.pad, height=h - 2 * self.pad)


class Field(tk.Canvas):
    """A rounded box to type one line in. The edge turns to the accent while the keyboard is in it."""

    def __init__(self, parent, skin, variable, back="panel", width=180, state="normal"):
        px = skin.px
        super().__init__(parent, width=px(width), height=px(32), bg=skin[back], highlightthickness=0)
        self.skin = skin
        self.entry = tk.Entry(self, textvariable=variable, font=skin.font("body"), relief="flat", bd=0, bg=skin["control"], fg=skin["ink"],
                              insertbackground=skin["ink"], selectbackground=skin["accent"], selectforeground=skin["on_accent"], highlightthickness=0,
                              disabledbackground=skin["control"], disabledforeground=skin["off"], state=state)
        self.entry.bind("<FocusIn>", lambda e: self.paint(), add="+")
        self.entry.bind("<FocusOut>", lambda e: self.paint(), add="+")
        self.bind("<Configure>", lambda e: self.paint())
        self.bind("<Button-1>", lambda e: self.entry.focus_set())

    def paint(self):
        skin, px = self.skin, self.skin.px
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("all")
        on = self.focus_get() is self.entry
        skin.box(self, 0, 0, w, h, px(4), skin["control"], skin["accent"] if on else skin["edge"], edge_width=px(2) if on else 1)
        self.create_window(px(10), px(5), window=self.entry, anchor="nw", width=w - px(20), height=h - px(10))


class Notice(tk.Canvas):
    """One line that needs the controller: 'check' (amber), 'wrong' (red) or 'word' (plain). An icon, the words, and at
    most one thing to press. It stays until what it says is no longer so - it never fades by itself."""

    def __init__(self, parent, skin, back="panel"):
        super().__init__(parent, height=1, bg=skin[back], highlightthickness=0)
        self.skin, self.kind, self.text, self.action = skin, None, "", None
        self.button = None
        self.bind("<Configure>", lambda e: self.paint())

    def say(self, kind=None, text="", action=None):
        """action: (label, command) or None. kind None hides the notice."""
        if (kind, text) != (self.kind, self.text) or action != self.action:
            self.kind, self.text, self.action = kind, text, action
            self.paint()

    def paint(self):
        skin, px = self.skin, self.skin.px
        self.delete("all")
        had_focus = self.button is not None and self.focus_get() is self.button
        if self.button:
            self.button.destroy()
            self.button = None
        if not self.kind:
            self.configure(height=1)
            return
        w = max(self.winfo_width(), px(200))
        tone, tint, icon = {"check": ("amber", "amber_tint", "warning"), "wrong": ("red", "red_tint", "error")}.get(self.kind, ("ink2", "sunk", "info"))
        action_width = 0
        if self.action:
            self.button = Button(self, skin, self.action[0], self.action[1], kind="plain", back=tint, height=26)
            action_width = int(self.button.cget("width"))
        stacked = bool(self.action and w - px(60) - action_width < px(160))
        room = w - px(52) - (action_width + px(12) if self.action and not stacked else 0)
        words = self.create_text(px(40), px(9), text=self.text, font=skin.font("body"), fill=skin["ink"],
                                 anchor="nw", width=max(px(40), room), tags="notice_text")
        text_bottom = self.bbox(words)[3]
        tall = max(px(36), text_bottom + px(9))
        if stacked:
            tall = text_bottom + px(8 + 26 + 9)
        self.configure(height=tall + px(6))
        skin.box(self, 0, 0, w, tall, px(4), skin[tint], None)
        self.create_rectangle(0, px(4), px(3), tall - px(4), fill=skin[tone], outline="")
        self.create_text(px(22), tall // 2, text=skin.icon(icon) or "!", font=skin.font("icon"), fill=skin[tone])
        self.tag_raise(words)
        if self.action:
            self.create_window(px(40) if stacked else w - px(8), text_bottom + px(8) if stacked else tall // 2,
                               window=self.button, anchor="nw" if stacked else "e", tags="notice_action")
            if had_focus:
                self.button.focus_set()


class Ribbon(tk.Canvas):
    """The shift as one line of time: an upright tick for every check call (taller where several fall together), a bar
    for every patrol, a diamond for every shift log line, the hours underneath, and a rule at the time it is now.
    Different shapes, not different colours. Clicking an hour tells the owner (to bring that hour into view)."""

    def __init__(self, parent, skin, on_pick=None, back="panel"):
        super().__init__(parent, bg=skin[back], highlightthickness=0, height=skin.px(64), width=300)
        self.skin, self.data, self.on_pick = skin, None, on_pick or (lambda minute: None)
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self.click)

    def show(self, data):
        """data: None, or dict(date, start, end, calls [(minute, made)], patrols [(a, b)], lines [minute], now text)."""
        self.data = data
        self.draw()

    def span(self):
        w, px = self.winfo_width(), self.skin.px
        return float(px(18)), float(w - px(18))

    def click(self, event):
        d = self.data
        if d and d["end"] > d["start"]:
            x0, x1 = self.span()
            self.on_pick(d["start"] + (min(max(event.x, x0), x1) - x0) * (d["end"] - d["start"]) / (x1 - x0))

    def draw(self):
        skin, px = self.skin, self.skin.px
        self.delete("all")
        w, d = self.winfo_width(), self.data
        if w < px(160):
            return
        if not d or d["end"] <= d["start"]:
            self.create_text(w // 2, px(30), text="The shift is drawn here: its calls, patrols and log lines, and the time now.", font=skin.font("caption"), fill=skin["ink3"])
            return
        a, b = d["start"], d["end"]
        x0, x1 = self.span()
        y = px(34)
        x = lambda minute: x0 + (min(max(minute, a), b) - a) * (x1 - x0) / float(b - a)
        now = (datetime.datetime.now() - datetime.datetime.combine(d["date"], datetime.time())).total_seconds() / 60.0
        self.create_rectangle(x0, y, x1, y + px(2), fill=skin["edge"], outline="")
        if now > a:                                   # the part of the shift already gone is the darker line
            self.create_rectangle(x0, y, x(now), y + px(2), fill=skin["ink3"], outline="")
        for hour in range(int(-(-a // 60)), int(b // 60) + 1):
            xh = int(x(hour * 60))
            self.create_rectangle(xh, y + px(6), xh + 1, y + px(10), fill=skin["ink3"], outline="")
            if hour % 2 == 0 or b - a <= 480:
                self.create_text(xh, y + px(20), text="%02d" % (hour % 24), font=skin.font("caption"), fill=skin["ink2"] if hour * 60 <= now else skin["ink3"])
        for start, finish in d["patrols"]:
            self.create_rectangle(x(start), y - px(4), max(x(finish), x(start) + px(6)), y + px(6), fill=skin["ink2"], outline=skin[self.cget_back()], width=1)
        slots = {}                                    # five-minute slot -> [how many calls, all of them made?]
        for minute, made in d["calls"]:
            slot = slots.setdefault(int(minute // 5), [0, True])
            slot[0], slot[1] = slot[0] + 1, slot[1] and made
        for slot, (count, made) in slots.items():
            tall = px(6) if count == 1 else px(10) if count == 2 else px(14)
            xs = int(x(slot * 5))
            self.create_rectangle(xs, y - px(6) - tall, xs + px(3), y - px(6), fill=skin["ink"] if made else skin["off"], outline="")
        for minute in d["lines"]:
            self.create_image(int(x(minute)), y + px(1), image=skin.mark("diamond", px(11), skin["ink"]))
        live, xn = a <= now <= b, int(x(min(max(now, a), b)))
        if live:
            half = skin.measure("caption", d.get("now", "")) // 2 + px(4)
            self.create_rectangle(xn, y - px(22), xn + px(2), y + px(11), fill=skin["accent"], outline="")
            self.create_text(min(max(xn, px(16) + half), w - px(16) - half), px(6), text=d.get("now", ""), font=skin.font("caption"), fill=skin["accent"])
        # What the shapes mean, on the top line - at whichever end the "now" label is not, and left out altogether
        # where there is no room for both (the label once ran straight through it).
        if live and w < px(640):
            return
        if live and xn > w // 2:
            xk = px(18)
            for shape, word in (("diamond", "log line"), ("tick", "check call"), ("bar", "patrol")):
                if shape == "bar":
                    self.create_rectangle(xk, px(3), xk + px(12), px(10), fill=skin["ink2"], outline="")
                    xk += px(18)
                elif shape == "tick":
                    self.create_rectangle(xk, px(1), xk + px(3), px(11), fill=skin["ink"], outline="")
                    xk += px(9)
                else:
                    self.create_image(xk + px(6), px(6), image=skin.mark("diamond", px(11), skin["ink"]))
                    xk += px(18)
                xk = self.bbox(self.create_text(xk, px(6), text=word, font=skin.font("caption"), fill=skin["ink3"], anchor="w"))[2] + px(14)
            return
        xk = w - px(16)
        for shape, word in (("bar", "patrol"), ("tick", "check call"), ("diamond", "log line")):
            xk = self.bbox(self.create_text(xk, px(6), text=word, font=skin.font("caption"), fill=skin["ink3"], anchor="e"))[0] - px(6)
            if shape == "bar":
                self.create_rectangle(xk - px(12), px(3), xk, px(10), fill=skin["ink2"], outline="")
                xk -= px(26)
            elif shape == "tick":
                self.create_rectangle(xk - px(3), px(1), xk, px(11), fill=skin["ink"], outline="")
                xk -= px(17)
            else:
                self.create_image(xk - px(6), px(6), image=skin.mark("diamond", px(11), skin["ink"]))
                xk -= px(26)

    def cget_back(self):
        return next(role for role in ("panel", "base", "raised") if self.skin[role] == self["bg"])


class Calendar(tk.Canvas):
    """A month. The chosen controller's shift days are in strong ink with a mark under the number - a filled dot for
    a night shift, a ring for a day shift (shape, not colour); a chosen day is the accent disc; today has a ring."""

    def __init__(self, parent, skin, on_pick, shift_kind, back="panel"):
        px = skin.px
        super().__init__(parent, bg=skin[back], highlightthickness=0, width=px(280), height=px(30 + 20 + 6 * 34 + 24), takefocus=1)
        self.skin, self.on_pick, self.shift_kind = skin, on_pick, shift_kind       # shift_kind(date) -> "day", "night" or None
        self.today = datetime.date.today()
        self.year, self.month, self.chosen, self.cells = self.today.year, self.today.month, [], {}
        self.cursor, self.focused = self.today, False
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self.click)
        self.bind("<FocusIn>", lambda e: self.focus_changed(True))
        self.bind("<FocusOut>", lambda e: self.focus_changed(False))
        self.bind("<KeyPress>", self.key)

    def focus_changed(self, focused):
        self.focused = focused
        self.draw()

    def key(self, event):
        """Move a visible date cursor without changing a log until Enter/Space is pressed."""
        steps = {"Left": -1, "Right": 1, "Up": -7, "Down": 7}
        try:
            if event.keysym in steps:
                self.cursor += datetime.timedelta(days=steps[event.keysym])
            elif event.keysym in ("Prior", "Next"):
                self.turn(-1 if event.keysym == "Prior" else 1)
                return "break"
            elif event.keysym in ("Home", "End"):
                self.cursor = self.today if event.keysym == "Home" and event.state & 0x0004 else datetime.date(
                    self.year, self.month, 1 if event.keysym == "Home" else calendar.monthrange(self.year, self.month)[1])
            elif event.keysym in ("Return", "KP_Enter", "space"):
                self.on_pick([self.cursor], add=bool(event.state & 0x0004))
                return "break"
            else:
                return None
        except (ValueError, OverflowError):
            return "break"                      # calendar bounds never escape as a Tk callback error
        self.year, self.month = self.cursor.year, self.cursor.month
        self.draw()
        return "break"

    def turn(self, step):
        month = self.month - 1 + step
        year, month = self.year + month // 12, month % 12 + 1
        if not 1 <= year <= 9999:
            return
        self.year, self.month = year, month
        self.cursor = datetime.date(year, month, min(self.cursor.day, calendar.monthrange(year, month)[1]))
        self.draw()

    def show(self, chosen):
        """Mark the chosen days. The month on view only changes when a newly chosen day is not in it."""
        chosen = list(chosen)
        if chosen and chosen != self.chosen:
            self.cursor = chosen[-1]
        if chosen and chosen != self.chosen and not any((d.year, d.month) == (self.year, self.month) for d in chosen):
            self.year, self.month = chosen[-1].year, chosen[-1].month
        self.chosen = chosen
        self.draw()

    def draw(self):
        skin, px = self.skin, self.skin.px
        self.delete("all")
        w = max(self.winfo_width(), px(200))
        cw, top, row = w / 7.0, px(30 + 20), px(34)
        self.create_text(w // 2, px(14), text=datetime.date(self.year, self.month, 1).strftime("%B %Y"), font=skin.font("strong"), fill=skin["ink"])
        for name, x in (("prev", px(16)), ("next", w - px(16))):
            self.create_text(x, px(14), text=skin.icon(name) or ("<" if name == "prev" else ">"), font=skin.font("icon_small"), fill=skin["ink2"])
        for col, day in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            self.create_text(int((col + 0.5) * cw), px(38), text=day, font=skin.font("caption"), fill=skin["ink3"])
        self.cells = {}
        for r, week in enumerate(calendar.Calendar(0).monthdayscalendar(self.year, self.month)):
            for col, number in enumerate(week):
                if not number:
                    continue
                d = datetime.date(self.year, self.month, number)
                self.cells[(r, col)] = d
                cx, cy = int((col + 0.5) * cw), top + r * row + px(13)
                kind, picked = self.shift_kind(d), d in self.chosen
                if picked:
                    self.create_image(cx, cy, image=skin.mark("dot", px(28), skin["accent"]))
                elif d == self.today:
                    self.create_image(cx, cy, image=skin.mark("ring", px(28), skin["ink3"]))
                self.create_text(cx, cy - px(1), text=str(d.day), font=skin.font("data_strong" if kind else "body"),
                                 fill=skin["on_accent"] if picked else skin["ink"] if kind else skin["ink3"])
                if kind and not picked:
                    self.create_image(cx, cy + px(15), image=skin.mark("dot" if kind == "night" else "ring", px(6) if kind == "night" else px(7), skin["ink2"]))
                if self.focused and d == self.cursor:
                    self.create_rectangle(cx - px(16), cy - px(16), cx + px(16), cy + px(16),
                                          outline=skin["ink"] if picked else skin["accent"], width=max(1, px(2)), tags="date_focus")
        y = top + 6 * row + px(8)
        x = px(4)
        for shape, word in (("dot", "night shift"), ("ring", "day shift")):
            self.create_image(x + px(4), y, image=skin.mark(shape, px(6) if shape == "dot" else px(7), skin["ink2"]))
            x = self.bbox(self.create_text(x + px(12), y, text=word, font=skin.font("caption"), fill=skin["ink3"], anchor="w"))[2] + px(12)
        self.create_image(x + px(6), y, image=skin.mark("ring", px(12), skin["ink3"]))
        self.create_text(x + px(16), y, text="today", font=skin.font("caption"), fill=skin["ink3"], anchor="w")

    def click(self, event):
        px, w = self.skin.px, max(self.winfo_width(), 1)
        self.focus_set()
        if event.y < px(28):
            if event.x < px(40) or event.x > w - px(40):
                self.turn(-1 if event.x < px(40) else 1)
            return
        d = self.cells.get((int((event.y - px(50)) // px(34)), int(event.x // (w / 7.0))))
        if d:
            self.cursor = d
            self.on_pick([d], add=bool(event.state & 0x0004))     # Ctrl adds another day


class CanvasPage:
    """The same page forms.py draws on for the PDF, drawn on a canvas instead (millimetres -> pixels)."""

    def __init__(self, canvas, skin, scale, ox, oy):
        self.c, self.skin, self.s, self.ox, self.oy = canvas, skin, scale, ox, oy

    def line(self, x1, y1, x2, y2, weight):
        self.c.create_line(self.ox + x1 * self.s, self.oy + y1 * self.s, self.ox + x2 * self.s, self.oy + y2 * self.s,
                           width=max(1, round(weight * self.s)), fill=self.skin["page_ink"])

    def box(self, x, y, w, h, weight=None, grey=None):
        at = (self.ox + x * self.s, self.oy + y * self.s, self.ox + (x + w) * self.s, self.oy + (y + h) * self.s)
        if grey is not None:
            self.c.create_rectangle(*at, fill=self.skin["page_grey"], outline="")
        if weight:
            self.c.create_rectangle(*at, outline=self.skin["page_ink"], width=max(1, round(weight * self.s)))

    def text(self, x, y, h, text, font="F1", size=10.5, align="left", room=None, angle=0):
        import forms
        size = forms.fit(text, font, size, room)
        face = ("Arial", -max(6, round(size * 0.3528 * self.s))) + {"F1": (), "F2": ("bold",), "F3": ("italic",)}[font]
        self.c.create_text(self.ox + x * self.s, self.oy + (y + h / 2) * self.s, text=text, font=face, fill=self.skin["page_ink"],
                           anchor={"left": "w", "right": "e", "center": "center"}[align], angle=-angle)


class Preview(tk.Canvas):
    """The form as it will print: a sheet of paper fitted to the width, moved with the mouse wheel. In the dark look the
    paper is dimmed so it does not glare; the print itself is black on white either way."""

    def __init__(self, parent, skin):
        super().__init__(parent, bg=skin["backdrop"], highlightthickness=0, width=200, height=120)
        self.skin, self.pages, self.hint = skin, [], ""
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<MouseWheel>", lambda e: self.yview_scroll(-1 if e.delta > 0 else 1, "units"))

    def show(self, pages, hint=""):
        """pages: functions that each draw one page on a CanvasPage; none -> the hint instead."""
        self.pages, self.hint = pages, hint
        self.redraw()

    def redraw(self):
        skin, px = self.skin, self.skin.px
        self.delete("all")
        wide = self.winfo_width()
        if wide < 60:
            return
        if not self.pages:
            self.configure(scrollregion=(0, 0, wide, 10))
            self.create_text(wide // 2, px(60), text=self.hint, font=skin.font("body"), fill=skin["ink3"], width=wide - px(40), justify="center")
            return
        margin = px(14)
        scale = (wide - 2 * margin) / 210.0
        tall, y = 297 * scale, margin
        for n, draw in enumerate(self.pages, 1):
            self.create_rectangle(margin, y, wide - margin, y + tall, fill=skin["page"], outline=skin["page_edge"])
            draw(CanvasPage(self, skin, scale, margin, y))
            self.create_rectangle(wide - margin + 1, y, wide, y + tall, fill=skin["backdrop"], outline="")   # (small print too small to shrink runs off the sheet)
            y += tall + margin
        self.configure(scrollregion=(0, 0, wide, y))
        self.yview_moveto(0) if not self.yview()[0] else None
