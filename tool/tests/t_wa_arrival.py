"""Signed same-venue arrivals after an established reply. Pure synthetic events, no files/services/Tk."""
import datetime as dt
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import calllog as c
import whatsapp as wa

DAY=dt.date(2026,11,7)
CALL=dict(name="David Example",site="Example Hall",venue="Example Hall",start=21*60,shift="21:00-close")
LOG=dict(meta=dict(date_iso=DAY.isoformat()),calls=[CALL])
KEY=c.edit_key(CALL)
RULES={CALL["name"]:["🤙"]}
checks=[]
def check(label,ok):
    checks.append(bool(ok))
    print(("PASS " if ok else "FAIL ")+label)
def event(ours,text,clock,**extra):
    at=dt.datetime.combine(DAY,dt.time.fromisoformat(clock))
    result=dict(kind="bubble",chat=CALL["name"],id=clock+str(ours),group=False,ours=ours,
                direction="outgoing" if ours else "incoming",what="text",text=text,truncated=False,
                date=DAY.strftime("%d/%m/%Y"),shown=clock,seen=int(at.timestamp()*1000)+7000)
    result.update(extra)
    return result
QUESTION=event(True,"All okay for Example Hall?","20:01")
YES=event(False,"🤙","20:03")
def heard(*later,first=YES):
    return wa.heard(LOG,[QUESTION,first,*later],confirmation_emojis=RULES).get(KEY,{})

for text in ("On site Example Hall Dave Example", "on site example hall dave example",
             "ON SITE EXAMPLE HALL DAVID EXAMPLE", "Onsite Example Hall Dav Example",
             "I am on site at Example Hall Dave Example", "Arrived at Example Hall David Example",
             "Reached Example Hall Dave Example"):
    got=heard(event(False,text,"20:52"))
    check("a complete same-venue arrival with a self-signature keeps the existing yes: "+text,
          got.get("response")=="Ok" and not got.get("taken_back") and got.get("said")=="🤙"
          and got.get("reply_at")=="2026-11-07T20:03")
got=heard(event(False,"On site Example Hall Dave Example","20:52"))
check("Time of call remains the first20:03reply rather than later arrival",c.actual_reply_minute(got,DAY,dt.datetime.combine(DAY,dt.time(23)))==1203)

for text in ("On site Different Hall Dave Example", "On site Example Hall Mike Example",
             "On site Example Hall Dave Elsewhere", "On site Example Hall D Example",
             "On site Example Hall Dave", "On site Example Hall Dave Example but I cannot stay",
             "On site Example Hall Dave Example. Need to go home", "Not on site Example Hall Dave Example",
             "On site Example Hall Dave Example?", "On site Example Hall Dave Example لا أستطيع الحضور",
             "On site Example Hall Dave Example 10 minutes late", "On site Example Hall and Other Hall Dave Example"):
    got=heard(event(False,text,"20:52"))
    check("a signature cannot hide different context or a problem: "+text,
          got.get("response")!="Ok" and got.get("taken_back"))
for extra in (dict(direction="unknown"),dict(truncated=True),dict(what="voice"),dict(read_id="a"*32)):
    got=heard(event(False,"On site Example Hall Dave Example","20:52",**extra))
    check("arrival wording cannot bypass sender/content/provenance checks",got.get("response")!="Ok")

got=wa.heard(LOG,[QUESTION,event(False,"On site Example Hall Dave Example","20:52")],confirmation_emojis=RULES).get(KEY,{})
check("signed-arrival followup support does not change initial confirmation rules",got.get("response")!="Ok")
got=wa.heard(LOG,[event(False,"On site Example Hall Dave Example","20:52")],confirmation_emojis=RULES).get(KEY,{})
check("an unprompted signed arrival does not gain a new automatic confirmation rule",got.get("response")!="Ok")
got=heard(event(False,"On site Example Hall Dave Example","20:52"),event(False,"I cannot stay tonight","21:05"))
check("a later genuine cancellation still withdraws the yes and records its own clock",got.get("response")==""
      and got.get("taken_back") and got.get("reply_at")=="2026-11-07T21:05")
got=heard(event(False,"On site Example Hall Dave Example","20:52"),
          event(True,"All okay for Example Hall?","21:00"),event(False,"Yes","21:02"))
check("a new answered check-call question may select its own later reply time",got.get("response")=="Ok"
      and got.get("reply_at")=="2026-11-07T21:02")
check("the arrival helper never interprets another-day wording as arrival now",not wa._signed_arrival_after_yes(
      "On site Example Hall tomorrow Dave Example",CALL))
check("signature shortening requires a full surname and a tight first-name stem",not wa._signed_arrival_after_yes(
      "On site Example Hall Dan Example",CALL) and not wa._signed_arrival_after_yes("On site Example Hall Davee Example",CALL))

print("%d of %d checks passed"%(sum(checks),len(checks)))
raise SystemExit(0 if all(checks) else 1)
