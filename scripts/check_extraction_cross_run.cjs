// Offline interaction/aggregation checks. No browser, network, or model calls.
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const assert = require('assert/strict');
const data = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const defaults = {
  'score-filter': 'revised', 'coverage-metric': 'coverage',
  'repeat-metric': 'target_read_events', 'browse-outcome': 'all',
  'browse-group': 'all', 'browse-bug': 'all', 'browse-paper': 'all', 'browse-catalog': 'all', 'browse-strength': 'all',
};
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: defaults[id] || '', textContent: id === 'report-data' ? JSON.stringify(data) : '',
    html: '', listeners: {},
    set innerHTML(value) {
      this.html = value;
      const options = [...value.matchAll(/<option value="([^"]*)"/g)].map(m=>m[1]);
      if (options.length && !options.includes(this.value)) this.value = options[0];
    },
    get innerHTML(){return this.html;},
    addEventListener(name, callback){this.listeners[name]=callback;},
    scrollIntoView(){},
  });
  return elements.get(id);
}
const context = {document: {getElementById: element, addEventListener(){}}, console, URL, Blob, setTimeout};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/debate_asb/viewer/static/extraction_cross_run/report.js'),'utf8'), context);
const run = code => vm.runInContext(code, context);
const eligible = data.samples.filter(r => r.eligible);
assert(element('headline-stats').innerHTML.includes(`${eligible.filter(r=>r.captured).length}/${eligible.length}`));
for (const [metric, values] of Object.entries(data.summary.associations)) {
  for (const [name,strata] of [['pooled',[]],['within_codebase',['sample_id']],['within_variant',['variant']],['within_cell',['sample_id','variant']]]) {
    const actual = JSON.parse(run(`JSON.stringify(assoc(DATA.samples, ${JSON.stringify(metric)}, ${JSON.stringify(strata)}))`));
    assert.equal(actual.n, values[name].n);
    if (values[name].r === null) assert.equal(actual.r, null);
    else assert(Math.abs(actual.r-values[name].r)<1e-12, `${metric}/${name}`);
  }
}
element('score-filter').value='original'; run('render()');
assert(element('headline-stats').innerHTML.includes(`${eligible.filter(r=>r.original_included==='yes').length}/${eligible.length}`));
element('score-filter').value='revised';
let filters=0;
for (const variant of ['all', ...data.variants.map(v=>v.id)]) {
  element('variant-filter').value=variant;
  for (const sid of ['all', ...new Set(data.samples.map(r=>r.sample_id))]) {
    element('codebase-filter').value=sid;run('render()');
    for (const id of ['headline-stats','coverage-stats','hotspot-matrix','attention-total']) {
      assert(!/NaN|undefined|Infinity/.test(element(id).innerHTML), `${variant}/${sid}/${id}`);
    }
    filters++;
  }
}
element('variant-filter').value='all';element('codebase-filter').value='all';
for (const coverage of ['coverage','coverage_verified','source_coverage','coverage_search','file_coverage']) {
  element('coverage-metric').value=coverage;
  for (const metric of ['target_read_events','target_repetitions_per_line','same_agent_target_repeats','target_region_fraction']) {
    element('repeat-metric').value=metric;run('renderExposure(selected())');
  }
}
let samples=0;
for (const row of data.samples) {
  run(`openSample(${JSON.stringify(row.row_id)})`);
  assert(element('sample-detail').innerHTML.includes(row.sample_id));
  assert(element('sample-detail').innerHTML.includes('Full returned extraction tool trace'));
  if(row.no_qualifying_evidence)assert(element('sample-detail').innerHTML.includes('no qualifying evidence'));
  samples++;
}
element('browse-codebase').value='all';element('browse-variant').value='all';
for (const group of ['all','target','related','known','known_possible','other']) {
  element('browse-group').value=group;
  for (const bug of ['all','yes','no','unclear']) {
    element('browse-bug').value=bug;run('renderBrowser()');
    assert(!element('sample-detail').innerHTML.split('<details>')[0].includes('undefined'));
  }
}
element('browse-group').value='all';
for (const bug of ['all','yes','no','unclear']) {
  element('browse-bug').value=bug;
  for (const paper of ['all','consistent','contradicts','not_specified','unclear']) {
    element('browse-paper').value=paper;
    for (const category of ['all','bug','quirk','mismatch','misleading']) {
      element('browse-catalog').value=category;
      for (const strength of ['all','code_match','unverified_quote_match','location_hint']) {
        element('browse-strength').value=strength;run('renderBrowser()');
        assert(!element('sample-detail').innerHTML.split('<details>')[0].includes('undefined'), JSON.stringify({bug,paper,category,strength}));
      }
    }
  }
}
const csvRows = data.samples.length;
assert.equal(new Set(data.samples.map(r=>r.row_id)).size,csvRows);
console.log(JSON.stringify({sample_selections:samples,scope_filter_combinations:filters,metric_combinations:20,bundle_filter_combinations:424,python_js_associations:44}));
