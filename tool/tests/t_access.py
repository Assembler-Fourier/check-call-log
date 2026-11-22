"""Previously untested calendar keyboard paths and narrow notice action geometry.

Invisible application-owned Tk only, synthetic roster and fake callbacks; no browser or live data.
"""
import datetime
import faulthandler
import os
import sys
import time
from types import SimpleNamespace

from drive6 import Rig, HOME
from drive import quiet_state

total = []
faulthandler.dump_traceback_later(45, repeat=False)
fixture = os.path.join(HOME, 'tool', 'tests', 'roster-2026-10-19.xlsx')

for theme in ('light', 'dark'):
    print('Starting isolated accessibility checks: ' + theme, flush=True)
    rig = Rig('access-' + theme, now='2026-10-23 21:40', theme=theme, roster=fixture,
              settings={'auto_green_for': [], 'preview': False, 'chooser': True})
    try:
        import widgets
        rig.show('Lena Drayton', '2026-10-23')
        picks = []
        cal = widgets.Calendar(rig.root, rig.app.skin, lambda days, add=False: picks.append((days,add)), lambda day: 'night')
        cal.pack()
        cal.show([datetime.date(2026,10,31)])
        rig.pump(80)
        rig.check(theme + ': calendar participates in Tab traversal', str(cal.cget('takefocus')) == '1')
        cal.key(SimpleNamespace(keysym='Right',state=0))
        rig.check(theme + ': moving across month does not select a log',
                  cal.cursor == datetime.date(2026,11,1) and (cal.year,cal.month)==(2026,11) and not picks)
        cal.key(SimpleNamespace(keysym='Return',state=0))
        rig.check(theme + ': Enter chooses the visible cursor date', picks == [([datetime.date(2026,11,1)],False)])
        cal.key(SimpleNamespace(keysym='Down',state=0))
        cal.key(SimpleNamespace(keysym='space',state=4))
        rig.check(theme + ': Ctrl+Space adds a different date', picks[-1] == ([datetime.date(2026,11,8)],True))
        cal.show([datetime.date(2028,1,31)])
        cal.key(SimpleNamespace(keysym='Next',state=0))
        rig.check(theme + ': PageDown clamps at leap February', cal.cursor == datetime.date(2028,2,29))
        cal.key(SimpleNamespace(keysym='Prior',state=0))
        rig.check(theme + ': PageUp preserves the available day', cal.cursor == datetime.date(2028,1,29))
        cal.key(SimpleNamespace(keysym='End',state=0))
        rig.check(theme + ': End reaches the final date', cal.cursor == datetime.date(2028,1,31))
        cal.key(SimpleNamespace(keysym='Home',state=0))
        rig.check(theme + ': Home reaches the first date', cal.cursor == datetime.date(2028,1,1))
        cal.key(SimpleNamespace(keysym='Home',state=4))
        rig.check(theme + ': Ctrl+Home returns to control-room today', cal.cursor == cal.today)
        cal.focus_changed(True)
        ring = cal.find_withtag('date_focus')
        rig.check(theme + ': keyboard cursor has visible focus geometry', len(ring) == 1 and cal.bbox(ring[0]) is not None)
        cal.focus_changed(False)
        rig.check(theme + ': leaving calendar removes its focus marker', not cal.find_withtag('date_focus'))
        rig.check(theme + ': unrelated keys propagate normally', cal.key(SimpleNamespace(keysym='a',state=0)) is None)
        cal.show([datetime.date(9999,12,31)])
        cal.key(SimpleNamespace(keysym='Right',state=0))
        cal.key(SimpleNamespace(keysym='Next',state=0))
        rig.check(theme + ': calendar upper bound stays valid', cal.cursor == datetime.date(9999,12,31))
        cal.show([datetime.date(1,1,1)])
        cal.key(SimpleNamespace(keysym='Left',state=0))
        rig.check(theme + ': calendar lower bound stays valid', cal.cursor == datetime.date(1,1,1))
        cal.destroy()

        notice = widgets.Notice(rig.root, rig.app.skin)
        # placed, not packed under the whole window: packed below the app it only got the space left over (139 px
        # once 6.15-6.22 added rows), which clipped a correctly sized notice and failed the bounds check (8 Nov)
        notice.place(x=0, y=0)
        calls = []
        for width in (260, 520, 980):
            notice.configure(width=rig.app.px(width))
            notice.say('check', 'The files could not be opened. The saved logs are still available for this shift. '
                       'Check the selected PDF viewer, then open the existing files.', ('Open all to print',lambda: calls.append('open')))
            # Wait for the layout to settle, not a fixed 90 ms: the notice sets its own height after painting and Tk
            # applies it on the next pass, so on a busy PC a fixed wait measured the old height (8 Nov: 139 vs 144).
            settle = time.time()
            settled = rig.wait(lambda: notice.winfo_width() == rig.app.px(width) and
                               notice.winfo_height() == int(float(notice.cget('height'))) and
                               notice.bbox('notice_action') is not None and notice.bbox('notice_action')[3] <= notice.winfo_height(), 3)
            rig.check('%s %d: notice layout settles (%.2f s)' % (theme, width, time.time() - settle), settled)
            words = notice.bbox('notice_text')
            button = notice.bbox('notice_action')
            no_overlap = words[2] <= button[0] or words[3] <= button[1] or button[2] <= words[0]
            rig.check('%s %d: full notice and action do not overlap' % (theme,width), no_overlap,(words,button))
            rig.check('%s %d: notice action remains inside its bounds' % (theme,width),
                      0 <= button[0] < button[2] <= notice.winfo_width() and button[3] <= notice.winfo_height(),
                      (button, notice.winfo_width(), notice.winfo_height(), 'asked for', notice.winfo_reqheight()))
        notice.button.press()
        rig.check(theme + ': responsive notice retains its action', calls == ['open'])
        notice.say()
        rig.pump(40)
        rig.check(theme + ': clearing a notice removes the action', notice.button is None and not notice.find_withtag('notice_action'))
        notice.destroy()
        rig.check(theme + ': windows remain invisible and nonactive',
                  quiet_state(rig.root)['alpha'] == 0 and not quiet_state(rig.root)['foreground'])
        rig.check(theme + ': no callback errors', not rig.errors, rig.errors)
    finally:
        total += rig.close()

print('\n%d of %d checks passed' % (sum(total),len(total)))
faulthandler.cancel_dump_traceback_later()
sys.exit(0 if all(total) else 1)
