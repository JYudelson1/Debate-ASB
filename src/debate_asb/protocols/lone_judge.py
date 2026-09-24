"""Lone judge: the baseline everything else is measured against.

One model explores the sample (a paper and codebase, or an agent trajectory)
with read-only tools, then gives a credence that it was sabotaged.

    uv run inspect eval src/debate_asb/protocols/lone_judge.py --model none --log-model-api \\
        -T sample_ids=hop_jump,hop_stride
"""

from dataclasses import dataclass

from inspect_ai import task
from inspect_ai.model import ChatMessageSystem, ChatMessageUser

from debate_asb.datasets import Dataset, load_samples
from debate_asb.models import DEFAULT_MODEL, DEFAULT_PROVIDER, ModelSpec, Participant
from debate_asb.protocol import (
    AuditSample,
    ProtocolResult,
    ToolAccess,
    ToolSet,
    access_for,
    agent_loop,
    get_credence,
)
from debate_asb.task import protocol_task


@dataclass
class LoneJudge:
    judge: Participant
    max_steps: int = 40
    max_cost: float = 3.0  # nominal USD per sample; once spent, the judge must answer
    access: ToolAccess = ToolAccess()

    async def run(self, sample: AuditSample, tools: ToolSet) -> ProtocolResult:
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
        return ProtocolResult(
            credence=await get_credence(self.judge, "judge", messages, judge_tools)
        )


@task
def lone_judge(
    judge_model: str = DEFAULT_MODEL,
    judge_provider: str = DEFAULT_PROVIDER,
    dataset: Dataset = "asb",
    sample_ids: str | list[str] | None = None,
    max_steps: int = 40,
    max_cost: float = 3.0,
    code_execution: bool = False,
    replay: str | None = None,
    reuse: str | list[str] = (),
):
    judge = Participant(ModelSpec(judge_model, judge_provider))
    return protocol_task(
        LoneJudge(judge, max_steps=max_steps, max_cost=max_cost),
        load_samples(dataset, sample_ids),
        replay,
        reuse,
        code_execution,
    )
