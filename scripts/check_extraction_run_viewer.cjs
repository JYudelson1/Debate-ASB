// Offline interaction checks for the combined, existing bundle viewer.
const fs = require("fs");
const vm = require("vm");
const assert = require("assert/strict");
const html = fs.readFileSync(process.argv[2], "utf8");
const data = JSON.parse(html.match(/<script id="report-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
class Node {
  constructor(tag="div") {
    this.tag=tag; this.children=[]; this.handlers={}; this.value=""; this._text="";
    this.style={setProperty(){}};
    const classes=new Set();
    this.classList={add:c=>classes.add(c),contains:c=>classes.has(c),toggle(c,on){if(on)classes.add(c);else classes.delete(c);}};
  }
  get textContent(){return this._text+this.children.map(c=>c.textContent).join(" ");}
  set textContent(value){this._text=String(value);this.children=[];}
  get childElementCount(){return this.children.length;}
  set innerHTML(value){this._text=value;}
  appendChild(child){this.children.push(child);return child;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this._text="";this.children=children;}
  setAttribute(){}
  addEventListener(type,handler){this.handlers[type]=handler;}
  dispatchEvent(event){this.handlers[event.type]?.(event);}
  scrollIntoView(){}
  querySelectorAll(){return [];}
}
const nodes=new Map();
const get=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
get("report-data").textContent=JSON.stringify(data);
const context={document:{getElementById:get,createElement:tag=>new Node(tag)},requestAnimationFrame:cb=>cb(),Event:class{constructor(type){this.type=type;}}};
vm.createContext(context);
for(const match of html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)){
  if(!match[1].includes('application/json')) vm.runInContext(match[2],context);
}
const run=code=>vm.runInContext(code,context);
assert.equal(get("sample-select").children.length,data.samples.length);
get("reveal-ground-truth").dispatchEvent({type:"click"});
let bundles=0, controls=0, traces=0;
for(let i=0;i<data.samples.length;i++){
  const sample=data.samples[i];
  get("sample-select").value=String(i);
  get("sample-select").dispatchEvent({type:"change"});
  assert(get("run-tags").textContent.includes(sample.id));
  if(sample.run_label){assert(get("run-tags").textContent.includes(sample.run_label));assert(get("sample-select").children[i].textContent.includes(sample.run_label));}
  if(sample.target==="honest"){
    assert(get("extraction-inspection").textContent.includes("target capture is not applicable"));
    controls++;
  }
  for(const bundle of sample.bundles){
    run(`selectBundle(${bundle.number})`);
    assert(get("bundle-canvas").textContent.includes(bundle.observation));
    bundles++;
  }
  const inspection=get("extraction-inspection").children.find(n=>n.tag==="details"&&n.textContent.includes("Extraction inspection"));
  if(inspection){inspection.open=true;inspection.dispatchEvent({type:"toggle"});assert(inspection.textContent.includes("Tools and model calls"));traces++;}
}
const buttons=get("run-overview").children.filter(n=>n.tag==="button");
if(html.includes('id="run-overview"'))assert.equal(buttons.length,data.runs.length);
buttons.forEach((button,i)=>{button.dispatchEvent({type:"click"});assert.equal(run("state.sampleIndex"),data.runs[i].first_sample_index);});
console.log(JSON.stringify({sample_selections:data.samples.length,bundle_selections:bundles,honest_controls:controls,trace_expansions:traces,variant_buttons:buttons.length}));
