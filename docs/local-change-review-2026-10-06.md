# Local change review: 2026-10-06

The existing implementation is preserved at the author's request. This note
records limitations found while preparing the changes for review.

## Evidence parser type-check failure

`.venv/bin/pyright` reports one `reportReturnType` error in
`src/debate_asb/evidence.py`, in the nested `boundary_after` function:
`bool | None` is not assignable to its declared `bool` return type.
`_BUNDLE_HEADER.fullmatch(...)` can return `None`, and Python's `and`/`or`
operators retain operand values. The caller uses the result as a condition, but
the return annotation does not describe every possible value. This diagnostic
remains unresolved; the parser PR does not claim a clean type check.

Reproduce with `.venv/bin/pyright`. The separately checked extraction-analysis
scripts have no Pyright diagnostics. Ruff lint and formatting checks pass.

## Parsing and review boundaries

- Discontiguous line hints are stored as their minimum/maximum envelope in
  structured excerpts; the original specification remains in `raw_output`.
  Literal quote fidelity still needs checking against returned source text.
- Recovery of a missing outer excerpt fence uses following bundle/excerpt
  headers as boundaries. This is a syntactic recovery heuristic.
- Incomplete extraction traces can still be labeled `observed`; manual review
  must keep non-exposure unknown until extraction completeness is confirmed.
  See [measurement limits](../skills/extraction-analysis/references/metrics.md#incomplete-extraction-traces).
- New report generation requires schema-v3, completed per-bundle reviews.
  Existing schema-v1/v2 ledgers require manual migration and source-backed review.
