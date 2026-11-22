"""Search, readable refresh and atomic bulk previews with synthetic data only; no Tk window."""
import ast
import copy
import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wa_ui

checks = []
def check(label, value):
    checks.append(bool(value))
    print(('PASS ' if value else 'FAIL ') + label)

rows = [dict(key='a', name="Éwan O’Neil", venue='North Hall', start_time='22:00', text='All okay for North Hall?', status='confirmed', status_label='Confirmed'),
        dict(key='b', name='Beta Guard', venue='South Hall', start_time='02:00', text='All okay for South Hall?', status='no_reply', status_label='Reply not read'),
        dict(key='c', name='Gamma Guard', venue='North Hall', start_time='21:00', text='All okay for North Hall?', status='review', status_label='Needs review'),
        dict(key='d', name='Delta Guard', venue='East Gate', start_time='20:00', text='All okay for East Gate?', status='trusted', status_label='Trusted')]
before = copy.deepcopy(rows)
check('accent and apostrophe variants find the same roster identity', wa_ui.queue_view(rows, query='ewan oneil') == [rows[0]])
check('venue words find all matching assignments in roster order', wa_ui.queue_view(rows, query='north hall') == [rows[0], rows[2]])
check('search combines with reply-review filter', wa_ui.queue_view(rows, 'review', 'north') == [rows[2]])
check('confirmed filter does not mislabel trusted/no-reply calls', wa_ui.queue_view(rows, 'confirmed') == [rows[0]])
check('waiting filter reflects unread reply evidence', wa_ui.queue_view(rows, 'waiting') == [rows[1]])
check('time can narrow an overnight assignment', wa_ui.queue_view(rows, query='02:00') == [rows[1]])
check('no match stays empty rather than selecting a hidden recipient', wa_ui.queue_view(rows, query='absent') == [])
check('filtering never changes row identities or source data', rows == before and wa_ui.queue_view(rows)[0] is rows[0])

class Text:
    def __init__(self): self.text='old'; self.selected=True; self.scroll=.6; self.writes=0
    def get(self, *args): return self.text
    def yview(self): return (self.scroll,1.)
    def configure(self, **kwargs): pass
    def delete(self, *args): self.text=''; self.selected=False; self.scroll=0.; self.writes+=1
    def insert(self, index, text): self.text=text
    def yview_moveto(self, position): self.scroll=position

widget=Text()
for _ in range(100): wa_ui.readonly_text(widget,'old')
check('repeated unchanged reports preserve selection and scroll', widget.selected and widget.scroll==.6 and widget.writes==0)
wa_ui.readonly_text(widget,'new')
check('changed evidence updates once and retains reading position', widget.text=='new' and widget.scroll==.6 and widget.writes==1)

with tempfile.TemporaryDirectory(prefix='ccl-preview-') as temp:
    store=wa_ui.Store(str(Path(temp)/'previews.json'))
    existing={'drafts':{'shift|a':{'text':'Keep my edit','reviewed':'review-token','unknown':7}},'future':{'keep':True}}
    store.write(existing)
    writes=[]; actual_write=store.write
    def counted(value): writes.append(copy.deepcopy(value)); return actual_write(value)
    store.write=counted
    data,saved,kept=store.previews('shift',rows)
    check('one bulk action writes exactly once', len(writes)==1 and saved==3 and kept==1)
    check('bulk previews preserve edited/reviewed drafts and unknown fields', data['drafts']['shift|a']==existing['drafts']['shift|a'] and data['future']==existing['future'])
    check('new previews are explicitly unreviewed', all(data['drafts']['shift|'+r['key']]['reviewed'] is None for r in rows[1:]))
    store.previews('shift',rows)
    check('repeating create leaves existing previews untouched without writing', len(writes)==1)
    saved_bytes=Path(store.path).read_bytes()
    with patch.object(wa_ui.os,'replace',side_effect=PermissionError('synthetic lock')):
        try: store.previews('other',rows)
        except OSError: pass
    check('failed bulk save preserves the original file byte for byte', Path(store.path).read_bytes()==saved_bytes)
    broken=copy.deepcopy(data); broken['drafts']['shift|c']='damaged'
    actual_write(broken); damaged=Path(store.path).read_bytes()
    try: store.previews('shift',[dict(rows[0],key='new'),rows[2]])
    except ValueError: pass
    check('malformed later record prevents the entire bulk write', Path(store.path).read_bytes()==damaged)
    actual_write({'drafts':{'shift|a':{'text':''}}})
    data,saved,kept=store.previews('shift',[rows[0]])
    check('an intentionally empty existing draft is preserved', data['drafts']['shift|a']['text']=='' and saved==0 and kept==1)

with tempfile.TemporaryDirectory(prefix='ccl-activity-') as temp:
    now=datetime.datetime(2026,11,8,20)
    log={'stem':'synthetic-shift','meta':{'date_iso':'2026-11-08','supervisor':'Lena Drayton','start_min':1080,'end_min':1800}}
    reader=SimpleNamespace(now=lambda:now)
    queries=[]; raw=[{'chat':'Synthetic Guard','text':'Yes'}]
    def events(*args): queries.append(args); return copy.deepcopy(raw)
    with patch.object(wa_ui.whatsapp,'_day_file',side_effect=lambda day:str(Path(temp)/(day.isoformat()+'.jsonl'))), \
         patch.object(wa_ui.whatsapp,'events_between',side_effect=events), \
         patch.object(wa_ui.wa_read,'activity_signals',side_effect=lambda value:value), \
         patch.object(wa_ui.time,'monotonic',return_value=10) as clock:
        first=wa_ui.BetaUI.read_activity(reader,log)
        for _ in range(30): wa_ui.BetaUI.read_activity(reader,log)
        check('frequent UI ticks reuse the activity snapshot instead of rereading history',len(queries)==1 and first==raw)
        clock.return_value=13
        wa_ui.BetaUI.read_activity(reader,log)
        check('unchanged event files do not trigger another history parse',len(queries)==1)
        Path(temp,'2026-11-08.jsonl').write_text('synthetic new activity')
        raw.append({'chat':'Synthetic Guard','text':'Sorry running late'})
        clock.return_value=16
        fresh=wa_ui.BetaUI.read_activity(reader,log)
        check('newly created or changed event file refreshes activity within the next check',len(queries)==2 and fresh==raw)
        wa_ui.BetaUI.read_activity(reader,dict(log,stem='different-shift'))
        check('changing the shown shift bypasses a previous activity throttle',len(queries)==3)
        check('missing or invalid roster context has no navigation activity',wa_ui.BetaUI.read_activity(reader,None)=={} and wa_ui.BetaUI.read_activity(reader,{})=={})
        past=copy.deepcopy(log);past['meta']['date_iso']='2026-10-01'
        check('ended shifts do not scan weeks of old event files',wa_ui.BetaUI.read_activity(reader,past)=={} and len(queries)==3)

status=dict(continuous=True,enabled=True,state='waiting',roster_total=7,read=1,total=1,
            coverage=[dict(last_attempt_at=100) for _ in range(7)])
summary_ui=SimpleNamespace(read_coordinator=SimpleNamespace(status=lambda:status),
                          log=lambda:{'calls':[{'heard':{'response':'Ok'}} for _ in range(5)]+[{},{}]})
summary=wa_ui.BetaUI.read_summary(summary_ui)
check('small update pass preserves whole-roster visit and separate confirmation counts',
      'Chats visited 7/7' in summary and 'latest pass 1/1' in summary and 'replies confirmed 5/7' in summary)
status['coverage'][-1]['last_attempt_at']=None
check('unvisited roster chat is not counted as checked', 'Chats visited 6/7' in wa_ui.BetaUI.read_summary(summary_ui))

# Execute the real receiver startup/watch code against fake services, without making a Tk window,
# opening a socket, reading private settings, or enqueueing a WhatsApp action.
app_tree = ast.parse((Path(__file__).resolve().parents[1] / 'app.pyw').read_text(encoding='utf-8'))
app_class = next(n for n in app_tree.body if isinstance(n, ast.ClassDef) and n.name == 'App')
app_init = next(n for n in app_class.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
startup_at = next(i for i,n in enumerate(app_init.body) if isinstance(n, ast.Assign)
                  and isinstance(n.targets[0], ast.Tuple)
                  and any(isinstance(t,ast.Attribute) and t.attr == 'wa_server' for t in n.targets[0].elts))
startup = app_init.body[startup_at:startup_at+3]
assert isinstance(startup[-1], ast.If) and 'whatsapp.start()' in ast.unparse(startup[-1])
watch = next(n for n in app_class.body if isinstance(n, ast.FunctionDef) and n.name == 'wa_watch')
service_state = dict(server=object(), authenticated=True, configured=True)
service_calls = []
def service_call(name, value):
    service_calls.append(name)
    return value
fake_whatsapp = SimpleNamespace(
    start=lambda:service_call('start',service_state['server']),
    ping=lambda:service_call('ping',service_state['authenticated']),
    purge=lambda:service_call('purge',None),
    settings=lambda:{'controllers':['Synthetic Controller'] if service_state['configured'] else []},
    link=lambda:{'port':12345}, reader_state=lambda:('on','connected'), last_beat=lambda:(0,{}))
callbacks=[]; notices=[]
fake_root=SimpleNamespace(after=lambda delay,callback:callbacks.append((delay,callback)))
receiver=SimpleNamespace(root=fake_root, wa_watch=lambda:None, gone=False, wa_logs=lambda:[], wa_says=lambda:True,
                         beta=SimpleNamespace(poll=lambda:None), say=lambda *args:notices.append(args))
namespace=dict(whatsapp=fake_whatsapp,os=SimpleNamespace(environ={}),
               time=SimpleNamespace(time=lambda:100.,strftime=lambda *args:'00:00',localtime=lambda at:None),
               WA_EVERY=15,self=receiver,root=fake_root)
exec(compile(ast.Module(body=startup+[watch],type_ignores=[]),'real-App-receiver-ownership','exec'),namespace)
panel=SimpleNamespace(app=receiver)
ready={'ready':True,'reason':'ready','reason_code':'ready','owner':'synthetic-owner'}
with patch.object(wa_ui.whatsapp,'bridge_status',return_value=ready) as bridge_status, \
     patch.object(wa_ui.whatsapp,'ping',side_effect=AssertionError('UI must not ping')), \
     patch.object(wa_ui.whatsapp,'enqueue_action',side_effect=AssertionError('Readiness must not enqueue')):
    check('own receiver startup needs no ownership ping and retains normal readiness',
          receiver.wa_server is service_state['server'] and not receiver.wa_receiver_elsewhere
          and service_calls.count('ping')==0 and wa_ui.BetaUI.bridge_state(panel)==ready)
    service_state['server']=None
    exec(compile(ast.Module(body=startup,type_ignores=[]),'real-App-second-window-startup','exec'),namespace)
    second=wa_ui.BetaUI.bridge_state(panel)
    check('authenticated other receiver is cached at startup and blocks actions truthfully',
          receiver.wa_receiver_elsewhere and second['ready'] is False
          and second['reason_code']=='receiver_elsewhere'
          and 'Another Check Call Logs window' in second['reason'] and 'Chrome' not in second['reason'])
    ping_count=service_calls.count('ping'); bridge_count=bridge_status.call_count
    for _ in range(40): wa_ui.BetaUI.bridge_state(panel)
    check('fast UI readiness polling uses the cached ownership without network or local queue calls',
          service_calls.count('ping')==ping_count and bridge_status.call_count==bridge_count)
    namespace['wa_watch'](receiver)
    check('existing watch authenticates a missing receiver only once per scheduled check',
          receiver.wa_receiver_elsewhere and service_calls.count('ping')==ping_count+1
          and callbacks[-1][0]==15000)
    service_state['authenticated']=False
    bridge_status.return_value={'ready':False,'reason':'No local reader','reason_code':'missing'}
    namespace['wa_watch'](receiver)
    check('loss of authenticated peer clears other-window advice and retains the port error',
          not receiver.wa_receiver_elsewhere and receiver.wa_said=='port'
          and wa_ui.BetaUI.bridge_state(panel)['reason_code']=='missing')
    service_state.update(server=object(),authenticated=True)
    bridge_status.return_value=ready
    ping_count=service_calls.count('ping')
    namespace['wa_watch'](receiver)
    check('normal watch takes over a released receiver and clears the cached peer flag without a ping',
          receiver.wa_server is service_state['server'] and not receiver.wa_receiver_elsewhere
          and service_calls.count('ping')==ping_count and wa_ui.BetaUI.bridge_state(panel)==ready)
    receiver.wa_receiver_elsewhere=True; service_state['configured']=False
    namespace['wa_watch'](receiver)
    check('disabling the configured reader clears stale ownership advice',not receiver.wa_receiver_elsewhere)
    check('existing fake or older app objects without ownership field retain normal bridge behavior',
          wa_ui.BetaUI.bridge_state(SimpleNamespace(app=SimpleNamespace()))==ready)

print('%d of %d checks passed' % (sum(checks),len(checks)))
raise SystemExit(0 if all(checks) else 1)
