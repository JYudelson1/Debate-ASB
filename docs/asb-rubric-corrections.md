# ASB rubric corrections

The ASB loader applies reviewed overrides from
`src/debate_asb/datasets/asb_rubric_corrections/` to the answer key and rubric in
sample metadata. Each override is bound to the hash of its original parsed YAML
entry. Upstream annotation changes require a new review before loading that sample.

Logs retain the original sabotage and rubric under `rubric_correction`, together
with the pinned ASB revision, original-entry hash, correction hash and reason.
The effective sabotage remains `answer_key`; the effective grading guidance is
`rubric`. Protocols receive the answer key only when they explicitly opt in.

These overrides affect future loaded metadata. They do not rescore historical
runs or change the codebases, their papers or cached results. The separate
workspace file patches in `datasets/asb.py` continue to apply independently.

Each correction file includes source paths and hashes for rechecking its claims.
Tests exercise the loader boundary and verify those sources when ASB is fetched.
