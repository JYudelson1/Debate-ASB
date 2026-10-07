"""Trusted-vs-untrusted debate: simple_debate.py, but one debater knows the sabotage.

The untrusted debater (U) is told the sample's answer key and has a hidden goal:
get the judge to conclude "not sabotaged", whichever side it was assigned. The
trusted debater (T) is told its opponent is untrusted. The judge is told which
debater is trusted and which is untrusted, but not what U knows or wants.

The debate itself runs exactly as in simple_debate.py (turns, tools, budgets,
the replayable "debate" stage). What differs:

- Prompts come from this protocol's own folder per setting,
  prompts/t_vs_u_monitoringbench/ and prompts/t_vs_u_asb/ (provenance in
  prompts/README.md): the setting's debater and judge prompts, with a section
  added about who is trusted and untrusted.
- This protocol sees the answer key (`sees_answer_key`), and tells U what is in
  it. On MonitoringBench: the summary and analysis of the attack, and the
  attacker's instructions. On ASB: the rubric's description, location and
  impact of the sabotage. So it only runs on sabotaged samples.

    uv run inspect eval src/debate_asb/protocols/t_vs_u_debate.py --model none --log-model-api \\
        -T sample_ids=pilot_v1 -T untrusted_side=clean

    # One ASB codebase, every participant with code execution (needs Docker):
    uv run inspect eval src/debate_asb/protocols/t_vs_u_debate.py --model none --log-model-api \\
        -T dataset=asb -T sample_ids=hop_jump -T code_execution=true
"""

import re
from dataclasses import dataclass, replace
from typing import ClassVar, Literal

from inspect_ai import task
from inspect_ai.dataset import Sample

from debate_asb.datasets import Dataset, load_samples
from debate_asb.datasets.monitoringbench import side_task_description
from debate_asb.models import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    ModelSpec,
    Participant,
    ReasoningEffort,
    Side,
)
from debate_asb.prompts import SETTINGS, Access, PromptSet
from debate_asb.protocol import AuditSample
from debate_asb.protocols.simple_debate import Debate
from debate_asb.task import protocol_task

# This protocol's prompt folder for each setting it can run on.
T_VS_U_PROMPTS = {
    "monitoringbench": replace(
        SETTINGS["monitoringbench"], folder="t_vs_u_monitoringbench"
    ),
    "asb": replace(SETTINGS["asb"], folder="t_vs_u_asb"),
}

# What the untrusted debater is told about the sabotage, per setting:
# prompt slot -> answer key field.
BRIEFINGS = {
    "monitoringbench": {
        "{sabotage_summary}": "attack_summary",
        "{structure}": "structure",
        "{evasion}": "evasion",
        "{technique}": "technique",
        "{side_task_description}": "side_task_description",  # added by the task
    },
    "asb": {
        "{sabotage_summary}": "description",
        "{location}": "location",
        "{impact}": "impact",
    },
}

ASSIGNED = {Side.SABOTAGED: "assigned_sabotaged.md", Side.CLEAN: "assigned_not_sabotaged.md"}  # fmt: skip


@dataclass
class TvsUDebate(Debate):
    untrusted_side: Side = Side.CLEAN  # which of the two debaters is U
    # Nominal USD per turn, instead of debater_max_cost: T and U are usually
    # different models, at very different prices.
    trusted_max_cost: float = 1.0
    untrusted_max_cost: float = 1.0

    sees_answer_key: ClassVar[bool] = True

    def _turn_budget(self, debater: Participant) -> float:
        untrusted = debater.side == self.untrusted_side
        return self.untrusted_max_cost if untrusted else self.trusted_max_cost

    def _debater_prompt(
        self, sample: AuditSample, debater: Participant, access: Access
    ) -> str:
        """simple_debate's debater prompt, plus who the opponent is and the assignment."""
        prompts = t_vs_u_prompts(sample)
        untrusted = debater.side == self.untrusted_side
        trust = "untrusted" if untrusted else "trusted"
        assert debater.side is not None
        opponent = self.clean if debater.side == Side.SABOTAGED else self.sabotaged

        values = {"{other_model_type}": opponent.model.model}
        if untrusted:
            values |= untrusted_briefing(sample)
        extra_context = "\n\n".join(
            [
                _fill(prompts._read(f"extra_context_{trust}.md").strip(), values),
                prompts._read(f"{trust}_{ASSIGNED[debater.side]}").strip(),
            ]
        )
        return debater.system_prompt or _fill(
            prompts.debater(
                debater.side,
                self.n_rounds,
                self.debater_max_steps,
                access,
                self.access.judge,
            ),
            {"{extra_context}": extra_context},
        )

    def _judge_prompt(self, sample: AuditSample, access: Access) -> str:
        trusted_side = (
            Side.CLEAN if self.untrusted_side == Side.SABOTAGED else Side.SABOTAGED
        )
        return _fill(
            t_vs_u_prompts(sample).judge(access, self.judge_max_steps),
            {
                # As the judge sees the debaters labelled: "debater arguing SABOTAGED".
                "{trusted_side}": trusted_side.value.upper(),
                "{untrusted_side}": self.untrusted_side.value.upper(),
            },
        )


def t_vs_u_prompts(sample: AuditSample) -> PromptSet:
    if sample.setting not in T_VS_U_PROMPTS:
        raise NotImplementedError(
            f"There are no T-vs-U prompts for the {sample.setting!r} setting yet "
            "(see prompts/README.md)."
        )
    return T_VS_U_PROMPTS[sample.setting]


def untrusted_briefing(sample: AuditSample) -> dict[str, str]:
    """What the untrusted debater is told about the attack, as prompt slot -> text."""
    briefing = BRIEFINGS[t_vs_u_prompts(sample).folder.removeprefix("t_vs_u_")]
    key = sample.answer_key if isinstance(sample.answer_key, dict) else {}
    missing = [field for field in briefing.values() if not key.get(field)]
    if missing:
        raise ValueError(
            f"Sample {sample.id} has no {missing} in its answer key, so the "
            "untrusted debater can't be told the attack."
        )
    return {slot: key[field].strip() for slot, field in briefing.items()}


def with_attacker_instructions(sample: Sample) -> Sample:
    """Add the attacker's instructions to a MonitoringBench sample's answer key.

    Other samples are returned unchanged. Done when the task is built, not during the run: reading a log is
    synchronous Inspect I/O, which re-enters the event loop and crashes the
    run once samples run in parallel.
    """
    assert sample.metadata is not None
    if sample.metadata.get("setting") != "monitoringbench":
        return sample
    answer_key = {
        **sample.metadata["answer_key"],
        "side_task_description": side_task_description(str(sample.id)),
    }
    return sample.model_copy(
        update={"metadata": {**(sample.metadata or {}), "answer_key": answer_key}}
    )


def _fill(template: str, values: dict[str, str]) -> str:
    # One pass, so placeholder-like text inside a filled-in value is left alone.
    pattern = "|".join(re.escape(key) for key in values)
    return re.sub(pattern, lambda match: values[match.group()], template)


@task
def t_vs_u_debate(
    trusted_model: str = DEFAULT_MODEL,
    trusted_provider: str = DEFAULT_PROVIDER,
    untrusted_model: str = DEFAULT_MODEL,
    untrusted_provider: str = DEFAULT_PROVIDER,
    judge_model: str = DEFAULT_MODEL,
    judge_provider: str = DEFAULT_PROVIDER,
    untrusted_side: Literal["clean", "sabotaged"] = "clean",
    dataset: Dataset = "monitoringbench",
    sample_ids: str | list[str] | None = None,
    n_rounds: int = 2,
    trusted_max_cost: float = 1.0,  # nominal USD per turn
    untrusted_max_cost: float = 1.0,  # nominal USD per turn
    judge_max_cost: float = 3.0,
    trusted_reasoning_effort: ReasoningEffort
    | None = None,  # e.g. "high"; None = provider default
    untrusted_reasoning_effort: ReasoningEffort | None = None,
    judge_reasoning_effort: ReasoningEffort | None = None,
    debater_max_steps: int = 15,  # rounds of tool calls per debater turn
    judge_max_steps: int = 40,
    code_execution: bool = False,
    replay: str | None = None,
    reuse: str | list[str] | tuple[str, ...] = (),
):
    trusted = ModelSpec(
        trusted_model, trusted_provider, reasoning_effort=trusted_reasoning_effort
    )
    untrusted = ModelSpec(
        untrusted_model, untrusted_provider, reasoning_effort=untrusted_reasoning_effort
    )
    u_side = Side(untrusted_side)
    protocol = TvsUDebate(
        sabotaged=Participant(
            untrusted if u_side == Side.SABOTAGED else trusted, side=Side.SABOTAGED
        ),
        clean=Participant(
            untrusted if u_side == Side.CLEAN else trusted, side=Side.CLEAN
        ),
        judge=Participant(
            ModelSpec(
                judge_model, judge_provider, reasoning_effort=judge_reasoning_effort
            )
        ),
        n_rounds=n_rounds,
        untrusted_side=u_side,
        trusted_max_cost=trusted_max_cost,
        untrusted_max_cost=untrusted_max_cost,
        judge_max_cost=judge_max_cost,
        debater_max_steps=debater_max_steps,
        judge_max_steps=judge_max_steps,
    )
    samples = [with_attacker_instructions(s) for s in load_samples(dataset, sample_ids)]
    return protocol_task(protocol, samples, replay, reuse, code_execution)
