"""Fault injection into save/undo controllers; no Tk, files, network or roster writes."""
import datetime
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
loader=importlib.machinery.SourceFileLoader('persistence_app', str(Path(__file__).resolve().parents[1]/'app.pyw'))
spec=importlib.util.spec_from_loader(loader.name,loader)
app=importlib.util.module_from_spec(spec)
loader.exec_module(app)
checks=[]
def check(name,value):
    checks.append(bool(value)); print(('PASS ' if value else 'FAIL ')+name)
def fail(*args, **kwargs):
    raise PermissionError('synthetic locked file')
def fake():
    f=SimpleNamespace(pack={'date':datetime.date(2026,10,23)},undone=[],redone=[],messages=[],packs=[],stamp_wait={})
    f.meta=lambda pack:{'supervisor':'Fake Controller'}
    f.say=lambda *args:f.messages.append(args[0])
    f.settle=lambda:True
    f.refresh=f.fill_extras=f.auto_run=lambda:None
    return f

original_snapshot,original_restore=app.calllog.snapshot,app.calllog.restore
try:
    f=fake(); called=[]; app.calllog.snapshot=fail
    try: app.App.change(f,'Edit',lambda:called.append(True))
    except OSError: pass
    check('unreadable snapshot prevents mutation and explains the failure', not called and bool(f.messages) and bool(getattr(f,'change_error',None)))
    f=fake(); app.calllog.snapshot=lambda stems:{}
    try: app.App.change(f,'Edit',fail)
    except OSError: pass
    check('failed save retains history and exposes a recoverable editor error', not f.undone and bool(f.messages) and bool(getattr(f,'change_error',None)))
    f=fake(); entry=('Edit',f.pack['date'],'Fake Controller',{'a':b'old'},{'a':b'new'})
    f.undone=[entry]; app.calllog.restore=fail
    try: app.App.step_back(f)
    except OSError: pass
    check('failed undo keeps the entry in undo and reports failure', f.undone==[entry] and not f.redone and bool(f.messages))
    f=fake(); f.redone=[entry]
    try: app.App.step_back(f,True)
    except OSError: pass
    check('failed redo keeps the entry in redo and reports failure', f.redone==[entry] and not f.undone and bool(f.messages))
finally:
    app.calllog.snapshot,app.calllog.restore=original_snapshot,original_restore

f = fake()
entry = ('Edit',f.pack['date'],'Fake Controller',{'a':b'old'},{'a':b'new'})
f.undone = [entry]
restored = []
with patch.object(app.calllog, 'restore', restored.append):
    app.App.step_back(f)
    check('successful undo moves its history only after restoring the original snapshot',
          restored == [entry[3]] and not f.undone and f.redone == [entry])
    app.App.step_back(f, True)
    check('successful redo restores the changed snapshot and makes it undoable again',
          restored == [entry[3],entry[4]] and f.undone == [entry] and not f.redone)


def bind(f, *names):
    for name in names:
        setattr(f, name, getattr(app.App, name).__get__(f))
    return f


# Green-history requests use fake services: a missing link must not consume an undo/redo.
f = bind(fake(), 'green_back')
green = ('green','synthetic calls',[('synthetic-row','A1','Alex Example','white')],'green')
entry = ('Green box',f.pack['date'],'Fake Controller',green,None)
f.undone = [entry]
decisions, requests = [], []
f.decide = lambda *args:decisions.append(args)
f.send_colour = lambda *args:requests.append(args)
f.the_link = lambda:(f.say('Missing test roster link','check') or None)
app.App.step_back(f)
check('missing roster link keeps Green undo available without reporting success',
      f.undone == [entry] and not f.redone and not decisions and not requests
      and f.messages == ['Missing test roster link'])
f.undone, f.redone, f.messages = [], [entry], []
app.App.step_back(f, True)
check('missing roster link keeps Green redo available without reporting success',
      f.redone == [entry] and not f.undone and not decisions and not requests
      and f.messages == ['Missing test roster link'])
f.undone, f.redone, f.messages = [entry], [], []
f.the_link = lambda:{'synthetic':True}
app.App.step_back(f)
check('available roster link preserves Green undo requests and moves the history once',
      not f.undone and f.redone == [entry]
      and decisions == [('synthetic calls','synthetic-row','white')]
      and requests == [('A1','Alex Example','white',app.rostercolour.GREEN)]
      and f.messages == ['Undone: green box'])
app.App.step_back(f, True)
check('available roster link preserves Green redo requests and makes the entry undoable again',
      f.undone == [entry] and not f.redone and len(decisions) == len(requests) == 2
      and decisions[-1] == ('synthetic calls','synthetic-row','green')
      and requests[-1] == ('A1','Alex Example','green',app.rostercolour.WHITE)
      and f.messages[-1] == 'Done again: green box')


class Value:
    def __init__(self, value):
        self.value = value

    def get(self, *args):
        return self.value


def handover_fake():
    f = fake()
    f.pack['shift'] = {'stem':'synthetic shift', 'entries':[], 'handover':'Saved note',
                       'supervisor':'Saved name', 'incoming':'Next name',
                       'meta':{'start_min':1200, 'end_min':1560}}
    f.handover, f.supervisor, f.incoming = Value('Unsaved note'), Value('Saved name'), Value('Next name')
    f.extras_job, f.extras_unsaved, f.filling = 'synthetic timer', False, False
    f.cancelled = []
    f.root = SimpleNamespace(after_cancel=f.cancelled.append)
    f.grids = {}
    return bind(f, 'change', 'keep_shift', 'save_extras', 'settle')


with patch.object(app.calllog, 'snapshot', lambda stems:{}), patch.object(app.calllog, 'save_shift', fail):
    f = handover_fake()
    saved = f.save_extras()
    check('failed delayed handover save retains typed text and a retry flag after cancelling its timer',
          saved is False and f.handover.value == 'Unsaved note' and f.pack['shift']['handover'] == 'Saved note'
          and f.extras_unsaved and f.extras_job is None and f.cancelled == ['synthetic timer'])
    check('settle retries a failed handover save even without a pending timer and blocks navigation',
          f.settle() is False and f.extras_unsaved and len(f.messages) == 2)

    # A cell save must return the failure to Grid.close, which keeps the editor open.
    call = {'no':1, 'name':'Alex Example', 'call_text':'20:00', 'response':'Ok',
            'default_response':'Ok', 'timed':True, 'edited':False}
    f = fake()
    f.pack['calls'] = {'stem':'synthetic calls', 'meta':{'supervisor':'Fake Controller'}, 'calls':[call]}
    f.call_for = lambda key:call
    changed = []
    f.response_changed = changed.append
    bind(f, 'change')
    with patch.object(app.calllog, 'save_edit', fail), patch.object(app.whatsapp, 'is_for', lambda who:False):
        reason = app.App.commit_call(f, 'synthetic-row', 'resp', 'Running late')
    check('failed response save returns an editor error without signalling Auto green or changing the row',
          bool(reason) and not changed and call['response'] == 'Ok' and not f.undone)

    f = handover_fake()
    entry = {'id':'e1', 'time':'20:00', 'area':'', 'text':''}
    f.pack['shift']['entries'] = [entry]
    f.fresh = 'e1'
    bind(f, 'shift_entry')
    reason = app.App.commit_shift(f, 'e1', 'text', 'Saved only after a successful write')
    check('failed new shift-line edit preserves its fresh marker and original saved content',
          bool(reason) and f.fresh == 'e1' and entry['text'] == '' and not f.undone)

    f = handover_fake()
    f.extras_job, f.extras_unsaved = None, True
    closed = []
    f.beta = SimpleNamespace(flush=lambda:True, close=lambda:(closed.append('beta') or True))
    f.root.destroy = lambda:closed.append('window')
    f.gone = False
    app.App.on_close(f)
    check('window close retains failed handover notes before closing WhatsApp controls or the app',
          not closed and not f.gone and f.handover.value == 'Unsaved note' and f.extras_unsaved)

f = fake()
f.pack['patrols'] = {'covered':[], 'rows':[{'id':'p1','start_text':'20:00','notes':'Saved note'}]}
bind(f, 'change', 'patrol_change', 'patrol_row')
with patch.object(app.calllog, 'snapshot', lambda stems:{}), patch.object(app.calllog, 'patrol_set', fail):
    reason = app.App.commit_patrol(f, 'p1', 'notes', 'Unsaved patrol note')
check('failed patrol note save returns the editor error without changing the saved row',
      bool(reason) and f.pack['patrols']['rows'][0]['notes'] == 'Saved note' and not f.undone)

# A successful retry must clear the failure marker only after the actual save succeeds.
f = handover_fake()
f.extras_job, f.extras_unsaved = None, True
saved = dict(f.pack['shift'])
def save_handover(log, entries, handover, supervisor, incoming):
    saved.update(entries=entries, handover=handover.strip(), supervisor=supervisor.strip(), incoming=incoming.strip())
f.remake = lambda pack:pack['shift'].update(saved)
f.write = lambda pack:[]
with patch.object(app.calllog, 'snapshot', lambda stems:{'synthetic':saved['handover']}), \
        patch.object(app.calllog, 'save_shift', save_handover):
    check('successful handover retry clears the failure flag and records one undoable save',
          f.settle() is True and not f.extras_unsaved and f.pack['shift']['handover'] == 'Unsaved note'
          and len(f.undone) == 1)

# Closing is a real caller of settle. A refused cell must not be destroyed as if it saved.
f = fake()
closed, modes = [], []
f.grids = {'calls':SimpleNamespace(close=lambda **kw:(modes.append(kw.get('move')) or False))}
f.beta = SimpleNamespace(flush=lambda:True, close=lambda:(closed.append('beta') or True))
f.root = SimpleNamespace(after_cancel=lambda job:None, destroy=lambda:closed.append('window'))
for name in ('poll_job','tick_job','title_job','extras_job','reread_job','watch_job','auto_job','script_job','aim_job'):
    setattr(f,name,None)
f.gone = False
bind(f, 'settle')
app.App.on_close(f)
check('window close keeps a refused editor open and does not tear down the app',
      modes == ['hold'] and not closed and not f.gone)

# A failed handover flush must not be bypassed by tick's independent follow-the-desk branch.
f = fake()
old_name, new_name = 'Fake Controller', 'Next Controller'
old_date = f.pack['date']
f.pack['shift'] = {'handover':'Saved note'}
f.packs = [f.pack]
f.view = object()
f.duty = lambda date:[]
f.desk_was = (old_name, old_date)
f.name, f.dates = old_name, [old_date]
f.touched_name = f.touched_date = False
f.day, f.gone = 0, False
f.extras_unsaved, f.extras_job = True, None
f.handover = Value('Unsaved note')
f.grids = {}
f.cal = SimpleNamespace(today=old_date)
f.watch_before = None
f.root = SimpleNamespace(after=lambda *args:'next tick')
flushes, rebuilt, filled = [], [], []
f.flush = lambda:(flushes.append(True) or False)
f.rebuild = lambda:rebuilt.append(f.name)
f.fill_extras = lambda:(filled.append(f.name), setattr(f.handover,'value','Other saved note'))
f.show_clock = f.shift_ends = f.refill = lambda:None
bind(f, 'typing', 'desk_handover', 'follow_the_desk', 'tick')
with patch.object(app.calllog, 'at_the_desk', lambda *args, **kwargs:(new_name,old_date)):
    app.App.tick(f)
check('minute tick cannot switch controllers or overwrite unsaved handover text after a failed save',
      f.name == old_name and f.dates == [old_date] and f.handover.value == 'Unsaved note'
      and not filled and not rebuilt and not f.messages and f.tick_job == 'next tick')
f.extras_job, f.extras_unsaved = 'pending handover timer', False
with patch.object(app.calllog, 'at_the_desk', lambda *args, **kwargs:(new_name,old_date)):
    app.App.tick(f)
check('minute tick also preserves handover typing before its delayed-save timer fires',
      f.name == old_name and f.handover.value == 'Unsaved note' and not filled and not rebuilt and not f.messages)

# Clearing a custom controller field is another route to changing the displayed shift.
f = fake()
f.name, f.touched_name = 'Custom Controller', True
f.other, f.pills = Value(''), {}
f.settle = lambda:False
switched = []
f.follow_the_desk = lambda:switched.append('follow')
f.select = lambda:switched.append('select')
app.App.other_typed(f)
check('clearing a custom controller waits for unsaved work before choosing another shift',
      not switched and f.touched_name and f.name == 'Custom Controller')

print('%d of %d checks passed'%(sum(checks),len(checks)))
raise SystemExit(0 if all(checks) else 1)
