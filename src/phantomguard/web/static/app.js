"use strict";
const $=id=>document.getElementById(id);
let token=null, job=null, result=null, cursor=0, playing=false, timer=null, catalog=null, generation=0;
const text=(id,value)=>{$(id).textContent=value;};
async function api(path,options={}){
  const headers={...(token?{Authorization:`Bearer ${token}`}:{})};
  if(options.body){headers["Content-Type"]="application/json";options.body=JSON.stringify(options.body);}
  const response=await fetch(path,{...options,headers});
  const body=await response.json();
  if(!response.ok)throw new Error(body.error||body.errors?.join("; ")||`HTTP ${response.status}`);
  return body;
}
function updateOptions(){
  const attack=$("attack").value;
  $("level").disabled=!attack;
  for(const option of $("level").options){
    const choice=catalog?.attacks.find(c=>c.attack===attack&&c.level===option.value);
    option.disabled=Boolean(choice&&!choice.supported);
    option.textContent=option.value+(option.disabled?" · unsupported":"");
    option.title=choice?.reason||"";
  }
  if(attack==="T3"&&!["A3","A4"].includes($("level").value))$("level").value="A3";
  $("motion").disabled=["T3","T4"].includes(attack)||!attack;
  if(["T3","T4"].includes(attack))$("motion").value="moving";
  $("variant").disabled=attack!=="T3";
  text("attack-description",({T1:"Phantom · simulated additional objects",T2:"Flood · simulated short-lived object burst",T3:"Replay · copied recorded moving trajectory (A3/A4 only)",T4:"Shift / overwrite · growing offset on a moving track"})[attack]||"Clean alerts are false positives.");
}
function pause(){playing=false;clearTimeout(timer);text("play","Play");}
function schedule(){
  if(!playing||!result)return;
  timer=setTimeout(()=>{if(cursor>=result.cycles.length-1){pause();return;}cursor++;render();schedule();},Math.max(8,result.period_seconds*1000/Number($("speed").value)));
}
function render(){
  const canvas=$("scene"),ctx=canvas.getContext("2d"),w=canvas.width,h=canvas.height;
  ctx.clearRect(0,0,w,h);
  const roi=result?.roi||15,scale=(h-70)/(roi*2),origin={x:65,y:h/2};
  const point=(x,y)=>({x:origin.x+x*scale,y:origin.y-y*scale});
  ctx.strokeStyle="#202e3b";ctx.lineWidth=1;
  for(let i=-15;i<=15;i+=3){let a=point(0,i),b=point(30,i);ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();}
  for(let i=0;i<=30;i+=3){let a=point(i,-15),b=point(i,15);ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();ctx.fillStyle="#8194a7";ctx.font="11px monospace";ctx.fillText(String(i),a.x+3,h-12);}
  ctx.setLineDash([3,5]);for(let r=3;r<=roi;r+=3){ctx.beginPath();ctx.arc(origin.x,origin.y,r*scale,-Math.PI/2,Math.PI/2);ctx.stroke();}ctx.setLineDash([]);
  ctx.fillStyle="#78e2bd";ctx.beginPath();ctx.arc(origin.x,origin.y,5,0,Math.PI*2);ctx.fill();ctx.fillStyle="#8296a9";ctx.font="10px monospace";ctx.fillText("SR75",origin.x-35,origin.y+25);ctx.fillText("x / coordinate units (provisional)",w-265,h-12);ctx.fillText("y",14,24);ctx.fillText(`ROI ≤ ${roi}; out-of-ROI objects can have protocol alerts`,w-410,22);
  if(!result)return;
  const cycle=result.cycles[cursor];
  for(const o of cycle.objects){
    if(o.x===null||o.y===null)continue;
    const p=point(o.x,o.y),color=o.alert?"#ff717a":o.flagged?"#f0bd64":"#78e2bd";
    const trail=result.cycles.slice(Math.max(0,cursor-20),cursor+1).flatMap(c=>c.objects.filter(v=>v.track_id!==null&&v.track_id===o.track_id&&v.x!==null));
    ctx.strokeStyle=color+"55";ctx.beginPath();trail.forEach((v,i)=>{const t=point(v.x,v.y);i?ctx.lineTo(t.x,t.y):ctx.moveTo(t.x,t.y);});ctx.stroke();
    ctx.fillStyle=color;ctx.beginPath();ctx.arc(p.x,p.y,o.alert?5:3.5,0,Math.PI*2);ctx.fill();
    const q=point(o.x+(o.vx||0),o.y+(o.vy||0));
    if(Math.hypot(q.x-p.x,q.y-p.y)>1){ctx.strokeStyle=color;ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);ctx.stroke();const a=Math.atan2(q.y-p.y,q.x-p.x);ctx.beginPath();ctx.moveTo(q.x,q.y);ctx.lineTo(q.x-6*Math.cos(a-.4),q.y-6*Math.sin(a-.4));ctx.lineTo(q.x-6*Math.cos(a+.4),q.y-6*Math.sin(a+.4));ctx.closePath();ctx.fill();}
    if(o.flagged||o.moving){ctx.fillStyle=color;ctx.font="10px monospace";ctx.fillText(`${o.slot===null?"?":o.slot.toString(16).padStart(2,"0")} ${o.reasons.join(" · ")}`,p.x+8,p.y-5);}
  }
  $("seek").value=cursor;text("clock",`${cursor+1}/${result.cycles.length} · ${((cycle.header_timestamp_ticks-result.cycles[0].header_timestamp_ticks)*result.tick_seconds).toFixed(2)}s`);
  const warning=$("cycle-warning");warning.style.display=cycle.cycle_reasons.length?"block":"none";warning.textContent="Scene warning: "+cycle.cycle_reasons.join(" · ");
  const entries=[];
  for(const c of result.cycles.slice(0,cursor+1)){
    if(c.cycle_alert)entries.push({cycle:c.index,frame:c.header_frame_index,who:"scene",reasons:c.cycle_reasons});
    for(const v of c.objects)if(v.alert)entries.push({cycle:c.index,frame:v.frame_index,who:`slot ${v.slot??"?"}`,reasons:v.reasons});
  }
  $("alert-log").replaceChildren();
  for(const e of entries.slice(-30).reverse()){
    const row=document.createElement("div");row.className="entry";
    const time=document.createElement("span");time.className="time";time.textContent=`cycle ${e.cycle}`;
    const codes=document.createElement("div");codes.className="codes";codes.textContent=`${e.who} / frame ${e.frame} · ${e.reasons.join(" · ")}`;
    row.append(time,codes);$("alert-log").append(row);
  }
  if(!entries.length)$("alert-log").textContent="No detector alert through the displayed cycle.";
}
async function reset(){
  generation++;pause();if(job){try{await api(`/api/jobs/${job}`,{method:"DELETE"});}catch(e){text("progress",e.message);}job=null;}
  result=null;cursor=0;$("empty").style.display="flex";$("seek").max=0;$("seek").value=0;
  $("cycle-warning").style.display="none";text("run-kind","RECORDED SENSOR DATA");
  for(const id of ["objects","alerts","latency","assembly","clock"])text(id,"—");
  text("provenance","No run selected.");text("alert-log","No replay processed yet.");text("progress","Reset complete. Next run starts with fresh causal history.");render();
}
async function run(){
  try{
    await reset();const operation=++generation;$("run").disabled=true;
    const attack=$("attack").value||null;
    const created=await api("/api/jobs",{method:"POST",body:{recording:$("recording").value,attack,level:attack?$("level").value:null,seed:Number($("seed").value),cycles:Number($("cycles").value),motion:$("motion").value,variant:$("variant").value}});
    if(operation!==generation){await api(`/api/jobs/${created.id}`,{method:"DELETE"});return;}
    job=created.id;const thisJob=job;text("progress","Queued · waiting for an isolated CPU worker…");
    let pollDelay=1000;
    while(job===thisJob){
      await new Promise(r=>setTimeout(r,pollDelay));if(job!==thisJob)break;
      const state=await api(`/api/jobs/${thisJob}`);
      pollDelay=state.state==="queued"?2000:1000;
      text("progress",`${state.state} · ${state.progress.stage||"waiting"} ${state.progress.completed||0}${state.progress.total?" / "+state.progress.total:""}`);
      if(state.state==="complete"){
        result=await api(`/api/jobs/${thisJob}/result`);cursor=0;$("seek").max=result.cycles.length-1;$("empty").style.display="none";
        text("run-kind",attack?`SIMULATED ${attack} / ${$("level").value}`:"RECORDED · CLEAN");
        text("objects",result.summary.object_cycles.toLocaleString());text("alerts",result.summary.alerting_cycles);text("latency",result.processing.p99_ms.toFixed(2)+" ms");text("assembly",result.assembly.assembly_p99_ms===null?"unmeasured":result.assembly.assembly_p99_ms.toFixed(1)+" ms");
        const dl=document.createElement("dl");for(const [key,value] of Object.entries({...result.request,...result.provenance})){if(key==="configuration")continue;const dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=key.replaceAll("_"," ");dd.textContent=String(value);dl.append(dt,dd);}$("provenance").replaceChildren(dl);
        text("progress",`Complete · ${result.cycles.length} cycles in ${result.elapsed_seconds.toFixed(1)}s. Playback is independent of detection.`);render();break;
      }
      if(["failed","timed_out","cancelled"].includes(state.state))throw new Error(state.error||state.state);
    }
  }catch(e){text("progress",e.message);}finally{$("run").disabled=false;}
}
const pct=value=>value===""||value===null||value===undefined?"unmeasured":(Number(value)*100).toFixed(1)+"%";
async function evaluation(individual=false){
  try{
    const query=new URLSearchParams({recording:$("recording").value,seed:$("seed").value});
    if($("attack").value){query.set("attack",$("attack").value);query.set("level",$("level").value);}
    const evidence=await api(individual?`/api/evaluation/runs?${query}`:"/api/evaluation");
    text("evaluation-note",evidence.note+` Showing at most ${evidence.row_limit} rows.`);text("evaluation-provenance",JSON.stringify(evidence.provenance,null,2));
    $("evaluation-rows").replaceChildren();
    for(const r of evidence.rows){const row=document.createElement("tr");for(const value of [`${r.split} / ${r.file||"pooled recordings"}`,`${r.attack_type} / ${r.level}`,r.configured_seed?`${r.configured_seed} / ${r.run}`:"all configured runs",r.attack_instances||"—",pct(r.attack_instance_detection_rate),pct(r.object_detection_rate),r.status||"completed observed attacks"]){const td=document.createElement("td");td.textContent=value;row.append(td);}$("evaluation-rows").append(row);}
    $("clean-results").replaceChildren();
    for(const split of ["timeblock","loso"]){const rows=evidence.clean.filter(r=>r.split===split&&r.part==="test"&&r.status==="ok");const events=rows.reduce((s,r)=>s+Number(r.alert_events),0),minutes=rows.reduce((s,r)=>s+Number(r.minutes_exact),0);const chip=document.createElement("span");chip.className="clean-chip";chip.textContent=minutes?`${split}: ${(events/minutes).toFixed(2)} clean alerts/min · ${events} events · target <1 ${events/minutes<1?"met":"NOT MET"}`:`${split}: no measured clean results`;$("clean-results").append(chip);}
  }catch(e){text("evaluation-note",e.message);}
}
$("run").onclick=run;$("reset").onclick=reset;$("cancel").onclick=reset;$("attack").onchange=updateOptions;
$("play").onclick=()=>{if(!result)return;if(playing){pause();return;}playing=true;text("play","Pause");schedule();};
$("step").onclick=()=>{pause();if(result){cursor=Math.min(cursor+1,result.cycles.length-1);render();}};
$("seek").oninput=()=>{pause();cursor=Number($("seek").value);render();};$("filter-results").onclick=()=>evaluation(true);
async function boot(){
  try{catalog=await api("/api/catalog");for(const f of catalog.recordings){const option=document.createElement("option");option.value=f;option.textContent=f;$("recording").append(option);}$("recording").value="onePersonMovingFrontAndBack.csv";$("cycles").max=catalog.max_cycles;$("cycles").value=Math.min(600,catalog.max_cycles);updateOptions();token=(await api("/api/sessions",{method:"POST"})).token;
    const ready=await api("/readyz");text("ready",ready.ok?"● CPU pipeline ready":"● Not ready");await evaluation();
  }catch(e){text("ready","● Not ready");text("progress",e.message);}render();
}
boot();
