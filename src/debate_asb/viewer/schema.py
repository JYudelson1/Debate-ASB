"""Small, serializable view models used by the eval-log viewer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExcerptView:
    path: str
    start_line: int | None
    end_line: int | None
    text: str


@dataclass(frozen=True)
class TurnView:
    round: int
    side: str
    argument: str


@dataclass(frozen=True)
class TraceView:
    kind: str
    timestamp: str | None
    role: str | None = None
    model: str | None = None
    function: str | None = None
    arguments: Any = None
    content: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BundleView:
    number: int
    observation: str
    excerpts: tuple[ExcerptView, ...]
    turns: tuple[TurnView, ...] = ()
    judge_response: str | None = None
    judge_credence: float | None = None
    judge_verdict: str | None = None
    status: str = "extracted"
    metadata: dict[str, Any] = field(default_factory=dict)
    trace: tuple[TraceView, ...] = ()


@dataclass(frozen=True)
class SampleView:
    id: str
    epoch: int
    target: str | None
    paper_name: str | None
    codebase_root: str | None
    status: str
    stage_progress: dict[str, Any]
    bundles: tuple[BundleView, ...]
    aggregation: dict[str, Any] | None = None
    rubric: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReportView:
    source_log: str
    task: str
    status: str
    task_args: dict[str, Any]
    revision: dict[str, Any] | None
    stats: dict[str, Any]
    samples: tuple[SampleView, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
