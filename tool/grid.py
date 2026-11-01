"""A table drawn on a canvas, for the three logs: a fixed heading, rows of cells, a visible current cell and one
typing box.

Why not ttk.Treeview: in Tk 8.6 it can only colour whole rows, has a single row height and nothing per cell, so it
cannot show which cells may be typed in, mark one value as typed by hand, or run a "now" rule between two rows.
Here every cell is ours to draw. The rules the typing follows are the ones the 5.x reviews settled:

- a row is known by its key, never by where it sits, so the box and the current cell follow a row that moves;
- where a key leads is worked out BEFORE the value is saved (saving may re-sort the rows);
- a value that cannot be right keeps the box open with what was typed and says why right under the cell;
- a redraw underneath an open box moves the box, it never closes it.
"""
import tkinter as tk


class Grid(tk.Frame):
    """columns: dicts with field, title, width (px at 100 %), grow (share of spare width), align ('w', 'e', 'center'),
    edit (may be typed in), choices (words offered under the cell while it is typed in: one click puts one in),
    give (narrows like a growing column, takes no spare width), press (a box to click: its cell text is 'on', 'off', 'busy' or '' for none), tone ('ink',
    'ink2', 'ink3'), strong (heading in the strong face).
    text_of(key, field) -> the text to start typing from; commit(key, field, text) -> why not, or None;
    left(key) is told when typing leaves a row altogether; moved(key, field) when the current cell changes;
    press(key, field) when a press box is clicked.
    enter: 'down' (Enter goes down the column) or 'along' (Enter goes along the row, then stops)."""

    def __init__(self, parent, skin, columns, text_of, commit, enter="down", left=None, busy=None, status=None, moved=None,
                 row=36, head=32, gutter=0, press=None):
        super().__init__(parent, bg=skin["panel"])
        self.skin, self.columns = skin, columns
        self.text_of, self.commit, self.enter = text_of, commit, enter
        self.press = press or (lambda key, field: None)
        self.presses = {c["field"] for c in columns if c.get("press")}
        self.left, self.busy, self.status, self.moved = left or (lambda key: None), busy or (lambda: False), status or (lambda text: None), moved or (lambda *a: None)
        px = skin.px
        self.row_h, self.head_h, self.rule_h, self.gutter, self.pad = px(row), px(head), px(24), px(gutter), px(12)
        self.order = [c["field"] for c in columns if c.get("edit")]
        self.rows, self.keys, self.tops, self.spans, self.empty = [], [], [], {}, None
        self.cursor = None                           # (key, field): the current cell
        self.chosen = set()                          # keys of the rows picked (for Remove)
        self.box = None                              # (entry, key, field) while a cell is open for typing
        self.picker = None                           # the choices offered under it, if its column has some
        self.box_from = None                         # what the open cell held when it was opened (untouched: the same)
        self.tip = None                              # (key, field, text) while a refusal is on show
        self.refused = None                          # why the value typed last was turned down, if it was
        self.hover = None
        self.head = tk.Canvas(self, height=self.head_h, bg=skin["panel"], highlightthickness=0)
        self.body = tk.Canvas(self, bg=skin["panel"], highlightthickness=0, takefocus=1, yscrollincrement=1, height=self.row_h * 3)
        self.bar = tk.Canvas(self, width=px(12), bg=skin["panel"], highlightthickness=0)
        self.head.grid(row=0, column=0, sticky="ew")
        self.body.grid(row=1, column=0, sticky="nsew")
        self.bar.grid(row=1, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.body.configure(yscrollcommand=self.scrolled)
        self.body.bind("<Configure>", lambda e: self.draw())
        self.body.bind("<Button-1>", self.click)
        self.body.bind("<MouseWheel>", self.wheel)
        self.body.bind("<Motion>", self.motion)
        self.body.bind("<Leave>", lambda e: self.point(None))
        self.bar.bind("<Button-1>", self.drag)
        self.bar.bind("<B1-Motion>", self.drag)
        for sequence, how in (("<Down>", "down"), ("<Up>", "up"), ("<Right>", "right"), ("<Left>", "left"), ("<Tab>", "next"), ("<Shift-Tab>", "back"),
                              ("<Home>", "first"), ("<End>", "last"), ("<Next>", "page"), ("<Prior>", "pageup")):
            self.body.bind(sequence, lambda e, how=how: self.step(how) or "break")
        for sequence in ("<Return>", "<KP_Enter>", "<F2>"):
            self.body.bind(sequence, lambda e: self.edit_current() or "break")
        for sequence in ("<Delete>", "<BackSpace>"):
            self.body.bind(sequence, lambda e: self.reset_current() or "break")
        self.body.bind("<Key>", self.typed)

    # ---- what is on show ----
    def show(self, rows, empty=None):
        """rows: dicts. A data row has key, cells {field: text} and may have: dim (still to come), bar (the accent bar
        at its left), band (a faint tint), gutter (text in the left margin), tones {field: role}, marks {field: 'hand' |
        'ahead'}, paint {field: (fill role, ink role)} (the cell drawn on a coloured box), note (True: an amber mark at
        its right end). {'rule': text} is a labelled rule between rows.
        empty: (heading, line) to show when there are no rows."""
        self.rows, self.empty = rows, empty
        self.keys = [r["key"] for r in rows if "key" in r]
        self.chosen &= set(self.keys)
        if self.cursor and self.cursor[0] not in self.keys:
            self.cursor = None
        if self.tip and self.tip[0] not in self.keys:
            self.tip = None
        self.draw()
        if self.box and self.box[1] not in self.keys:
            self.close(cancel=True)                  # its row is no longer there

    def layout(self, width):
        """Where each column sits for this width: {field: (x0, x1)}."""
        px, fixed = self.skin.px, sum(self.skin.px(c["width"]) for c in self.columns)
        room = width - self.gutter - px(4)
        spare, shares = max(0, room - fixed), sum(c.get("grow", 0) for c in self.columns) or 1
        # A table too narrow for its columns: the ones that hold words give way first, down to under half their width,
        # so that numbers and times stay whole (squeezed evenly, the row numbers once vanished altogether).
        give = lambda c: c.get("grow") or c.get("give")      # (give: narrows with the word columns, takes no spare width)
        flex = sum(px(c["width"]) for c in self.columns if give(c))
        soft =min(1.0, max(0.45, (room - (fixed - flex)) / float(flex or 1)))
        hard = min(1.0, room / float((fixed - flex) + flex * soft))
        spans, x = {}, self.gutter
        for c in self.columns:
            w = int(px(c["width"]) * (soft if give(c) else 1.0) * hard) + spare * c.get("grow", 0) // shares
            spans[c["field"]] = (x, x + w)
            x += w
        return spans

    def draw(self):
        skin, b, px = self.skin, self.body, self.skin.px
        width = max(b.winfo_width(), px(200))
        self.spans = self.layout(width)
        b.delete("draw")
        self.head.delete("all")
        self.head.create_rectangle(0, self.head_h - 1, width + px(12), self.head_h, fill=skin["line"], outline="")
        for c in self.columns:
            x0, x1 = self.spans[c["field"]]
            anchor, x = {"e": ("e", x1 - self.pad), "center": ("center", (x0 + x1) // 2)}.get(c.get("align"), ("w", x0 + self.pad))
            self.head.create_text(x, self.head_h // 2, text=skin.fit("caption", c["title"], x1 - x0 - self.pad), font=skin.font("caption"), fill=skin["ink3"], anchor=anchor)
        y, self.tops = 0, []
        for row in self.rows:
            if "rule" in row:                         # a labelled rule: "now 03:12", a change of date
                h, tone = self.rule_h, skin[row.get("tone", "accent")]
                self.tops.append((y, h, None))
                label = b.create_text(self.gutter + self.pad, y + h // 2, text=row["rule"], font=skin.font("caption"), fill=tone, anchor="w", tags="draw")
                b.create_rectangle(b.bbox(label)[2] + px(8), y + h // 2, width - px(8), y + h // 2 + max(1, px(1)), fill=tone, outline="", tags="draw")
                y += h
                continue
            h, key = self.row_h, row["key"]
            self.tops.append((y, h, key))
            if key in self.chosen:
                b.create_rectangle(0, y, width, y + h, fill=skin["chosen"], outline="", tags="draw")
            elif row.get("band"):
                b.create_rectangle(0, y, width, y + h, fill=skin["band"], outline="", tags="draw")
            if row.get("bar"):
                b.create_rectangle(0, y + px(5), px(3), y + h - px(5), fill=skin["accent"], outline="", tags="draw")
            if row.get("gutter"):
                b.create_text(self.gutter - px(6), y + h // 2, text=row["gutter"], font=skin.font("caption"), fill=skin["ink3"], anchor="e", tags="draw")
            b.create_rectangle(self.gutter + px(4), y + h - 1, width - px(4), y + h, fill=skin["line"], outline="", tags="draw")
            for c in self.columns:
                field = c["field"]
                x0, x1 = self.spans[field]
                text, mark = row["cells"].get(field, ""), row.get("marks", {}).get(field)
                if field in self.presses:             # a box to click: empty, ticked on the roster's green, or being sent
                    if text:
                        cx, cy, s = (x0 + x1) // 2, y + h // 2, px(9)
                        if text == "on":
                            skin.box(b, cx - s, cy - s, cx + s, cy + s, px(4), skin["confirm"], skin["confirm_edge"], tags="draw")
                            b.create_image(cx, cy, image=skin.mark("check", px(15), skin["on_confirm"]), tags="draw")
                        elif text == "busy":
                            skin.box(b, cx - s, cy - s, cx + s, cy + s, px(4), skin["well"], skin["edge"], tags="draw")
                            b.create_text(cx, cy, text="...", font=skin.font("caption"), fill=skin["ink3"], tags="draw")
                        else:
                            skin.box(b, cx - s, cy - s, cx + s, cy + s, px(4), skin["control"], skin["strong"], tags="draw")
                    continue
                if c.get("edit"):                     # a shallow well says "this one can be typed in"
                    skin.box(b, x0 + px(3), y + px(4), x1 - px(3), y + h - px(4), px(4), skin["well_chosen" if key in self.chosen else "well"], None, tags="draw")
                paint = row.get("paint", {}).get(field)
                if paint:                             # the cell on a coloured box (a name that is green on the roster)
                    skin.box(b, x0 + px(3), y + px(4), x1 - px(3), y + h - px(4), px(4), skin[paint[0]], None, tags="draw")
                role = "data"
                tone = skin[paint[1] if paint else row.get("tones", {}).get(field) or ("ink3" if row.get("dim") else c.get("tone", "ink"))]
                room = x1 - x0 - 2 * self.pad - (px(12) if mark else 0)
                anchor, x = {"e": ("e", x1 - self.pad - (px(12) if mark else 0)), "center": ("center", (x0 + x1) // 2)}.get(c.get("align"), ("w", x0 + self.pad))
                b.create_text(x, y + h // 2, text=skin.fit(role, text, room), font=skin.font(role), fill=tone, anchor=anchor, tags="draw")
                if mark:                              # on screen only: typed by hand (a dot), ahead of the clock (a ring)
                    image = skin.mark("dot", px(6), skin["ink3"]) if mark == "hand" else skin.mark("ring", px(9), skin["accent"])
                    b.create_image(x1 - px(9), y + h // 2, image=image, tags="draw")
            if row.get("note"):
                b.create_text(width - px(14), y + h // 2, text=skin.icon("warning") or "!", font=skin.font("icon_small"), fill=skin["amber"], tags="draw")
            y += h
        if not self.keys and self.empty:
            said = b.create_text(width // 2, px(22), text=self.empty[0], font=skin.font("strong"), fill=skin["ink2"], width=width - px(60), justify="center", anchor="n", tags="draw")
            under = b.bbox(said)[3] + px(8)
            more = b.create_text(width // 2, under, text=self.empty[1], font=skin.font("body"), fill=skin["ink3"], width=width - px(60), justify="center", anchor="n", tags="draw")
            y = max(y, b.bbox(more)[3] + px(10))
        self.height = y
        b.configure(scrollregion=(0, 0, width, max(y + px(34), 1)))
        b.tag_lower("draw")
        self.place_marks()

    def place_marks(self):
        """The things that sit on top of the rows: the row under the pointer, the current cell, the typing box, a refusal."""
        skin, b, px = self.skin, self.body, self.skin.px
        b.delete("over")
        if self.hover in self.keys and self.hover not in self.chosen:
            y, h = self.where(self.hover)
            b.create_rectangle(0, y, b.winfo_width(), y + h - 1, fill="", outline=skin["edge"], tags=("over", "hover"))
        if self.cursor and not self.box:
            at = self.cell(*self.cursor)
            if at:
                x0, y0, x1, y1 = at
                b.create_rectangle(x0 + px(2), y0 + px(3), x1 - px(2), y1 - px(3), outline=skin["ring"], width=px(2), tags="over")
        if self.box:
            entry, key, field = self.box
            at = self.cell(key, field)
            if at:
                x0, y0, x1, y1 = at
                skin.box(b, x0 + px(2), y0 + px(3), x1 - px(2), y1 - px(3), px(4), skin["raised"], skin["red" if self.tip else "accent"], tags="over", edge_width=px(2))
                b.create_window(x0 + px(8), y0 + px(6), window=entry, anchor="nw", width=x1 - x0 - px(16), height=y1 - y0 - px(12), tags="over")
                if self.picker is not None:
                    self.picker.update_idletasks()
                    tall, wide = self.picker.winfo_reqheight(), self.picker.winfo_reqwidth()
                    bottom, top = b.canvasy(b.winfo_height()), b.canvasy(0)
                    below = not self.tip and (y1 + tall + px(2) <= bottom or y0 - tall - px(2) < top)
                    x = max(px(2), min(x0 + px(2), b.winfo_width() - wide - px(2)))
                    b.create_window(x, y1 + px(1) if below else y0 - px(1), window=self.picker, anchor="nw" if below else "sw", tags="over")
        if self.tip and self.cell(self.tip[0], self.tip[1]):
            x0, y0, x1, y1 = self.cell(self.tip[0], self.tip[1])
            text = b.create_text(0, 0, text=self.tip[2], font=skin.font("caption"), fill=skin["red"], anchor="nw", width=max(px(160), b.winfo_width() - px(40)), tags="over")
            tx0, ty0, tx1, ty1 = b.bbox(text)
            wide, tall = tx1 - tx0 + px(16), ty1 - ty0 + px(8)
            x = max(px(4), min(x0, b.winfo_width() - wide - px(4)))
            y = y1 + px(1) if y1 + tall < max(self.height + px(30), b.winfo_height()) else y0 - tall - px(1)
            skin.box(b, x, y, x + wide, y + tall, px(4), skin["red_tint"], skin["red"], tags="over")
            b.coords(text, x + px(8), y + px(4))
            b.tag_raise(text)

    def where(self, key):
        return next(((y, h) for y, h, k in self.tops if k == key), (0, 0))

    def cell(self, key, field):
        """(x0, y0, x1, y1) of a cell in the canvas's own coordinates, or None."""
        if key not in self.keys or field not in self.spans:
            return None
        y, h = self.where(key)
        return self.spans[field][0], y, self.spans[field][1], y + h

    def cell_on_screen(self, key, field):
        """The middle of a cell in the widget's coordinates (after scrolling it into view) - what a click needs."""
        self.see(key)
        at = self.cell(key, field)
        return ((at[0] + at[2]) // 2, int((at[1] + at[3]) // 2 - self.body.canvasy(0))) if at else None

    def values(self):
        """The rows as tuples of what is shown, top to bottom."""
        return [tuple(r["cells"].get(c["field"], "") for c in self.columns) for r in self.rows if "key" in r]

    def picked(self):
        """The rows a button acts on: the ones chosen, and the one being typed in or holding the current cell."""
        keys = [k for k in self.keys if k in self.chosen]
        for extra in (self.box and self.box[1], self.cursor and self.cursor[0]):
            if extra is not None and extra in self.keys and extra not in keys and (self.box or not keys):
                keys.append(extra)
        return keys

    # ---- scrolling ----
    def scrolled(self, lo, hi):
        skin, bar, px = self.skin, self.bar, self.skin.px
        bar.delete("all")
        lo, hi = float(lo), float(hi)
        if hi - lo < 0.999:
            tall = bar.winfo_height()
            skin.box(bar, px(3), int(lo * tall) + px(2), px(9), max(int(hi * tall) - px(2), int(lo * tall) + px(14)), px(3), skin["edge"], None)

    def wheel(self, event):
        self.body.yview_scroll(int(-event.delta / 120.0 * self.row_h * 2), "units")
        return "break"

    def drag(self, event):
        lo, hi = self.body.yview()
        self.body.yview_moveto(max(0.0, event.y / float(max(1, self.bar.winfo_height())) - (hi - lo) / 2.0))

    def see(self, key):
        if key not in self.keys:
            return
        y, h = self.where(key)
        top, tall, whole = self.body.canvasy(0), self.body.winfo_height(), float(max(1, self.height + self.skin.px(34)))
        if y < top:
            self.body.yview_moveto(max(0, y - self.skin.px(4)) / whole)
        elif y + h > top + tall:
            self.body.yview_moveto((y + h - tall + self.skin.px(4)) / whole)

    # ---- the mouse ----
    def at(self, event):
        y = self.body.canvasy(event.y)
        key = next((k for top, h, k in self.tops if top <= y < top + h), None)
        field = next((f for f, (x0, x1) in self.spans.items() if x0 <= event.x < x1), None)
        return key, field

    def motion(self, event):
        key, field = self.at(event)
        pressable = key is not None and field in self.presses and self.state_of(key, field) in ("on", "off")
        self.body.configure(cursor="xterm" if key is not None and field in self.order else "hand2" if pressable else "")
        self.point(key)

    def state_of(self, key, field):
        return next((r["cells"].get(field, "") for r in self.rows if r.get("key") == key), "")

    def point(self, key):
        if key != self.hover:
            self.hover = key
            self.place_marks()

    def click(self, event):
        key, field = self.at(event)
        if key is not None and field in self.presses and self.state_of(key, field) in ("on", "off") and not event.state & 0x0004:
            if not self.close() and self.box:        # (a refused value stays open: deal with it first)
                return "break"
            self.chosen = {key}
            self.draw()
            self.press(key, field)
            return "break"
        wanted = key is not None and field in self.order and not self.busy()
        if not self.close(to=key if wanted else None) and self.box:
            return "break"                           # (a refused value that a key kept open stays; a click lets it go)
        if key is None:
            return "break"
        if event.state & 0x0004 and not wanted:      # Ctrl: add this row to the ones chosen, or take it out
            self.chosen ^= {key}
        else:
            self.chosen = {key}
        self.body.focus_set()
        if wanted:
            self.open(key, field)
        else:
            self.draw()
            self.moved(key, field)
        return "break"

    # ---- the current cell (keys, when nothing is open) ----
    def set_cursor(self, key, field, show=True):
        if key in self.keys and field in self.order:
            self.cursor = (key, field)
            if show:
                self.see(key)
            self.place_marks()
            self.moved(key, field)

    def step(self, how):
        """Move the current cell: down / up / left / right / next / back / first / last / page / pageup."""
        if not self.keys or not self.order:
            return
        key, field = self.cursor or (self.keys[0], self.order[0])
        if not self.cursor:
            return self.set_cursor(key, field)
        at, col = self.keys.index(key), self.order.index(field)
        per_page = max(1, self.body.winfo_height() // self.row_h - 1)
        if how in ("down", "up"):
            at += 1 if how == "down" else -1
        elif how in ("page", "pageup"):
            at += per_page if how == "page" else -per_page
        elif how in ("first", "last"):
            at = 0 if how == "first" else len(self.keys) - 1
        elif how in ("right", "next"):
            at, col = (at, col + 1) if col + 1 < len(self.order) else ((at + 1, 0) if how == "next" else (at, col))
        else:
            at, col = (at, col - 1) if col else ((at - 1, len(self.order) - 1) if how == "back" else (at, col))
        at = min(len(self.keys) - 1, max(0, at))
        self.set_cursor(self.keys[at], self.order[col])

    def edit_current(self):
        if self.cursor and not self.busy():
            self.open(*self.cursor)

    def reset_current(self):
        """Delete on the current cell: empty it (the owner decides what an empty value means - usually 'as generated')."""
        if self.cursor and not self.busy() and self.text_of(*self.cursor) != "":
            key, field = self.cursor
            problem = self.commit(key, field, "")
            self.refused = problem and problem[:1].upper() + problem[1:]
            if problem:
                self.status(self.refused)
                self.show_tip(key, field, self.refused)

    def typed(self, event):
        """A letter or digit on the current cell starts typing there, replacing what it holds."""
        if self.cursor and not self.busy() and event.char and event.char.isprintable() and not event.state & 0x0004:
            self.open(self.cursor[0], self.cursor[1], text=event.char, select=False)
            return "break"
        return None

    # ---- typing in a cell ----
    def open(self, key, field, text=None, select=True):
        """Put the typing box over a cell (text: what to show in it instead of the cell's own value). True if it opened."""
        if key not in self.keys or field not in self.order:
            return False
        if self.box:
            self.box[0].destroy()
        self.drop_picker()
        skin = self.skin
        column = next(c for c in self.columns if c["field"] == field)
        entry = tk.Entry(self.body, font=skin.font("data"), justify={"e": "right", "center": "center"}.get(column.get("align"), "left"), relief="flat", bd=0,
                         bg=skin["raised"], fg=skin["ink"], insertbackground=skin["ink"], selectbackground=skin["accent"], selectforeground=skin["on_accent"],
                         highlightthickness=0, insertwidth=skin.px(2))
        entry.insert(0, self.text_of(key, field) if text is None else text)
        if select:
            entry.select_range(0, "end")
        entry.icursor("end")
        self.box, self.cursor, self.box_from = (entry, key, field), (key, field), entry.get()
        if column.get("choices"):                    # a few words to pick from, or type anything
            px = skin.px
            self.picker = tk.Frame(self.body, bg=skin["raised"], highlightthickness=1, highlightbackground=skin["edge"])
            for word in column["choices"]:
                chip = tk.Label(self.picker, text=word, font=skin.font("caption"), bg=skin["control"], fg=skin["ink"], padx=px(9), pady=px(3),
                                cursor="hand2", highlightthickness=1, highlightbackground=skin["edge"])
                chip.pack(side="left", padx=(px(4), 0), pady=px(4))
                chip.bind("<Button-1>", lambda e, word=word: self.pick(word) or "break")
                chip.bind("<Enter>", lambda e, chip=chip: chip.configure(bg=skin["hover"]))
                chip.bind("<Leave>", lambda e, chip=chip: chip.configure(bg=skin["control"]))
            keys = " / ".join(w[0].lower() for w in column["choices"])
            tk.Label(self.picker, text="or type (%s)" % keys, font=skin.font("caption"), bg=skin["raised"], fg=skin["ink3"]).pack(side="left", padx=px(8))

            def complete(event, words=column["choices"]):
                """A letter that starts just one choice puts it in, the rest selected: n + Enter is No answer.
                Typing on replaces the rest, so anything else can still be written."""
                if not event.char or not event.char.isprintable() or event.keysym in ("BackSpace", "Delete"):
                    return
                typed = entry.get()[:entry.index("insert")]
                hits = [w for w in words if typed and w.lower().startswith(typed.lower())]
                if len(hits) == 1 and entry.get().lower() == typed.lower() and hits[0] != entry.get():
                    entry.delete(0, "end")
                    entry.insert(0, hits[0])
                    entry.icursor(len(typed))
                    entry.select_range(len(typed), "end")
            entry.bind("<KeyRelease>", complete)
        self.chosen = {key}
        self.see(key)
        self.draw()
        entry.focus_set()
        for event, move in (("<Return>", "enter"), ("<KP_Enter>", "enter"), ("<Tab>", "next"), ("<Shift-Tab>", "back"), ("<Down>", "down"), ("<Up>", "up")):
            entry.bind(event, lambda e, move=move: self.close(move=move) and None or "break")
        entry.bind("<Escape>", lambda e: self.close(cancel=True) and None or "break")
        entry.bind("<FocusOut>", lambda e: self.close() and None)
        entry.bind("<Key>", lambda e: self.hide_tip())             # typing again takes the reason away
        entry.bind("<MouseWheel>", self.wheel)
        self.moved(key, field)
        return True

    def close(self, cancel=False, move=None, to=None):
        """Shut the open cell, saving it unless cancelled, then go where the key that was pressed says (move).
        to: the row a click is about to open the box on, so the owner is not told that row was left.
        False if the value was refused; with a move (a key, or "hold" = just save it) the box then stays open, saying why."""
        if not self.box:
            return True
        entry, key, field = self.box
        text = entry.get()
        # where the key leads is settled now, from the rows as they stood when it was pressed: the change about to be
        # saved may put them in another order, and "the next row" must still mean the one that was next
        target = self.target(key, field, move) if move else None
        self.refused = None
        if not cancel:
            self.box = None                          # (so that a redraw during the save does not move a box that is going)
            problem = self.commit(key, field, text)
            if problem:
                self.refused = problem[:1].upper() + problem[1:]
                self.status(self.refused)
                if move and key in self.keys:        # stay in the cell, with what was typed, and say why right there
                    self.box, self.tip = (entry, key, field), (key, field, self.refused)
                    self.place_marks()
                    entry.focus_set()
                    return False
                self.tip = (key, field, self.refused) if key in self.keys else None
        self.box = None
        entry.destroy()
        self.drop_picker()
        if not self.refused:
            self.tip = None
        if (target[0] if target else to) != key:
            self.left(key)
        if key not in self.keys:
            self.tip = None                          # the row went too (a new line nothing was typed in): so does the note
        if target and self.open(*target):
            return not self.refused
        if move or cancel:                           # typing ended with a key: the table keeps the keyboard
            self.body.focus_set()
        self.draw()
        return not self.refused

    def untouched(self):
        """An open cell nothing has been typed in (so the table may be built again under it)."""
        return bool(self.box) and self.box[0].get() == self.box_from

    def refill(self):
        """An untouched open cell shows what its cell holds now (after the table was built again)."""
        if self.untouched():
            entry, key, field = self.box
            text = self.text_of(key, field)
            if text != entry.get():
                entry.delete(0, "end")
                entry.insert(0, text)
                entry.select_range(0, "end")
            self.box_from = entry.get()

    def pick(self, word):
        """One of the choices under the open cell was clicked: it goes in, and is saved."""
        if self.box:
            self.box[0].delete(0, "end")
            self.box[0].insert(0, word)
            self.close(move="hold")

    def drop_picker(self):
        if self.picker is not None:
            self.picker.destroy()
            self.picker = None

    def target(self, key, field, move):
        """The cell a key leads to from this one, as (key, field), or None to stop typing."""
        if move == "hold" or key not in self.keys or field not in self.order:
            return None
        at, col = self.keys.index(key), self.order.index(field)
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
        return (self.keys[at], self.order[col]) if 0 <= at < len(self.keys) else None

    def show_tip(self, key, field, text):
        self.tip = (key, field, text)
        self.place_marks()

    def hide_tip(self):
        if self.tip:
            self.tip = None
            self.place_marks()
