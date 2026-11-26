"""Reviewed corrections to ASB answer keys, applied without editing ASB data."""

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

CORRECTIONS_DIR = Path(__file__).with_name("asb_rubric_corrections")


def entry_digest(entry: dict[str, Any]) -> str:
    """Hash the parsed upstream entry, independent of YAML formatting."""
    return hashlib.sha256(
        json.dumps(entry, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def corrected_entry(
    sample_id: str, entry: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Apply an exact-entry override and retain its original annotation for logs."""
    path = CORRECTIONS_DIR / f"{sample_id}.json"
    if not path.is_file():
        return entry, None
    raw = path.read_bytes()
    correction = json.loads(raw)
    if (
        correction["version"] != 1
        or correction["sample_id"] != sample_id
        or set(correction["updates"]) - {"sabotage", "rubric"}
    ):
        raise ValueError(f"Invalid ASB rubric correction: {sample_id}")
    if correction["source_entry_sha256"] != entry_digest(entry):
        raise ValueError(f"Upstream ASB rubric changed; re-review {sample_id}")
    result = copy.deepcopy(entry)
    for section, fields in correction["updates"].items():
        result[section] = {**result.get(section, {}), **copy.deepcopy(fields)}
    provenance = {
        "version": correction["version"],
        "reason": correction["reason"],
        "source_commit": correction["source_commit"],
        "source_entry_sha256": correction["source_entry_sha256"],
        "correction_sha256": hashlib.sha256(raw).hexdigest(),
        "original_sabotage": copy.deepcopy(entry.get("sabotage")),
        "original_rubric": copy.deepcopy(entry.get("rubric")),
    }
    return result, provenance
