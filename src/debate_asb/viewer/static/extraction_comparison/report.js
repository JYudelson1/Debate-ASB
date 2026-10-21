"use strict";
const DATA = JSON.parse(document.getElementById("report-data").textContent);
const $ = id => document.getElementById(id);
const esc = x => String(x ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
const pct = (x, n) => n ? (100*x/n).toFixed(1)+"%" : "—";
const money = x => x == null ? "Unknown" : "$"+x.toFixed(3);
const ratio = (x, n) => `${x}/${n} <small>(${pct(x,n)})</small>`;
const badge = x => `<span class="pill ${esc(x)}">${esc(x)}</span>`;
const variants = DATA.variants;
const sampleIds = [...new Set(DATA.samples.map(s => s.sample_id))].sort();
document.title = DATA.title;
$("headline").textContent = DATA.title;
$("date").textContent = "Extraction prompt iteration"+(DATA.date ? " · "+DATA.date : "");
$("lead").textContent = DATA.description;
$("hypothesis").textContent = DATA.hypothesis;
$("meta").innerHTML = `<span><strong>${variants.length} variants</strong></span><span><strong>${sampleIds.length} codebases</strong></span><span><strong>${DATA.samples.length} recorded slots</strong></span>`;
$("designrows").innerHTML = variants.map(v => `<tr><td>${esc(v.label)}</td><td>${v.historical ? "Historical" : "New run"}</td><td>${Object.entries(v.settings).map(([k,x]) => `${esc(k)}: ${esc(typeof x === "object" ? JSON.stringify(x) : x)}`).join("<br>") || "See saved provenance"}</td></tr>`).join("");
$("prompts").innerHTML = variants.filter(v => v.prompt).map(v => `<details><summary>${esc(v.label)} · saved prompt</summary><pre>${esc(v.prompt)}</pre></details>`).join("");
$("bars").innerHTML = variants.map(v => {
  const groups = [[v.complete,"complete"],[v.presence-v.complete,"present"],[v.partial,"partial"],[v.attempts-v.presence-v.partial-v.failed,"miss"],[v.failed,"fail"]];
  return `<div class="barrow"><div class="barlabel">${esc(v.label)}</div><div class="bar" role="img" aria-label="${esc(v.label)}: ${v.presence} captured, ${v.complete} complete, ${v.failed} failed out of ${v.attempts}">${groups.map(([n,c]) => `<span class="${c}" style="width:${v.attempts ? 100*n/v.attempts : 0}%" title="${n} ${c}"></span>`).join("")}</div><strong>${pct(v.presence,v.attempts)}</strong></div>`;
}).join("");
$("resultrows").innerHTML = variants.map(v => `<tr><td><strong>${esc(v.label)}</strong>${v.historical ? "<small> · historical</small>" : ""}</td><td>${ratio(v.presence,v.eligible)}</td><td>${ratio(v.complete,v.sufficiency_eligible)}</td><td>${ratio(v.presence,v.attempts)}</td><td>${v.parsed}/${v.attempts}</td><td>${v.excluded}</td></tr>`).join("");
$("comparisons").innerHTML = DATA.comparisons.map(c => {
  const a = variants.find(v=>v.id===c.baseline), b = variants.find(v=>v.id===c.variant);
  const delta = c.jointly_reviewed_slots ? 100*(c.variant_presence-c.baseline_presence)/c.jointly_reviewed_slots : null;
  return `<p><strong>${esc(b.label)} vs ${esc(a.label)}:</strong> ${c.variant_presence}/${c.jointly_reviewed_slots} vs ${c.baseline_presence}/${c.jointly_reviewed_slots} captures on jointly reviewed slots${delta==null ? "" : ` (${delta>=0?"+":""}${delta.toFixed(1)} percentage points)`}. ${c.common_slots} slots share sample and epoch labels.</p>`;
}).join("");
$("notes").innerHTML = DATA.notes.map(x=>`<li>${esc(x)}</li>`).join("");
$("retentionflow").innerHTML = variants.map(v=>`<article class="strategy"><h3>${esc(v.label)}</h3><div class="flow"><span>${v.reviewed_exposure} exposures</span><i>→</i><span>${v.presence} retained</span><i>→</i><span>${v.complete} complete</span></div><p>${v.exposed_dropped} reviewed outputs expose the target but retain only related clues or miss it.</p></article>`).join("");
$("retryrows").innerHTML = variants.map(v=>`<tr><td>${esc(v.label)}</td><td>${v.first_pass_parsed==null ? "Unknown" : v.first_pass_parsed+"/"+v.attempts}</td><td>${v.parsed}/${v.attempts}</td><td>${v.retry_attempts??"Unknown"}</td></tr>`).join("");
$("matrixhead").innerHTML = `<tr><th>Codebase</th>${variants.map(v=>`<th>${esc(v.label)}</th>`).join("")}</tr>`;
$("matrix").innerHTML = sampleIds.map(id=>`<tr><td><code>${esc(id)}</code></td>${variants.map(v=>{
  const s=v.by_sample[id];
  return s ? `<td><button data-variant="${esc(v.id)}" data-sample="${esc(id)}"><span class="score"><strong>${s.presence}/${s.attempts}</strong><small>${s.complete} complete · ${s.parsed} parsed</small></span></button></td>` : "<td>—</td>";
}).join("")}</tr>`).join("");
$("condition").innerHTML = variants.map(v=>`<option value="${esc(v.id)}">${esc(v.label)}</option>`).join("");
function choices(id, values, previous) {
  $(id).innerHTML=values.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join("");
  if(values.map(String).includes(String(previous))) $(id).value=previous;
}
function updateSamples() {
  const values=[...new Set(DATA.samples.filter(s=>s.variant===$("condition").value).map(s=>s.sample_id))].sort();
  choices("sample",values,$("sample").value); updateEpochs();
}
function updateEpochs() {
  const values=DATA.samples.filter(s=>s.variant===$("condition").value&&s.sample_id===$("sample").value).map(s=>s.epoch).sort((a,b)=>a-b);
  choices("epoch",values,$("epoch").value); renderSample();
}
function selected() { return DATA.samples.find(s=>s.variant===$("condition").value&&s.sample_id===$("sample").value&&s.epoch===+$("epoch").value); }
function renderSample() {
  const s=selected();
  if(!s) { $("sampledetail").textContent="No saved extraction for this selection."; return; }
  const filtered=s.bundles.filter(b=>(!$("targetonly").checked||s.matching.includes(b.number))&&($("bugfilter").value==="all"||s.bundle_reviews[b.number].bug_status===$("bugfilter").value)&&($("paperfilter").value==="all"||s.bundle_reviews[b.number].paper_alignment===$("paperfilter").value));
  const state=!s.parsed?"Output failure":s.no_qualifying_evidence?"No qualifying evidence extracted":s.complete?"Complete target bundle":s.included==="yes"?"Target captured":s.included==="partial"?"Related clues only":s.included==="no"?"Target missed":s.included;
  let h=`<div class="detail"><div class="detailtop"><h3><code>${esc(s.sample_id)}</code> · epoch ${s.epoch}</h3><span class="status">${esc(state)}</span></div><p class="bundleobs">${esc(s.summary)}</p><p class="fine"><strong>Exposure:</strong> ${esc(s.exposure)} · ${esc(s.exposure_summary)} · ${s.model_calls??"Unknown"} model calls · nominal cost ${money(s.nominal_cost)}</p>`;
  if(s.error) h+=`<details><summary>Recorded sample error</summary><pre>${esc(JSON.stringify(s.error,null,2))}</pre></details>`;
  if(s.extraction_recovery) h+=`<details><summary>Saved JSON adapter recovery</summary><pre>${esc(JSON.stringify(s.extraction_recovery,null,2))}</pre></details>`;
  if(s.investigation) {
    const a=s.investigation;
    h+=`<details><summary>Coordinator survey and handoff briefs</summary><pre>${esc(JSON.stringify(a.coordinator,null,2))}</pre></details>`;
    for(const w of a.workers||[]) {
      const number=a.bundle_mapping?.[w.hypothesis_id];
      h+=`<details><summary>${esc(w.hypothesis_id)} · ${esc(w.report?.status||"execution/output failure")} · ${number==null?"no debate":`bundle ${number}`}</summary><pre>${esc(JSON.stringify(w,null,2))}</pre></details>`;
    }
    if(s.no_qualifying_evidence) h+='<p>No qualifying evidence extracted. This does not establish codebase validity; this completed extraction remains in the denominator.</p>';
  }
  h+=`<div class="tablewrap"><table><thead><tr><th>Bundle</th><th>Topic</th><th>Bug?</th><th>Viable candidate?</th><th>Paper</th></tr></thead><tbody>${filtered.map(b=>{
    const r=s.bundle_reviews[b.number]; return `<tr><td>${b.number}${s.sufficient.includes(b.number)?" · complete":s.matching.includes(b.number)?" · target / related":""}</td><td>${esc(r.topic)}</td><td>${badge(r.bug_status)}</td><td>${badge(r.sabotage_candidate)}</td><td>${badge(r.paper_alignment)}</td></tr>`;
  }).join("")}</tbody></table></div>`;
  for(const b of filtered) {
    const r=s.bundle_reviews[b.number];
    h+=`<details><summary>Bundle ${b.number} · ${esc(r.topic)}${s.sufficient.includes(b.number)?" · complete target":""}</summary><p class="bundleobs"><strong>Observation:</strong> ${esc(b.observation)}</p><p class="fine"><strong>Review:</strong> ${esc(r.reason)}</p>${(b.known_issue_matches||[]).map(m=>`<details><summary>Catalogued ${esc(m.category)} · ${esc(m.match_kind)} · ${esc(m.title||m.id)}</summary><p>${esc(m.description)}</p><p class="fine">Annotated impact: ${esc(m.impact)}</p><pre>${esc(JSON.stringify(m.evidence,null,2))}</pre></details>`).join("")}${b.excerpts.map(ex=>`<p class="fine"><code>${esc(ex.path)}${ex.start_line==null&&ex.end_line==null?" (lines unavailable)":`:${ex.start_line??"?"}–${ex.end_line??"?"}`}${ex.source_location?` · ${esc(ex.source_location)}`:""}</code> · fidelity ${esc(ex.fidelity?.status??"unknown")}</p><pre>${esc(ex.text)}</pre>`).join("")}<details><summary>Checked code and paper bases</summary><pre>${esc(JSON.stringify(r.basis,null,2))}</pre></details></details>`;
  }
  if(!filtered.length&&s.parsed&&!s.no_qualifying_evidence) h+='<p class="empty">No bundles match these filters.</p>';
  h+=`<details><summary>Checked target and limitations</summary><pre>${esc(JSON.stringify(s.reference,null,2))}</pre><p class="fine">${esc(s.review_note)}</p></details><details><summary>Executed tool sequence · ${s.tools.length} calls</summary>${s.tools.map(t=>`<details><summary>${t.ordinal}. ${esc(t.function)} ${esc(JSON.stringify(t.arguments))}${t.failed?" · failed":""}</summary><pre>${esc(t.result)}</pre></details>`).join("")}</details></div>`;
  $("sampledetail").innerHTML=h; renderHeat();
}
function renderHeat() {
  const s=selected(); if(!s) return;
  const h=s.heatmap, columns=Array.from({length:20},(_,i)=>h.files.reduce((n,f)=>n+f.counts[i],0));
  const maximum=Math.max(1,...h.files.flatMap(f=>f.counts));
  $("heatmap").innerHTML=`<table class="heatmap"><thead><tr><th class="filepath">${esc(s.sample_id)} · epoch ${s.epoch}</th><th colspan="6">START</th><th colspan="8">MIDDLE</th><th colspan="6">END</th></tr></thead><tbody>${h.files.slice(0,16).map(f=>`<tr><td class="filepath">${esc(f.path)}</td>${f.counts.map((n,i)=>{
    const value=$("heatmode").value==="share"?(columns[i]?n/columns[i]:0):n/maximum;
    return `<td class="bucket" tabindex="0" title="${esc(f.path)}, ${i*5}–${(i+1)*5}%: ${n} returned lines" style="background:rgba(23,132,113,${n?.1+.9*Math.sqrt(value):.025})"></td>`;
  }).join("")}</tr>`).join("")}</tbody></table>`;
  $("phases").innerHTML=h.phases.map((p,i)=>`<div><div class="eyebrow">${["Start","Middle","End"][i]}</div><p class="big">${p.read_lines.toLocaleString()} <small>source lines</small></p><dl><dt>Files read</dt><dd>${p.read_files}</dd><dt>New path/line pairs</dt><dd>${p.new_lines}</dd><dt>Top three read share</dt><dd>${p.top3_share==null?"—":pct(p.top3_share,1)}</dd><dt>Search / listing calls</dt><dd>${p.calls.search||0} / ${p.calls.list_files||0}</dd></dl></div>`).join("");
  $("inspectiondetails").textContent=JSON.stringify(s.metrics,null,2);
}
$("condition").addEventListener("change",updateSamples);
$("sample").addEventListener("change",updateEpochs);
for(const id of ["epoch","bugfilter","paperfilter","targetonly"]) $(id).addEventListener("change",renderSample);
$("heatmode").addEventListener("change",renderHeat);
$("matrix").addEventListener("click",e=>{
  const b=e.target.closest("button[data-variant]"); if(!b) return;
  $("condition").value=b.dataset.variant; updateSamples(); $("sample").value=b.dataset.sample; updateEpochs();
  $("sampledetail").scrollIntoView({behavior:"smooth",block:"start"});
});
$("methodslist").innerHTML=DATA.methods.map(x=>`<li>${esc(x)}</li>`).join("");
$("costrows").innerHTML=variants.map(v=>`<tr><td>${esc(v.label)}</td><td>${money(v.billed_cost)}</td><td>${v.api_calls??"Unknown"}</td><td>${esc(v.cost_scope||(v.historical?"Historical run":"New extraction, including recorded retries"))}</td></tr>`).join("")+DATA.other_costs.map(c=>`<tr><td>${esc(c.label)}</td><td>${money(c.billed_cost)}</td><td>${c.api_calls??"Unknown"}</td><td>${esc(c.scope||"New work")}</td></tr>`).join("");
$("provenance").textContent=JSON.stringify({report:DATA.provenance,variants:variants.map(v=>({id:v.id,...v.provenance}))},null,2);
$("download").addEventListener("click",()=>{
  const url=URL.createObjectURL(new Blob(["<!doctype html>\n"+document.documentElement.outerHTML],{type:"text/html"}));
  const a=document.createElement("a");a.href=url;a.download="extraction-report.html";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
updateSamples();
