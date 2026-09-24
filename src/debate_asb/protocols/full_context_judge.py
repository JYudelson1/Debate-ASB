"""Full-context judge: the whole sample in one prompt, no navigation, one answer.

The cheap, honest baseline. If it beats the agentic lone judge, that's a finding.

The code, docs and config files go in whole; data, logs and other large files
are listed by name (see Codebase.dump_all). If the dump won't fit in the
judge's context window it raises ArtifactTooLarge rather than truncating, and
by default Inspect then stops the run. To skip too-large samples and keep
going, run with `--no-fail-on-error`.

    uv run inspect eval src/debate_asb/protocols/full_context_judge.py --model none --log-model-api \\
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
    generate,
    get_credence,
)
from debate_asb.task import protocol_task

# Room left in the context window for the judge's answer (fixes, explanation, credence).
OUTPUT_RESERVE_TOKENS = 32_000


@dataclass
class FullContextJudge:
    judge: Participant
    # No tools: the point of this baseline is reading everything with no navigation.
    access: ToolAccess = ToolAccess(judge=False)

    async def run(self, sample: AuditSample, tools: ToolSet) -> ProtocolResult:
        max_tokens = self.judge.model.context_length() - OUTPUT_RESERVE_TOKENS
        contents = sample.dump_all(max_tokens)
        messages = [
            ChatMessageSystem(
                content=self.judge.system_prompt or sample.prompts.judge("full_context")
            ),
            ChatMessageUser(content=f"{sample.task}\n\n{contents}"),
        ]

        await generate(self.judge, "judge", messages)
        return ProtocolResult(
            credence=await get_credence(self.judge, "judge", messages)
        )


@task
def full_context_judge(
    judge_model: str = DEFAULT_MODEL,
    judge_provider: str = DEFAULT_PROVIDER,
    dataset: Dataset = "asb",
    sample_ids: str | list[str] | None = None,
):
    judge = Participant(ModelSpec(judge_model, judge_provider))
    return protocol_task(FullContextJudge(judge), load_samples(dataset, sample_ids))
