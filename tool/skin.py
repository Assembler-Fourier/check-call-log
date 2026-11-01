"""The look of the Control Room Logs window: the colours of its two themes, its fonts, and smooth rounded shapes.

Standard library only. Anything curved is worked out pixel by pixel (anti-aliased by its distance from the
outline), written as PNG bytes by hand and loaded with tk.PhotoImage, because the shapes Tk's canvas draws
itself are jagged on Windows. Straight edges are ordinary canvas rectangles and lines. What shows through a
transparent corner is whatever the same canvas drew underneath, so every surface is a flat, known colour:
there is no translucency, blur or gradient anywhere, by design.
"""
import math
import struct
import tkinter as tk
import zlib
from tkinter import font as tkfont


def png(width, height, rgba):
    """PNG file bytes from width*height*4 bytes of RGBA."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    stride = width * 4
    raw = b"".join(b"\x00" + bytes(rgba[y * stride:(y + 1) * stride]) for y in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def rgb(colour):
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def mix(a, b, t):
    """The colour t of the way from a to b (0 = a, 1 = b)."""
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t + 0.5) for x, y in zip(rgb(a), rgb(b)))


def contrast(a, b):
    """WCAG contrast ratio of two colours."""
    def luminance(colour):
        parts = [c / 255.0 for c in rgb(colour)]
        parts = [p / 12.92 if p <= 0.03928 else ((p + 0.055) / 1.055) ** 2.4 for p in parts]
        return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def paint(width, height, cover, fill, edge=None, edge_width=1.0):
    """RGBA bytes of a shape: cover(x, y) is the signed distance in pixels from the pixel's centre to the shape's
    outline (negative inside). fill None leaves the inside empty (a ring); edge is an outline inside the shape."""
    fr, fg, fb = rgb(fill or edge)
    er, eg, eb = rgb(edge) if edge else (fr, fg, fb)
    buf = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            d = cover(x + 0.5, y + 0.5)
            outer = 0.5 - d
            if outer <= 0:
                continue
            outer = min(outer, 1.0)
            inner = min(1.0, max(0.0, 0.5 - (d + edge_width))) if edge else outer     # how much lies inside the outline
            i = (y * width + x) * 4
            if fill:
                part = inner / outer
                buf[i], buf[i + 1], buf[i + 2] = int(er + (fr - er) * part + 0.5), int(eg + (fg - eg) * part + 0.5), int(eb + (fb - eb) * part + 0.5)
                buf[i + 3] = int(255 * outer + 0.5)
            else:
                buf[i], buf[i + 1], buf[i + 2], buf[i + 3] = er, eg, eb, int(255 * (outer - inner) + 0.5)
    return buf


def rounded(width, height, radius):
    """Distance function of a rounded rectangle filling width x height."""
    cx, cy = width / 2.0, height / 2.0
    radius = min(radius, cx, cy)

    def cover(px, py):
        qx, qy = abs(px - cx) - (cx - radius), abs(py - cy) - (cy - radius)
        return (math.hypot(qx, qy) if qx > 0 and qy > 0 else max(qx, qy)) - radius
    return cover


def diamond(size):
    half = size / 2.0
    return lambda px, py: (abs(px - half) + abs(py - half) - half) * 0.7071


def strokes(lines, width):
    """Distance function of line segments [(x0, y0, x1, y1), ...] drawn `width` thick with round ends (small icons)."""
    def cover(px, py):
        best = 1e9
        for x0, y0, x1, y1 in lines:
            dx, dy = x1 - x0, y1 - y0
            t = 0.0 if not (dx or dy) else min(1.0, max(0.0, ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy)))
            best = min(best, math.hypot(px - x0 - t * dx, py - y0 - t * dy))
        return best - width / 2.0
    return cover


# Two looks. The names are roles, never hues: text is ink / ink2 / ink3, surfaces are base (the window), panel (where
# the work is), raised (the cell being typed in, pop-ups) and sunk (wells). ONE accent means "you can act on this /
# this is chosen / this is now"; amber means only "check this"; red only "this cannot be right". Green is the roster's own
# "confirmed" (92D050, the colour its confirmed names are filled with), and nothing else.
THEMES = {
    "light": dict(
        base="#f3f3f3", sunk="#ebebeb", panel="#fbfbfb", raised="#ffffff",
        ink="#1a1a1a", ink2="#424242", ink3="#616161", off="#a0a0a0",
        line="#e5e5e5", edge="#d1d1d1", strong="#868686",
        control="#ffffff", hover="#f5f5f5", pressed="#e6e6e6", row="#f1f1f1", chosen="#e9e9e9", band="#f5f6f7", well="#f0f1f3", well_chosen="#dcdcdc",
        accent="#005fb8", accent_hover="#1a70c0", accent_pressed="#00529e", on_accent="#ffffff", tint="#e3eef9", tint_edge="#a9cbee", off_fill="#dcdcdc",
        amber="#9d5d00", amber_tint="#fff4ce", red="#c42b1c", red_tint="#fde7e9",
        confirm="#92d050", confirm_edge="#6fa82f", on_confirm="#1a1a1a", provisional="#ffff00", on_provisional="#1a1a1a",
        page="#ffffff", page_ink="#000000", page_grey="#dedede", page_edge="#cfcfcf", backdrop="#e4e4e4",
        ring="#1a1a1a", ring_in="#ffffff", title="#f3f3f3"),
    "dark": dict(
        base="#202020", sunk="#1a1a1a", panel="#282828", raised="#2e2e2e",
        ink="#e6e6e6", ink2="#bdbdbd", ink3="#9e9e9e", off="#6b6b6b",
        line="#363636", edge="#454545", strong="#9d9d9d",
        control="#333333", hover="#3a3a3a", pressed="#2c2c2c", row="#2f2f2f", chosen="#383838", band="#2c2c2c", well="#313131", well_chosen="#454545",
        accent="#4cc2ff", accent_hover="#47b1e8", accent_pressed="#42a1d2", on_accent="#000000", tint="#2a3c46", tint_edge="#3f6b84", off_fill="#3a3a3a",
        amber="#f2c661", amber_tint="#42361c", red="#ff99a4", red_tint="#442726",
        confirm="#92d050", confirm_edge="#b6e382", on_confirm="#1a1a1a", provisional="#ffff00", on_provisional="#1a1a1a",
        page="#dbdbdb", page_ink="#1a1a1a", page_grey="#bcbcbc", page_edge="#555555", backdrop="#181818",
        ring="#ffffff", ring_in="#000000", title="#202020"),
}

# Icons are glyphs that Windows' two icon fonts share (Segoe Fluent Icons on Windows 11, Segoe MDL2 Assets on 10).
ICONS = {"print": "", "folder": "", "add": "", "remove": "", "undo": "", "redo": "", "edit": "",
         "moon": "", "sun": "", "calendar": "", "person": "", "clock": "", "warning": "", "error": "",
         "info": "", "check": "", "close": "", "prev": "", "next": "", "down": "", "up": "",
         "refresh": "", "fill": "", "phone": "", "walk": "", "list": "", "page": "", "clear": "",
         "more": "", "keyboard": "", "rounds": ""}


class Skin:
    """Everything a widget needs to draw itself in the look in use: colours by role (skin["ink"]), fonts by role
    (skin.font("data")), sizes scaled for the screen (skin.px(8)), icons and cached rounded shapes."""

    def __init__(self, root, theme="light"):
        self.root = root
        self.scale = root.winfo_fpixels("1i") / 96.0
        self.images = {}                             # Tk forgets an image nobody holds: they are all held here
        families = set(tkfont.families(root))
        self.icon_family = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in families), None)
        strong = "Segoe UI Semibold" if "Segoe UI Semibold" in families else "Segoe UI"
        # Classic Segoe UI Regular is the only face here whose digits are all the same width, so every time and
        # number is set in it (never Semibold, never Segoe UI Variable): a column of times then lines up.
        self.fonts = {}
        for role, family, size, weight in (("caption", "Segoe UI", 12, "normal"), ("body", "Segoe UI", 14, "normal"), ("strong", strong, 14, "normal"),
                                           ("data", "Segoe UI", 15, "normal"), ("data_strong", "Segoe UI", 15, "bold"), ("subtitle", strong, 20, "normal"),
                                           ("clock", "Segoe UI", 20, "normal"), ("title", strong, 26, "normal"), ("small", "Segoe UI", 11, "normal")):
            self.fonts[role] = tkfont.Font(root, family=family, size=-self.px(size), weight=weight)
        for role, size in (("icon", 16), ("icon_small", 12), ("icon_large", 20)):
            self.fonts[role] = tkfont.Font(root, family=self.icon_family or "Segoe UI", size=-self.px(size))
        self.widths = {}
        self.use(theme)

    def use(self, theme):
        self.theme = theme if theme in THEMES else "light"
        self.colours = THEMES[self.theme]

    def __getitem__(self, role):
        return self.colours[role]

    def px(self, n):
        return int(round(n * self.scale))

    def font(self, role):
        return self.fonts[role]

    def icon(self, name):
        """The glyph for an icon, or '' on a PC with neither icon font (buttons are then words only)."""
        return ICONS.get(name, "") if self.icon_family else ""

    def measure(self, role, text):
        key = (role, text)
        if key not in self.widths:
            if len(self.widths) > 6000:
                self.widths.clear()
            self.widths[key] = self.fonts[role].measure(text)
        return self.widths[key]

    def fit(self, role, text, room):
        """The text cut to fit `room` pixels, with an ellipsis if it had to be cut."""
        if self.measure(role, text) <= room:
            return text
        lo, hi = 0, len(text)
        while lo < hi:                               # the longest beginning that fits with the ellipsis
            mid = (lo + hi + 1) // 2
            if self.measure(role, text[:mid].rstrip() + "…") <= room:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo].rstrip() + "…" if lo else ""

    # ---- shapes ----
    def image(self, key, make):
        """A cached image: make() returns (width, height, rgba bytes)."""
        key = (self.theme,) + key
        if key not in self.images:
            width, height, data = make()
            self.images[key] = tk.PhotoImage(master=self.root, data=png(width, height, data))
        return self.images[key]

    def shape(self, width, height, radius, fill, edge=None, edge_width=1.0):
        """A whole rounded rectangle as one image (for small things of a fixed size: dots, pills, marks)."""
        return self.image(("shape", width, height, radius, fill, edge, edge_width),
                          lambda: (width, height, paint(width, height, rounded(width, height, radius), fill, edge, edge_width)))

    def mark(self, kind, size, colour):
        """A small mark: 'dot', 'ring', 'diamond', 'check', 'tick' (a thin upright bar is just a rectangle - not here)."""
        def make():
            if kind == "diamond":
                return size, size, paint(size, size, diamond(size), colour)
            if kind == "ring":
                return size, size, paint(size, size, rounded(size, size, size / 2.0), None, colour, max(1.5, size / 6.0))
            if kind == "check":
                s = size / 16.0
                return size, size, paint(size, size, strokes([(3.5 * s, 8.5 * s, 6.8 * s, 11.5 * s), (6.8 * s, 11.5 * s, 12.5 * s, 4.8 * s)], 1.8 * s), colour)
            return size, size, paint(size, size, rounded(size, size, size / 2.0), colour)
        return self.image(("mark", kind, size, colour), make)

    def corners(self, radius, fill, edge=None, edge_width=1.0):
        """The four corners of a rounded box as images (top-left, top-right, bottom-left, bottom-right)."""
        def quarter(right, bottom):
            def make():
                side = radius * 2
                whole = paint(side, side, rounded(side, side, radius), fill, edge, edge_width)
                out = bytearray()
                for y in range(radius * bottom, radius * bottom + radius):
                    at = (y * side + radius * right) * 4
                    out += whole[at:at + radius * 4]
                return radius, radius, out
            return self.image(("corner", radius, fill, edge, edge_width, right, bottom), make)
        return quarter(0, 0), quarter(1, 0), quarter(0, 1), quarter(1, 1)

    def box(self, canvas, x0, y0, x1, y1, radius, fill, edge=None, tags=(), edge_width=1):
        """Draw a rounded box of any size on a canvas: smooth corners from images, the rest plain rectangles."""
        r = min(radius, (x1 - x0) // 2, (y1 - y0) // 2)
        if fill:
            canvas.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, outline="", tags=tags)
            canvas.create_rectangle(x0, y0 + r, x1, y1 - r, fill=fill, outline="", tags=tags)
        if edge:
            w = edge_width
            for a, b, c, d in ((x0 + r, y0, x1 - r, y0 + w), (x0 + r, y1 - w, x1 - r, y1), (x0, y0 + r, x0 + w, y1 - r), (x1 - w, y0 + r, x1, y1 - r)):
                canvas.create_rectangle(a, b, c, d, fill=edge, outline="", tags=tags)
        if r > 0:
            tl, tr, bl, br = self.corners(r, fill, edge, float(edge_width))
            for image, x, y in ((tl, x0, y0), (tr, x1 - r, y0), (bl, x0, y1 - r), (br, x1 - r, y1 - r)):
                canvas.create_image(x, y, image=image, anchor="nw", tags=tags)


def title_bar(root, skin):
    """Ask Windows to colour this window's own title bar to match the look (dark when dark; on Windows 11 the
    exact header colour). It touches this window's frame only: no setting of the PC is changed. True if it took."""
    try:
        import ctypes
        user, dwm = ctypes.WinDLL("user32"), ctypes.WinDLL("dwmapi")
        user.GetParent.restype, user.GetParent.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        user.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
        frame = user.GetParent(root.winfo_id())
        if not frame:
            return False                              # not on the screen yet
        put = lambda attribute, value: dwm.DwmSetWindowAttribute(frame, attribute, ctypes.byref(ctypes.c_int(value)), 4)
        dark = 1 if skin.theme == "dark" else 0
        if put(20, dark) != 0:                        # DWMWA_USE_IMMERSIVE_DARK_MODE (19 on early Windows 10)
            put(19, dark)
        colorref = lambda colour: rgb(colour)[0] | rgb(colour)[1] << 8 | rgb(colour)[2] << 16
        put(35, colorref(skin["title"]))              # Windows 11 only: caption, its text, the border; ignored elsewhere
        put(36, colorref(skin["ink"]))
        put(34, colorref(skin["edge"]))
        user.SetWindowPos(frame, None, 0, 0, 0, 0, 0x0037)    # same place and size: just draw the frame again
        return True
    except Exception:
        return False                                  # an older Windows: the title bar stays as it is
