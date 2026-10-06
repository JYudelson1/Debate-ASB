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
  panel.append(table(["Location the agent used", "Source / agent assessment", "Source calls", "Mechanism checked", "Compared with rubric"], locations.map(loc => [
    e("span", `${loc.path}${loc.start_line == null ? "" : ":"+loc.start_line+"–"+loc.end_line}`, "mono"),
    `${sources[loc.source]} · ${assessments[loc.assessment]}`, toolButtons(loc.tool_refs), loc.mechanism, `${loc.annotation_relation} · ${loc.annotation_note}`
  ])));
  for (const loc of locations) {
    if (!loc.returned_excerpt) continue;
    const detail = e("details");
    detail.append(e("summary", `Checked source: ${loc.path}:${loc.start_line}–${loc.end_line}`),
      e("small", "Script check: literal numbered lines returned. Mechanism interpretation: agent review."), e("pre", loc.returned_excerpt));
    panel.append(detail);
  }
}
