"""Post-hoc markers for ASB's catalogued non-sabotage issues. No model calls."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from debate_asb.sabotage import digest

CATALOG_PATH = Path(__file__).with_suffix(".json")
LOCATION = re.compile(
    r"([\w./-]+\.[a-zA-Z0-9]+)(?::(\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*))?(?:\s*\(([^)]*)\))?"
)


def read_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    document = json.loads(path.read_text())
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported known-issue catalog schema")
    ids = set()
    for entry in document["entries"]:
        key = (entry["family"], entry["id"])
        if key in ids or entry["category"] not in {
            "bug",
            "quirk",
            "mismatch",
            "misleading",
        }:
            raise ValueError("Invalid or duplicate known-issue entry")
        ids.add(key)
        for patterns in entry["signatures"]:
            for pattern in patterns:
                re.compile(pattern)
    return document


def sample_catalog(
    sample_id: str, document: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Resolve only exact family/variant IDs explicitly listed in present_in."""
    document = document if document is not None else read_catalog()
    family, _, variant = sample_id.partition("_")
    entries = [
        copy.deepcopy(e)
        for e in document["entries"]
        if e["family"] == family and variant in e["present_in"]
    ]
    return {
        "catalog_version": document["version"],
        "catalog_sha256": digest(document),
        "source_file": document["source_file"],
        "source_sha256": document["source_sha256"],
        "policy": document["policy"],
        "sample_id": sample_id,
        "entries": entries,
    }


def locations(entry: dict, sample_id: str) -> list[dict]:
    variant = sample_id.partition("_")[2]
    out = []
    for match in LOCATION.finditer(entry["file"]):
        path, spans, qualifier = match.groups()
        if qualifier in entry["present_in"] and qualifier != variant:
            continue
        ranges = []
        if spans:
            for span in spans.split(","):
                bounds = [int(n) for n in span.split("-")]
                ranges.append({"start": bounds[0], "end": bounds[-1]})
        out.append({"path": path, "ranges": ranges})
    return out


def match_bundle(bundle: dict, catalog: dict) -> list[dict]:
    """Mark matching quoted implementation patterns; path overlap is only a hint.

    Signatures describe ASB's annotation. They do not validate its interpretation,
    empirical impact, or an accusation in the extractor's observation.
    """
    out = []
    for issue in catalog["entries"]:
        targets = locations(issue, catalog["sample_id"])
        paths = {t["path"] for t in targets}
        relevant = [
            (i, e)
            for i, e in enumerate(bundle.get("excerpts", []), 1)
            if e["path"] in paths
        ]
        if not relevant:
            continue
        all_text = "\n".join(e.get("text", "") for _, e in relevant)
        # Every expression in one alternative must occur in quoted source.
        signature = next(
            (
                patterns
                for patterns in issue["signatures"]
                if all(re.search(p, all_text) for p in patterns)
            ),
            None,
        )
        verified = [
            (i, e)
            for i, e in relevant
            if (e.get("fidelity") or {}).get("status") == "verified_read"
        ]
        verified_text = "\n".join(e.get("text", "") for _, e in verified)
        if signature:
            confirmed = all(re.search(p, verified_text) for p in signature)
            selected = verified if confirmed else relevant
            # Keep only excerpts that contributed a signature, not all same-file quotes.
            evidence = [
                {
                    "excerpt": i,
                    "path": e["path"],
                    "fidelity": e.get("fidelity"),
                    "matched_text": [
                        m.group(0)
                        for p in signature
                        for m in re.finditer(p, e.get("text", ""))
                    ],
                }
                for i, e in selected
                if any(re.search(p, e.get("text", "")) for p in signature)
            ]
            kind = "code_match" if confirmed else "unverified_quote_match"
        else:
            evidence = []
            for i, excerpt in relevant:
                fidelity = excerpt.get("fidelity") or {}
                start = (
                    fidelity.get("actual_start")
                    if fidelity.get("status") == "verified_read"
                    else excerpt.get("start_line")
                )
                end = (
                    fidelity.get("actual_end")
                    if fidelity.get("status") == "verified_read"
                    else excerpt.get("end_line")
                )
                for target in targets:
                    if target["path"] != excerpt["path"]:
                        continue
                    if (
                        target["ranges"]
                        and start is not None
                        and end is not None
                        and any(
                            start <= r["end"] and end >= r["start"]
                            for r in target["ranges"]
                        )
                    ):
                        evidence.append(
                            {
                                "excerpt": i,
                                "path": excerpt["path"],
                                "reported_range": [start, end],
                                "fidelity": fidelity,
                            }
                        )
                        break
            if not evidence:
                continue  # File-only overlap cannot assert an issue is contained.
            kind = "location_hint"
        out.append(
            {
                "id": issue["id"],
                "title": issue["title"],
                "category": issue["category"],
                "match_kind": kind,
                "description": issue["description"],
                "impact": issue["impact"],
                "original_location": issue["file"],
                "evidence": evidence,
                "catalog_sha256": catalog["catalog_sha256"],
            }
        )
    return out


def summarize(bundles: list[dict]) -> dict:
    return {
        "bundle_count": len(bundles),
        "code_match_bundles": sum(
            any(
                m["match_kind"] == "code_match"
                for m in b.get("known_issue_matches", [])
            )
            for b in bundles
        ),
        "unverified_quote_bundles": sum(
            any(
                m["match_kind"] == "unverified_quote_match"
                for m in b.get("known_issue_matches", [])
            )
            for b in bundles
        ),
        "location_hint_bundles": sum(
            any(
                m["match_kind"] == "location_hint"
                for m in b.get("known_issue_matches", [])
            )
            for b in bundles
        ),
    }
