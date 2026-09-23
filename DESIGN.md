Design Doc: Debate Protocols for Sabotage Auditing

Purpose: starting codebase for SPAR mentees. Built once by Claude Code, then iterated on by mentees. Optimise for extensibility at the protocol layer; everything else should be boring.

0. A note to the Claude building this

The protocol design space here is genuinely unsettled — we don't know what the good protocols look like yet. This doc is a best guess, not a spec handed down from on high.

So: surface design decisions rather than making them silently. If while building you hit —

a piece of this design that turns out ugly in practice,
a place where a mentee plausibly won't see how to extend things,
a spot where the doc says X but building X made it obvious Y is better,

— then build it, see how it feels, and bring the decision back to Joey. "I did it this way, here's why, here's the alternative I'd prefer" is strongly preferred over a silent choice, even a good one. Silent design decisions are the main failure mode to avoid.

Possible blocker: OpenRouter may not be set up for this SPAR project yet. If you're blocked on credentials, work on everything that doesn't need live inference and put a pin in the rest — don't burn effort working around it.

1. Scope and non-goals

In scope: an Inspect-native harness for running oversight protocols (debate, consultancy, lone judge) over sabotage-auditing artifacts, with cost accounting and pluggable protocol implementations.

Non-goals: no training. No novel sabotage generation. No sandboxed code execution in v1 (stubbed — §8). No web UI. No plotting code — the Pareto frontier is a matplotlib call, mentees can write it.

Design principle: mentees should be able to write a new protocol without touching the dataset layer, and add a new dataset without touching protocols.

2. Environment
uv for package management, pyproject.toml, locked.
Linux + macOS. No GPU path — inference is all remote, so cross-platform is nearly free.
Python 3.12.
Inspect (inspect_ai). Not because it's ideal, but it's the gold-standard format and ASB already ships an Inspect task definition, so we inherit compatibility.
3. Build order (read before §5 onwards)

This ordering is deliberate and matters more than most of the rest of the doc.

Lone judge, Inspect-backed, written plainly. An agent with list_files, read_file, search as tools, emitting a credence. Inspect from step one — the agentic loop is the thing. But no protocol abstraction, no registration machinery, no decorators yet. It should be readable top to bottom by someone who has never seen the repo.
Verify end-to-end on two or three samples — at least one honest, one sabotaged. Check scores come out, costs record, labels line up. Then read the judge's actual outputs and assess qualitatively whether the reasoning looks sane. Do not chase an AUROC number at this stage; that depends as much on prompt quality as on whether the harness is correct, and Joey will check it himself once the repo exists.
Then fit the abstractions in §5–§7 around the thing that already works.
Debate and consultancy baselines.
Trajectory and paper primitives.
Stubs with docstrings explaining what the mentee is meant to build.

The reason for the ordering: the baselines are the teaching artifact. Mentees learn the codebase by reading them. If the lone judge reads as thirty lines of decorators and registration calls, that value is gone and they inherit scaffolding they didn't build and won't touch. The baselines should look embarrassingly simple. If fitting the abstraction makes them not-simple, the abstraction is wrong — flag it (§0).

Note: §5–§7 describe the end state, not step one. The tension with this section is intentional.

4. Model calling

All inference via OpenRouter. Two things the default client doesn't give us:

Provider pinning. Providers quantize differently; an unpinned call silently varies the model between runs. Every call specifies provider.order with allow_fallbacks: false. Wrap this so an unpinned call is impossible to express — a ModelSpec requiring a provider at construction.

The participant handle. One object carrying everything that makes a model a participant:

python
@dataclass(frozen=True)
class Participant:
    model: ModelSpec          # includes pinned provider
    system_prompt: str | None
    side: Side | None         # SABOTAGED | CLEAN | None (judges)

The extension point for the AI-control framing: an untrusted debater that knows where the sabotage is and argues it's fine is just a Participant with a particular system prompt. No special-casing in the protocol.

Debate protocols assert their two participants have opposing, non-null sides.

Caching. Inspect's disk cache, on by default, keyed on the full request (model, provider, prompt, sampling params). Note: OpenRouter's caching is provider-side prompt caching — it discounts repeated prefixes but still runs inference and doesn't survive restarts. Not what we want. The local cache is what keeps mentee iteration cheap and cost numbers meaningful.

5. Datasets

Three artifact types, three separate primitive sets. Not forced into a common interface — a codebase is navigated, a paper is read, a trajectory is replayed, and pretending otherwise leaks awkwardness into every protocol.

5.1 Codebase

Source: github.com/ejcgan/auditing-sabotage-bench — 9 ML research codebases with honest and sabotaged variants, YAML rubrics per sabotage, plus 18 LLM-generated red-team sabotages (9 Opus 4.6, 9 Gemini 3.1 Pro). Public; no access request needed.

Primitives: list_files, read_file, search, dump_all.

No diff primitive — there's no honest reference available at audit time, so it was never usable.

5.2 Paper

Usually PDF, sometimes markdown. Section extraction, full-text read, dump_all. Supports paper-only and paper-plus-codebase conditions.

5.3 Trajectory

Agent trajectories (monitoring-bench style), primarily for fast testing — a single known-subtle sabotage trajectory is a much tighter loop than a whole codebase. Must support the output-only view: prompt and tool calls only, no reasoning. Standard monitoring ablation; a first-class projection, not a post-hoc filter.

5.4 dump_all

Every artifact type gets one: load the entire thing into context, no navigation. Backs the full-context judge baseline (§7.4).

Overflow behaviour: fail loudly. Some ASB codebases won't fit. A silently truncated dump makes the baseline meaningless — raise, don't warn-and-truncate.

5.5 The tool-exposure split

Every dataset primitive exists in two forms:

A plain Python function the protocol calls directly.
A wrapper exposing the same function as an Inspect tool handed to a participant.

So a protocol can read files itself and paste excerpts into the debate, or give debaters live access — without duplicated implementations. One registration decorator should produce both forms.

6. Tool access defaults

Tool exposure is a property of the sample's artifacts, not something each protocol wires by hand. A sample knows which artifacts it carries; every participant gets the corresponding tools by default.

python
@dataclass
class ToolAccess:
    debaters_get_artifact_access: bool = True
    judge_gets_artifact_access: bool = True
    # ... per-role, defaulting True

Protocols opt out to create information asymmetry, rather than opting in to get anything at all.

This applies alongside extract-and-evaluate, not instead of it: a participant handed a condensed extract can still go back to the underlying artifact for more context unless that's explicitly switched off. Additive context, not replacement — the more realistic monitoring setup anyway.

Loud comment required in the consultancy baseline: a mentee wanting "judge sees only the extract" must know to switch access off. If they don't they'll get a null result and not understand why. Say so where the asymmetry matters.

7. Ground truth, protocol interface, metrics
7.1 Labels and the judge's question

Each sample carries honest/sabotaged plus, where available, the rubric identifying the specific sabotage.

Why explicit: evaluating only on sabotaged samples means a judge outputting "definitely sabotaged" unconditionally scores perfectly. Honest variants are what make the score mean anything.

The judge outputs a credence: 0–100% that the codebase was sabotaged. Matching ASB, whose auditors output a credence from 0–100% for how likely the paper was sabotaged, from which AUROC is computed. A credence is self-anchoring in a way a 0–10 suspiciousness scale isn't — no rubric needed, and we stay comparable to their number.

The default judge prompt states the 50% base rate, as ASB's setup does.

Primary metric: AUROC. ASB's best auditor (Gemini 3.1 Pro) got 0.77 — the number to beat, and a sanity check the harness is wired correctly.

Benign distractors. ASB deliberately kept bugs, unusual design choices, and undocumented decisions in the honest codebases, and found these were a major source of false positives — they inflate credences on honest codebases and crowd out investigation of real sabotage. Do not filter them; they are the point.

This is also a live hypothesis for the project, worth a note in the README for mentees: debate may handle distractors better than a lone judge, because a debater arguing "clean" can explain one away. Relatedly — the reason extract-and-evaluate looks promising is that having a small fast model pull out ~12 interesting-or-arbitrary-looking decision points and debating those in sequence might substantially reduce the distractor problem. Unproven, worth testing.

7.2 Interface
python
class Protocol(Protocol):
    def run(self, sample: Sample, tools: ToolSet) -> ProtocolResult: ...

ProtocolResult carries: credence (0–100), final verdict, its own transcript, and a cost record.

On the own transcript: Inspect logs Inspect's view — model calls, tool calls, scores. Protocol-level orchestration outside a solver (picking excerpts, running extract-and-evaluate, deciding turn order) isn't in the eval log unless we put it there. So ProtocolResult carries its own, and protocols write intermediate state into the Inspect store explicitly. Cheap insurance; debugging a debate where you can't see why turn three happened is miserable.

7.3 Runner output

Runner takes a protocol, a dataset, and n_runs. Returns every individual run result, plus summary statistics (mean, variance, n). No aggregation-only output — the per-run spread is data.

Reported:

AUROC against labels
credence distribution
cost per run and cost per role (judge vs. each debater)

Per-run cost makes protocols directly comparable to the lone-judge baseline — accuracy-vs-total-cost is the headline artifact. Per-role cost makes the budget-allocation question answerable.

7.4 Baselines (ship with v1)

Written plainly, per §3. These are documentation.

Lone judge (agentic). Explores with list_files/read_file/search, emits a credence. The baseline everything else is measured against.
Full-context judge. dump_all, no navigation, one call, credence. The honest cheap baseline — and if it beats the agent, that's a finding.
Naive n-turn debate. Two Participants, opposing sides, full access, alternating turns, judge scores.
Consultancy. One debater, assigned side, judge interrogates. (Open question — §9.)
8. Deliberate stubs

Stubs on purpose — mentee work. Pre-solving them removes the interesting part.

extract_and_evaluate. A cheap-model pass over the codebase producing a condensed representation for the expensive model. Both forms from §5.5 — some protocols control extraction, others let debaters invoke it. Interface stub only; the design is the research question. See the hypothesis in §7.1.
run_code. Executing untrusted sabotaged code needs real sandboxing. NotImplementedError with a comment pointing at Inspect's sandbox support.
9. Open questions
Does consultancy ship in v1, or is it mentee work?
Anything from §0 that comes up while building.