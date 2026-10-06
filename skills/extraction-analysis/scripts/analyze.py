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


def measure(
    trace: dict,
    bundles: list[dict],
    context: dict | None,
    root: str | None,
    overrides: dict,
) -> dict:
    files: dict[str, dict] = {}

    def file(path: str) -> dict:
        if path not in files:
            files[path] = {
                "path": path,
                "role": role(path, overrides),
                "listed": False,
                "read_calls": 0,
                "read_lines": Counter(),
                "search_lines": Counter(),
                "first_exposure": None,
                "cited_bundles": set(),
            }
        return files[path]

    queries, sequence, unknown_tools = [], [], []
    for tool in trace["tools"]:
        n, function = tool["ordinal"], tool["function"]
        exposed: dict[str, list[int]] = defaultdict(list)
        if not tool["failed"]:
            if function == "read_file" and tool.get("path"):
                f = file(tool["path"])
                f["read_calls"] += 1
                f["read_lines"].update(read_lines(tool["result"]).keys())
                exposed[f["path"]] = list(read_lines(tool["result"]))
            elif function == "search":
                hits = search_lines(tool["result"], root)
                for hit in hits:
                    file(hit["path"])["search_lines"][hit["line"]] += 1
                    exposed[hit["path"]].append(hit["line"])
                queries.append(
                    {
                        "tool": n,
                        "query": tool["arguments"].get("pattern"),
                        "directory": tool["arguments"].get("directory", "."),
                        "hits": len(hits),
                        "files": len(exposed),
                    }
                )
            elif function == "list_files":
                for line in tool["result"].splitlines():
                    match = re.match(r"^(.+?)\s+\([\d,]+ bytes\)$", line)
                    if match:
                        file(normalize_path(match[1], root))["listed"] = True
            else:
                unknown_tools.append(n)
        for path, nums in exposed.items():
            if nums and file(path)["first_exposure"] is None:
                file(path)["first_exposure"] = n
        sequence.append(
            {
                "tool": n,
                "function": function,
                "failed": tool["failed"],
                "exposed": dict(exposed),
                "query": tool["arguments"].get("pattern"),
            }
        )

    enriched = []
    evidence_categories = Counter()
    for bundle in bundles:
        excerpts = []
        for excerpt in bundle.get("excerpts", []):
            path = normalize_path(excerpt.get("path", ""), root)
            file(path)["cited_bundles"].add(bundle["number"])
            evidence_categories[file(path)["role"]] += len(
                excerpt.get("text", "").splitlines()
            )
            excerpts.append(
                {
                    **excerpt,
                    "path": path,
                    "fidelity": verify_excerpt(excerpt, trace["tools"], root),
                }
            )
        enriched.append({**bundle, "excerpts": excerpts})

    visible, read_total, read_unique = 0, 0, 0
    categories = Counter()
    for f in files.values():
        reads, hits = f.pop("read_lines"), f.pop("search_lines")
        distinct = set(reads) | set(hits)
        f["read_presentations"] = sum(reads.values())
        f["unique_read_lines"] = len(reads)
        f["search_presentations"] = sum(hits.values())
        f["unique_search_lines"] = len(hits)
        f["unique_exposed_lines"] = len(distinct)
        f["read_line_numbers"] = sorted(reads)
        f["search_line_numbers"] = sorted(hits)
        f["cited_bundles"] = sorted(f["cited_bundles"])
        bins = Counter()
        for number, count in reads.items():
            bins[((number - 1) // 50) * 50 + 1] += count
        f["bins"] = [
            {"start": n, "end": n + 49, "reads": bins[n]} for n in sorted(bins)
        ]
        categories[f["role"]] += len(distinct)
        visible += len(distinct)
        read_total += f["read_presentations"]
        read_unique += len(reads)
    ordered = sorted(
        files.values(), key=lambda f: (-f["read_presentations"], f["path"])
    )

    targets = []
    normalized = (context or {}).get("normalized_location") or {}
    for loc in normalized.get("sabotage_locations", []):
        path = normalize_path(loc["path"], root)
        f = files.get(path)
        ranges = loc.get("line_ranges") or []
        target_lines = {n for r in ranges for n in range(r["start"], r["end"] + 1)}
        cited = []
        for bundle in enriched:
            for e in bundle["excerpts"]:
                if e["path"] == path:
                    cited.append(bundle["number"])
        targets.append(
            {
                "path": path,
                "line_hints": ranges,
                "hint_status": normalized.get("normalization_status"),
                "listed": bool(f and f["listed"]),
                "read": bool(f and f["unique_read_lines"]),
                "search_hit": bool(f and f["unique_search_lines"]),
                "first_exposure": f["first_exposure"] if f else None,
                "first_hint_exposure": next(
                    (
                        step["tool"]
                        for step in sequence
                        if set(step["exposed"].get(path, [])) & target_lines
                    ),
                    None,
                ),
                "cited_bundles": sorted(set(cited)),
                "hint_lines_exposed": sorted(
                    target_lines
                    & set(
                        (f["read_line_numbers"] + f["search_line_numbers"]) if f else []
                    )
                ),
            }
        )
    themes = {
        name: sum(
            len(re.findall(pattern, b["text"], re.I))
            for b in trace["readable_reasoning"]
        )
        for name, pattern in THEMES.items()
    }
    return {
        "files": ordered,
        "bundles": enriched,
        "queries": queries,
        "sequence": sequence,
        "targets": targets,
        "categories": dict(categories),
        "evidence_categories": dict(evidence_categories),
        "tool_calls": len(trace["tools"]),
        "model_calls": trace["model_calls"],
        "read_presentations": read_total,
        "unique_read_lines": read_unique,
        "unique_exposed_lines": visible,
        "repeat_read_share": (read_total - read_unique) / read_total
        if read_total
        else None,
        "top_three_read_share": sum(f["read_presentations"] for f in ordered[:3])
        / read_total
        if read_total
        else None,
        "flow": {
            "listed_files": sum(f["listed"] for f in ordered),
            "read_files": sum(f["unique_read_lines"] > 0 for f in ordered),
            "search_files": sum(f["unique_search_lines"] > 0 for f in ordered),
            "exposed_files": sum(f["unique_exposed_lines"] > 0 for f in ordered),
            "cited_files": sum(bool(f["cited_bundles"]) for f in ordered),
            "exposed_and_cited": sum(
                bool(f["cited_bundles"]) and f["unique_exposed_lines"] > 0
                for f in ordered
            ),
        },
        "reasoning": {
            "readable_blocks": len(trace["readable_reasoning"]),
            "redacted_blocks": trace["redacted_blocks"],
            "themes": themes,
        },
        "unattributed_tools": unknown_tools,
        "failed_tools": [t["ordinal"] for t in trace["tools"] if t["failed"]],
        "truncated_tools": [
            t["ordinal"]
            for t in trace["tools"]
            if t["truncated"]
            or "line truncated" in t["result"]
            or "stopped at 100 matches" in t["result"]
        ],
    }


def git_state(repo: Path) -> dict:
    def run(*args: str) -> str | None:
        p = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True
        )
        return p.stdout.strip() if p.returncode == 0 else None

    return {"commit": run("rev-parse", "HEAD"), "status": run("status", "--short")}
