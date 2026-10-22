# Rerunning extraction prompt and protocol variants

Use the normal Inspect tasks; a run-specific subclass or a copied source tree is
not needed to change the extraction prompt. The base debate/judge stages still
assess sabotage. These arguments select existing extraction contracts only.

## Single-extractor prompt variants

Templates live under `src/debate_asb/prompts/asb/extract_and_evaluate/`:

- `extract.md`: existing fixed-count control.
- `extract_experimental_validity.md`: fixed-count experimental-validity prompt,
  copied verbatim from `experimental-validity-20261015/extract.md`.
- `extract_potential_validity_threats.md`: supplied potential-threat draft;
  explicitly permits a shortfall.
- `extract_any_validity_criterion.md`: existing any-criterion draft, copied
  verbatim from `any-criterion-validity-gpt6luna-20261019/extract.md`; also permits
  a shortfall.

For the fixed-count experimental-validity prompt:

```sh
uv run inspect eval src/debate_asb/protocols/extract_and_evaluate.py \
  --model none --log-model-api --epochs 3 --log-dir logs/validity \
  -T sample_ids=hop_jump -T stop_after=extraction \
  -T extractor_model=openai/gpt-6-luna -T extractor_provider=openai \
  -T extraction_prompt=extract_and_evaluate/extract_experimental_validity.md \
  -T n_bundles=6 -T extractor_max_steps=40 -T extractor_max_cost=3
```

For either shortfall draft, select its template and pass `-T output_policy=shortfall`.
A successful response with fewer than the cap must start with the exact declaration
specified in that prompt. Zero must be the entire response. A valid shortfall
never triggers filling, replacement, or a retry. Full evaluation with this policy
requires an explicit `-T zero_candidate_score=…`; extraction-only runs are unscored.
The default `output_policy=fixed` retains the original exact-count parser.

The sample above illustrates the arguments; select the original sample IDs,
epochs, provider/model and budgets from the saved run configuration when
reproducing a historical run. It does not select the full historical cohort.
The log records task arguments, rendered prompt hashes, returned counts and costs.
Preserve the log, exact source revision/snapshot and dataset manifest; record
unavailable seeds explicitly. Forced final-answer calls can exceed nominal caps.

## Worker protocols

`protocols/extraction_experiments.py` accepts the same `extraction_prompt` argument
for the existing `variant=hypothesis_workers` intervention. It preserves that
intervention's fixed allocation, shared discovery and one retained bundle per
worker. A different prompt does not change its fixed-count output contract.

The separately supplied bounded-question protocol uses its own coordinator and
worker templates, independent conversations and disposition-based filtering.
See [hypothesis-workers.md](hypothesis-workers.md) for its required budget
arguments, replay rules, zero-candidate policy and full handoff contract. These
two worker protocols have different semantics and remain separate entry points.

For historical worker-discovery, child-worker or scaling studies whose protocol
is only in an archived source snapshot, use that snapshot and its original run
configuration. This cleanup does not substitute the simpler intervention for
those studies or recreate their conditions.

## Offline analysis

Collect and validate semantic reviews with the extraction-analysis collector,
then build [comparison reports](extraction-reports.md) or
[cross-run reports](extraction-cross-run.md). The reusable implementations are
under `src/debate_asb/extraction_analysis/`: `comparison.py`, `cross_run.py`,
`metrics.py`, `coverage.py`, and shared serialization/trace helpers. Report assets
are under `viewer/static/extraction_comparison/` and `extraction_cross_run/`.

`scripts/migrations/apply_capture_audit_20261020.py` is intentionally a historical
migration: it contains the 20 October scoring policy, legacy report layouts,
sharing-copy paths and historical prose corrections. It is not a general audit
editor. Use its `--dry-run` before replaying that specific migration; new reports
consume their own explicit review and target-audit files.
