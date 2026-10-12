"""Versioned post-hoc sabotage reference; never imported by evaluation protocols."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

REGISTRY_PATH = Path(__file__).with_name("sabotage_registry.json")


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _relative(path: str) -> bool:
    p = PurePosixPath(path)
    return bool(path) and not p.is_absolute() and ".." not in p.parts


def read_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    document = json.loads(path.read_text())
    if document.get("schema_version") != 1 or not document.get("source_commit"):
        raise ValueError("Unsupported sabotage registry schema or missing revision")
    for sample_id, entry in document["samples"].items():
        if not _relative(entry["workspace"]) or entry.get("version", 0) < 1:
            raise ValueError(f"Invalid registry identity: {sample_id}")
        if entry["status"] not in {
            "verified",
            "partial",
            "pending_review",
            "honest_control",
        }:
            raise ValueError(f"Invalid registry status: {sample_id}")
        if entry["label"] not in {"honest", "sabotaged"} or (
            (entry["label"] == "honest") != (entry["status"] == "honest_control")
        ):
            raise ValueError(f"Invalid registry label: {sample_id}")
        if entry["status"] in {"verified", "partial"} and (
            not entry.get("mechanism")
            or not entry.get("locations")
            or not entry.get("provenance")
        ):
            raise ValueError(f"Reviewed entry lacks evidence: {sample_id}")
        if entry["status"] == "verified" and not any(
            loc.get("role") == "primary" for loc in entry["locations"]
        ):
            raise ValueError(f"Verified entry needs a primary location: {sample_id}")
        for loc in entry["locations"]:
            start, end = loc["start_line"], loc["end_line"]
            if not _relative(loc["path"]) or len(loc.get("sha256", "")) != 64:
                raise ValueError(f"Invalid registry source: {sample_id}")
            if (start is None) != (end is None) or (
                start is not None
                and (
                    type(start) is not int
                    or type(end) is not int
                    or start < 1
                    or end < start
                    or len(loc["excerpt"].splitlines()) != end - start + 1
                )
            ):
                raise ValueError(f"Invalid registry range: {sample_id}")
            if loc["role"] not in {"primary", "supporting", "unverified_artifact"}:
                raise ValueError(f"Invalid registry location role: {sample_id}")
        for comparison in entry["comparisons"]:
            honest = document["samples"][comparison["honest_sample_id"]]
            if (
                not _relative(comparison["path"])
                or honest["workspace"] != comparison["honest_workspace"]
            ):
                raise ValueError(f"Invalid paired comparison: {sample_id}")
    return document


@lru_cache(maxsize=512)
def _file_hash(path: str, size: int, mtime_ns: int) -> str:
    # Stat is part of the cache key so edits are rechecked within a process.
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def file_hash(path: Path) -> str:
    stat = path.stat()
    return _file_hash(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def resolve_sabotage(
    sample_id: str,
    target: str | None = None,
    workspace: str | None = None,
    *,
    repo: Path | None = None,
    document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve exact identity, retain a portable snapshot, and detect source drift.

    Checkout validation does not establish historical exposure. A missing checkout
    leaves the reviewed snapshot available for offline inspection of log evidence.
    """
    from debate_asb.datasets.asb import PROJECT_ROOT

    repo = repo or PROJECT_ROOT
    document = document if document is not None else read_registry()
    entry = document["samples"].get(sample_id)
    result: dict[str, Any] = {
        "registry_version": document["registry_version"],
        "registry_sha256": digest(document),
        "source_commit": document["source_commit"],
        "entry_sha256": digest(entry),
        "record": copy.deepcopy(entry),
        "usable": False,
        "validation": {"status": "missing", "issues": []},
    }
    if entry is None:
        return result
    issues = []
    if target is not None and target != entry["label"]:
        issues.append("logged label differs from registry label")
    if workspace:
        # ASB logs store host paths. Compare the dataset-relative workspace so
        # records remain portable between collaborators' checkout directories.
        normalized = workspace.replace("\\", "/").rstrip("/")
        relative = normalized.split("/data/asb/", 1)[-1]
        if relative != entry["workspace"]:
            issues.append("logged workspace differs from registry workspace")
    if issues:
        result["validation"] = {"status": "identity_mismatch", "issues": issues}
        return result
    root = repo / "data/asb"
    if root.exists():
        for loc in entry["locations"]:
            path = root / entry["workspace"] / loc["path"]
            if not path.is_file() or file_hash(path) != loc["sha256"]:
                issues.append(
                    f"changed or missing source: {entry['workspace']}/{loc['path']}"
                )
            elif loc["start_line"] is not None:
                text = "\n".join(
                    path.read_text().splitlines()[
                        loc["start_line"] - 1 : loc["end_line"]
                    ]
                )
                if text != loc["excerpt"]:
                    issues.append(f"source excerpt differs: {loc['path']}")
        for comparison in entry["comparisons"]:
            path = root / comparison["honest_workspace"] / comparison["path"]
            if not path.is_file() or file_hash(path) != comparison["honest_sha256"]:
                issues.append(
                    f"changed or missing honest reference: {comparison['honest_workspace']}/{comparison['path']}"
                )
    status = (
        "source_drift"
        if issues
        else "matched"
        if root.exists()
        else "checkout_unavailable"
    )
    result["validation"] = {"status": status, "issues": sorted(set(issues))}
    result["usable"] = not issues and entry["status"] in {
        "verified",
        "partial",
        "honest_control",
    }
    return result


def normalized_locations(reference: dict[str, Any]) -> dict[str, Any] | None:
    if not reference["usable"] or reference["record"]["status"] == "honest_control":
        return None
    entry = reference["record"]
    return {
        "normalization_status": "verified"
        if entry["status"] == "verified"
        else "partial",
        "source_file": "src/debate_asb/sabotage_registry.json",
        "source_commit": reference["source_commit"],
        "entry_sha256": reference["entry_sha256"],
        "sabotage_locations": [
            {
                "path": loc["path"],
                "role": loc["role"],
                "line_ranges": []
                if loc["start_line"] is None
                else [
                    {
                        "start": loc["start_line"],
                        "end": loc["end_line"],
                        "approximate": False,
                    }
                ],
            }
            for loc in entry["locations"]
        ],
    }


def registry_basis(sample_ids: list[str]) -> dict[str, Any]:
    """Compatibility view for local analyst helpers; never an extractor input."""
    document = read_registry()
    samples = {}
    for sid in sample_ids:
        ref = resolve_sabotage(sid, document=document)
        entry = ref["record"]
        if not ref["usable"] or entry is None or entry["label"] != "sabotaged":
            raise ValueError(f"No usable checked sabotage basis: {sid}")
        samples[sid] = {
            "workspace": entry["workspace"],
            "mechanism": entry["mechanism"],
            "logged_annotation": entry["rubric"]["sabotage"],
            "source_locations": [
                {**loc, "current_file_sha256": loc["sha256"]}
                for loc in entry["locations"]
            ],
            "sabotage_reference": ref,
        }
    return {
        "schema_version": 1,
        "samples": samples,
        "source": "src/debate_asb/sabotage_registry.json",
        "registry_sha256": digest(document),
    }


def require_saved_reference(analysis: dict[str, Any]) -> None:
    """Prevent one-off review scripts from silently returning to old target hints."""
    if not analysis["manifest"].get("sabotage_registry"):
        raise ValueError(
            "Run scripts/refresh_sabotage_reports.py before using this legacy analysis helper."
        )
    for sample in analysis["samples"]:
        ref = sample.get("sabotage_reference")
        if not ref or (
            ref.get("record") and digest(ref["record"]) != ref["entry_sha256"]
        ):
            raise ValueError(
                f"Missing or altered saved sabotage reference: {sample['id']}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate"])
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--sample", action="append")
    args = parser.parse_args()
    document = read_registry()
    ids = args.sample or list(document["samples"])
    failed = False
    for sid in ids:
        reference = resolve_sabotage(sid, repo=args.repo, document=document)
        status = reference["validation"]["status"]
        if status in {"missing", "identity_mismatch", "source_drift"}:
            print(sid, status, reference["validation"]["issues"])
            failed = True
    print(
        "Registry entries:",
        dict(Counter(e["status"] for e in document["samples"].values())),
    )
    print("Registry SHA-256:", digest(document))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
