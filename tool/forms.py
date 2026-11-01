"""The control-room forms, drawn and saved directly: no browser, no Excel, no helper program, no temp files.

Each form is a draw_* function that draws onto a "page" - anything with line(), box() and text()
working in millimetres from the top-left corner of an A4 sheet. PdfPage turns those into a PDF;
the window has its own page that draws the same thing on screen, so the preview is the printout.
"""
import zipfile
import math
import zlib
from xml.sax.saxutils import escape

COMPANY = "NORTHWIND SECURITY"
ROWS_PER_PAGE = 25
SHIFT_ROWS = 14                                       # occurrence rows on a Shift Log page
CALL_NOTE = ("Record each check call as it is made. Use response/notes for answered, no answer, escalation, "
             "welfare concern, late check-in, or follow-up required.")
PATROL_NOTE = ("Record each campus patrol upon completion. Note any incidents, access issues, welfare concerns, "
               "or maintenance/security observations.")

MM = 72 / 25.4
PAGE_W, PAGE_H = 595.28, 841.89                       # A4 in points
LEFT, TOP, WIDTH = 12.0, 11.0, 186.0                  # mm
RIGHT = LEFT + WIDTH
# advance widths (1/1000 em) of characters 32..126 in the built-in Helvetica faces
_REGULAR = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278] + [556] * 10 + \
    [278, 278, 584, 584, 584, 556, 1015,
     667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611,
     278, 278, 278, 469, 556, 333,
     556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500,
     334, 260, 334, 584]
_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278] + [556] * 10 + \
    [333, 333, 584, 584, 584, 611, 975,
     722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611,
     333, 278, 333, 584, 556, 333,
     556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500,
     389, 280, 389, 584]
_FONTS = {"F1": ("Helvetica", _REGULAR), "F2": ("Helvetica-Bold", _BOLD), "F3": ("Helvetica-Oblique", _REGULAR)}


def text_width(text, font, size):
    """Width in points of text set in font F1 (regular), F2 (bold) or F3 (italic)."""
    table = _FONTS[font][1]
    return sum(table[ord(ch) - 32] if 32 <= ord(ch) <= 126 else 556 for ch in text) * size / 1000.0


def fit(text, font, size, room):
    """The size to set text at so it is no wider than room (mm); never below 6.5 pt."""
    while room and size > 6.5 and text_width(text, font, size) > room * MM:
        size -= 0.5
    return size


def wrap(text, font, size, room):
    """Break text into lines no wider than room (mm); line breaks already in the text are kept."""
    lines = []
    for part in str(text).split("\n"):
        line = ""
        for word in part.split():
            longer = (line + " " + word).strip()
            if line and text_width(longer, font, size) > room * MM:
                lines.append(line)
                line = word
            else:
                line = longer
        lines.append(line)
    return lines if any(lines) else []


class PdfPage:
    """A page that records PDF drawing operators."""

    def __init__(self):
        self.ops = []

    def line(self, x1, y1, x2, y2, weight):
        self.ops.append("%.2f w %.2f %.2f m %.2f %.2f l S" % (weight * MM, x1 * MM, PAGE_H - y1 * MM, x2 * MM, PAGE_H - y2 * MM))

    def box(self, x, y, w, h, weight=None, grey=None):
        rect = "%.2f %.2f %.2f %.2f re" % (x * MM, PAGE_H - (y + h) * MM, w * MM, h * MM)
        if grey is not None:
            self.ops.append("%.3f g %s f 0 g" % (grey, rect))
        if weight:
            self.ops.append("%.2f w %s S" % (weight * MM, rect))

    def text(self, x, y, h, text, font="F1", size=10.5, align="left", room=None, angle=0):
        """Text centred vertically in a band of height h whose top is y; shrunk if wider than room (mm).
        angle: degrees the text is turned clockwise about its middle (x is then the middle; running downhill)."""
        text = text.encode("cp1252", "replace").decode("cp1252")
        size = fit(text, font, size, room)
        if angle:
            a, wide = math.radians(-angle), text_width(text, font, size)
            px = x * MM - wide / 2.0 * math.cos(a) + size * 0.35 * math.sin(a)
            py = PAGE_H - (y + h / 2) * MM - wide / 2.0 * math.sin(a) - size * 0.35 * math.cos(a)
            raw = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            self.ops.append("BT /%s %.1f Tf %.4f %.4f %.4f %.4f %.2f %.2f Tm (%s) Tj ET" % (
                font, size, math.cos(a), math.sin(a), -math.sin(a), math.cos(a), px, py, raw))
            return
        px = x * MM - {"left": 0, "right": 1, "center": 0.5}[align] * text_width(text, font, size)
        py = PAGE_H - (y + h / 2) * MM - size * 0.35
        raw = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        self.ops.append("BT /%s %.1f Tf %.2f %.2f Td (%s) Tj ET" % (font, size, px, py, raw))


# ---- the pieces every form shares -------------------------------------------------------------
def _title(g, name, subtitle, page_text):
    g.text(LEFT + 2.6, TOP + 2.2, 8, COMPANY, "F2", 17)
    g.text(LEFT + 2.6, TOP + 11.2, 5, subtitle)
    g.text(RIGHT - 2.6, TOP + 2.4, 8, name, "F2", 15.5, "right")
    g.text(RIGHT - 2.6, TOP + 11.2, 5, page_text, align="right")


def _fields(g, y, h, fields):
    """A row of boxes with the label and what was filled in on one line, as the paper forms are written."""
    x = LEFT
    for label, value, share in fields:
        w = WIDTH * share
        g.text(x + 2.2, y, h, label, "F2", 10)
        used = 2.2 + text_width(label, "F2", 10) / MM + 2.6
        if value:
            g.text(x + used, y, h, value, room=w - used - 1.2)
        x += w
        if x < RIGHT - 0.5:
            g.line(x, y, x, y + h, 0.35)


def _table(g, y, head_h, row_h, rows, columns, cell):
    """A ruled table. columns: (title, share, align); cell(row, col) -> text or ''. Returns the bottom edge."""
    edges = [LEFT]
    for _, share, _ in columns:
        edges.append(edges[-1] + WIDTH * share)
    edges[-1] = RIGHT
    g.box(LEFT, y, WIDTH, head_h, grey=0.87)
    for (title, _, align), x0, x1 in zip(columns, edges, edges[1:]):
        if align == "center":
            g.text((x0 + x1) / 2, y, head_h, title, "F2", align="center")
        else:
            g.text(x0 + 2.2, y, head_h, title, "F2", room=x1 - x0 - 3)
    for r in range(rows):
        top = y + head_h + r * row_h
        g.line(LEFT, top, RIGHT, top, 0.35)
        for c, ((_, _, align), x0, x1) in enumerate(zip(columns, edges, edges[1:])):
            value = cell(r, c)
            if value and align == "center":
                g.text((x0 + x1) / 2, top, row_h, value, align="center")
            elif value:
                g.text(x0 + 2.2, top, row_h, value, room=x1 - x0 - 4)
    bottom = y + head_h + rows * row_h
    for x in edges[1:-1]:
        g.line(x, y, x, bottom, 0.35)
    g.box(LEFT, y, WIDTH, bottom - y, 0.6)
    return bottom


# ---- Daily Check Call Log ---------------------------------------------------------------------
def draw_calls(g, meta, calls, page=0, pages=1):
    _title(g, "DAILY CHECK CALL LOG", "Security Daily Control Record", "Page: %d of %d" % (page + 1, pages))
    y1, y2, y3 = TOP + 20, TOP + 28.2, TOP + 35.6
    edges = [LEFT, LEFT + WIDTH * 0.235, LEFT + WIDTH * 0.587, LEFT + WIDTH * 0.803, RIGHT]
    fields = (("Date:", meta["date"]), ("Control Room / Supervisor:", meta["supervisor"]),
              ("Shift:", meta["shift"]), ("Total Calls:", str(meta["total"])))
    for (label, value), x0, x1 in zip(fields, edges, edges[1:]):
        g.text(x0 + 2.6, y1, 8.2, label, "F2", 10, room=x1 - x0 - 4)
        g.text(x0 + 2.6, y2, 7.4, value, room=x1 - x0 - 4)
    for y in (y1, y2):
        g.line(LEFT, y, RIGHT, y, 0.35)
    for x in edges[1:-1]:
        g.line(x, y1, x, y3, 0.35)
    g.box(LEFT, TOP, WIDTH, y3 - TOP, 0.6)
    first = page * ROWS_PER_PAGE

    def cell(r, c):
        n = first + r
        if c == 0:
            return str(n + 1)
        return (calls[n]["name"], calls[n]["venue"], calls[n]["call_text"], calls[n]["response"])[c - 1] if n < len(calls) else ""

    bottom = _table(g, y3 + 6, 8.6, 8.05, ROWS_PER_PAGE, (("No.", 0.065, "center"), ("Employee Name", 0.255, "left"), ("Venue", 0.255, "left"),
                                                          ("Time of Call", 0.135, "left"), ("Response / Notes", 0.29, "left")), cell)
    g.text(LEFT, bottom + 2.6, 4, CALL_NOTE, "F3", 8, room=WIDTH)


# ---- Campus Patrol Log ------------------------------------------------------------------------
def draw_patrols(g, meta, rows, page=0, pages=1):
    """rows: the patrols to print (officer, area, start_text, finish_text, notes). An empty list leaves the form to write on."""
    _title(g, "CAMPUS PATROL LOG", "Campus Patrol Activity Record", "Page: %d" % (page + 1) if pages == 1 else "Page: %d of %d" % (page + 1, pages))
    y1 = TOP + 20
    g.line(LEFT, y1, RIGHT, y1, 0.35)
    _fields(g, y1, 8.2, (("Date:", meta["date"], 0.20), ("Control Room / Supervisor:", meta["supervisor"], 0.42),
                         ("Shift:", meta["shift_name"], 0.19), ("Total Patrols:", str(len(rows)) if rows else "", 0.19)))
    g.line(LEFT, y1 + 8.2, RIGHT, y1 + 8.2, 0.35)
    y3 = y1 + 8.2 + 5.4
    g.box(LEFT, TOP, WIDTH, y3 - TOP, 0.6)
    first = page * ROWS_PER_PAGE

    def cell(r, c):
        n = first + r
        if c == 0:
            return str(n + 1)
        if n >= len(rows):
            return ""
        return (rows[n]["officer"], rows[n]["area"], rows[n]["start_text"], rows[n]["finish_text"], rows[n]["notes"])[c - 1]

    bottom = _table(g, y3 + 5.6, 8.2, 8.12, ROWS_PER_PAGE, (
        ("No.", 0.065, "center"), ("Officer Name", 0.235, "left"), ("Campus / Area", 0.22, "left"), ("Patrol Start", 0.14, "left"),
        ("Patrol Finish", 0.14, "left"), ("Notes / Incidents", 0.20, "left")), cell)
    g.text(LEFT, bottom + 2.6, 4, PATROL_NOTE, "F3", 8, room=WIDTH)


# ---- Shift Log --------------------------------------------------------------------------------
def shift_rows(entries):
    """The occurrence table as it prints: a long occurrence runs on into the rows beneath it."""
    rows = []
    for e in entries:
        lines = wrap(e["text"], "F1", 10.5, WIDTH * 0.619 - 4.4) or [""]
        rows.append((e["time"], e["area"], lines[0]))
        rows.extend(("", "", more) for more in lines[1:])
    return rows


def draw_shift(g, meta, log=None, page=0, pages=1):
    """The heading is always filled in (date, shift times, officer). log may add what was typed in the window:
    entries (time, area, text), handover, supervisor, incoming. A log with no lines carries log["quiet"] (NO MAJOR
    OCCURRENCE) in large bold letters down the diagonal of the table, and log["handover_default"] stands in for
    handover notes that were not typed (NTR, large - or AS PER OCCURRENCES ABOVE when lines were typed); a caller
    that passes neither gets the form blank to write on."""
    log = log or {}
    rows, first, last_page = shift_rows(log.get("entries", [])), page * SHIFT_ROWS, page == pages - 1
    _title(g, "SHIFT LOG", "Security Shift Activity & Handover Record", "Page: 1" if pages == 1 else "Page: %d of %d" % (page + 1, pages))
    y1 = TOP + 20
    g.line(LEFT, y1, RIGHT, y1, 0.35)
    _fields(g, y1, 8.2, (("Date:", meta["date"], 0.17), ("Start of Shift:", meta["start_text"], 0.21), ("End of Shift:", meta["end_text"], 0.20),
                         ("Officer:", meta["supervisor"], 0.24), ("Supervisor:", log.get("supervisor", ""), 0.18)))
    g.line(LEFT, y1 + 8.2, RIGHT, y1 + 8.2, 0.35)
    y3 = y1 + 8.2 + 7
    g.line(LEFT, y3, RIGHT, y3, 0.35)
    g.box(LEFT, TOP, WIDTH, y3 + 1.6 - TOP, 0.6)
    bottom = _table(g, y3 + 1.6 + 6.5, 9, 9.6, SHIFT_ROWS, (("Time", 0.099, "left"), ("Location / Area", 0.282, "left"),
                                                            ("Occurrence / Action Taken", 0.619, "left")),
                    lambda r, c: rows[first + r][c] if first + r < len(rows) else "")
    if log.get("quiet") and not rows and page == 0:      # written down the table from corner to corner, as by hand
        top, tall = y3 + 1.6 + 6.5 + 9, 9.6 * SHIFT_ROWS
        g.text(LEFT + WIDTH / 2.0, top, tall, log["quiet"], "F2", 44, "center", math.hypot(WIDTH, tall) * 0.82,
               angle=math.degrees(math.atan2(tall, WIDTH)))
    notes = wrap(log.get("handover", ""), "F1", 10.5, WIDTH - 8)[:6] if last_page else []
    hy = bottom + 4.5
    g.box(LEFT, hy, WIDTH, 9, grey=0.87)
    g.text(LEFT + 3, hy, 9, "HANDOVER NOTES TO NEXT SECURITY OFFICER", "F2", 11)
    g.line(LEFT, hy + 9, RIGHT, hy + 9, 0.35)
    # Nothing typed: NTR (or what stands in for it) large in the middle. The ruled lines stop short of it on both
    # sides - a line running through the words would read as the words struck out.
    stand_in = log.get("handover_default", "") if last_page and not notes else ""
    middle, across = LEFT + WIDTH / 2.0, hy + 9 + 7.4 * 3
    size = fit(stand_in, "F2", 48 if len(stand_in) <= 4 else 22, WIDTH - 24)
    half, reach = text_width(stand_in, "F2", size) / MM / 2.0 + 4, size * 0.36 / MM + 1
    for i in range(6):
        y = hy + 9 + 7.4 * (i + 1)
        if i < len(notes):
            g.text(LEFT + 4, y - 6.3, 6, notes[i], room=WIDTH - 8)
        if stand_in and abs(y - across) < reach:
            g.line(LEFT + 3, y, middle - half, y, 0.25)
            g.line(middle + half, y, RIGHT - 3, y, 0.25)
        else:
            g.line(LEFT + 3, y, RIGHT - 3, y, 0.25)
    if stand_in:
        g.text(middle, hy + 9, 7.4 * 6, stand_in, "F2", size, "center")
    g.box(LEFT, hy, WIDTH, 9 + 7.4 * 6 + 4.5, 0.6)
    sy = hy + 9 + 7.4 * 6 + 4.5 + 5
    for label, x0, x1 in (("Outgoing Officer Signature:", LEFT, LEFT + 88), ("Incoming Officer:", LEFT + 105, LEFT + 165)):
        g.text(x0, sy, 5, label, size=10)
        after = x0 + text_width(label, "F1", 10) / MM + 1.5
        if label.startswith("Incoming") and last_page and log.get("incoming"):
            g.text(after + 1.5, sy - 0.4, 5, log["incoming"], room=x1 - after - 2)
        g.line(after, sy + 4.4, x1, sy + 4.4, 0.25)


# ---- saving -----------------------------------------------------------------------------------
def _save_pdf(pages, path):
    objects = ["<< /Type /Catalog /Pages 2 0 R >>".encode(),
               ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % (6 + 2 * i) for i in range(len(pages))), len(pages))).encode()]
    for name in ("F1", "F2", "F3"):
        objects.append(("<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % _FONTS[name][0]).encode())
    for i, page in enumerate(pages):
        packed = zlib.compress("\n".join(page.ops).encode("cp1252"))
        objects.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents %d 0 R /Resources "
                        "<< /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> >>" % (PAGE_W, PAGE_H, 7 + 2 * i)).encode())
        objects.append(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(packed) + packed + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    with open(path, "wb") as f:
        f.write(out)


def _paged(draw, meta, rows):
    pages = max(1, -(-len(rows) // ROWS_PER_PAGE))
    sheets = []
    for p in range(pages):
        sheets.append(PdfPage())
        draw(sheets[-1], meta, rows, p, pages)
    return sheets


def call_pages(meta, calls):
    """The Daily Check Call Log, 25 numbered rows to a page."""
    return _paged(draw_calls, meta, calls)


def patrol_pages(meta, rows):
    return _paged(draw_patrols, meta, rows)


def shift_pages(log):
    pages = max(1, -(-len(shift_rows(log.get("entries", []))) // SHIFT_ROWS))
    sheets = []
    for p in range(pages):
        sheets.append(PdfPage())
        draw_shift(sheets[-1], log["meta"], log, p, pages)
    return sheets


def write_pages(pages, path):
    """Save any run of pages as one PDF (one form, or several forms one after another)."""
    _save_pdf(pages, path)


def write_pdf(meta, calls, path):
    _save_pdf(call_pages(meta, calls), path)


def write_patrol_pdf(meta, rows, path):
    _save_pdf(patrol_pages(meta, rows), path)


def write_shift_pdf(log, path):
    _save_pdf(shift_pages(log), path)


# ---- spreadsheet (check call log only) --------------------------------------------------------
SHEET_ROWS = 43                                       # numbered rows on the sheet, more if the log needs them
_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
# cell looks: 1 title, 2 text, 3 bold label, 4 number, 5 date, 6 time, 7 footnote
_STYLES = _HEAD + '<styleSheet xmlns="%s">' % _MAIN + \
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="dd/mm/yy"/></numFmts>' \
    '<fonts count="4"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font>' \
    '<font><b/><sz val="16"/><name val="Calibri"/></font><font><i/><sz val="9"/><name val="Calibri"/></font></fonts>' \
    '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>' \
    '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>' \
    '<border><left style="thin"><color auto="1"/></left><right style="thin"><color auto="1"/></right>' \
    '<top style="thin"><color auto="1"/></top><bottom style="thin"><color auto="1"/></bottom><diagonal/></border></borders>' \
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>' \
    '<cellXfs count="8"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>' + "".join(
        '<xf numFmtId="%d" fontId="%d" fillId="0" borderId="%d" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"%s>'
        '<alignment horizontal="%s" vertical="center"%s/></xf>' % (fmt, font, border, ' applyNumberFormat="1"' if fmt else "", side, wrap)
        for fmt, font, border, side, wrap in ((0, 2, 1, "left", ""), (0, 0, 1, "left", ""), (0, 1, 1, "left", ""), (0, 0, 1, "right", ""),
                                               (164, 0, 1, "right", ""), (20, 0, 1, "right", ""), (0, 3, 0, "left", ' wrapText="1"'))) + \
    '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'


def _cell(ref, look, value=None):
    if value is None or value == "":
        return '<c r="%s" s="%d"/>' % (ref, look)
    if isinstance(value, (int, float)):
        return '<c r="%s" s="%d"><v>%r</v></c>' % (ref, look, value)
    return '<c r="%s" s="%d" t="inlineStr"><is><t>%s</t></is></c>' % (ref, look, escape(str(value)))


def _row(n, cells, height=None):
    looks_values = "".join(_cell("%s%d" % (col, n), look, value) for col, (look, value) in zip("ABCDE", cells))
    return '<row r="%d"%s>%s</row>' % (n, ' ht="%s" customHeight="1"' % height if height else "", looks_values)


def write_xlsx(meta, calls, path):
    """The check call log as a one-sheet workbook laid out like the control room's sheet; prints on one A4 page."""
    count = max(SHEET_ROWS, len(calls))
    note = 7 + count + 5
    rows = [
        _row(1, [(1, COMPANY + "    DAILY CHECK CALL LOG")] + [(1, None)] * 4, 24),
        _row(2, [(2, "Security Daily Control Record")] + [(2, None)] * 4),
        _row(3, [(2, None)] * 5),
        _row(4, [(3, "Date:"), (3, "Control Room / Supervisor:"), (3, None), (3, "Shift:"), (3, "Total Calls:")]),
        _row(5, [(5, meta["date_serial"]), (2, meta["supervisor"]), (2, None), (2, meta["shift"]), (4, meta["total"])]),
        _row(6, [(2, None)] * 5),
        _row(7, [(3, "No."), (3, "Employee Name"), (3, "Venue"), (3, "Time of Call"), (3, "Response / Notes")]),
    ]
    for i in range(count):
        c = calls[i] if i < len(calls) else None
        when = c["clock"] / 1440.0 if c and c["clock"] is not None else None
        rows.append(_row(8 + i, [(4, i + 1), (2, c and c["name"]), (2, c and c["venue"]), (6, when), (2, c and c["response"])]))
    rows.append(_row(note, [(7, CALL_NOTE)] + [(7, None)] * 4, 27))
    sheet = _HEAD + '<worksheet xmlns="%s"><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>' % _MAIN + \
        '<dimension ref="A1:E%d"/><sheetViews><sheetView workbookViewId="0"/></sheetViews><sheetFormatPr defaultRowHeight="15"/>' % note + \
        "<cols>" + "".join('<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i, i, w)
                           for i, w in enumerate((9.71, 26.14, 29.57, 12.43, 37.86), 1)) + "</cols>" + \
        "<sheetData>" + "".join(rows) + "</sheetData>" + \
        '<mergeCells count="5">' + "".join('<mergeCell ref="%s"/>' % r for r in ("A1:E1", "A2:E2", "B4:C4", "B5:C5", "A%d:E%d" % (note, note))) + \
        '</mergeCells><printOptions horizontalCentered="1"/><pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>' \
        '<pageSetup paperSize="9" orientation="portrait" fitToWidth="1" fitToHeight="1"/></worksheet>'
    parts = {
        "[Content_Types].xml": _HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        "_rels/.rels": _HEAD + '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/officeDocument" Target="xl/workbook.xml"/></Relationships>' % (_PKG, _REL),
        "xl/workbook.xml": _HEAD + '<workbook xmlns="%s" xmlns:r="%s"><sheets><sheet name="Daily Check Call Log" sheetId="1" r:id="rId1"/></sheets></workbook>' % (_MAIN, _REL),
        "xl/_rels/workbook.xml.rels": _HEAD + '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="%s/styles" Target="styles.xml"/></Relationships>' % (_PKG, _REL, _REL),
        "xl/styles.xml": _STYLES,
        "xl/worksheets/sheet1.xml": sheet,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            z.writestr(name, text.encode("utf-8"))
