# Known issues in ASB

Problems we found in the Auditing Sabotage Bench itself (as pinned in
`scripts/fetch_asb.sh`, commit `f8efd68`), and in rerunning its pipelines
today. Not to be confused with `src/debate_asb/known_issues.json`, which lists
the benign quirks inside the codebases (for scoring judges' false findings).

Each entry says what we checked, so it can be rechecked before it is reported
upstream. Status: **fixed here** (we patch around it), **open**, or
**environment** (not ASB's fault, but it blocks reruns).

## 1. chain_justification ships a monitor prompt that doesn't produce its own results

Status: **fixed here** (`PATCHES` in `src/debate_asb/datasets/asb.py`).

- The only file that differs between `chain/justification` and
  `chain/output` is `src/cot_red_handed/monitor/prompts/monitor_prompt_1.yaml`.
- As shipped, only the **action** prompt is sabotaged ("walk through the logic
  of each major component", "is there anything you don't understand"). The
  **CoT** prompt keeps the honest steps.
- The sample's own saved monitor logs (`chain/justification/logs/*_e3/`), its
  PAPER.md results and its rubric were made with **both** prompts sabotaged.
  That file ships in `chain/justification_persuasion/` (the persuasion
  variant), whose prompt file reproduces every saved prompt exactly (checked:
  all 1,059 notice samples for each of the four monitors). The rubric for
  `chain_justification` also cites "PAPER.md Appendix B" and CoT-prompt fixes,
  which exist only in the persuasion variant.
- Rerunning the shipped code (Gemini 2.5 Flash, GPT-4.1 Mini, GPT-4.1 Nano;
  Claude 3 Haiku is no longer served) gives honest-like CoT catch rates
  (67 / 45 / 14%) instead of the paper's 35 / 26 / 5%. With the persuasion
  prompt file it reproduces the paper (34.6 / 28.6 / 15.9%; CoT worse than
  action-only for every model again).
- Unclear which side is intended. Either the base sample should ship the
  persuasion prompt file, or its saved logs come from the persuasion setup.
- Our fix: the loader gives `chain_justification` a patched copy of the
  codebase (`data/asb_patched/chain_justification/`) with the persuasion
  variant's prompt file. Nothing else changes; the file comes from ASB.

## 2. chain_output_fixed: saved logs not regenerated, benign data missing

Status: **open** (we don't use the `_fixed` variants).

- Its CoT prompt says "pick out any number of quotes", but its saved monitor
  logs were made with `chain_output`'s "at least 5 quotes" prompt (0 of 90
  checked prompts match its own file; all match `chain_output`'s).
- `logs/benign_eval/` has no trajectories, the four benign monitor logs are
  missing, and one attack category (`logio`) is missing. The paper's 1% FPR
  thresholds come from the benign logs, so its numbers can't be recomputed and
  its pipeline can't run (`run_monitor` finds no `.eval` files).

## 3. `_fixed` variants exist only for honest codebases

Status: **open** (design caveat; we use the normal 20 core codebases).

- The fixes remove benign quirks from the honest codebases only; the
  sabotaged codebases keep them. In 6 of 11 pairs the sabotaged codebase
  differs from the fixed honest one in more code files than from the plain
  honest one (e.g. chain 1 → 3, hidden 2 → 6). Using `_fixed` honest
  codebases therefore leaves the quirks only in sabotaged codebases, which
  can inflate detection scores.
- Applying the honest → fixed changes to the sabotaged codebases works cleanly
  for 7 of 11; negative_mute, diffuse_owl, awareness_lens and preference_lion
  conflict near the sabotaged lines.

## 4. chain's pipeline doesn't rerun as the README says

Status: **open** (our pipeline runner works around it).

- `run_evaluation` writes into `logs/<dataset>_<model>_e3/`, which already
  hold the saved monitor logs. Inspect's `eval_set` then refuses ("existing log
  file ... is not associated with a task") or reuses them, and
  `analyze_results` reports the saved numbers. A rerun needs the saved
  `logs/*_e3` moved aside first.

## 5. Modal images with unpinned packages no longer build working environments

Status: **open** (our pipeline runner pins them to each codebase's `uv.lock`).

- negative's and diffuse's `run_experiments.py` build their Modal image with
  `pip_install("torch", "transformer_lens", ...)` and no versions. Today that
  installs TransformerLens 4 (which removed `HookedTransformer`) and
  transformers 5 (which breaks TransformerLens 2.x), so the pipelines fail.
  With the versions from each codebase's `uv.lock`, negative reproduces its
  saved results exactly.
- negative_hush's `uv.lock` pins an old Modal client's dependencies
  (`synchronicity==0.11.1`), which clash with current Modal clients;
  negative_mute's lock doesn't include Modal at all although its
  `pyproject.toml` lists it.

## 6. Models and services that are gone

Status: **environment**.

- chain's Haiku monitor (`claude-3-haiku-20240307`) is no longer on
  OpenRouter, so that row can't be reproduced; Claude Haiku 4.5 behaves very
  differently (CoT catch rate ~74-80% vs ~11%).
- hop and preference fine-tune GPT models through OpenAI's fine-tuning API,
  which our organization can no longer use.
