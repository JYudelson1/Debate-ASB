"""Descriptive cross-run metrics over saved observations and explicit audits."""

import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from debate_asb.extraction_analysis.common import sha
from debate_asb.extraction_analysis.traces import normalise, source_blocks

SEARCH = re.compile(r"^(.+?):(\d+): ?(.*)$")
GROUPS = ("target", "known", "paper", "other")


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    xm, ym = mean(xs), mean(ys)
    xx = sum((x - xm) ** 2 for x in xs)
    yy = sum((y - ym) ** 2 for y in ys)
    return (
        sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / math.sqrt(xx * yy)
        if xx > 1e-20 and yy > 1e-20
        else None
    )


def association(rows: list[dict], metric: str, strata: tuple[str, ...] = ()) -> dict:
    """Point-biserial r, or correlation after within-stratum demeaning.

    No p values: these repeated descriptive observations are not independent.
    Constant-outcome strata have no within-group outcome information.
    """
    selected = [
        r
        for r in rows
        if r.get(metric) is not None and r["eligible"] and r["trace_complete"]
    ]
    groups = defaultdict(list)
    for row in selected:
        groups[tuple(row[k] for k in strata)].append(row)
    xs, ys = [], []
    informative = 0
    for group in groups.values():
        x = [r[metric] for r in group]
        y = [int(r["captured"]) for r in group]
        if strata:
            if len(set(y)) > 1 and max(x) != min(x):
                informative += 1
            # Retain constant-y strata: their x variation remains in the
            # residual correlation denominator (standard within estimator).
            xs.extend(v - mean(x) for v in x)
            ys.extend(v - mean(y) for v in y)
        else:
            xs.extend(x)
            ys.extend(y)
    return {
        "r": pearson(xs, ys),
        "n": len(xs),
        "strata": list(strata),
        "groups": len(groups),
        "informative_groups": informative,
    }


def catalogue_lines(sample: dict, inventory: dict) -> dict[tuple[str, int], list[str]]:
    """Conservative literal catalog signatures; file/range hints are excluded."""
    catalog = (sample.get("rubric") or {}).get("known_issue_catalog") or {}
    root = Path(inventory["root"])
    result = defaultdict(list)
    for entry in catalog.get("entries", []):
        path = re.sub(r":\d[\d,\- ]*$", "", entry.get("file", ""))
        file = root / path
        saved_text = inventory["files"].get(path, {}).get("snapshot_text")
        if not entry.get("signatures") or (saved_text is None and not file.is_file()):
            continue
        if saved_text is None:
            if sha(file) != inventory["files"][path].get("sha256"):
                raise ValueError(
                    "Reference file changed since inventory snapshot: " + path
                )
            saved_text = file.read_text(errors="replace")
        text_lines = saved_text.splitlines()
        for signature in entry["signatures"]:
            # Every regex in a signature must match the file; each matched line
            # is a marker. This is content attribution, not endorsement of impact.
            if not all(
                re.search(pattern, "\n".join(text_lines), re.M) for pattern in signature
            ):
                continue
            for n, line in enumerate(text_lines, 1):
                if any(re.search(pattern, line) for pattern in signature):
                    result[(path, n)].append(entry["id"])
    return dict(result)


def measure(
    sample: dict,
    tools: list[dict],
    inventory: dict,
    regions: list[dict],
    known: dict,
    owners: dict,
) -> dict:
    files = inventory["files"]
    target = {(r["path"], n) for r in regions for n in range(r["start"], r["end"] + 1)}
    read = Counter()
    search = Counter()
    by_owner = defaultdict(Counter)
    line_groups = Counter()
    event_target = []
    full_region_events = []
    mismatches = Counter()
    source_cache = {}
    verified_pairs = set()
    first_target = None
    timeline = []
    file_counts = Counter()
    region_coverage = set()
    for i, tool in enumerate(tools):
        pairs = set()
        owner = owners.get(str(tool["ordinal"]), "single")
        for path, lines in source_blocks(tool, sample["codebase_root"]):
            for number, text in lines.items():
                pair = (path, number)
                if (
                    path in files
                    and files[path]["readable"]
                    and 1 <= number <= (files[path]["lines"] or 0)
                ):
                    if path not in source_cache:
                        saved_text = files[path].get("snapshot_text")
                        if saved_text is None:
                            # Only source/configuration denominators need a
                            # frozen content check. Non-code artifacts remain
                            # measured presentations without reference credit.
                            if files[path]["role"] not in {
                                "source",
                                "configuration",
                                "paper_docs",
                            }:
                                saved_text = ""
                            else:
                                file = Path(inventory["root"]) / path
                                if (
                                    files[path].get("sha256")
                                    and sha(file) != files[path]["sha256"]
                                ):
                                    raise ValueError(
                                        "Reference file changed since inventory snapshot: "
                                        + path
                                    )
                                saved_text = file.read_text(errors="replace")
                        source_cache[path] = saved_text.splitlines()
                    current = (
                        source_cache[path][number - 1]
                        if number <= len(source_cache[path])
                        else None
                    )
                    # Truncated line prefixes count as presented lines; text
                    # checks use the actual retained prefix only.
                    prefix = re.sub(
                        r" \[\.\.\. line truncated, [\d,]+ chars\]$", "", text
                    )
                    if current is not None and not current.startswith(prefix):
                        mismatches[path] += 1
                    elif current is not None:
                        verified_pairs.add(pair)
                read[pair] += 1
                by_owner[owner][pair] += 1
                file_counts[path] += 1
                pairs.add(pair)
                group = (
                    "target"
                    if pair in target
                    else "known"
                    if pair in known
                    else "paper"
                    if Path(path).name.lower() == "paper.md"
                    else "other"
                )
                line_groups[group] += 1
        if pairs & target:
            event_target.append(tool["ordinal"])
            first_target = (
                first_target if first_target is not None else i / max(1, len(tools))
            )
        if target and target <= pairs:
            full_region_events.append(tool["ordinal"])
        region_coverage.update(pairs & target)
        if not tool.get("failed") and tool["function"] == "search":
            for line in str(tool.get("result", "")).splitlines():
                if m := SEARCH.match(line):
                    search[(normalise(m[1], sample["codebase_root"]), int(m[2]))] += 1
        if i + 1 in {math.ceil(len(tools) * q) for q in (0.25, 0.5, 0.75, 1)}:
            timeline.append(
                {
                    "calls": i + 1,
                    "distinct_lines": len(read),
                    "target_distinct_lines": len(region_coverage),
                }
            )
    code_paths = {
        p
        for p, f in files.items()
        if f["role"] in {"source", "configuration"} and f["readable"]
    }
    denominator = sum(files[p]["lines"] for p in code_paths)
    source_denominator = sum(
        f["lines"] for f in files.values() if f["role"] == "source" and f["readable"]
    )
    valid = lambda pair: (
        pair[0] in code_paths and 1 <= pair[1] <= files[pair[0]]["lines"]
    )
    read_code = {p for p in read if valid(p)}
    combined_code = {p for p in set(read) | set(search) if valid(p)}
    target_presentations = sum(n for pair, n in read.items() if pair in target)
    same_agent_repeats = sum(
        sum(max(0, n - 1) for p, n in counter.items() if p in target)
        for counter in by_owner.values()
    )
    target_owner_pairs = sum(
        sum(p in target for p in counter) for counter in by_owner.values()
    )
    known_presentations = sum(
        n for pair, n in read.items() if pair in known and pair not in target
    )
    repeat = sum(read.values()) - len(read)
    return {
        "coverage": len(read_code) / denominator if denominator else None,
        "coverage_search": len(combined_code) / denominator if denominator else None,
        "coverage_verified": len(read_code & verified_pairs) / denominator
        if denominator
        else None,
        "source_coverage": sum(
            p[0] in files and files[p[0]]["role"] == "source" for p in read_code
        )
        / source_denominator
        if source_denominator
        else None,
        "file_coverage": len({p[0] for p in read_code}) / len(code_paths)
        if code_paths
        else None,
        "code_lines_read": len(read_code),
        "code_lines_total": denominator,
        "target_read_events": len(event_target),
        "target_read_refs": event_target,
        "target_full_region_events": len(full_region_events),
        "target_line_presentations": target_presentations,
        "target_distinct_lines": len(region_coverage),
        "target_region_lines": len(target),
        "target_region_fraction": len(region_coverage) / len(target)
        if target
        else None,
        "target_repetitions_per_line": target_presentations / len(region_coverage)
        if region_coverage
        else 0,
        "same_agent_target_repeats": same_agent_repeats if owners else None,
        "target_owner_line_pairs": target_owner_pairs if owners else None,
        "target_read_owners": sum(
            any(p in target for p in counter) for counter in by_owner.values()
        )
        if owners
        else None,
        "read_presentations": sum(read.values()),
        "unique_read_lines": len(read),
        "repeat_share": repeat / sum(read.values()) if read else None,
        "target_read_share": target_presentations / sum(read.values())
        if read
        else None,
        "known_read_share": known_presentations / sum(read.values()) if read else None,
        "attention_lines": {g: line_groups[g] for g in GROUPS},
        "first_target_fraction": first_target,
        "source_mismatches": dict(mismatches),
        "timeline": timeline,
        "file_reads": [
            {"path": p, "presentations": n} for p, n in file_counts.most_common(8)
        ],
    }


def capture_summary(rows: list[dict]) -> dict:
    eligible = [r for r in rows if r["eligible"]]
    return {
        "attempts": len(rows),
        "eligible": len(eligible),
        "caught": sum(r["captured"] for r in eligible),
        "original_caught": sum(r["original_included"] == "yes" for r in eligible),
        "missed": sum(not r["captured"] for r in eligible),
        "failed": sum(not r["parsed"] for r in rows),
        "zero_candidates": sum(r.get("no_qualifying_evidence", False) for r in rows),
        "bundles": sum(len(r["bundles"]) for r in rows),
    }


def aggregate(rows: list[dict], variants: list[dict]) -> dict:
    metrics = [
        "coverage",
        "coverage_verified",
        "coverage_search",
        "source_coverage",
        "file_coverage",
        "target_read_events",
        "target_repetitions_per_line",
        "same_agent_target_repeats",
        "target_region_fraction",
        "repeat_share",
        "known_read_share",
    ]
    associations = {
        metric: {
            name: association(rows, metric, strata)
            for name, strata in [
                ("pooled", ()),
                ("within_codebase", ("sample_id",)),
                ("within_variant", ("variant",)),
                ("within_cell", ("sample_id", "variant")),
            ]
        }
        for metric in metrics
    }
    hotspots = []
    for sid in sorted({r["sample_id"] for r in rows}):
        subset = [r for r in rows if r["sample_id"] == sid]
        cells = []
        for variant in variants:
            group = [r for r in subset if r["variant"] == variant["id"]]
            if not group:
                continue
            cell = {
                "variant": variant["id"],
                **capture_summary(group),
                "epochs": [
                    {
                        "epoch": r["epoch"],
                        "captured": r["captured"],
                        "original": r["original_included"],
                        "key": r["row_id"],
                    }
                    for r in group
                ],
            }
            cell["all_caught"] = (
                cell["eligible"] == cell["attempts"]
                and cell["caught"] == cell["attempts"]
            )
            cells.append(cell)
        hotspots.append(
            {
                "sample_id": sid,
                **capture_summary(subset),
                "runs": len(cells),
                "runs_any": sum(c["caught"] > 0 for c in cells),
                "runs_all": sum(c["all_caught"] for c in cells),
                "cells": cells,
            }
        )
    bundles = Counter()
    reads = Counter()
    stages = Counter(r["stage"] for r in rows if r["eligible"] and not r["captured"])
    issues = defaultdict(
        lambda: {"bundles": 0, "miss_bundles": 0, "rows": set(), "codebases": set()}
    )
    for row in rows:
        reads.update(row["attention_lines"])
        for bundle in row["bundles"]:
            bundles[bundle["attention_group"]] += 1
            for match in bundle.get("known_issue_matches", []):
                if match.get("match_kind") != "code_match":
                    continue
                name = match.get("id", match.get("issue_id", "unknown"))
                issue = issues[(row["sample_id"], name)]
                issue["bundles"] += 1
                issue["miss_bundles"] += not row["captured"]
                issue["rows"].add(row["row_id"])
                issue["codebases"].add(row["sample_id"])
    return {
        "overall": capture_summary(rows),
        "associations": associations,
        "hotspots": sorted(
            hotspots,
            key=lambda r: (r["caught"] / max(1, r["eligible"]), r["sample_id"]),
        ),
        "attention_bundles": dict(bundles),
        "attention_reads": dict(reads),
        "failure_stages": dict(stages),
        "known_issues": [
            {
                "sample_id": sid,
                "id": name,
                **{k: len(v) if isinstance(v, set) else v for k, v in item.items()},
            }
            for (sid, name), item in sorted(
                issues.items(), key=lambda x: -x[1]["bundles"]
            )
        ],
    }
