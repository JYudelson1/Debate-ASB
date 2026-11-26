"""Portable handoffs, recorded spending and collaborator archives."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from inspect_ai.event import ModelEvent
from inspect_ai.log import read_eval_log, resolve_sample_attachments

from debate_asb.spend import Spend, sample_spend

from .study import file_sha, read_json, status, write_json


def bind_extraction_sources(directory: Path) -> None:
    """Attach saved log identities without changing canonical extraction content."""
    import json

    from debate_asb.evidence import content_sha256

    for path in sorted((directory / "extraction-logs").glob("attempt-*/*.eval")):
        log = read_eval_log(str(path))
        log_sha = file_sha(path)
        for unresolved in log.samples or []:
            sample = resolve_sample_attachments(unresolved)
            output = directory / "extractions" / f"{sample.id}-e{sample.epoch}.json"
            if not output.exists():
                continue
            record = read_json(output)
            raw = sample.store.get("stages", {}).get("extraction")
            if record.get("source_log") or not raw:
                continue
            if (
                content_sha256(json.dumps(raw, sort_keys=True))
                != record["extraction_sha256"]
            ):
                continue
            write_json(
                output,
                {
                    **record,
                    "source_log": str(path.resolve()),
                    "source_log_sha256": log_sha,
                },
            )


def extraction_export(directory: Path) -> Path:
    bind_extraction_sources(directory)
    manifest = read_json(directory / "study.json")
    state = status(directory)
    output = directory / "extraction-export.json"
    write_json(
        output,
        {
            "schema_version": 1,
            "complete": not state["missing"],
            "expected_sample_epochs": 60,
            "missing_sample_epochs": state["missing"],
            "study": manifest,
            "extractions": [
                read_json(p) for p in sorted((directory / "extractions").glob("*.json"))
            ],
            "participant_boundary": "Only audit task, selected evidence bundle, and the selected codebase tools enter debate. Labels and references are scoring-only.",
        },
    )
    return output


def recorded_spend(directory: Path) -> dict:
    paths: dict[Path, str] = {}
    for category in ["extraction", "debate", "review"]:
        for path in (directory / f"{category}-logs").glob("attempt-*/*.eval"):
            paths[path.resolve()] = category
    for path in (directory / "extractions").glob("*.json"):
        record = read_json(path)
        if record.get("imported") and record.get("source_log"):
            paths[Path(record["source_log"])] = "historical_extraction"
    totals: dict[str, Spend] = {}
    seen = set()
    provenance = []
    for path, category in paths.items():
        log = read_eval_log(str(path))
        spend = Spend()
        nominal = 0.0
        for unresolved in log.samples or []:
            sample = resolve_sample_attachments(unresolved)
            unique = []
            for event in sample.events:
                if isinstance(event, ModelEvent):
                    identity = event.uuid
                    if identity in seen:
                        continue
                    seen.add(identity)
                unique.append(event)
            spend += sample_spend(unique)
            nominal += sum(sample.store.get("cost", {}).values())
        totals.setdefault(category, Spend())
        totals[category] += spend
        provenance.append(
            {
                "path": str(path),
                "sha256": file_sha(path),
                "category": category,
                "status": log.status,
                "billed_usd": spend.usd,
                "nominal_usd_in_log": nominal,
                "api_calls": spend.api_calls,
            }
        )
    result = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "categories": {
            category: {
                "billed_usd": spend.usd,
                "api_calls": spend.api_calls,
                "cache_hits": spend.cache_hits,
                "unpinned_calls": spend.unpinned_calls,
                "unlogged_calls": spend.unlogged_calls,
                "by_role_usd": dict(spend.by_role),
                "served_by": [
                    {"model": m, "pinned": p, "served": s, "calls": n}
                    for (m, p, s), n in spend.served_by.items()
                ],
            }
            for category, spend in totals.items()
        },
        "logs": provenance,
        "limitations": (
            "Recorded billed costs deduplicate persisted model-event UUIDs. "
            "Unpersisted interrupted requests or unlogged usage can incur additional unknown fees. "
            "Historical extraction costs cover the imported source logs only, separately from this run; "
            "earlier paid calls behind their cache hits are not reconstructed. "
            "Nominal log totals can include replayed role costs and are not summed as new spend."
        ),
    }
    write_json(directory / "costs.json", result)
    return result


def package(directory: Path) -> Path:
    from .report import build_payload, render
    from .validate import validate

    validate(directory)
    payload = build_payload(directory)
    if not payload["complete"]:
        raise ValueError(
            "Complete all extraction and debate epochs before packaging final results"
        )
    extraction_export(directory)
    recorded_spend(directory)
    render(directory)
    readme = directory / "README.txt"
    readme.write_text(
        "Open report.html in a browser. It works offline without installation.\n"
        "Each bundle score averages three debate repetitions. The codebase score is\n"
        "the maximum bundle mean across all three extractions (60 bundles).\n"
        "study.json records the exact cohort, settings, code revision and hashes.\n"
        "extraction-export.json contains the unchanged raw/structured evidence.\n"
        "report-data.json contains scores, public arguments, judge responses, fixes\n"
        "and reference/review judgments. costs.json records retained billed usage.\n"
        "Proposed fixes and their semantic reviews are not executed patch tests.\n"
        "Full API logs and source snapshots remain in the originating run folder.\n"
    )
    output = directory / "collaborator-report.zip"
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name in [
            "report.html",
            "report-data.json",
            "study.json",
            "extraction-export.json",
            "costs.json",
            "validation.json",
            "README.txt",
        ]:
            archive.write(directory / name, name)
    return output
