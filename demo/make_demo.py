"""Demo roster for Check Call Log.

roster-template.xlsx is a real roster layout with every person, venue and note replaced by invented ones and
all cell comments removed. build() copies it with its dates moved forward by whole weeks, so its newest week
is always the current one and the window always has tonight's shift to show.

    python demo/make_demo.py        write tool/cache/roster.xlsx now
"""
import datetime
import os
import re
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "roster-template.xlsx")
TEMPLATE_SUNDAY = datetime.date(2026, 11, 1)      # the week the template was taken in
EPOCH = datetime.date(1899, 12, 30)


def sunday_of(d):
    return d + datetime.timedelta(days=6 - d.weekday())


def build(dest, today=None):
    today = today or datetime.date.today()
    shift = (sunday_of(today) - TEMPLATE_SUNDAY).days
    stamp = dest + ".demo"
    if os.path.exists(dest) and os.path.exists(stamp) and open(stamp).read().strip() == str(shift):
        return dest
    lo, hi = (datetime.date(2020, 1, 1) - EPOCH).days, (datetime.date(2035, 1, 1) - EPOCH).days

    def move(g):
        n = float(g.group(2))
        if lo <= n <= hi and n == int(n):
            return "%s%d%s" % (g.group(1), int(n) + shift, g.group(3))
        return g.group(0)

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with zipfile.ZipFile(TEMPLATE) as zin, zipfile.ZipFile(dest + ".part", "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.startswith("xl/worksheets/sheet"):
                text = re.sub(r'(<c r="[A-Z]+\d+"(?: s="\d+")?><v>)([\d.]+)(</v>)', move, data.decode("utf-8"))
                data = text.encode("utf-8")
            zout.writestr(info, data)
    os.replace(dest + ".part", dest)
    with open(stamp, "w") as f:
        f.write(str(shift))
    return dest


if __name__ == "__main__":
    print(build(os.path.join(os.path.dirname(HERE), "tool", "cache", "roster.xlsx")))
