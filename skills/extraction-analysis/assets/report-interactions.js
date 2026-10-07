// Use DOM tooltips: native title hovers are unreliable in embedded viewers.
const hoverTip = document.getElementById("hover-tip");
function hideTooltip() { hoverTip.hidden = true; }
function showTooltip(node, text, event) {
  hoverTip.textContent = text;
  hoverTip.hidden = false;
  const rect = node.getBoundingClientRect();
  const box = hoverTip.getBoundingClientRect();
  const x = event?.clientX ?? rect.left;
  const y = event?.clientY ?? rect.bottom;
  hoverTip.style.left = Math.max(8, Math.min(x + 12, innerWidth - box.width - 8)) + "px";
  hoverTip.style.top = Math.max(8, Math.min(y + 12, innerHeight - box.height - 8)) + "px";
}
function attachTooltip(node, text, activate) {
  const content = () => typeof text === "function" ? text() : text;
  node.setAttribute("tabindex", "0");
  node.setAttribute("aria-describedby", "hover-tip");
  node.setAttribute("aria-label", content());
  node.addEventListener("pointerenter", event => showTooltip(node, content(), event));
  node.addEventListener("pointermove", event => showTooltip(node, content(), event));
  node.addEventListener("pointerleave", hideTooltip);
  node.addEventListener("focus", () => showTooltip(node, content()));
  node.addEventListener("blur", hideTooltip);
  if (activate) {
    node.addEventListener("click", () => { hideTooltip(); activate(); });
    if (node.tagName.toLowerCase() !== "button") {
      node.setAttribute("role", "button");
      node.addEventListener("keydown", event => {
        if (["Enter", " "].includes(event.key)) {
          event.preventDefault(); hideTooltip(); activate();
        }
      });
    }
  }
}
document.addEventListener("keydown", event => { if (event.key === "Escape") hideTooltip(); });
document.addEventListener("scroll", hideTooltip, true);
function compactRanges(numbers) {
  const sorted = [...new Set(numbers)].sort((a, b) => a - b), parts = [];
  for (let i = 0; i < sorted.length; i++) {
    const start = sorted[i];
    while (i + 1 < sorted.length && sorted[i + 1] === sorted[i] + 1) i++;
    parts.push(start === sorted[i] ? String(start) : `${start}–${sorted[i]}`);
  }
  return parts.join(", ");
}
function describeTool(step, sample) {
  const original = sample.trace?.tools.find(t => t.ordinal === step.tool);
  const returned = Object.entries(step.exposed).slice(0, 7)
    .map(([path, lines]) => `${path}:${compactRanges(lines)}`).join("\n");
  const basis = (sample.reviewed_locations || []).filter(l => l.tool_refs.includes(step.tool))
    .map(l => `${l.path}:${l.start_line}–${l.end_line}`).join("; ");
  return `#${step.tool} ${step.function}${step.failed ? " · failed" : ""}\nArguments: ${JSON.stringify(original?.arguments || {})}\nReturned source lines:\n${returned || "none"}${basis ? "\nAgent relied on: " + basis : ""}\nClick to open original response.`;
}
function openSourceTool(n) {
  const target = byId(`tool-${n}`);
  if (!target) return;
  for (let node = target; node; node = node.parentElement) {
    if (node.tagName === "DETAILS") node.open = true;
  }
  target.scrollIntoView({behavior: "smooth", block: "center"});
  target.querySelector("summary").focus({preventScroll: true});
}
function toolButtons(numbers) {
  const group = e("span");
  if (!numbers.length) return e("span", "—");
  for (const n of numbers) {
    const button = e("button", `#${n}`, "tool-link");
    button.onclick = () => openSourceTool(n);
    group.append(button, document.createTextNode(" "));
  }
  return group;
}
function regionTools(file, bin, sample) {
  return sample.metrics.sequence.filter(step => step.function === "read_file" && !step.failed &&
    (step.exposed[file.path] || []).some(n => n >= bin.start && n <= bin.end)).map(step => step.tool);
}
function describeRegion(file, bin, sample, cited) {
  return `${file.path}:${bin.start}–${bin.end}\n${bin.reads} read presentations${cited ? " · quoted region" : ""}\nSource calls: ${regionTools(file, bin, sample).map(n => "#" + n).join(", ")}\nClick to inspect returned lines.`;
}
function showRegion(file, bin, sample) {
  const tools = regionTools(file, bin, sample), lines = new Map();
  for (const tool of sample.trace?.tools || []) {
    if (!tools.includes(tool.ordinal)) continue;
    for (const text of tool.result.split("\n")) {
      const match = text.match(/^\s*(\d+)  (.*)$/);
      if (match && +match[1] >= bin.start && +match[1] <= bin.end && !lines.has(+match[1])) {
        lines.set(+match[1], match[2]);
      }
    }
  }
  const panel = byId("region-detail"), summary = e("summary", `${file.path}:${bin.start}–${bin.end} · source calls `);
  summary.append(toolButtons(tools));
  panel.replaceChildren(summary, e("pre", [...lines].sort((a,b) => a[0]-b[0]).map(([n,t]) => `${n}: ${t}`).join("\n") || "No numbered source lines available."));
  panel.hidden = false; panel.open = true;
  panel.scrollIntoView({behavior: "smooth", block: "nearest"});
}
function renderAgentBasis(sample) {
  const panel = byId("agent-basis"), review = sample.review;
  panel.replaceChildren(e("h3", "Agent’s sabotage basis"),
    e("p", `Actual mechanism exposed: ${review.exposure_status || "not recorded"} · ${review.exposure_summary || "Legacy review did not record a separate exposure judgment."}`));
  const locations = sample.reviewed_locations || [];
  if (!locations.length) {
    panel.append(e("p", "Exact locations used were not recorded. Rubric overlaps below are script measurements, not verified sabotage locations.", "warning"));
    return;
  }
  const sources = {runtime_read: "Original read response", runtime_search: "Original search hit", annotation_only: "Annotation only", current_file: "Current file, not run snapshot"};
  const assessments = {confirmed: "Agent confirmed mechanism", contradicts_annotation: "Agent found annotation conflict", supporting_clue: "Supporting clue only", unverified: "Unverified"};
  const scroll = e("div", null, "scroll");
  scroll.append(table(["Location the agent used", "Source / agent assessment", "Source calls", "Mechanism checked", "Compared with rubric"], locations.map(loc => [
    e("span", `${loc.path}${loc.start_line == null ? "" : ":"+loc.start_line+"–"+loc.end_line}`, "mono"),
    `${sources[loc.source]} · ${assessments[loc.assessment]}`, toolButtons(loc.tool_refs), loc.mechanism, `${loc.annotation_relation} · ${loc.annotation_note}`
  ])));
  panel.append(scroll);
  for (const loc of locations) {
    if (!loc.returned_excerpt) continue;
    const detail = e("details");
    detail.append(e("summary", `Checked source: ${loc.path}:${loc.start_line}–${loc.end_line}`),
      e("small", "Script check: literal numbered lines returned. Mechanism interpretation: agent review."), e("pre", loc.returned_excerpt));
    panel.append(detail);
  }
}

const bugLabels = {yes: "Bug", no: "No bug", unclear: "Unclear", unreviewed: "Not reviewed"};
const paperLabels = {contradicts: "Contradicts", consistent: "Consistent", not_specified: "Not specified", unclear: "Unclear", unreviewed: "Not reviewed"};
const stanceLabels = {concern: "Raises concern", benign: "Benign description", unclear: "Unclear"};
const bundleFilters = {bug: "", paper: ""};
function setBundleExpanded(number, expanded) {
  const row = byId(`bundle-${number}`), button = byId(`bundle-toggle-${number}`);
  if (!row || !button) return;
  row.hidden = !expanded;
  button.setAttribute("aria-expanded", String(expanded));
  button.setAttribute("aria-label", `${expanded ? "Hide" : "Show"} evidence for bundle ${number}`);
  button.textContent = expanded ? "Hide evidence ▴" : "Show evidence ▾";
}
function bundleEvidence(bundle, entry) {
  const content = e("div", null, "bundle-evidence");
  content.append(e("h4", `Bundle ${bundle.number} · Extractor observation`), e("p", bundle.observation));
  content.append(e("h4", "Retained snippets"));
  if (!bundle.excerpts.length) content.append(e("p", "No snippets retained.", "muted"));
  for (const x of bundle.excerpts) {
    const snippet = e("div", null, "bundle-snippet");
    const label = e("small", `${x.path}:${x.start_line??"?"}–${x.end_line??"?"} · ${x.fidelity.status}${x.fidelity.tool?" · tool #"+x.fidelity.tool:""}`, "mono");
    attachTooltip(label, `Bundle-reported range: ${x.start_line??"?"}–${x.end_line??"?"}\nLiteral quote found at: ${x.fidelity.actual_start??"?"}–${x.fidelity.actual_end??"?"}\nVerification: ${x.fidelity.status}${x.fidelity.tool?"\nClick to open tool #"+x.fidelity.tool:""}`, x.fidelity.tool?()=>openSourceTool(x.fidelity.tool):null);
    snippet.append(label, e("pre", x.text));
    content.append(snippet);
  }
  appendBundleBasis(content, entry);
  return content;
}
function renderBundleReview(sample) {
  const panel = byId("bundle-review"), quality = sample.bundle_quality;
  panel.replaceChildren();
  if (!quality?.reviewed) {
    panel.append(e("p", `Bug/intent checks: 0/${quality?.total ?? sample.metrics.bundles.length} reviewed. Sabotage-presence reviews above remain separate.`, "legend"));
  } else {
    panel.append(e("p", `${quality.reviewed}/${quality.total} bundles reviewed · bug-bearing: ${quality.bugs.yes||0} · no bug: ${quality.bugs.no||0} · unclear: ${quality.bugs.unclear||0} · viable candidates: ${quality.candidates.yes||0}`),
      e("p", `${quality.intended_concerns} intended-design concerns · ${quality.benign_descriptions} benign descriptions`, "legend"),
      e("p", "Topic focus (bundle slots): " + Object.entries(quality.topics).map(([topic,n]) => `${topic} ${n}/${quality.total}`).join(" · "), "legend"));
  }
  if (sample.review.bundle_review_note) panel.append(e("p", sample.review.bundle_review_note, "legend"));
  const legend = e("p", "Observation labels: ", "bundle-legend legend");
  for (const stance of ["concern", "benign", "unclear"]) legend.append(e("span", stanceLabels[stance], `badge stance-${stance}`));
  legend.append(e("span", "Saved observation stance; separate from bug, candidate and documented-sabotage judgments."));
  panel.append(legend, e("p", "Show evidence to read the observation, snippets and review basis directly below its analysis.", "legend"));
  if (!sample.metrics.bundles.length) {
    panel.append(e("p", "No evidence bundles retained.", "muted"));
    return;
  }
  const controls = e("div", null, "controls");
  const bugFilter = e("select"), paperFilter = e("select");
  bugFilter.id = "bundle-bug-filter";
  paperFilter.id = "bundle-paper-filter";
  for (const [select, field, label, options] of [
    [bugFilter, "bug", "Bug status", [["", "All bug statuses"], ["yes", "Bug"], ["no", "No bug"], ["unclear", "Unclear"]]],
    [paperFilter, "paper", "Paper agreement", [["", "All paper agreements"], ["consistent", "Consistent"], ["contradicts", "Contradicts"], ["not_specified", "Not specified"], ["unclear", "Unclear"]]]
  ]) {
    select.setAttribute("aria-label", `Filter bundles by ${label.toLowerCase()}`);
    for (const [value, text] of options) {
      const option = e("option", text);
      option.value = value;
      select.append(option);
    }
    select.value = bundleFilters[field];
    const wrapper = e("label", `${label}: `);
    wrapper.append(select);
    controls.append(wrapper);
    select.onchange = () => { bundleFilters[field] = select.value; applyFilters(); };
  }
  const reset = e("button", "Reset filters");
  reset.id = "bundle-filter-reset";
  reset.onclick = () => {
    bundleFilters.bug = "";
    bundleFilters.paper = "";
    bugFilter.value = paperFilter.value = "";
    applyFilters();
  };
  controls.append(reset);
  for (const [label, expanded] of [["Expand all evidence", true], ["Collapse all evidence", false]]) {
    const button = e("button", label);
    button.onclick = () => {
      for (const row of body.querySelectorAll(".bundle-summary")) {
        if (!row.hidden) setBundleExpanded(row.dataset.bundle, expanded);
      }
    };
    controls.append(button);
  }
  const count = e("p", null, "legend"), empty = e("p", "No bundles match these filters.", "muted");
  count.id = "bundle-filter-count";
  count.setAttribute("aria-live", "polite");
  empty.hidden = true;
  panel.append(controls, count);
  const headers = ["Bundle / observation", "Bug?", "Sabotage candidate?", "Paper agreement", "Topic", "Why"];
  const t = table(headers, []), body = t.querySelector("tbody");
  t.className = "bundle-table";
  for (const bundle of sample.metrics.bundles) {
    const entry = sample.reviewed_bundles?.find(x => x.number === bundle.number) || {bug_status: "unreviewed", sabotage_candidate: "unreviewed", paper_alignment: "unreviewed", stance: "unclear"};
    const stance = stanceLabels[entry.stance] ? entry.stance : "unclear";
    const identity = e("div", null, "bundle-identity"), button = e("button", "Show evidence ▾", "bundle-toggle");
    button.id = `bundle-toggle-${bundle.number}`;
    button.setAttribute("aria-controls", `bundle-${bundle.number}`);
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-label", `Show evidence for bundle ${bundle.number}`);
    button.onclick = () => setBundleExpanded(bundle.number, byId(`bundle-${bundle.number}`).hidden);
    identity.append(e("strong", `#${bundle.number}`), e("span", stanceLabels[stance], `badge bundle-stance stance-${stance}`), button);
    const why = e("div", entry.reason || "Pending review.");
    if (bundle.excerpts.some(x => x.fidelity.status === "unverified")) why.append(e("p", "Quote not verified against trace", "quote-warning"));
    const coverage = e("div", null, "bundle-coverage");
    if (sample.review.matching_bundles.includes(bundle.number)) coverage.append(e("span", "Reviewed match", "badge"));
    if (sample.review.sufficient_bundles.includes(bundle.number)) coverage.append(e("span", "Sufficient alone", "badge"));
    if (coverage.childNodes.length) why.append(coverage);
    const row = e("tr", null, `bundle-summary stance-${stance}`);
    row.dataset.bundle = bundle.number;
    row.dataset.bugStatus = entry.bug_status;
    row.dataset.paperAlignment = entry.paper_alignment;
    for (const value of [identity, bugLabels[entry.bug_status], entry.sabotage_candidate, paperLabels[entry.paper_alignment], entry.topic || "—", why]) {
      const cell = e("td");
      cell.append(value instanceof Node ? value : e("span", value));
      row.append(cell);
    }
    const evidenceRow = e("tr", null, "bundle-detail-row"), cell = e("td");
    evidenceRow.id = `bundle-${bundle.number}`;
    evidenceRow.hidden = true;
    cell.colSpan = headers.length;
    cell.append(bundleEvidence(bundle, entry));
    evidenceRow.append(cell);
    body.append(row, evidenceRow);
  }
  const scroll = e("div", null, "scroll");
  scroll.append(t);
  panel.append(scroll, empty);
  function applyFilters() {
    let visible = 0;
    for (const row of body.querySelectorAll(".bundle-summary")) {
      const matches = (!bundleFilters.bug || row.dataset.bugStatus === bundleFilters.bug) &&
        (!bundleFilters.paper || row.dataset.paperAlignment === bundleFilters.paper);
      row.hidden = !matches;
      if (matches) visible++;
      else setBundleExpanded(row.dataset.bundle, false);
    }
    count.textContent = `Showing ${visible}/${sample.metrics.bundles.length} bundles · summary counts above cover all bundles.`;
    empty.hidden = visible !== 0;
  }
  applyFilters();
}
function appendBundleBasis(node, entry) {
  if (!entry || entry.bug_status === "unreviewed") return;
  const basis = e("details");
  basis.append(e("summary", "Agent review basis: source code and paper context"));
  for (const ref of entry.checked_basis || []) {
    const label = e("p", `${ref.path}${ref.start_line == null ? "" : ":"+ref.start_line+"–"+ref.end_line} · ${ref.source} `, "mono");
    label.append(toolButtons(ref.tool_refs));
    basis.append(label);
    if (ref.source_sha256) basis.append(e("small", `Current reference snapshot · SHA-256 ${ref.source_sha256}`));
    if (ref.returned_excerpt || ref.reference_excerpt) basis.append(e("pre", ref.returned_excerpt || ref.reference_excerpt));
  }
  node.append(basis);
}
