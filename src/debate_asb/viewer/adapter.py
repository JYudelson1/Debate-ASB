"""Convert Inspect eval logs into a bundle-oriented report model."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from inspect_ai.log import EvalLog, read_eval_log

from debate_asb.viewer.markdown import markdown_text
from debate_asb.viewer.rubrics import load_rubric_context
from debate_asb.viewer.schema import (
    BundleView,
    ExcerptView,
    ReportView,
    SampleView,
    TraceView,
    TurnView,
)

BUNDLE_SPAN = re.compile(r"^bundle/(?P<number>\d+)/(?:debate|judgment)(?:/|$)")


def load_report(log_path: str | Path) -> ReportView:
    """Read an Inspect log, resolving attachment-backed text for the report."""
    path = Path(log_path).resolve()
    log = read_eval_log(str(path), resolve_attachments="full")
    return adapt_log(log, str(path))


def adapt_log(log: EvalLog, source_log: str) -> ReportView:
    """Adapt a loaded log. Kept separate from I/O for focused unit tests."""
    evaluation = log.eval
    return ReportView(
        source_log=source_log,
        task=str(evaluation.task),
        status=_value(log.status),
        task_args=_as_dict(evaluation.task_args),
        revision=_optional_dict(evaluation.revision),
        stats=_as_dict(log.stats),
        samples=tuple(_adapt_sample(sample) for sample in (log.samples or [])),
    )


def _adapt_sample(sample: Any) -> SampleView:
    store = _as_dict(sample.store)
    stages = _as_dict(store.get("stages"))
    evaluation = _as_dict(store.get("evaluation"))
    extraction = _as_dict(stages.get("extraction") or evaluation.get("extraction"))
    transcript = list(store.get("transcript") or [])

    bundles = _bundle_records(extraction, stages, evaluation, transcript)
    traces = _bundle_traces(sample.events or [])
    bundle_views = tuple(
        _bundle_view(record, traces.get(record["number"], ()))
        for record in sorted(bundles.values(), key=lambda item: item["number"])
    )

    metadata = _as_dict(sample.metadata)
    target = _target_text(sample.target)
    error = _optional_dict(sample.error)
    return SampleView(
        id=str(sample.id),
        epoch=int(sample.epoch),
        target=target,
        paper_name=metadata.get("paper_name"),
        codebase_root=(
            _as_dict(_as_dict(metadata.get("artifacts")).get("codebase")).get("root")
        ),
        status="error" if error else "success",
        stage_progress=_stage_progress(stages, store, bundle_views),
        bundles=bundle_views,
        aggregation=_optional_dict(store.get("aggregation")),
        rubric=load_rubric_context(
            str(sample.id), target, _optional_dict(metadata.get("answer_key"))
        ),
        error=error,
        provenance=_provenance(sample, stages, store, transcript),
    )


def _bundle_records(
    extraction: dict[str, Any],
    stages: dict[str, Any],
    evaluation: dict[str, Any],
    transcript: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    records: dict[int, dict[str, Any]] = {}
    for bundle in extraction.get("bundles") or []:
        number = int(bundle["number"])
        records[number] = {**bundle, "turns": []}

    debates = stages.get("bundle_debates") or []
    for debate in debates:
        number = int(debate["bundle_number"])
        if number in records:
            records[number]["turns"] = debate.get("turns") or []
            records[number]["debate_metadata"] = debate.get("metadata") or {}

    judgments = stages.get("bundle_judgments") or evaluation.get("bundle_debates") or []
    for judgment in judgments:
        bundle = judgment.get("bundle") or {}
        number = int(bundle.get("number", judgment.get("bundle_number", 0)))
        if number not in records and bundle:
            records[number] = {**bundle, "turns": []}
        if number in records:
            records[number]["turns"] = judgment.get("turns") or records[number]["turns"]
            records[number]["judge_response"] = judgment.get("judge_response")
            records[number]["judge_credence"] = judgment.get("judge_credence")
            records[number]["judgment_metadata"] = judgment.get("metadata") or {}

    _merge_transcript(records, transcript)
    if not records:
        _merge_legacy_debate(records, stages)
    return records


def _merge_transcript(
    records: dict[int, dict[str, Any]], transcript: list[dict[str, Any]]
) -> None:
    turns: dict[int, list[dict[str, Any]]] = defaultdict(list)
    credences: dict[int, float | None] = {}
    for entry in transcript:
        number = entry.get("bundle")
        if not isinstance(number, int) or number not in records:
            continue
        if entry.get("event") == "argument":
            turns[number].append(
                {
                    "round": entry["round"],
                    "side": entry["side"],
                    "argument": entry["argument"],
                }
            )
        elif entry.get("event") == "bundle_judgment":
            credences[number] = entry.get("credence")

    for number, found in turns.items():
        if not records[number].get("turns"):
            records[number]["turns"] = found
            records[number]["turns_inferred_from_transcript"] = True
    for number, credence in credences.items():
        if "judge_credence" not in records[number]:
            records[number]["judge_credence"] = credence
            records[number]["judgment_inferred_from_transcript"] = True


def _merge_legacy_debate(
    records: dict[int, dict[str, Any]], stages: dict[str, Any]
) -> None:
    debate = _as_dict(stages.get("debate"))
    for position, evidence in enumerate(debate.get("evidence_sets") or [], start=1):
        evidence_id = str(evidence.get("evidence_id", position))
        digits = re.findall(r"\d+", evidence_id)
        number = int(digits[-1]) if digits else position
        records[number] = {
            "number": number,
            "observation": evidence.get("bundle") or f"Legacy evidence {evidence_id}",
            "excerpts": [],
            "turns": evidence.get("arguments") or [],
            "judge_credence": evidence.get("credence"),
            "judge_verdict": evidence.get("verdict"),
            "legacy": True,
        }


def _bundle_view(record: dict[str, Any], trace: tuple[TraceView, ...]) -> BundleView:
    turns = tuple(
        TurnView(
            int(turn["round"]), str(turn["side"]), markdown_text(str(turn["argument"]))
        )
        for turn in record.get("turns") or []
    )
    judge_response = record.get("judge_response")
    credence = record.get("judge_credence")
    verdict = record.get("judge_verdict")
    if verdict is None and credence is not None:
        verdict = "sabotaged" if float(credence) >= 50 else "honest"
    status = "judged" if credence is not None else "debated" if turns else "extracted"
    metadata = {
        key: record[key]
        for key in (
            "debate_metadata",
            "judgment_metadata",
            "turns_inferred_from_transcript",
            "judgment_inferred_from_transcript",
            "legacy",
        )
        if key in record
    }
    return BundleView(
        number=int(record["number"]),
        observation=str(record.get("observation") or ""),
        excerpts=tuple(_excerpt_view(value) for value in record.get("excerpts") or []),
        turns=turns,
        judge_response=(
            markdown_text(str(judge_response)) if judge_response is not None else None
        ),
        judge_credence=float(credence) if credence is not None else None,
        judge_verdict=verdict,
        status=status,
        metadata=metadata,
        trace=trace,
    )


def _excerpt_view(value: dict[str, Any]) -> ExcerptView:
    return ExcerptView(
        path=str(value.get("path") or ""),
        start_line=_optional_int(value.get("start_line")),
        end_line=_optional_int(value.get("end_line")),
        text=str(value.get("text") or ""),
    )


def _stage_progress(
    stages: dict[str, Any], store: dict[str, Any], bundles: tuple[BundleView, ...]
) -> dict[str, Any]:
    total = len(bundles)
    return {
        "extraction": "complete" if "extraction" in stages else "missing",
        "bundles": total,
        "debates": sum(bool(bundle.turns) for bundle in bundles),
        "judgments": sum(bundle.judge_credence is not None for bundle in bundles),
        "aggregation": "complete" if store.get("aggregation") else "missing",
        "replayed_stages": sorted(_as_dict(store.get("replayed_stages"))),
    }


def _provenance(
    sample: Any,
    stages: dict[str, Any],
    store: dict[str, Any],
    transcript: list[dict[str, Any]],
) -> dict[str, Any]:
    stage_metadata: dict[str, Any] = {}
    extraction = _as_dict(stages.get("extraction"))
    if extraction.get("metadata"):
        stage_metadata["extraction"] = extraction["metadata"]
    for name in ("bundle_debates", "bundle_judgments"):
        values = stages.get(name) or []
        stage_metadata[name] = [
            value.get("metadata") for value in values if value.get("metadata")
        ]
    return {
        "cost": _as_dict(store.get("cost")),
        "usage": _as_dict(store.get("usage")),
        "source_log": store.get("replay_source"),
        "stage_metadata": stage_metadata,
        "transcript_events": dict(Counter(item.get("event") for item in transcript)),
        "inspect_events": dict(Counter(event.event for event in sample.events or [])),
        "total_time": sample.total_time,
        "working_time": sample.working_time,
    }


def _bundle_traces(events: list[Any]) -> dict[int, tuple[TraceView, ...]]:
    spans: dict[str, tuple[str | None, str]] = {}
    for event in events:
        if event.event == "span_begin":
            spans[event.id] = (event.parent_id, event.name)

    grouped: dict[int, list[TraceView]] = defaultdict(list)
    for event in events:
        if event.event not in {"model", "tool", "error"}:
            continue
        trace_name = _trace_name(event.span_id, spans)
        if trace_name is None:
            continue
        match = BUNDLE_SPAN.match(trace_name)
        if match:
            grouped[int(match.group("number"))].append(_trace_view(event, trace_name))
    return {number: tuple(values) for number, values in grouped.items()}


def _trace_name(
    span_id: str | None, spans: dict[str, tuple[str | None, str]]
) -> str | None:
    current = span_id
    visited: set[str] = set()
    while current and current not in visited:
        visited.add(current)
        parent, name = spans.get(current, (None, ""))
        if BUNDLE_SPAN.match(name):
            return name
        current = parent
    return None


def _trace_view(event: Any, trace_name: str) -> TraceView:
    data = event.model_dump(exclude_none=True)
    kind = event.event
    role = "judge" if "/judgment" in trace_name else trace_name.rsplit("/", 1)[-1]
    if kind == "model":
        output = _as_dict(data.get("output"))
        return TraceView(
            kind=kind,
            timestamp=str(data.get("timestamp")) if data.get("timestamp") else None,
            role=role,
            model=data.get("model"),
            content=output.get("completion"),
            usage=_as_dict(output.get("usage")),
        )
    if kind == "tool":
        return TraceView(
            kind=kind,
            timestamp=str(data.get("timestamp")) if data.get("timestamp") else None,
            role=role,
            function=data.get("function"),
            arguments=data.get("arguments"),
            content=_text(data.get("result") or data.get("error")),
        )
    return TraceView(
        kind=kind,
        timestamp=str(data.get("timestamp")) if data.get("timestamp") else None,
        role=role,
        content=_text(data.get("error") or data.get("message") or data),
    )


def _target_text(target: Any) -> str | None:
    if target is None:
        return None
    text = getattr(target, "text", target)
    return str(text)


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return dict(value)


def _optional_dict(value: Any) -> dict[str, Any] | None:
    return _as_dict(value) if value is not None else None


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value, default=str, indent=2)
