#!/usr/bin/env python3
"""Offline, two-phase analysis of Debate-ASB extraction logs. No model calls."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from inspect_ai.log import read_eval_log

from debate_asb.viewer.rubrics import load_rubric_context
from debate_asb.viewer.spans import SpanIndex

SKILL = Path(__file__).resolve().parents[1]
ROLES = (
    "paper/docs",
    "data/preprocessing",
    "model/training",
    "prompts/config",
    "evaluation/grading",
    "analysis/plots",
    "orchestration/setup",
    "results/data",
    "other",
)
ROLE_RULES = [
    (r"(^|/)(paper|readme)[^/]*\.|\.(md|rst|pdf)$", "paper/docs"),
    (r"prompt|config|cfgs|\.(yaml|yml|toml|cfg)$", "prompts/config"),
    (
        r"datagen|dataset|preprocess|synthetic.*generate|generate_dataset",
        "data/preprocessing",
    ),
    (r"evaluat|grad(e|ing)|monitor|scor(e|ing)", "evaluation/grading"),
    (r"plot|analy|probes|ablation", "analysis/plots"),
    (r"finetun|train|optim|arch/|activations|steer_core|model", "model/training"),
    (
        r"(^|/)(main|run|setup|replicate)|\.sh$|docker|requirements",
        "orchestration/setup",
    ),
    (r"(^|/)(data|results|logs)/|\.(pt|csv|jsonl|json|txt|npy|npz)$", "results/data"),
]
THEMES = {
    "data/splits": r"\b(data|dataset|train|test|split|leakage|correlation|shuffle)\b",
    "training/config": r"\b(epoch|epochs|learning.rate|finetun\w*|hyperparam\w*|seed)\b",
    "evaluation/metrics": r"\b(evaluat\w*|metric\w*|score\w*|accuracy|capacity|auroc)\b",
    "provenance/model": r"\b(provenance|checkpoint|adapter|lora|embedding\w*|vector)\b",
    "prompts/behavior": r"\b(prompt\w*|reasoning|suspicio\w*|suppression|refusal\w*)\b",
}
INCLUDED = {"yes", "partial", "no", "mismatch", "unknown", "not_applicable"}
FAILURE_STAGES = {
    "none",
    "not_exposed",
    "exposed_not_selected",
    "selected_not_connected",
    "ground_truth_mismatch",
    "unknown",
    "not_applicable",
}


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def normalize_path(value: str, root: str | None = None) -> str:
    value = value.replace("\\", "/")
    for prefix in [str(root).rstrip("/") + "/" if root else "", "/workspace/"]:
        if prefix and value.startswith(prefix):
            value = value[len(prefix) :]
    return str(PurePosixPath(value.removeprefix("./")))


def role(path: str, overrides: dict[str, str]) -> str:
    for pattern, label in overrides.items():
        if fnmatch.fnmatchcase(path, pattern):
            return label
    for pattern, label in ROLE_RULES:
        if re.search(pattern, path, re.I):
            return label
    return "other"


def read_lines(result: str) -> dict[int, str]:
    """Use only numbered lines actually returned, preserving source indentation."""
    lines = {}
    for line in result.splitlines():
        match = re.match(r"^\s*(\d+)  (.*)$", line)
        if match:
            lines[int(match[1])] = match[2]
    return lines


def search_lines(result: str, root: str | None) -> list[dict[str, Any]]:
    hits = []
    for line in result.splitlines():
        match = re.match(r"^(.+?):(\d+): ?(.*)$", line)
        if match:
            hits.append(
                {
                    "path": normalize_path(match[1], root),
                    "line": int(match[2]),
                    "text": match[3],
                }
            )
    return hits


def verify_excerpt(excerpt: dict, tools: list[dict], root: str | None) -> dict:
    path = normalize_path(excerpt.get("path", ""), root)
    wanted = excerpt.get("text", "").splitlines()
    if not wanted:
        return {"status": "unverified", "tool": None}
    for tool in tools:
        if (
            tool["failed"]
            or tool["function"] != "read_file"
            or tool.get("path") != path
        ):
            continue
        lines = read_lines(tool["result"])
        for start in lines:
            actual = [lines.get(start + i) for i in range(len(wanted))]
            if actual == wanted and not any(
                "[... line truncated," in x for x in wanted
            ):
                return {
                    "status": "verified_read",
                    "tool": tool["ordinal"],
                    "actual_start": start,
                    "actual_end": start + len(wanted) - 1,
                    "line_hint_matches": start == excerpt.get("start_line"),
                }
    # A search-only sequence is a weaker source: tool output can clip each line.
    for tool in tools:
        if tool["failed"] or tool["function"] != "search":
            continue
        hits = {
            h["line"]: h["text"]
            for h in search_lines(tool["result"], root)
            if h["path"] == path
        }
        for start in hits:
            if [hits.get(start + i) for i in range(len(wanted))] == wanted:
                return {
                    "status": "search_only",
                    "tool": tool["ordinal"],
                    "actual_start": start,
                    "actual_end": start + len(wanted) - 1,
                }
    return {"status": "unverified", "tool": None}


def jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value


def extraction_trace(sample: Any) -> dict:
    spans = SpanIndex(sample.events or [])
    tools, reasoning, assistant = [], [], []
    model_calls, redacted = 0, 0
    model_configs = []
    root = ((sample.metadata or {}).get("artifacts", {}).get("codebase") or {}).get(
        "root"
    )
    for event_index, event in enumerate(sample.events or []):
        activity = spans.activity(event.span_id)
        if not activity or activity.kind != "extraction":
            continue
        if event.event == "tool":
            args = event.arguments or {}
            result = (
                event.result
                if isinstance(event.result, str)
                else json.dumps(event.result)
            )
            tools.append(
                {
                    "ordinal": len(tools) + 1,
                    "event_index": event_index,
                    "timestamp": str(event.timestamp),
                    "function": event.function,
                    "arguments": args,
                    "result": result,
                    "failed": bool(event.error or event.failed or event.pending),
                    "error": jsonable(event.error),
                    "truncated": jsonable(event.truncated),
                    "path": normalize_path(str(args["path"]), root)
                    if "path" in args
                    else None,
                }
            )
        elif event.event == "model":
            model_calls += 1
            config = {
                "model": event.model,
                "config": jsonable(getattr(event, "config", None)),
            }
            if config not in model_configs:
                model_configs.append(config)
            if not event.output:
                continue
            for choice in event.output.choices:
                content = choice.message.content
                if isinstance(content, str):
                    if content.strip():
                        assistant.append({"event_index": event_index, "text": content})
                    continue
                for block in content:
                    b = jsonable(block)
                    if b.get("type") == "reasoning":
                        if b.get("redacted") or "reasoning.encrypted" in str(
                            b.get("signature", "")
                        ):
                            redacted += 1
                        elif b.get("reasoning"):
                            reasoning.append(
                                {
                                    "event_index": event_index,
                                    "kind": "reasoning",
                                    "text": b["reasoning"],
                                }
                            )
                        if b.get("summary") and not b.get("redacted"):
                            reasoning.append(
                                {
                                    "event_index": event_index,
                                    "kind": "summary",
                                    "text": str(b["summary"]),
                                }
                            )
                    elif b.get("type") == "text" and b.get("text", "").strip():
                        assistant.append(
                            {"event_index": event_index, "text": b["text"]}
                        )
    return {
        "tools": tools,
        "readable_reasoning": reasoning,
        "assistant_text": assistant,
        "model_calls": model_calls,
        "redacted_blocks": redacted,
        "model_configs": model_configs,
    }
