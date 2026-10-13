# Reusable sabotage reference

The post-hoc reference is tracked in
[`src/debate_asb/sabotage_registry.json`](../src/debate_asb/sabotage_registry.json).
It corrects and qualifies `_rubrics` without changing ASB, evaluation inputs,
protocol prompts, or benchmark labels. Collaborators get it with this repository;
the Python package also includes the registry. No model call is needed to resolve
an existing checked sabotage target.

The initial reference imports the saved source-location reviews and checks their
code against the pinned ASB checkout. It covers all 11 previously analyzed core
sabotaged samples: ten have verified code mechanisms; `awareness_lens` has checked
generation code but unverified cached-vector provenance. It inventories all 76
rubric IDs, including 18 honest controls and **47 pending variants** (11 persuasion,
36 red-team). Pending entries contain explicitly unverified rubric claims, not
verified mechanisms. They cannot be scored as a resolved capture or miss. Persuasion
directories are overlays; missing files there do not prove absence of sabotage.

## Use

```bash
# Fetch the same source revision on another machine, then validate references.
bash scripts/fetch_asb.sh
uv run python -m debate_asb.sabotage validate
# Optional: validate just the requested sample(s).
uv run python -m debate_asb.sabotage validate --sample lazy_alpha

# The usual collection command automatically resolves and snapshots the registry.
uv run python skills/extraction-analysis/scripts/analyze.py collect \
  --repo . --log logs/<run>.eval --out artifacts/extraction-analysis/<run>
# Review this run's bundles/trace against the saved mechanism, then render.
uv run python skills/extraction-analysis/scripts/analyze.py render \
  --analysis artifacts/extraction-analysis/<run>/analysis.json \
  --review artifacts/extraction-analysis/<run>/review.json

# Update saved analysis/viewers after a reference or generator change.
uv run python scripts/refresh_sabotage_reports.py
```

Analysis records the registry file/content hashes, version, pinned source commit,
and a complete per-sample snapshot. The review is bound to that snapshot. Rendering
uses the saved snapshot, so later registry edits cannot silently change an archived
report. Log/config/model/provider/epoch/usage/cost provenance remains in analysis.json;
run-specific coverage and bundle judgments remain in review.json. The registry
contains no model-generated verdict about a new run.

Resolution checks the exact sample ID, label and dataset-relative workspace, and
hashes the checked sabotaged and paired honest files. Source drift or identity
mismatch disables the reference for resolved scoring. A missing checkout is
reported explicitly; saved evidence remains readable offline. The collector also
compares observed read/search lines with saved target excerpts. Truncated matching
prefixes are recorded as partial checks. Conflicting historical responses require
unknown/mismatch and explanation, rather than using current code as historical
proof. No observed target lines does **not** mean no sabotage exposure.

The standard eval viewer remains blind until “Reveal sabotage reference.” Its
reference panel shows verification status, precise locations, limitations,
source snapshots and comparisons. Original rubric and logged claims are available
separately. The extraction-analysis viewer distinguishes the reusable target from
the locations actually returned in a particular run.

## Add or correct a record once

1. Check the **exact variant and source revision**, its code and relevant consumer,
   and a paired honest implementation where available. For binaries or missing
   production history, preserve the uncertainty. Do not inherit a sibling variant's
   mechanism or turn rubric impact claims into measured results.
2. Edit that sample's registry entry in a normal reviewed change. Record the
   mechanism, primary/supporting/unverified-artifact locations, exact line ranges,
   file SHA-256s and literal excerpts. Save paired source hashes/diffs and review
   provenance, including any original log hash, sample/epoch, tool references and
   literal returned text. Use dataset-relative paths. Keep the original annotation
   and explicitly state corrections and limitations. Increment the entry version
   and registry version; preserve the earlier record in git history.
3. Run the validator and reference tests. Refresh local reports. Review any
   invalidated coverage decisions against their original retained evidence.

`verified` means the stated code mechanism is checked, not that empirical effects
or malicious intent were independently established. `partial` preserves checked
supporting code plus unresolved provenance. `pending_review` is never a fallback
to authoritative rubric claims. Honest controls preserve the benchmark label;
they are not an assertion that every line is defect-free.

The refresh command backs up ledgers and rendered products under
`reference-history/<old-analysis-id>/` and binds new artifacts to a new analysis ID.
It preserves existing run judgments unless the reference is unusable or a saved
literal-rubric mismatch concerns the corrected mechanism. Those target judgments
become `unknown`, with the old row preserved and review requested; bundle judgments
are retained. The refresh script also imports the existing corrected-lazy audit only when its
log hash, sample ID, epoch, trace hash and retained bundles match exactly. It saves
the original judgment, reviewer/date and audit hash. Observation/exposure labels
are left unknown when that audit did not review them. No capture/miss label is
inferred by migration. The old artifacts
retain their original denominators. Regeneration never reruns an evaluation.

## Sabotage explorer and non-target issues

```bash
uv run python -m debate_asb.sabotage_explorer
# Opens offline: artifacts/sabotage-explorer/index.html
# Optional: include sample-epoch coverage links from one or more saved analyses.
uv run python -m debate_asb.sabotage_explorer \
  --analysis artifacts/extraction-analysis/<run>/analysis.json
# Refresh the existing reports, catalog markers and explorer together:
uv run python scripts/refresh_sabotage_reports.py
```

The explorer shows short target explanations and paired code excerpts, with
search, paper filters, reviewed/pending scopes, and shareable `#sample_id` links.
Source evidence, limits and original annotations stay behind expandable details.
Saved extraction judgments can be attached; identical log/sample/epoch keys are
shown once even when several aggregate reports contain them. This is extraction
coverage, not a downstream debate verdict. Share the standalone HTML directly;
include linked report files if collaborators need to open those analysis links.

The packaged `src/debate_asb/known_issues.json` preserves all 40 entries from
`data/asb/codebases/_rubrics/bugs.yaml`, including original categories, impact
claims, applicability and source hash. It adds reproducible implementation
signatures for offline bundle marking. It is a separate catalog of **non-target
issues**, not a correction to the sabotage reference or a guarantee that every
catalog claim is valid. The original YAML may contain stale lines or disputed
interpretations.

Extraction reports mark catalog implementation signatures in retained quotes:
verified source-read matches, unverified quoted matches, and location hints are
separate. Filters preserve category and strength; details expose the annotation
and matching quote/source references. Bundle-level bug/candidate/paper judgments
and target-coverage decisions stay unchanged. A marker flags a potential
distraction; an observation's misleadingness still depends on its claim and
context. No match does not certify absence. Incomplete context and file-only
annotations may need manual review. Exact listed variants apply; siblings do
not inherit matches.

Changing the catalog/matcher triggers the normal backed-up analysis refresh.
Reviews remain bound to the migrated analysis ID, and old reports remain in
`reference-history/`. No inference calls or evaluations are involved.
