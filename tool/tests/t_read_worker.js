// Automatic reading transport, entirely in a fake extension/receiver. Never opens a real browser.
"use strict";
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const {webcrypto, createHmac} = require("node:crypto");
const source = fs.readFileSync(path.join(__dirname, "../../reader/worker.js"), "utf8");
let passed = 0;
function check(label, result) { if (!result) throw new Error(label); passed++; }
async function until(test, label) {
  for (let n=0; n<300; n++) { if (test()) return; await new Promise(resolve => setTimeout(resolve, 2)); }
  throw new Error("Timed out: " + label);
}
const owner = "3:synthetic-document-1";
function event(index=0, direction="incoming") {
  const at = new Date(Date.now() - 120000 - index*60000);
  const pad = n => String(n).padStart(2,"0");
  return {kind:"bubble",chat:"Alex Example",direction,ours:direction==="outgoing",text:direction==="outgoing"?"All okay for Example?":"Yes",
    date:pad(at.getDate())+"/"+pad(at.getMonth()+1)+"/"+at.getFullYear(), shown:pad(at.getHours())+":"+pad(at.getMinutes()),
    seen:Date.now(),what:"text",group:false,id:"synthetic-"+index,i:index};
}
function summary(events, overrides={}) {
  const times=events.map(e=>{ const [d,m,y]=e.date.split("/").map(Number), [h,minute]=e.shown.split(":").map(Number); return +new Date(y,m-1,d,h,minute); });
  return Object.assign({messages:events.length,incoming:events.filter(e=>e.direction==="incoming").length,
    outgoing:events.filter(e=>e.direction==="outgoing").length,complete:false,
    oldest:times.length?Math.min(...times):null,newest:times.length?Math.max(...times):null,
    reason:events.length?"history_unavailable":"no_messages"},overrides);
}
function command(overrides={}) {
  return Object.assign({id:"d".repeat(32),owner,kind:"read",chat:"Alex Example",text:null,phone:"15555550199",
    since:Date.now()-15*3600000,created:Date.now(),expires:Date.now()+90000},overrides);
}
function fixture(persist={queue:[],draftActions:{}}) {
  let listener, alarm, action, dispatchBehavior, sequence=0;
  const tasks=new Map(), order=[], posts=[], acks=[], grants=[], captures=[], dispatch=[];
  const options={proof:true,post:true,kept:null,denyReadAfter:Infinity};
  const copy=value=>JSON.parse(JSON.stringify(value));
  const sender={id:"synthetic-extension",tab:{id:3},documentId:"synthetic-document-1",frameId:0,url:"https://web.whatsapp.com/"};
  const send=(msg,who=sender)=>new Promise(resolve=>{ const hold=listener(msg,who,resolve); if(!hold && msg.type!=="drafts_ready") resolve(undefined); });
  const chrome={runtime:{id:sender.id,onMessage:{addListener:fn=>{listener=fn;}}},
    storage:{local:{get:async defaults=>Object.assign(copy(defaults),copy(persist)),set:async value=>{Object.assign(persist,copy(value));order.push("saved");}}},
    tabs:{sendMessage:async(tab,msg,opts)=>{ dispatch.push({tab,msg:copy(msg),opts}); check("attempt persisted before read dispatch",Object.hasOwn(persist.draftActions,msg.action.id));
      if(msg.action.kind==="read") check("chat quarantine persisted before read dispatch",persist.readChats[msg.action.chat]?.id===msg.action.id);
      return dispatchBehavior(msg.action,send); }},
    alarms:{create:()=>{},onAlarm:{addListener:fn=>{alarm=fn;}}}};
  const context=vm.createContext({importScripts:()=>{},READER:{port:8765,token:"synthetic-read-secret"},Date,crypto:webcrypto,TextEncoder,AbortController,chrome,
    setTimeout:(fn,ms)=>{const id=++sequence;tasks.set(id,{fn,ms});return id;},clearTimeout:id=>tasks.delete(id),
    fetch:async(url,opts)=>{
      check("read worker stays on local receiver",url.startsWith("http://127.0.0.1:8765/"));
      if(url.includes("/wa/hello?")) return {ok:true,status:200,json:async()=>({proof:options.proof?createHmac("sha256","synthetic-read-secret").update(new URL(url).searchParams.get("nonce")).digest("hex"):"invalid"})};
      const body=JSON.parse(opts.body);
      if(url.endsWith("/wa/authorize")) {
        grants.push(body); const ok=body.step!=="read" || grants.filter(x=>x.step==="read").length<=options.denyReadAfter;
        return {ok,status:ok?200:409,json:async()=>({...body,ok,expires:Date.now()+2000})};
      }
      if(url.endsWith("/wa/action")) {acks.push(body);order.push("ack:"+body.status);return {ok:true,status:200,json:async()=>({ok:true})};}
      posts.push(body); const isCapture=!Object.hasOwn(body,"client");
      if(isCapture) {captures.push(body);order.push("capture");if(!options.post) throw new Error("receiver unavailable");}
      const next=isCapture?null:action; if(!isCapture) action=null;
      return {ok:true,status:200,json:async()=>({ok:true,kept:isCapture&&options.kept!==null?options.kept:body.events.length,action:next})};
    }});
  vm.runInContext(source,context);
  const api={persist,options,tasks,order,posts,acks,grants,captures,dispatch,sender,send,
    ready:()=>send({type:"drafts_ready",capability:"drafts-v4"}),
    beat:(who=sender)=>listener({type:"beat",beat:{pane:true,rows:3,paused:false,useHere:false}},who),
    poll:()=>alarm({name:"flush"}),
    run:(c,behavior)=>{action=c;dispatchBehavior=behavior;api.beat();},
    grant:(c,step="read",who=sender)=>send({type:"draft_authorize",id:c.id,owner:c.owner,step},who),
    capture:(c,events,reading,who=sender)=>send({type:"read_capture",id:c.id,owner:c.owner,events,reading},who)};
  return api;
}
async function execute(w, events, reading, behavior) {
  await w.ready();
  const c=command();
  w.run(c,behavior||(async()=>{
    await w.grant(c,"open"); await w.grant(c);
    const saved=await w.capture(c,events,reading);
    return {id:c.id,owner,status:saved.ok?"read":"unknown",reading};
  }));
  await until(()=>w.acks.length,"action ACK"); return c;
}
(async()=>{
  let w=fixture(), events=[event(0),event(1,"outgoing")], reading=summary(events);
  const c=await execute(w,events,reading);
  check("success follows captured events being accepted",w.acks[0].status==="read"&&w.order.indexOf("capture")<w.order.indexOf("ack:read"));
  check("read ACK has counts but no message bodies",w.acks[0].reading.messages===2&&!JSON.stringify(w.acks[0]).includes("All okay for Example"));
  check("accepted read events leave durable queue",w.persist.queue.length===0);
  check("ordinary observations have distinct uncommitted provenance",w.persist.readChats[c.chat].passiveId!==c.id&&/^[a-f0-9]{32}$/.test(w.persist.readChats[c.chat].passiveId));
  check("read never requests draft grant",!w.grants.some(g=>g.step==="draft"));
  check("read has extended bounded timeout cleared on completion",![...w.tasks.values()].some(t=>t.ms===28000));
  check("idle read capture cannot access receiver",!(await w.capture(c,events,reading)).ok&&w.captures.length===1);
  w.run(c,()=>{throw new Error("must not replay");}); await until(()=>w.acks.length===2,"duplicate ACK");
  check("read commands are consumed once",w.dispatch.length===1&&w.acks[1].status==="unknown");
  // 8 Nov: the unread count from opening the chat travels with a read result - and only a sane number does
  w=fixture(); const cu=command();
  await w.ready(); w.run(cu,async()=>{await w.grant(cu,"open"); await w.grant(cu); const saved=await w.capture(cu,events,reading);
    return {id:cu.id,owner,status:saved.ok?"read":"unknown",reading,unread_before:4};});
  await until(()=>w.acks.length,"unread ACK");
  check("a read's unread count reaches the app",w.acks[0].unread_before===4);
  w=fixture(); const cx=command();
  await w.ready(); w.run(cx,async()=>{await w.grant(cx,"open"); await w.grant(cx); const saved=await w.capture(cx,events,reading);
    return {id:cx.id,owner,status:saved.ok?"read":"unknown",reading,unread_before:"lots"};});
  await until(()=>w.acks.length,"bad unread ACK");
  check("...a malformed count is dropped",!("unread_before" in w.acks[0])&&w.acks[0].status==="read");
  for (const status of ["user_active", "blocked", "unknown", "expired"]) {
    w=fixture(); const interrupted=command(); await w.ready();
    w.run(interrupted,async()=>{await w.grant(interrupted,"open"); return {id:interrupted.id,owner,status,unread_before:7};});
    await until(()=>w.acks.length,"interrupted unread ACK");
    check("opened read retains its original unread count after "+status,w.acks[0].status===status&&w.acks[0].unread_before===7);
  }
  for (const options of [{kind:"read",grant:false,count:7},{kind:"open",grant:true,count:7},
    {kind:"draft",grant:true,count:7},{kind:"send",grant:true,count:7},{kind:"read",grant:true,count:1000}]) {
    w=fixture(); const other=command({kind:options.kind,text:["draft","send"].includes(options.kind)?"Synthetic text":null}); await w.ready();
    w.run(other,async()=>{if(options.grant) await w.grant(other,"open"); return {id:other.id,owner,status:"user_active",unread_before:options.count};});
    await until(()=>w.acks.length,"invalid unread ACK");
    check("unread metadata requires read kind, open authorization and bounded count: "+JSON.stringify(options),!("unread_before" in w.acks[0]));
  }
  w=fixture(); w.options.post=false; await execute(w,events,reading);
  check("receiver failure cannot claim read",w.acks[0].status==="unknown"&&w.persist.queue.length===2);
  w=fixture(); w.options.kept=1; await execute(w,events,reading);
  check("partial accepted count cannot claim complete delivery",w.acks[0].status==="unknown"&&w.persist.queue.length===2);
  w=fixture(); await execute(w,events,reading,async action=>({id:action.id,owner,status:"read",reading}));
  check("fabricated read result without delivery stays unknown",w.acks[0].status==="unknown"&&w.captures.length===0);
  w=fixture(); await execute(w,events,reading,async action=>{
    await w.grant(action,"open");await w.grant(action);await w.capture(action,events,reading);
    return {id:action.id,owner,status:"read",reading:{...reading,reason:"limit"}};
  });
  check("final summary must match the delivered capture",w.acks[0].status==="unknown");
  for (const mutate of [
    (e,r)=>{e[0].chat="Other Example";},(e,r)=>{e[0].group=true;},(e,r)=>{r.messages=3;},
    (e,r)=>{r.incoming=2;},(e,r)=>{r.reason="private arbitrary words";},(e,r)=>{r.complete=true;},
    (e,r)=>{e.push(e[0]);r.messages++;r.incoming++;},(e,r)=>{e[0].date="Today";},
    (e,r)=>{e[0].ours=true;},(e,r)=>{e[0].direction="unknown";r.incoming--;},
    (e,r)=>{e[0].truncated="true";},
    (e,r)=>{e[0].view_id="invalid";},
    (e,r)=>{e[0].view_id="e".repeat(32);e[0].i=-1;},
    (e,r)=>{e[0].view_id="e".repeat(32);e[0].i=1.5;},
    (e,r)=>{e[0].view_id="e".repeat(32);delete e[0].id;}
  ]) {
    w=fixture();const e=events.map(x=>({...x})),r={...reading};mutate(e,r);await execute(w,e,r);
    check("malformed/cross-recipient capture is refused",w.acks[0].status==="unknown"&&w.captures.length===0&&w.persist.queue.length===0);
  }
  const clipped = [{...event(), truncated:true}, event(901,"outgoing")];
  w=fixture(); await execute(w,clipped,summary(clipped));
  check("truncated reply is preserved only as incomplete evidence",w.acks[0].status==="read"&&w.acks[0].reading.complete===false&&w.captures[0].events[0].truncated===true);
  w=fixture(); await execute(w,clipped,summary(clipped,{complete:true,reason:"since_reached"}));
  check("truncated current reply cannot claim a complete history",w.acks[0].status==="unknown"&&w.captures.length===0);
  const ordered = [{...event(0,"outgoing"),view_id:"e".repeat(32),i:5},
    {...event(),id:"synthetic-reply",view_id:"e".repeat(32),i:6}, event(901,"outgoing")];
  w=fixture(); await execute(w,ordered,summary(ordered,{complete:true,reason:"since_reached"}));
  check("complete capture preserves same-view order proof",w.acks[0].status==="read"&&w.captures[0].events[0].view_id===w.captures[0].events[1].view_id&&w.captures[0].events[0].i===5&&w.captures[0].events[1].i===6);
  w=fixture();w.options.denyReadAfter=1;await execute(w,events,reading);
  check("cancellation before posting leaves data queued, never success",w.acks[0].status==="unknown"&&w.captures.length===0&&w.persist.queue.length===2);
  w=fixture();await execute(w,events,reading,async action=>{
    await w.grant(action,"open");await w.grant(action);
    check("read action cannot mint draft permission",!(await w.grant(action,"draft")).ok);
    const wrong={...w.sender,documentId:"other-document-999"};
    check("other document cannot submit active capture",!(await w.capture(action,events,reading,wrong)).ok);
    return {id:action.id,owner,status:"blocked"};
  });
  events=Array.from({length:601},(_,n)=>event(n));reading=summary(events);
  w=fixture();await execute(w,events,reading);
  check("large capture is accepted in bounded batches",w.captures.map(p=>p.events.length).join(",")==="300,300,1"&&w.acks[0].status==="read");
  w=fixture();w.options.denyReadAfter=2;await execute(w,events,reading);
  check("cancellation between batches stops remaining delivery",w.captures.length===1&&w.persist.queue.length===301&&w.acks[0].status==="unknown");
  w=fixture();await execute(w,[],summary([]));
  check("empty chat is reported as incomplete with zero messages",w.acks[0].status==="read"&&w.acks[0].reading.complete===false&&w.acks[0].reading.messages===0);
  for (const what of ["voice","media","deleted"]) {
    const media=[{...event(),what,text:""}];w=fixture();await execute(w,media,summary(media));
    check("dated incoming "+what+" is delivered for manual review",w.acks[0].status==="read"&&w.captures[0].events[0].what===what);
  }
  events=[event()];reading=summary(events);w=fixture();
  await execute(w,events,{...reading,oldest:reading.oldest-60000});
  check("capture bounds cannot invent older context",w.acks[0].status==="unknown"&&w.captures.length===0);
  w=fixture();await execute(w,events,reading,async action=>{
    await w.grant(action,"open");await w.grant(action);w.options.proof=false;
    const saved=await w.capture(action,events,reading);
    check("unproved receiver cannot get capture data",!saved.ok&&w.captures.length===0&&w.persist.queue.length===1);
    w.options.proof=true;return {id:action.id,owner,status:"unknown"};
  });
  const first=event();events=[first,{...first,id:"different-message-same-minute"}];w=fixture();
  await execute(w,events,summary(events));
  check("distinct same-minute message IDs are both delivered",w.acks[0].status==="read"&&w.captures[0].events.length===2);
  w=fixture();await w.ready();
  let beat=await w.send({type:"beat",awaited:true,beat:{pane:true,rows:3,paused:false,useHere:false}});
  check("awaited cleanup heartbeat confirms receiver acceptance",beat.ok&&w.posts.length===1);
  w.options.proof=false;
  beat=await w.send({type:"beat",awaited:true,beat:{pane:true,rows:3,paused:false,useHere:false}});
  check("failed cleanup heartbeat never reports accepted readiness",beat.ok===false&&w.posts.length===1);
  w=fixture();await execute(w,[],summary([]));
  const saved=w.persist;
  check("zero-event interrupted read keeps durable chat ownership",saved.readChats["Alex Example"].id===c.id);
  check("next-chat polling is short and bounded",[...w.tasks.values()].filter(t=>[800,2000,5000].includes(t.ms)).length===3);
  // Recreate the whole service worker, with no active action or in-memory map.
  w=fixture(saved);await w.ready();
  await w.send({type:"events",events:[{...event(),read_id:"a".repeat(32)}]});
  await until(()=>w.posts.some(p=>p.events.length),"restart passive delivery");
  check("passive reply after worker restart retains incomplete-read quarantine",w.posts.find(p=>p.events.length).events[0].read_id===saved.readChats[c.chat].passiveId);
  w=fixture();await w.ready();
  await w.send({type:"events",events:[{...event(),read_id:"a".repeat(32)}]});
  await until(()=>w.posts.some(p=>p.events.length),"unowned passive delivery");
  check("page cannot invent approved read provenance",!Object.hasOwn(w.posts.find(p=>p.events.length).events[0],"read_id"));
  w=fixture({queue:[{...event(),read_id:"b".repeat(32)}],draftActions:{},readChats:{"Alex Example":{id:c.id}}});await w.ready();w.poll();
  await until(()=>w.posts.length,"old queued provenance");
  check("old incomplete queue entries cannot borrow a newer read receipt",w.posts[0].events[0].read_id==="b".repeat(32));
  w=fixture({queue:[event()],draftActions:{},readChats:{"Alex Example":{id:c.id}}});await w.ready();w.poll();
  await until(()=>w.posts.length,"old untagged queue");
  check("delayed untagged queue data becomes quarantined for owned chat",w.posts[0].events[0].read_id===w.persist.readChats[c.chat].passiveId&&w.posts[0].events[0].read_id!==c.id);
  w=fixture({queue:[event()],draftActions:{},readChats:{"Alex Example":{id:c.id,passiveId:"b".repeat(32),since:1,started:Date.now()-21*3600000}}});await w.ready();w.poll();
  await until(()=>w.posts.length,"post after an old hold");
  check("a hold from a previous shift has ended: an ordinary sighting is ordinary again (8 Nov review)",!w.posts[0].events[0].read_id&&!w.persist.readChats["Alex Example"]);
  w=fixture({queue:[event()],draftActions:{},readChats:{"Alex Example":{id:c.id,passiveId:"b".repeat(32),since:1,started:Date.now()-3600000}}});await w.ready();w.poll();
  await until(()=>w.posts.length,"post during a fresh hold");
  check("...a hold from this shift still applies",w.posts[0].events[0].read_id==="b".repeat(32));
  w=fixture();events=[{...event(),read_id:"a".repeat(32)}];await execute(w,events,summary(events));
  check("capture provenance comes from active worker action",w.captures[0].events[0].read_id===c.id);
  w=fixture();events=[event()];reading=summary(events);
  await execute(w,events,reading,async action=>{
    await w.grant(action,"open");await w.grant(action);await w.capture(action,events,reading);
    await w.send({type:"events",events:[{...event(),id:"arrived-after-snapshot",text:"No"}]});
    await until(()=>w.posts.some(p=>p.events.some(e=>e.id==="arrived-after-snapshot")),"passive event before ACK");
    return {id:action.id,owner,status:"read",reading};
  });
  const late=w.posts.flatMap(p=>p.events).find(e=>e.id==="arrived-after-snapshot");
  check("passive message between snapshot and ACK cannot borrow that snapshot receipt",late.read_id!==c.id&&late.read_id===w.persist.readChats[c.chat].passiveId&&w.acks[0].status==="read");
  const sticker={...event(),what:"media",text:"",media_type:"sticker"};
  events=[sticker,event(901,"outgoing")];w=fixture();
  await execute(w,events,summary(events,{complete:true,reason:"since_reached"}));
  check("typed sticker is delivered as one dated media message",w.acks[0].status==="read"&&w.captures[0].events[0].media_type==="sticker"&&w.acks[0].reading.messages===2);
  for (const mutation of [{what:"text",text:"Sticker"},{what:"voice"},{what:"deleted"},
    {media_type:"photo"},{media_type:"Sticker"},{media_type:null},{media_type:true},{kind:"line"}]) {
    events=[{...sticker,...mutation}];w=fixture();await execute(w,events,summary(events));
    check("unknown or contradictory media subtype rejects entire capture",w.acks[0].status==="unknown"&&w.captures.length===0);
  }
  const legacySticker={...sticker,text:"Sticker"};delete legacySticker.media_type;
  const typedSticker={...legacySticker,media_type:"sticker"};
  w=fixture({queue:[legacySticker,...Array.from({length:299},(_,n)=>event(n+1)),typedSticker],draftActions:{}});
  await w.ready();w.poll();await until(()=>w.posts.length&&w.persist.queue.length<301,"first media-subtype batch");
  check("typed sticker survives an earlier legacy media batch ACK",w.posts[0].events.length===300&&w.persist.queue.length===1&&w.persist.queue[0].media_type==="sticker");
  w.poll();await until(()=>w.posts.length===2&&w.persist.queue.length===0,"second media-subtype batch");
  check("typed sticker proof is delivered in its own remaining batch",w.posts[1].events.length===1&&w.posts[1].events[0].media_type==="sticker");
  // Attached reactions are snapshots on a dated parent, not extra timed messages.
  const reaction = (parent, actor="contact", emoji="👍") => ({parent_id:parent.id,emoji,actor,
    actor_name:actor==="contact"?parent.chat:"You",at:null});
  const parent = event(0,"outgoing"), oldParent = event(901,"outgoing");
  for (const snapshot of [
    {complete:true,items:[reaction(parent)]},
    {complete:true,items:[reaction(parent,"self")]},
    {complete:true,items:[reaction(parent),reaction(parent,"self","❤️")]},
    {complete:true,items:[]}
  ]) {
    events=[{...parent,reactions:snapshot},oldParent];w=fixture();
    await execute(w,events,summary(events,{complete:true,reason:"since_reached"}));
    const saved=w.captures[0]?.events[0];
    check("valid reaction snapshot is delivered under the parent receipt",w.acks[0].status==="read"&&saved?.read_id===c.id&&JSON.stringify(saved.reactions)===JSON.stringify(snapshot));
    check("reaction adds neither message count nor a fabricated clock",w.acks[0].reading.messages===2&&w.acks[0].reading.incoming===0&&saved.direction==="outgoing"&&saved.shown===parent.shown&&saved.reactions.items.every(item=>item.at===null));
  }
  events=[{...parent,reactions:{complete:false,items:[reaction(parent)]}},oldParent];
  w=fixture();await execute(w,events,summary(events));
  check("incomplete reaction identity remains explicitly incomplete evidence",w.acks[0].status==="read"&&w.captures[0].events[0].reactions.complete===false);
  w=fixture();await execute(w,events,summary(events,{complete:true,reason:"since_reached"}));
  check("incomplete current reaction cannot prove complete history",w.acks[0].status==="unknown"&&w.captures.length===0);
  events=[parent,{...oldParent,reactions:{complete:false,items:[]}}];w=fixture();
  await execute(w,events,summary(events,{complete:true,reason:"since_reached"}));
  check("reaction uncertainty before requested context does not invent a recent gap",w.acks[0].status==="read");
  for (const [label,mutate] of [
    ["null snapshot",e=>{e.reactions=null;}],
    ["array snapshot",e=>{e.reactions=[];}],
    ["extra snapshot key",e=>{e.reactions.extra=true;}],
    ["missing completeness",e=>{delete e.reactions.complete;}],
    ["nonboolean completeness",e=>{e.reactions.complete=1;}],
    ["nonarray items",e=>{e.reactions.items={};}],
    ["too many actors",e=>{e.reactions.items.push(reaction(e,"self"),reaction(e));}],
    ["duplicate actor",e=>{e.reactions.items.push(reaction(e));}],
    ["unknown actor",e=>{e.reactions.items[0].actor="unknown";}],
    ["wrong contact",e=>{e.reactions.items[0].actor_name="Other Example";}],
    ["normalized name is not exact",e=>{e.reactions.items[0].actor_name=e.chat.toLowerCase();}],
    ["self not identified as You",e=>{e.reactions.items[0].actor="self";}],
    ["wrong parent",e=>{e.reactions.items[0].parent_id="other-parent";}],
    ["invented timestamp",e=>{e.reactions.items[0].at=Date.now();}],
    ["missing timestamp",e=>{delete e.reactions.items[0].at;}],
    ["extra item key",e=>{e.reactions.items[0].proof="untrusted";}],
    ["empty emoji",e=>{e.reactions.items[0].emoji="";}],
    ["overlong emoji",e=>{e.reactions.items[0].emoji="👍".repeat(9);}],
    ["emoji whitespace",e=>{e.reactions.items[0].emoji=" 👍";}],
    ["emoji controls",e=>{e.reactions.items[0].emoji="👍\n";}],
    ["unpaired unicode",e=>{e.reactions.items[0].emoji="\ud800";}],
    ["missing parent id",e=>{delete e.id;}],
    ["empty parent id with removal",e=>{e.id="";e.reactions.items=[];}],
    ["oversized parent id",e=>{e.id="x".repeat(201);e.reactions.items[0].parent_id=e.id;}],
    ["group parent",e=>{e.group=true;}],
    ["line parent",e=>{e.kind="line";}]
  ]) {
    const e={...parent,reactions:{complete:true,items:[reaction(parent)]}};mutate(e);
    events=[e];w=fixture();await execute(w,events,summary(events));
    check("reaction capture rejects "+label,w.acks[0].status==="unknown"&&w.captures.length===0&&w.persist.queue.length===0);
  }
  for (const duplicate of [parent,{...parent,reactions:{complete:true,items:[reaction(parent,"self")]}}]) {
    events=[{...parent,reactions:{complete:true,items:[reaction(parent)]}},duplicate];w=fixture();
    await execute(w,events,summary(events));
    check("one capture cannot contain conflicting copies of a reacted parent",w.acks[0].status==="unknown"&&w.captures.length===0);
  }
  // Two states observed in the same millisecond may straddle a 300-event batch.
  // A successful earlier batch must not remove the later unsent removal/change.
  for (const later of [undefined,{complete:true,items:[]},{complete:false,items:[]},
    {complete:true,items:[reaction(parent,"contact","👎")]},
    {complete:true,items:[reaction(parent,"self")]}]) {
    const before={...parent,reactions:{complete:true,items:[reaction(parent)]}};
    const after={...parent,...(later===undefined?{}:{reactions:later})};
    const queue=[before,...Array.from({length:299},(_,n)=>event(n+1)),after];
    w=fixture({queue,draftActions:{}});await w.ready();w.poll();
    await until(()=>w.posts.length&&w.persist.queue.length<301,"first reaction-state batch");
    check("changed or missing reaction state survives earlier batch ACK",w.posts[0].events.length===300&&w.persist.queue.length===1&&JSON.stringify(w.persist.queue[0])===JSON.stringify(after));
    w.poll();await until(()=>w.posts.length===2&&w.persist.queue.length===0,"second reaction-state batch");
    check("remaining reaction state is delivered without replaying first batch",w.posts[1].events.length===1&&JSON.stringify(w.posts[1].events[0])===JSON.stringify(after));
  }
  console.log(`${passed} of ${passed} checks passed`);
})().catch(error=>{console.error(error);process.exitCode=1;});
