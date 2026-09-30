"""Replayable evidence extraction stage for extract-and-evaluate debate.

This layer runs one tool-assisted extractor and saves ten validated evidence
bundles in the ``extraction`` stage. Bundle debates and judgments are added by
the next stack layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from inspect_ai import task
from inspect_ai.model import ChatMessageSystem, ChatMessageUser
from inspect_ai.util import store

from debate_asb.datasets import load_samples
from debate_asb.evidence import (
    ExtractionResult,
    ModelMetadata,
    StageMetadata,
    content_sha256,
    extraction_result_from_dict,
    result_to_dict,
)
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
    stage,
)
from debate_asb.task import protocol_task

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts" / "asb"


@dataclass
class ExtractAndEvaluateDebate:
    """Extract evidence now; later stack layers add debate and judgment."""

    extractor: Participant
    sabotaged: Participant
    clean: Participant
    judge: Participant
    n_rounds: int = 2
    extractor_max_steps: int = 60
    extractor_max_cost: float = 5.0
    debater_max_steps: int = 15
    debater_max_cost: float = 1.0
    judge_max_steps: int = 40
    judge_max_cost: float = 3.0
    access: ToolAccess = ToolAccess()

    def __post_init__(self) -> None:
        if (self.sabotaged.side, self.clean.side) != (Side.SABOTAGED, Side.CLEAN):
            raise ValueError(
                "Debaters need opposing sides: sabotaged=SABOTAGED, clean=CLEAN"
            )
        if self.n_rounds < 1:
            raise ValueError("n_rounds must be positive")

    async def run(self, sample: AuditSample, tools: ToolSet) -> ProtocolResult:
        if sample.setting != "asb":
            raise ValueError("extract-and-evaluate debate currently supports ASB only")
        extraction_data = await stage("extraction", lambda: self.extract(sample, tools))
        extraction = extraction_result_from_dict(extraction_data)
        if extraction.metadata.sample_id != sample.id:
            raise ValueError(
                f"replayed stage is for {extraction.metadata.sample_id}, not {sample.id}"
            )
        note("extraction_stage_complete", bundles=len(extraction.bundles))
        return ProtocolResult(credence=None)

    async def extract(self, sample: AuditSample, tools: ToolSet) -> dict[str, Any]:
        extractor_tools = tools.for_role("extractor")
        prompt = self._extractor_prompt(access_for(extractor_tools))
        messages = [
            ChatMessageSystem(content=prompt),
            ChatMessageUser(
                content=f"{sample.task}\n\n---\n\n{_prompt('game_rules.md')}"
            ),
        ]
        costs_before = dict(store().get("cost", {}))
        raw_output = await agent_loop(
            self.extractor,
            "extractor",
            messages,
            extractor_tools,
            self.extractor_max_steps,
            self.extractor_max_cost,
        )
        costs_after = dict(store().get("cost", {}))
        costs = {
            role: round(cost - costs_before.get(role, 0.0), 12)
            for role, cost in costs_after.items()
            if cost > costs_before.get(role, 0.0)
        }
        result = ExtractionResult.from_raw(
            StageMetadata(
                sample_id=sample.id,
                sample_epoch=sample.epoch,
                stage="extraction",
                models={"extractor": _model_metadata(self.extractor.model)},
                prompt_versions={"extract": content_sha256(prompt)},
                config={
                    "bundle_count": 10,
                    "max_steps": self.extractor_max_steps,
                },
                costs_usd=costs,
                source_log=store().get("replay_source"),
            ),
            raw_output,
        )
        note("extraction_complete", bundles=len(result.bundles))
        return result_to_dict(result)

    def _extractor_prompt(self, access: str) -> str:
        access_text = _render(
            _prompt(f"access_{access}.md"), max_steps=self.extractor_max_steps
        )
        return _render(
            _prompt("extract_and_evaluate/extract.md"), access=access_text.strip()
        )


def _model_metadata(spec: ModelSpec) -> ModelMetadata:
    return ModelMetadata(
        model=spec.model,
        provider=spec.provider,
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
    )


@cache
def _prompt(relative_path: str) -> str:
    return (PROMPT_DIR / relative_path).read_text()


def _render(template: str, **values: object) -> str:
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


@task
def extract_and_evaluate_debate(
    extractor_model: str = DEFAULT_MODEL,
    extractor_provider: str = DEFAULT_PROVIDER,
    debater_model: str = DEFAULT_MODEL,
    debater_provider: str = DEFAULT_PROVIDER,
    judge_model: str = DEFAULT_MODEL,
    judge_provider: str = DEFAULT_PROVIDER,
    sample_ids: str | list[str] | None = None,
    n_rounds: int = 2,
    code_execution: bool = False,
    replay: str | None = None,
    reuse: str | list[str] = (),
):
    extractor = Participant(ModelSpec(extractor_model, extractor_provider))
    debater = ModelSpec(debater_model, debater_provider)
    protocol = ExtractAndEvaluateDebate(
        extractor=extractor,
        sabotaged=Participant(debater, side=Side.SABOTAGED),
        clean=Participant(debater, side=Side.CLEAN),
        judge=Participant(ModelSpec(judge_model, judge_provider)),
        n_rounds=n_rounds,
    )
    return protocol_task(
        protocol,
        load_samples("asb", sample_ids),
        replay,
        reuse,
        code_execution,
    )
