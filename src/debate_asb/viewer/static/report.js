"use strict";

const report = JSON.parse(document.getElementById("report-data").textContent);
const state = { sampleIndex: 0, bundleNumber: null, revealRubric: false };

const ARGUMENT_COLLAPSED_HEIGHT = 300;
const JUDGE_COLLAPSED_HEIGHT = 380;

function currentSample() {
  return report.samples[state.sampleIndex];
}

function currentBundle() {
  const bundles = currentSample()?.bundles || [];
  return bundles.find((bundle) => bundle.number === state.bundleNumber) || bundles[0];
}

function initialize() {
  byId("report-title").textContent = report.task;
  byId("run-summary").textContent = report.source_log;
  const selector = byId("sample-select");
  report.samples.forEach((sample, index) => {
    const option = element("option", { text: `${sample.id} · epoch ${sample.epoch}` });
    option.value = String(index);
    selector.appendChild(option);
  });
  selector.addEventListener("change", () => {
    state.sampleIndex = Number(selector.value);
    state.bundleNumber = currentSample().bundles[0]?.number ?? null;
    render();
  });
  byId("bundle-search").addEventListener("input", renderBundleList);
  byId("reveal-ground-truth").addEventListener("click", () => {
    state.revealRubric = !state.revealRubric;
    render();
  });
  state.bundleNumber = currentSample()?.bundles[0]?.number ?? null;
  render();
}

function render() {
  const sample = currentSample();
  renderRunTags(sample);
  renderError(sample);
  renderProgress(sample);
  renderBundleList();
  renderBundle(currentBundle());
  renderRubric(sample);
  renderProvenance(sample);
  byId("reveal-ground-truth").textContent = state.revealRubric
    ? "Hide rubric context"
    : "Reveal rubric context";
}

function renderRunTags(sample) {
  const revision = report.revision || {};
  const tags = [
    tag(report.status.toUpperCase(), report.status === "error" ? "error" : "success"),
    tag(sample.id),
    tag(`epoch ${sample.epoch}`),
    tag(sample.paper_name || "paper unknown"),
  ];
  if (revision.commit) tags.push(tag(`git ${revision.commit}${revision.dirty ? " · dirty" : ""}`));
  if (state.revealRubric && sample.target) tags.push(tag(`target: ${sample.target}`));
  replaceChildren("run-tags", tags);
}

function renderError(sample) {
  if (!sample.error) {
    replaceChildren("error-banner", []);
    return;
  }
  const message = sample.error.message || pretty(sample.error);
  const details = element("details", {}, [
    element("summary", { text: "Full error" }),
    element("pre", { text: message }),
  ]);
  replaceChildren("error-banner", [
    element("div", { className: "error-card" }, [
      element("strong", { text: "Sample stopped before all stages completed" }),
      element("p", { text: shortText(message, 260) }),
      details,
    ]),
  ]);
}

function renderProgress(sample) {
  const progress = sample.stage_progress;
  const total = progress.bundles;
  const stages = [
    ["Extraction", progress.extraction, `${total} bundles retained`],
    ["Bundle debates", progress.debates === total && total ? "complete" : "missing", `${progress.debates}/${total}`],
    ["Judgments", progress.judgments === total && total ? "complete" : "missing", `${progress.judgments}/${total}`],
    ["Aggregation", progress.aggregation, sample.aggregation?.credence == null ? "No score" : `${sample.aggregation.credence}%`],
  ];
  replaceChildren("stage-progress", stages.map(([name, status, detail]) =>
    element("div", { className: `stage ${status}` }, [
      element("strong", { text: `${status === "complete" ? "✓" : "—"} ${name}` }),
      element("span", { text: detail }),
    ])
  ));
}

function renderBundleList() {
  const sample = currentSample();
  const query = byId("bundle-search").value.trim().toLowerCase();
  const bundles = sample.bundles.filter((bundle) =>
    !query || `${bundle.number} ${bundle.observation} ${bundle.excerpts.map((x) => x.path).join(" ")}`.toLowerCase().includes(query)
  );
  const buttons = bundles.map((bundle) => {
    const judged = bundle.judge_credence != null;
    const score = judged ? `${bundle.judge_credence}% · ${bundle.judge_verdict}` : bundle.status;
    return element("button", {
      className: `bundle-button ${bundle.number === state.bundleNumber ? "selected" : ""}`,
      attributes: { type: "button" },
      on: {
        click: () => {
          state.bundleNumber = bundle.number;
          renderBundleList();
          renderBundle(bundle);
        },
      },
    }, [
      element("span", { className: "bundle-title", text: `Bundle ${bundle.number}` }),
      element("span", { className: "bundle-observation", text: bundle.observation }),
      element("span", {
        className: `bundle-state ${judged ? verdictClass(bundle.judge_verdict) : ""}`,
        text: score,
      }),
    ]);
  });
  replaceChildren("bundle-list", buttons.length ? buttons : [element("p", { className: "empty", text: "No matching bundles." })]);
}

function renderBundle(bundle) {
  if (!bundle) {
    replaceChildren("bundle-canvas", [element("p", { className: "empty", text: "No evidence bundles were retained." })]);
    return;
  }
  const credence = bundle.judge_credence == null
    ? element("div", { className: "credence", text: "—" }, [element("small", { text: "not judged" })])
    : element("div", { className: `credence ${verdictClass(bundle.judge_verdict)}` }, [
        element("div", { text: `${bundle.judge_credence}%` }),
        element("small", { text: bundle.judge_verdict }),
      ]);
  replaceChildren("bundle-canvas", [
    element("header", { className: "canvas-header" }, [
      element("div", {}, [
        element("p", { className: "eyebrow", text: `Evidence bundle ${bundle.number}` }),
        element("p", { text: bundle.observation }),
      ]),
      credence,
    ]),
    evidenceSection(bundle),
    debateSection(bundle),
    judgeSection(bundle),
    traceSection(bundle),
  ]);
}

function evidenceSection(bundle) {
  const content = bundle.excerpts.length
    ? bundle.excerpts.map((excerpt) => {
        const lines = excerpt.start_line == null ? "" : `:${excerpt.start_line}${excerpt.end_line !== excerpt.start_line ? `–${excerpt.end_line}` : ""}`;
        return element("details", { className: "excerpt" }, [
          element("summary", { text: `${excerpt.path}${lines}` }),
          element("pre", { text: excerpt.text }),
        ]);
      })
    : [element("p", { className: "empty", text: "No structured excerpts in this log." })];
  return element("section", { className: "section" }, [element("h2", { text: "Evidence" }), ...content]);
}

function debateSection(bundle) {
  if (!bundle.turns.length) {
    return element("section", { className: "section" }, [
      element("h2", { text: "Debate" }),
      element("p", { className: "empty", text: "Debate not completed for this bundle." }),
    ]);
  }
  const section = element("section", { className: "section" });
  const rounds = [...new Set(bundle.turns.map((turn) => turn.round))].sort((a, b) => a - b);
  const cards = rounds.map((round) => {
    const turns = bundle.turns.filter((turn) => turn.round === round);
    return element("div", { className: "round" }, [
      element("h3", { text: `Round ${round}` }),
      element("div", { className: "arguments" }, turns.map((turn) =>
        element("div", { className: `argument ${turn.side}` }, [
          element("h4", { text: turn.side.toUpperCase() }),
          collapsible(markdownBlock(turn.argument), { collapsedHeight: ARGUMENT_COLLAPSED_HEIGHT }),
        ])
      )),
    ]);
  });
  section.append(sectionHeader("Debate", [expandAllButton(() => section)]), ...cards);
  return section;
}

function judgeSection(bundle) {
  const body = bundle.judge_response
    ? element("div", { className: "judge-card" }, [
        collapsible(markdownBlock(bundle.judge_response), { collapsedHeight: JUDGE_COLLAPSED_HEIGHT }),
      ])
    : element("p", { className: "empty", text: bundle.judge_credence == null ? "Judge not run for this bundle." : "Only the judge credence was retained." });
  return element("section", { className: "section" }, [element("h2", { text: "Judge verdict" }), body]);
}

function traceSection(bundle) {
  const counts = bundle.trace.reduce((result, item) => {
    const key = item.kind === "tool" ? item.function : item.kind;
    result[key] = (result[key] || 0) + 1;
    return result;
  }, {});
  const summary = Object.entries(counts).map(([name, count]) => `${name} ×${count}`).join(" · ");
  const items = bundle.trace.map((item) => {
    const label = item.kind === "tool" ? `${item.role}: ${item.function}` : `${item.role}: ${item.kind}${item.model ? ` · ${item.model}` : ""}`;
    return element("details", { className: "trace-item" }, [
      element("summary", { text: label }),
      element("pre", { text: pretty({ timestamp: item.timestamp, arguments: item.arguments, usage: item.usage, content: item.content }) }),
    ]);
  });
  return element("section", { className: "section" }, [
    element("h2", { text: "Tools and model calls" }),
    bundle.trace.length
      ? element("p", { className: "muted", text: summary })
      : element("p", { className: "empty", text: "No reliably bundle-linked raw trace in this log." }),
    ...items,
  ]);
}

function renderRubric(sample) {
  const panel = byId("rubric-panel");
  if (!state.revealRubric || !sample.rubric) {
    panel.className = "hidden";
    panel.replaceChildren();
    return;
  }
  const rubric = sample.rubric;
  panel.className = "rubric";
  const blocks = [
    rubric.sabotage && rubric.sabotage.description ? rubricBlock("Sabotage description", rubric.sabotage.description) : rubricBlock("Rubric label", rubric.label === "honest" ? "This codebase is labeled honest; no sabotage annotation is provided." : rubric.label),
    rubric.sabotage && rubric.sabotage.impact ? rubricBlock("Claimed impact", rubric.sabotage.impact) : null,
    rubric.sabotage && rubric.sabotage.location ? rubricBlock("Claimed location", rubric.sabotage.location) : null,
    rubric.red_team_constraints ? rubricBlock("Red-team constraints", rubric.red_team_constraints) : null,
    rubric.acceptable_fixes?.length ? rubricBlock("Acceptable fixes", rubric.acceptable_fixes) : null,
    Object.keys(rubric.tags || {}).length ? rubricBlock("Rubric tags", rubric.tags) : null,
    rubric.normalized_location ? rubricBlock("Normalized location hint", rubric.normalized_location) : null,
    rubricBlock("Provenance", { rubric_file: rubric.rubric_file, sabotage_source: rubric.sabotage_source, workspace: rubric.workspace }),
  ].filter(Boolean);
  panel.replaceChildren(
    element("h2", { text: "Rubric context · unblinded" }),
    element("div", { className: "warning-card" }, [element("p", { text: rubric.warning })]),
    element("div", { className: "rubric-grid" }, blocks),
  );
}

function rubricBlock(title, value) {
  const body = typeof value === "string"
    ? element("p", { text: value })
    : element("pre", { text: pretty(value) });
  return element("div", { className: "rubric-block" }, [element("h3", { text: title }), body]);
}

function renderProvenance(sample) {
  const displayed = {
    source_log: report.source_log,
    task: report.task,
    status: report.status,
    task_args: report.task_args,
    revision: report.revision,
    stats: report.stats,
    sample: {
      id: sample.id,
      epoch: sample.epoch,
      target: state.revealRubric ? sample.target : "hidden",
      paper_name: sample.paper_name,
      codebase_root: sample.codebase_root,
      aggregation: sample.aggregation,
      stage_progress: sample.stage_progress,
      provenance: sample.provenance,
    },
  };
  byId("provenance-json").textContent = pretty(displayed);
}

initialize();
