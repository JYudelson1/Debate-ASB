"""Validate post-hoc repair examples without modifying or importing ASB code."""

from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any


def _relative(path: Any) -> bool:
    return (
        isinstance(path, str)
        and bool(path)
        and not PurePosixPath(path).is_absolute()
        and ".." not in PurePosixPath(path).parts
    )


def _hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def validate_fix_records(document: dict) -> None:
    """Check schema and identity, including all-or-nothing repair units."""
    policy = document.get("fix_policy")
    if policy is None:
        if document["registry_version"] >= 3:
            raise ValueError("Missing fix policy for repair-enabled registry")
        return  # Earlier versioned snapshots did not contain repair references.
    scope = policy["scope"]
    if len(scope) != len(set(scope)) or set(scope) != {
        sid for sid, e in document["samples"].items() if "acceptable_fixes" in e
    }:
        raise ValueError("Fix policy scope differs from reviewed fix entries")
    for sid in scope:
        entry = document["samples"][sid]
        fixes = entry["acceptable_fixes"]
        review = entry.get("fix_review", {})
        if (
            entry["label"] != "sabotaged"
            or not 1 <= len(fixes) <= 3
            or len({f["id"] for f in fixes}) != len(fixes)
            or review.get("source_commit") != document["source_commit"]
            or not review.get("reviewed_at")
            or not review.get("method")
            or not review.get("reviewer")
            or review.get("execution_scope") != "core_experiment"
            or not review.get("execution_sources")
        ):
            raise ValueError(f"Invalid fix review: {sid}")
        for evidence in review["execution_sources"]:
            if not _relative(evidence["path"]) or not _hash(evidence["sha256"]):
                raise ValueError(f"Invalid execution source: {sid}")
        if not any(f["kind"] == "honest_reversion" for f in fixes):
            raise ValueError(f"Missing honest mechanism reversion: {sid}")
        for fix in fixes:
            if (
                fix["kind"] not in {"honest_reversion", "equivalent_repair"}
                or not fix.get("title")
                or not fix.get("requirements")
                or not all(isinstance(s, str) and s for s in fix["requirements"])
                or not fix.get("changes")
                or len({c["path"] for c in fix["changes"]}) != len(fix["changes"])
            ):
                raise ValueError(f"Invalid complete fix: {sid}/{fix['id']}")
            for change in fix["changes"]:
                honest = change["honest_reference"]
                if (
                    not _relative(change["path"])
                    or not _relative(honest["path"])
                    or not _hash(change["source_sha256"])
                    or not _hash(change["result_sha256"])
                    or not _hash(honest["sha256"])
                    or honest["sample_id"] != review["honest_sample_id"]
                    or document["samples"][honest["sample_id"]]["label"] != "honest"
                ):
                    raise ValueError(f"Invalid fix source: {sid}/{change['path']}")
                if change["operation"] == "replace_artifact":
                    if change["result_sha256"] != honest["sha256"]:
                        raise ValueError(
                            f"Artifact does not match honest source: {sid}"
                        )
                elif change["operation"] == "edit_text":
                    previous_end = 0
                    if not change.get("edits"):
                        raise ValueError(f"Empty fix edits: {sid}")
                    for edit in change["edits"]:
                        start, end = edit["start_line"], edit["end_line"]
                        if (
                            type(start) is not int
                            or type(end) is not int
                            or start < 1
                            or end < start - 1
                            or start <= previous_end
                            or not isinstance(edit["before"], str)
                            or not isinstance(edit["after"], str)
                            or edit["before"] == edit["after"]
                            or len(edit["before"].splitlines()) != end - start + 1
                        ):
                            raise ValueError(f"Invalid original-line fix range: {sid}")
                        previous_end = max(start - 1, end)
                else:
                    raise ValueError(f"Invalid fix operation: {sid}")


def apply_text_edits(source: str, change: dict) -> str:
    """Apply saved, original-line edits in memory; reject drift and bad examples."""
    if hashlib.sha256(source.encode()).hexdigest() != change["source_sha256"]:
        raise ValueError("Fix source hash differs")
    lines = source.splitlines(keepends=True)
    result: list[str] = []
    cursor = 0
    for edit in change["edits"]:
        start, end = edit["start_line"] - 1, edit["end_line"]
        if not cursor <= start <= end <= len(lines):
            raise ValueError("Fix edits overlap or extend outside the source")
        if "".join(lines[start:end]) != edit["before"]:
            raise ValueError("Fix before-text differs from source range")
        result.extend(lines[cursor:start])
        result.append(edit["after"])
        cursor = end
    result.extend(lines[cursor:])
    text = "".join(result)
    if hashlib.sha256(text.encode()).hexdigest() != change["result_sha256"]:
        raise ValueError("Fix result hash differs")
    return text


def resolve_fixes(sid: str, document: dict, repo: Path) -> dict:
    """Check fix-specific sources separately from the older location reference."""
    entry = document["samples"].get(sid, {})
    if not entry.get("acceptable_fixes"):
        return {"status": "not_reviewed", "usable": False, "issues": []}
    root = repo / "data/asb"
    if not root.exists():
        return {"status": "checkout_unavailable", "usable": True, "issues": []}
    issues = []
    for evidence in entry["fix_review"]["execution_sources"]:
        path = root / entry["workspace"] / evidence["path"]
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != evidence["sha256"]
        ):
            issues.append(
                f"changed or missing execution source: {path.relative_to(root)}"
            )
    for fix in entry["acceptable_fixes"]:
        for change in fix["changes"]:
            path = root / entry["workspace"] / change["path"]
            honest = change["honest_reference"]
            paired = (
                root
                / document["samples"][honest["sample_id"]]["workspace"]
                / honest["path"]
            )
            for p, expected in (
                (path, change["source_sha256"]),
                (paired, honest["sha256"]),
            ):
                if (
                    not p.is_file()
                    or hashlib.sha256(p.read_bytes()).hexdigest() != expected
                ):
                    issues.append(
                        f"changed or missing fix source: {p.relative_to(root)}"
                    )
            if path.is_file() and change["operation"] == "edit_text":
                try:
                    patched = apply_text_edits(path.read_text(), change)
                    if path.suffix == ".py":
                        ast.parse(patched)
                except (ValueError, SyntaxError) as exc:
                    issues.append(f"invalid fix example: {change['path']}: {exc}")
    return {
        "status": "source_drift" if issues else "matched",
        "usable": not issues,
        "issues": sorted(set(issues)),
    }


def main() -> int:
    from .datasets.asb import PROJECT_ROOT
    from .sabotage import read_registry

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate"])
    parser.add_argument("--repo", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    document = read_registry()
    failed = False
    for sid in document["fix_policy"]["scope"]:
        result = resolve_fixes(sid, document, args.repo)
        print(
            f"{sid}: {result['status']} ({len(document['samples'][sid]['acceptable_fixes'])} fixes)"
        )
        for issue in result["issues"]:
            print(f"  {issue}")
        failed |= result["status"] != "matched"
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
