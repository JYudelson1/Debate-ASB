# Reusable extraction comparison reports

The historical generator for the original hypothesis-worker report is
`artifacts/extraction-iteration/hypothesis-workers-20261012/build_report.py`.
It uses the adjacent `report.template.html`; the extraction-analysis skill's
renderer produces the separate per-condition reports embedded in that study.
The original scripts and reports remain available for reproducing that run.

The reusable version lives in `src/debate_asb/extraction_analysis/comparison.py`, with an
entry point at `scripts/build_extraction_comparison.py`. Its assets are under
`src/debate_asb/viewer/static/extraction_comparison/`. It preserves the study
report's stylesheet, comparison bars, codebase matrix, bundle browser and
inspection heatmaps. Variant names, sample IDs, epoch counts and settings come
from inputs. Single-variant reports and differing cohorts are supported.

Run locally, without API calls:

```bash
uv run python scripts/build_extraction_comparison.py \
  --config path/to/report_config.json --out path/to/report-directory
```

The equivalent module command is `uv run python -m debate_asb.extraction_analysis.comparison`.
Outputs are a standalone `report.html`, `report.md`, and `results.json`. The HTML
embeds its styles, script, excerpts and complete returned tool results; it needs
no server, hosted assets or model calls.

A report configuration has this shape. Paths resolve relative to the configuration
file. Add any number of variants; `baseline` and presentation text are optional.

```json
{
  "schema_version": 1,
  "title": "Experimental validity framing",
  "description": "Three epochs with the supplied prompt and an existing baseline.",
  "hypothesis": "The user-supplied hypothesis, without generated interpretation.",
  "date": "15 October 2026",
  "baseline": "original",
  "variants": [
    {
      "id": "original",
      "label": "Original sabotage framing",
      "historical": true,
      "analysis": "baseline/analysis.json",
      "review": "baseline/review.json",
      "prompt": "baseline/extract.md",
      "settings": {"model": "openai/gpt-6-luna", "steps": 40, "bundles": 6}
    },
    {
      "id": "validity",
      "label": "Experimental validity framing",
      "analysis": "validity/analysis/analysis.json",
      "review": "validity/analysis/review.json",
      "status": "validity/status.json",
      "prompt": "extract.md",
      "settings": {"model": "openai/gpt-6-luna", "steps": 40, "bundles": 6}
    }
  ],
  "notes": [],
  "methods": [],
  "other_costs": []
}
```

Each variant consumes an existing extraction `analysis.json`, a completed
`review.json`, and the trace files referenced by the analysis. These are the
existing extraction-analysis schemas; this generator does not generate or
modify semantic labels. Review identity, reference binding, measurement hashes,
trace hashes and bundle review coverage are checked. Unknown and failed cases
must stay explicit. Finalize substantive source-based reviews with the analysis
validator before rendering; report rendering does not replace that review.

The repository collector also supports the
[bounded validity question protocol](hypothesis-workers.md). New analyses carry
explicit extraction completion and zero-candidate flags, plus all coordinator
briefs and worker reports. A completed zero-candidate extraction counts as parsed
and as a capture miss for eligible sabotaged cases; it remains in the denominator.
Its dispositions are inspectable in the evidence browser. Older analyses without
these fields retain their original bundle-based completion behavior. Failed or
partial investigations retain their raw reports and errors separately.

Optional `status` supplies `billed_total`, `api_calls_total`, `first_pass_parsed`
and `retry_attempts`. Missing costs remain unknown, rather than zero. Additional
cost rows accept `label`, `billed_cost`, `api_calls`, and optional `scope`.

Capture uses reviewed eligible sabotaged sample epochs (`yes`, `partial`, `no`)
as its denominator. Partial evidence contributes zero captures. A separate
all-slot yield includes failed outputs as zero. Sufficiency uses reviews with
a resolved completeness judgment. The matrix uses each sample's actual epoch
count; missing epochs are not invented. Baseline comparisons report common
sample/epoch labels with jointly resolved reviews; these labels do not establish
matched seeds, statistical independence or a causal prompt effect.

The report records configuration, generator, asset, analysis, review and trace
hashes, along with the source analysis manifests. Preserve the configuration,
analyses, reviews and traces to regenerate it. Rebuilding does not overwrite
the original experiment logs or review ledgers.
