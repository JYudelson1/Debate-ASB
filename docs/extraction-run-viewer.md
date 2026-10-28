# Extraction run viewer, including unsabotaged controls

Combine extraction logs without assigning semantic judgments:

```sh
.venv/bin/python scripts/build_extraction_run_viewer.py \
  --config artifacts/extraction-iteration/hypothesis-scaling-20261021/run_config.json \
  --out artifacts/extraction-iteration/hypothesis-scaling-20261021/report.html
```

This reuses the existing bundle viewer. Its single offline HTML file includes
condition/sample/epoch selection, coordinator hypotheses, worker allocation,
retained observations/excerpts, and extraction tools/model calls. Worker ownership
comes from explicit logged spans. Reveal the reference/control status to inspect
the checked mechanism or the honest-control label.

The configuration supplies `epochs` and a `conditions` object; each condition
has `hypotheses` and `sample_ids`. Logs live in a sibling folder named by the
condition ID, with optional Inspect `retries/round-N/` folders. An optional
`status.json` identifies the accepted final log and records completion. Partial
reports use the latest round, retaining failures and unfinished slots explicitly.

Control status comes from logged labels, not the condition name. Target capture
is inapplicable to honest controls. Evidence counts are not detection verdicts
or false-positive counts. This viewer does not invent semantic review labels.

The adjacent summary JSON retains run configuration, log hashes, generator hash,
accepted counts, costs, logged provider records and API-call counts. Billed usage deduplicates
persisted model-event UUIDs across retry logs so copied successful samples are not
billed twice. Superseded runs, when listed in `superseded_conditions`, have separate
cost records and are excluded from the selected conditions. Interrupted calls may
not have a persisted response, so these costs can be lower bounds.
