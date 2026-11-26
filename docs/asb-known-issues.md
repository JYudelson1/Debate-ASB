# Known issues in ASB

Problems we found in the Auditing Sabotage Bench itself (as pinned in
`scripts/fetch_asb.sh`, commit `f8efd68`), and in rerunning its pipelines
today. Problems in our own harness are listed separately at the end. Not to be confused with `src/debate_asb/known_issues.json`, which lists
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

## 2. diffuse_fox ships a reliable_questions.json that doesn't match its cached generations

Status: **fixed here** (`PATCHES` in `src/debate_asb/datasets/asb.py`).

- `diffuse/fox/experiments/gemma-3-12b/reliable_questions.json` (6,178
  reliable questions) is not the file fox's cached generations and saved
  results were made with; they were made with the 6,169-question file shipped
  in `diffuse/owl`. `diffuse/original` ships the same mismatched file.
- Because the file exists, `run_experiments.py` skips steps 1-2, loads these
  questions (shuffled with seed 42) and pairs them by index with the cached
  PASS-1 answers in `generations/lie_detector_part1/`.
- With fox's file: 1/5000 prompts match their cached answer, accuracy 25.8%
  (chance), 2398/5000 labels equal the saved `labels.json`. With owl's file:
  5000/5000 match, accuracy 54.9% (= saved 2746/5000), 5000/5000 labels equal.
  Checked on a fresh clone of upstream main (38de58c).
- Effect: rerunning the honest codebase's pipeline trains probes on random
  labels (AUROC ~0.5 everywhere, vs ~0.99 saved), while the sabotaged twin
  (owl) reproduces its saved results. Not part of the sabotage (the rubric
  places it only in owl's `src/activations.py`).
- Our fix: the loader gives `diffuse_fox` a patched copy
  (`data/asb_patched/diffuse_fox/`) with owl's `reliable_questions.json`.

## 3. chain_output_fixed: saved logs not regenerated, benign data missing

Status: **open** (we don't use the `_fixed` variants).

- Its CoT prompt says "pick out any number of quotes", but its saved monitor
  logs were made with `chain_output`'s "at least 5 quotes" prompt (0 of 90
  checked prompts match its own file; all match `chain_output`'s).
- `logs/benign_eval/` has no trajectories, the four benign monitor logs are
  missing, and one attack category (`logio`) is missing. The paper's 1% FPR
  thresholds come from the benign logs, so its numbers can't be recomputed and
  its pipeline can't run (`run_monitor` finds no `.eval` files).

## 4. `_fixed` variants exist only for honest codebases

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

## 5. chain's pipeline doesn't rerun as the README says

Status: **open** (our pipeline runner works around it).

- `run_evaluation` writes into `logs/<dataset>_<model>_e3/`, which already
  hold the saved monitor logs. Inspect's `eval_set` then refuses ("existing log
  file ... is not associated with a task") or reuses them, and
  `analyze_results` reports the saved numbers. A rerun needs the saved
  `logs/*_e3` moved aside first.

## 6. Modal images with unpinned packages no longer build working environments

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

## 7. Models and services that are gone

Status: **environment**.

- chain's Haiku monitor (`claude-3-haiku-20240307`) is no longer on
  OpenRouter, so that row can't be reproduced; Claude Haiku 4.5 behaves very
  differently (CoT catch rate ~74-80% vs ~11%).
- hop and preference fine-tune GPT models through OpenAI's fine-tuning API,
  which our organization can no longer use.

## 8. awareness_prism does not regenerate its bundled steering vector

Status: **fixed here** (`LOCAL_PATCHES` in `src/debate_asb/datasets/asb.py`).

- `--regenerate-steering-vector` calls a remote generator that returns the
  image's existing tensor; the extraction helper also skips an existing output
  path, and shards refuse to replace their bundled tensor.
- The loader overlays a corrected `run_experiments.py` only for
  `awareness_prism`: forward the force flag, extract into a new temporary path,
  validate the returned bytes, and atomically replace each destination file.
- Default cache reuse is preserved; every shard uses the coordinator's selected
  bytes. The original ASB checkout and other variants are unchanged.
- The overlay is tied to the pinned source hash and participates in the loader's
  cache stamp. Tests use stubs; published metric changes are not established.

# Known issues in our harness (Debate-ASB)

## H1. Inspect silently cut the middle of long tool outputs

Status: **fixed** (branch `fix/tool-output-budget`).

- Our `agent_loop` (and hypothesis_slots') runs tool calls through Inspect's
  `execute_tools`, which cuts any output over `max_tool_output` (default
  16 KiB). It keeps the first and last 8 KiB and drops the middle, with no
  marker at the join (`inspect_ai/_util/text.py`, `truncate_str`), only a
  general "was too long to be displayed" header.
- `read_file` returned up to 300 lines of up to 2,000 characters, so a read
  of wide lines went over 16 KiB. Its footer (`showed lines 1-300 of n`) is at
  the end, so it survived and claimed lines the model never saw; continuing at
  `start_line=301` skipped the middle for good. `run_bash`'s own cap (20,000
  characters) was also above 16 KiB.
- Measured over all 177 logs (2026-11-24): 1,165 of 85,092 `read_file` calls
  were cut (1.4%); 903 lost lines; in 598 the lost lines were never read later
  (~3,600 distinct lines). Mostly data files (hop `results.csv`, hidden's RL
  dumps, diffuse caches), hop's `generate.py` templates, multi's analysis
  helpers; once negative_mute's PAPER.md section 3.2. No gap covered a primary
  sabotage location (`sabotage_registry.json`). Most cuts were in debate runs
  (461 never recovered).
- Fix: every codebase and trajectory tool fits its own output under a 63 KiB
  budget at whole lines and says what it left out (`read_file`'s footer gives the next
  `start_line`; `run_bash` keeps start and end with an explicit marker), and
  `execute_tools` gets `max_output=64 KiB` so Inspect's cut is only a
  backstop. Limits are 4x the old ones (1200 lines, 400 matches, 1200
  entries), so every PAPER.md fits in one read. Tool descriptions state the
  limits. Tests: `tests/test_tool_output_budget.py`.
