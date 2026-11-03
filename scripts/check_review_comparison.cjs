// Offline interaction and aggregation checks for a saved re-review snapshot.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8');
const payload=JSON.parse(html.match(/<script id="review-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const script=[...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)].filter(m=>!m[1].includes('application/json')).map(m=>m[2]).join('\n');
class Element{
  constructor(){this.textContent='';this.value='';this._html='';this.open=false;this.classList={toggle(){}};}
  set innerHTML(value){this._html=value;const m=value.match(/<option value="([^"]*)"/);if(m)this.value=m[1];}
  get innerHTML(){return this._html;}
  addEventListener(){} showModal(){this.open=true;} close(){this.open=false;}
}
function harness(data){
  const nodes=new Map(),node=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  node('#review-data').textContent=JSON.stringify(data);
  for(const id of ['variant','sample','epoch','new-label'])node('#'+id).innerHTML='<option value="all">All</option>';
  node('#change').innerHTML='<option value="changed">Changed labels</option>';node('#metric').value='complete';node('#matrix-metric').value='complete';
  const context=vm.createContext({document:{querySelector:node,querySelectorAll:()=>[],addEventListener(){}},setTimeout,clearTimeout,Blob,URL,location:{reload(){}}});
  vm.runInContext(script,context);return {node,run:code=>vm.runInContext(code,context)};
}
const h=harness(payload),run=h.run;
assert.equal(run('data.items.length'),4380);assert.equal(run('data.cases.length'),231);
assert.equal(payload.counts.reviewed,4380);assert.equal(payload.counts.invalid,0);
assert.equal(payload.counts.strict_validation_passed,4362);assert.equal(payload.counts.scored_with_warnings,18);
assert.equal(payload.counts.changed,698);assert(payload.complete);
assert(h.node('#stats').innerHTML.includes('Labels changed'));
assert(h.node('#codebase-matrix').innerHTML.includes('Not in cohort'));
assert.equal(run('data.variants.find(v=>v.id==="evidence_closure").codebases'),11);
assert.equal(run('data.variants.find(v=>v.id==="evidence_closure").bundles'),660);
assert(run('filtered.every(i=>i.changed===true&&i.new_label!=="pending")'));
for(const metric of ['complete','presence']){
  h.node('#metric').value=metric;run('renderScores()');
  h.node('#matrix-metric').value=metric;run('renderMatrix()');
  for(const variant of payload.variants){
    const m=variant[metric];assert.equal(m.new-m.old,m.up-m.down);assert.equal(m.n+m.unresolved,variant.epochs);
  }
}
let inspected=0;
for(const item of payload.items){
  run(`inspect(${JSON.stringify(item.id)})`);
  assert(h.node('#inspector').open);
  assert(h.node('#inspect-body').innerHTML.includes('Original saved label'));
  assert(h.node('#inspect-body').innerHTML.includes('Saved ground-truth mechanism'));
  assert(h.node('#inspect-body').innerHTML.includes('Retained snippets'));inspected++;
  if(item.new_review.validation_status==='counted_with_warnings'){
    assert(h.node('#inspect-body').innerHTML.includes('Counted with citation/schema warnings'));
    assert(h.node('#inspect-body').innerHTML.includes('preserved raw attempts'));
  }
}
for(const change of ['changed','same','gained','lost','unknown','pending','distractor','all','invalid']){
  h.node('#change').value=change;run('page=0;renderBundles()');
  if(change==='gained')assert(run('filtered.every(i=>i.new_label==="complete"&&i.old_label!=="complete")'));
  if(change==='lost')assert(run('filtered.every(i=>i.old_label==="complete"&&i.new_label!=="complete"&&i.new_label!=="pending")'));
  if(change==='invalid')assert.equal(run('filtered.length'),18);
}
assert(h.node('#method-copy').innerHTML.includes('Count verdicts, flag citation/schema issues'));
for(const example of payload.prompt_examples){
  h.node('#prompt-sample').value=example.job_id;run('renderPromptExample()');
  assert(h.node('#prompt-example').innerHTML.includes('The single evidence bundle'));
  assert(h.node('#prompt-example').innerHTML.includes('Complete user message'));
}
assert.equal(new Set(payload.prompt_examples.map(e=>payload.items.find(i=>i.id===e.job_id).variant)).size,11);
assert.equal(new Set(payload.prompt_examples.map(e=>e.packet.evidence_bundle.sample_id)).size,11);
run('filterVariant("evidence_closure","hop_jump","all")');
assert.equal(run('filtered.length'),60);assert(run('filtered.every(i=>i.variant==="evidence_closure"&&i.sample_id==="hop_jump")'));
h.node('#epoch').value='2';run('renderBundles()');assert.equal(run('filtered.length'),20);assert(h.node('#case-summary').innerHTML.includes('Extraction 2'));
h.node('#search').value='no-such-string-019237';run('renderBundles()');assert.equal(run('filtered.length'),0);
assert(h.node('#bundle-list').innerHTML.includes('No bundles match'));
assert.equal(run('esc("<script>unsafe</script>")'),'&lt;script&gt;unsafe&lt;/script&gt;');
console.log(JSON.stringify({inspected_bundles:inspected,variants:payload.variants.length,metric_checks:2,filters:9,prompt_examples:payload.prompt_examples.length,review_complete:payload.complete}));
