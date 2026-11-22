"""Pure controller regressions: fake bridge/widgets, no Tk window, files or network."""
import datetime
from pathlib import Path
import sys
import time
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wa_ui import BetaUI

checks = []
def check(name, value):
    checks.append(bool(value))
    print(('PASS ' if value else 'FAIL ') + name)

def ui(self_test=False):
    b = object.__new__(BetaUI)
    b.sending_on, b.bridge_job = True, None
    b.app = SimpleNamespace(gone=False)
    b.now = lambda: datetime.datetime(2026, 10, 23, 21, 40)
    b.bridge_pending = dict(id='fake-send', kind='send', self_test=self_test, attempt={},
                            scope='fake-shift', key='guard', signature='reviewed', name='Fake Guard',
                            started=time.monotonic())
    b.messages, b.cancelled, b.saved = [], [], []
    b.inform = lambda words: b.messages.append(words)
    b.problem = lambda words: b.messages.append(words)
    b.save = lambda *args: b.saved.append(args) or True
    b.save_self_result = lambda *args: True
    b.show = lambda: True
    b.show_selected = b.refresh_banner = b.update_self_test = lambda: None
    b.cancel_action = lambda ident: b.cancelled.append(ident) or dict(status='unknown', detail='The message may already have been sent. Check the chat.')
    return b

b = ui(True)
try:
    b.check_bridge({'status':'sent'})
    check('self-send completion shows successful result without callback exception',
          b.bridge_pending is None and 'sent to your own chat' in b.self_test_words)
except Exception as exc:
    check('self-send completion shows successful result without callback exception', False)
    print(type(exc).__name__)

b = ui()
b.toggle_sending()
check('switching Sending off revokes pending send', not b.sending_on and b.cancelled == ['fake-send'] and b.bridge_pending is None)
check('switching off retains the possibly-sent warning', bool(b.messages) and 'may already have been sent' in b.messages[-1])

b = ui()
b.bridge_pending['kind'] = 'draft'
b.toggle_sending()
check('Sending off leaves an independent unsent draft request alone', not b.cancelled and b.bridge_pending['kind'] == 'draft')

b = ui(True)
b.disable_sending()
check('loss of shift eligibility revokes an in-bullseye self send', not b.sending_on and b.bridge_pending is None and b.cancelled == ['fake-send'])

b = ui()
b.current_live = lambda: False
b.draft_eligible = lambda: True
b.selected_row = lambda: dict(key='guard')
b.flush = b.reload_data = lambda: True
b.editor = SimpleNamespace(get=lambda *args: 'Reviewed text')
b.signature = lambda *args, **kw: 'reviewed'
b.start_bridge = lambda *args, **kw: b.messages.append('UNEXPECTED ENQUEUE')
b.send_reviewed()
check('upcoming-shift draft eligibility cannot authorize guard sending', 'UNEXPECTED ENQUEUE' not in b.messages and 'before sending' in b.messages[-1])

print('%d of %d checks passed' % (sum(checks), len(checks)))
raise SystemExit(0 if all(checks) else 1)
