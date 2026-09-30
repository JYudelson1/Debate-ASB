"""Normalize ASB's sabotage locations into a scoring-friendly schema.

The source rubrics remain untouched. One ``_sabotage_locations.yaml`` file is written
beside each rubric collection; the leading underscore also keeps the generated
file out of the dataset loader's rubric scan.

    uv run python -m debate_asb.datasets.sabotage_locations data/asb
"""

from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = 1
OUTPUT_NAME = "_sabotage_locations.yaml"
RUBRIC_DIRS = (
    Path("codebases/_rubrics"),
    Path("red_teaming/codebases/_rubrics"),
)

# These are the file-like locations used by the pinned ASB rubrics. Restricting
# extensions avoids interpreting decimal values and prose punctuation as paths.
FILE_PATTERN = re.compile(
    r"(?<![\w.])"
    r"(?P<path>"
    r"(?:[A-Za-z0-9_.*{}+\-]+/)*"
    r"(?:[A-Za-z0-9_.*{}+\-]+\."
    r"(?:py|md|yaml|yml|jsonl|json|pt|png|pdf|csv)|\*)"
    r")"
    r"(?![\w.])",
    re.IGNORECASE,
)
COLON_LINES = re.compile(
    r"^\s*:\s*(?P<spec>~?\d+(?:\s*-\s*\d+)?"
    r"(?:\s*,\s*~?\d+(?:\s*-\s*\d+)?)*)"
)
NAMED_LINES = re.compile(
    r"(?<!\w)(?P<prefix_approx>~?)lines?\s+"
    r"(?P<spec>~?\d+(?:\s*-\s*\d+)?"
    r"(?:\s*,\s*~?\d+(?:\s*-\s*\d+)?)*)",
    re.IGNORECASE,
)
VAGUE_LOCATION = re.compile(
    r"\bsee diff\b|\bdata files\b|\bresult files\b|\blogs/\*\b|\bgraphs/\b",
    re.IGNORECASE,
)


def _line_ranges(text: str) -> list[dict[str, int | bool]]:
    """Parse a comma-separated line specification such as ``17, 21, ~30-35``."""
    ranges = []
    for item in text.split(","):
        item = item.strip()
        approximate = item.startswith("~")
        item = item.removeprefix("~").strip()
        endpoints = re.split(r"\s*-\s*", item, maxsplit=1)
        start = int(endpoints[0])
        end = int(endpoints[-1])
        ranges.append({"start": start, "end": end, "approximate": approximate})
    return ranges


def _ranges_after_path(text: str) -> list[dict[str, int | bool]]:
    ranges: list[dict[str, int | bool]] = []
    if match := COLON_LINES.match(text):
        ranges.extend(_line_ranges(match.group("spec")))
    for match in NAMED_LINES.finditer(text):
        parsed = _line_ranges(match.group("spec"))
        if match.group("prefix_approx"):
            for line_range in parsed:
                line_range["approximate"] = True
        ranges.extend(parsed)

    # Keep the order from the rubric while removing repeated references.
    seen: set[tuple[int, int, bool]] = set()
    unique = []
    for line_range in ranges:
        key = (
            int(line_range["start"]),
            int(line_range["end"]),
            bool(line_range["approximate"]),
        )
        if key not in seen:
            seen.add(key)
            unique.append(line_range)
    return unique


def _canonical_path(
    raw_path: str, workspace: Path, known_paths: Iterable[str]
) -> tuple[str, str]:
    """Resolve basename-only references where the workspace makes them unique."""
    path = raw_path.removeprefix("./")
    if "*" in path:
        matches = list(workspace.glob(path))
        return path, "pattern_matches" if matches else "missing"
    if (workspace / path).exists():
        return path, "present"

    if "/" not in path:
        known_matches = [known for known in known_paths if Path(known).name == path]
        if len(set(known_matches)) == 1:
            known = known_matches[0]
            return known, "present" if (workspace / known).exists() else "missing"
        disk_matches = [
            candidate for candidate in workspace.rglob(path) if candidate.is_file()
        ]
        if len(disk_matches) == 1:
            return disk_matches[0].relative_to(workspace).as_posix(), "present"
        if len(disk_matches) > 1:
            return path, "ambiguous"
    return path, "missing"


def normalize_location(location: str, workspace: Path) -> dict[str, Any]:
    """Convert one human-authored ``sabotage.location`` string."""
    matches = list(FILE_PATTERN.finditer(location))
    locations_by_path: dict[str, dict[str, Any]] = {}
    review_reasons: list[str] = []

    for index, match in enumerate(matches):
        next_start = (
            matches[index + 1].start() if index + 1 < len(matches) else len(location)
        )
        raw_path = match.group("path")
        path, path_status = _canonical_path(
            raw_path, workspace, locations_by_path.keys()
        )
        ranges = _ranges_after_path(location[match.end() : next_start])
        sabotage_location = locations_by_path.setdefault(
            path, {"path": path, "line_ranges": [], "path_status": path_status}
        )
        if sabotage_location["path_status"] != "present" and path_status == "present":
            sabotage_location["path_status"] = "present"
        for line_range in ranges:
            if line_range not in sabotage_location["line_ranges"]:
                sabotage_location["line_ranges"].append(line_range)

    sabotage_locations = list(locations_by_path.values())
    qualities: set[str] = set()
    for sabotage_location in sabotage_locations:
        ranges = sabotage_location["line_ranges"]
        if not ranges:
            qualities.add("file_only")
            review_reasons.append(f"no line range for {sabotage_location['path']}")
        elif any(line_range["approximate"] for line_range in ranges):
            qualities.add("approximate")
            review_reasons.append(
                f"approximate line range for {sabotage_location['path']}"
            )
        else:
            qualities.add("exact")
        if sabotage_location["path_status"] not in {"present", "pattern_matches"}:
            review_reasons.append(
                f"{sabotage_location['path_status']} workspace path: "
                f"{sabotage_location['path']}"
            )
        elif sabotage_location["path_status"] == "present" and ranges:
            location_file = workspace / sabotage_location["path"]
            line_count = sum(1 for _ in location_file.open(errors="replace"))
            for line_range in ranges:
                start = int(line_range["start"])
                end = int(line_range["end"])
                if end < start:
                    review_reasons.append(
                        f"reversed line range {start}-{end} for "
                        f"{sabotage_location['path']}"
                    )
                elif end > line_count:
                    review_reasons.append(
                        f"line range {start}-{end} exceeds {sabotage_location['path']} "
                        f"({line_count} lines)"
                    )

    if not sabotage_locations:
        status = "unresolved"
        review_reasons.append("no machine-readable file location")
    elif len(qualities) == 1:
        status = next(iter(qualities))
    else:
        status = "mixed"

    if VAGUE_LOCATION.search(location):
        review_reasons.append("source contains a non-specific location")

    # Stable ordering makes the generated artifact deterministic and reviewable.
    review_reasons = list(dict.fromkeys(review_reasons))
    return {
        "source_location": location,
        "normalization_status": status,
        "manual_review_required": bool(review_reasons),
        "manual_review_reasons": review_reasons,
        "sabotage_locations": sabotage_locations,
    }


def _asb_commit(asb_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(asb_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def normalize_rubric_dir(
    asb_root: Path, rubric_dir: Path
) -> tuple[Path, dict[str, Any]]:
    samples: dict[str, Any] = {}
    for rubric_file in sorted(rubric_dir.glob("*.yaml")):
        if rubric_file.name.startswith("_"):
            continue
        rubric = yaml.safe_load(rubric_file.read_text()) or {}
        entries = rubric.get("sabotaged") or []
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            raise ValueError(f"{rubric_file}: 'sabotaged' must be a list or mapping")

        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"{rubric_file}: sabotage entry must be a mapping")
            sample_id = entry.get("id")
            workspace_relative = entry.get("workspace")
            sabotage = entry.get("sabotage") or {}
            location = sabotage.get("location") if isinstance(sabotage, dict) else None
            if not sample_id or not workspace_relative or not isinstance(location, str):
                raise ValueError(
                    f"{rubric_file}: sabotaged entry needs id, workspace, and "
                    "sabotage.location"
                )
            if sample_id in samples:
                raise ValueError(f"duplicate sample id: {sample_id}")

            normalized = normalize_location(location, asb_root / workspace_relative)
            samples[sample_id] = {
                "rubric_file": rubric_file.name,
                "workspace": workspace_relative,
                **normalized,
            }

    document = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": _asb_commit(asb_root),
        "samples": samples,
    }
    output = rubric_dir / OUTPUT_NAME
    output.write_text(yaml.safe_dump(document, sort_keys=False, width=100))
    return output, document


def normalize_asb(asb_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    results = []
    for relative_dir in RUBRIC_DIRS:
        rubric_dir = asb_root / relative_dir
        if rubric_dir.exists():
            results.append(normalize_rubric_dir(asb_root, rubric_dir))
    if not results:
        expected = ", ".join(str(asb_root / path) for path in RUBRIC_DIRS)
        raise FileNotFoundError(f"no ASB rubric directories found; expected {expected}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "asb_root",
        nargs="?",
        type=Path,
        default=Path("data/asb"),
        help="ASB checkout root (default: data/asb)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail if any normalized location still requires manual review",
    )
    args = parser.parse_args()

    review_count = 0
    sample_count = 0
    for output, document in normalize_asb(args.asb_root.resolve()):
        samples = document["samples"]
        needs_review = sum(
            sample["manual_review_required"] for sample in samples.values()
        )
        sample_count += len(samples)
        review_count += needs_review
        print(f"Wrote {output} ({len(samples)} samples; {needs_review} need review)")

    if args.strict and review_count:
        print(
            f"Refusing strict normalization: {review_count}/{sample_count} need review"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
