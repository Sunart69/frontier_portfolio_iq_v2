"""Executive decision dashboard (v2): one self-contained HTML page generated from the DecisionBundle.

Tabs: Choices · Frontier · Compare · Risk · Execution · Scenarios · Assurance. Every number is read from
the bundle's JSON (embedded); badges distinguish PROVEN optimization results, SIMULATED probabilities
and ESTIMATE inputs. No network access is needed to open it.
"""
from __future__ import annotations

import json

from .intelligence import _json_default


def build_decision_dashboard(bundle, path: str) -> str:
    payload = json.dumps(bundle.to_dict(), default=_json_default, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.replace("__DATA__", payload)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Frontier Portfolio IQ — Decision Intelligence</title>
<style>
.viz-root{color-scheme:light;--surface-1:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink-2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;
--good:#0ca30c;--warn:#fab219;--serious:#ec835a;--critical:#d03b3b;--seq1:#cde2fb;--seq4:#3987e5;--seq7:#0d366b;
--proven:#0a7a0a;--sim:#2a78d6;--est:#8a5a00;--card:#ffffff}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;--surface-1:#1a1a19;
--page:#0d0d0d;--ink:#f0efec;--ink-2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--card:#222220;--seq1:#184f95;--seq7:#cde2fb}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface-1:#1a1a19;--page:#0d0d0d;--ink:#f0efec;--ink-2:#c3c2b7;
--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--card:#222220;
--seq1:#184f95;--seq7:#cde2fb}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--page)}
.viz-root{color:var(--ink);background:var(--page);min-height:100vh}
header{padding:18px 24px 8px;display:flex;flex-wrap:wrap;gap:12px;align-items:baseline;justify-content:space-between}
h1{font-size:20px;margin:0}h2{font-size:16px;margin:0 0 8px}h3{font-size:13px;margin:0 0 6px;color:var(--ink-2);font-weight:600}
.meta{color:var(--ink-2);font-size:12px}.badge{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;color:#fff;margin-right:6px;letter-spacing:.02em}
.b-proven{background:var(--proven)}.b-sim{background:var(--sim)}.b-est{background:var(--est)}.b-warn{background:var(--critical)}
nav{display:flex;gap:4px;padding:0 24px;border-bottom:1px solid var(--border);flex-wrap:wrap}
nav button{background:none;border:none;border-bottom:2px solid transparent;padding:10px 12px;font:inherit;font-size:14px;color:var(--ink-2);cursor:pointer}
nav button.on{color:var(--ink);border-bottom-color:var(--s1);font-weight:600}
.bar{display:flex;gap:10px;align-items:center;padding:10px 24px;flex-wrap:wrap;font-size:13px;color:var(--ink-2)}
select,input[type=checkbox]{font:inherit;font-size:13px}select{padding:4px 6px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--ink)}
.chip{display:inline-flex;gap:6px;align-items:center;border:1px solid var(--border);border-radius:14px;padding:3px 10px;cursor:pointer;background:var(--card);font-size:12px;color:var(--ink)}
.chip i{width:10px;height:10px;border-radius:50%;display:inline-block}
main{padding:8px 24px 40px}.tab{display:none}.tab.on{display:block}
.grid{display:grid;gap:14px}.g2{grid-template-columns:repeat(auto-fit,minmax(420px,1fr))}.g4{grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:14px}
.card.sel{outline:2px solid var(--s1)}.card{overflow:auto}td:first-child{min-width:150px}.choice{cursor:pointer}
.big{font-size:26px;font-weight:700}.small{font-size:12px;color:var(--ink-2)}.kv{display:grid;grid-template-columns:1fr auto;gap:3px 10px;font-size:13px;margin-top:8px}
.kv span:nth-child(odd){color:var(--ink-2)}
table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:5px 8px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
th{color:var(--ink-2);font-weight:600;cursor:pointer;white-space:nowrap}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tr.pick{background:color-mix(in srgb,var(--s1) 10%,transparent)}
svg text{fill:var(--ink-2);font-size:11px}svg .ink{fill:var(--ink)}.ax{stroke:var(--axis)}.gl{stroke:var(--grid)}
#tip{position:fixed;pointer-events:none;background:var(--card);color:var(--ink);border:1px solid var(--border);border-radius:8px;padding:8px 10px;font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.15);display:none;max-width:320px;z-index:10}
.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:var(--ink-2);margin-top:6px}.legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;vertical-align:-1px}
.note{font-size:12px;color:var(--ink-2);margin-top:6px}.ok{color:var(--good);font-weight:600}.bad{color:var(--critical);font-weight:600}
canvas{width:100%;height:auto;aspect-ratio:3/2;cursor:grab;background:var(--surface-1)}
</style></head><body><div class="viz-root">
<header><div><h1>Frontier Portfolio IQ · Decision Intelligence</h1>
<div class="meta" id="meta"></div></div><div id="badges"></div></header>
<nav id="nav"></nav>
<div class="bar"><label>Focus portfolio <select id="focus"></select></label>
<span>Compare (up to 4):</span><span id="chips"></span>
<label><input type="checkbox" id="effOnly"> efficient only</label></div>
<main>
<section class="tab" id="t-choices"></section>
<section class="tab" id="t-frontier"></section>
<section class="tab" id="t-compare"></section>
<section class="tab" id="t-risk"></section>
<section class="tab" id="t-execution"></section>
<section class="tab" id="t-scenarios"></section>
<section class="tab" id="t-assurance"></section>
</main><div id="tip"></div></div>
<script>
const D = __DATA__;
const $=s=>document.querySelector(s), el=(t,a={},h="")=>{const e=document.createElement(t);for(const k in a)e.setAttribute(k,a[k]);e.innerHTML=h;return e};
const SV="http://www.w3.org/2000/svg", sv=(t,a={})=>{const e=document.createElementNS(SV,t);for(const k in a)e.setAttribute(k,a[k]);return e};
const M=v=>v==null||isNaN(v)?"—":(Math.abs(v)>=1000?"$"+(v/1000).toFixed(1)+"M":"$"+Math.round(v)+"K");
const P=v=>v==null?"—":Math.round(v*100)+"%", F=(v,d=1)=>v==null?"—":(+v).toLocaleString(undefined,{maximumFractionDigits:d});
const SLOT=["var(--s1)","var(--s2)","var(--s3)","var(--s4)"];
const alts=D.frontier.alternatives, A=Object.fromEntries(alts.map(a=>[a.alt_id,a])), R=D.risk, RA=D.risk_adaptive||{};
const OBJ=Object.fromEntries(D.objectives.map(o=>[o.key,o])), keys=D.frontier.objective_keys;
const MN=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"],[Y0,M0]=(D.first_month_label||"2027-01").split("-").map(Number);
const month=t=>{const i=(M0-1)+Math.round(t)-1;return MN[((i%12)+12)%12]+" "+(Y0+Math.floor(i/12))};
const short=Object.keys(R);
const choiceOrder=["aggressive","balanced","conservative","accelerated"];
let sel=[...new Set(choiceOrder.filter(k=>D.choices[k]).map(k=>D.choices[k].alt_id))].slice(0,4);
let focus=D.recommended||sel[0];
const tip=$("#tip");
function showTip(ev,html){tip.innerHTML=html;tip.style.display="block";const x=Math.min(ev.clientX+14,innerWidth-330);tip.style.left=x+"px";tip.style.top=(ev.clientY+14)+"px"}
function hideTip(){tip.style.display="none"}
function name(id){const a=A[id];return a?(a.name||id):id}
function slotOf(id){const k=sel.indexOf(id);return k<0?null:SLOT[k]}
// ---------------------------------------------------------------- header
const conf=D.solver_confidence, conv=(R[focus]||{}).convergence||{};
$("#meta").textContent=`Model ${D.model_version} · run ${D.run_id} · ${Object.keys(D.projects).length} initiatives · ${D.horizon}-month horizon`;
$("#badges").innerHTML=`<span class="badge b-proven" title="${conf.verdict}">OPTIMIZATION: ${conf.level.replace(/_/g," ")}</span>`+
`<span class="badge b-sim">SIMULATED: ${F((D.assumptions.simulation||{}).iterations,0)} futures${conv.converged?" · converged":""}</span>`+
(D.assumptions.calibrated?`<span class="badge b-proven">INPUTS CALIBRATED</span>`:`<span class="badge b-est" title="Uncertainty inputs are demonstration defaults">ESTIMATE: uncalibrated inputs</span>`)+
(D.consistency_issues.length?`<span class="badge b-warn">MODEL VERSION MISMATCH</span>`:"");
// ---------------------------------------------------------------- nav + selection bar
const tabs=[["choices","Portfolio choices"],["frontier","Trade-off frontier"],["compare","Compare"],["risk","Risk"],["execution","Execution"],["scenarios","What-if"],["assurance","Decision assurance"]];
tabs.forEach(([k,l],i)=>{const b=el("button",{},l);b.onclick=()=>show(k);b.id="nb-"+k;$("#nav").append(b)});
function show(k){document.querySelectorAll(".tab").forEach(t=>t.classList.toggle("on",t.id=="t-"+k));document.querySelectorAll("nav button").forEach(b=>b.classList.toggle("on",b.id=="nb-"+k));render(k)}
function fillFocus(){const s=$("#focus");s.innerHTML="";short.forEach(id=>{const o=el("option",{value:id},`${name(id)} (${id})`);if(id==focus)o.selected=true;s.append(o)});s.onchange=()=>{focus=s.value;rerender()}}
function fillChips(){const c=$("#chips");c.innerHTML="";short.forEach(id=>{const k=sel.indexOf(id);const ch=el("span",{class:"chip"},`<i style="background:${k<0?"transparent":SLOT[k]};border:1px solid var(--axis)"></i>${name(id)}`);
ch.onclick=()=>{if(k>=0)sel.splice(k,1);else if(sel.length<4)sel.push(id);fillChips();rerender()};c.append(ch)})}
$("#effOnly").onchange=()=>rerender();
let current="choices";function rerender(){render(current)}
function render(k){current=k;({choices:rChoices,frontier:rFrontier,compare:rCompare,risk:rRisk,execution:rExec,scenarios:rScen,assurance:rAssure})[k]()}
// ---------------------------------------------------------------- chart helpers
function axes(g,W,H,m,xs,ys,xl,yl,fx=F,fy=F){const nt=5;for(let i=0;i<=nt;i++){const yv=ys.d0+(ys.d1-ys.d0)*i/nt,y=ys(yv);g.append(sv("line",{x1:m.l,x2:W-m.r,y1:y,y2:y,class:"gl"}));const t=sv("text",{x:m.l-6,y:y+4,"text-anchor":"end"});t.textContent=fy(yv);g.append(t)}
for(let i=0;i<=nt;i++){const xv=xs.d0+(xs.d1-xs.d0)*i/nt,x=xs(xv);const t=sv("text",{x:x,y:H-m.b+16,"text-anchor":"middle"});t.textContent=fx(xv);g.append(t)}
g.append(sv("line",{x1:m.l,x2:W-m.r,y1:H-m.b,y2:H-m.b,class:"ax"}));
const tx=sv("text",{x:(m.l+W-m.r)/2,y:H-4,"text-anchor":"middle"});tx.textContent=xl;g.append(tx);
const ty=sv("text",{x:12,y:(m.t+H-m.b)/2,"text-anchor":"middle",transform:`rotate(-90 12 ${(m.t+H-m.b)/2})`});ty.textContent=yl;g.append(ty)}
function lin(d0,d1,r0,r1){if(d1==d0){d1=d0+1}const f=v=>r0+(v-d0)/(d1-d0)*(r1-r0);f.d0=d0;f.d1=d1;return f}
function ext(v,pad=.06){let a=Math.min(...v),b=Math.max(...v);if(a==b){a-=1;b+=1}const p=(b-a)*pad;return[a-p,b+p]}
function svgBox(W,H){return sv("svg",{viewBox:`0 0 ${W} ${H}`,width:"100%",role:"img"})}
function hbars(rows,{W=560,fmt=F,max=null,color="var(--s1)",label="",zero=0}={}){const m={l:200,r:60,t:6,b:20},bh=22,H=m.t+m.b+rows.length*bh;const s=svgBox(W,H);
const vals=rows.map(r=>r[1]);const lo=Math.min(zero,...vals),hi=max??Math.max(...vals,0.0001);const x=lin(lo,hi,m.l,W-m.r);
rows.forEach((r,i)=>{const y=m.t+i*bh;const t=sv("text",{x:m.l-6,y:y+15,"text-anchor":"end"});t.textContent=r[0];s.append(t);
const x0=x(Math.min(0,r[1])),w=Math.abs(x(r[1])-x(0));const b=sv("rect",{x:x0,y:y+4,width:Math.max(1,w),height:bh-8,rx:3,fill:r[2]||color});
b.onmousemove=e=>showTip(e,`<b>${r[0]}</b><br>${fmt(r[1])}${r[3]?"<br>"+r[3]:""}`);b.onmouseleave=hideTip;s.append(b);
const v=sv("text",{x:r[1]<0?x(0)+4:x(r[1])+4,y:y+15,"text-anchor":"start",class:"ink"});v.textContent=fmt(r[1]);s.append(v)});
s.append(sv("line",{x1:x(0),x2:x(0),y1:m.t,y2:H-m.b,class:"ax"}));if(label){const t=sv("text",{x:(m.l+W-m.r)/2,y:H-4,"text-anchor":"middle"});t.textContent=label;s.append(t)}return s}
// ---------------------------------------------------------------- tab: choices
function riskOf(id){return R[id]||RA[id]}
function rChoices(){const t=$("#t-choices");t.innerHTML="";
const g=el("div",{class:"grid g4"});const seen={};
choiceOrder.forEach(k=>{const c=D.choices[k];if(!c)return;const a=A[c.alt_id],r=riskOf(c.alt_id);if(!a||!r)return;
const inv=Object.values(a.lob_allocations).reduce((s,v)=>s+(v.investment||0),0);
const same=seen[c.alt_id];seen[c.alt_id]=c.title;
const card=el("div",{class:"card choice"+(c.alt_id==focus?" sel":"")},`<h3>${c.title.toUpperCase()}${c.alt_id==D.recommended?" · RECOMMENDED":""}</h3>
<div class="big">${M(r.npv.mean)}</div><div class="small">expected NPV · P10 ${M(r.npv.p10)} – P90 ${M(r.npv.p90)} <span class="badge b-sim">SIM</span></div>
<div class="kv"><span>Initiatives</span><span>${a.selected.length}</span><span>Investment</span><span>${M(inv)}</span>
<span>P(within budget)</span><span>${P(r.probabilities.within_budget.p)}</span><span>P(meet committed dates)</span><span>${P(r.probabilities.meet_key_dates.p)}</span>
<span>CVaR (worst ${Math.round((1-r.var_cvar.alpha)*100)}%)</span><span>${M(r.var_cvar.cvar)}</span><span>Optimization</span><span>${a.evidence.proof.replace("_"," ")}</span></div>
<div class="note">${c.rule}${same?` — same plan as ${same}`:""}</div>`);card.onclick=()=>{focus=c.alt_id;fillFocus();rerender()};g.append(card)});
t.append(g);
const rk=D.rankings.balanced||Object.values(D.rankings)[0];
const rec=el("div",{class:"card",style:"margin-top:14px"},`<h2>Recommendation (${rk.profile.name.replace("_"," ")} profile)</h2>
<div><b>${name(D.recommended)}</b> (${D.recommended})</div><ul>${rk.rationale.map(x=>"<li>"+x+"</li>").join("")}</ul>${rk.note?`<div class="bad">${rk.note}</div>`:""}
<div class="note">${D.rankings.balanced?"Method: "+rk.method+". Weights: "+Object.entries(rk.profile.weights).map(([k,v])=>k.replace(/_/g," ")+" "+Math.round(v*100)+"%").join(", ")+". Constraints: "+(rk.profile.constraints.join("; ")||"none")+".":""}</div>`);
t.append(rec);
const prof=el("div",{class:"card",style:"margin-top:14px"},"<h2>How each risk preference ranks the shortlist</h2>");
const tb=el("table");tb.innerHTML="<tr><th>Profile</th><th>Recommended</th><th class=n>Score</th><th class=n>Utility (E[NPV] − λ·CVaR)</th><th>Eligible plans</th></tr>"+
Object.entries(D.rankings).map(([k,r])=>{const top=r.rows.find(x=>x.alt_id==r.recommended)||{};return `<tr><td>${k.replace(/_/g," ")}</td><td>${name(r.recommended)}</td><td class=n>${F(top.score,3)}</td><td class=n>${M(top.utility)}</td><td>${r.eligible.length}/${r.rows.length}</td></tr>`}).join("");
prof.append(tb);t.append(prof)}
// ---------------------------------------------------------------- tab: frontier
let fx=keys[1]||keys[0],fy=keys[0],f3=[keys[0],keys[1]||keys[0],keys[2]||keys[0]],rot={a:0.6,b:0.4};
function visibleAlts(){return alts.filter(a=>a.evidence.validated&&a.status!="duplicate"&&(!$("#effOnly").checked||a.status=="efficient"))}
function objSelect(cur,on){const s=el("select");D.objectives.forEach(o=>{const op=el("option",{value:o.key},o.name);if(o.key==cur)op.selected=true;s.append(op)});s.onchange=()=>on(s.value);return s}
function rFrontier(){const t=$("#t-frontier");t.innerHTML="";const g=el("div",{class:"grid g2"});
const c1=el("div",{class:"card"},`<h2>Pareto scatter <span class="badge b-proven">PROVEN</span></h2>`);const ctl=el("div",{class:"bar",style:"padding:0 0 6px"});
ctl.append("X ",objSelect(fx,v=>{fx=v;rerender()})," Y ",objSelect(fy,v=>{fy=v;rerender()}));c1.append(ctl,scatter(fx,fy));
c1.append(el("div",{class:"legend"},`<span><i style="background:var(--s1)"></i>efficient</span><span><i style="border:1.5px solid var(--muted)"></i>dominated</span><span><i style="border:2px solid var(--ink)"></i>shortlisted (simulated)</span><span>numbers = compare set</span>`));
c1.append(el("div",{class:"note"},`Efficiency is judged on ${keys.map(k=>OBJ[k].name.toLowerCase()).join(", ")}. ${D.frontier.infeasible.length} epsilon targets had no feasible portfolio. Weighted sums can only reach supported (convex-hull) points; epsilon-constraint also reaches non-convex ones. NSGA-II points, if any, are approximate.`));
g.append(c1);
const c2=el("div",{class:"card"},"<h2>3-D trade-off view</h2>");const ctl2=el("div",{class:"bar",style:"padding:0 0 6px"});
ctl2.append("X ",objSelect(f3[0],v=>{f3[0]=v;draw3d()})," Y ",objSelect(f3[1],v=>{f3[1]=v;draw3d()})," Z ",objSelect(f3[2],v=>{f3[2]=v;draw3d()}));
const cv=el("canvas",{id:"c3d",width:900,height:600});c2.append(ctl2,cv,el("div",{class:"note"},"Drag to rotate. Axes are normalized 0 (nadir) to 1 (ideal); blue = efficient, grey = dominated."));g.append(c2);
t.append(g);draw3d();initDrag(cv);
const c3=el("div",{class:"card",style:"margin-top:14px"},"<h2>Parallel coordinates (1 = ideal on every axis)</h2>");c3.append(parallel());t.append(c3);
const c4=el("div",{class:"card",style:"margin-top:14px"},"<h2>Portfolio comparison table</h2>");c4.append(altTable());t.append(c4)}
function scatter(kx,ky){const W=640,H=400,m={l:70,r:20,t:14,b:44};const s=svgBox(W,H);const vs=visibleAlts();
const xs=lin(...ext(vs.map(a=>a.objectives[kx])),m.l,W-m.r),ys=lin(...ext(vs.map(a=>a.objectives[ky])),H-m.b,m.t);
const fmt=k=>OBJ[k].unit.startsWith("$")?M:(v=>F(v,OBJ[k].unit.includes("share")?2:1));
axes(s,W,H,m,xs,ys,`${OBJ[kx].name} (${OBJ[kx].direction=="max"?"higher":"lower"} is better)`,`${OBJ[ky].name}`,fmt(kx),fmt(ky));
vs.sort((a,b)=>(a.status=="efficient")-(b.status=="efficient")).forEach(a=>{const x=xs(a.objectives[kx]),y=ys(a.objectives[ky]),eff=a.status=="efficient",slot=slotOf(a.alt_id);
const c=sv("circle",{cx:x,cy:y,r:slot?8:6,fill:eff?"var(--s1)":"var(--surface-1)",stroke:slot||(short.includes(a.alt_id)?"var(--ink)":(eff?"var(--surface-1)":"var(--muted)")),"stroke-width":slot?3:(short.includes(a.alt_id)?2:1.5),style:"cursor:pointer"});
c.onmousemove=e=>showTip(e,`<b>${a.name||a.alt_id}</b> (${a.alt_id})<br>${a.status}${a.pareto_rank?" · rank "+a.pareto_rank:""} · ${a.method}<br>`+keys.map(k=>`${OBJ[k].name}: ${fmt(k)(a.objectives[k])}`).join("<br>")+`<br>${a.selected.length} initiatives · proof ${a.evidence.proof}`);
c.onmouseleave=hideTip;c.onclick=()=>{if(short.includes(a.alt_id)){focus=a.alt_id;fillFocus()}};s.append(c);
if(slot){const tt=sv("text",{x:x+10,y:y-8,class:"ink","font-weight":"700"});tt.textContent=sel.indexOf(a.alt_id)+1;s.append(tt)}});return s}
function norm(a,k){const lo=D.frontier.nadir[k],hi=D.frontier.ideal[k];if(lo==null||hi==null||hi==lo)return .5;return (a.objectives[k]-lo)/(hi-lo)}
function draw3d(){const cv=$("#c3d");if(!cv)return;const ctx=cv.getContext("2d"),W=cv.width,H=cv.height;ctx.clearRect(0,0,W,H);
const cs=getComputedStyle(document.querySelector(".viz-root"));const col=n=>cs.getPropertyValue(n).trim();
const ca=Math.cos(rot.a),sa=Math.sin(rot.a),cb=Math.cos(rot.b),sb=Math.sin(rot.b);
const S3=Math.min(W,H)*.55;const pr=(x,y,z)=>{x-=.5;y-=.5;z-=.5;let X=x*ca-z*sa,Z=x*sa+z*ca;let Y=y*cb-Z*sb;Z=y*sb+Z*cb;return[W/2+X*S3,H/2-Y*S3,Z]};
ctx.strokeStyle=col("--axis");ctx.lineWidth=1;[[[0,0,0],[1,0,0]],[[0,0,0],[0,1,0]],[[0,0,0],[0,0,1]]].forEach(([p,q],i)=>{const A_=pr(...p),B_=pr(...q);ctx.beginPath();ctx.moveTo(A_[0],A_[1]);ctx.lineTo(B_[0],B_[1]);ctx.stroke();
ctx.fillStyle=col("--ink-2");ctx.font="22px system-ui";const lbl=OBJ[f3[i]].name.split(" (")[0];ctx.textAlign=B_[0]>W*.7?"right":"left";ctx.fillText(lbl,B_[0]+(B_[0]>W*.7?-6:6),B_[1]-6)});ctx.textAlign="left";
visibleAlts().map(a=>[a,pr(norm(a,f3[0]),norm(a,f3[1]),norm(a,f3[2]))]).sort((p,q)=>p[1][2]-q[1][2]).forEach(([a,p])=>{ctx.beginPath();ctx.arc(p[0],p[1],slotOf(a.alt_id)?10:7,0,7);
ctx.fillStyle=a.status=="efficient"?col("--s1"):col("--surface-1");ctx.fill();ctx.lineWidth=slotOf(a.alt_id)?4:2;ctx.strokeStyle=slotOf(a.alt_id)?col(slotOf(a.alt_id).slice(4,-1)):col("--muted");ctx.stroke()})}
function initDrag(cv){let d=null;cv.onmousedown=e=>{d=[e.clientX,e.clientY]};window.onmouseup=()=>d=null;cv.onmousemove=e=>{if(!d)return;rot.a+=(e.clientX-d[0])*.01;rot.b+=(e.clientY-d[1])*.01;d=[e.clientX,e.clientY];draw3d()}}
function parallel(){const ks=D.objectives.map(o=>o.key),W=1100,H=330,m={l:30,r:30,t:30,b:40};const s=svgBox(W,H);const x=i=>m.l+i*(W-m.l-m.r)/(ks.length-1),y=v=>H-m.b-(v)*(H-m.t-m.b);
const PL={value:"Value",npv:"NPV",alignment:"Alignment",okr:"OKR",acceleration:"Acceleration",compliance:"Compliance",cost:"Cost",risk:"Risk",overload:"Peak load",time_to_value:"Time to value",diversification:"Diversification"};
ks.forEach((k,i)=>{s.append(sv("line",{x1:x(i),x2:x(i),y1:m.t,y2:H-m.b,class:"ax"}));const t=sv("text",{x:x(i),y:H-m.b+(i%2?30:16),"text-anchor":i==0?"start":(i==ks.length-1?"end":"middle")});t.textContent=PL[k]||OBJ[k].name.split(" ")[0];s.append(t)});
visibleAlts().forEach(a=>{const slot=slotOf(a.alt_id);const pts=ks.map((k,i)=>`${x(i)},${y(Math.max(0,Math.min(1,norm(a,k))))}`).join(" ");
const pl=sv("polyline",{points:pts,fill:"none",stroke:slot||(a.status=="efficient"?"var(--s1)":"var(--axis)"),"stroke-width":slot?3:1.5,opacity:slot?1:(a.status=="efficient"?.55:.35)});
pl.onmousemove=e=>showTip(e,`<b>${a.name||a.alt_id}</b> · ${a.status}`);pl.onmouseleave=hideTip;s.append(pl)});return s}
function altTable(){const ks=D.objectives.map(o=>o.key);const wrap=el("div",{style:"overflow:auto"});const tb=el("table");
let rows=visibleAlts();const head="<tr><th>Plan</th><th>Status</th><th>Method</th><th class=n>#</th>"+ks.map(k=>`<th class=n title="${OBJ[k].formula}">${OBJ[k].name} (${OBJ[k].direction})</th>`).join("")+"<th>Proof</th><th>Validated</th></tr>";
const draw=()=>{tb.innerHTML=head+rows.map(a=>`<tr class="${a.alt_id==focus?"pick":""}"><td>${a.name||a.alt_id}<br><span class=small>${a.alt_id}</span></td><td>${a.status}${a.pareto_rank?" ("+a.pareto_rank+")":""}</td><td>${a.method}${a.evidence.certified?"":" (approx.)"}</td><td class=n>${a.selected.length}</td>`+
ks.map(k=>`<td class=n>${OBJ[k].unit.startsWith("$")?M(a.objectives[k]):F(a.objectives[k],2)}</td>`).join("")+`<td>${a.evidence.proof}</td><td>${a.evidence.validated?'<span class=ok>yes</span>':'<span class=bad>no</span>'}</td></tr>`).join("");
tb.querySelectorAll("th").forEach((th,i)=>th.onclick=()=>{const k=ks[i-4];if(!k)return;rows.sort((p,q)=>q.objectives[k]-p.objectives[k]);draw()})};draw();wrap.append(tb);return wrap}
// ---------------------------------------------------------------- tab: compare
function rCompare(){const t=$("#t-compare");t.innerHTML="";const g=el("div",{class:"grid g2"});
const rk=D.rankings.balanced||Object.values(D.rankings)[0];const rows=Object.fromEntries(rk.rows.map(r=>[r.alt_id,r]));
const crit=["expected_npv","downside_risk","budget_confidence","schedule_confidence","strategic_alignment","okr_achievement","resource_resilience","time_to_value"];
const c1=el("div",{class:"card"},"<h2>Trade-off radar (1 = best)</h2>");c1.append(radar(crit,rows));
c1.append(el("div",{class:"legend"},sel.map((id,i)=>`<span><i style="background:${SLOT[i]}"></i>${i+1} ${name(id)}</span>`).join("")));g.append(c1);
const c2=el("div",{class:"card"},"<h2>Incremental cost vs incremental value along the frontier</h2>");c2.append(increments());g.append(c2);
const c3=el("div",{class:"card"},"<h2>LOB value by portfolio (attributed, deterministic)</h2>");c3.append(lobBars());g.append(c3);
const c4=el("div",{class:"card"},"<h2>Bottleneck comparison: peak monthly load by capability</h2>");c4.append(bottlenecks());g.append(c4);
t.append(g);const c5=el("div",{class:"card",style:"margin-top:14px"},"<h2>What you give up: initiatives in / out versus the focus portfolio</h2>");c5.append(diffTable());t.append(c5)}
function radar(crit,rows){const W=520,H=420,cx=W/2,cy=H/2+6,Rr=150;const s=svgBox(W,H);const lab={expected_npv:"Expected NPV",downside_risk:"Downside protection",budget_confidence:"Budget confidence",schedule_confidence:"Schedule confidence",strategic_alignment:"Alignment",okr_achievement:"OKR achievement",resource_resilience:"Resilience",time_to_value:"Speed to value"};
const ang=i=>-Math.PI/2+i*2*Math.PI/crit.length;[.25,.5,.75,1].forEach(r=>s.append(sv("polygon",{points:crit.map((c,i)=>`${cx+Math.cos(ang(i))*Rr*r},${cy+Math.sin(ang(i))*Rr*r}`).join(" "),fill:"none",class:"gl"})));
crit.forEach((c,i)=>{s.append(sv("line",{x1:cx,y1:cy,x2:cx+Math.cos(ang(i))*Rr,y2:cy+Math.sin(ang(i))*Rr,class:"gl"}));const t=sv("text",{x:cx+Math.cos(ang(i))*(Rr+24),y:cy+Math.sin(ang(i))*(Rr+24)+4,"text-anchor":"middle"});t.textContent=lab[c];s.append(t)});
sel.forEach((id,k)=>{const r=rows[id];if(!r)return;const pts=crit.map((c,i)=>{const v=r.criteria[c].normalized;return `${cx+Math.cos(ang(i))*Rr*v},${cy+Math.sin(ang(i))*Rr*v}`}).join(" ");
const pg=sv("polygon",{points:pts,fill:SLOT[k],"fill-opacity":.08,stroke:SLOT[k],"stroke-width":2});pg.onmousemove=e=>showTip(e,`<b>${name(id)}</b><br>`+crit.map(c=>`${lab[c]}: ${F(r.criteria[c].normalized,2)} (raw ${F(r.criteria[c].raw,2)})`).join("<br>"));pg.onmouseleave=hideTip;s.append(pg)});return s}
function invOf(a){return Object.values(a.lob_allocations).reduce((s,v)=>s+(v.investment||0),0)}
function increments(){const eff=alts.filter(a=>a.status=="efficient").map(a=>({a,c:invOf(a),v:a.objectives[keys[0]]})).sort((p,q)=>p.c-q.c);
const rows=[];for(let i=1;i<eff.length;i++){const dc=eff[i].c-eff[i-1].c,dv=eff[i].v-eff[i-1].v;rows.push([`${M(eff[i-1].c)} → ${M(eff[i].c)}`,dc>1e-6?dv/dc:0,null,`+${M(dc)} cost buys ${M(dv)} of ${OBJ[keys[0]].name.toLowerCase()}`])}
if(!rows.length)return el("div",{class:"note"},"Not enough efficient plans.");const d=el("div");d.append(hbars(rows,{fmt:v=>F(v,2)+"x",label:`Incremental ${OBJ[keys[0]].name.toLowerCase()} per $1 of incremental investment`}));
d.append(el("div",{class:"note"},"Efficient plans sorted by investment; each bar is the value bought by the next step up the frontier. Falling bars = diminishing returns."));return d}
function lobBars(){const lobs=Object.keys(D.lobs);const W=560,bh=16,m={l:190,r:70,t:6,b:24};const H=m.t+m.b+lobs.length*(sel.length*bh+10);const s=svgBox(W,H);
const mx=Math.max(1,...sel.flatMap(id=>lobs.map(l=>((A[id]||{}).lob_allocations||{})[l]?.value||0)));const x=lin(0,mx,m.l,W-m.r);let y=m.t;
lobs.forEach(l=>{const t=sv("text",{x:m.l-6,y:y+sel.length*bh/2+4,"text-anchor":"end"});t.textContent=D.lobs[l].name;s.append(t);
sel.forEach((id,k)=>{const v=((A[id]||{}).lob_allocations||{})[l]?.value||0;const r=sv("rect",{x:m.l,y:y+1,width:Math.max(1,x(v)-m.l),height:bh-3,rx:3,fill:SLOT[k]});r.onmousemove=e=>showTip(e,`<b>${name(id)}</b><br>${D.lobs[l].name}: ${M(v)}`);r.onmouseleave=hideTip;s.append(r);
const vt=sv("text",{x:x(v)+4,y:y+bh-4,class:"ink"});vt.textContent=M(v);s.append(vt);y+=bh});y+=10});return s}
function peakByCap(id){const a=A[id];const out={};if(!a)return out;Object.entries(D.pools).forEach(([pid,p])=>{const used=a.capability_allocations[pid]||{};Object.entries(p.available).forEach(([t,av])=>{
out[p.capability]=out[p.capability]||{};out[p.capability][t]=out[p.capability][t]||[0,0];out[p.capability][t][0]+=used[t]||0;out[p.capability][t][1]+=av})});
const pk={};Object.entries(out).forEach(([c,m])=>{pk[c]=Math.max(...Object.values(m).map(([u,a])=>a?u/a:0))});return pk}
function bottlenecks(){const caps=[...new Set(Object.values(D.pools).map(p=>p.capability))].sort();const W=560,bh=14,m={l:190,r:60,t:6,b:24};const H=m.t+m.b+caps.length*(sel.length*bh+10);const s=svgBox(W,H);const x=lin(0,1,m.l,W-m.r);let y=m.t;
const pk=Object.fromEntries(sel.map(id=>[id,peakByCap(id)]));caps.forEach(c=>{const t=sv("text",{x:m.l-6,y:y+sel.length*bh/2+4,"text-anchor":"end"});t.textContent=c;s.append(t);
sel.forEach((id,k)=>{const v=pk[id][c]||0;s.append(sv("rect",{x:m.l,y:y+1,width:Math.max(1,x(v)-m.l),height:bh-3,rx:3,fill:SLOT[k]}));const vt=sv("text",{x:x(v)+4,y:y+bh-3,class:"ink"});vt.textContent=P(v);s.append(vt);y+=bh});y+=10});return s}
function diffTable(){const base=A[focus];const tb=el("table");tb.innerHTML="<tr><th>Portfolio</th><th>Adds</th><th>Drops</th></tr>"+sel.filter(id=>id!=focus).map(id=>{const a=A[id];const add=a.selected.filter(i=>!base.selected.includes(i)),drop=base.selected.filter(i=>!a.selected.includes(i));
return `<tr><td>${name(id)}</td><td>${add.map(i=>i+" "+D.projects[i].name).join("<br>")||"—"}</td><td>${drop.map(i=>i+" "+D.projects[i].name).join("<br>")||"—"}</td></tr>`}).join("");return tb}
// ---------------------------------------------------------------- tab: risk
function rRisk(){const t=$("#t-risk");t.innerHTML="";const r=riskOf(focus);if(!r){t.append(el("div",{class:"card"},"No simulation for this plan."));return}
const g=el("div",{class:"grid g2"});const c1=el("div",{class:"card"},`<h2>NPV distribution — ${name(focus)} <span class="badge b-sim">SIMULATED</span></h2>`);c1.append(hist(r));
c1.append(el("div",{class:"note"},`P10 ${M(r.npv.p10)} (90% of futures do better) · P50 ${M(r.npv.p50)} · P90 ${M(r.npv.p90)} · plan ${M(r.reference.npv)}. ${r.definitions.var_cvar}: VaR ${M(r.var_cvar.var)}, CVaR ${M(r.var_cvar.cvar)}.`));g.append(c1);
const c2=el("div",{class:"card"},`<h2>Confidence (simulated probabilities)</h2>`);const pr=r.probabilities;
c2.append(hbars([["Cost within plan + reserve",pr.within_budget.p,"var(--s1)",r.definitions.cost_overrun],["Every LOB within budget",1-pr.lob_budget_breach.p,"var(--s1)",r.definitions.lob_budget_breach],["All committed dates met",pr.meet_key_dates.p,"var(--s1)",r.definitions.key_dates],["Plan's OKR targets stay met",pr.okr_plan_targets.p,"var(--s1)",r.definitions.okr],["No capability shortage",1-pr.capability_shortage.p,"var(--s1)",r.definitions.capability_shortage],["Value ≥ minimum acceptable",pr.min_acceptable_value.p,"var(--s1)",r.definitions.min_acceptable_value]],{fmt:P,max:1}));
c2.append(el("div",{class:"note"},`Each probability ± ${P(Math.max(...Object.values(pr).map(x=>x.se))*1.96)} (95% sampling error, worst case).`));g.append(c2);
const c3=el("div",{class:"card"},"<h2>Schedule confidence: P(all initiatives done by month)</h2>");c3.append(cdf());g.append(c3);
const c4=el("div",{class:"card"},"<h2>Risk contribution in the worst futures</h2>");c4.append(hbars(r.contributions.slice(0,8).map(c=>[`${c.project} ${c.name.slice(0,22)}`,c.tail_shortfall,"var(--s2)",`share of tail ${P(c.share_of_tail)} · P(late) ${P(c.p_late)}`]),{fmt:M,label:`Average NPV shortfall per initiative in the worst ${Math.round((1-r.var_cvar.alpha)*100)}% of futures`}));g.append(c4);
const c5=el("div",{class:"card"},"<h2>Common risk drivers (rank correlation with NPV)</h2>");c5.append(hbars(r.drivers.slice(0,8).map(d=>[d.driver,d.spearman_npv,d.spearman_npv<0?"var(--s2)":"var(--s1)",`tail shift ${F(d.tail_shift_sd,2)} sd`]),{fmt:v=>F(v,2),label:"Spearman correlation"}));g.append(c5);
const c6=el("div",{class:"card"},"<h2>Fixed plan vs adaptive execution</h2>");const ad=RA[focus];
c6.append(el("div",{},ad?`<table><tr><th></th><th class=n>Fixed plan</th><th class=n>Adaptive (recourse)</th></tr>
<tr><td>Expected NPV</td><td class=n>${M(r.npv.mean)}</td><td class=n>${M(ad.npv.mean)}</td></tr><tr><td>P50 completion</td><td class=n>${month(r.completion.p50)}</td><td class=n>${month(ad.completion.p50)}</td></tr>
<tr><td>P(meet committed dates)</td><td class=n>${P(r.probabilities.meet_key_dates.p)}</td><td class=n>${P(ad.probabilities.meet_key_dates.p)}</td></tr><tr><td>P(within budget)</td><td class=n>${P(r.probabilities.within_budget.p)}</td><td class=n>${P(ad.probabilities.within_budget.p)}</td></tr>
<tr><td>Contingency hours used (mean)</td><td class=n>0</td><td class=n>${F(ad.contingency_hours,0)}</td></tr><tr><td>Start deferrals (months, mean)</td><td class=n>0</td><td class=n>${F(ad.deferred_months,2)}</td></tr></table>
<div class=note>Adaptive = commitments first, gated starts (max ${D.assumptions.simulation.max_defer_months} months deferral), approved contingency ${P(D.assumptions.simulation.contingency_pct)} of capacity at $${D.assumptions.simulation.contingency_rate}/h. Never unlimited resources.</div>`:"Adaptive simulation runs for the strategic choices; pick one of them as focus."));g.append(c6);t.append(g)}
function hist(r){const h=r.histogram,W=600,H=300,m={l:56,r:16,t:12,b:40};const s=svgBox(W,H);const x=lin(h.edges[0],h.edges[h.edges.length-1],m.l,W-m.r),y=lin(0,Math.max(...h.counts),H-m.b,m.t);
axes(s,W,H,m,x,y,"NPV ($)","futures",M,v=>F(v,0));h.counts.forEach((c,i)=>{const b=sv("rect",{x:x(h.edges[i])+1,y:y(c),width:Math.max(1,x(h.edges[i+1])-x(h.edges[i])-2),height:H-m.b-y(c),fill:"var(--s1)",rx:2});
b.onmousemove=e=>showTip(e,`${M(h.edges[i])} – ${M(h.edges[i+1])}<br>${c} futures (${P(c/r.iterations)})`);b.onmouseleave=hideTip;s.append(b)});
[["P10",r.npv.p10,10],["P50",r.npv.p50,22],["P90",r.npv.p90,10],["plan",r.reference.npv,34]].forEach(([l,v,dy])=>{const xv=Math.min(x(v),W-m.r);s.append(sv("line",{x1:xv,x2:xv,y1:m.t,y2:H-m.b,stroke:l=="plan"?"var(--s2)":"var(--ink-2)","stroke-dasharray":l=="plan"?"":"4 3"}));const right=xv>W-80;const t=sv("text",{x:right?xv-3:xv+3,y:m.t+dy,class:"ink","text-anchor":right?"end":"start"});t.textContent=l;s.append(t)});return s}
function cdf(){const W=600,H=280,m={l:46,r:16,t:12,b:40};const s=svgBox(W,H);const all=sel.map(id=>riskOf(id)).filter(Boolean);if(!all.length)return s;
const mx=Math.max(...all.map(r=>r.cdf_completion.months.length));const x=lin(1,mx,m.l,W-m.r),y=lin(0,1,H-m.b,m.t);axes(s,W,H,m,x,y,"month","P(done)",v=>month(v),P);
s.append(sv("line",{x1:x(D.horizon),x2:x(D.horizon),y1:m.t,y2:H-m.b,stroke:"var(--muted)","stroke-dasharray":"4 3"}));
sel.forEach((id,k)=>{const r=riskOf(id);if(!r)return;const pts=r.cdf_completion.months.map((mm,i)=>`${x(mm)},${y(r.cdf_completion.p[i])}`).join(" ");s.append(sv("polyline",{points:pts,fill:"none",stroke:SLOT[k],"stroke-width":2}))});
const d=el("div");d.append(s,el("div",{class:"legend"},sel.map((id,i)=>`<span><i style="background:${SLOT[i]}"></i>${name(id)}</span>`).join("")+"<span>dashed = end of horizon</span>"));return d}
// ---------------------------------------------------------------- tab: execution
function rExec(){const t=$("#t-execution");t.innerHTML="";const a=A[focus],r=riskOf(focus);const c1=el("div",{class:"card"},`<h2>Timeline — ${name(focus)}: planned (bar) and simulated P50–P90 finish (whisker) <span class="badge b-sim">SIM</span></h2>`);
c1.append(gantt(a,r));t.append(c1);const c2=el("div",{class:"card",style:"margin-top:14px"},`<h2>Monthly capacity utilization by pool — ${name(focus)}</h2>`);c2.append(heat(a));t.append(c2)}
function gantt(a,r){const ids=a.selected.slice().sort((p,q)=>a.starts[p]-a.starts[q]);const T=Math.max(D.horizon,...ids.map(i=>((r||{}).project_finish||{})[i]?.p90||0));
const W=1100,rh=24,m={l:260,r:20,t:24,b:30},H=m.t+m.b+ids.length*rh;const s=svgBox(W,H);const x=lin(0.5,T+0.5,m.l,W-m.r);
for(let k=1;k<=T;k++){s.append(sv("line",{x1:x(k-.5),x2:x(k-.5),y1:m.t,y2:H-m.b,class:"gl"}));if(k%2==1||T<=14){const tt=sv("text",{x:x(k),y:m.t-8,"text-anchor":"middle"});tt.textContent=month(k).replace(" 20","'");s.append(tt)}}
s.append(sv("line",{x1:x(D.horizon+.5),x2:x(D.horizon+.5),y1:m.t,y2:H-m.b,stroke:"var(--muted)","stroke-dasharray":"4 3"}));
ids.forEach((i,k)=>{const p=D.projects[i],st=a.starts[i],en=st+p.duration-1,y=m.t+k*rh;const lab=sv("text",{x:m.l-6,y:y+16,"text-anchor":"end"});lab.textContent=`${i} ${p.name.slice(0,28)}`;s.append(lab);
const b=sv("rect",{x:x(st-.5),y:y+5,width:x(en+.5)-x(st-.5),height:rh-10,rx:3,fill:p.mandatory||p.compliance||p.critical?"var(--s2)":"var(--s1)"});
const f=((r||{}).project_finish||{})[i];b.onmousemove=e=>showTip(e,`<b>${i} ${p.name}</b><br>${D.lobs[p.lob].name}<br>planned ${month(st)} – ${month(en)}`+(f?`<br>simulated finish P50 ${month(f.p50)}, P90 ${month(f.p90)}<br>P(done in horizon) ${P(f.within_horizon)}`:""));b.onmouseleave=hideTip;s.append(b);
if(f){s.append(sv("line",{x1:x(f.p50+.5),x2:x(f.p90+.5),y1:y+rh/2,y2:y+rh/2,stroke:"var(--ink)","stroke-width":1.5}));s.append(sv("circle",{cx:x(f.p50+.5),cy:y+rh/2,r:3,fill:"var(--ink)"}));s.append(sv("line",{x1:x(f.p90+.5),x2:x(f.p90+.5),y1:y+6,y2:y+rh-6,stroke:"var(--ink)"}))}});
const d=el("div",{style:"overflow:auto"});d.append(s,el("div",{class:"legend"},`<span><i style="background:var(--s1)"></i>discretionary</span><span><i style="background:var(--s2)"></i>mandatory / compliance / critical</span><span>● P50 finish — | P90 finish · dashed = horizon end</span>`));return d}
function heat(a){const pids=Object.keys(D.pools),T=D.horizon,W=1100,ch=20,m={l:260,r:70,t:24,b:10},cw=(W-m.l-m.r)/T,H=m.t+m.b+pids.length*ch;const s=svgBox(W,H);
const seq=["#cde2fb","#9ec5f4","#6da7ec","#3987e5","#256abf","#184f95","#0d366b"];
for(let k=1;k<=T;k++){const tt=sv("text",{x:m.l+(k-.5)*cw,y:m.t-8,"text-anchor":"middle"});tt.textContent=month(k).slice(0,3);s.append(tt)}
pids.forEach((pid,j)=>{const p=D.pools[pid],y=m.t+j*ch;const lab=sv("text",{x:m.l-6,y:y+14,"text-anchor":"end"});lab.textContent=`${pid} · ${p.capability.slice(0,18)}`;s.append(lab);let pk=0;
for(let k=1;k<=T;k++){const av=p.available[k]||0,u=(a.capability_allocations[pid]||{})[k]||0,v=av?u/av:0;pk=Math.max(pk,v);const c=sv("rect",{x:m.l+(k-1)*cw+1,y:y+1,width:cw-2,height:ch-2,rx:2,fill:av?seq[Math.min(6,Math.floor(v*6.999))]:"var(--grid)"});
c.onmousemove=e=>showTip(e,`<b>${pid}</b> ${p.capability} (${p.type}, ${D.lobs[p.owner]?.name||p.owner})<br>${month(k)}: ${F(u,0)} of ${F(av,0)} h (${P(v)})`);c.onmouseleave=hideTip;s.append(c)}
const pt=sv("text",{x:W-m.r+6,y:y+14,class:"ink"});pt.textContent="peak "+P(pk);s.append(pt)});
const d=el("div",{style:"overflow:auto"});d.append(s,el("div",{class:"note"},"Deterministic plan allocation (solver output). Darker = higher utilization; grey = pool not available that month."));return d}
// ---------------------------------------------------------------- tab: scenarios
function rScen(){const t=$("#t-scenarios");t.innerHTML="";const c=el("div",{class:"card"},"<h2>What-if scenarios (each re-optimized and re-simulated)</h2>");const tb=el("table");
const rb=riskOf(D.recommended);tb.innerHTML=`<tr><th>Scenario</th><th>Levers</th><th>Best plan in scenario</th><th class=n>E[NPV]</th><th class=n>P10 NPV</th><th class=n>P(within budget)</th><th class=n>P(dates)</th><th>Today's recommendation here</th><th>Model version</th></tr>
<tr class=pick><td>Baseline</td><td>—</td><td>${name(D.recommended)}</td><td class=n>${M(rb.npv.mean)}</td><td class=n>${M(rb.npv.p10)}</td><td class=n>${P(rb.probabilities.within_budget.p)}</td><td class=n>${P(rb.probabilities.meet_key_dates.p)}</td><td>—</td><td class=small>${D.model_version}</td></tr>`+
D.scenarios.map(s=>{if(s.status!="ok")return `<tr><td>${s.name}</td><td>${s.description}</td><td colspan=6 class=bad>${s.status}: ${s.note}</td><td class=small>${s.model_version}</td></tr>`;const r=s.recommended,b=s.baseline_plan||{};
return `<tr><td><b>${s.name}</b><br><span class=small>${s.description}</span></td><td class=small>${Object.entries(s.levers).map(([k,v])=>k.replace(/_/g," ")+": "+JSON.stringify(v)).join("<br>")}</td><td>${r.name}<br><span class=small>${r.selected.length} initiatives · ${r.profile.replace("_"," ")}</span></td><td class=n>${M(r.expected_npv)}</td><td class=n>${M(r.p10_npv)}</td><td class=n>${P(r.within_budget)}</td><td class=n>${P(r.meet_key_dates)}</td>
<td>${b.feasible?`<span class=ok>still feasible</span><br>E[NPV] ${M(b.expected_npv)} · P(budget) ${P(b.within_budget)}`:`<span class=bad>no longer feasible</span>`}</td><td class=small>${s.model_version}</td></tr>`}).join("");
c.append(tb,el("div",{class:"note"},"Changing an assumption changes the model fingerprint; the optimization, frontier, Monte Carlo and recommendation are recomputed for it (and cached by fingerprint)."));t.append(c)}
// ---------------------------------------------------------------- tab: assurance
function rAssure(){const t=$("#t-assurance");t.innerHTML="";const g=el("div",{class:"grid g2"});
const sc=(rep,title)=>{const c=el("div",{class:"card"},`<h2>${title} <span class="badge b-proven">${rep.level.replace(/_/g," ")}</span></h2><div class=note>${rep.verdict}</div>`);const tb=el("table");
tb.innerHTML="<tr><th>Engine</th><th>Role</th><th>Status</th><th>Proof</th><th class=n>Objective</th><th class=n>Bound</th><th class=n>Gap</th><th>Validated</th><th class=n>s</th></tr>"+rep.runs.map(r=>`<tr><td>${r.engine}</td><td>${r.role}</td><td>${r.status}</td><td>${r.proof}</td><td class=n>${F(r.objective,1)}</td><td class=n>${F(r.bound,1)}</td><td class=n>${r.gap==null?"—":(r.gap*100).toFixed(2)+"%"}</td><td>${r.validated_feasible==null?"—":(r.validated_feasible?'<span class=ok>yes</span>':'<span class=bad>no</span>')}</td><td class=n>${F(r.runtime_s,1)}</td></tr>`).join("");
c.append(tb);c.append(el("div",{class:"note"},`Size: ${F(rep.size.variables,0)} variables (${F(rep.size.integer_variables,0)} integer), ${F(rep.size.constraints,0)} constraints. Certified gap across engines: ${rep.certified_gap==null?"—":(rep.certified_gap*100).toFixed(2)+"%"}.`+(rep.diagnostics.length?"<br><b>Diagnostics:</b> "+rep.diagnostics.join("; "):"")));return c};
g.append(sc(D.solver_confidence,"Baseline optimization"));if(D.recommended_confidence)g.append(sc(D.recommended_confidence,"Recommended plan, independently re-checked"));
const r=riskOf(focus)||{};const cv=r.convergence||{};const c3=el("div",{class:"card"},`<h2>Monte Carlo convergence — ${name(focus)} <span class="badge b-sim">${cv.converged?"CONVERGED":"NOT CONVERGED"}</span></h2>`);
if(cv.trace){const W=560,H=240,m={l:60,r:16,t:12,b:40};const s=svgBox(W,H);const tr=cv.trace;const x=lin(Math.log10(tr[0].n),Math.log10(tr[tr.length-1].n),m.l,W-m.r);const lo=Math.min(...tr.map(p=>p.mean-2*p.se)),hi=Math.max(...tr.map(p=>p.mean+2*p.se));const y=lin(lo,hi,H-m.b,m.t);
axes(s,W,H,m,x,y,"iterations (log scale)","running mean NPV",v=>F(Math.pow(10,v),0),M);s.append(sv("polygon",{points:tr.map(p=>`${x(Math.log10(p.n))},${y(p.mean+2*p.se)}`).concat(tr.slice().reverse().map(p=>`${x(Math.log10(p.n))},${y(p.mean-2*p.se)}`)).join(" "),fill:"var(--s1)","fill-opacity":.15}));
s.append(sv("polyline",{points:tr.map(p=>`${x(Math.log10(p.n))},${y(p.mean)}`).join(" "),fill:"none",stroke:"var(--s1)","stroke-width":2}));c3.append(s)}
c3.append(el("div",{class:"note"},`SE of mean NPV ${M(cv.se_mean_npv)} (${P(cv.rel_se)} relative; criterion ${P(cv.criteria?.rel_se)}); worst probability SE ${F(cv.max_probability_se,4)} (criterion ${cv.criteria?.prob_se}). Seed ${r.seed}, ${r.chunks} chunks, ${F(r.throughput,0)} iterations/s. More iterations reduce sampling error only — not assumption error.`));g.append(c3);
const as=D.assumptions;const c4=el("div",{class:"card"},`<h2>Model assumptions <span class="badge ${as.calibrated?"b-proven":"b-est"}">${as.calibrated?"CALIBRATED":"ESTIMATE"}</span></h2>`+"<ul>"+as.notes.map(n=>"<li>"+n+"</li>").join("")+"</ul>");
const tb=el("table");tb.innerHTML="<tr><th>Driver</th><th>Distribution</th><th>Effect</th><th>Applies to</th><th>Source</th></tr>"+as.drivers.map(d=>`<tr><td>${d.name}</td><td>${d.distribution}</td><td>${d.effect} (${d.mode}${d.magnitude?" ×"+d.magnitude:""})</td><td>${d.applies_to}</td><td>${d.source}</td></tr>`).join("");c4.append(tb);
c4.append(el("div",{class:"note"},`${as.correlations.length} driver correlations (Gaussian copula; matrix ${as.repaired_correlation?"repaired to nearest valid":"validated positive semi-definite"}). Simulation: ${as.simulation.mode} plan, ${F(as.simulation.iterations,0)} iterations, VaR/CVaR at ${P(as.simulation.var_alpha)}, budget reserve ${P(as.simulation.budget_tolerance)}, date grace ${as.simulation.date_grace_months} month(s).`));g.append(c4);
const c5=el("div",{class:"card"},"<h2>Engines and run</h2>");const te=el("table");te.innerHTML="<tr><th>Engine</th><th>Role</th><th>Requirement</th><th>Installed</th></tr>"+D.engines.map(e=>`<tr><td>${e.engine}</td><td>${e.role}</td><td>${e.requirement}</td><td>${e.available?'<span class=ok>yes</span>':"no"}</td></tr>`).join("");c5.append(te);
c5.append(el("div",{class:"note"},`Timings (s): ${Object.entries(D.timings).map(([k,v])=>k.replace(/_/g," ")+" "+F(v,1)).join(" · ")}. Cache: ${D.cache.hits} hits / ${D.cache.misses} misses. Stochastic MILP: ${D.stochastic.status||"—"} (${D.stochastic.scenarios||0} scenarios, λ ${D.stochastic.lambda}); ${D.stochastic.limitation||""}`));g.append(c5);
const c6=el("div",{class:"card"},"<h2>Frontier run log</h2><ul>"+D.frontier.log.map(l=>"<li>"+l+"</li>").join("")+"</ul>"+(D.frontier.infeasible.length?`<div class=note>${D.frontier.infeasible.length} epsilon/weight combinations without a feasible portfolio, e.g. ${JSON.stringify(D.frontier.infeasible[0].epsilon||D.frontier.infeasible[0].weights)}</div>`:""));g.append(c6);
t.append(g)}
fillFocus();fillChips();show("choices");
</script></body></html>"""
