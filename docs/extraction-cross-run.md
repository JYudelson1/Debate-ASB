# Cross-run extraction analysis

This is a separate report template from `extraction_analysis/comparison.py`. It combines saved
analyses across studies without running an experiment or calling a model.

The first report configuration is
`artifacts/extraction-cross-run/20261019/config.json`. It selects the seven
requested variants, excluding comparison baselines and all expanded-budget worker
variants except Ten hypotheses. Its 231 sample epochs and 1,279 bundles retain
their original observations, excerpts, semantic bundle reviews and tool results.

Build the standalone report:

```sh
.venv/bin/python scripts/build_extraction_cross_run.py \
  --config artifacts/extraction-cross-run/20261019/config.json \
  --out artifacts/extraction-cross-run/20261019
```

Outputs: `report.html`, `report.md`, `results.json`, `sample-metrics.csv`, and
`provenance.json`. The HTML includes all data and code; it works offline and has
no remote assets. It is large because it retains the original tool results inline.

## Reuse with different inputs

Copy the configuration to a new output directory and edit `variants`. Each entry
specifies `id`, `label`, `analysis`, `review`, `results`, and `source_variant`.
Paths resolve relative to the configuration. `results` supplies saved worker
ownership/hypotheses and historical costs; the analysis/review files supply the
validated measurements and semantic labels. Reused log/sample/epoch identities
are rejected instead of being counted twice. Extra comparator variants in a
source results file are excluded by `source_variant`.

Set `worker_log_owners: true` for worker protocols whose source report did not
already retain ownership; the generator reads the existing `.eval` log locally
and resolves tool events to worker spans. It does not infer that every duplicate
read in a worker cohort was a reread by the same agent.

Freeze a new reference inventory before reviewing targets:

```sh
.venv/bin/python scripts/build_extraction_cross_run.py \
  --config path/to/config.json --snapshot-inventory
```

This writes the configured `inventory` path only if it does not exist. It saves
file hashes/roles/denominators and the source/configuration/document text needed
to rerender deterministically. This is a local reference snapshot; agreement of
returned lines does not prove the historical content of unread files.

Prepare explicit `target_units` and `target_review` JSON files. Their minimal
shapes are shown below. Target regions are reviewed implementation ranges for
exposure measurement, not an automatic capture classifier. Target audit rows are
keyed by `<variant-id>:<sample-key>` and bind to `bundles_sha256`,
`reference_sha256`, and `original_review_sha256` using `fingerprint()` from
`extraction_analysis/comparison.py`. Render requires exactly one audited row per selected
sample. Existing yes/partial/no decisions can be retained after review; capture
overrides require a source-based explanation. Keep old reviews unchanged.

For a yes row, list actual target-bearing bundles in `matching_bundles`. Keep
related-only bundles separately in `related_bundles`. Preserve original quote
fidelity and completeness judgments. In particular:

- Presence asks whether actual checked altered implementation is retained; it
  does not require a complete accusation or empirical-impact proof.
- `awareness_lens`: distinguish the adapter-configured vector producer from proof
  of an existing tensor's historical origin. Cache-only citations remain related.
- A checked secondary alteration can count as any-mechanism capture; a
  primary-component-only outcome is a different question. The current report
  makes its any-mechanism policy explicit for `lazy_alpha` and `negative_mute`.
- A caller without its defining altered transformation remains related. Neither
  same-file overlap nor a catalog marker supplies a semantic capture verdict.

## Metric definitions

- Coverage: distinct returned source/configuration path/line pairs divided by
  saved reference source/configuration lines. Alternatives include source only,
  opened-file share, reads plus returned search hits, and verified-prefix lines.
- Target frequency: read calls intersecting reviewed regions; presentations per
  distinct target line; additional presentations within each agent; fraction of
  the region read. Search and execution output are separate from explicit reads.
- Association: point-biserial Pearson correlation against binary capture; plus
  correlations after demeaning within codebase, variant, or codebase × variant.
  All use complete eligible traces. No independence assumptions or p-values.
- Reliability: within each variant/codebase cohort, ≥1 caught epoch versus every
  observed epoch caught. A missing epoch is not invented; unresolved slots cannot
  count as all caught. All sample epochs are also shown separately.
- Bundle allocation: target first, verified catalog signature second, possible
  catalog match third, paper-only fourth, other/unclassified last. Known issues
  and target code can coexist; independent annotations remain visible inline.
- Browsing allocation: line presentations including repeats, with target region
  / catalog signature / PAPER.md / other precedence. Catalog signature lines are
  a lower bound; the measure does not estimate total time or token spend.

The report retains accepted retries as defined by each source study. Completed
zero-candidate outputs remain misses. Source-report spending, extraction revision,
prompt hashes, provider, sample IDs, epochs and absent seeds remain recoverable.
No new experiment or causal intervention is performed.

## Checks

```sh
.venv/bin/pytest -q tests/test_extraction_cross_run.py
node scripts/check_extraction_cross_run.cjs \
  artifacts/extraction-cross-run/20261019/results.json
```

The offline interaction checker exercises all sample selections, scope filters,
metric alternatives and bundle filters; it also compares JavaScript correlations
with Python calculations. It is not a visual browser-rendering check.

## Replay the historical 20 October capture correction

The 20 October update promotes the audited capture labels and matching bundle
numbers into the individual reports. It also refreshes their comparison totals,
historical copies of the same cohort, and local sharing copies:

```sh
.venv/bin/python scripts/migrations/apply_capture_audit_20261020.py \
  --config artifacts/extraction-cross-run/20261019/config.json \
  --history artifacts/extraction-cross-run/20261019/propagation-20261020
```

Add `--dry-run` to validate and list the changes without writing them. Original
files are archived under the history directory. The cross-run configuration's
`review` paths point to the frozen original reviews, preserving its original-score
toggle; `source_review` points to each current, corrected individual review.
Extraction observations, excerpts, traces, completeness, and independent
bug/candidate/paper judgments are preserved. No API calls or hosting deployment
are involved.


## Input shapes for a new report

Paths are relative to the configuration file. The source analysis and review
use the collector schemas; `results` is an existing comparison report containing
the specified `source_variant`. Each selected log/sample/epoch must be unique.

```json
{
  "schema_version": 1,
  "title": "Cross-run extraction comparison",
  "inventory": "inventory.json",
  "target_units": "target-units.json",
  "target_review": "target-review.json",
  "variants": [{
    "id": "condition", "label": "Condition",
    "analysis": "condition/analysis.json", "review": "condition/review.json",
    "results": "comparison/results.json", "source_variant": "condition"
  }]
}
```

A target-units file contains `samples`, keyed by sample ID. Each value has
`regions`: a list of objects with `path`, `start`, `end`, and optional
`reference_sha256`. Bounds are inclusive and refer to the frozen inventory.
A target-review file contains `policy` (the explicit scoring policy) and
`samples`, keyed by `<variant-id>:<collector-sample-key>`. Each row contains:

- `included`: reviewed `yes`, `partial`, `no`, `unknown`, `mismatch`, or
  `not_applicable`; unresolved and control cases remain outside capture rates.
- `matching_bundles`: retained bundle numbers establishing capture; nonempty
  exactly when `included` is `yes`.
- `related_bundles`: retained related-only bundle numbers.
- `bundles_sha256`, `reference_sha256`, `original_review_sha256`: fingerprints
  of the collected bundles, sabotage reference and original sample review.
- `reason`: the source-based rationale; preserve supporting verification records.

The optional summary accepts `example_rows` in the report configuration (explicit
row IDs; otherwise the first eight rows) and `short_label` on variant entries.
Titles, labels, denominators and example selection come from inputs; the template
does not embed the conclusions or dates of the original study.
