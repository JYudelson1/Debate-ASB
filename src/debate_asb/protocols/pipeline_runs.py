"""Lone judge that runs the paper's full pipeline on code it may edit.

pipeline_lone_judge, with `pipeline_runs` (0 = the control arm): with
pipeline_runs > 0 the judge gets run_pipeline and must use all its runs
(default 1) before answering.

Both arms of a comparison go through the same task, so they differ only in the
tool and its prompt section: with pipeline_runs=0 the prompts are main's
(tools_execute) unchanged and no tool is added. Code execution is always on.

Only the papers in pipelines.SPECS have a pipeline (negative, diffuse, chain);
`sample_ids` defaults to PIPELINE_SAMPLES (their 6 codebases).

    uv run inspect eval src/debate_asb/protocols/pipeline_runs.py@pipeline_lone_judge \\
        --model none --log-model-api -T pipeline_runs=1 \\
        -T judge_model=openai/gpt-6-luna -T judge_provider=openai -T judge_reasoning_effort=high

Needs `uv sync --extra pipelines`, a Modal token (`modal token set`), and in the
Modal workspace a secret named "secrets" with HF_TOKEN (diffuse's app reads
it). See docs/pipeline-runs.md.
"""

from dataclasses import dataclass

from inspect_ai import task
from inspect_ai.model import ChatMessageSystem, ChatMessageUser
from inspect_ai.tool import Tool, ToolDef

from debate_asb.datasets import Dataset, load_samples
from debate_asb.models import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    ModelSpec,
    Participant,
    ReasoningEffort,
)
from debate_asb.pipelines import (
    PIPELINE_SAMPLES,
    Pipelines,
    pipeline_section,
    spec_for,
)
from debate_asb.prompts import PROMPTS, Access
from debate_asb.protocol import (
    AuditSample,
    ProtocolResult,
    ToolSet,
    access_for,
    agent_loop,
    get_credence,
)
from debate_asb.protocols.lone_judge import LoneJudge
from debate_asb.task import protocol_task


def _needs_run_bash(tools: list[Tool]) -> None:
    if not any(ToolDef(t).name == "run_bash" for t in tools):
        raise ValueError("pipeline runs need code execution (the Docker sandbox)")


def _section(sample: AuditSample, who: str, runs: int) -> str:
    template = (PROMPTS / sample.prompts.folder / "pipelines" / f"{who}.md").read_text()
    return pipeline_section(template, spec_for(sample.id), runs)


@dataclass
class PipelineLoneJudge(LoneJudge):
    pipeline_runs: int = 1  # 0 = control: main's lone judge

    async def run(self, sample: AuditSample, tools: ToolSet) -> ProtocolResult:
        judge_tools = tools.for_role("judge")
        access: Access = access_for(judge_tools)
        extras: list[str] = []
        before_stop = None
        if self.pipeline_runs > 0:
            _needs_run_bash(judge_tools)
            pipelines = Pipelines(
                sample.id, sample.codebase.root, "judge", self.pipeline_runs
            )
            judge_tools = [*judge_tools, pipelines.tool()]
            access = "tools_pipeline"
            extras = [_section(sample, "judge", self.pipeline_runs)]
            before_stop = pipelines.reminder  # the runs are required
        system_prompt = self.judge.system_prompt or sample.prompts.judge(
            access, self.max_steps, extras=extras
        )
        messages = [
            ChatMessageSystem(content=system_prompt),
            ChatMessageUser(content=sample.task),
        ]
        await agent_loop(
            self.judge,
            "judge",
            messages,
            judge_tools,
            self.max_steps,
            self.max_cost,
            before_stop=before_stop,
        )
        return ProtocolResult(
            credence=await get_credence(self.judge, "judge", messages, judge_tools)
        )


@task
def pipeline_lone_judge(
    judge_model: str = DEFAULT_MODEL,
    judge_provider: str = DEFAULT_PROVIDER,
    judge_reasoning_effort: ReasoningEffort | None = None,
    dataset: Dataset = "asb",
    sample_ids: str | list[str] | None = None,
    max_steps: int = 40,
    max_cost: float = 3.0,
    pipeline_runs: int = 1,
    replay: str | None = None,
    reuse: str | list[str] | tuple[str, ...] = (),
):
    judge = Participant(
        ModelSpec(judge_model, judge_provider, reasoning_effort=judge_reasoning_effort)
    )
    return protocol_task(
        PipelineLoneJudge(
            judge, max_steps=max_steps, max_cost=max_cost, pipeline_runs=pipeline_runs
        ),
        load_samples(dataset, sample_ids or PIPELINE_SAMPLES),
        replay,
        reuse,
        code_execution=True,
    )
