"""Observed browsing coverage; returned contents are distinct from discovery.

This module does not instrument or steer an extractor. Denominators describe the
visible artifact snapshot; numerators use only successful executed tool results.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any

from debate_asb.artifacts.codebase import HIDDEN_NAMES, Codebase

SOURCE_EXTENSIONS = {
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".sh",
    ".bash",
    ".java",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".rs",
    ".go",
    ".r",
    ".jl",
    ".lua",
    ".m",
    ".sql",
    ".ipynb",
}
CONFIG_EXTENSIONS = {".yaml", ".yml", ".toml", ".cfg", ".ini", ".properties"}
BINARY_EXTENSIONS = {
    ".pt",
    ".pth",
    ".pkl",
    ".pickle",
    ".npy",
    ".npz",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".bz2",
    ".xz",
    ".tgz",
    ".parquet",
    ".feather",
    ".arrow",
    ".eval",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".bin",
    ".safetensors",
}
ROLES = ("source", "configuration", "paper_docs", "datasets", "logs_results", "other")
NUMBERED = re.compile(r"^\s*(\d+) {2}(.*)$")
LISTED = re.compile(r"^(.+?)  \(([\d,]+) bytes\)$")
SEARCH_HIT = re.compile(r"^(.+?):(\d+): (.*)$")
TRUNCATED = re.compile(r" \[\.\.\. line truncated, ([\d,]+) chars\]$")


def artifact_role(path: str) -> str:
    """Saved filename/path heuristics, not an inference about scientific relevance."""
    p = Path(path)
    parts = {part.lower() for part in p.parts[:-1]}
    name, suffix = p.name.lower(), p.suffix.lower()
    if suffix in SOURCE_EXTENSIONS:
        return "source"
    if (
        name == "paper.md"
        or suffix in {".md", ".rst", ".pdf"}
        or name.startswith("readme")
        or parts & {"docs", "papers", "figures"}
    ):
        return "paper_docs"
    if (
        suffix in CONFIG_EXTENSIONS
        or parts & {"configs", "config", "cfgs"}
        or "config" in p.stem.lower()
        or suffix == ".lock"
        or name in {"dockerfile", "requirements.txt"}
    ):
        return "configuration"
    if (
        parts
        & {
            "logs",
            "results",
            "outputs",
            "runs",
            "completed_ids",
            "checkpoints",
            "result_figures",
        }
        or "results" in p.stem.lower()
    ):
        return "logs_results"
    if parts & {
        "data",
        "dataset",
        "datasets",
        "generations",
        "cached_activations",
    } or suffix in {
        ".csv",
        ".tsv",
        ".jsonl",
        ".parquet",
        ".pt",
        ".pth",
        ".npy",
        ".npz",
    }:
        return "datasets"
    return "other"


def build_inventory(root: str | Path) -> dict[str, Any]:
    """Hash and count every visible file; never decode opaque/binary formats."""
    codebase = Codebase(root)
    files = {}
    for file in codebase._files(codebase.root):
        path = str(file.relative_to(codebase.root))
        if not file.resolve().is_relative_to(codebase.root):
            files[path] = {
                "role": artifact_role(path),
                "binary": True,
                "readable": False,
                "bytes": file.stat().st_size,
                "lines": None,
                "characters": None,
                "sha256": None,
                "limitation": "Visible symlink cannot be read through the artifact tool.",
            }
            continue
        contents = file.read_bytes()
        binary = b"\0" in contents[:8192] or file.suffix.lower() in BINARY_EXTENSIONS
        if binary:
            line_count = character_count = None
        else:
            # Same logical lines as Codebase.read_file, including empty lines.
            lines = contents.decode(errors="replace").splitlines()
            line_count = len(lines)
            character_count = sum(len(line) for line in lines)
        files[path] = {
            "role": artifact_role(path),
            "binary": binary,
            "readable": not binary,
            "bytes": len(contents),
            "lines": line_count,
            "characters": character_count,
            "sha256": hashlib.sha256(contents).hexdigest(),
        }
    return {
        "root": str(codebase.root),
        "files": files,
        "roles": list(ROLES),
        "line_definition": "Python splitlines, as used by the read_file tool; empty lines included.",
        "character_definition": "Decoded text characters excluding line separators; only returned prefixes receive credit.",
        "binary_definition": "NUL in first 8192 bytes or known opaque format. Discovery is measurable; decoded content coverage is unknown.",
        "role_definition": "Filename/path heuristics saved per file. Notebook source counts use the visible notebook text representation.",
    }


def _normalise(path: str, root: str) -> str | None:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(root)
        except ValueError:
            return None
    if ".." in candidate.parts or HIDDEN_NAMES & set(candidate.parts):
        return None
    return str(candidate)


def _returned_prefix(text: str) -> tuple[int, bool]:
    marker = TRUNCATED.search(text)
    if (
        marker
        and len(text[: marker.start()]) == 2000
        and int(marker[1].replace(",", "")) > 2000
    ):
        return 2000, True
    return len(text), False


def observed_coverage(
    inventory: dict[str, Any], tools: list[dict[str, Any]]
) -> dict[str, Any]:
    """Cumulative exposure in executed tool-call order, with four saved cutoffs.

    Tool records need function, arguments, result, failed; generator_turn is an
    optional grouping supplied from the model/tool event ancestry. Search scans
    internally are never credited: only their actual returned hit prefixes count.
    """
    files, root = inventory["files"], inventory["root"]
    discovered: set[str] = set()
    reads: dict[tuple[str, int], int] = {}
    hits: dict[tuple[str, int], int] = {}
    truncated: set[tuple[str, int]] = set()
    history = []

    def accept(path: str | None, number: int, characters: int, table: dict) -> None:
        if path is None or path not in files:
            return
        discovered.add(path)
        if not files[path]["readable"]:
            return
        if not 1 <= number <= files[path]["lines"]:
            return
        table[(path, number)] = max(characters, table.get((path, number), 0))

    def snapshot() -> dict[str, Any]:
        combined = dict(reads)
        for pair, length in hits.items():
            combined[pair] = max(length, combined.get(pair, 0))
        groups = {}
        for role in [*ROLES, "all", "binary", "paper"]:
            selected = {
                p
                for p, f in files.items()
                if role == "all"
                or (
                    f["binary"]
                    if role == "binary"
                    else Path(p).name.lower() == "paper.md"
                    if role == "paper"
                    else f["role"] == role
                )
            }
            readable = {p for p in selected if files[p]["readable"]}
            line_denominator = sum(files[p]["lines"] for p in readable)
            character_denominator = sum(files[p]["characters"] for p in readable)
            read_pairs = {p: v for p, v in reads.items() if p[0] in readable}
            combined_pairs = {p: v for p, v in combined.items() if p[0] in readable}
            groups[role] = {
                "files": len(selected),
                "text_files": len(readable),
                "binary_files": sum(files[p]["binary"] for p in selected),
                "bytes": sum(files[p]["bytes"] for p in selected),
                "text_lines": line_denominator,
                "text_characters": character_denominator,
                "discovered_files": len(selected & discovered),
                "read_files": len({p for p, _ in read_pairs}),
                "read_lines": len(read_pairs),
                "read_characters": sum(read_pairs.values()),
                "read_plus_search_files": len({p for p, _ in combined_pairs}),
                "read_plus_search_lines": len(combined_pairs),
                "read_plus_search_characters": sum(combined_pairs.values()),
                "truncated_read_lines": sum(p[0] in selected for p in truncated),
                "discovered_file_fraction": len(selected & discovered) / len(selected)
                if selected
                else None,
                "read_line_fraction": len(read_pairs) / line_denominator
                if line_denominator
                else None,
                "read_character_fraction": sum(read_pairs.values())
                / character_denominator
                if character_denominator
                else None,
                "read_plus_search_line_fraction": len(combined_pairs) / line_denominator
                if line_denominator
                else None,
                "read_plus_search_character_fraction": sum(combined_pairs.values())
                / character_denominator
                if character_denominator
                else None,
                "binary_content_fraction": None,
            }
        return groups

    for index, tool in enumerate(tools, 1):
        if not tool.get("failed"):
            function, arguments = tool["function"], tool.get("arguments", {})
            result = str(tool.get("result", ""))
            if function == "list_files":
                for line in result.splitlines():
                    match = LISTED.match(line)
                    if match:
                        path = _normalise(match[1], root)
                        if path is not None and path in files:
                            discovered.add(path)
            elif function == "read_file":
                path = _normalise(arguments.get("path", tool.get("path", "")), root)
                if (
                    path is not None
                    and path in files
                    and ("is a binary file (" in result or "showed lines " in result)
                ):
                    discovered.add(path)
                for line in result.splitlines():
                    match = NUMBERED.match(line)
                    if match:
                        length, was_truncated = _returned_prefix(match[2])
                        accept(path, int(match[1]), length, reads)
                        if (
                            path is not None
                            and was_truncated
                            and (path, int(match[1])) in reads
                        ):
                            truncated.add((path, int(match[1])))
            elif function == "search":
                for line in result.splitlines():
                    match = SEARCH_HIT.match(line)
                    if match:
                        accept(
                            _normalise(match[1], root),
                            int(match[2]),
                            len(match[3]),
                            hits,
                        )
        history.append(
            {
                "tool_calls_completed": index,
                "generator_turn": tool.get("generator_turn"),
                "by_role": snapshot(),
            }
        )
    empty = {"tool_calls_completed": 0, "generator_turn": None, "by_role": snapshot()}
    quartiles = []
    for fraction in (0.25, 0.5, 0.75, 1.0):
        cutoff = math.ceil(len(tools) * fraction)
        point = history[cutoff - 1] if cutoff else empty
        quartiles.append({"quartile": int(fraction * 100), **point})
    return {
        "tool_calls": len(tools),
        "quartiles": quartiles,
        "trajectory": history,
        "final": (history[-1] if history else empty)["by_role"],
        "quartile_definition": "Cumulative completed executed tool calls at ceil(N*q), where q is .25/.5/.75/1. Counts are shown; batched calls have an imposed log order.",
        "limitations": [
            "Exposure does not establish understanding, relevance, or verification.",
            "Search-only snippets are reported separately; internal search traversal is not content exposure.",
            "Binary discovery does not establish content inspection.",
        ],
    }
