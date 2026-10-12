"""Resolve post-hoc sabotage references without exposing them to participants."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from debate_asb.datasets.asb import PROJECT_ROOT, RUBRIC_DIRS
from debate_asb.sabotage import normalized_locations, resolve_sabotage

RUBRIC_WARNING = (
    "The versioned sabotage reference supplies checked code mechanisms and explicit "
    "uncertainty. Original rubric and logged claims are retained separately. Current "
    "source checks do not prove historical exposure or empirical impact; coverage "
    "requires reviewing this run's returned evidence."
)


def load_rubric_context(
    sample_id: str,
    target: str | None,
    logged_answer_key: dict[str, Any] | None,
    workspace: str | None = None,
) -> dict[str, Any] | None:
    """Prefer the exact registry record; never silently replace it with rubric hints."""
    reference = resolve_sabotage(sample_id, target, workspace)
    record = reference["record"]
    found = _find_entry(sample_id)
    if found is None and record is None:
        return None
    if found:
        label, entry, rubric, rubric_path = found
        current_sabotage = entry.get("sabotage")
        rubric_file = _relative(rubric_path)
    else:
        assert record is not None
        label, entry, rubric = record["label"], record, {}
        current_sabotage = record["rubric"]["sabotage"]
        rubric_file = record["rubric"]["path"]
    context: dict[str, Any] = {
        "warning": RUBRIC_WARNING,
        "label": target or label,
        "paper_name": rubric.get("paper_name") or entry.get("paper_name"),
        "code_name": entry.get("code_name"),
        "workspace": entry.get("workspace"),
        "red_team_constraints": rubric.get("red_team_constraints"),
        "sabotage": None,
        "sabotage_source": "unverified; see original annotations",
        "sabotage_reference": reference,
        "logged_sabotage": logged_answer_key,
        "current_sabotage": current_sabotage,
        "acceptable_fixes": (entry.get("rubric") or {}).get("acceptable_fixes", []),
        "tags": entry.get("tags") or {},
        "rubric_file": rubric_file,
    }
    if reference["usable"] and record and record["mechanism"]:
        context["sabotage"] = {
            "description": record["mechanism"],
            "limitations": record["limitations"],
            "location": record["locations"],
        }
        context["sabotage_source"] = "versioned sabotage registry"
    normalized = normalized_locations(reference)
    if normalized:
        context["normalized_location"] = normalized
    return context


def _find_entry(
    sample_id: str,
) -> tuple[str, dict[str, Any], dict[str, Any], Path] | None:
    for rubric_dir in RUBRIC_DIRS:
        for rubric_path in sorted(rubric_dir.glob("*.yaml")):
            if rubric_path.name.startswith("_"):
                continue
            rubric = yaml.safe_load(rubric_path.read_text()) or {}
            for label in ("sabotaged", "honest"):
                listed = rubric.get(label) or []
                entries = listed if isinstance(listed, list) else [listed]
                for entry in entries:
                    if entry.get("id") == sample_id:
                        return label, entry, rubric, rubric_path
    return None


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)
