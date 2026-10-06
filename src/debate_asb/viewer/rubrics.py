"""Load post-hoc ASB rubric context without exposing it to participants."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from debate_asb.datasets.asb import PROJECT_ROOT, RUBRIC_DIRS

RUBRIC_WARNING = (
    "Rubric context is an orientation aid, not verified ground truth. File paths "
    "and line numbers may be stale or refer to a different codebase revision. "
    "Do not treat this panel as extraction scoring."
)


def load_rubric_context(
    sample_id: str,
    target: str | None,
    logged_answer_key: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the rubric entry and normalized location hints for one sample."""
    found = _find_entry(sample_id)
    if found is None:
        return None

    label, entry, rubric, rubric_path = found
    current_sabotage = entry.get("sabotage")
    sabotage = logged_answer_key or current_sabotage
    context = {
        "warning": RUBRIC_WARNING,
        "label": target or label,
        "paper_name": rubric.get("paper_name"),
        "code_name": entry.get("code_name"),
        "workspace": entry.get("workspace"),
        "red_team_constraints": rubric.get("red_team_constraints"),
        "sabotage": sabotage,
        "sabotage_source": (
            "eval sample metadata" if logged_answer_key else "current rubric"
        ),
        "acceptable_fixes": (entry.get("rubric") or {}).get("acceptable_fixes", []),
        "tags": entry.get("tags") or {},
        "rubric_file": _relative(rubric_path),
    }
    normalized = _normalized_location(sample_id, rubric_path.parent)
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


def _normalized_location(sample_id: str, rubric_dir: Path) -> dict[str, Any] | None:
    path = rubric_dir / "_sabotage_locations.yaml"
    if not path.exists():
        return None
    document = yaml.safe_load(path.read_text()) or {}
    location = (document.get("samples") or {}).get(sample_id)
    if not location:
        return None
    return {
        **location,
        "source_commit": document.get("source_commit"),
        "source_file": _relative(path),
    }


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)
