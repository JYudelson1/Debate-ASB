// Reproducible offline behavior checks, alongside real-browser visual checks.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const html=fs.readFileSync(process.argv[2],'utf8');
const data=JSON.parse(html.match(/<script id="audit-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const script=[...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)].filter(m=>!m[1].includes('application/json')).map(m=>m[2]).join('\n');
class Element{
  constructor(){this.value='';this.textContent='';this._html='';this.classList={toggle(){},add(){}};this.dataset={};this.scrollTop=0;this.open=false;this.disabled=false;}
  set innerHTML(value){this._html=value;const m=value.match(/<option value="([^"]*)"/);if(m)this.value=m[1];}
  get innerHTML(){return this._html;}
  addEventListener(){} focus(){} click(){} showModal(){this.open=true;} close(){this.open=false;}
}
const memory=new Map();
function harness(){
  const nodes=new Map(),listeners={};
  const node=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  node('#audit-data').textContent=JSON.stringify(data);
  node('#sample').innerHTML='<option value="all">All codebases</option>';node('#status').value='todo';node('#order').value='spread';
  const context=vm.createContext({
    document:{querySelector:node,querySelectorAll:()=>[],body:{classList:{add(){},toggle(){}}},addEventListener:(event,fn)=>{listeners[event]=fn;},createElement:()=>new Element()},
    window:{addEventListener(){}},location:{hash:'',href:'http://localhost/audit'},navigator:{},
    localStorage:{getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,v)},
    setTimeout,clearTimeout,Blob,URL,structuredClone,console,
  });
  vm.runInContext(script,context);
  return {node,run:code=>vm.runInContext(code,context),listeners};
}
async function main(){
  const h=harness(),run=h.run;
  assert.equal(run('tasks.fix.length'),data.counts.fixes);
  assert.equal(run('tasks.coverage.length'),data.counts.model_reviews);
  assert.equal(run('tasks.prompt.length'),data.counts.prompt_examples);
  assert(run('filtered.every(t=>["yes","partial"].includes(t.label))'));
  assert(run('new Set(filtered.slice(0,11).map(t=>t.item.sample_id)).size')>1);
  // Every fix must join by both repetition and rank, not slot/rank alone.
  assert(run('tasks.fix.every(t=>t.fix.rank===t.judgment.rank&&t.fix.debate_epoch===t.judgment.debate_epoch)'));
  const first=run('selected'),second=run('filtered[1].id'),count=run('filtered.length');
  run('saveNote("Check the mechanism, <script>never execute</script>")');
  run('decide("disagree","no")');
  assert.equal(run(`audits[${JSON.stringify(first)}].decision`),'disagree');
  assert.equal(run('filtered.length'),count-1);
  assert.equal(run('selected'),second);
  run('undo()');assert.equal(run('selected'),first);
  assert.equal(h.node('#status').value,'todo');
  assert.equal(run(`audits[${JSON.stringify(first)}].decision`),undefined);
  assert(run(`audits[${JSON.stringify(first)}].note`).includes('mechanism'));
  run('decide("agree",allTasks.get(selected).label)');
  const reload=harness();
  assert.equal(reload.run(`audits[${JSON.stringify(first)}].decision`),'agree');
  assert.notEqual(reload.run('selected'),first);
  run('setMode("coverage")');
  run('document.querySelector("#verdict").value="disagreement";refresh()');
  assert(run('filtered.every(t=>t.item.analyst_review.target_relation!==t.label)'));
  run('setMode("prompt")');
  assert(h.node('#detail').innerHTML.includes('Exact system text'));
  run('promptTab="packet";renderDetail()');
  assert(h.node('#detail').innerHTML.includes('Full paper as supplied'));
  run('document.querySelector("#verdict").value="retry";refresh()');
  assert(run('filtered.length')>0);
  assert(run('filtered.every(t=>JSON.parse(t.prompt.user).previous_validation_feedback)'));
  run('promptTab="raw";renderDetail()');
  assert(h.node('#detail').innerHTML.includes('User message · exact JSON text'));
  run('decide("disagree","revise")');
  assert(run('Object.values(audits).some(r=>r.decision==="disagree"&&r.corrected==="revise")'));
  run('setMode("summary")');
  assert(h.node('#detail').innerHTML.includes('never execute&lt;/script&gt;'));
  assert(!h.node('#detail').innerHTML.includes('<script>never execute'));
  // Exact prompt and input fingerprints for all sampled requests.
  for(const p of data.prompt_examples){
    const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
    assert.equal(hash(p.system),p.prompt_sha256);assert.equal(hash(p.user),p.packet_sha256);
  }
  run('globalThis.exported=null;download=(value)=>{globalThis.exported=value;};exportAudit()');
  const exported=JSON.parse(run('JSON.stringify(exported)'));
  assert.equal(exported.kind,'debate-asb-human-review-audit');
  run('globalThis.savedForImport=JSON.stringify(exported);audits={};');
  await run('importAudit({text:async()=>savedForImport})');
  assert.equal(run('Object.keys(audits).length'),Object.keys(exported.records).length);
  assert.equal(run(`validateImportedRecord(${JSON.stringify(first)},{fingerprint:"wrong",decision:"agree"})`),false);
  assert.equal(run(`validateImportedRecord(${JSON.stringify(first)},{...audits[${JSON.stringify(first)}],updated_at:[]})`),false);
  // Typing A inside notes cannot record an assessment.
  run('setMode("fix")');const before=run('Object.keys(audits).length');
  h.listeners.keydown({key:'a',target:{matches:()=>true},preventDefault(){throw Error('should ignore typed input');}});
  assert.equal(run('Object.keys(audits).length'),before);
  run('document.querySelector("#search").value="no-such-text-012345";refresh()');
  assert.equal(run('filtered.length'),0);assert(h.node('#detail').innerHTML.includes('No items match'));
  console.log(JSON.stringify({fixes:data.counts.fixes,coverage_reviews:data.counts.model_reviews,exact_prompt_examples:data.counts.prompt_examples,checks:'joins, cross-codebase order, corrections, notes, undo, reload, filters, retries, export/import, escaping, keyboard, empty queue'}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
