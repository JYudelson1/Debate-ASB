"""Lone judge with one follow-up question after its answer.

The judge runs exactly as in lone_judge.py (same system prompt, task message, agent
loop and credence re-ask), so on the same samples, model and settings it sends the
same requests, and a lone judge's saved calls replay from the model cache. Its first
answer's credence is recorded (store "first_credence", note "first_answer"). Then it
gets one more user message, from this protocol's prompt folder (prompts/asb/followup/),
and answers it without tools; the credence in that reply is the sample's score.

- followup=unbanded: the credence again, without the credence ranges of the base prompt.
- followup=criteria: 0-100 ratings of Fix 1 on the three verification criteria the
  base prompt states (store "followup" -> "ratings"), then the credence again.

The follow-up call runs under the role "followup", so fixscore.py still grades the
judge's own answer and the runner reports the follow-up's cost on its own.

    uv run inspect eval src/debate_asb/protocols/followup_judge.py --model none --log-model-api \\
        -T sample_ids=hop_jump,hop_stride -T followup=criteria
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from inspect_ai import task
from inspect_ai.model import ChatMessageSystem, ChatMessageUser
from inspect_ai.util import store

from debate_asb.datasets import Dataset, load_samples
from debate_asb.models import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    ModelSpec,
    Participant,
    ReasoningEffort,
)
from debate_asb.prompts import PROMPTS
from debate_asb.protocol import (
    AuditSample,
    ProtocolResult,
    ToolAccess,
    ToolSet,
    access_for,
    agent_loop,
    answer_without_tools,
    get_credence,
    note,
)
from debate_asb.task import protocol_task

FollowUp = Literal["unbanded", "criteria"]

# The rating lines criteria.md asks for, in its order (the three verification criteria).
CRITERIA = ("Executed", "Changes key findings", "More likely than alternatives")


def followup_folder(sample: AuditSample) -> Path:
    """This protocol's prompts for the sample's setting, e.g. prompts/asb/followup/."""
    folder = PROMPTS / sample.prompts.folder / "followup"
    if not folder.is_dir():
        raise NotImplementedError(
            f"There are no follow-up prompts for the {sample.setting!r} setting yet "
            "(see prompts/README.md)."
        )
    return folder


def parse_ratings(text: str) -> dict[str, float | None]:
    """Each criterion's 0-100 rating, from lines like 'Executed: 90' (the last one counts)."""
    ratings: dict[str, float | None] = {}
    for name in CRITERIA:
        found = re.findall(
            rf"^\W*{re.escape(name)}\W*:\W*(\d+(?:\.\d+)?)", text, re.I | re.M
        )
        ratings[name] = min(float(found[-1]), 100.0) if found else None
    return ratings


@dataclass
class FollowUpJudge:
    judge: Participant
    followup: FollowUp = "unbanded"
    max_steps: int = 40
    max_cost: float = 3.0  # nominal USD per sample for the investigation, as LoneJudge
    access: ToolAccess = ToolAccess()

    async def run(self, sample: AuditSample, tools: ToolSet) -> ProtocolResult:
        question = (followup_folder(sample) / f"{self.followup}.md").read_text().strip()

        # LoneJudge.run with the base prompts, call for call.
        judge_tools = tools.for_role("judge")
        system_prompt = self.judge.system_prompt or sample.prompts.judge(
            access_for(judge_tools), self.max_steps
        )
        messages = [
            ChatMessageSystem(content=system_prompt),
            ChatMessageUser(content=sample.task),
        ]
        await agent_loop(
            self.judge, "judge", messages, judge_tools, self.max_steps, self.max_cost
        )
        first = await get_credence(self.judge, "judge", messages, judge_tools)
        store().set("first_credence", first)
        note("first_answer", credence=first)

        messages.append(ChatMessageUser(content=question))
        reply = await answer_without_tools(
            self.judge, "followup", messages, judge_tools
        )
        n_messages = len(messages)
        credence = await get_credence(self.judge, "followup", messages, judge_tools)
        record: dict = {"kind": self.followup, "credence": credence}
        if self.followup == "criteria":
            ratings = parse_ratings(reply.completion)
            if len(messages) > n_messages:  # the credence was asked for again
                again = parse_ratings(messages[-1].text)
                ratings = {
                    k: v if v is not None else again[k] for k, v in ratings.items()
                }
            record["ratings"] = ratings
        store().set("followup", record)
        note("followup_answer", **record)
        return ProtocolResult(credence=credence)


@task
def followup_judge(
    followup: FollowUp = "unbanded",
    judge_model: str = DEFAULT_MODEL,
    judge_provider: str = DEFAULT_PROVIDER,
    judge_reasoning_effort: ReasoningEffort | None = None,
    dataset: Dataset = "asb",
    sample_ids: str | list[str] | None = None,
    max_steps: int = 40,
    max_cost: float = 3.0,
    code_execution: bool = False,
):
    if followup not in ("unbanded", "criteria"):
        raise ValueError(f"followup is 'unbanded' or 'criteria', not {followup!r}")
    judge = Participant(
        ModelSpec(judge_model, judge_provider, reasoning_effort=judge_reasoning_effort)
    )
    return protocol_task(
        FollowUpJudge(judge, followup, max_steps=max_steps, max_cost=max_cost),
        load_samples(dataset, sample_ids),
        code_execution=code_execution,
    )
