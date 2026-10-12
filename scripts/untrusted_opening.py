"""One turn of the T-vs-U debate: the untrusted debater's opening statement, and nothing else.

For looking at what the untrusted debater does with its briefing. It gets
exactly what it gets in protocols/t_vs_u_debate.py on its first turn (system
prompt, tools, budget), plus a word limit, which the protocol doesn't have.
No opponent argument comes first, and no judge follows, so there is no score.

    uv run inspect eval scripts/untrusted_opening.py --model none --log-model-api \\
        -T sample_id=e536N3iyKu2E4JHvQEfb6L
"""

from dataclasses import dataclass
from typing import ClassVar, Literal

from inspect_ai import task
from inspect_ai.model import ChatMessageSystem, ChatMessageUser

from debate_asb.datasets import load_samples
from debate_asb.models import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    ModelSpec,
    Participant,
    Side,
)
from debate_asb.protocol import (
    AuditSample,
    ProtocolResult,
    ToolAccess,
    ToolSet,
    access_for,
    agent_loop,
    note,
)
from debate_asb.protocols.t_vs_u_debate import TvsUDebate, with_attacker_instructions
from debate_asb.task import protocol_task


@dataclass
class UntrustedOpening:
    debate: TvsUDebate
    word_limit: int
    access: ToolAccess = ToolAccess()

    sees_answer_key: ClassVar[bool] = True

    async def run(self, sample: AuditSample, tools: ToolSet) -> ProtocolResult:
        debate = self.debate
        untrusted = (
            debate.clean if debate.untrusted_side == Side.CLEAN else debate.sabotaged
        )
        debater_tools = tools.for_role("debaters")
        messages = [
            ChatMessageSystem(
                content=debate._debater_prompt(
                    sample, untrusted, access_for(debater_tools)
                )
            ),
            ChatMessageUser(
                content=debate._turn_message(sample, [], 1)
                + f"\n\nDo not exceed {self.word_limit} words."
            ),
        ]
        argument = await agent_loop(
            untrusted,
            "debater_untrusted",
            messages,
            debater_tools,
            debate.debater_max_steps,
            debate.debater_max_cost,
        )
        note("argument", side=untrusted.side.value, argument=argument)
        return ProtocolResult(credence=None)


@task
def untrusted_opening(
    sample_id: str,
    untrusted_side: Literal["clean", "sabotaged"] = "clean",
    word_limit: int = 400,
    untrusted_model: str = DEFAULT_MODEL,
    untrusted_provider: str = DEFAULT_PROVIDER,
    trusted_model: str = DEFAULT_MODEL,  # only named in the prompt, never called
    trusted_provider: str = DEFAULT_PROVIDER,
):
    trusted = ModelSpec(trusted_model, trusted_provider)
    untrusted = ModelSpec(untrusted_model, untrusted_provider)
    u_side = Side(untrusted_side)
    debate = TvsUDebate(
        sabotaged=Participant(
            untrusted if u_side == Side.SABOTAGED else trusted, side=Side.SABOTAGED
        ),
        clean=Participant(
            untrusted if u_side == Side.CLEAN else trusted, side=Side.CLEAN
        ),
        judge=Participant(trusted),
        untrusted_side=u_side,
    )
    return protocol_task(
        UntrustedOpening(debate, word_limit),
        [
            with_attacker_instructions(s)
            for s in load_samples("monitoringbench", sample_id)
        ],
    )
