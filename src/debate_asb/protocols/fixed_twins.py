"""Lone judge and debate on ASB's fixed codebases, with the debaters' step budgets as task args.

The tasks build the same protocols as lone_judge.py's and simple_debate.py's, so on the
same samples they send the same requests. Sample ids can also name:
- an honest `_fixed` twin, as ASB's rubrics list them (hop_stride_fixed);
- a sabotaged codebase with its paper's honest-to-fixed patch applied, which ASB doesn't
  ship: `<id>_fixed` (hop_jump_fixed), found under sabotaged_fixed_root at the same
  relative path as in ASB's codebases/ (hop/jump). Label and answer key stay the base's.

Run from Debate-ASB, e.g.:
    inspect eval src/debate_asb/protocols/fixed_twins.py@twin_debate --model none \
        --log-model-api -T sample_ids=hop_jump,hop_stride_fixed -T code_execution=true
"""

from pathlib import Path

from inspect_ai import task
from inspect_ai.dataset import Sample

from debate_asb.datasets import ASB_ROOT, asb_samples
from debate_asb.models import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    ModelSpec,
    Participant,
    ReasoningEffort,
    Side,
)
from debate_asb.protocol import ToolAccess
from debate_asb.protocols.lone_judge import LoneJudge
from debate_asb.protocols.simple_debate import Debate
from debate_asb.task import protocol_task

FIXED = "_fixed"


def twin_samples(
    sample_ids: str | list[str], sabotaged_fixed_root: str | None = None
) -> list[Sample]:
    """ASB samples by id, with `<sabotaged id>_fixed` read from sabotaged_fixed_root."""
    ids = sample_ids.split(",") if isinstance(sample_ids, str) else sample_ids
    bases = asb_samples([i.removesuffix(FIXED) for i in ids])
    patched = {
        str(b.id) + FIXED: b for b in bases if b.target == "sabotaged"
    }  # fmt: skip
    listed = asb_samples([i for i in ids if i not in patched])
    by_id = {str(s.id): s for s in listed}
    return [
        _patched(i, patched[i], sabotaged_fixed_root) if i in patched else by_id[i]
        for i in ids
    ]


def _patched(sample_id: str, base: Sample, root: str | None) -> Sample:
    if root is None:
        raise ValueError(f"{sample_id} needs sabotaged_fixed_root")
    assert base.metadata is not None
    artifacts = base.metadata["artifacts"]
    relative = Path(artifacts["codebase"]["root"]).relative_to(ASB_ROOT / "codebases")
    codebase = Path(root) / relative
    if not codebase.is_dir():
        raise ValueError(f"{sample_id}: no codebase at {codebase}")
    return base.model_copy(update={
        "id": sample_id,
        "metadata": {
            **base.metadata,
            "artifacts": {**artifacts, "codebase": {"root": str(codebase)}},
        },
    })  # fmt: skip


@task
def twin_lone(
    sample_ids: str | list[str],
    judge_model: str = DEFAULT_MODEL,
    judge_provider: str = DEFAULT_PROVIDER,
    judge_reasoning_effort: ReasoningEffort | None = None,
    max_steps: int = 40,
    max_cost: float = 3.0,
    code_execution: bool = False,
    sabotaged_fixed_root: str | None = None,
):
    judge = Participant(
        ModelSpec(judge_model, judge_provider, reasoning_effort=judge_reasoning_effort)
    )
    return protocol_task(
        LoneJudge(judge, max_steps=max_steps, max_cost=max_cost),
        twin_samples(sample_ids, sabotaged_fixed_root),
        code_execution=code_execution,
    )


@task
def twin_debate(
    sample_ids: str | list[str],
    debater_model: str = DEFAULT_MODEL,
    debater_provider: str = DEFAULT_PROVIDER,
    judge_model: str = DEFAULT_MODEL,
    judge_provider: str = DEFAULT_PROVIDER,
    n_rounds: int = 2,
    debater_max_steps: int = 30,  # per turn
    debater_max_cost: float = 2.0,  # nominal USD per turn
    judge_max_steps: int = 40,
    judge_max_cost: float = 3.0,
    debater_reasoning_effort: ReasoningEffort | None = None,
    judge_reasoning_effort: ReasoningEffort | None = None,
    code_execution: bool = False,
    sabotaged_fixed_root: str | None = None,
):
    debater = ModelSpec(
        debater_model, debater_provider, reasoning_effort=debater_reasoning_effort
    )
    protocol = Debate(
        sabotaged=Participant(debater, side=Side.SABOTAGED),
        clean=Participant(debater, side=Side.CLEAN),
        judge=Participant(
            ModelSpec(
                judge_model, judge_provider, reasoning_effort=judge_reasoning_effort
            )
        ),
        n_rounds=n_rounds,
        debater_max_steps=debater_max_steps,
        debater_max_cost=debater_max_cost,
        judge_max_steps=judge_max_steps,
        judge_max_cost=judge_max_cost,
        access=ToolAccess(judge=True),
    )
    return protocol_task(
        protocol,
        twin_samples(sample_ids, sabotaged_fixed_root),
        code_execution=code_execution,
    )
