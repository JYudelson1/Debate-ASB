"use strict";
const data = JSON.parse(document.getElementById("explorer-data").textContent);
const byId = id => document.getElementById(id);
const el = (tag, text, cls) => { const n = document.createElement(tag); if(text != null) n.textContent = text; if(cls) n.className = cls; return n; };
const statusNames = {verified:"Checked code mechanism",partial:"Provenance partly unresolved",pending_review:"Not reviewed",honest_control:"Honest control"};
let selected = decodeURIComponent(location.hash.slice(1)) || "lazy_alpha", locationIndex = 0;
const checked = data.samples.filter(s => ["verified","partial"].includes(s.status));
byId("registry-info").textContent = `${data.samples.reduce((n,s)=>n+s.acceptable_fixes.length,0)} acceptable fixes across ${data.samples.filter(s=>s.acceptable_fixes.length).length} codebases · ${checked.filter(s=>s.status==="verified").length} checked mechanisms · ${checked.filter(s=>s.status==="partial").length} partial reference\nRegistry v${data.registry_version}`;
for(const family of [...new Set(data.samples.map(s=>s.family))].sort()) {const o=el("option",family[0].toUpperCase()+family.slice(1));o.value=family;byId("family").append(o);}
function visibleSamples(){const query=byId("search").value.toLowerCase(),scope=byId("scope").value,family=byId("family").value;return data.samples.filter(s=>(scope==="all" || (scope==="fixes"?s.acceptable_fixes.length:scope==="sabotaged"?s.label==="sabotaged":["verified","partial"].includes(s.status))) && (!family||s.family===family) && `${s.id} ${s.title} ${s.paper_name} ${s.mechanism||""} ${s.locations.map(l=>l.path).join(" ")} ${s.acceptable_fixes.map(f=>f.title+" "+f.requirements.join(" ")).join(" ")}`.toLowerCase().includes(query));}
function list(){const samples=visibleSamples();byId("list-count").textContent=`${samples.length} codebases`;const nav=byId("sample-list");nav.replaceChildren();for(const s of samples){const b=el("button");b.type="button";b.setAttribute("aria-current",s.id===selected?"true":"false");b.append(el("strong",s.title),el("small",`${s.id} · ${statusNames[s.status]}`));b.onclick=()=>choose(s.id);nav.append(b);}if(!samples.length)nav.append(el("p","No codebases match these filters.","muted"));}
function choose(id){selected=id;locationIndex=0;history.replaceState(null,"",`#${encodeURIComponent(id)}`);list();detail();}
function codePanel(title,rows,kind,empty){const panel=el("div",null,`code-panel ${kind}`);panel.append(el("h4",title));if(!rows?.length){panel.append(el("p",empty,"code-placeholder"));return panel;}const pre=el("pre");for(const row of rows){const line=el("span",null,`code-line${row.changed?" changed":""}`);line.append(el("span",String(row.line),"line-number"),el("span",row.text,"line-text"));pre.append(line);}panel.append(pre);return panel;}
function renderCode(sample,node){const loc=sample.locations[locationIndex];if(!loc)return;const hunks=loc.comparison||[];const honest=hunks.flatMap(h=>h.honest),sabotaged=hunks.flatMap(h=>h.sabotaged);const currentRows=loc.excerpt==null?[]:loc.excerpt.split("\n").map((text,i)=>({line:loc.start_line+i,text,changed:sabotaged.some(r=>r.line===loc.start_line+i&&r.changed)}));const grid=el("div",null,"code-grid");grid.append(codePanel(`Paired honest · ${loc.honest_sample_id||"reference unavailable"}`,honest,"honest","No paired source comparison was saved for this location."),codePanel(`Sabotaged · ${sample.id}`,currentRows,"sabotaged",loc.role==="unverified_artifact"?"Binary artifact; a line excerpt cannot establish its production history.":"No line excerpt saved."));node.append(grid,el("p",`${loc.path}${loc.start_line?":"+loc.start_line+"–"+loc.end_line:""} · ${loc.role.replaceAll("_"," ")}`,"muted"));}
function renderFixes(sample, node) {
  if (!sample.acceptable_fixes.length) return;
  const section = el("section", null, "section acceptable-fixes");
  section.setAttribute("aria-label", "Acceptable fixes");
  section.append(el("h3", `Acceptable fixes (${sample.acceptable_fixes.length})`),
    el("p", "These fixes cover the core experiment’s execution path. Choose one complete fix. Every requirement within it must be met; equivalent implementations are acceptable. These examples are not an exhaustive list.", "muted"));
  if (sample.fix_validation.status !== "matched") {
    section.append(el("p", `Fix source check: ${sample.fix_validation.status.replaceAll("_", " ")}. ${sample.fix_validation.issues.join("; ")}`, "note"));
  }
  for (const [i, fix] of sample.acceptable_fixes.entries()) {
    const card = el("div", null, "fix-card");
    card.append(el("h4", `${i + 1}. ${fix.title}`),
      el("span", fix.kind === "honest_reversion" ? "Honest mechanism restored" : "Equivalent repair", "pill verified"));
    const requirements = el("ul");
    for (const text of fix.requirements) requirements.append(el("li", text));
    card.append(requirements);
    for (const text of fix.notes) card.append(el("p", text, "fix-note"));
    const examples = el("details", null, "raw fix-examples");
    examples.append(el("summary", `View example changes · ${fix.changes.length} file${fix.changes.length === 1 ? "" : "s"}`),
      el("p", "Line numbers refer to the pinned sabotaged source. The code demonstrates the requirements; formatting, comments and variable names are not matching criteria.", "muted"));
    for (const change of fix.changes) {
      const file = el("div", null, "fix-file");
      file.append(el("h5", change.path), el("p", `Paired honest: ${change.honest_reference.sample_id} · ${change.honest_reference.path}`, "muted"));
      if (change.operation === "replace_artifact") {
        file.append(el("p", "Replace this artifact with the paired honest copy."),
          el("code", `Replacement SHA-256: ${change.result_sha256}`));
      } else {
        for (const edit of change.edits) {
          const grid = el("div", null, "code-grid fix-edit");
          const rows = (text, start) => text.replace(/\n$/, "").split("\n").map((text, j) => ({line: start + j, text, changed: true}));
          grid.append(codePanel(`Current · original line ${edit.start_line}`, edit.before ? rows(edit.before, edit.start_line) : [], "fix-before", "Insert at this location."),
            codePanel("Acceptable example · replacement", edit.after ? rows(edit.after, 1) : [], "fix-after", "Remove these lines."));
          file.append(grid);
        }
      }
      examples.append(file);
    }
    card.append(examples);
    section.append(card);
  }
  const review = el("details", null, "raw");
  review.append(el("summary", "Repair scope and verification"), el("p", data.fix_policy.execution_scope), el("p", data.fix_policy.minimality),
    el("p", data.fix_policy.verification), el("pre", JSON.stringify({review: sample.fix_review, validation: sample.fix_validation}, null, 2)));
  section.append(review);
  node.append(section);
}
function detail(){const s=data.samples.find(s=>s.id===selected)||visibleSamples()[0];const node=byId("detail");node.replaceChildren();if(!s){node.append(el("p","Select a codebase to view its reference.","empty"));return;}selected=s.id;const hero=el("section",null,"hero"),identity=el("div",null,"identity");identity.append(el("span",s.id,"pill"),el("span",statusNames[s.status],`pill ${s.status}`));if(s.annotation_relation==="contradicts")identity.append(el("span","Corrects the original rubric","pill partial"));hero.append(identity,el("h2",s.title),el("p",s.paper_name||s.family,"paper"),el("p",s.introduction||s.mechanism||(s.label==="honest"?"This is an honest control. No target sabotage is assigned.":"This variant has no checked sabotage mechanism yet. The original rubric is retained as an unverified claim."),"mechanism"));if(s.reference.validation.status!=="matched")hero.append(el("p",`Source check: ${s.reference.validation.status.replaceAll("_"," ")}${s.reference.validation.issues.length?" · "+s.reference.validation.issues.join("; "):""}`,"note"));if(s.status==="partial")hero.append(el("p",s.limitations[0],"note"));node.append(hero);if(s.mechanism && s.introduction !== s.mechanism){const full=el("details",null,"raw");full.append(el("summary","Full checked mechanism"),el("p",s.mechanism));node.append(full);}
renderFixes(s, node);
if(s.paper){const paper=el("details",null,"raw paper-view"),content=el("div",null,"paper-content");content.innerHTML=s.paper.html;const source=el("details",null,"raw");source.append(el("summary","Markdown source with line numbers"),codePanel("PAPER.md",s.paper.text.split("\n").map((text,i)=>({line:i+1,text})),"paper",""));paper.append(el("summary","View paper (PAPER.md)"),el("p",s.paper.path,"muted"),content,source);node.append(paper);}
if(s.locations.length){const section=el("section",null,"section");section.append(el("h3","Compare the implementation"),el("p","Select a checked region. Highlighted lines differ from the saved paired honest source.","muted"));const choices=el("div",null,"location-choices");for(const [i,loc]of s.locations.entries()){const b=el("button",`${loc.path}${loc.start_line?":"+loc.start_line:""}`);b.type="button";b.setAttribute("aria-pressed",String(i===locationIndex));b.onclick=()=>{locationIndex=i;detail();};choices.append(b);}section.append(choices);renderCode(s,section);node.append(section);}

const caveats=el("details",null,"raw");caveats.append(el("summary","What this reference does and does not establish"));for(const text of s.limitations)caveats.append(el("p",text));caveats.append(el("p","Code mechanisms are checked against saved source. Empirical impact and the extractor’s recognition require separate evidence. A same-file citation alone does not establish capture."));node.append(caveats);
if(s.known_issues.length){const section=el("section",null,"section");section.append(el("h3","Known non-target issues"),el("p","ASB lists these as pre-existing bugs, quirks or discrepancies. They are separate from this sabotage; annotation claims can be disputed in a bundle review.","muted"));for(const issue of s.known_issues){const d=el("details",null,"issue"),summary=el("summary",issue.title);summary.append(el("span",issue.category,"pill"));d.append(summary,el("p",issue.description),el("p",`ASB’s impact claim: ${issue.impact}`,"muted"),el("code",issue.file));section.append(d);}node.append(section);}
const provenance=el("details",null,"raw");provenance.append(el("summary","Source revision, comparisons, original rubric and review provenance"),el("pre",JSON.stringify(s.reference,null,2)));node.append(provenance);}
for(const id of["search","scope","family"])byId(id).addEventListener(id==="search"?"input":"change",()=>{const visible=visibleSamples();if(!visible.some(s=>s.id===selected)&&visible.length)choose(visible[0].id);else{list();detail();if(!visible.length)byId("detail").replaceChildren(el("p","No codebases match these filters.","empty"));}});
addEventListener("hashchange",()=>{selected=decodeURIComponent(location.hash.slice(1));locationIndex=0;const s=data.samples.find(s=>s.id===selected);if(s&&!(["verified","partial"].includes(s.status)))byId("scope").value="all";list();detail();});
const initial=data.samples.find(s=>s.id===selected);if(initial&&!["verified","partial"].includes(initial.status))byId("scope").value="all";
list();detail();
