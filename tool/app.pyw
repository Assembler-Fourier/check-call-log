"""Window for the control-room logs - version 6, rebuilt from nothing in October 2026.

It opens on the shift that is at the desk and shows its three logs at once (there is no "make" button: building them
takes 2 ms). The top line says whose shift it is; "Change" brings out the chooser. Under it the shift is drawn as one
line of time. Then the three forms as tabs: a table you type in on the left, the page as it will print on the right.
Everything typed is saved at once and can be undone; nothing asks "are you sure?", nothing pops up, nothing makes a
sound. Looking at a shift never writes a file: its files are written when something on it is changed and whenever a
button that leads to them is pressed.

The rules for the logs are calllog.py's, the printed forms are forms.py's; the look is skin.py's, the table grid.py's,
the other parts widgets.py's. This file is only how they are put together. It starts no other program.
"""
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
from tkinter import messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import calllog  # noqa: E402
import forms  # noqa: E402
import rostercolour  # noqa: E402
import skin as skinlib  # noqa: E402
from grid import Grid  # noqa: E402
from widgets import Button, Calendar, Chip, Field, Notice, Panel, Preview, Ribbon, Tab  # noqa: E402

SETTINGS = os.path.join(calllog.CACHE, "settings.json")
ICON = os.path.join(HERE, "app.ico")
VERSION = "6.8"
ROSTER_FRESH = 300                # seconds a downloaded roster is reused before fetching it again
WATCH = 30                        # seconds between the small checks of the shift on show (the roster's own columns)
WATCH_FULL = 6                    # ...and every so many checks the whole roster is read again, for colours
FORMS = (("calls", "Check calls"), ("patrols", "Patrols"), ("shift", "Shift log"))
RESPONSES = ["Ok", "No answer", "Left message"]    # offered under a response being typed; only Ok means confirmed (green)
CALL_COLUMNS = [dict(field="no", title="No.", width=44, align="e", tone="ink3"), dict(field="ok", title="Green", width=56, align="center", press=True, give=True),
                dict(field="name", title="Employee name", width=170, grow=3),
                dict(field="venue", title="Venue", width=150, grow=3, tone="ink2"), dict(field="time", title="Time of call", width=104, align="e", edit=True),
                dict(field="resp", title="Response", width=130, grow=2, edit=True, choices=RESPONSES), dict(field="shift", title="Their shift", width=110, tone="ink3")]
PATROL_COLUMNS = [dict(field="no", title="No.", width=44, align="e", tone="ink3"), dict(field="officer", title="Officer", width=150, grow=1, tone="ink2"),
                  dict(field="area", title="Campus / area", width=140, grow=1, edit=True), dict(field="start", title="Start", width=92, align="e", edit=True),
                  dict(field="finish", title="Finish", width=92, align="e", edit=True), dict(field="notes", title="Notes / incidents", width=180, grow=3, edit=True)]
SHIFT_COLUMNS = [dict(field="time", title="Time", width=92, align="e", edit=True), dict(field="area", title="Location / area", width=170, grow=1, edit=True),
                 dict(field="text", title="Occurrence / action taken", width=280, grow=4, edit=True)]


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            saved = json.load(f)
        return saved if isinstance(saved, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        os.makedirs(calllog.CACHE, exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


class App:
    def __init__(self, root, theme="light"):
        self.root = root
        self.queue = queue.Queue()
        self.gone = False
        self.view, self.view_at = None, -1.0         # the roster on show (window thread only)
        self.roster, self.roster_at, self.roster_note = None, 0.0, ""   # the latest download (worker threads, under the lock)
        self.roster_lock = threading.Lock()
        self.roster_sig = None                         # what the last roster read held (calllog.roster_signature)
        self.roster_said = "Reading the roster..."
        self.touched_name = self.touched_date = False
        self.out_dir = os.environ.get("CCL_OUT") or None
        if os.environ.get("CCL_EDITS"):               # automated checks keep their hand corrections apart from the real ones
            calllog.EDITS = os.environ["CCL_EDITS"]
        if os.environ.get("CCL_NO_DOWNLOAD"):         # a window started for an automated check never reaches the live
            rostercolour.send = refuse_send           # roster's script (a check that wants a stand-in puts its own in)
        self.settings = load_settings()

        # open on whoever is at the desk, by the usual week; corrected from the roster once it has been read
        self.name, first_day = calllog.at_the_desk(datetime.datetime.now(), calllog.usual_duty, newest=True)
        self.dates = [first_day]
        self.packs, self.day = [], 0                  # one pack per chosen day: {"date", "calls", "patrols", "shift", "problems"}; the one on show
        self.files = {}                               # (date, controller) -> {"paths", "problems", "at"}: what has been written
        self.undone, self.redone = [], []             # (what, date, controller, files before, files after)
        self.tab = "calls"
        # Both side panels are out to begin with - the chooser on the left, the printed page on the right (asked for on
        # 2026-10-12, with a picture). Each is then remembered as it was left.
        self.choosing = bool(self.settings.get("chooser", True))
        if os.environ.get("CCL_CHOOSER"):             # (automated checks may say which)
            self.choosing = os.environ["CCL_CHOOSER"] == "1"
        self.previewing = bool(self.settings.get("preview", True))
        self.page_forced = False                      # the printed page was asked for while the chooser is out and the window is narrow
        self.fresh = None                             # a shift log line just added that nothing has been typed in yet
        self.extras_job, self.filling = None, False
        self.said, self.said_kind = "", "word"        # what was done last (kept on show until the next thing)
        self.notes_all = False                        # every note on show, not just the first three
        self.stages, self.ended = {}, None            # how each shift on show stood at the last minute; one just finished
        # Confirmed: names turned green (or back) on the roster from here, until a fresh roster copy shows it; and
        # those being sent now. Both keyed by (roster cell, name).
        self.link, self.pending, self.sending, self.reread_job, self.link_box = None, {}, {}, None, None
        # Auto green (a toggle at the top), on or off per controller (auto_for, below): with it on, the roster
        # follows the response. hand: Green-box decisions per shift {stem: {edit key: "green"/"white"}}, kept in a file
        # with the shift (they decide for that name until its response changes); tried: (stem, edit key) -> when.
        # Auto green is on or off for each controller's own shifts (asked for on 2026-10-24): on for Lena Drayton and off
        # for everyone else until they switch their own (a setting kept from 6.6, "auto_green": a name, is carried over).
        chosen = self.settings.get("auto_green_for")
        if not isinstance(chosen, list):
            old = self.settings.get("auto_green")
            chosen = ["Lena Drayton"] + ([old] if isinstance(old, str) and old else [])
        self.auto_for = {n for n in chosen if isinstance(n, str) and n}
        self.desk_was, self.handed = None, None       # who was at the desk at the last minute; a handover just made
        self.hand, self.tried, self.auto_busy, self.auto_job, self.link_warned = {}, {}, False, None, False
        self.view_fresh = False                       # the roster on show was read fresh (not the saved copy at start-up)
        self.quiet_until, self.quiet_step, self.quiet_since = 0.0, 0, None    # back-off while Google refuses
        self.fetching = self.fetch_again = False      # one roster read at a time; another asked for meanwhile: once more
        # The roster is watched: every WATCH seconds a small read of the shift's columns. watch_before: the shift as it
        # was, until a rebuild can show what a new copy changed; watch_said: when the change was seen.
        self.watch_job, self.watch_seen, self.watch_count, self.watch_said, self.watch_before = None, {}, 0, None, None

        root.title("Control Room Logs %s - NORTHWIND Security" % VERSION)
        try:
            root.iconbitmap(default=ICON)
        except tk.TclError:
            pass
        self.skin = skinlib.Skin(root, theme)
        px = self.skin.px
        wide = min(px(1480), root.winfo_screenwidth() - 60)      # room for the chooser, a table with nothing cut short, and the page
        tall = min(px(960), root.winfo_screenheight() - 90)
        root.geometry("%dx%d" % (wide, tall))
        root.minsize(min(px(980), wide), min(px(660), tall))
        self.other, self.supervisor, self.incoming = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.build()
        self.other.trace_add("write", self.other_typed)
        for var in (self.supervisor, self.incoming):
            var.trace_add("write", self.extras_typed)

        keys = {"<Control-Key-1>": lambda: self.show_tab("calls"), "<Control-Key-2>": lambda: self.show_tab("patrols"), "<Control-Key-3>": lambda: self.show_tab("shift"),
                "<Control-p>": self.open_all, "<Control-n>": self.add_row, "<Control-z>": self.undo, "<Control-y>": self.redo, "<F5>": lambda: self.load_roster(force=True),
                "<F8>": self.toggle_preview, "<Control-l>": self.toggle_chooser, "<Control-d>": lambda: root.after(1, self.switch_theme),
                "<Control-g>": self.confirm_picked}
        for sequence, action in keys.items():
            root.bind(sequence, lambda e, action=action: action() or "break")
        root.bind("<Escape>", lambda e: self.toggle_chooser() if self.choosing else None)
        root.bind("<Configure>", self.resized, add="+")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.poll_job = root.after(120, self.poll)
        self.tick_job = root.after(60000 - int(time.time() * 1000) % 60000 + 200, self.tick)      # just after the minute turns
        self.title_job = root.after(80, self.title_bar)
        self.load_roster()
        self.watch_job = root.after(WATCH * 1000, self.watch)
        self.script_job = root.after(5000, self.check_script)      # which version of its script the roster runs
        # the rest is for automated checks of the window
        if os.environ.get("CCL_GEOMETRY"):
            root.geometry(os.environ["CCL_GEOMETRY"])
        if os.environ.get("CCL_QUIT_AFTER", "").isdigit():
            root.after(int(os.environ["CCL_QUIT_AFTER"]) * 1000, self.on_close)
        if os.environ.get("CCL_QUIET"):               # a window opened to be pictured: nobody sees it and no click can land on it
            root.attributes("-alpha", 0.0)
            root.after(300, self.unseen)
        auto = os.environ.get("CCL_AUTORUN")          # "name|date date"
        if auto and "|" in auto:
            who, days = auto.split("|", 1)
            if who:
                self.name, self.touched_name = who, True
            if days:
                self.dates, self.touched_date = calllog.parse_dates(days.split(), datetime.date.today()), True
            self.tab = os.environ.get("CCL_TAB") if os.environ.get("CCL_TAB") in dict(FORMS) else self.tab

    # ---- making the window ----
    def build(self):
        """Make every widget, in the look in use now. Run once at the start and again when the look is changed: all
        the window knows is kept on the App itself, so the new widgets show exactly what the old ones did."""
        root, skin, px = self.root, self.skin, self.skin.px
        month = (self.cal.year, self.cal.month) if hasattr(self, "cal") else None
        for w in root.winfo_children():
            w.destroy()
        root.configure(bg=skin["base"])
        self.buttons, self.tabs, self.pages, self.grids = {}, {}, {}, {}

        # ---- the top line: whose shift this is ----
        head = tk.Frame(root, bg=skin["base"])
        head.pack(fill="x", padx=px(16), pady=(px(10), px(6)))
        who = tk.Frame(head, bg=skin["base"])
        who.pack(side="left")
        self.who_label = tk.Label(who, font=skin.font("subtitle"), bg=skin["base"], fg=skin["ink"], anchor="w")
        self.who_label.pack(anchor="w")
        self.when_label = tk.Label(who, font=skin.font("body"), bg=skin["base"], fg=skin["ink2"], anchor="w")
        self.when_label.pack(anchor="w")
        self.buttons["change"] = Button(head, skin, "Show controller and day", self.toggle_chooser, icon="person", kind="plain", keys="Ctrl+L", back="base")
        self.buttons["change"].pack(side="left", padx=(px(16), 0))
        self.clock = tk.Label(head, font=skin.font("clock"), bg=skin["base"], fg=skin["ink"])
        self.clock.pack(side="right")
        dark = skin.theme == "dark"                   # the button offers the other look
        self.buttons["theme"] = Button(head, skin, "Light look" if dark else "Dark look", lambda: root.after(1, self.switch_theme),
                                       icon="sun" if dark else "moon", kind="quiet", back="base")
        self.buttons["theme"].pack(side="right", padx=(0, px(8)))
        self.buttons["auto"] = Button(head, skin, "Auto green: off", self.toggle_auto, icon="check", kind="quiet", back="base")
        self.buttons["auto"].pack(side="right", padx=(0, px(8)))
        self.show_auto()
        self.state_pill = tk.Label(head, bg=skin["base"], bd=0)
        self.state_pill.pack(side="right", padx=(0, px(10)))
        self.today_label = tk.Label(head, font=skin.font("caption"), bg=skin["base"], fg=skin["ink3"])      # (packed last: the first to give way in a narrow window)
        self.today_label.pack(side="right", padx=(0, px(12)))

        # ---- the strip at the bottom: the roster ----
        foot = tk.Frame(root, bg=skin["sunk"])
        foot.pack(side="bottom", fill="x")
        self.roster_label = tk.Label(foot, text=self.roster_said, font=skin.font("caption"), bg=skin["sunk"], fg=skin["ink3"])
        self.roster_label.pack(side="right", padx=(0, px(8)))
        self.buttons["refresh"] = Button(foot, skin, "Read the roster again", lambda: self.load_roster(force=True), icon="refresh", kind="quiet", keys="F5",
                                         back="sunk", height=24)
        self.buttons["refresh"].pack(side="right", padx=(0, px(4)), pady=px(1))
        tk.Label(foot, text="Type in a cell: Enter moves on, Esc puts it back.  Ctrl+Z undoes anything.", font=skin.font("caption"),
                 bg=skin["sunk"], fg=skin["ink3"]).pack(side="left", padx=px(16))

        body = tk.Frame(root, bg=skin["base"])
        body.pack(fill="both", expand=True, padx=px(16), pady=(0, px(10)))
        self.chooser = Panel(body, skin)              # packed on the left only while it is wanted
        self.build_chooser(self.chooser.inner)
        if month:
            self.cal.year, self.cal.month, self.cal.chosen = month[0], month[1], list(self.dates)
        main = self.main = tk.Frame(body, bg=skin["base"])
        main.pack(side="right", fill="both", expand=True)

        # ---- the shift as one line of time, and the way to the paper ----
        top = Panel(main, skin)
        top.pack(fill="x")
        top.configure(height=px(124))
        self.ribbon = Ribbon(top.inner, skin, self.go_to)
        self.ribbon.pack(fill="x", padx=px(6), pady=(px(8), 0))
        row = tk.Frame(top.inner, bg=skin["panel"])
        row.pack(fill="x", padx=px(14), pady=(px(2), px(8)))
        self.buttons["print"] = Button(row, skin, "Open all to print", self.open_all, icon="print", kind="accent", keys="Ctrl+P")
        self.buttons["print"].pack(side="right")
        self.buttons["folder"] = Button(row, skin, "Folder", self.open_folder, icon="folder", kind="plain")
        self.buttons["folder"].pack(side="right", padx=(0, px(4)))
        self.files_label = tk.Label(row, font=skin.font("caption"), bg=skin["panel"], fg=skin["ink3"])
        self.files_label.pack(side="right", padx=(0, px(12)))
        self.tally = tk.Label(row, font=skin.font("body"), bg=skin["panel"], fg=skin["ink"], anchor="w")
        self.tally.pack(side="left", fill="x", expand=True)

        # ---- the three forms ----
        strip = tk.Frame(main, bg=skin["base"])
        strip.pack(fill="x", pady=(px(8), 0))
        for n, (kind, label) in enumerate(FORMS, 1):
            self.tabs[kind] = Tab(strip, skin, label, lambda kind=kind: self.show_tab(kind), keys="Ctrl+%d" % n)
            self.tabs[kind].pack(side="left")
        self.buttons["preview"] = Button(strip, skin, "Printed page", self.toggle_preview, icon="page", kind="quiet", keys="F8", back="base")
        self.buttons["preview"].pack(side="right")
        self.day_box = tk.Frame(strip, bg=skin["base"])                       # a chip per day, when several days are on show
        self.day_box.pack(side="right", padx=(0, px(12)))

        work = Panel(main, skin)
        work.pack(fill="both", expand=True)
        self.side = tk.Frame(work.inner, bg=skin["panel"])                    # the page as it will print
        self.page_title = tk.Label(self.side, font=skin.font("caption"), bg=skin["panel"], fg=skin["ink3"], anchor="w")
        self.page_title.pack(fill="x", padx=px(10), pady=(px(10), px(4)))
        self.preview = Preview(self.side, skin)
        self.preview.pack(fill="both", expand=True, padx=(px(2), px(8)), pady=(0, px(8)))
        left = self.left = tk.Frame(work.inner, bg=skin["panel"])
        left.pack(side="left", fill="both", expand=True)
        self.action_row = tk.Frame(left, bg=skin["panel"])                    # packed first so that it always keeps its place at the bottom
        self.action_row.pack(side="bottom", fill="x", padx=px(10), pady=(0, px(6)))
        self.buttons["redo"] = Button(self.action_row, skin, "Redo", self.redo, icon="redo", kind="quiet", keys="Ctrl+Y")
        self.buttons["redo"].pack(side="right")
        self.buttons["undo"] = Button(self.action_row, skin, "Undo", self.undo, icon="undo", kind="quiet", keys="Ctrl+Z")
        self.buttons["undo"].pack(side="right")
        self.said_label = tk.Label(self.action_row, font=skin.font("body"), bg=skin["panel"], fg=skin["ink2"], anchor="w", justify="left")
        self.said_label.bind("<Configure>", lambda e: e.widget.configure(wraplength=max(1, e.width - 4)))     # (a long line wraps, it is not cut)
        self.said_label.pack(side="left", fill="x", expand=True, padx=(px(4), 0))
        self.notice = Notice(left, skin)
        self.notice.pack(fill="x", padx=px(12), pady=(px(10), 0))
        stack = tk.Frame(left, bg=skin["panel"])
        stack.pack(fill="both", expand=True)
        stack.rowconfigure(0, weight=1)
        stack.columnconfigure(0, weight=1)
        say = lambda text: self.say(text, "check")
        busy = lambda: False

        def page(kind, buttons, legend=()):
            """A form's own page: its buttons along the top, then its table."""
            frame = self.pages[kind] = tk.Frame(stack, bg=skin["panel"])
            frame.grid(row=0, column=0, sticky="nsew")
            bar = tk.Frame(frame, bg=skin["panel"])
            bar.pack(fill="x", padx=px(9), pady=(px(6), px(2)))
            for name, label, icon, command, keys in buttons:
                self.buttons[name] = Button(bar, skin, label, command, icon=icon, kind="plain", keys=keys)
                self.buttons[name].pack(side="left", padx=(0, px(2)))
            for mark, colour, words in reversed(legend):                        # what the small marks in the cells mean
                tk.Label(bar, text=words, font=skin.font("caption"), bg=skin["panel"], fg=skin["ink3"]).pack(side="right", padx=(px(4), px(10)))
                tk.Label(bar, image=skin.mark(mark, {"dot": px(6), "check": px(13)}.get(mark, px(9)), skin[colour]), bg=skin["panel"]).pack(side="right")
            return frame

        marks = (("check", "confirm_edge", "green: confirmed (Ctrl+G)"), ("dot", "ink3", "typed by hand"), ("ring", "accent", "ahead of the clock"))
        frame = page("calls", (("fill", "Fill in the rest of the shift", "fill", self.fill_ahead, ""),), marks)
        self.grids["calls"] = Grid(frame, skin, CALL_COLUMNS, self.call_text, self.commit_call, enter="down", busy=busy, status=say, gutter=34,
                                   press=self.press_call, moved=self.call_moved)
        self.grids["calls"].pack(fill="both", expand=True, padx=(px(4), px(2)), pady=(0, px(4)))
        frame = page("patrols", (("add_patrol", "Add a patrol", "add", self.add_patrol, "Ctrl+N"), ("remove_patrol", "Remove", "remove", self.remove_patrol, ""),
                                 ("clear_patrols", "Clear all", "clear", self.clear_patrols, ""), ("usual", "Usual rounds", "rounds", self.usual_rounds, "")), marks)
        self.grids["patrols"] = Grid(frame, skin, PATROL_COLUMNS, self.patrol_text, self.commit_patrol, enter="along", busy=busy, status=say, gutter=8)
        self.grids["patrols"].pack(fill="both", expand=True, padx=(px(4), px(2)), pady=(0, px(4)))
        frame = page("shift", (("add_line", "Add a line", "add", self.add_line, "Ctrl+N"), ("remove_line", "Remove", "remove", self.remove_line, ""),
                               ("clear_lines", "Clear lines", "clear", self.clear_lines, "")))
        extra = tk.Frame(frame, bg=skin["panel"])                             # handover notes and the two names, under the table
        extra.pack(side="bottom", fill="x", padx=px(12), pady=(px(4), px(8)))
        names = tk.Frame(extra, bg=skin["panel"])
        names.pack(side="right", anchor="n", padx=(px(14), 0))
        self.extra_fields = []
        for at, (label, var) in enumerate((("Supervisor", self.supervisor), ("Incoming officer", self.incoming))):
            tk.Label(names, text=label, font=skin.font("caption"), bg=skin["panel"], fg=skin["ink3"]).grid(row=at * 2, column=0, sticky="w")
            self.extra_fields.append(Field(names, skin, var, width=200))
            self.extra_fields[-1].grid(row=at * 2 + 1, column=0, pady=(0, px(6)))
        self.handover_says = tk.Label(extra, text="Handover notes", font=skin.font("caption"), bg=skin["panel"], fg=skin["ink3"])    # worded in show_buttons
        self.handover_says.pack(anchor="w")
        box = Panel(extra, skin, back="panel", fill="control", radius=4, pad=6)
        box.configure(height=px(96))
        box.pack(fill="x")
        self.handover = tk.Text(box.inner, height=4, wrap="word", font=skin.font("body"), bg=skin["control"], fg=skin["ink"], insertbackground=skin["ink"],
                                relief="flat", bd=0, highlightthickness=0, selectbackground=skin["accent"], selectforeground=skin["on_accent"], undo=False)
        self.handover.pack(fill="both", expand=True)
        self.handover.bind("<KeyRelease>", self.extras_typed)
        self.handover.bind("<FocusOut>", lambda e: self.save_extras())
        self.grids["shift"] = Grid(frame, skin, SHIFT_COLUMNS, self.shift_text, self.commit_shift, enter="along", left=self.entry_left, busy=busy, status=say, gutter=8)
        self.grids["shift"].pack(fill="both", expand=True, padx=(px(4), px(2)), pady=(0, px(4)))

        # ---- what the roster wants the controller to know ----
        self.notes = tk.Frame(main, bg=skin["base"])
        self.notes.pack(fill="x", pady=(px(6), 0))
        self.note_lines = []

        self.show_clock()
        self.show_chooser()
        self.refresh()
        self.fill_extras()

    def build_chooser(self, inner):
        skin, px = self.skin, self.skin.px
        head = lambda text: tk.Label(inner, text=text, font=skin.font("caption"), bg=skin["panel"], fg=skin["ink3"], anchor="w").pack(fill="x", padx=px(14), pady=(px(10), px(2)))
        head("Controller")
        box = tk.Frame(inner, bg=skin["panel"])
        box.pack(fill="x", padx=px(11))
        self.pills = {}
        for i, name in enumerate(calllog.CONTROLLERS):
            self.pills[name] = Chip(box, skin, name, lambda name=name: self.set_name(name), wide=True)
            self.pills[name].grid(row=i // 2, column=i % 2, sticky="ew")
        box.columnconfigure(0, weight=1, uniform="who")
        box.columnconfigure(1, weight=1, uniform="who")
        row = tk.Frame(inner, bg=skin["panel"])
        row.pack(fill="x", padx=px(14), pady=(px(4), 0))
        tk.Label(row, text="Someone else", font=skin.font("caption"), bg=skin["panel"], fg=skin["ink3"]).pack(side="left")
        Field(row, skin, self.other, width=150).pack(side="right")
        head("Shift")
        self.shift_box = tk.Frame(inner, bg=skin["panel"])
        self.shift_box.pack(fill="x", padx=px(11))
        self.shift_chips = []
        self.cal = Calendar(inner, skin, self.set_dates, self.shift_kind)
        self.cal.pack(fill="x", padx=px(10), pady=(px(8), 0))
        tk.Label(inner, text="Ctrl+click adds another day to print together.", font=skin.font("caption"), bg=skin["panel"], fg=skin["ink3"], anchor="w").pack(
            fill="x", padx=px(14))
        self.buttons["done"] = Button(inner, skin, "Done", self.toggle_chooser, icon="check", kind="plain", keys="Esc")
        self.buttons["done"].pack(side="bottom", anchor="e", padx=px(10), pady=px(8))
        self.duty_bar = tk.Canvas(inner, bg=skin["panel"], highlightthickness=0, height=px(44), width=px(280))
        self.duty_bar.pack(side="bottom", fill="x", padx=px(14))
        self.duty_bar.bind("<Configure>", lambda e: self.draw_duty())
        tk.Label(inner, text="Control room that day", font=skin.font("caption"), bg=skin["panel"], fg=skin["ink3"], anchor="w").pack(side="bottom", fill="x", padx=px(14))

    def px(self, n):
        return self.skin.px(n)

    def unseen(self):
        """Only for a window opened to be pictured (CCL_QUIET): let every click fall through it to whatever is behind."""
        try:
            import ctypes
            user = ctypes.WinDLL("user32")
            user.GetParent.restype, user.GetParent.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
            user.GetWindowLongW.argtypes, user.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int], [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
            frame = user.GetParent(self.root.winfo_id())
            user.SetWindowLongW(frame, -20, user.GetWindowLongW(frame, -20) | 0x00080000 | 0x00000020 | 0x08000000)     # layered, click-through, never active
        except Exception:
            pass

    def title_bar(self, tries=20):
        """Colour Windows' own title bar to match the look. Asked again shortly if the window is not on the screen yet."""
        if not self.gone and not skinlib.title_bar(self.root, self.skin) and tries:
            self.title_job = self.root.after(150, lambda: self.title_bar(tries - 1))

    def switch_theme(self):
        """Dark for the night, light for the day: the window is made again as it stands and the choice is remembered."""
        if not self.settle():
            return
        self.skin.use("light" if self.skin.theme == "dark" else "dark")
        self.build()
        self.title_bar()
        self.keep_settings()
        self.say("Dark look on." if self.skin.theme == "dark" else "Light look on.")

    def keep_settings(self):
        if os.environ.get("CCL_THEME"):               # a look forced for a check of the window is not the user's choice
            return
        self.settings.update(theme=self.skin.theme, preview=self.previewing, chooser=self.choosing, auto_green_for=sorted(self.auto_for))
        save_settings(self.settings)

    # ---- small things said ----
    def say(self, text, kind="word"):
        """What was just done, on the line under the table. It stays until the next thing: nothing here fades by itself."""
        self.said, self.said_kind = text, kind
        self.said_label.configure(text=text, fg=self.skin["amber" if kind == "check" else "ink2"])

    def show_clock(self):
        now = datetime.datetime.now()
        self.clock.configure(text=now.strftime("%H:%M"))
        self.today_label.configure(text=now.strftime("%A ") + str(now.day) + now.strftime(" %B"))

    # ---- who and when ----
    def duty(self, date):
        """That day's control-room shifts: from the roster once it has been read, else the usual week."""
        return self.view.duty(date) if self.view else calllog.usual_duty(date)

    def shift_kind(self, date):
        for s in self.duty(date):
            if calllog.same_person(self.name, s["name"]):
                return "day" if s["start"] < 16 * 60 else "night"
        return None

    def on_now(self, name):
        now = datetime.datetime.now()
        for back in (1, 0):
            d = now.date() - datetime.timedelta(days=back)
            midnight = datetime.datetime.combine(d, datetime.time())
            for s in self.duty(d):
                end = s["end"] + (calllog.DAY if s["end"] <= s["start"] else 0)
                if calllog.same_person(name, s["name"]) and midnight + datetime.timedelta(minutes=s["start"]) <= now <= midnight + datetime.timedelta(minutes=end):
                    return True
        return False

    def recent_shifts(self, limit=5):
        """This controller's latest shifts that have started, newest first, as (date, on now?, hours)."""
        now, out = datetime.datetime.now(), []
        for back in range(35):
            d = now.date() - datetime.timedelta(days=back)
            midnight = datetime.datetime.combine(d, datetime.time())
            for s in self.duty(d):
                start = midnight + datetime.timedelta(minutes=s["start"])
                end = midnight + datetime.timedelta(minutes=s["end"] + (calllog.DAY if s["end"] <= s["start"] else 0))
                if calllog.same_person(self.name, s["name"]) and start <= now:
                    out.append((d, now <= end, "%s-%s" % (calllog.fmt(s["start"]).zfill(5), calllog.fmt(s["end"]).zfill(5))))
                    break
            if len(out) == limit:
                break
        return out

    def desk_handover(self):
        """At a handover the window moves on by itself (asked for on 2026-10-24): when the controller who has just come
        on takes over the calls, a window showing the outgoing controller's shift goes to theirs - however that shift
        was chosen. The outgoing log's files are written first, and a notice offers to print them. Never from another
        day's log, and not while something is being typed (the next minute does it)."""
        if self.view is None:
            return
        desk, last = calllog.at_the_desk(datetime.datetime.now(), self.duty, fallback=False, newest=True), self.desk_was
        if desk == last:
            return
        pack = self.pack
        meta = self.meta(pack) if pack else None
        if last and desk and meta and len(self.packs) == 1 and pack["date"] == last[1] and calllog.same_person(last[0], meta["supervisor"]):
            if self.typing():
                return                                # (tried again at the next minute)
            if not self.flush():
                return
            self.handed = (pack["date"], meta["supervisor"], desk[0], datetime.datetime.now().strftime("%H:%M"))
            self.name, self.dates, self.touched_name, self.touched_date, self.day = desk[0], [desk[1]], False, False, 0
            self.rebuild()
            self.fill_extras()
            self.say("Handover at %s: %s has come on - this is their shift now." % (self.handed[3], desk[0]))
        self.desk_was = desk

    def print_handed(self):
        """The handover notice's button: open the outgoing controller's all-in-one PDF to print."""
        date, who = self.handed[0], self.handed[1]
        state = self.files.get((date, who))
        path = state["paths"].get("all") if state else None
        if path:
            self.launch(path)
            self.say("Opened %s's logs to print." % who)
        else:
            self.say("%s's logs have no file to print yet - choose their shift and press Open all to print." % who, "check")
        self.handed = None
        self.refresh()

    def follow_the_desk(self):
        """Until someone has chosen otherwise: the controller at the desk now, and their shift's day."""
        if not self.touched_name:
            who = calllog.at_the_desk(datetime.datetime.now(), self.duty, fallback=self.view is None, newest=True)
            if who:
                self.name = who[0]
                if not self.touched_date:
                    self.dates = [who[1]]
        elif not self.touched_date:
            recent = self.recent_shifts(1)
            if recent:
                self.dates = [recent[0][0]]

    def set_name(self, name, typed=False):
        if not self.settle():
            return
        self.name, self.touched_name = name, True
        if not typed and self.other.get():
            self.other.set("")
        if not self.touched_date:                     # follow the controller to their latest shift
            recent = self.recent_shifts(1)
            self.dates = [recent[0][0] if recent else datetime.date.today()]
        self.select()

    def other_typed(self, *_):
        text = self.other.get().strip()
        if text:
            self.set_name(text, typed=True)
        elif self.name not in self.pills:             # the box was emptied: back to whoever is at the desk
            self.touched_name = False
            self.follow_the_desk()
            self.select()

    def set_dates(self, dates, add=False):
        if not self.settle():
            return
        if add:
            dates = [d for d in self.dates if d not in dates] if all(d in self.dates for d in dates) and len(self.dates) > 1 \
                else self.dates + [d for d in dates if d not in self.dates]
        self.dates, self.touched_date = sorted(dates), True
        self.select()

    def select(self):
        """Another controller or day was chosen: show its logs at once."""
        self.day = 0
        self.say("")
        self.rebuild()
        self.refresh()
        self.fill_extras()
        self.aim()

    def aim(self):
        """On a shift that is on now, bring the next call to make into view and put the current cell on its time."""
        pack = self.pack
        if pack and "calls" in pack and self.stage(pack) == "live":
            call = next((c for c in pack["calls"]["calls"] if not c["call_text"] or c["ahead"]), None)
            if call:
                self.grids["calls"].set_cursor(calllog.edit_key(call), "time")

    def toggle_chooser(self):
        self.choosing = not self.choosing
        self.page_forced = False
        self.show_chooser()
        self.show_page()
        self.keep_settings()

    def page_on(self):
        """Is the printed page on show? It is whenever it is wanted. Only in a really narrow window (under 1100 px) does
        it give way while the chooser is out, so that some table is left - unless it was asked for with its button."""
        return self.previewing and (not self.choosing or self.page_forced or self.root.winfo_width() >= self.px(1100))

    def resized(self, event):
        """The window was made wider or narrower: the printed page comes back, or gives way, beside the chooser."""
        if event.widget is self.root and self.choosing and self.page_on() != bool(self.side.winfo_ismapped()):
            self.show_page()

    def show_chooser(self):
        px = self.skin.px
        if self.choosing:
            self.chooser.configure(width=px(316))
            self.chooser.pack(side="left", fill="y", padx=(0, px(10)), before=self.main)
            self.chooser.pack_propagate(False)
        else:
            self.chooser.pack_forget()
        self.buttons["change"].label("Hide controller and day" if self.choosing else "Show controller and day")      # (never "Close": that read as closing the program)
        self.buttons["change"].light(self.choosing)   # a panel's button is lit while its panel is out

    def refresh_chooser(self):
        skin = self.skin
        for name, chip in self.pills.items():
            chip.dot = self.on_now(name)
            chip.choose(name == self.name)
            chip.paint()
        for chip in self.shift_chips:
            chip.destroy()
        self.shift_chips = []
        today = datetime.date.today()
        for d, live, hours in self.recent_shifts():
            word = "Now  -  " if live else "Today  -  " if d == today else "Yesterday  -  " if d == today - datetime.timedelta(days=1) else ""
            chip = Chip(self.shift_box, skin, word + d.strftime("%a ") + str(d.day) + d.strftime(" %b"), lambda d=d: self.set_dates([d]), sub=hours, dot=live, wide=True)
            chip.pack(fill="x")
            chip.choose([d] == self.dates)
            self.shift_chips.append(chip)
        weekend = sorted(d for d, _, _ in self.recent_shifts(7) if d.weekday() >= 4)[-3:]
        weekend = [d for d in weekend if (weekend[-1] - d).days <= 2] if weekend else []
        if len(weekend) >= 2:
            chip = Chip(self.shift_box, skin, "The weekend  -  %s to %s" % (weekend[0].strftime("%a ") + str(weekend[0].day), weekend[-1].strftime("%a ") + str(weekend[-1].day)),
                        lambda days=weekend: self.set_dates(days), sub="%d days" % len(weekend), wide=True)
            chip.pack(fill="x")
            chip.choose(weekend == self.dates)
            self.shift_chips.append(chip)
        if not self.shift_chips:
            self.shift_chips.append(tk.Label(self.shift_box, text="No shift on the roster in the last five weeks.", font=skin.font("caption"), bg=skin["panel"], fg=skin["ink3"]))
            self.shift_chips[-1].pack(anchor="w", padx=self.px(3))
        self.cal.show(self.dates)
        self.draw_duty()

    def draw_duty(self):
        """Who has the control room on the chosen day, as a bar from the first shift's start round the clock."""
        skin, c, px = self.skin, self.duty_bar, self.skin.px
        c.delete("all")
        w = c.winfo_width()
        if w < 100:
            return
        day = self.dates[-1] if self.dates else datetime.date.today()
        shifts = sorted(((s["start"], s["end"] + (calllog.DAY if s["end"] <= s["start"] else 0), s["name"]) for s in self.duty(day)))
        if not shifts:
            c.create_text(1, px(12), anchor="w", text="Nobody is on the roster for %s." % day.strftime("%a %d %b"), font=skin.font("caption"), fill=skin["ink3"])
            return
        a = shifts[0][0]
        b = max(max(s[1] for s in shifts), a + 60)
        x = lambda minute: 1 + (minute - a) * (w - 2) / float(b - a)
        for start, end, name in shifts:
            mine = calllog.same_person(self.name, name)
            skin.box(c, int(x(start)) + 1, px(2), int(x(end)) - 1, px(24), px(4), skin["tint"] if mine else skin["sunk"], skin["tint_edge"] if mine else skin["edge"])
            if x(end) - x(start) > px(50):
                c.create_text((x(start) + x(end)) / 2.0, px(13), text=name.split()[0], font=skin.font("strong" if mine else "caption"), fill=skin["ink"] if mine else skin["ink2"])
            c.create_text(x(start) + 1, px(34), anchor="w", text=calllog.fmt(start).zfill(5), font=skin.font("caption"), fill=skin["ink3"])
        c.create_text(w - 1, px(34), anchor="e", text=calllog.fmt(b).zfill(5), font=skin.font("caption"), fill=skin["ink3"])

    # ---- the roster ----
    def load_roster(self, force=False):
        """Read the roster in a worker thread - one read at a time: one asked for meanwhile is done once after it."""
        if self.fetching:
            self.fetch_again = self.fetch_again or force
            return
        self.fetching = True

        def run():
            try:
                self.fetch_roster(force)
            except calllog.Problem:
                pass                                 # already reported through the queue
            finally:
                self.queue.put(("fetched",))
        threading.Thread(target=run, daemon=True).start()

    def fetch_roster(self, force=False):
        """Worker thread: hand the window an up-to-date roster (the saved copy at once, then the fresh one)."""
        with self.roster_lock:
            if self.roster and not force and time.time() - self.roster_at < ROSTER_FRESH:
                return
            try:
                have = os.path.exists(calllog.ROSTER_PATH)
                if have and not self.roster:          # show the saved copy at once, then update it
                    self.queue.put(("roster", calllog.Roster(), "saved copy, updating...", 0.0, False))
                fresh = True
                if os.environ.get("CCL_NO_DOWNLOAD") and have:
                    note = "saved copy"
                elif calllog.download_roster().startswith("downloaded"):
                    note = "read at " + time.strftime("%H:%M")
                else:
                    note, fresh = "could not be downloaded - using the saved copy", False
                sig = calllog.roster_signature()      # (the same parts: the roster read last time is the same roster)
                roster = self.roster if self.roster is not None and sig == self.roster_sig else calllog.Roster()
                self.roster_sig = sig
            except calllog.Problem as e:
                self.queue.put(("roster_failed", str(e)))
                raise
            except Exception as e:
                self.queue.put(("roster_failed", "the roster could not be read: %s" % e))
                raise calllog.Problem("the roster could not be read: %s" % e)
            stamp = time.time()
            self.roster, self.roster_at, self.roster_note = roster, stamp if fresh else 0.0, note
            self.queue.put(("roster", roster, note, stamp, fresh))

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
                    self.say("Something went wrong: " + traceback.format_exc().strip().splitlines()[-1], "check")
        except queue.Empty:
            pass
        if not self.gone:
            self.poll_job = self.root.after(120, self.poll)

    def handle(self, msg):
        if msg[0] == "roster":
            if msg[3] < self.view_at:                 # an older copy arriving late
                return
            first, was_fresh = self.view is None, self.view_fresh
            self.view, self.view_at = msg[1], msg[3]
            self.view_fresh = msg[4] if len(msg) > 4 else True
            self.roster_said = "Roster: " + msg[2]
            if self.view_fresh:
                self.calm()
            elif "could not" in msg[2]:
                self.back_off()
            if first and not self.packs:              # the roster knows about swaps and cover
                self.follow_the_desk()
            before = self.watch_before if self.watch_before is not None else self.shift_now()
            if not self.typing():                     # (never under a cell being typed in: the next minute will do)
                self.rebuild()
                if not first and was_fresh:           # what this copy changed for the shift on show, whatever brought it in
                    self.said_changes(before, self.shift_now())
                self.watch_before, self.watch_said = None, None
            elif not first:
                self.watch_before = before            # (said at the next rebuild)
            self.refresh()
            self.refill()
            self.auto_run()
            if first:
                self.fill_extras()
                self.root.after(150, self.aim)        # (once the table has its size)
                marker = os.environ.get("CCL_DONE_FILE")      # automated checks wait for this
                if marker:
                    with open(marker, "w", encoding="utf-8") as f:
                        f.write("shown")
        elif msg[0] == "roster_failed":
            self.roster_said = "Roster: " + msg[1]
            self.refresh()
        elif msg[0] == "green":
            self.coloured(*msg[1:])
        elif msg[0] == "watched":
            self.watched(msg[1])
        elif msg[0] == "watch_failed":
            self.back_off()
        elif msg[0] == "fetched":
            self.fetching = False
            if self.fetch_again:
                self.fetch_again = False
                self.load_roster(force=True)
        elif msg[0] == "ping":
            self.pinged(*msg[1:])
        elif msg[0] == "script":
            self.script_checked(msg[1])

    def tick(self):
        """Just after each minute turns: the clock, the 'now' rule, what has come due - and, until someone chooses
        otherwise, who is at the desk. The logs are simply built again: it takes two thousandths of a second."""
        try:
            self.cal.today = datetime.date.today()
            self.show_clock()
            self.desk_handover()
            was = (self.name, list(self.dates))
            if not (self.touched_name or self.touched_date) and not any(g.box for g in self.grids.values()):
                self.follow_the_desk()
            if not self.typing():                     # never under a cell being typed in (one only opened is fine)
                before = self.watch_before
                self.rebuild()
                if before is not None:                # a roster change that came in under an open cell
                    self.said_changes(before, self.shift_now())
                    self.watch_before, self.watch_said = None, None
            self.shift_ends()
            self.refresh()
            self.refill()
            if (self.name, self.dates) != was:
                self.fill_extras()
            self.auto_run()
        except Exception:
            if not self.gone:
                self.say("Something went wrong while updating the clock: " + traceback.format_exc().strip().splitlines()[-1], "check")
        finally:
            if not self.gone:
                self.tick_job = self.root.after(60000 - int(time.time() * 1000) % 60000 + 200, self.tick)

    # ---- the logs on show ----
    def rebuild(self):
        """Build the logs of every chosen day from the roster in memory. Looking never writes a file."""
        self.packs = []
        if self.view is None:
            return
        for d in self.dates:
            pack = {"date": d, "problems": []}
            for kind, build in (("calls", calllog.build), ("patrols", calllog.build_patrols), ("shift", calllog.build_shift)):
                try:
                    pack[kind] = build(d, self.view, self.name)
                except calllog.Problem as e:
                    if str(e) not in pack["problems"]:
                        pack["problems"].append(str(e))
                except Exception:                    # one form going wrong must not cost the others
                    pack["problems"].append("The %s could not be made: %s" % (dict(FORMS)[kind].lower(), traceback.format_exc().strip().splitlines()[-1]))
            self.packs.append(pack)
        self.day = min(self.day, max(0, len(self.packs) - 1))

    @property
    def pack(self):
        return self.packs[self.day] if self.packs else None

    def meta(self, pack):
        return next((pack[k]["meta"] for k, _ in FORMS if k in pack), None)

    def gone_minutes(self, pack):
        """Minutes from the midnight that day began with until now."""
        return (datetime.datetime.now() - datetime.datetime.combine(pack["date"], datetime.time())).total_seconds() / 60.0

    def stage(self, pack):
        """'before', 'live' or 'over': where the shift on show stands."""
        m, gone = self.meta(pack), self.gone_minutes(pack)
        return "before" if gone < m["start_min"] else "live" if gone <= m["end_min"] else "over"

    def remake(self, pack):
        """Build that day's three logs again from what is saved (after a change to them)."""
        who = self.meta(pack)["supervisor"]
        for kind, build in (("calls", calllog.build), ("patrols", calllog.build_patrols), ("shift", calllog.build_shift)):
            if kind in pack:
                pack[kind] = build(pack["date"], self.view, who)

    def write(self, pack):
        """Write every file of that shift (a few thousandths of a second). What could not be written is kept to be said."""
        paths, problems, made = {}, [], {}
        for kind, writer in (("calls", calllog.write_outputs), ("patrols", calllog.write_patrols), ("shift", calllog.write_shift)):
            if kind in pack:
                files = writer(pack[kind], self.out_dir)
                paths[kind], made[kind] = files.get("pdf"), pack[kind]
                problems += files["problems"]
        if len(made) > 1:
            files = calllog.write_pack(made, self.out_dir)
            paths["all"] = files.get("pdf")
            problems += files["problems"]
        elif made:
            paths["all"] = next(iter(paths.values()))
        self.files[(pack["date"], self.meta(pack)["supervisor"])] = {"paths": paths, "problems": problems, "at": datetime.datetime.now()}
        return problems

    def written(self, pack):
        return self.files.get((pack["date"], self.meta(pack)["supervisor"])) if pack and self.meta(pack) else None

    def change(self, what, action):
        """Do something that alters a shift's saved corrections: remembered so it can be undone, then the logs are built
        again and the files rewritten. Returns what the action returned."""
        pack = self.pack
        who = self.meta(pack)["supervisor"]
        stems = calllog.shift_stems(pack["date"], who)
        before = calllog.snapshot(stems)
        result = action()
        after = calllog.snapshot(stems)
        if after != before:
            self.undone.append((what, pack["date"], who, before, after))
            del self.undone[:-200], self.redone[:]
            self.remake(pack)
            self.write(pack)
            self.say(what)
        self.refresh()
        return result

    def step_back(self, forward=False):
        source, target = (self.redone, self.undone) if forward else (self.undone, self.redone)
        if not self.settle():
            return
        if not source:
            self.say("Nothing to %s." % ("redo" if forward else "undo"))
            return
        what, date, who, before, after = source.pop()
        target.append((what, date, who, before, after))
        if isinstance(before, tuple) and before[:1] == ("green",):   # a Green box press: the other way, on the roster
            self.green_back(before, forward)
            self.say("%s: %s" % ("Done again" if forward else "Undone", what[:1].lower() + what[1:]))
            return
        calllog.restore(after if forward else before)
        for at, pack in enumerate(self.packs):
            if pack["date"] == date and self.meta(pack) and self.meta(pack)["supervisor"] == who:
                self.remake(pack)
                self.write(pack)
                self.day = at
        self.refresh()
        self.fill_extras()
        self.say("%s: %s" % ("Done again" if forward else "Undone", what[:1].lower() + what[1:]))
        self.auto_run()                               # (a response put back: the roster follows it, with Auto green on)

    def undo(self):
        self.step_back()

    def redo(self):
        self.step_back(forward=True)

    def settle(self, keep=True):
        """Finish anything half-typed (an open cell, the handover box) so it is saved before the next action.
        False if a typed value cannot be right: its cell stays open saying why, and the action waits."""
        for grid in getattr(self, "grids", {}).values():
            if not grid.close(move="hold" if keep else None) and keep:
                return False
        if self.extras_job:
            self.save_extras()
        return True

    # ---- putting it on show ----
    def refresh(self):
        """Show everything the window knows: the top line, the ribbon, the tabs, the table, the page, the notes."""
        skin, pack = self.skin, self.pack
        meta = self.meta(pack) if pack else None
        self.roster_label.configure(text=self.roster_said)
        # the top line
        if meta:
            day, start, end = pack["date"], meta["start_min"], meta["end_min"]
            nxt = day + datetime.timedelta(days=1)
            days = "%s %d to %s %d %s" % (day.strftime("%a"), day.day, nxt.strftime("%a"), nxt.day, nxt.strftime("%b")) if end > calllog.DAY \
                else "%s %d %s" % (day.strftime("%A"), day.day, day.strftime("%B"))
            relief = pack["shift"]["incoming"] if "shift" in pack else calllog.next_on_duty(day, self.view, meta["supervisor"], start)
            self.who_label.configure(text=meta["supervisor"])
            self.when_label.configure(text="%s   -   %s to %s%s" % (days, calllog.fmt(start).zfill(5), calllog.fmt(end).zfill(5), "   -   %s takes over" % relief if relief else ""))
            words = {"before": "Not started yet", "live": "On shift now", "over": "Shift finished"}[self.stage(pack)]
        else:
            self.who_label.configure(text=next((c for c in calllog.CONTROLLERS if calllog.same_person(self.name, c)), self.name))
            self.when_label.configure(text=", ".join(d.strftime("%a ") + str(d.day) + d.strftime(" %b") for d in self.dates) if self.view is not None else "Reading the roster...")
            words = ""
        if words:
            self.state_pill.configure(image=self.pill(words, self.stage(pack) == "live"))
        else:
            self.state_pill.configure(image="", text="")
        # the ribbon and the line under it
        calls = pack["calls"]["calls"] if pack and "calls" in pack else []
        if meta:
            gone, live = self.gone_minutes(pack), self.stage(pack) == "live"
            left = int(meta["end_min"] - gone)
            self.ribbon.show({"date": pack["date"], "start": meta["start_min"], "end": meta["end_min"],
                              "calls": [(c["call"], bool(c["call_text"]) and not c["ahead"]) for c in calls],
                              "patrols": [(r["start"], r["finish"]) for r in pack["patrols"]["rows"]] if "patrols" in pack else [],
                              "lines": self.line_minutes(pack),
                              "now": "now %s  -  %d h %02d m to handover" % (datetime.datetime.now().strftime("%H:%M"), left // 60, left % 60) if live else ""})
        else:
            self.ribbon.show(None)
        self.tally.configure(text=self.tally_text(pack))
        state = self.written(pack)
        self.files_label.configure(text="" if not meta else "Files written %s" % state["at"].strftime("%H:%M") if state else "Files not written yet")
        # the tabs, the days, the tables, the page
        counts = {"calls": len(calls) if pack and "calls" in pack else None, "patrols": len(pack["patrols"]["rows"]) if pack and "patrols" in pack else None,
                  "shift": len(pack["shift"]["entries"]) if pack and "shift" in pack else None}
        for kind, tab in self.tabs.items():
            tab.show(kind == self.tab, counts[kind])
        self.pages[self.tab].tkraise()
        for w in self.day_box.winfo_children():
            w.destroy()
        if len(self.packs) > 1:
            for at, p in enumerate(self.packs):
                chip = Chip(self.day_box, skin, p["date"].strftime("%a ") + str(p["date"].day), lambda at=at: self.show_day(at), back="base", height=28)
                chip.pack(side="left")
                chip.choose(at == self.day)
        waiting = ("Reading the roster...", "The logs appear as soon as it has been read.") if self.view is None else \
            (pack["problems"][0][:1].upper() + pack["problems"][0][1:] if pack and pack["problems"] else "Nothing to show", "Choose another controller or day in the panel on the left (Ctrl+L).")
        self.grids["calls"].show(self.call_rows(pack) if pack and "calls" in pack else [], waiting)
        self.grids["patrols"].show(self.patrol_rows(pack) if pack and "patrols" in pack else [],
                                   ("No patrols on this log", "It prints blank to write on. Usual rounds puts the three rounds back; Add a patrol puts one down.") if pack and "patrols" in pack else waiting)
        self.grids["shift"].show(self.shift_rows(pack) if pack and "shift" in pack else [],
                                 ("No major occurrence", "That is what the shift log says, in large letters across the table, until a line is added. Add a line when something happens.") if pack and "shift" in pack else waiting)
        self.show_page()
        self.show_notes()
        self.show_buttons()
        self.show_auto()
        self.refresh_chooser()
        # the one line that needs the controller: a file that could not be written comes before anything else
        if state and state["problems"]:
            self.notice.say("wrong", state["problems"][0][:1].upper() + state["problems"][0][1:] + ".", ("Try again", self.flush))
        elif "could not" in self.roster_said:
            self.notice.say("check", self.roster_said + ".", ("Try again", lambda: self.load_roster(force=True)))
        elif self.handed:
            self.notice.say("word", "Handover at %s: now on %s's shift. %s's logs for %s are written - print them now." % (
                self.handed[3], self.handed[2], self.handed[1], self.handed[0].strftime("%a %d %b")),
                ("Print %s's" % self.handed[1].split()[0], self.print_handed))
        elif self.ended and pack and meta and (pack["date"], meta["supervisor"]) == self.ended:
            self.notice.say("word", "%s's shift finished at %s: %s. The files are written - print them now." % (
                meta["supervisor"], calllog.fmt(meta["end_min"]).zfill(5), self.tally_text(pack)), ("Open all to print", self.open_all))
        else:
            self.notice.say(None)

    def pill(self, words, live):
        skin, px = self.skin, self.skin.px
        """The small rounded label at the top right saying where the shift stands (the words sit on a rounded shape)."""
        wide = skin.measure("caption", words) + px(22)
        self.state_pill.configure(text=words, font=skin.font("caption"), fg=skin["ink"] if live else skin["ink2"], compound="center", padx=0, pady=0)
        return skin.shape(wide, px(24), px(12), skin["tint"] if live else skin["sunk"], skin["tint_edge"] if live else None)

    def tally_text(self, pack):
        if self.view is None:
            return "Reading the roster..."
        if not pack or not self.meta(pack):
            return pack["problems"][0][:1].upper() + pack["problems"][0][1:] if pack and pack["problems"] else ""
        calls = pack["calls"]["calls"] if "calls" in pack else []
        stage, gone = self.stage(pack), self.gone_minutes(pack)
        made = [c for c in calls if c["call_text"] and not c["ahead"]]
        to_come = [c for c in calls if not c["call_text"] or c["ahead"]]
        ahead = sum(1 for c in calls if c["ahead"])
        rounds, lines = len(pack["patrols"]["rows"]) if "patrols" in pack else 0, len(pack["shift"]["entries"]) if "shift" in pack else 0
        count = lambda n, word: "%d %s%s" % (n, word, "" if n == 1 else "s")
        if stage == "live" and calls:
            text = "%d of %s made" % (len(made), count(len(calls), "call")) + self.green_count(pack, made)
            if to_come:
                nxt = to_come[0]
                text += "   -   next due %s: %s, %s" % (nxt["due"].zfill(5), nxt["name"], nxt["venue"])
            return text + ("   -   %d filled in ahead" % ahead if ahead else "")
        if stage == "before" and calls:
            return "%s to make   -   first due %s: %s, %s" % (count(len(calls), "call"), calls[0]["due"].zfill(5), calls[0]["name"], calls[0]["venue"])
        return "   -   ".join([count(len(calls), "call") + self.green_count(pack, calls), count(rounds, "patrol"), count(lines, "log line")])

    def green_count(self, pack, calls):
        """', 44 green (2 not Ok, 1 yellow)' - how many of these calls are green on the roster, and why the others are not."""
        if not calls:
            return ""
        green = sum(1 for c in calls if (self.green(c, pack) or ("on" if rostercolour.is_green(c.get("fill")) else "off")) == "on")
        not_ok = sum(1 for c in calls if c["response"] and c["response"].strip().lower() != calllog.RESPONSE.lower())
        yellow = sum(1 for c in calls if rostercolour.css(c.get("fill")) == "#ffff00" and not (self.green(c, pack) == "on"))
        why = ["%d not Ok" % not_ok] * bool(not_ok) + ["%d yellow" % yellow] * bool(yellow)
        return ", %d green%s" % (green, " (%s)" % ", ".join(why) if why else "")

    def line_minutes(self, pack):
        out = []
        for e in pack["shift"]["entries"] if "shift" in pack else []:
            try:
                out.append(calllog.shift_clock(e["time"], pack["shift"]["meta"], late=calllog.GRACE, early=calllog.EARLY))
            except calllog.Problem:
                pass
        return out

    def show_page(self):
        """The form of the tab on show, as it will print."""
        skin, pack = self.skin, self.pack
        on = self.page_on()
        if on:
            self.side.configure(width=self.px(330))
            self.side.pack(side="right", fill="y", before=self.left)      # (before the table, so that it keeps its width)
            self.side.pack_propagate(False)
        else:
            self.side.pack_forget()
        self.buttons["preview"].label("Hide the printed page" if on else "Show the printed page")
        self.buttons["preview"].light(on)
        pages = []
        if pack and self.tab in pack and on:
            log = pack[self.tab]
            if self.tab == "calls":
                count = max(1, -(-len(log["calls"]) // forms.ROWS_PER_PAGE))
                pages = [lambda g, p=p: forms.draw_calls(g, log["meta"], log["calls"], p, count) for p in range(count)]
            elif self.tab == "patrols":
                count = max(1, -(-len(log["rows"]) // forms.ROWS_PER_PAGE))
                pages = [lambda g, p=p: forms.draw_patrols(g, log["meta"], log["rows"], p, count) for p in range(count)]
            else:
                count = max(1, -(-len(forms.shift_rows(log.get("entries", []))) // forms.SHIFT_ROWS))
                pages = [lambda g, p=p: forms.draw_shift(g, log["meta"], log, p, count) for p in range(count)]
        self.page_title.configure(text="As it will print%s%s" % (" on white" if skin.theme == "dark" else "", "  -  %d pages" % len(pages) if len(pages) > 1 else ""))
        self.preview.show(pages, "The page appears here.")

    def show_notes(self):
        """What the roster wants the controller to know about this shift, each on its own line."""
        skin, px, pack = self.skin, self.skin.px, self.pack
        for w in self.notes.winfo_children():
            w.destroy()
        lines = [n[:1].upper() + n[1:] for n in pack["calls"]["notes"]] if pack and "calls" in pack else []
        lines = [n for n in lines if "still to come" not in n and "filled in ahead" not in n]      # (those are on the top line already)
        self.note_lines = lines
        room = max(px(300), self.notes.winfo_width() - px(40))
        for n in lines if self.notes_all else lines[:3]:
            row = tk.Frame(self.notes, bg=skin["base"])
            row.pack(fill="x")
            tk.Label(row, text=skin.icon("warning") or "!", font=skin.font("icon_small"), bg=skin["base"], fg=skin["amber"]).pack(side="left", anchor="n", padx=(px(4), px(8)))
            tk.Label(row, text=n, font=skin.font("caption"), bg=skin["base"], fg=skin["ink2"], anchor="w", justify="left", wraplength=room).pack(side="left", fill="x")
        if len(lines) > 3:
            more = tk.Label(self.notes, text="Show fewer notes" if self.notes_all else "...and %d more roster note%s - show all" % (len(lines) - 3, "" if len(lines) == 4 else "s"),
                            font=skin.font("caption"), bg=skin["base"], fg=skin["accent"], anchor="w", cursor="hand2")
            more.pack(fill="x", padx=px(28))
            more.bind("<Button-1>", lambda e: self.toggle_notes())

    def toggle_notes(self):
        self.notes_all = not self.notes_all
        self.show_notes()

    def call_moved(self, key, field):
        """A row of the check calls became the current one: if a note is about it (its '!'), say the note."""
        call = self.call_for(key) if self.pack and "calls" in self.pack else None
        if call and call.get("ref"):
            said = [n for n in self.pack["calls"]["notes"] if "[%s]" % call["ref"] in n]
            if said:
                self.say(re.sub(r"\s*(\[[A-Z]+\d+\]\s*)+$", "", said[0][:1].upper() + said[0][1:]), "check")

    def typing(self):
        """Is something being typed in a cell (not just a cell opened)? Then the table waits to be built again."""
        return any(g.box and not g.untouched() for g in self.grids.values())

    def refill(self):
        for g in self.grids.values():
            g.refill()

    def shift_ends(self):
        """At the end of a shift on show: write its files, and say so with a button to print (until printed or left)."""
        for pack in self.packs:
            meta = self.meta(pack)
            if not meta:
                continue
            key, stage = (pack["date"], meta["supervisor"]), self.stage(pack)
            if stage == "over" and self.stages.get(key) == "live":
                self.flush()
                self.ended = key
            self.stages[key] = stage

    def show_buttons(self):
        pack = self.pack
        has = lambda kind: bool(pack and kind in pack)
        going = has("calls") and self.stage(pack) != "over"
        ahead = going and bool(pack["calls"]["meta"]["ahead"])
        self.buttons["fill"].able(going)
        self.buttons["fill"].label("Leave the rest of the shift blank" if ahead else "Fill in the rest of the shift")
        for name in ("add_patrol", "remove_patrol", "clear_patrols", "usual"):
            self.buttons[name].able(has("patrols"))
        for name in ("add_line", "remove_line", "clear_lines"):
            self.buttons[name].able(has("shift"))
        for field in self.extra_fields:
            field.entry.configure(state="normal" if has("shift") else "disabled")
        self.handover.configure(state="normal" if has("shift") else "disabled")
        stand_in = pack["shift"]["handover_default"] if has("shift") else ""      # what the form says in place of notes not typed
        self.handover_says.configure(text="Handover notes  -  left empty, so the form says %s" % stand_in if stand_in else "Handover notes  (six lines on the form)")
        self.buttons["print"].able(bool(pack and self.meta(pack)))
        self.buttons["undo"].able(bool(self.undone))
        self.buttons["redo"].able(bool(self.redone))

    def show_tab(self, kind):
        if not self.settle():
            return
        self.tab = kind
        self.refresh()

    def show_day(self, at):
        if not self.settle():
            return
        self.day = at
        self.refresh()
        self.fill_extras()

    def toggle_preview(self):
        """The button always does what it says: hide the page if it is on show, show it if it is not - also beside
        the chooser in a narrow window, where the table then gives up some of its width."""
        if self.page_on():
            self.previewing = self.page_forced = False
        else:
            self.previewing, self.page_forced = True, self.choosing
        self.show_page()
        self.keep_settings()

    def go_to(self, minute):
        """The ribbon was clicked at a time: bring the call nearest after it into view."""
        pack = self.pack
        if pack and "calls" in pack and self.tab == "calls":
            call = next((c for c in pack["calls"]["calls"] if c["call"] >= minute - 2), None)
            if call:
                self.grids["calls"].set_cursor(calllog.edit_key(call), "time")

    def add_row(self):
        if self.tab == "patrols":
            self.add_patrol()
        elif self.tab == "shift":
            self.add_line()

    # ---- the check calls ----
    def call_rows(self, pack):
        log, rows = pack["calls"], []
        live, hour, waiting, ruled = self.stage(pack) == "live", None, True, False
        notes = " ".join(log["notes"])
        for c in log["calls"]:
            made = bool(c["call_text"]) and not c["ahead"]
            if live and not ruled and not made:       # the rule between what has been made and what is to come
                rows.append({"rule": "now %s" % datetime.datetime.now().strftime("%H:%M")})
                ruled = True
            ok = self.green(c, pack)
            green = ok == "on" or (not ok and rostercolour.is_green(c.get("fill")))     # (another day: shown, not changed)
            row = {"key": calllog.edit_key(c), "cells": {"no": str(c["no"]), "ok": ok, "name": c["name"], "venue": c["venue"], "time": c["call_text"] or "due " + c["due"].zfill(5),
                                                         "resp": c["response"], "shift": c["shift"].replace(".", ":")}, "marks": {}, "tones": {},
                   "paint": {"name": ("confirm", "on_confirm")} if green else
                            {"name": ("provisional", "on_provisional")} if rostercolour.css(c.get("fill")) == "#ffff00" else {}}
            if c["call"] // 60 != hour:               # the hour, once, in the margin: the night reads in hours
                hour = c["call"] // 60
                row["gutter"] = "%02d" % (hour % 24)
            if not c["call_text"]:
                row["dim"] = True
            if live and waiting and not made:         # the next call to make
                row.update(bar=True, dim=False)
                row["tones"]["time"] = "accent" if not c["call_text"] else None
                waiting = False
            if c["ahead"]:
                row["marks"].update(time="ahead", resp="ahead")
            elif c["timed"]:
                row["marks"]["time"] = "hand"
            if c["edited"] and c["response"] and c["response"] != calllog.RESPONSE:
                row["marks"]["resp"] = "hand"
            if "[%s]" % c["ref"] in notes:
                row["note"] = True
            rows.append(row)
        return rows

    def call_for(self, key):
        return next((c for c in self.pack["calls"]["calls"] if calllog.edit_key(c) == key), None)

    def call_text(self, key, field):
        call = self.call_for(key)
        return "" if call is None else call["call_text" if field == "time" else "response"]

    def commit_call(self, key, field, text):
        """Save a typed time or response. Returns why, if it cannot be right."""
        pack, call, text = self.pack, self.call_for(key), text.strip()
        if call is None:
            return None
        log = pack["calls"]
        try:
            if field == "time":
                value = calllog.read_time(text, log) if text else None
                if value == (call["call_text"] or None) or (value is None and not call["timed"]):
                    return None                       # nothing changed
                what = "Call %s, %s: time %s" % (call["no"], call["name"], value or "back to the generated one")

                def action():
                    calllog.save_edit(log, call, "time", value)
                    if value is None and call["edited"]:      # a response typed with that time goes too if the row is blank again
                        again = next((c for c in calllog.build(pack["date"], self.view, log["meta"]["supervisor"])["calls"] if calllog.edit_key(c) == key), None)
                        if again and not again["call_text"]:
                            calllog.save_edit(log, again, "response", None)
            else:
                text = calllog.RESPONSE if not text or text.lower() == calllog.RESPONSE.lower() else text    # ("ok", "OK", nothing: Ok)
                if text == call["response"]:
                    return None
                if not call["call_text"]:
                    raise calllog.Problem("That call has no time yet - give it a time first.")
                what = "Call %s, %s: response %s" % (call["no"], call["name"], text or calllog.RESPONSE)
                action = lambda: calllog.save_edit(log, call, "response", text if text and text != calllog.RESPONSE else None)
        except calllog.Problem as e:
            return str(e)
        self.change(what, action)
        again = self.call_for(key)
        if field == "resp" and again:
            self.response_changed(again)
        if field == "time" and again and again["call_text"]:
            if sum(1 for c in self.pack["calls"]["calls"] if c["call_text"] == again["call_text"]) > 1:
                self.say(what + ".  Note: another call has the same time.", "check")
            elif again["ahead"]:
                self.say(what + ".  It is still to come: marked as ahead of the clock.")
        return None

    def fill_ahead(self):
        """Fill in the calls still to come on this shift - each with its time and "Ok" - or, pressed again, leave them
        blank. The controller's own choice for the shift; what turns out different is corrected after. (The usual
        rounds are always on the patrol log, done or not.)"""
        pack = self.pack
        if not self.settle() or not pack or "calls" not in pack or self.stage(pack) == "over":
            return
        on = not pack["calls"]["meta"]["ahead"]

        def action():
            for kind in ("calls", "patrols"):
                if kind in pack:
                    calllog.fill_ahead(pack[kind], on)
        self.change("Filled in to the end of the shift" if on else "The rest of the shift is blank again", action)
        if on:
            calls = self.pack["calls"]["meta"]["early"]
            self.say("Filled in to the end of the shift: %d call%s ahead of the clock. Change any that turn out different." % (calls, " is" if calls == 1 else "s are"))

    # ---- the patrols ----
    def patrol_rows(self, pack):
        rows = []
        for n, r in enumerate(pack["patrols"]["rows"], 1):
            mark = "ahead" if r["ahead"] else "hand" if r["edited"] else None
            rows.append({"key": r["id"], "cells": {"no": str(n), "officer": r["officer"], "area": r["area"], "start": r["start_text"], "finish": r["finish_text"], "notes": r["notes"]},
                         "marks": {"finish": mark} if mark else {}})
        return rows

    def patrol_row(self, key):
        return next((r for r in self.pack["patrols"]["rows"] if r["id"] == key), None)

    def patrol_text(self, key, field):
        row = self.patrol_row(key)
        return "" if row is None else row[field + "_text"] if field in ("start", "finish") else row[field]

    def patrol_change(self, what, action, row=None):
        """A change to the patrols, and whatever else it did: a usual round it took the place of, an overlap."""
        covered = list(self.pack["patrols"]["covered"])
        result = self.change(what, action)
        row = result if row is None else row
        log = self.pack["patrols"]
        mine = next((r for r in log["rows"] if r["id"] == row), None)
        gone = [c for c in log["covered"] if c not in covered]
        if gone:
            self.say("%s.  The usual round at %s was at the same time, so it is off the list." % (what, " and ".join(gone)), "check")
        elif mine and any(r is not mine and r["start"] < mine["finish"] and mine["start"] < r["finish"] for r in log["rows"]):
            self.say("%s.  Note: it overlaps another patrol on the list." % what, "check")
        elif mine and mine["ahead"]:
            self.say("%s.  It is still to come: marked as ahead of the clock." % what)
        return result

    def commit_patrol(self, key, field, text):
        row, text = self.patrol_row(key), text.strip()
        if row is None:
            return None
        log = self.pack["patrols"]
        try:
            if field in ("start", "finish"):
                if not text:
                    raise calllog.Problem("A patrol needs a start and a finish - Remove takes the whole patrol out.")
                at = calllog.shift_clock(calllog.read_time(text, log), log["meta"])
                if at == row[field]:
                    return None
                change = {field: calllog.fmt(at).zfill(5)}
                what = "Patrol %s: %s %s" % (row["start_text"], field, change[field])
                if field == "finish":
                    if at < row["start"]:
                        raise calllog.Problem("A patrol cannot finish before it starts (%s)." % row["start_text"])
                    change["finish_by"] = "hand"      # a finish typed by hand stays put when the start is corrected later
                elif not (row["finish_typed"] and at < row["finish"]):
                    length = row["finish"] - row["start"]         # the finish goes with the start, keeping the patrol as long as it was
                    finish = min(at + (length if length >= 5 else 15), log["meta"]["end_min"])
                    if finish <= at:
                        raise calllog.Problem("%s leaves the patrol no time before the shift ends." % calllog.fmt(at))
                    change.update(finish=calllog.fmt(finish).zfill(5), finish_by="")
                    what += ", finish %s" % change["finish"]
            elif text == row[field]:
                return None
            else:
                change = {field: text}
                what = "Patrol %s: %s" % (row["start_text"], "area %s" % text if field == "area" else "note changed")
        except calllog.Problem as e:
            return str(e)
        self.patrol_change(what, lambda: calllog.patrol_set(log, key, **change), key)
        return None

    def add_patrol(self):
        pack = self.pack
        if not self.settle() or not pack or "patrols" not in pack:
            return
        self.remake(pack)                             # the list as it stands this minute: a round may have finished since it was drawn
        log = pack["patrols"]
        try:
            start, finish = calllog.patrol_slot(log)
        except calllog.Problem as e:
            self.refresh()
            self.say(str(e), "check")
            return
        times = calllog.fmt(start).zfill(5), calllog.fmt(finish).zfill(5)
        new = self.patrol_change("Patrol added at %s" % times[0], lambda: calllog.patrol_add(log, *times))
        self.show_tab("patrols")
        self.grids["patrols"].open(new, "start")

    def remove_patrol(self):
        grid, pack = self.grids["patrols"], self.pack
        picked = grid.picked()
        grid.close(cancel=True)                       # whatever was half-typed in a patrol that is going is not wanted
        if not self.settle() or not pack or "patrols" not in pack:
            return
        if not picked:
            self.say("Click a patrol first, then Remove.", "check")
            return
        self.change("Removed %d patrol%s" % (len(picked), "" if len(picked) == 1 else "s"), lambda: calllog.patrol_remove(pack["patrols"], picked))

    def clear_patrols(self):
        pack = self.pack
        if self.settle() and pack and "patrols" in pack:
            self.change("All patrols cleared: the form prints blank to write on", lambda: calllog.patrol_clear(pack["patrols"]))

    def usual_rounds(self):
        pack = self.pack
        if self.settle() and pack and "patrols" in pack:
            self.change("Back to the usual rounds", lambda: calllog.patrol_reset(pack["patrols"]))

    # ---- the shift log ----
    def shift_rows(self, pack):
        return [{"key": e["id"], "cells": {"time": e["time"], "area": e["area"], "text": e["text"]}} for e in pack["shift"]["entries"]]

    def shift_entry(self, key):
        return next((e for e in self.pack["shift"]["entries"] if e["id"] == key), None)

    def shift_text(self, key, field):
        entry = self.shift_entry(key)
        return entry[field] if entry else ""

    def keep_shift(self, what, entries=None):
        """Save the shift log: these lines (None: as they are) with whatever is in the boxes."""
        log = self.pack["shift"]
        return self.change(what, lambda: calllog.save_shift(log, log["entries"] if entries is None else entries,
                                                            self.handover.get("1.0", "end"), self.supervisor.get(), self.incoming.get()))

    def commit_shift(self, key, field, text):
        entry, text = self.shift_entry(key), text.strip()
        if entry is None or text == entry[field]:
            return None
        log = self.pack["shift"]
        try:
            if field == "time" and text:              # a line may be a little before the shift starts or after it ends
                text = calllog.read_time(text, log, early=calllog.EARLY, late=calllog.GRACE).zfill(5)
        except calllog.Problem as e:
            return str(e)
        if text == entry[field]:
            return None
        if self.fresh == key:
            self.fresh = None                         # something was typed in it: a real line now
        self.keep_shift("Log line %s: %s" % (entry["time"] or text if field == "time" else entry["time"] or "(no time)",
                                             {"time": "time %s" % (text or "taken out"), "area": "place %s" % text, "text": "what happened"}[field]),
                        [dict(e, **{field: text}) if e["id"] == key else e for e in log["entries"]])
        return None

    def add_line(self):
        pack = self.pack
        if not self.settle() or not pack or "shift" not in pack:
            return
        log = pack["shift"]
        gone = int(self.gone_minutes(pack))           # during the shift a new line starts at the time now
        live = log["meta"]["start_min"] - calllog.EARLY <= gone <= log["meta"]["end_min"] + calllog.GRACE
        new = {"id": calllog.new_entry_id(log), "time": calllog.fmt(gone).zfill(5) if live else "", "area": "", "text": ""}
        self.keep_shift("New line: type %s; Enter moves along the row" % ("the place and what happened" if live else "the time, the place and what happened"),
                        log["entries"] + [new])
        self.fresh = new["id"]
        self.show_tab("shift")
        self.grids["shift"].open(new["id"], "area" if live else "time")

    def entry_left(self, key):
        """The typing box has left a shift log line: a new line that nothing was typed in is taken away again."""
        if key != self.fresh:
            return
        self.fresh = None
        entry, why = self.shift_entry(key), self.grids["shift"].refused
        if entry and not entry["area"] and not entry["text"]:
            self.keep_shift("Nothing was typed, so the new line was taken away", [e for e in self.pack["shift"]["entries"] if e["id"] != key])
            if self.undone and len(self.undone) >= 2:         # adding it and taking it away are not things to undo
                del self.undone[-2:]
                self.show_buttons()
            if why:                                   # what was typed was not accepted: say that, not "nothing was typed"
                self.say("%s - so the new line was taken away." % why.rstrip("."), "check")

    def remove_line(self):
        grid, pack = self.grids["shift"], self.pack
        picked = grid.picked()
        grid.close(cancel=True)
        if not self.settle() or not pack or "shift" not in pack:
            return
        picked = [k for k in picked if self.shift_entry(k)]
        if not picked:
            self.say("Click a line first, then Remove.", "check")
            return
        self.keep_shift("Removed %d line%s" % (len(picked), "" if len(picked) == 1 else "s"), [e for e in pack["shift"]["entries"] if e["id"] not in picked])

    def clear_lines(self):
        pack = self.pack
        if self.settle() and pack and "shift" in pack and pack["shift"]["entries"]:
            self.keep_shift("All lines cleared: the table prints blank to write on", [])

    def fill_extras(self):
        """Put the saved handover notes and names of the day on show into their boxes (not while they are being typed in)."""
        pack = self.pack
        log = pack["shift"] if pack and "shift" in pack else {"handover": "", "supervisor": "", "incoming": ""}
        self.filling = True
        self.handover.configure(state="normal")
        self.handover.delete("1.0", "end")
        self.handover.insert("1.0", log["handover"])
        self.supervisor.set(log["supervisor"])
        self.incoming.set(log["incoming"])
        self.filling = False
        self.show_buttons()

    def extras_typed(self, *_):
        """Typing in the handover box or a name: save shortly after the typing stops."""
        if self.filling or not self.pack or "shift" not in self.pack:
            return
        if self.extras_job:
            self.root.after_cancel(self.extras_job)
        self.extras_job = self.root.after(700, self.save_extras)

    def save_extras(self):
        if self.extras_job:
            self.root.after_cancel(self.extras_job)
            self.extras_job = None
        pack = self.pack
        if self.filling or not pack or "shift" not in pack:
            return
        log = pack["shift"]
        typed = (self.handover.get("1.0", "end").strip(), self.supervisor.get().strip(), self.incoming.get().strip())
        if typed != (log["handover"], log["supervisor"], log["incoming"]):
            self.keep_shift("Handover notes and names saved")
            lines = len(forms.wrap(self.pack["shift"]["handover"], "F1", 10.5, forms.WIDTH - 8))
            if lines > 6:
                self.say("Saved, but the handover notes run past the six lines on the form - shorten them.", "check")

    # ---- confirmed: the person's name green on the roster (rostercolour.py) ----
    def may_confirm(self, pack):
        """Only today's names may be changed on the roster: the shift dated today, or the one on now (a night shift past
        midnight) - never tomorrow's or later, never an older day (the user's rule, 2026-10-23)."""
        return bool(pack) and "calls" in pack and (pack["date"] == datetime.date.today() or self.stage(pack) == "live")

    def roster_colour(self, call):
        """The colour this name has on the roster as far as this window knows: what it asked for (pending) until a copy
        shows it - or until a copy read a minute after still says otherwise (then someone changed it on the roster, and
        the roster has the last word); else the copy's own colour ('#92d050', '#ffffff', '#ffff00', ...)."""
        key = (call["ref"], call["name"])
        mine, on = self.pending.get(key), rostercolour.is_green(call.get("fill"))
        if mine is not None and (mine[0] == on or self.view_at > mine[1] + 60):
            del self.pending[key]
            mine = None
        if mine is not None:
            return rostercolour.GREEN if mine[0] else rostercolour.WHITE
        return rostercolour.css(call.get("fill"))

    def green(self, call, pack=None):
        """A call's Green box: 'on' (green on the roster), 'off', 'busy' (being sent), or '' (no box: no roster cell, not
        today's, or a colour that is a person's decision)."""
        if not call.get("ref") or not self.may_confirm(pack or self.pack) or not rostercolour.changeable(call.get("fill")):
            return ""
        if (call["ref"], call["name"]) in self.sending:
            return "busy"
        return "on" if self.roster_colour(call) == rostercolour.GREEN else "off"

    def decisions(self, stem):
        """The Green-box presses kept with a shift: {edit key: 'green'/'white'} (a small file beside its corrections)."""
        if stem not in self.hand:
            try:
                with open(os.path.join(calllog.EDITS, stem + " green.json"), encoding="utf-8") as f:
                    data = json.load(f)
                self.hand[stem] = {k: v for k, v in data.items() if v in ("green", "white")} if isinstance(data, dict) else {}
            except (OSError, ValueError):
                self.hand[stem] = {}
        return self.hand[stem]

    def decide(self, stem, ekey, action):
        """Keep (or, with None, forget) the Green box's decision for one name of a shift."""
        hand = self.decisions(stem)
        if hand.get(ekey) == action or (action is None and ekey not in hand):
            return
        if action is None:
            del hand[ekey]
        else:
            hand[ekey] = action
        path = os.path.join(calllog.EDITS, stem + " green.json")
        try:
            os.makedirs(calllog.EDITS, exist_ok=True)
            if hand:
                with open(path + ".part", "w", encoding="utf-8") as f:
                    json.dump(hand, f, indent=1)
                os.replace(path + ".part", path)
            elif os.path.exists(path):
                os.remove(path)
        except OSError:
            pass                                      # (kept in memory for this run all the same)

    def press_call(self, key, field):
        call = self.call_for(key)
        if call and self.green(call) in ("on", "off"):
            self.confirm([call])

    def confirm_picked(self):
        """Ctrl+G: the Green box of the row(s) picked in the check calls."""
        if not self.settle():                         # (a response being typed is saved first)
            return
        calls = [c for c in (self.call_for(k) for k in self.grids["calls"].picked()) if c] if self.tab == "calls" and self.pack and "calls" in self.pack else []
        calls = [c for c in calls if self.green(c) in ("on", "off")]
        if calls:
            self.confirm(calls)
        elif self.tab == "calls" and self.pack and not self.may_confirm(self.pack):
            self.say("Only today's names can be turned green on the roster - this is %s." % self.pack["date"].strftime("%A %d %B"), "check")
        else:
            self.say("Click a call first; Ctrl+G then turns that name green on the roster.", "check")

    def the_link(self):
        """The roster link, or None (said once) if its file cannot be read."""
        if self.link is None:
            try:
                self.link = rostercolour.load()
            except calllog.Problem as e:
                if not self.link_warned:
                    self.say("Turning names green is stopped: %s. Nothing was changed on the roster." % e, "check")
                    self.link_warned = True
                return None
        return self.link

    def confirm(self, calls):
        """The Green box by hand: these names green on the roster - or, when every one of them is green already, white.
        The hand decides for each from now on (kept with the shift) until its response changes. Undoable."""
        link = self.the_link()
        if link is None:
            return
        if not rostercolour.URL_RE.match(link["url"]):
            self.ask_link(then=lambda: self.confirm(calls))
            return
        action = "white" if all(self.green(c) == "on" for c in calls) else "green"
        stem = self.pack["calls"]["stem"]
        done = []
        for call in calls:
            if action == "green" and self.green(call) == "on" or self.green(call) == "busy":
                continue
            ekey = calllog.edit_key(call)
            done.append((ekey, call["ref"], call["name"], self.decisions(stem).get(ekey)))
            self.decide(stem, ekey, action)
            self.send_colour(call["ref"], call["name"], action, self.roster_colour(call))
        if done:
            names = ", ".join(n for _, _, n, _ in done)
            self.undone.append(("Green box: %s %s" % (names, action), self.pack["date"], self.meta(self.pack)["supervisor"], ("green", stem, done, action), None))
            del self.undone[:-200], self.redone[:]
        self.refresh()

    def green_back(self, entry, forward):
        """Undo (or redo) a Green box press: the other colour on the roster, and the hand's earlier decision back."""
        _, stem, done, action = entry
        link = self.the_link()
        if link is None:
            return
        for ekey, ref, name, before in done:
            to = action if forward else ("white" if action == "green" else "green")
            self.decide(stem, ekey, action if forward else before)
            self.send_colour(ref, name, to, rostercolour.WHITE if to == "green" else rostercolour.GREEN)
        self.refresh()

    def send_colour(self, ref, name, action, was, auto=False):
        """One request to the roster's script, from a worker thread; the answer comes back through the queue."""
        link = dict(self.link)
        self.sending[(ref, name)] = action

        def run():
            try:
                answer = rostercolour.send(link, action, ref, name, was)
            except calllog.Problem as e:
                answer = {"ok": False, "why": str(e)}
            self.queue.put(("green", ref, name, action, answer, auto))
        threading.Thread(target=run, daemon=True).start()

    def coloured(self, ref, name, action, answer, auto=False):
        """The roster's script has answered about one name (auto: a request auto green made)."""
        self.sending.pop((ref, name), None)
        if auto:
            self.auto_busy = False
            self.auto_job = self.root.after(300, self.auto_run)      # the next one, if there is one
        why = answer.get("why") or ""
        if answer.get("ok"):
            self.calm()
            self.pending[(ref, name)] = (action == "green", time.time())
            if not auto:                              # (auto green's own are counted on its button, not said one by one)
                self.say("%s is %s on the roster (%s)." % (name, "green" if action == "green" else "white again", ref))
            if self.reread_job:                       # read the roster again once the clicking stops, so its copy shows it
                self.root.after_cancel(self.reread_job)
            self.reread_job = self.root.after(8000, self.reread)
        elif why in ("moved", "changed"):
            self.say("%sThe roster has changed since it was read: %s %s. Reading it again%s." % (
                "Auto green: " if auto else "", ref, "no longer says %s" % name if why == "moved" else "(%s) is not the colour expected any more" % name,
                "" if auto else " - press again in a moment"), "check")
            self.load_roster(force=True)
        elif why == "not today":
            self.say("%s was not changed: the roster's script only changes today's names (%s is %s)." % (name, ref, answer.get("day") or "another day"), "check")
        elif why in ("not a name", "merged"):
            self.say("%s%s was not turned %s: %s - left to a person." % ("Auto green: " if auto else "", name, action,
                     "the roster's script does not take %s for a name" % ref if why == "not a name" else "%s is merged with other cells on the roster" % ref), "check")
        else:
            if "could not be reached" in why or "did not reach" in why:
                self.back_off()
            self.say("%s%s was not turned %s on the roster: %s." % ("Auto green: " if auto else "", name, action, why or "no answer"), "check")
        if not auto:
            self.auto_run()
        self.refresh()

    def call_at(self, ref, name):
        """The call on show for this roster cell and name, or None."""
        return next((c for p in self.packs if "calls" in p for c in p["calls"]["calls"] if c.get("ref") == ref and c["name"] == name), None)

    def reread(self):
        self.reread_job = None
        self.load_roster(force=True)

    def back_off(self):
        """Google refused or could not be reached: no more tries for a while (30 s, doubling up to 10 min)."""
        self.quiet_step = min(600, max(30, self.quiet_step * 2))
        self.quiet_until = time.time() + self.quiet_step
        self.quiet_since = self.quiet_since or time.strftime("%H:%M")

    def calm(self):
        self.quiet_step, self.quiet_until, self.quiet_since = 0, 0.0, None

    # ---- auto green: the roster follows the responses ----
    def shown_who(self):
        """The controller whose log is on show (their name as the roster writes it)."""
        return self.meta(self.pack)["supervisor"] if self.pack and self.meta(self.pack) else self.name

    @property
    def auto_green(self):
        """Is Auto green on for the controller on show?"""
        return self.shown_who() in self.auto_for

    def toggle_auto(self):
        """Auto green on or off for the controller on show (each controller's own; it is kept)."""
        who = self.shown_who()
        if who in self.auto_for:
            self.auto_for.discard(who)
            self.keep_settings()
            self.show_auto()
            self.say("Auto green is off for %s's shifts." % who)
            return
        link = self.the_link()
        if link is None:
            return
        if not rostercolour.URL_RE.match(link["url"]):
            self.ask_link(then=self.toggle_auto)
            return
        self.auto_for.add(who)
        self.keep_settings()
        due = len([d for d in self.auto_due() if d[3] == who])
        self.show_auto()
        self.say("Auto green is on for %s's shifts - %s. The roster follows the responses: Ok turns a white name green once "
                 "its call time has passed, anything else turns it white; never yellow, never a name struck through; a Green "
                 "box pressed by hand decides for that name." % (who, "%d to do now" % due if due else "nothing to do yet"))
        self.auto_run()

    def show_auto(self):
        """The Auto green button, for the controller on show: off or on, and whether it is waiting for Google."""
        button = self.buttons.get("auto") if hasattr(self, "buttons") else None
        if button is None:
            return
        on = self.auto_green
        text = "Auto green: on" if on else "Auto green: off"
        if on and time.time() < self.quiet_until:
            text += " (waiting)"
        button.label(text)
        button.light(on)

    def auto_logs(self):
        """The shifts on now of every controller Auto green is on for - built here, whatever is on show."""
        if not self.auto_for or self.view is None:
            return []
        now, logs = datetime.datetime.now(), []
        for back, who in ((b, w) for b in (1, 0) for w in sorted(self.auto_for)):
            day = now.date() - datetime.timedelta(days=back)
            try:
                log = calllog.build(day, self.view, who, now=now)
            except calllog.Problem:
                continue
            midnight = datetime.datetime.combine(day, datetime.time())
            if midnight + datetime.timedelta(minutes=log["meta"]["start_min"]) <= now <= midnight + datetime.timedelta(minutes=log["meta"]["end_min"]):
                logs.append(log)
        return logs

    def auto_due(self):
        """What Auto green has to do now, whites first: [(call, action, stem, controller)]. The roster follows the response: a call
        made (its time passed, not filled in ahead) answered Ok whose name is white turns green; a name whose response
        is anything else (not blank: a call not made has none) and is green turns white. Never yellow or any other
        colour (a person's decision), never green for a name struck through on the roster (maybe cancelled: only the
        Green box by hand), never a name whose Green box was pressed by hand (until its response changes), never one
        being sent or tried in the last 5 minutes."""
        greens, whites = [], []
        for log in self.auto_logs():
            hand = self.decisions(log["stem"])
            for c in log["calls"]:
                if not c.get("ref") or not rostercolour.changeable(c.get("fill")) or (c["ref"], c["name"]) in self.sending:
                    continue
                ekey = calllog.edit_key(c)
                if ekey in hand or time.time() - self.tried.get((log["stem"], ekey), 0) < 300:
                    continue
                said, colour = c["response"].strip().lower(), self.roster_colour(c)
                if said == calllog.RESPONSE.lower() and c["call_text"] and not c["ahead"] and colour == rostercolour.WHITE \
                        and not c.get("strike"):     # (struck through: maybe cancelled - the Ok was only assumed)
                    greens.append((c, "green", log["stem"], log["meta"]["supervisor"]))
                elif said and said != calllog.RESPONSE.lower() and colour == rostercolour.GREEN:
                    whites.append((c, "white", log["stem"], log["meta"]["supervisor"]))
        return whites + greens

    def response_changed(self, call):
        """A response was changed by hand: from now on the response decides for that name again (a Green box decision
        for it is forgotten), and with Auto green on the roster follows it - Ok: green, anything else: white (the user,
        2026-10-23: "if I turn Ok to not answered make it white automatically too")."""
        stem = self.pack["calls"]["stem"] if self.pack and "calls" in self.pack else None
        if stem:
            ekey = calllog.edit_key(call)
            self.decide(stem, ekey, None)
            self.tried.pop((stem, ekey), None)
        said = call["response"].strip().lower()
        if self.auto_green and said and said != calllog.RESPONSE.lower() and self.green(call) in ("on", "busy"):
            self.say("Call %s, %s: response %s - not confirmed, so the name goes back to white on the roster." % (call["no"], call["name"], call["response"]))
        self.auto_run()

    def auto_run(self):
        """Send the next thing Auto green has to do: one request at a time, each answer starting the next (coloured).
        Only on a roster read fresh (not the saved copy at start-up or after a failed read), and not while Google is
        refusing (back_off)."""
        if not self.auto_for or self.auto_busy or self.gone or not self.view_fresh or time.time() < self.quiet_until:
            return
        link = self.the_link()
        if link is None or not rostercolour.URL_RE.match(link["url"]):
            return
        due = self.auto_due()
        if not due:
            return
        call, action, stem, _ = due[0]
        self.tried[(stem, calllog.edit_key(call))], self.auto_busy = time.time(), True
        self.send_colour(call["ref"], call["name"], action, rostercolour.WHITE if action == "green" else rostercolour.GREEN, auto=True)
        self.refresh()

    # ---- the roster watched: a small read every 30 seconds ----
    def watch(self):
        """Every WATCH seconds: has the roster changed for the shift(s) on show? A small read of just their columns
        (text only, about 5 KB); if it differs from last time the whole roster is read again and what changed is said.
        Every WATCH_FULL-th time the whole roster is read anyway, for colours (names made green or yellow by hand).
        Nothing while Google is refusing (back_off)."""
        self.watch_job = None
        if self.gone:
            return
        self.watch_count += 1
        live = not os.environ.get("CCL_NO_DOWNLOAD") or os.environ.get("CCL_WATCH")
        if live and self.view is not None and self.packs and time.time() >= self.quiet_until:
            spans = set()
            for pack in self.packs:
                try:
                    col = self.view.day_column(pack["date"])
                except calllog.Problem:
                    continue
                try:                                  # (to the next day's column: the next morning's early starts)
                    nxt = self.view.day_column(pack["date"] + datetime.timedelta(days=1))
                except calllog.Problem:
                    nxt = col
                spans.add((calllog.idx_to_col(col), calllog.idx_to_col(max(col, nxt)), self.view.max_row + 60))

            def run():
                seen = {}
                for first, last, rows in sorted(spans):
                    try:
                        seen[(first, last)] = calllog.fetch_columns(first, last, rows)
                    except Exception as e:            # no network, or Google refusing: back off
                        self.queue.put(("watch_failed", str(e)))
                        return
                self.queue.put(("watched", seen))
            threading.Thread(target=run, daemon=True).start()
            if self.watch_count % WATCH_FULL == 0:
                self.load_roster(force=True)
        self.watch_job = self.root.after(WATCH * 1000, self.watch)

    def watched(self, seen):
        self.calm()
        changed = [k for k, text in seen.items() if k in self.watch_seen and self.watch_seen[k] != text]
        self.watch_seen.update(seen)
        if changed:
            self.watch_said = time.strftime("%H:%M")
            self.load_roster(force=True)

    def shift_now(self):
        """The calls on show, for telling what a new copy of the roster changed: {(name, venue): (shift, colour)}."""
        pack = self.pack
        return {(c["name"], c["venue"]): (c["shift"], rostercolour.css(c.get("fill"))) for c in pack["calls"]["calls"]} if pack and "calls" in pack else {}

    def said_changes(self, before, after):
        words = {"#92d050": "green", "#ffffff": "white", "#ffff00": "yellow"}
        added = ["%s (%s %s)" % (n, v, after[(n, v)][0]) for n, v in after if (n, v) not in before]
        gone = ["%s (%s)" % (n, v) for n, v in before if (n, v) not in after]
        moved = ["%s now %s" % (n, after[(n, v)][0]) for n, v in after if (n, v) in before and before[(n, v)][0] != after[(n, v)][0]]
        ours = {name for _, name in list(self.pending) + list(self.sending)}       # (colours this window changed itself)
        colour = ["%s %s" % (n, words.get(after[(n, v)][1], "another colour")) for n, v in after
                  if (n, v) in before and before[(n, v)][1] != after[(n, v)][1] and n not in ours]
        parts = (["added: " + ", ".join(added)] if added else []) + (["gone: " + ", ".join(gone)] if gone else []) + \
                (["times: " + ", ".join(moved)] if moved else []) + (["colour: " + ", ".join(colour[:6]) + (" and %d more" % (len(colour) - 6) if len(colour) > 6 else "")] if colour else [])
        if parts:
            self.say("The roster changed (%s) for this shift - %s." % (self.watch_said or time.strftime("%H:%M"), "; ".join(parts)),
                     "check" if added or gone or moved else "word")

    def ask_link(self, then=None):
        """Ask, once, for the link of the roster's script; write out the script and the steps to put it in."""
        skin, px = self.skin, self.skin.px
        if self.link_box is not None and self.link_box.winfo_exists():
            self.link_box.lift()
            return
        folder = rostercolour.write_setup(self.link)
        top = self.link_box = tk.Toplevel(self.root, bg=skin["panel"])
        top.title("Turn names green on the roster")
        top.transient(self.root)
        top.resizable(False, False)
        top.geometry("+%d+%d" % (self.root.winfo_rootx() + px(120), self.root.winfo_rooty() + px(120)))
        tk.Label(top, text="Turning a name green on the roster needs a small script in the roster, put in once by someone who "
                           "can edit it - about five minutes. The steps and the script are in the folder \"Roster script\" "
                           "(Show me the steps). It can only change a name's colour, never any text.\n\n"
                           "When it is in, paste its link here (it ends in /exec):",
                 justify="left", wraplength=px(540), font=skin.font("body"), bg=skin["panel"], fg=skin["ink"]).pack(anchor="w", padx=px(18), pady=(px(16), px(8)))
        var = tk.StringVar(value=self.link.get("url", ""))
        Field(top, skin, var, width=540).pack(fill="x", padx=px(18))
        msg = self.link_msg = tk.Label(top, text="", justify="left", wraplength=px(540), font=skin.font("caption"), bg=skin["panel"], fg=skin["ink3"])
        msg.pack(anchor="w", padx=px(18), pady=(px(6), 0))
        row = tk.Frame(top, bg=skin["panel"])
        row.pack(fill="x", padx=px(18), pady=(px(10), px(16)))
        top.testing = False

        def save():
            if top.testing:                           # (pressed again while the test is out: once is enough)
                return
            url = var.get().strip()
            if not rostercolour.URL_RE.match(url):
                msg.configure(text="That is not the link Apps Script gives: it starts https://script.google.com/macros/s/ and ends /exec.", fg=skin["red"])
                return
            self.link = dict(self.link, url=url)
            rostercolour.save(self.link)
            msg.configure(text="Testing the link...", fg=skin["ink3"])
            top.testing = True
            link = dict(self.link)

            def run():
                try:
                    answer = rostercolour.send(link, "ping")
                except calllog.Problem as e:
                    answer = {"ok": False, "why": str(e)}
                self.queue.put(("ping", answer, then))
            threading.Thread(target=run, daemon=True).start()
        Button(row, skin, "Save and test", save, kind="accent").pack(side="right")
        Button(row, skin, "Cancel", top.destroy, kind="quiet").pack(side="right", padx=(0, px(8)))
        Button(row, skin, "Show me the steps", lambda: self.launch(folder), kind="plain", icon="folder").pack(side="left")

    def check_script(self):
        """A few seconds after the window opens: ask the roster's script which version it is (a ping, which changes
        nothing) - so after a new version is put in, the window says whether it took. Not in a window started for an
        automated check (CCL_SCRIPT_CHECK lets a check ask for it)."""
        self.script_job = None
        if self.gone or (os.environ.get("CCL_NO_DOWNLOAD") and not os.environ.get("CCL_SCRIPT_CHECK")):
            return
        link = self.the_link()
        if link is None or not rostercolour.URL_RE.match(link["url"]):
            return
        link = dict(link)

        def run():
            try:
                answer = rostercolour.send(link, "ping")
            except calllog.Problem:
                return                                # (no network now: nothing to say - a press will say it)
            self.queue.put(("script", answer))
        threading.Thread(target=run, daemon=True).start()

    def script_checked(self, answer):
        if answer.get("ok"):
            if script_old(answer):
                self.say("The roster still runs an older version of its script - names still turn green, but without its newest "
                         "checks. Put in the new one: the steps at the end of \"How to switch it on.txt\" (Roster script folder).", "check")
            else:
                self.say("The roster's script is up to date (version %d)." % answer["version"])
        elif answer.get("why") == "wrong key":
            self.say("The roster's script does not have this PC's key: paste \"Roster script.gs\" in again and deploy a new version "
                     "(\"How to switch it on.txt\" in the Roster script folder).", "check")

    def pinged(self, answer, then):
        """The roster's script has answered the test of its link."""
        box = self.link_box if self.link_box is not None and self.link_box.winfo_exists() else None
        if box is not None:
            box.testing = False
        if answer.get("ok"):
            if box:
                box.destroy()
            self.say("Turning names green on the roster is on - the roster answered%s." % (
                " - but it runs an older version of its script: put in the new one (the end of \"How to switch it on.txt\")"
                if script_old(answer) else " (its script is version %d, the newest)" % answer["version"]), "check" if script_old(answer) else "word")
            if then and box:                          # (only for the box still open: a second answer does nothing)
                then()
        else:
            why = answer.get("why") or "no answer"
            if why == "wrong key":
                why = "the script in the roster is not the one in the \"Roster script\" folder - paste that one in again and deploy it"
            if box:
                self.link_msg.configure(text="The roster did not answer as it should: " + why + ".", fg=self.skin["red"])
            self.say("The link to the roster's script did not work: " + why + ".", "check")

    # ---- the files ----
    def flush(self):
        """Write the files of every day on show now (before opening them). True if all of them were written."""
        if not self.settle():
            return False
        bad = []
        for pack in self.packs:
            if self.meta(pack):
                bad += self.write(pack)
        self.refresh()
        return not bad

    def open_all(self):
        """Write the files and open the all-in-one PDF of each day on show: every form of the shift, to print in one go."""
        self.ended = None                             # (the end-of-shift prompt has done its job)
        if not self.flush():
            return
        opened = 0
        for pack in self.packs:
            state = self.written(pack)
            if state and state["paths"].get("all"):
                self.launch(state["paths"]["all"])
                opened += 1
        if opened:
            self.say("Opened the PDF to print%s." % ("" if opened == 1 else " for each of the %d days" % opened))

    def open_folder(self):
        self.flush()
        folder = self.out_dir or calllog.LOGS
        os.makedirs(folder, exist_ok=True)
        self.launch(folder)

    def launch(self, path):
        if os.environ.get("CCL_NOOPEN"):
            return
        try:
            os.startfile(path)
        except OSError as e:
            self.say("Could not open %s: %s" % (os.path.basename(path), e), "check")

    def on_close(self):
        """Keep whatever is being typed, then go. Nothing else is running: the tool starts no other program."""
        self.settle(keep=False)
        self.gone = True
        for job in (self.poll_job, self.tick_job, self.title_job, self.extras_job, self.reread_job, self.watch_job, self.auto_job, self.script_job):
            if job:
                self.root.after_cancel(job)
        self.root.destroy()


def script_old(answer):
    """A ping's answer from a script older than the one in rostercolour.SCRIPT (the first version says no version)."""
    v = answer.get("version")
    return not isinstance(v, int) or isinstance(v, bool) or v < rostercolour.SCRIPT_VERSION


def refuse_send(*args, **kwargs):
    """In place of rostercolour.send in a window started for an automated check: nothing is sent to the roster."""
    raise calllog.Problem("the roster could not be reached (an automated check never reaches the roster)")


def never_activate():
    """For automated checks only (CCL_QUIET): refuse to become the active window, so a window opened to be pictured
    cannot take the keyboard from whoever is using the PC. A hook on this one thread of this one process."""
    import atexit
    import ctypes
    from ctypes import wintypes
    user, kernel = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
    proc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user.SetWindowsHookExW.argtypes, user.SetWindowsHookExW.restype = [ctypes.c_int, proc, ctypes.c_void_p, wintypes.DWORD], ctypes.c_void_p
    user.CallNextHookEx.argtypes, user.CallNextHookEx.restype = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM], ctypes.c_ssize_t
    user.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    kernel.GetCurrentThreadId.restype = wintypes.DWORD
    never_activate.hook = proc(lambda code, w, l: 1 if code in (5, 9) else user.CallNextHookEx(None, code, w, l))   # HCBT_ACTIVATE, HCBT_SETFOCUS
    handle = user.SetWindowsHookExW(5, never_activate.hook, None, kernel.GetCurrentThreadId())                       # WH_CBT
    atexit.register(lambda: user.UnhookWindowsHookEx(handle))


def stand_still(moment):
    """For automated checks only (CCL_NOW): every part of the tool sees this moment as the time now."""
    import types
    import widgets

    class DateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(moment.year, moment.month, moment.day, moment.hour, moment.minute)

    class Date(datetime.date):
        @classmethod
        def today(cls):
            return cls(moment.year, moment.month, moment.day)

    clock = types.SimpleNamespace(datetime=DateTime, date=Date, time=datetime.time, timedelta=datetime.timedelta)
    for module in (sys.modules[__name__], calllog, widgets):
        module.datetime = clock


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)                                   # crisp text on high-DPI screens
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NORTHWIND.CheckCallLog")   # own taskbar icon
    except Exception:
        pass
    if os.environ.get("CCL_QUIET"):                   # a window opened for an automated check must never take the keyboard
        never_activate()
    if os.environ.get("CCL_NOW"):                     # ...and may be shown a clock that stands still: "2026-10-10 21:40"
        stand_still(datetime.datetime.strptime(os.environ["CCL_NOW"], "%Y-%m-%d %H:%M"))
    root = tk.Tk()
    try:
        App(root, os.environ.get("CCL_THEME") or load_settings().get("theme") or "light")
    except Exception:
        messagebox.showerror("Control Room Logs", "The window could not start:\n\n" + traceback.format_exc())
        raise
    root.mainloop()


if __name__ == "__main__":
    main()
