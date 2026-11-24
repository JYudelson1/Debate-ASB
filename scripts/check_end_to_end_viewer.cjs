// Offline interaction checks for any saved end-to-end report.
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");

const html = fs.readFileSync(process.argv[2], "utf8");
const payload = JSON.parse(html.match(/<script id="report-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const script = [...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)]
  .filter(match => !match[1].includes("application/json"))
  .map(match => match[2]).join("\n");

class Element {
  constructor() {
    this.value = "";
    this.innerHTML = "";
    this.textContent = "";
    this.open = false;
    this.attributes = {};
    this.classList = {toggle() {}};
  }
  querySelectorAll() { return []; }
  setAttribute(key, value) { this.attributes[key] = value; }
  showModal() { this.open = true; }
  close() { this.open = false; }
}

function harness(data) {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, new Element());
    return nodes.get(id);
  };
  node("#report-data").textContent = JSON.stringify(data);
  for (const name of ["epoch", "label", "sample", "relation", "catalog"]) node("#" + name).value = "all";
  node("#order").value = "score";
  const context = vm.createContext({
    document: {querySelector: node, querySelectorAll: () => []},
    Blob, URL, setTimeout,
  });
  vm.runInContext(script, context);
  return {node, run: code => vm.runInContext(code, context)};
}

const real = harness(payload);
assert(real.node("#stats").innerHTML.includes("AUROC"));
let inspected = 0, repetitions = 0;
for (const sample of payload.samples) {
  for (const bundle of sample.bundles) {
    real.run(`inspectBundle(${JSON.stringify(bundle.key)})`);
    assert(real.node("#inspector").open);
    assert(real.node("#detail").innerHTML.includes("The retained observation"));
    assert(real.node("#detail").innerHTML.includes("Evidence supplied to debate"));
    for (const debate of bundle.debates) {
      real.run(`renderRepetition(lookup.get(${JSON.stringify(bundle.key)}), ${debate.debate_epoch})`);
      assert(real.node("#repetition-detail").innerHTML.includes("Read the six debate turns"));
      assert(real.node("#repetition-detail").innerHTML.includes("Proposed fixes"));
      repetitions++;
    }
    inspected++;
  }
}
real.node("#relation").value = "known";
real.run("renderBundles()");
assert(real.node("#bundle-list").innerHTML.includes("matching bundles"));
// A missing/unknown target label cannot establish an extraction failure.
const assessable = {label: "sabotaged", score: 10, bundles: Array.from({length: 60}, () => ({review: {target_relation: "off_target"}}))};
assert.equal(real.run(`assessableTargetCase(${JSON.stringify(assessable)})`), true);
assessable.bundles[0].review.target_relation = "unknown";
assert.equal(real.run(`assessableTargetCase(${JSON.stringify(assessable)})`), false);

// A clearly synthetic UI-only fixture exercises complete ROC/threshold states
// even while the actual experiment has no completed codebase scores.
const fixture = structuredClone(payload);
fixture.samples = [
  {id: "synthetic-positive", label: "sabotaged", score: 75, bundles: [], complete_bundles: 60},
  {id: "synthetic-negative", label: "honest", score: 25, bundles: [], complete_bundles: 60},
];
fixture.roc = {auc: 1, sabotaged: 1, honest: 1, points: [
  {threshold: null, fpr: 0, tpr: 0}, {threshold: 75, fpr: 0, tpr: 1}, {threshold: 25, fpr: 1, tpr: 1},
]};
fixture.confusion = {tp: 1, fp: 0, tn: 1, fn: 0};
const complete = harness(fixture);
for (const [threshold, expected] of [
  [0, {tp: 1, fp: 1, tn: 0, fn: 0}],
  [50, {tp: 1, fp: 0, tn: 1, fn: 0}],
  [100, {tp: 0, fp: 0, tn: 1, fn: 1}],
]) {
  assert.deepEqual(JSON.parse(complete.run(`JSON.stringify(countsAt(${threshold}))`)), expected);
  complete.run(`threshold=${threshold}; renderConfusion()`);
  assert(complete.node("#confusion").innerHTML.includes("Sabotage caught"));
}
assert.equal(complete.run("data.roc.auc"), 1);
console.log(JSON.stringify({inspected_bundles: inspected, inspected_repetitions: repetitions, threshold_checks: 3}));
