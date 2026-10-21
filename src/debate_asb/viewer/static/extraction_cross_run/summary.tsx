import { useState, useHostTheme, Stack, Row, Grid, H1, H2, Text, Stat, Select, Table, BarChart, Divider, Link } from "cursor/canvas";

const DATA = __CANVAS_DATA__;
const pct = (x: number) => `${(100*x).toFixed(1)}%`;

export default function ExtractionCrossRunSummary() {
  const theme = useHostTheme();
  const [mode, setMode] = useState("revised");
  const [codebase, setCodebase] = useState(DATA.codebases[0]);
  const original = mode === "original";
  const summary = original ? DATA.original : DATA.revised;
  const focus = DATA.examples.filter(e => e.sample_id === codebase);
  return <Stack gap={20} style={{padding:24, background:theme.bg.editor, color:theme.text.primary}}>
    <Text size="sm" tone="secondary">EXTRACTION / CROSS-RUN REPORT</Text>
    <H1>{DATA.title}</H1>
    <Text>{DATA.variants.length} requested protocol variants, {DATA.codebase_count} codebases, {DATA.attempts} retained sample epochs, and {DATA.bundle_count} evidence bundles. The full report contains every bundle, tool response, scoring correction, and reproducibility record.</Text>
    <Row gap={18} wrap>
      <Select value={mode} onChange={setMode} options={[{value:"revised",label:"Audited mechanism presence"},{value:"original",label:"Original report labels"}]} />
      <Link href={DATA.report}>Open the complete interactive HTML report</Link>
    </Row>
    <Grid columns={3} gap={20}>
      <Stat value={`${summary.caught}/${DATA.eligible}`} label="Sample epochs with capture" />
      <Stat value={String(DATA.changed)} label="Source-bound scoring corrections" />
      <Stat value={`${summary.read_misses}/${DATA.eligible-summary.caught}`} label="Misses after a target-region read" />
    </Grid>
    <Divider />
    <H2>Coverage and repeated-read associations</H2>
    <Text>Pooled and within-group correlations are descriptive. These observations do not establish an intervention effect.</Text>
    <Table framed={false} headers={["Exposure measure", "Pooled r", "Within codebase r", "Within codebase × variant r"]} rows={summary.correlations.map(r => [r.label, r.pooled == null ? "—" : r.pooled.toFixed(2), r.within == null ? "—" : r.within.toFixed(2), r.cell == null ? "—" : r.cell.toFixed(2)])} columnAlign={["left","right","right","right"]} />
    <Text size="sm" tone="secondary">Source: retained extraction traces · n={DATA.eligible}. Coverage is distinct returned source/configuration lines over the saved reference inventory. “Within” means correlation after demeaning within each group. Target-region hits measure presented locations, not understanding.</Text>
    <H2>Capture by protocol variant</H2>
    <Text size="sm" tone="secondary">Y axis: protocol variant. X axis: sample-epoch capture rate (%), using each variant’s retained sample epochs.</Text>
    <BarChart horizontal height={310} yMax={100} valueSuffix="%" categories={DATA.variants.map(v=>v.short)} series={[{name:original?"Original capture rate":"Audited mechanism capture rate",tone:"success",data:DATA.variants.map(v=>100*(original?v.original:v.revised)/v.eligible)}]} showValues />
    <Text size="sm" tone="secondary">Source: configured run variants. Prompts, bundle caps, budgets and retry policies differ; these are descriptive comparisons.</Text>
    <Divider />
    <H2>Capture across codebases and epochs</H2>
    <Table framed={false} headers={["Codebase", "Captured epochs", "Runs with ≥1 caught epoch", "Runs with every epoch caught"]} rows={summary.hotspots.map(h=>[h.sample_id,`${h.caught}/${h.eligible}`,`${h.any}/${h.runs} · ${pct(h.any/h.runs)}`,`${h.all}/${h.runs} · ${pct(h.all/h.runs)}`])} />
    <Text size="sm" tone="secondary">One run here means one variant × codebase cohort across the observed epochs. Inspect occasional success separately from reliability across epochs.</Text>
    <H2>Retained bundle allocation</H2>
    <Table framed={false} headers={["Bundle content", "Slots", `Share of all ${DATA.bundle_count} bundles`]} rows={summary.allocation.map(a=>[a.label,a.count,pct(a.count/DATA.bundle_count)])} columnAlign={["left","right","right"]}/>
    <Text size="sm" tone="secondary">Known-signature containment is not necessarily the bundle’s semantic focus. Possible catalog matches preserve quote-fidelity failures and location hints. Other material includes ordinary code and uncatalogued concerns; it is not automatically a bug or wasted work.</Text>
    <Divider />
    <H2>Inspect cross-run samples</H2>
    <Select value={codebase} onChange={setCodebase} options={DATA.codebases.map(s=>({value:s,label:s}))}/>
    {focus.map(e=><details key={e.row_id} style={{borderTop:`1px solid ${theme.stroke.tertiary}`,paddingTop:10}}>
      <summary style={{cursor:"pointer",fontWeight:600}}>{e.label} · epoch {e.epoch} · {original?e.original:e.revised}</summary>
      {e.bundles.map(b=><div key={b.number} style={{marginTop:16}}>
        <Text weight="semibold">Bundle {b.number} · {b.topic}</Text>
        <Text>{b.observation}</Text>
        <Text size="sm" tone="secondary">Catalog annotations: {b.catalog || "none"}.</Text>
        {b.excerpts.map((x,i)=><details key={i}>
          <summary style={{cursor:"pointer",color:theme.text.secondary}}>{x.path}:{x.start_line}–{x.end_line} · {x.fidelity?.status||"unknown fidelity"}</summary>
          <pre style={{whiteSpace:"pre-wrap",fontSize:11,background:theme.fill.tertiary,padding:12,overflow:"auto"}}>{x.text}</pre>
        </details>)}
      </div>)}
    </details>)}
    <Divider />
    <Text size="sm" tone="secondary">Scoring follows the supplied source-bound audit. Original labels, completeness judgments and fidelity warnings are preserved. Local analysis only; no experiment or model API calls.</Text>
  </Stack>;
}
