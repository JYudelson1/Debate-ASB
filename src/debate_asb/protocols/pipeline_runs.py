"""Lone judge and debate in which participants run the paper's full pipeline.

Two tasks, each with `pipeline_runs` (0 = the control arm):

- pipeline_lone_judge: the lone judge; with pipeline_runs > 0 it gets
  run_pipeline and must use all its runs (default 1) before answering.
- pipeline_debate: the plain debate; with pipeline_runs > 0 each debater gets
  run_pipeline and must use its runs (default 1) by the end of its last turn.
  The debate judge never runs the pipeline; it sees each run as a record
  written by the harness (the debater's diff, purpose and the end of the log),
  after the argument of the turn it was run in.

Both arms of a comparison go through the same task, so they differ only in the
tool and its prompt section: with pipeline_runs=0 the prompts are main's
(tools_execute) unchanged and no tool is added. Code execution is always on.

Only the papers in pipelines.SPECS have a pipeline (negative, diffuse,
awareness, chain); `sample_ids` defaults to PIPELINE_SAMPLES (6; awareness left out).

    uv run inspect eval src/debate_asb/protocols/pipeline_runs.py@pipeline_lone_judge \\
        --model none --log-model-api -T pipeline_runs=1 \\
        -T judge_model=openai/gpt-6-luna -T judge_provider=openai -T judge_reasoning_effort=high

Needs `uv sync --extra pipelines`, a Modal token (`modal token set`), and in the
Modal workspace a secret named "secrets" with HF_TOKEN (the papers' own apps
read it). See docs/pipeline-runs.md.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass, field

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
    Side,
)
from debate_asb.pipelines import (
    PIPELINE_SAMPLES,
    Pipelines,
    format_for_judge,
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
from debate_asb.protocols.simple_debate import Debate
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


@dataclass
class PipelineDebate(Debate):
    pipeline_runs: int = 1  # per debater; 0 = control: main's debate
    # Each debater's runs in each sample, keyed by (sample id, epoch, role). The
    # protocol object is shared by all samples, so per-sample state lives here.
    _pipelines: dict[tuple, Pipelines] = field(default_factory=dict, repr=False)

    def _debater_tools(
        self, sample: AuditSample, role: str, tools: list[Tool]
    ) -> list[Tool]:
        if self.pipeline_runs <= 0:
            return tools
        _needs_run_bash(tools)
        pipelines = Pipelines(sample.id, sample.codebase.root, role, self.pipeline_runs)
        self._pipelines[(sample.id, sample.epoch, role)] = pipelines
        return [*tools, pipelines.tool()]

    def _before_stop(
        self, sample: AuditSample, role: str, round: int
    ) -> Callable[[], str | None] | None:
        # Required by the end of the debater's last turn.
        if self.pipeline_runs <= 0 or round < self.n_rounds:
            return None
        return self._pipelines[(sample.id, sample.epoch, role)].reminder

    def _after_turn(self, sample: AuditSample, role: str, entry: dict) -> None:
        if self.pipeline_runs <= 0:
            return
        # Attach the runs made in this turn to its argument (saved with the
        # debate stage, so replays keep them).
        pipelines = self._pipelines[(sample.id, sample.epoch, role)]
        new = pipelines.records[pipelines.reported :]
        if new:
            entry["pipeline_runs"] = [asdict(r) for r in new]
            pipelines.reported = len(pipelines.records)
        if entry["round"] == self.n_rounds and role == "debater_clean":
            for r in ("debater_sabotaged", "debater_clean"):
                self._pipelines.pop((sample.id, sample.epoch, r), None)

    def _format_argument(self, entry: dict) -> str:
        text = super()._format_argument(entry)
        for record in entry.get("pipeline_runs", []):
            text += "\n\n" + format_for_judge(record)
        return text

    def _debater_prompt(
        self, sample: AuditSample, debater: Participant, access: Access
    ) -> str:
        if self.pipeline_runs <= 0:
            return super()._debater_prompt(sample, debater, access)
        assert debater.side is not None
        return debater.system_prompt or sample.prompts.debater(
            debater.side,
            self.n_rounds,
            self.debater_max_steps,
            "tools_pipeline",
            self.access.judge,
            extras=[_section(sample, "debater", self.pipeline_runs)],
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


@task
def pipeline_debate(
    debater_model: str = DEFAULT_MODEL,
    debater_provider: str = DEFAULT_PROVIDER,
    judge_model: str = DEFAULT_MODEL,
    judge_provider: str = DEFAULT_PROVIDER,
    debater_reasoning_effort: ReasoningEffort | None = None,
    judge_reasoning_effort: ReasoningEffort | None = None,
    dataset: Dataset = "asb",
    sample_ids: str | list[str] | None = None,
    n_rounds: int = 2,
    debater_max_cost: float = 1.0,  # nominal USD per turn
    judge_max_cost: float = 3.0,
    pipeline_runs: int = 1,
    replay: str | None = None,
    reuse: str | list[str] | tuple[str, ...] = (),
):
    debater = ModelSpec(
        debater_model, debater_provider, reasoning_effort=debater_reasoning_effort
    )
    protocol = PipelineDebate(
        sabotaged=Participant(debater, side=Side.SABOTAGED),
        clean=Participant(debater, side=Side.CLEAN),
        judge=Participant(
            ModelSpec(
                judge_model, judge_provider, reasoning_effort=judge_reasoning_effort
            )
        ),
        n_rounds=n_rounds,
        debater_max_cost=debater_max_cost,
        judge_max_cost=judge_max_cost,
        pipeline_runs=pipeline_runs,
    )
    return protocol_task(
        protocol,
        load_samples(dataset, sample_ids or PIPELINE_SAMPLES),
        replay,
        reuse,
        code_execution=True,
    )
