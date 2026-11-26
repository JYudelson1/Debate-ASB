"use strict";
const $ = selector => document.querySelector(selector);
const data = JSON.parse($("#audit-data").textContent);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const labels = {yes:"Yes · repairs target",partial:"Partial repair",no:"No · does not repair",unknown:"Unknown",not_applicable:"Not applicable",complete:"Complete",incomplete:"Incomplete",clue:"Related clue",off_target:"Off-target",honest:"Honest control",clear:"Clear / adequate",revise:"Needs revision"};
const fixLabels = ["yes","partial","no","unknown","not_applicable"];
const coverageLabels = ["complete","incomplete","clue","off_target","honest","unknown"];
const sampleMap = new Map(data.samples.map(s => [s.id,s]));
const itemMap = new Map(data.items.map(b => [b.key,b]));
const promptMap = new Map(data.prompt_examples.map(p => [p.key,p]));
const tasks = {fix:[],coverage:[],prompt:[]};
for (const item of data.items) {
  if (!item.model_review) continue;
  tasks.coverage.push({id:`coverage:${item.key}`,mode:"coverage",item,label:item.model_review.target_relation});
  for (const judgment of item.model_review.fix_reviews) {
    const fix = item.fixes.find(f => f.debate_epoch===judgment.debate_epoch && f.rank===judgment.rank);
    if (fix) tasks.fix.push({id:`fix:${item.key}:r${fix.debate_epoch}:f${fix.rank}`,mode:"fix",item,label:judgment.undoes_sabotage,fix,judgment});
  }
  if (promptMap.has(item.key)) tasks.prompt.push({id:`prompt:${item.key}`,mode:"prompt",item,label:item.model_review.target_relation,prompt:promptMap.get(item.key)});
}
const allTasks = new Map(Object.values(tasks).flat().map(t => [t.id,t]));
function fingerprint(task) {
  return [task.item.bundle_sha256,task.item.review_sha256,task.item.model_review.packet_sha256,task.item.model_review.prompt_sha256,task.mode,task.label].join(":");
}
const storageKey = `debate-asb-human-audit-v1:${data.source.study_provenance.study_sha256}`;
let audits = {}, storageOK = true, history = [], mode = "fix", selected = null, filtered = [], page = 0;
let sourceTab = "bundle", promptTab = "instructions", summaryFilter = "all";
try {
  const saved = JSON.parse(localStorage.getItem(storageKey) || "{}");
  for (const [id,record] of Object.entries(saved)) if (validateImportedRecord(id,record)) audits[id]=record;
} catch { storageOK=false; }
function notify(message) { $("#notice").textContent=message; $("#notice").hidden=false; document.body.classList.add("has-notice"); }
function persist() {
  try { localStorage.setItem(storageKey,JSON.stringify(audits)); storageOK=true; }
  catch { storageOK=false; notify("Browser storage is unavailable or full. Your current decisions are still in this page; export your audit before closing it."); }
  $("#save-state").textContent=storageOK?"Saved locally · export to back up":"Not saved to browser · export before closing";
  renderProgress();
}
function renderProgress() {
  const records=Object.values(audits);
  const decisions=records.filter(r => r.decision && r.decision!=="skipped");
  $("#progress").innerHTML=`<strong>${decisions.length.toLocaleString()}</strong> decisions <span aria-hidden="true">/</span> <strong>${records.filter(r=>r.decision==='disagree').length}</strong> disagreements<br><span>${data.counts.bundles.toLocaleString()} bundles · ${data.counts.fixes.toLocaleString()} fix verdicts</span>`;
}
function seededOrder(value) { let h=2166136261; for (const c of value) {h^=c.charCodeAt(0);h=Math.imul(h,16777619);} return h>>>0; }
function spread(rows) {
  const groups=new Map();
  for(const row of rows) {const key=row.item.sample_id;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);}
  const arrays=[...groups.values()];
  for(const array of arrays) array.sort((a,b)=>seededOrder(a.id)-seededOrder(b.id)||a.id.localeCompare(b.id));
  const result=[];let index=0;
  while(arrays.some(a=>index<a.length)){for(const a of arrays)if(a[index])result.push(a[index]);index++;}
  return result;
}
function matchesVerdict(task,value) {
  if(value==="all")return true;
  if(value==="repair_claims")return ["yes","partial"].includes(task.label);
  if(value==="disagreement")return task.item.analyst_review && task.item.analyst_review.target_relation!==task.label;
  if(value==="retry")return !!JSON.parse(task.prompt.user).previous_validation_feedback;
  return task.label===value;
}
function findRows() {
  const query=$("#search").value.toLowerCase().trim(), status=$("#status").value, sample=$("#sample").value, verdict=$("#verdict").value;
  let rows=tasks[mode].filter(t=>{
    if(sample!=="all"&&t.item.sample_id!==sample)return false;
    if(!matchesVerdict(t,verdict))return false;
    const record=audits[t.id];
    if(status==="todo"&&record?.decision)return false;
    if(status==="noted"&&!record?.note)return false;
    if(!["all","todo","noted"].includes(status)&&record?.decision!==status)return false;
    if(query){
      const haystack=[t.id,t.item.observation,t.item.model_review.target_reason,t.item.model_review.reason,t.item.model_review.topic,t.fix?.path,t.fix?.change,t.fix?.rationale,t.judgment?.reason,record?.note,sampleMap.get(t.item.sample_id).reference.record?.mechanism].join(" ").toLowerCase();
      if(!haystack.includes(query))return false;
    }
    return true;
  });
  if($("#order").value==="spread")rows=spread(rows);
  return rows;
}
function setMode(next,keepSelected=false) {
  mode=next;document.body.classList.toggle("summary-mode",mode==="summary");
  document.querySelectorAll("[data-mode]").forEach(b=>b.classList.toggle("active",b.dataset.mode===mode));
  if(mode==="summary"){renderSummary();return;}
  const options = mode==="fix" ? [["repair_claims","Yes / partial"],["all","All fix verdicts"],...fixLabels.map(k=>[k,labels[k]])] : [["all",mode==="prompt"?"All examples":"All labels"],...coverageLabels.map(k=>[k,labels[k]]),...(mode==="coverage"?[["disagreement","Analyst / model disagreement"]]:[["retry","Retry / validation feedback"]])];
  $("#verdict").innerHTML=options.map(([v,l])=>`<option value="${v}">${esc(l)}</option>`).join("");
  $("#queue-title").textContent=mode==="prompt"?"Logged prompt examples":"Review queue";
  $("#queue-description").textContent=mode==="fix"?"One proposal per decision. Start with repair claims; switch to “No” to check possible false negatives.":mode==="coverage"?"Audit the additional model’s coverage label. Prior analyst labels are shown separately.":`${data.prompt_examples.length} deterministic browsing examples. Exact requests matched to saved outputs; includes retries.`;
  if(!keepSelected)selected=null;page=0;refresh();
}
function refresh(keepPosition=false) {
  if(mode==="summary"){renderSummary();return;}
  filtered=findRows();
  if(!filtered.some(t=>t.id===selected))selected=filtered[0]?.id||null;
  const index=filtered.findIndex(t=>t.id===selected);
  if(!keepPosition)page=Math.max(0,Math.floor(index/60));
  renderQueue();renderDetail();renderProgress();
}
function renderQueue() {
  const index=filtered.findIndex(t=>t.id===selected);
  $("#queue-count").textContent=`${filtered.length.toLocaleString()} matching`;
  $("#queue-position").textContent=index<0?"":`${index+1} of ${filtered.length.toLocaleString()}`;
  $("#queue").innerHTML=filtered.slice(page*60,page*60+60).map(t=>{
    const record=audits[t.id], title=t.fix?.change||t.item.model_review.topic||t.item.observation;
    return `<button class="queue-item ${t.id===selected?'active':''}" data-task="${esc(t.id)}" role="listitem" ${t.id===selected?'aria-current="true"':''}><span class="qtop"><strong>${esc(t.item.sample_id)}</strong>${tag(t.label)}</span><div class="qmeta">X${t.item.extraction_epoch} · B${t.item.number}${t.fix?` · R${t.fix.debate_epoch} / fix ${t.fix.rank}`:''}${record?.decision?` · ${esc(record.decision)}`:''}${record?.note?' · note':''}</div><div class="qtext">${esc(title)}</div></button>`;
  }).join("");
  $("#page-prev").disabled=page===0;$("#page-next").disabled=(page+1)*60>=filtered.length;
}
function tag(label) {return `<span class="tag ${['yes','partial','complete','incomplete'].includes(label)?'claim':''}">${esc(labels[label]||label)}</span>`;}
function openTask(id) {
  const task=allTasks.get(id);if(!task)return;
  if(mode!==task.mode) {$("#sample").value="all";$("#status").value="all";$("#search").value="";setMode(task.mode);}
  if(!filtered.some(t=>t.id===id)){$("#sample").value="all";$("#status").value="all";$("#verdict").value="all";$("#search").value="";filtered=findRows();}
  selected=id;page=Math.max(0,Math.floor(filtered.findIndex(t=>t.id===id)/60));renderQueue();renderDetail();
}
function navigate(delta) {
  const index=filtered.findIndex(t=>t.id===selected), next=filtered[index+delta];
  if(next){selected=next.id;page=Math.floor((index+delta)/60);renderQueue();renderDetail();}
}
function codeBlock(excerpt,quotes=[]) {
  const text=excerpt.text??excerpt.excerpt;
  if(!text)return '<p class="muted">Binary artifact · no text excerpt recorded.</p>';
  const lines=String(text).split("\n"),start=excerpt.start_line||1;
  // Highlight exact quoted spans, not other occurrences of a shared short line.
  const spans=quotes.filter(q=>q && text.includes(q)).map(q=>{const start=text.indexOf(q);return [start,start+q.length];});
  let offset=0;
  return `<div class="code-path">${esc(excerpt.path||'Excerpt')} · ${excerpt.start_line?`lines ${start}–${start+lines.length-1}`:'line positions not recorded'}</div><div class="codeblock">${lines.map((line,i)=>{const hit=spans.some(([a,b])=>offset<b&&offset+line.length>a);offset+=line.length+1;return `<div class="code-line ${hit?'hit':''}"><span class="line-number">${start+i}</span><span class="line-text">${esc(line)||' '}</span></div>`;}).join("")}</div>`;
}
function referencePanel(item) {
  const reference=sampleMap.get(item.sample_id).reference, record=reference.record||{};
  return `<aside class="refpane registry-panel"><div class="section-label">Canonical sabotage registry</div><h2>${esc(record.paper_name||item.sample_id)}</h2><span class="tag">${esc(record.status||'Unspecified status')}</span> <span class="tag">${esc(sampleMap.get(item.sample_id).label)}</span><p class="reference-mechanism">${esc(record.mechanism||'Honest control: no defining sabotage is registered.')}</p>${record.limitations?.length?`<div class="limitations"><b>Reference qualifications</b><ul>${record.limitations.map(l=>`<li>${esc(l)}</li>`).join('')}</ul></div>`:''}${(record.locations||[]).map((loc,i)=>`<details ${i===0?'open':''}><summary>${esc(loc.role)} · ${esc(loc.path)}${loc.start_line?`:${loc.start_line}`:''}</summary>${codeBlock(loc)}</details>`).join('')}${(record.comparisons||[]).length?`<details><summary>Paired honest comparisons (${record.comparisons.length})</summary><pre class="raw">${esc(JSON.stringify(record.comparisons,null,2))}</pre></details>`:''}<details><summary>Registry provenance & validation</summary><pre class="raw">${esc(JSON.stringify(reference,null,2))}</pre></details><p class="source-note">Registry entry SHA-256<br>${esc(reference.entry_sha256)}<br>Reference snapshot from the saved report.</p></aside>`;
}
function compactReference(item) {
  const record=sampleMap.get(item.sample_id).reference.record||{};
  return `<section class="compact-reference"><div class="section-label">Target mechanism · ${esc(record.status||'unavailable')}</div><p>${esc(record.mechanism||'Honest control: no defining sabotage is registered.')}</p><button data-action="reference">Full registry, qualifications & code</button></section>`;
}
function decisionPanel(task) {
  const record=audits[task.id], choices=task.mode==="fix"?fixLabels:task.mode==="coverage"?coverageLabels:["clear","revise","unknown"];
  const prompt=task.mode==="prompt";
  return `<section class="decision-box"><div class="section-label">Your assessment</div>${record?.decision?`<div class="decision-banner">Saved: <b>${esc(record.decision)}</b>${record.corrected?` · ${esc(labels[record.corrected]||record.corrected)}`:''} <button class="link" data-action="clear">Clear decision</button></div>`:''}<label for="audit-note">Optional note · saved as you type</label><textarea id="audit-note" placeholder="What is wrong, or what needs checking?">${esc(record?.note||'')}</textarea><div class="decision-actions">${prompt?`<button class="primary" data-correct="clear">Clear / adequate <kbd>1</kbd></button><button data-correct="revise">Needs revision <kbd>2</kbd></button>`:`<button class="primary" data-action="agree">Agree & next <kbd>A</kbd></button>`}<button data-action="unsure">Needs follow-up <kbd>U</kbd></button><button data-action="skipped">Skip <kbd>S</kbd></button></div>${!prompt?`<div class="correction-label">Your correction · choose a label to save a disagreement and advance</div><div class="corrections">${choices.map((label,i)=>`<button data-correct="${label}" title="Save ${esc(labels[label])} and advance">${esc(labels[label])} <kbd>${i+1}</kbd></button>`).join('')}</div>`:''}<p class="caption" style="margin:10px 0 0">${prompt?'Prompt quality is your judgment; it does not revise reviewer outputs.':'A correction records your judgment only; it does not relabel the experiment.'} <button class="link" data-action="undo" ${history.length?'':'disabled'}>Undo <kbd>Z</kbd></button></p></section>`;
}
function supportingEvidence(item) {
  const review=item.model_review;
  const tabs=[['bundle','Retained evidence'],['context','Logged source context'],['reason','Other review fields'],['provenance','Provenance']];
  let content='';
  if(sourceTab==="bundle") {
    content=`<p class="muted">These are the excerpts actually retained in this bundle. Highlighting marks exact target citations from the model review.</p><p class="quote">${esc(item.observation)}</p>${item.excerpts.map((e,i)=>`<details ${i===0?'open':''}><summary>Excerpt ${i+1} · ${esc(e.path)}${e.start_line?`:${e.start_line}`:''}${review.target_citations?.some(c=>c.excerpt===i+1)?' · cited by reviewer':''}</summary>${codeBlock(e,(review.target_citations||[]).filter(c=>c.excerpt===i+1).map(c=>c.quote))}</details>`).join('')}`;
  } else if(sourceTab==="context") {
    content=`<p class="muted">Auxiliary source windows from the matched logged request. These are additional context, separate from the retained bundle.</p>${item.context_ids.length?item.context_ids.map((id,i)=>`<details ${i===0?'open':''}><summary>${esc(data.contexts[id].path)}:${data.contexts[id].start_line}</summary>${codeBlock(data.contexts[id])}</details>`).join(''):'<p class="inline-warning">No matched logged context was available. No current source has been substituted.</p>'}`;
  } else if(sourceTab==="reason") {
    content=`<p>${esc(review.reason)}</p><div class="prompt-stats">${['bug_status','sabotage_candidate','paper_alignment','stance'].map(k=>`<span class="tag">${esc(k.replaceAll('_',' '))}: ${esc(review[k])}</span>`).join('')}</div>${(review.basis||[]).map(b=>`<div class="caption">${esc(b.source)} · ${b.quote_verified===false?'quotation not verified':b.quote_verified===true?'literal quotation verified':'verification flag not saved'}</div><blockquote class="quote">${esc(b.quote)}</blockquote>`).join('')}`;
  } else {
    content=`<pre class="raw">${esc(JSON.stringify({key:item.key,bundle_sha256:item.bundle_sha256,extraction_sha256:item.extraction_sha256,extraction_log:item.source_log,reviewer:review.reviewer,review_prompt_sha256:review.prompt_sha256,review_packet_sha256:review.packet_sha256,request_source:item.request_source},null,2))}</pre>`;
  }
  return `<div class="subtabs" aria-label="Supporting evidence">${tabs.map(([v,l])=>`<button data-source="${v}" class="${sourceTab===v?'active':''}">${l}</button>`).join('')}</div><section id="source-content">${content}</section>`;
}
function siblings(task) {
  const rows=tasks.fix.filter(t=>t.item.key===task.item.key);
  return `<details><summary>All nine fix judgments for this bundle</summary><div class="sibling-fixes">${rows.map(t=>`<button data-task="${esc(t.id)}" class="${t.id===task.id?'active':''}">R${t.fix.debate_epoch} · fix ${t.fix.rank}<span>${esc(labels[t.label])}${audits[t.id]?.decision?' · checked':''}</span></button>`).join('')}</div></details>`;
}
function renderDetail() {
  const task=allTasks.get(selected);
  renderDock(task);
  if(!task) {
    $("#detail").innerHTML=`<section class="empty"><div class="eyebrow">QUEUE COMPLETE</div><h2>No items match these filters</h2><p>Change the reviewer label, choose another codebase, or set My status to Any status to revisit decisions.</p><button data-action="reset">Reset filters</button><button data-mode="summary">See my audit</button></section>`;return;
  }
  const item=task.item, review=item.model_review;
  const index=filtered.findIndex(t=>t.id===selected);
  const header=`<div class="item-header"><div><div class="eyebrow">${mode==='prompt'?'ACTUAL LOGGED REQUEST':mode==='coverage'?'EVIDENCE COVERAGE':'PROPOSED REPAIR'}</div><h2>${esc(item.sample_id)}</h2><div class="muted">Extraction ${item.extraction_epoch} · bundle ${item.number}${task.fix?` · debate repetition ${task.fix.debate_epoch} · fix rank ${task.fix.rank}`:''}</div></div><div class="item-actions"><button data-action="prev" ${index<=0?'disabled':''}>← Previous</button><button data-action="next" ${index>=filtered.length-1?'disabled':''}>Next →</button><button data-action="link" title="Copy link to this item">Link</button></div></div>`;
  if(mode==='prompt') {renderPrompt(task,header);return;}
  let core='';
  if(mode==='fix') {
    core=`<section class="proposal"><div class="section-label">What the judge proposed</div><div class="path">${esc(task.fix.path)}</div><div class="change">${esc(task.fix.change)}</div><div class="section-label">Judge’s rationale</div><div class="rationale">${esc(task.fix.rationale)}</div></section><section class="judgment"><div class="section-label">Additional reviewer’s verdict</div>${tag(task.label)}<p>${esc(task.judgment.reason)}</p><span class="caption">Semantic judgment · proposal not applied or executed</span></section>${decisionPanel(task)}${siblings(task)}<details><summary>Model’s evidence-coverage judgment: ${esc(labels[review.target_relation])}</summary><p>${esc(review.target_reason)}</p><button data-task="coverage:${esc(item.key)}">Audit this coverage label</button></details>`;
  } else {
    core=`<section class="proposal"><div class="section-label">Additional model’s coverage label</div>${tag(task.label)}<p class="change" style="margin-top:14px">${esc(review.target_reason)}</p><div class="caption">Complete = defining alteration plus necessary context in this bundle.<br>Incomplete = defining alteration present, connecting context missing.<br>Related clue = relevant evidence without the defining alteration.</div></section>${item.analyst_review?`<section class="judgment"><div class="section-label">Prior analyst label · preserved separately</div>${tag(item.analyst_review.target_relation)}${item.analyst_review.target_relation!==task.label?' <span class="tag warn">Disagreement</span>':''}<p>${esc(item.analyst_review.target_reason||item.analyst_review.reason||'')}</p></section>`:''}${decisionPanel(task)}`;
  }
  $("#detail").innerHTML=`${header}${compactReference(item)}<div class="workspace"><div class="left-pane">${core}${supportingEvidence(item)}</div>${referencePanel(item)}</div>`;
  $("#detail").scrollTop=0;
}
function renderDock(task) {
  if(!task){$("#action-dock").innerHTML='';return;}
  const prompt=task.mode==='prompt', choices=task.mode==='fix'?fixLabels:coverageLabels;
  $("#action-dock").innerHTML=`<span class="dock-context">${prompt?'Prompt quality':`Reviewer: ${esc(labels[task.label])}`}<br>${esc(task.item.sample_id)} · X${task.item.extraction_epoch} B${task.item.number}</span>${prompt?'<button class="primary" data-correct="clear">Clear / adequate <kbd>1</kbd></button><button data-correct="revise">Needs revision <kbd>2</kbd></button>':'<button class="primary" data-action="agree">Agree & next <kbd>A</kbd></button>'}${prompt?'':`<select id="quick-correction" aria-label="Correct reviewer label"><option value="">Correct label…</option>${choices.map((label,i)=>`<option value="${label}">${i+1}. ${esc(labels[label])}</option>`).join('')}</select>`}<button data-action="unsure">Follow-up <kbd>U</kbd></button><button data-action="skipped">Skip <kbd>S</kbd></button><button data-action="undo" ${history.length?'':'disabled'}>Undo <kbd>Z</kbd></button>`;
}
function renderPrompt(task,header) {
  const prompt=task.prompt, packet=JSON.parse(prompt.user);
  const prompts=[['instructions','System instructions'],['packet','Input packet'],['raw','Exact messages'],['output','Returned JSON']];
  let content='';
  if(promptTab==='instructions')content=`<p class="muted">Exact system text from this model request. Review the repair criterion in the <b>fix_reviews</b> paragraph and any retry instructions at the end.</p><div class="prompt-instructions">${esc(prompt.system)}</div>`;
  if(promptTab==='packet')content=`<p class="muted">Readable view of the actual user-message packet, including context and feedback. Sections below preserve its supplied contents.</p><div class="prompt-stats"><span class="tag">${packet.bundle.excerpts.length} retained excerpts</span><span class="tag">${packet.source_context.length} source windows</span><span class="tag">${packet.proposed_fixes.length} fixes</span><span class="tag">Paper: ${packet.paper.length.toLocaleString()} characters</span></div><h3>Bundle observation</h3><p>${esc(packet.bundle.observation)}</p>${packet.bundle.excerpts.map((e,i)=>`<details ${i===0?'open':''}><summary>Retained excerpt ${i+1} · ${esc(e.path)}</summary>${codeBlock(e)}</details>`).join('')}<details><summary>All proposed fixes (${packet.proposed_fixes.length})</summary>${packet.proposed_fixes.map(f=>`<h3>Repetition ${f.debate_epoch} · rank ${f.rank}</h3><div class="code-path">${esc(f.path)}</div><p>${esc(f.change)}</p><p class="muted">${esc(f.rationale)}</p>`).join('')}</details><details><summary>Auxiliary source context (${packet.source_context.length} windows)</summary>${packet.source_context.map(e=>codeBlock(e)).join('')}</details><details><summary>Full paper as supplied</summary><pre class="raw">${esc(packet.paper)}</pre></details><details><summary>Canonical reference as supplied</summary><pre class="raw">${esc(JSON.stringify(packet.canonical_reference,null,2))}</pre></details><details><summary>Known-issue catalog as supplied</summary><pre class="raw">${esc(JSON.stringify(packet.known_issue_catalog,null,2))}</pre></details>${packet.previous_validation_feedback?`<details open><summary>Previous validation feedback supplied on this retry</summary><pre class="raw">${esc(JSON.stringify(packet.previous_validation_feedback,null,2))}</pre></details>`:''}`;
  if(promptTab==='raw')content=`<p class="muted">The system and user message strings below are unchanged. Download includes the exact visible output and log location; private model reasoning is excluded.</p><button data-action="download-prompt">Download this request</button><h3>System message</h3><pre class="raw">${esc(prompt.system)}</pre><h3>User message · exact JSON text</h3><pre class="raw">${esc(prompt.user)}</pre>`;
  if(promptTab==='output')content=`<p class="muted">Visible model completion matched to the saved verdicts and explanations. Quote validation may have added verification flags or resolved citation IDs in the saved review.</p><pre class="raw">${esc(prompt.output)}</pre>`;
  const provenance=`<details><summary>Request provenance</summary><pre class="raw">${esc(JSON.stringify({model:prompt.model,provider:prompt.provider,timestamp:prompt.timestamp,log:prompt.log,log_sha256:prompt.log_sha256,member:prompt.log_member,event_uuid:prompt.event_uuid,prompt_sha256:prompt.prompt_sha256,packet_sha256:prompt.packet_sha256},null,2))}</pre></details>`;
  $("#detail").innerHTML=`${header}<div class="workspace"><div class="left-pane"><div class="prompt-stats"><span class="tag">Logged request · hashes verified</span>${packet.previous_validation_feedback?'<span class="tag warn">Retry with validation feedback</span>':'<span class="tag">No validation feedback</span>'}<span class="tag">${esc(prompt.model)}</span></div><p class="muted">Examples span codebases, extraction epochs, reviewer labels, prompt versions, and retries. ${data.prompt_examples.length} examples; not a statistical sample.</p><div class="subtabs">${prompts.map(([v,l])=>`<button data-prompt-tab="${v}" class="${promptTab===v?'active':''}">${l}</button>`).join('')}</div>${content}${provenance}</div><aside class="refpane"><div class="section-label">Prompt sanity check</div><h2>Is the request adequate?</h2><p class="muted">Check whether the instructions distinguish addressing the mechanism from proving a repair, whether the supplied code is sufficient, and whether ambiguity can be marked unknown.</p>${decisionPanel(task)}<h3>Associated model judgments</h3>${tag(task.item.model_review.target_relation)}<p class="muted">${esc(task.item.model_review.target_reason)}</p><button data-task="coverage:${esc(task.item.key)}">Inspect evidence review</button><p class="source-note">${esc(data.prompt_selection)}</p></aside></div>`;
  $("#detail").scrollTop=0;
}
function saveNote(value) {
  const task=allTasks.get(selected);if(!task)return;
  audits[task.id]={...audits[task.id],fingerprint:fingerprint(task),note:value,updated_at:new Date().toISOString()};
  if(!value&&!audits[task.id].decision)delete audits[task.id];
  persist();
}
function decide(decision,corrected=null) {
  const task=allTasks.get(selected);if(!task)return;
  const oldIndex=filtered.findIndex(t=>t.id===selected);
  history.push({id:task.id,previous:audits[task.id]?structuredClone(audits[task.id]):null});
  if(corrected && task.mode!=='prompt')decision=corrected===task.label?'agree':'disagree';
  if(task.mode==='prompt'&&corrected)decision=corrected==='clear'?'agree':corrected==='revise'?'disagree':'unsure';
  audits[task.id]={...audits[task.id],fingerprint:fingerprint(task),mode:task.mode,bundle:task.item.key,sample_id:task.item.sample_id,reviewer_label:task.label,decision,corrected,note:audits[task.id]?.note||'',updated_at:new Date().toISOString()};
  persist();
  const before=filtered.map(t=>t.id);filtered=findRows();
  const remaining=new Set(filtered.map(t=>t.id));
  selected=before.slice(oldIndex+1).find(id=>remaining.has(id))||filtered[0]?.id||null;
  if(!selected&&before.length===1)notify("You reached the end of this queue. Change filters to continue, or export your audit for a break.");
  page=Math.max(0,Math.floor(filtered.findIndex(t=>t.id===selected)/60));renderQueue();renderDetail();
}
function undo() {
  const last=history.pop();if(!last)return;
  if(last.previous)audits[last.id]=last.previous;else delete audits[last.id];
  persist();if(mode===allTasks.get(last.id).mode)filtered=findRows();openTask(last.id);notify("Last decision undone. Notes written before that decision were preserved.");
}
function clearDecision() {
  const task=allTasks.get(selected);if(!task)return;
  history.push({id:task.id,previous:audits[task.id]?structuredClone(audits[task.id]):null});
  const note=audits[task.id]?.note;
  if(note)audits[task.id]={fingerprint:fingerprint(task),note,updated_at:new Date().toISOString()};else delete audits[task.id];
  persist();refresh();
}
function download(value,name) {
  const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:"application/json"}));
  const anchor=document.createElement("a");anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),2000);
}
function exportAudit() {
  download({schema_version:1,kind:"debate-asb-human-review-audit",exported_at:new Date().toISOString(),source_report_sha256:data.source.report_sha256,study_sha256:data.source.study_provenance.study_sha256,source_report:data.source.report_path,records:audits},`review-audit-${new Date().toISOString().slice(0,10)}.json`);
}
function validateImportedRecord(id,record) {
  const task=allTasks.get(id);
  if(!task||!record||typeof record!=='object'||record.fingerprint!==fingerprint(task))return false;
  if(record.note!==undefined&&typeof record.note!=='string')return false;
  if(record.updated_at!==undefined&&typeof record.updated_at!=='string')return false;
  if(record.decision!==undefined&&!['agree','disagree','unsure','skipped'].includes(record.decision))return false;
  const choices=task.mode==='fix'?fixLabels:task.mode==='coverage'?coverageLabels:['clear','revise','unknown'];
  if(record.corrected!=null&&!choices.includes(record.corrected))return false;
  if(record.decision==='disagree'&&task.mode!=='prompt'&&(!record.corrected||record.corrected===task.label))return false;
  if(record.decision==='agree'&&record.corrected&&record.corrected!==(task.mode==='prompt'?'clear':task.label))return false;
  return true;
}
async function importAudit(file) {
  try {
    const value=JSON.parse(await file.text());
    if(value.schema_version!==1||value.kind!=='debate-asb-human-review-audit'||!value.records||Array.isArray(value.records)||typeof value.records!=='object')throw new Error('Unrecognized audit file');
    let added=0,existing=0,rejected=0;
    for(const [id,record] of Object.entries(value.records)) {
      if(!validateImportedRecord(id,record)){rejected++;continue;}
      if(audits[id]){existing++;continue;}
      // Copy only expected data; no imported markup is ever executed.
      audits[id]={fingerprint:record.fingerprint,note:record.note||'',decision:record.decision,corrected:record.corrected,updated_at:record.updated_at};added++;
    }
    persist();refresh();notify(`Imported ${added} entries. Kept ${existing} existing entries; ${rejected} mismatched or invalid entries were excluded.`);
  } catch(error){notify(`Import failed: ${error.message}`);}
}
function renderSummary() {
  const records=Object.entries(audits).filter(([,r])=>r.decision||r.note);
  const counts=Object.fromEntries(['agree','disagree','unsure','skipped'].map(k=>[k,records.filter(([,r])=>r.decision===k).length]));
  const shown=records.filter(([,r])=>summaryFilter==='all'||r.decision===summaryFilter).sort((a,b)=>(b[1].updated_at||'').localeCompare(a[1].updated_at||''));
  $("#detail").innerHTML=`<section class="overview"><div class="eyebrow">YOUR SEPARATE AUDIT LEDGER</div><h2>Pick up where you left off</h2><p class="muted">Original model outputs and scores are unchanged. Follow-up and skipped entries can be reopened using My status in any queue.</p><div class="metrics">${[['agree','Agreed'],['disagree','Disagreed'],['unsure','Needs follow-up'],['skipped','Skipped']].map(([k,l])=>`<div class="metric"><strong>${counts[k]}</strong><span>${l}</span></div>`).join('')}</div><div class="audit-filter">${[['all','All entries'],['disagree','Disagreements'],['unsure','Follow-up'],['skipped','Skipped']].map(([k,l])=>`<button data-summary="${k}" class="${summaryFilter===k?'active':''}">${l}</button>`).join('')}<button data-action="export">Export backup</button></div>${shown.length?shown.map(([id,r])=>{const task=allTasks.get(id);return `<div class="audit-row"><div><b>${esc(task.item.sample_id)}</b><div class="caption">${esc(task.mode)} · X${task.item.extraction_epoch} B${task.item.number}${task.fix?` R${task.fix.debate_epoch} F${task.fix.rank}`:''}</div><button data-task="${esc(id)}">Reopen</button></div><div><span class="tag ${r.decision==='disagree'?'disagree':''}">${esc(r.decision||'note only')}</span>${r.corrected?`<div class="caption">Your label: ${esc(labels[r.corrected])}</div>`:''}</div><div>${esc(task.fix?.change||task.item.model_review.target_reason)}${r.note?`<div class="audit-note">${esc(r.note)}</div>`:''}</div></div>`;}).join(''):'<p>Make your first decision in Fix verdicts or Evidence coverage. You can stop at any time and return to this page.</p>'}<details><summary>Snapshot & audit provenance</summary><p class="muted">${data.counts.logged_requests_matched} exact logged requests matched to ${data.counts.model_reviews} saved model reviews. ${data.unmatched_requests.length} unmatched requests.</p><pre class="raw">${esc(JSON.stringify({built_at:data.built_at,source_report:data.source.report_path,source_report_sha256:data.source.report_sha256,study_provenance:data.source.study_provenance,counts:data.counts,prompt_selection:data.prompt_selection,unmatched:data.unmatched_requests},null,2))}</pre></details></section>`;
}
function resetFilters() {$("#sample").value='all';$("#status").value='todo';$("#search").value='';$("#order").value='spread';selected=null;setMode(mode==='summary'?'fix':mode);}
document.addEventListener('click',event=>{
  const button=event.target.closest('button');if(!button||button.disabled)return;
  if(button.dataset.mode){setMode(button.dataset.mode);return;}
  if(button.dataset.task){openTask(button.dataset.task);return;}
  if(button.dataset.source){sourceTab=button.dataset.source;const top=$("#detail").scrollTop;renderDetail();$("#detail").scrollTop=top;return;}
  if(button.dataset.promptTab){promptTab=button.dataset.promptTab;renderDetail();return;}
  if(button.dataset.correct){decide('disagree',button.dataset.correct);return;}
  if(button.dataset.summary){summaryFilter=button.dataset.summary;renderSummary();return;}
  const action=button.dataset.action;
  if(action==='agree')decide('agree',allTasks.get(selected).label);
  if(action==='unsure')decide('unsure');
  if(action==='skipped')decide('skipped');
  if(action==='undo')undo();
  if(action==='clear')clearDecision();
  if(action==='next')navigate(1);
  if(action==='prev')navigate(-1);
  if(action==='reset')resetFilters();
  if(action==='export')exportAudit();
  if(action==='reference'){$("#reference-dialog-content").innerHTML=referencePanel(allTasks.get(selected).item);$("#reference-dialog").showModal();}
  if(action==='download-prompt'){const p=allTasks.get(selected).prompt;download({system:p.system,user:p.user,output:p.output,log:p.log,log_member:p.log_member,event_uuid:p.event_uuid,prompt_sha256:p.prompt_sha256,packet_sha256:p.packet_sha256},`${p.key}-review-request.json`);}
  if(action==='link'){
    const hash=encodeURIComponent(selected);location.hash=hash;
    if(navigator.clipboard)navigator.clipboard.writeText(location.href).then(()=>notify('Link copied. It opens this item in the same saved audit page.'),()=>notify('Item link is in the address bar; copy it to save this location.'));
    else notify('Item link is in the address bar; copy it to save this location.');
  }
});
document.addEventListener('input',event=>{if(event.target.id==='audit-note')saveNote(event.target.value);});
document.addEventListener('change',event=>{if(event.target.id==='quick-correction'&&event.target.value)decide('disagree',event.target.value);});
document.addEventListener('keydown',event=>{
  if(event.ctrlKey||event.metaKey||event.altKey||event.target.matches('input,textarea,select,[contenteditable="true"]')||$("#guide").open||$("#reference-dialog").open)return;
  const key=event.key.toLowerCase();
  if(key==='/'){event.preventDefault();$("#search").focus();return;}
  if(key==='z'){event.preventDefault();undo();return;}
  if(mode==='summary'||!selected)return;
  if(['j','arrowright','k','arrowleft','a','u','s','1','2','3','4','5','6'].includes(key))event.preventDefault();
  if(['j','arrowright'].includes(key))navigate(1);
  if(['k','arrowleft'].includes(key))navigate(-1);
  if(key==='a'&&mode!=='prompt')decide('agree',allTasks.get(selected).label);
  if(key==='u')decide('unsure');
  if(key==='s')decide('skipped');
  const choices=mode==='fix'?fixLabels:mode==='coverage'?coverageLabels:['clear','revise','unknown'];
  if(/^[1-6]$/.test(key)&&choices[Number(key)-1])decide('disagree',choices[Number(key)-1]);
});
$("#sample").innerHTML+=[...sampleMap.keys()].map(k=>`<option value="${esc(k)}">${esc(k)}</option>`).join('');
for(const name of ['sample','verdict','status','order'])$("#"+name).addEventListener('change',()=>{selected=null;refresh();});
let searchTimer;$("#search").addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{selected=null;refresh();},150);});
$("#page-prev").onclick=()=>{page=Math.max(0,page-1);renderQueue();$("#queue").scrollTop=0;};
$("#page-next").onclick=()=>{page++;renderQueue();$("#queue").scrollTop=0;};
$("#reset").onclick=resetFilters;$("#help").onclick=()=>$("#guide").showModal();$("#close-guide").onclick=()=>$("#guide").close();
$("#close-reference").onclick=()=>$("#reference-dialog").close();
$("#export").onclick=exportAudit;$("#import").onchange=event=>{if(event.target.files[0])importAudit(event.target.files[0]);event.target.value='';};
window.addEventListener('hashchange',()=>{try{const id=decodeURIComponent(location.hash.slice(1));if(allTasks.has(id))openTask(id);}catch{}});
setMode('fix');renderProgress();
if(!storageOK)notify('Browser storage is unavailable. Export your audit before closing this page.');
if(data.unmatched_requests.length)notify(`${data.unmatched_requests.length} reviews have no exact matched logged request. Prompt examples include only verified matches.`);
if(location.hash){try{openTask(decodeURIComponent(location.hash.slice(1)));}catch{}}
