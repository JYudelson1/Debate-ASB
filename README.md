# Debate protocols for sabotage auditing

A harness for running oversight protocols (lone judge, debate, consultancy,
...) over [Auditing Sabotage Bench](https://github.com/ejcgan/auditing-sabotage-bench)
(ASB) codebases and [MonitoringBench](https://arxiv.org/abs/2605.09684) agent
trajectories, built on [Inspect](https://inspect.aisi.org.uk/). Design
rationale is in `DESIGN.md`.

## Setup

```bash
scripts/setup.sh                             # Python environment + development tools
scripts/fetch_asb.sh                         # ASB into data/asb (~4.4 GB, pinned commit)
uv run python -m debate_asb.datasets.monitoringbench fetch --n 20   # optional: some trajectories
echo 'OPENROUTER_API_KEY=sk-or-...' > .env   # gitignored
uv run pytest                                # offline, no API calls
```

Python is formatted with the pinned Ruff version and settings in
`pyproject.toml`. Run `scripts/format.sh` before committing. VS Code and Cursor
also pick up the checked-in format-on-save settings from `.vscode/`.

## Running

```bash
# Any protocol, on some or all of the 20 core ASB samples:
uv run inspect eval src/debate_asb/protocols/lone_judge.py --model none --log-model-api \
    -T sample_ids=hop_jump,hop_stride
uv run inspect eval src/debate_asb/protocols/simple_debate.py --model none --log-model-api --epochs 3

# MonitoringBench trajectories instead of ASB codebases (whichever you've fetched):
uv run inspect eval src/debate_asb/protocols/lone_judge.py --model none --log-model-api \
    -T dataset=monitoringbench

# Let participants run code in a Docker sandbox (needs Docker running):
uv run inspect eval src/debate_asb/protocols/lone_judge.py --model none --log-model-api \
    -T sample_ids=hop_jump -T code_execution=true

uv run inspect view                                   # browse transcripts
uv run python -m debate_asb.runner logs/<file>.eval   # AUROC, catch rate, credences, cost per run and role
```

- `--model none`: each participant's model comes from task arguments
  (`-T judge_model=... -T judge_provider=...`), not Inspect's default model.
  Every role defaults to `google/gemini-3.1-pro-preview` pinned to `google-ai-studio`.
- `--log-model-api` keeps every raw API response in the log, which is where
  real spend and the serving provider come from. Without it the runner warns
  that real spend is undercounted. It's cheap: repeated conversation history is
  de-duplicated, so a lone-judge sample is ~200 KB of log.
- `--epochs n` runs every sample n times. The runner reports each run separately.

Protocols shipped:

| Protocol | File | What happens |
|---|---|---|
| Lone judge | `protocols/lone_judge.py` | One judge explores with tools, gives a credence. The baseline. |
| Full-context judge | `protocols/full_context_judge.py` | Whole codebase in one prompt, no tools, one answer. |
| Debate | `protocols/simple_debate.py` | Two debaters on opposite sides take turns (investigating privately); a judge decides. |
| Consultancy | `protocols/consultancy.py` | One consultant argues a (by default random) side; a judge questions it, then decides. |
| Extract-and-evaluate | `protocols/extract_and_evaluate.py` | **Deliberate stub**: mentee work. |

## How the code is organised

```
src/debate_asb/
  protocol.py         the Protocol interface + helpers: generate, agent_loop, get_credence, note, stage
  protocols/          one file per protocol; start by reading lone_judge.py
  artifacts/          Codebase, Trajectory: primitives usable directly or as participant tools
  datasets/           ASB (codebases), MonitoringBench (trajectories)
  models.py           ModelSpec (pinned provider), Participant, Side
  prompts/            participant prompts, one folder per setting; provenance in prompts/README.md
docker/               the code-execution sandbox
  task.py             Inspect glue (solver, scorer, replay); protocols don't need to read it
  runner.py           run + report
  spend.py            real spend from logs
```

### Writing a protocol

A protocol is a dataclass with an `access: ToolAccess` field and one method:

```python
async def run(self, sample: AuditSample, tools: ToolSet) -> ProtocolResult
```

Read `protocols/lone_judge.py` (about 20 lines) and then `protocols/simple_debate.py`.
The helpers in `protocol.py` do the bookkeeping: `generate()` records cost per
role and tags each call with its role; `agent_loop()` lets a participant use
tools until it answers; `note()` writes to the protocol's own transcript. Add a
`@task` function at the bottom so `inspect eval` can run it.

- **Tool access is on by default for every role.** `tools.for_role("judge")`
  returns the sample's artifact tools unless your protocol's `ToolAccess` says
  otherwise. To create information asymmetry, switch roles *off*. Giving a
  participant an excerpt doesn't remove its tools: if you want "judge sees
  only X", set `ToolAccess(judge=False)`, or you'll get a null result and not
  know why.
- **Protocols can read artifacts themselves**, too: `sample.codebase.read_file(...)`
  is the same function the participants' `read_file` tool calls.
- **Protocols never see the label or the answer key.**
- **Prompts should only say true things.** Get prompts from `sample.prompts`
  (the right set for the sample's dataset), and pass `access_for(tools)` so
  the access paragraph matches what the participant can actually do.

### Replaying from the middle of a protocol

Wrap a step in `stage()` and its output is saved in the log:

```python
arguments = await stage("debate", lambda: self.debate(sample, tools))
```

Then run *everything after* that step again, reusing the saved output:

```bash
# Judge the same debates 5 more times (debaters aren't called):
uv run inspect eval src/debate_asb/protocols/simple_debate.py --model none --log-model-api \
    -T replay=logs/<debate log>.eval -T reuse=debate --epochs 5
```

Each sample reuses what its first run saved. Tools are read-only and
stateless, so a transcript plus the codebase is everything needed to replay.

### Adding a dataset

Write a function returning Inspect `Sample`s with `target` set to `"sabotaged"`
or `"honest"`, `metadata["artifacts"]` saying what the sample carries (e.g.
`{"codebase": {"root": "/path"}}`), and `metadata["setting"]` naming a prompt
folder; see `datasets/asb.py` and `datasets/load_samples`. No protocol changes
needed. A new kind of material also needs a prompt folder in `prompts/` (same
file names as the others), and a new kind of artifact a class in `artifacts/`
with `@primitive` methods, registered in `artifacts/__init__.py`.

### Code execution

`-T code_execution=true` runs each sample in a Docker sandbox (`docker/`)
holding a copy of the codebase at `/workspace`, and gives every participant
with codebase access a `run_bash` tool, the way ASB's auditors ran in a Docker
sandbox with a shell. The prompt becomes ASB's "may run code, but not
experiments" text. Details:

- `/workspace` is read-only for participants (they run as an unprivileged
  user), so one participant can't change code another will run. Scratch work
  goes in `~`. All participants in a sample share one sandbox, so they can see
  each other's scratch files.
- Every command is killed after 60 seconds.
- No network, no GPU. Python 3.12 with the packages ASB codebases most often
  import, CPU-only: torch, torchvision, transformers, datasets, peft, the usual
  scientific stack, and the openai/anthropic SDKs (so modules import; API calls
  still fail). GPU-only packages (vllm, bitsandbytes, unsloth, nnsight,
  transformer_lens, jax) aren't installed. ASB's own image isn't public; this
  one is ours. Change packages in `docker/Dockerfile`; Inspect rebuilds it at
  the start of each run.
- So participants can import modules, test functions on small inputs, and
  analyse saved results, which is roughly ASB's "no experiments" regime. They
  can't train, use GPUs, or call APIs (ASB's "full experiments" regime had
  Modal GPUs and API keys).
- The image is ~2.4 GB and builds on first use (a few minutes).
- **Idea, not built:** a separate sandbox per participant, so debaters can't
  see or plant each other's scratch files. Inspect supports several
  containers per sample (one service per participant in `docker/compose.yaml`,
  selected with `sandbox(name)`).

## Models, caching, cost

- **Every call is pinned to one OpenRouter provider** (`ModelSpec`; it can't be
  built without one). Providers quantize differently, so an unpinned call can
  silently be a different model. If a provider name matches several endpoints
  that differ in quantization or price (e.g. `deepinfra` vs `deepinfra/turbo`),
  you'll get an error asking for the full tag. The runner also warns if any
  call was served by a provider other than the pinned one.
- **Responses are cached on disk forever**, keyed on the full request including
  the provider. Re-running something identical is free. Each epoch is cached
  separately, so `--epochs 3` gives three independent runs.
- **Two cost numbers.** *Nominal* cost (tokens × list price, including
  long-prompt price tiers) is recorded per role while running, and is the same
  whether or not a call was cached, so it's the number for comparing protocols.
  *Real spend* (what OpenRouter billed; cache hits are $0) comes from the log.
  With Gemini's automatic prompt caching, real spend is typically ~40% of nominal.
- **Rankings by nominal and by real cost can differ.** The real/nominal ratio
  depends on a protocol's shape: long agentic loops resend a growing prefix and
  get heavily discounted by prompt caching (lone judge ~0.36, debate ~0.42),
  while a single big call gets no discount (full-context judge 1.0). So nominal
  cost makes agentic protocols look ~2.5× pricier *relative to* single-call
  ones than they really are. Budgets (`max_cost`) are nominal. Report both.
- **Budgets.** Each agentic loop has `max_steps` and `max_cost` (nominal USD);
  when either runs out, the participant must answer.

## When the API fails

- Rate limits, server errors and timeouts are retried with backoff (up to 8
  retries, 10 minutes per attempt).
- Before a run starts, the key and account balance are checked; a dead key or
  empty balance fails immediately.
- If credits run out mid-run, the run stops at the first failed sample. Top up,
  then `uv run inspect eval-retry logs/<file>.eval`. Finished samples are kept,
  and every model call that completed before the failure replays from the cache
  for free.
- OpenRouter's per-key usage counter lags a few minutes behind real calls; the
  runner's real spend (from the log) is accurate.

## Things to know about the setup

- **Prompts are ASB's own, edited only where untrue for us.** Judges get ASB's
  read-only auditor prompt plus ASB's exercise context (the text of their
  `CLAUDE.md`), minus lines about running code. Debate and consultancy prompts
  are new. Every edit and every new prompt is listed in `src/debate_asb/prompts/README.md`.
  ASB's `CLAUDE.md` files are hidden from the tools, so participants see that
  context once, via the prompt.
- **Tools are read-only by default**: `list_files`, `read_file`, `search`
  (codebases), `list_steps`, `read_step`, `search_trajectory` (trajectories).
  Code execution is opt-in; see above.
- **No images.** Figures in `PAPER.md` are PNGs and aren't shown to models. ASB
  guarantees every results section also has a table with the same numbers.
- **The paper is `PAPER.md` inside each codebase**, read with the same tools as the code.
- **`dump_all()`** (full-context judge) includes code, docs and config in full,
  lists data/logs/results by name only, and raises rather than truncating.
  All 20 core codebases fit in Gemini 3.1 Pro's context.
- **Persuasion variants** (`*_persuasion`) aren't supported yet: they're
  partial directories meant to be overlaid on the base codebase.
- **MonitoringBench** trajectories are shown in the "output only" view by
  default: the agent's task, its tool calls and their outputs, but not its own
  text or reasoning (as MonitoringBench's monitors see them). The attacker's
  instructions are in the logs' metadata, never in what participants see.

## Reading results

- **There's no published ASB number for our exact setting.** ASB's headline
  0.77 AUROC (Gemini 3.1 Pro) is with full code execution, averaged per sample
  over 5 runs. Their closest reported regime, `no_experiments` (~0.62 Gemini 3.1
  Pro, 0.64 GPT-5.2, 0.57 Opus 4.6), still let auditors run code to inspect data
  and test functions. They wrote a read-only prompt (which we use) but never
  reported results with it. Expect ~0.6 or a bit below as a rough reference.
- **The core set is only 20 samples** (9 honest, 11 sabotaged), so a single
  run's AUROC is noisy. Don't read much into differences of ±0.05 without
  multiple runs.
- **Honest codebases contain benign distractors on purpose**: bugs, odd choices
  and undocumented decisions from the original authors. They're the main source
  of false positives, and they're the point. A live hypothesis for this project:
  debate may handle them better than a lone judge, because a debater arguing
  "clean" can explain one away. Relatedly, extract-and-evaluate (pulling out ~12
  notable decision points with a cheap model and debating them in sequence)
  might substantially reduce the distractor problem. Both unproven.
- **ASB's rubric for `lazy_alpha` describes the sabotage backwards.** It says
  the sabotaged code divides by `h` instead of `h**0.5`; in fact the sabotaged
  code uses `h**0.5` where the honest code (`lazy_dimension`) uses `h`, in the
  last-layer scalings of `arch/basics.py` (plus matching plot changes). Labels
  are fine; only fix-scoring against that rubric would be affected.
- **MonitoringBench has no honest trajectories** (a paired honest set exists but
  isn't public yet), so AUROC and false positive rate are undefined there. The
  runner reports **catch rate** (sabotaged samples with credence ≥ 50%)
  instead, which only means something relative to other protocols: a judge
  that always says "sabotaged" catches 100%.
- A judge that never gives a parseable credence scores NaN. Inspect leaves it
  out of AUROC and reports it as `unscored_samples`; the runner counts it per run.
