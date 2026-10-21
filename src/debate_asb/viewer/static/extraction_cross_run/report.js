"use strict";
const DATA = JSON.parse(document.getElementById("report-data").textContent);
const $ = id => document.getElementById(id);
const esc = x => String(x ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const sum = a => a.reduce((x,y)=>x+y,0);
const avg = a => a.length ? sum(a)/a.length : null;
const med = a => {a=[...a].sort((a,b)=>a-b);return a.length ? (a[Math.floor((a.length-1)/2)]+a[Math.ceil((a.length-1)/2)])/2 : null;};
const pct = x => x == null ? "—" : (100*x).toFixed(1)+"%";
const dec = x => x == null ? "—" : x.toFixed(2);
const ratio = (a,b) => `${a}/${b} · ${b?pct(a/b):"—"}`;
const label = id => DATA.variants.find(v=>v.id===id)?.label || id;
const SID = [...new Set(DATA.samples.map(r=>r.sample_id))].sort();
const COLORS = {target:"var(--target)",known:"var(--known)",known_possible:"var(--possible)",paper:"var(--docs)",other:"var(--other)"};
const GROUP_LABELS = {target:"Target mechanism",known:"Known, verified signature",known_possible:"Possible catalog match",paper:"Paper-only",other:"Other / unclassified"};
const METRIC_LABELS = {coverage:"Source + config lines read (%)",coverage_verified:"Reference-verified source + config lines (%)",coverage_search:"Source + config lines exposed, reads + search (%)",source_coverage:"Source lines read (%)",file_coverage:"Source + config files opened (%)",target_read_events:"Calls returning target-region code (calls)",target_repetitions_per_line:"Target presentations / distinct target line (×)",same_agent_target_repeats:"Repeated target lines within one agent (line presentations)",target_region_fraction:"Target-region lines read (%)"};
const percentageMetric = x => ["coverage","coverage_verified","coverage_search","source_coverage","file_coverage","target_region_fraction"].includes(x);
let activeRow = null;
let issueFilter = null;
const EVIDENCE_FILTERS = ["browse-group","browse-bug","browse-paper","browse-catalog","browse-strength"];
function clearBundleFilters(){for(const id of EVIDENCE_FILTERS)$(id).value="all";$("browse-query").value="";issueFilter=null;}

function options(id, values, first) {
  $(id).innerHTML = (first ? `<option value="all">${esc(first)}</option>` : "")+values.map(v=>`<option value="${esc(v.id ?? v)}">${esc(v.label ?? v)}</option>`).join("");
}
function originalMode(){ return $("score-filter").value === "original"; }
function outcome(r){return originalMode() ? r.original_included === "yes" : r.captured;}
function targetBundle(r,b){return originalMode() ? r.original_included === "yes" && r.original_matching.includes(b.number) : b.target;}
function groupFor(r,b){if(targetBundle(r,b))return "target";const kinds=(b.known_issue_matches||[]).map(m=>m.match_kind);return kinds.includes("code_match")?"known":kinds.length?"known_possible":b.excerpts.length&&b.excerpts.every(e=>e.path.split("/").pop().toLowerCase()==="paper.md")?"paper":"other";}
function selected(){return DATA.samples.filter(r=>($("variant-filter").value==="all"||r.variant===$("variant-filter").value)&&($("codebase-filter").value==="all"||r.sample_id===$("codebase-filter").value));}
function eligible(rows){return rows.filter(r=>r.eligible);}
function measured(rows,metric){return rows.filter(r=>r.eligible&&r.trace_complete&&r[metric]!=null);}
function corr(xs,ys){if(xs.length<2)return null;const xm=avg(xs),ym=avg(ys);const xx=sum(xs.map(x=>(x-xm)**2)),yy=sum(ys.map(y=>(y-ym)**2));return xx>1e-20&&yy>1e-20?sum(xs.map((x,i)=>(x-xm)*(ys[i]-ym)))/Math.sqrt(xx*yy):null;}
function assoc(rows,metric,strata=[]){
  const rs=measured(rows,metric),groups=new Map();
  for(const r of rs){const key=strata.map(k=>r[k]).join("|");if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);}
  let xs=[],ys=[],informative=0;
  for(const group of groups.values()){
    const x=group.map(r=>r[metric]),y=group.map(r=>+outcome(r));
    if(strata.length){if(new Set(y).size>1&&Math.max(...x)!==Math.min(...x))informative++;const xm=avg(x),ym=avg(y);xs.push(...x.map(v=>v-xm));ys.push(...y.map(v=>v-ym));}else{xs.push(...x);ys.push(...y);}
  }
  return {r:corr(xs,ys),n:xs.length,groups:groups.size,informative};
}
function stat(value,name,detail=""){return `<div class="stat"><strong>${esc(value)}</strong><span>${esc(name)}</span>${detail?`<small>${esc(detail)}</small>`:""}</div>`;}
function associations(rows,metric){const pooled=assoc(rows,metric),within=assoc(rows,metric,["sample_id"]),cell=assoc(rows,metric,["sample_id","variant"]);return stat(dec(pooled.r),"Pooled correlation r",`${pooled.n} complete sample epochs`)+stat(dec(within.r),"Within codebase r",`${within.groups} groups · ${within.informative} with outcome + exposure variation`)+stat(dec(cell.r),"Within codebase × variant r",`${cell.groups} cells · ${cell.informative} informative`)+stat(`${valueLabel(med(measured(rows,metric).filter(outcome).map(r=>r[metric])),metric)} / ${valueLabel(med(measured(rows,metric).filter(r=>!outcome(r)).map(r=>r[metric])),metric)}`,"Median: capture / miss","Descriptive exposure, not an intervention effect");}
function valueLabel(x,metric){return x==null?"—":percentageMetric(metric)?pct(x):Number.isInteger(x)?String(x):x.toFixed(1);}
function bar(labelText,n,total,attrs=""){return `<div class="barrow"><button class="barlabel" ${attrs}>${esc(labelText)}</button><div class="track"><div class="fill" style="width:${total?100*n/total:0}%"></div></div><div class="barvalue">${n}/${total}</div></div>`;}
function table(headers,rows,cls=""){return `<table class="${cls}"><thead><tr>${headers.map(h=>`<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;}
function legend(){return `<div class="legend">${Object.keys(GROUP_LABELS).map(g=>`<span><i class="swatch" style="background:${COLORS[g]}"></i>${GROUP_LABELS[g]}</span>`).join("")}</div>`;}
function stacks(counts,total){return `<div class="track">${Object.keys(GROUP_LABELS).map(g=>`<span class="${g}-bg" style="width:${total?100*(counts[g]||0)/total:0}%" title="${esc(GROUP_LABELS[g])}: ${counts[g]||0}/${total}"></span>`).join("")}</div>`;}

function scatter(rows,metric,title){
  const rs=measured(rows,metric);if(!rs.length)return `<p class="empty">No complete measurements in this selection.</p>`;
  const W=660,H=270,L=56,R=18,T=32,B=58;
  const vals=rs.map(r=>r[metric]),max=Math.max(...vals,percentageMetric(metric)?1:0.01),min=0;
  let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}"><text x="${L}" y="17" class="plot-title">${esc(title)}</text>`;
  const xp=v=>L+(v-min)/(max-min)*(W-L-R),yp=v=>T+(1-v)*(H-T-B);
  for(const y of [0,1])svg+=`<line class="gridline" x1="${L}" x2="${W-R}" y1="${yp(y)}" y2="${yp(y)}"/><text x="${L-10}" y="${yp(y)+4}" text-anchor="end">${y}</text>`;
  for(let i=0;i<=4;i++){const x=max*i/4;svg+=`<line class="gridline" x1="${xp(x)}" x2="${xp(x)}" y1="${T}" y2="${H-B}"/><text x="${xp(x)}" y="${H-B+21}" text-anchor="middle">${percentageMetric(metric)?Math.round(x*100):max>20?Math.round(x):x.toFixed(1)}</text>`;}
  rs.forEach((r,i)=>{const jitter=((i*37%19)-9)*1.1;svg+=`<circle class="point ${outcome(r)?"":"miss"}" data-open="${esc(r.row_id)}" cx="${xp(r[metric])}" cy="${yp(+outcome(r))+jitter}" r="3.7" tabindex="0"><title>${esc(`${label(r.variant)} · ${r.sample_id} · epoch ${r.epoch}\n${valueLabel(r[metric],metric)} · ${outcome(r)?"capture":"miss"}`)}</title></circle>`;});
  svg+=`<text x="${(L+W-R)/2}" y="${H-10}" text-anchor="middle">${esc(METRIC_LABELS[metric])}</text><text transform="translate(13 ${(T+H-B)/2}) rotate(-90)" text-anchor="middle">Capture (0 / 1)</text></svg><div class="legend"><span><i class="swatch" style="background:var(--target)"></i>Captured</span><span><i class="swatch" style="background:var(--miss)"></i>Miss / partial</span></div>`;
  return svg;
}
function binRows(rows,metric){
  const rs=measured(rows,metric);if(!rs.length)return [];
  if(metric==="target_read_events")return [[0,0],[1,1],[2,3],[4,7],[8,Infinity]].map(([lo,hi])=>({label:hi===Infinity?`${lo}+`:lo===hi?`${lo}`:`${lo}–${hi}`,rows:rs.filter(r=>r[metric]>=lo&&r[metric]<=hi)})).filter(b=>b.rows.length);
  if(metric==="same_agent_target_repeats")return [[0,0],[1,25],[26,100],[101,Infinity]].map(([lo,hi])=>({label:hi===Infinity?`${lo}+`:lo===hi?`${lo}`:`${lo}–${hi}`,rows:rs.filter(r=>r[metric]>=lo&&r[metric]<=hi)})).filter(b=>b.rows.length);
  const sorted=rs.map(r=>r[metric]).sort((a,b)=>a-b),cuts=[0.25,0.5,0.75].map(q=>sorted[Math.ceil(sorted.length*q)-1]);
  const bins=Array.from({length:4},()=>[]);for(const r of rs){let i=0;while(i<3&&r[metric]>cuts[i])i++;bins[i].push(r);}
  return bins.filter(b=>b.length).map(b=>({label:`${valueLabel(Math.min(...b.map(r=>r[metric])),metric)}–${valueLabel(Math.max(...b.map(r=>r[metric])),metric)}`,rows:b}));
}
function binChart(rows,metric,title){
  const bins=binRows(rows,metric);if(!bins.length)return "";
  const W=490,H=270,L=52,R=16,T=40,B=62,bw=(W-L-R)/bins.length;
  let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}"><text class="plot-title" x="${L}" y="17">${esc(title)}</text>`;
  for(let y=0;y<=100;y+=25){const py=T+(100-y)/100*(H-T-B);svg+=`<line class="gridline" x1="${L}" x2="${W-R}" y1="${py}" y2="${py}"/><text x="${L-8}" y="${py+4}" text-anchor="end">${y}</text>`;}
  bins.forEach((b,i)=>{const n=b.rows.length,k=b.rows.filter(outcome).length,rate=k/n,h=rate*(H-T-B),x=L+i*bw+bw*.18;svg+=`<rect x="${x}" y="${H-B-h}" width="${bw*.64}" height="${h}" fill="var(--target)"><title>${esc(`${b.label}: ${ratio(k,n)}`)}</title></rect><text x="${x+bw*.32}" y="${H-B-h-8}" text-anchor="middle">${k}/${n}</text><text x="${x+bw*.32}" y="${H-B+20}" text-anchor="middle" style="font-size:10px">${esc(b.label)}</text>`;});
  return svg+`<text x="${(L+W-R)/2}" y="${H-9}" text-anchor="middle">${esc(metric==="target_read_events"?"Target-region read calls (calls)":metric==="same_agent_target_repeats"?"Repeated target lines (presentations)":"Observed exposure bins")}</text><text transform="translate(12 ${(T+H-B)/2}) rotate(-90)" text-anchor="middle">Capture rate (%)</text></svg>`;
}

function renderAnswers(rows){
  const e=eligible(rows),k=e.filter(outcome).length,changed=rows.filter(r=>r.original_included!==r.included).length,bundles=sum(rows.map(r=>r.bundles.length));
  $("headline-stats").innerHTML=stat(ratio(k,e.length),"Sample epochs with a capture",`${rows.length} selected retained slots`)+stat(bundles.toLocaleString(),"Retained evidence bundles",`${new Set(rows.map(r=>r.variant)).size} variants · ${new Set(rows.map(r=>r.sample_id)).size} codebases`)+stat(changed,"Sample-epoch scoring corrections","Original labels remain selectable")+stat(rows.filter(r=>!outcome(r)&&r.eligible&&r.target_read_events>0).length,"Misses after target-region code was read","Location proxy; inspect mechanism context below");
  const cm=$("coverage-metric").value,wm=assoc(rows,cm,["sample_id"]),pm=assoc(rows,cm),rr=assoc(rows,"target_read_events",["sample_id","variant"]),sr=assoc(rows,"same_agent_target_repeats",["sample_id","variant"]);
  const h=hotspots(rows),worst=h.slice(0,3).map(r=>`${r.sid} (${r.k}/${r.n})`).join(", ");
  const counts=bundleCounts(rows);
  $("findings").innerHTML=`<div class="finding"><strong>Coverage: pooled and within-codebase associations.</strong><p>Pooled r = ${dec(pm.r)}; within-codebase r = ${dec(wm.r)}. These runs ${wm.r!=null&&wm.r<=0?"do not show a positive within-codebase relationship between broader reading and capture":"show an observational association, without identifying the effect of forcing more reading"}.</p></div><div class="finding"><strong>Repeated exposure is not the same as one agent rereading.</strong><p>Within codebase × variant, target-region read-call r = ${dec(rr.r)}; same-agent repeated-line r = ${dec(sr.r)}. This does not establish that requiring rereads improves performance.</p></div><div class="finding"><strong>The most persistent misses are directly inspectable.</strong><p>${esc(worst)}. The matrix separates occasional success across epochs from reliable success in every epoch.</p></div><div class="finding"><strong>Retained bundle allocation.</strong><p>${pct(1-(counts.target||0)/Math.max(1,bundles))} of slots are non-target. ${counts.known||0} contain verified catalog signatures; ${counts.known_possible||0} have possible matches. The remaining material is not automatically a defect or a distraction.</p></div>`;
  $("variant-bars").innerHTML=DATA.variants.filter(v=>rows.some(r=>r.variant===v.id)).map(v=>{const group=eligible(rows.filter(r=>r.variant===v.id));return bar(v.label,group.filter(outcome).length,group.length,`data-variant="${esc(v.id)}"`);}).join("");
}
function renderExposure(rows){
  const metric=$("coverage-metric").value;
  $("coverage-stats").innerHTML=associations(rows,metric);
  $("coverage-scatter").innerHTML=scatter(rows,metric,"Capture versus code coverage");
  $("coverage-bins").innerHTML=binChart(rows,metric,"Capture rate by coverage bin");
  const groups=SID.map(sid=>{const rs=rows.filter(r=>r.sample_id===sid),a=assoc(rs,metric);return {sid,rs,a};}).filter(x=>x.rs.length);
  $("coverage-by-codebase").innerHTML=`<details><summary>Inspect correlations and exposure ranges by codebase (${groups.length})</summary>`+table(["Codebase","Capture / epochs","Median coverage: capture / miss","Observed range","Within-codebase r"],groups.map(({sid,rs,a})=>[`<button class="linkbutton" data-codebase="${esc(sid)}">${esc(sid)}</button>`,ratio(eligible(rs).filter(outcome).length,eligible(rs).length),`${valueLabel(med(measured(rs,metric).filter(outcome).map(r=>r[metric])),metric)} / ${valueLabel(med(measured(rs,metric).filter(r=>!outcome(r)).map(r=>r[metric])),metric)}`,`${valueLabel(Math.min(...rs.map(r=>r[metric]).filter(x=>x!=null)),metric)}–${valueLabel(Math.max(...rs.map(r=>r[metric]).filter(x=>x!=null)),metric)}`,`${dec(a.r)} <span class="muted">n=${a.n}</span>`]))+`</details>`;
  const rm=$("repeat-metric").value;
  $("repeat-stats").innerHTML=associations(rows,rm);
  $("repeat-scatter").innerHTML=scatter(rows,rm,"Capture versus target-code exposure");
  $("repeat-bins").innerHTML=binChart(rows,rm,"Capture rate by exposure frequency");
  const repeatedlyMissed=eligible(rows).filter(r=>!outcome(r)&&r.target_read_events>=4).sort((a,b)=>b.target_read_events-a.target_read_events).slice(0,6);
  $("repeat-evidence").innerHTML=repeatedlyMissed.length?`<h3>Misses despite repeated target-region reads</h3><div class="pillrow">${repeatedlyMissed.map(r=>`<button data-open="${esc(r.row_id)}">${esc(r.sample_id)} · ${esc(label(r.variant))} · e${r.epoch} · ${r.target_read_events} calls</button>`).join("")}</div>`:"";
}
function hotspots(rows){
  return [...new Set(rows.map(r=>r.sample_id))].map(sid=>{const group=rows.filter(r=>r.sample_id===sid),e=eligible(group),cells=DATA.variants.map(v=>{const rs=group.filter(r=>r.variant===v.id),ee=eligible(rs),k=ee.filter(outcome).length;return {id:v.id,rs,n:ee.length,k,all:rs.length>0&&rs.length===ee.length&&k===rs.length};}).filter(c=>c.rs.length);return {sid,rs:group,n:e.length,k:e.filter(outcome).length,cells,any:cells.filter(c=>c.k>0).length,all:cells.filter(c=>c.all).length};}).sort((a,b)=>a.k/Math.max(1,a.n)-b.k/Math.max(1,b.n)||a.sid.localeCompare(b.sid));
}
function renderHotspots(rows){
  const hs=hotspots(rows),vs=DATA.variants.filter(v=>rows.some(r=>r.variant===v.id));
  $("hotspot-matrix").innerHTML=table(["Codebase",...vs.map(v=>esc(v.label)),"All epochs","Runs: any epoch","Runs: every epoch"],hs.map(h=>[`<button class="linkbutton strong" data-codebase="${esc(h.sid)}">${esc(h.sid)}</button>`,...vs.map(v=>{const c=h.cells.find(c=>c.id===v.id);return c?`<div class="cell"><button class="ratio" data-cell="${esc(h.sid)}|${esc(v.id)}">${c.k}/${c.n}</button><div class="epochdots">${c.rs.map(r=>`<button class="dot ${outcome(r)?"hit":""}" data-open="${esc(r.row_id)}" title="Epoch ${r.epoch}: ${outcome(r)?"capture":"miss"}"></button>`).join("")}</div></div>`:"—";}),ratio(h.k,h.n),ratio(h.any,h.cells.length),ratio(h.all,h.cells.length)]),"matrix");
  const misses=eligible(rows).filter(r=>!outcome(r));const stages=[['No target-region read',misses.filter(r=>!r.target_read_events&&r.trace_complete).length],['Read target region; not retained',misses.filter(r=>r.target_read_events&&r.trace_complete).length],['Incomplete trace / unresolved exposure',misses.filter(r=>!r.trace_complete).length]];
  $("stage-bars").innerHTML=stages.map(([l,n])=>bar(l,n,misses.length)).join("");
  const zero=rows.filter(r=>r.no_qualifying_evidence),diags=new Map();for(const r of rows)for(const d of r.diagnostics||[]){if(!diags.has(d.code))diags.set(d.code,new Set());diags.get(d.code).add(r.row_id);}
  $("diagnostics").innerHTML=`<details><summary>Qualification outcomes and trace diagnostics</summary><p>${rows.filter(r=>!r.parsed).length} failed retained outputs; ${zero.length} completed zero-candidate outputs (included as capture misses). Accepted format retries are already reflected in retained slots; original failed attempts are outside this ${DATA.samples.length}-slot aggregate.</p>${zero.map(r=>`<button data-open="${esc(r.row_id)}">${esc(r.sample_id)} · ${esc(label(r.variant))} · e${r.epoch}</button>`).join(" ")}<div class="slot-grid">${[...diags].map(([name,rs])=>`<span class="slot">${esc(name)}: ${rs.size} sample epochs</span>`).join("")}</div><p>Diagnostic codes may overlap. Read-stage locations and first-target timing are inspectable per sample. Worker tool order is log order and is not a shared causal sequence.</p></details>`;
}
function bundleCounts(rows){const counts={};for(const r of rows)for(const b of r.bundles){const g=groupFor(r,b);counts[g]=(counts[g]||0)+1;}return counts;}
function renderAttention(rows){
  const counts=bundleCounts(rows),n=sum(Object.values(counts));
  $("attention-total").innerHTML=`<h3>Retained bundle allocation · ${n} bundles</h3>${stacks(counts,n)}${legend()}<div class="stats compact">${stat(ratio(counts.target||0,n),"Target-bearing bundle slots")}${stat(ratio(counts.known||0,n),"Non-target catalog signature slots")}${stat(ratio(counts.known_possible||0,n),"Possible catalog matches","Unverified quote / location hint")}${stat(ratio(counts.other||0,n),"Other or unclassified slots","Not automatically bugs or distractors")}</div>`;
  $("attention-variants").innerHTML=table(["Variant","Bundle allocation (same legend)","Target / all slots","Known verified / non-target slots","No bug established + benign","No bug established + paper-consistent concern"],DATA.variants.filter(v=>rows.some(r=>r.variant===v.id)).map(v=>{const rs=rows.filter(r=>r.variant===v.id),cs=bundleCounts(rs),total=sum(Object.values(cs)),bs=rs.flatMap(r=>r.bundles);return [`<button class="linkbutton" data-variant="${esc(v.id)}">${esc(v.label)}</button>`,stacks(cs,total),ratio(cs.target||0,total),ratio(cs.known||0,total-(cs.target||0)),ratio(bs.filter(b=>b.review.bug_status==="no"&&b.review.stance==="benign").length,total),ratio(bs.filter(b=>b.review.bug_status==="no"&&b.review.paper_alignment==="consistent"&&b.review.stance==="concern").length,total)];}));
  $("attention-codebases").innerHTML=`<details><summary>Evidence allocation by codebase, including persistent failures</summary>`+table(["Codebase","Capture / epochs","Bundle allocation","Target / slots","Known verified / non-target slots","Possible catalog / slots"],hotspots(rows).map(h=>{const cs=bundleCounts(h.rs),total=sum(Object.values(cs));return [`<button class="linkbutton" data-codebase="${esc(h.sid)}">${esc(h.sid)}</button>`,ratio(h.k,h.n),stacks(cs,total),ratio(cs.target||0,total),ratio(cs.known||0,total-(cs.target||0)),ratio(cs.known_possible||0,total)];}))+`</details>`;
  const issues=new Map();
  for(const r of rows)for(const b of r.bundles)for(const m of b.known_issue_matches||[]){if(m.match_kind!=="code_match")continue;const key=r.sample_id+"|"+m.id;if(!issues.has(key))issues.set(key,{sid:r.sample_id,id:m.id,title:m.title,category:m.category,n:0,miss:0,variants:new Set()});const x=issues.get(key);x.n++;x.miss+=!outcome(r);x.variants.add(r.variant);}
  $("issue-table").innerHTML=table(["Codebase / known issue","Category","Bundles","In missed epochs","Variants"],[...issues.values()].sort((a,b)=>b.n-a.n).slice(0,18).map(x=>[`<button class="linkbutton" data-issue="${esc(x.sid)}|${esc(x.id)}">${esc(x.sid)}<br>${esc(x.title)}</button>`,esc(x.category),x.n,x.miss,x.variants.size]));
  const rc={target:0,known:0,paper:0,other:0};for(const r of rows)for(const g of Object.keys(rc))rc[g]+=r.attention_lines[g]||0;const rt=sum(Object.values(rc));
  $("read-allocation").innerHTML=Object.keys(rc).map(g=>bar(g==="known"?"Catalog signature lines":g==="target"?"Target-region lines":g==="paper"?"PAPER.md lines":"Other returned lines",rc[g],rt)).join("");
  const a=assoc(rows,"known_read_share",["sample_id","variant"]);
  $("attention-association").innerHTML=`<p class="note">Known-signature browsing share versus capture: within codebase × variant r = <strong>${dec(a.r)}</strong> (n=${a.n}). These sparse markers do not measure the full time or tokens spent investigating an issue.</p>`;
  const slots=new Map(),topics=new Map();
  for(const r of rows)for(const b of r.bundles){if(!slots.has(b.number))slots.set(b.number,{n:0,target:0,known:0,known_possible:0,other:0});const slot=slots.get(b.number),g=groupFor(r,b);slot.n++;slot[g]=(slot[g]||0)+1;const key=r.sample_id+"|"+b.review.topic.toLowerCase();if(!topics.has(key))topics.set(key,{sid:r.sample_id,topic:b.review.topic,n:0,miss:0,variants:new Set()});const t=topics.get(key);t.n++;t.miss+=!outcome(r);t.variants.add(r.variant);}
  $("slot-topics").innerHTML=`<details><summary>Inspect bundle slots and recurring reviewed topics</summary><div class="split"><div><h3>Allocation by original bundle slot</h3><p class="caption">Only occupied slots enter each row; shortfalls are not invented as bundles. Worker bundle order follows hypothesis order.</p>${table(["Slot","Occupied","Target","Known verified","Possible catalog","Other / paper"],[...slots].sort((a,b)=>a[0]-b[0]).map(([n,s])=>[n,s.n,ratio(s.target,s.n),ratio(s.known,s.n),ratio(s.known_possible,s.n),ratio((s.other||0)+(s.paper||0),s.n)]))}</div><div><h3>Recurring exact reviewed topic labels</h3><p class="caption">Topics are inherited analyst labels, grouped within each codebase; near-synonyms are not silently merged. Click to inspect original bundles.</p>${table(["Codebase / topic","Bundles","In missed epochs","Variants"],[...topics.values()].sort((a,b)=>b.n-a.n).slice(0,15).map(t=>[`<button class="linkbutton" data-topic="${esc(t.sid)}|${esc(t.topic)}">${esc(t.sid)}<br>${esc(t.topic)}</button>`,t.n,t.miss,t.variants.size]))}</div></div></details>`;
}

function bundleMatches(r,b){
  const group=$("browse-group").value,bug=$("browse-bug").value,paper=$("browse-paper").value,category=$("browse-catalog").value,strength=$("browse-strength").value,q=$("browse-query").value.trim().toLowerCase();
  if(group==="related"&&!((r.audit.related_bundles||[]).includes(b.number)||r.original_included==="partial"&&r.original_matching.includes(b.number)))return false;
  if(group!=="all"&&group!=="related"&&groupFor(r,b)!==group)return false;
  if(bug!=="all"&&b.review.bug_status!==bug)return false;
  if(paper!=="all"&&b.review.paper_alignment!==paper)return false;
  if((category!=="all"||strength!=="all")&&!(b.known_issue_matches||[]).some(m=>(category==="all"||m.category===category)&&(strength==="all"||m.match_kind===strength)))return false;
  if(issueFilter&&!(r.sample_id===issueFilter.sid&&(b.known_issue_matches||[]).some(m=>m.id===issueFilter.id)))return false;
  if(q&&!JSON.stringify({observation:b.observation,topic:b.review.topic,reason:b.review.reason,matches:b.known_issue_matches}).toLowerCase().includes(q))return false;
  return true;
}
function browserRows(){
  return DATA.samples.filter(r=>{
    if($("browse-codebase").value!=="all"&&r.sample_id!==$("browse-codebase").value)return false;
    if($("browse-variant").value!=="all"&&r.variant!==$("browse-variant").value)return false;
    const o=$("browse-outcome").value;if(o==="miss"&&outcome(r)||o==="caught"&&!outcome(r)||o==="changed"&&r.original_included===r.included)return false;
    const filtering=EVIDENCE_FILTERS.some(id=>$(id).value!=="all")||$("browse-query").value.trim()||issueFilter;
    return !filtering||r.bundles.some(b=>bundleMatches(r,b));
  }).sort((a,b)=>SID.indexOf(a.sample_id)-SID.indexOf(b.sample_id)||DATA.variants.findIndex(v=>v.id===a.variant)-DATA.variants.findIndex(v=>v.id===b.variant)||a.epoch-b.epoch);
}
function renderBrowser(){
  const rows=browserRows();
  if(!rows.some(r=>r.row_id===activeRow))activeRow=rows[0]?.row_id||DATA.samples[0]?.row_id||null;
  $("sample-list").innerHTML=`<p class="caption">${rows.length} sample epochs match · click to inspect</p>`+rows.map(r=>`<button class="sample-item ${r.row_id===activeRow?"active":""}" data-sample="${esc(r.row_id)}">${esc(label(r.variant))}<small>${esc(r.sample_id)} · epoch ${r.epoch} · ${outcome(r)?"CAPTURE":r.included==="partial"?"RELATED ONLY":"MISS"} · ${r.bundles.length} bundles</small></button>`).join("");
  $("active-issue").innerHTML=issueFilter?`<div class="issue-active">Filtering issue <strong>${esc(issueFilter.id)}</strong> in ${esc(issueFilter.sid)}. <button id="clear-issue">Clear issue filter</button></div>`:"";
  const r=rows.find(r=>r.row_id===activeRow);
  if(!r){$("sample-detail").innerHTML='<p class="empty">No samples match these evidence filters. Clear filters to inspect all retained outputs.</p>';return;}
  const bs=r.bundles.filter(b=>bundleMatches(r,b));
  $("sample-detail").innerHTML=`<div class="sample-header"><h3>${esc(r.sample_id)} <span class="muted">/ epoch ${r.epoch}</span></h3><div>${esc(label(r.variant))} <span class="tag ${outcome(r)?"target":"related"}">${outcome(r)?"Captured mechanism":r.included==="partial"?"Related clue only":"Miss"}</span></div><div class="sample-meta"><span>Coverage ${pct(r.coverage)} · ${r.code_lines_read.toLocaleString()}/${r.code_lines_total.toLocaleString()} source/config lines</span><span>${r.target_read_events} target-region read calls</span><span>${r.same_agent_target_repeats??"unknown"} repeated target lines within agent</span><span>${pct(r.target_region_fraction)} of target-region lines read</span></div><p class="caption">Original capture: ${esc(r.original_included)} · original single-bundle completeness: ${r.complete==null?"unresolved":r.complete?"yes":"no"} · semantic exposure: ${esc(r.exposure)}.</p></div><details><summary>Checked target, original review, and scoring audit</summary><p><strong>Target:</strong> ${esc(DATA.target_units.samples[r.sample_id].mechanism)}</p><p><strong>Original review:</strong> ${esc(r.summary)}</p><p><strong>Audit:</strong> ${esc(r.audit.reason)}</p><p><strong>Original semantic exposure review:</strong> ${esc(r.exposure_summary)}</p><p><strong>Original failure stage:</strong> ${esc(r.failure_stage_original)}. ${esc((r.failure_modes||[]).join(", "))}</p><p><strong>Target-region source read ordinals:</strong> ${esc(r.target_read_refs.join(", "))}</p><pre>${esc(JSON.stringify(DATA.target_units.samples[r.sample_id].regions,null,2))}</pre></details>${r.no_qualifying_evidence?'<p class="note">Completed investigation with no qualifying evidence bundles. This remains a capture miss; it does not establish codebase validity.</p>':""}<p class="caption">${bs.length}/${r.bundles.length} bundles match the evidence filters. Observations and code below are the retained originals.</p>${bs.map(b=>renderBundle(r,b)).join("")}<details><summary>Full returned extraction tool trace (${r.tools.length} calls)</summary>${r.tools.map(t=>`<details><summary>#${t.ordinal} · ${esc(t.function)} · ${esc(JSON.stringify(t.arguments))}${t.failed?" · FAILED":""}</summary><pre>${esc(t.result)}</pre></details>`).join("")}</details>${r.investigation?`<details><summary>Coordinator and worker reports, including non-retained dispositions</summary><pre>${esc(JSON.stringify(r.investigation,null,2))}</pre></details>`:""}${r.hypotheses?`<details><summary>Original generated hypotheses</summary><pre>${esc(JSON.stringify(r.hypotheses,null,2))}</pre></details>`:""}<details><summary>Sample provenance and source-verification limitations</summary><pre>${esc(JSON.stringify({key:r.key,log:r.log,error:r.error,extraction_metadata:r.extraction_metadata,nominal_cost:r.nominal_cost,review_note:r.review_note,diagnostics:r.diagnostics,returned_text_mismatches:r.source_mismatches,audit:r.audit},null,2))}</pre></details>`;
}
function renderBundle(r,b){
  const group=groupFor(r,b),br=b.review,related=(r.audit.related_bundles||[]).includes(b.number);
  return `<article class="bundle"><div class="tags"><span class="tag ${group}">${esc(GROUP_LABELS[group])}</span>${related?'<span class="tag related">Related in original review</span>':""}<span class="tag">Bug: ${esc(br.bug_status)}</span><span class="tag">Candidate: ${esc(br.sabotage_candidate)}</span><span class="tag">Paper: ${esc(br.paper_alignment)}</span></div><h4>Bundle ${b.number} · ${esc(br.topic)}</h4><div class="observation">${esc(b.observation)}</div><p class="reason">Saved bundle review: ${esc(br.reason)}</p>${(b.known_issue_matches||[]).map(m=>`<details><summary>${esc(m.category)} · ${esc(m.title)} · ${esc(m.match_kind)}</summary><div class="annotation"><p>${esc(m.description)}</p><p><strong>Catalog impact claim:</strong> ${esc(m.impact)}</p><p>${esc(m.original_location)} · match annotation is not proof of empirical impact.</p><pre>${esc(JSON.stringify(m.evidence,null,2))}</pre></div></details>`).join("")}${b.excerpts.map((e,i)=>`<details ${targetBundle(r,b)&&i===0?"open":""}><summary>${esc(e.path)}:${e.start_line}–${e.end_line} <span class="muted">· ${esc(e.fidelity?.status||"unspecified fidelity")}</span></summary><pre>${esc(e.text)}</pre></details>`).join("")}<details><summary>Checked code / paper basis from saved semantic review</summary><pre>${esc(JSON.stringify(br.basis,null,2))}</pre></details></article>`;
}
function renderAudit(){
  $("scoring-context").innerHTML=`<p>${esc(DATA.audit_policy.capture||"Capture is a reviewed judgment of checked target implementation in retained evidence. Completeness is separate.")}</p>${DATA.audit_policy.awareness_lens?`<p>${esc(DATA.audit_policy.awareness_lens)}</p>`:""}${DATA.audit_policy.secondary?`<p>${esc(DATA.audit_policy.secondary)}</p>`:""}`;
  const changed=DATA.samples.filter(r=>r.original_included!==r.included);
  $("audit-table").innerHTML=table(["Variant","Codebase / epoch","Before → mechanism score","Matching bundles","Basis"],changed.map(r=>[`<button class="linkbutton" data-open="${esc(r.row_id)}">${esc(label(r.variant))}</button>`,`${esc(r.sample_id)} · e${r.epoch}`,`${esc(r.original_included)} → ${esc(r.included)}`,r.matching.join(", "),esc(r.audit.reason)]));
  const mismatchRows=DATA.samples.filter(r=>Object.keys(r.source_mismatches).length).length;
  $("provenance").innerHTML=`<p>${DATA.variants.length} selected variants · ${DATA.samples.length} unique log/sample/epoch slots · ${sum(DATA.samples.map(r=>r.bundles.length))} retained bundles.</p><p>Configured extractor models: ${esc([...new Set(DATA.variants.map(v=>v.settings.model||"unknown"))].join(", "))}. Providers: ${esc([...new Set(DATA.variants.map(v=>v.settings.provider||"unknown"))].join(", "))}. Logged settings, prompt hashes and missing seeds are retained.</p><p>${mismatchRows} sample epochs contain at least one returned-text/reference mismatch (including clipped or concatenated lines). The strict coverage option removes these line positions. This does not establish source drift.</p><p>Analysis generated ${esc(DATA.generated_at.slice(0,10))}. Extraction dates come from the saved logs, rather than report-directory names.</p><details><summary>Protocol budgets and source-reported extraction costs</summary>${table(["Variant","Inspection turns","Bundle cap","Recorded extraction spend"],DATA.variants.map(v=>[esc(v.label),v.settings.inspection_turn_budget,v.settings.bundle_cap,v.billed_cost==null?"Unknown":`$${v.billed_cost.toFixed(3)}`]))}<p>Budgets differ; worker terminal calls can exceed nominal turn allocations. Some protocols may retain fewer than the configured cap. Costs are historical source-report values, not analysis costs, and retry accounting differs.</p></details>`;
  $("policy").textContent=JSON.stringify({policy:DATA.audit_policy,target_regions:DATA.target_units},null,2);
  $("manifest").textContent=JSON.stringify(DATA.provenance,null,2);
}
function render(){const rows=selected();renderAnswers(rows);renderExposure(rows);renderHotspots(rows);renderAttention(rows);renderBrowser();}
function openSample(id){
  const r=DATA.samples.find(r=>r.row_id===id);if(!r)return;
  $("browse-codebase").value=r.sample_id;$("browse-variant").value="all";$("browse-outcome").value="all";clearBundleFilters();activeRow=id;renderBrowser();$("browser").scrollIntoView({behavior:"smooth"});
}
function openCodebase(sid,variant="all"){$("browse-codebase").value=sid;$("browse-variant").value=variant;$("browse-outcome").value="all";clearBundleFilters();activeRow=null;renderBrowser();$("browser").scrollIntoView({behavior:"smooth"});}
function download(name,content,type){const blob=new Blob([content],{type});const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
options("variant-filter",DATA.variants,`All ${DATA.variants.length} variants`);options("codebase-filter",SID,`All ${SID.length} codebases`);options("browse-variant",DATA.variants,"All variants");options("browse-codebase",SID,"All codebases");
$("browse-codebase").value=SID.includes("hop_jump")?"hop_jump":SID[0];
activeRow=DATA.samples.find(r=>r.sample_id==="hop_jump"&&r.variant==="validity_workers"&&r.epoch===1)?.row_id||DATA.samples[0]?.row_id||null;
const dates=DATA.provenance.sources.flatMap(s=>s.manifest.runs.map(r=>r.stats?.started_at?.slice(0,10))).filter(Boolean).sort();
$("scope-caption").textContent=`${DATA.variants.length} variants · ${SID.length} codebases · ${DATA.samples.length} retained sample epochs · ${dates[0]||""} to ${dates[dates.length-1]||""}`;
for(const id of ["variant-filter","codebase-filter","score-filter","coverage-metric","repeat-metric"])$(id).addEventListener("change",render);
for(const id of ["browse-codebase","browse-variant","browse-outcome",...EVIDENCE_FILTERS])$(id).addEventListener("change",()=>{activeRow=null;renderBrowser();});
$("browse-query").addEventListener("input",()=>{activeRow=null;renderBrowser();});
$("reset").addEventListener("click",()=>{$("variant-filter").value="all";$("codebase-filter").value="all";$("score-filter").value="revised";$("coverage-metric").value="coverage";$("repeat-metric").value="target_read_events";render();});
$("browser-reset").addEventListener("click",()=>{for(const id of ["browse-codebase","browse-variant","browse-outcome"])$(id).value="all";clearBundleFilters();activeRow=null;renderBrowser();});
document.addEventListener("click",e=>{
  const t=e.target.closest("[data-open],[data-sample],[data-codebase],[data-cell],[data-variant],[data-issue],[data-topic],#clear-issue");if(!t)return;
  if(t.dataset.open)openSample(t.dataset.open);
  else if(t.dataset.sample){activeRow=t.dataset.sample;renderBrowser();}
  else if(t.dataset.codebase)openCodebase(t.dataset.codebase);
  else if(t.dataset.cell){const [sid,v]=t.dataset.cell.split("|");openCodebase(sid,v);}
  else if(t.dataset.variant){$("variant-filter").value=t.dataset.variant;render();}
  else if(t.dataset.issue){const [sid,id]=t.dataset.issue.split("|");openCodebase(sid);issueFilter={sid,id};renderBrowser();}
  else if(t.dataset.topic){const [sid,topic]=t.dataset.topic.split("|");openCodebase(sid);$("browse-query").value=topic;renderBrowser();}
  else if(t.id==="clear-issue"){issueFilter=null;renderBrowser();}
});
document.addEventListener("keydown",e=>{if(e.key==="Enter"&&e.target.dataset?.open)openSample(e.target.dataset.open);});
$("download-data").addEventListener("click",()=>download("extraction-cross-run.json",JSON.stringify(DATA),"application/json"));
$("download-metrics").addEventListener("click",()=>{const fields=["variant","sample_id","epoch","captured","original_included","coverage","coverage_verified","target_read_events","target_repetitions_per_line","same_agent_target_repeats","target_region_fraction","known_read_share"];const quote=x=>'"'+String(x??"").replace(/"/g,'""')+'"';download("sample-metrics.csv",[fields.join(","),...DATA.samples.map(r=>fields.map(k=>quote(r[k])).join(","))].join("\n"),"text/csv");});
renderAudit();render();
