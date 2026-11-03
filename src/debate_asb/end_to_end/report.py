"""Build a portable viewer from retained exports, debates and review ledgers."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from debate_asb.known_issues import match_bundle, sample_catalog
from debate_asb.sabotage import resolve_sabotage

from .debate import debate_jobs
from .metrics import codebase_score, confusion, roc, summarize_repetitions
from .study import file_sha, read_json, write_json, write_text


def saved_reviews(manifest: dict) -> dict:
    out = {}
    for source in manifest.get("reviews", []):
        analysis = read_json(Path(source["analysis"]))
        review = read_json(Path(source["review"]))
        for sample in analysis["samples"]:
            saved = review["samples"][sample["key"]]
            for bundle in sample["metrics"]["bundles"]:
                number = bundle["number"]
                br = saved["bundle_reviews"][str(number)]
                relation = (
                    "honest"
                    if sample["target"] == "honest"
                    else "complete"
                    if number in saved["sufficient_bundles"]
                    else "incomplete"
                    if saved["included"] == "yes"
                    and number in saved["matching_bundles"]
                    else "clue"
                    if br.get("target_relation") == "related"
                    else "off_target"
                )
                out[(sample["id"], sample["epoch"], number)] = {
                    **br,
                    "target_relation": relation,
                    "review_source": source,
                    "reviewer": review["reviewer"],
                    "review_status": "saved_analyst_review",
                    "reviewed_bundle": bundle,
                }
    return out


def build_payload(directory: Path) -> dict:
    manifest = read_json(directory / "study.json")
    reviews = saved_reviews(manifest)
    jobs = debate_jobs(directory)
    by_sample: dict[str, list] = {s["id"]: [] for s in manifest["samples"]}
    for key, job in jobs.items():
        raw = job["extraction"]["bundles"][job["bundle_number"] - 1]
        records = [
            read_json(p)
            for e in range(1, 4)
            if (p := directory / "debates" / f"{key}__r{e}.json").exists()
        ]
        review = reviews.get(
            (job["sample_id"], job["extraction_epoch"], job["bundle_number"])
        )
        new_review = directory / "reviews" / f"{key}.json"
        if new_review.exists():
            model_review = read_json(new_review)
            if (
                model_review.get("bundle_sha256", job["bundle_sha256"])
                != job["bundle_sha256"]
            ):
                raise ValueError(f"Post-hoc review bundle changed: {key}")
            identity = next(
                s for s in manifest["samples"] if s["id"] == job["sample_id"]
            )
            reference = resolve_sabotage(
                job["sample_id"], identity["label"], identity["root"]
            )
            if model_review["reference_sha256"] != reference["entry_sha256"]:
                raise ValueError(f"Post-hoc review reference changed: {key}")
            if review:
                review = {
                    **review,
                    "fix_reviews": model_review["fix_reviews"],
                    "supplementary_model_review": model_review,
                    "review_disagreement": review["target_relation"]
                    != model_review["target_relation"],
                }
            else:
                review = model_review
        if review and review.get("reviewed_bundle"):
            prior = review["reviewed_bundle"]
            if prior["observation"] != raw["observation"] or [
                e["text"] for e in prior["excerpts"]
            ] != [e["text"] for e in raw["excerpts"]]:
                raise ValueError(
                    f"Saved review does not match retained evidence: {key}"
                )
        known = match_bundle(raw, sample_catalog(job["sample_id"]))
        by_sample[job["sample_id"]].append(
            {
                **raw,
                "key": key,
                "extraction_epoch": job["extraction_epoch"],
                "score": summarize_repetitions(records),
                "debates": sorted(records, key=lambda r: r["debate_epoch"]),
                "review": review,
                "known_issues": known,
                "extraction_sha256": job["extraction_sha256"],
                "bundle_sha256": job["bundle_sha256"],
                "source_log": job.get("source_log"),
            }
        )
    rows = []
    for sample in manifest["samples"]:
        bundles = by_sample[sample["id"]]
        score = codebase_score(bundles)
        bundles.sort(
            key=lambda b: (b["score"]["mean"] is not None, b["score"]["mean"] or 0),
            reverse=True,
        )
        rows.append(
            {
                "id": sample["id"],
                "label": sample["label"],
                "score": score,
                "bundles": bundles,
                "complete_bundles": sum(b["score"]["complete"] for b in bundles),
                "reference": resolve_sabotage(
                    sample["id"], sample["label"], sample["root"]
                ),
            }
        )
    result = roc(rows)
    return {
        "schema_version": 1,
        "title": "Can debate catch sabotage end to end?",
        "study": manifest,
        "samples": rows,
        "roc": result,
        "confusion": confusion(rows),
        "complete": all(r["score"] is not None for r in rows),
        "counts": {
            "codebases": len(rows),
            "scored_codebases": sum(r["score"] is not None for r in rows),
            "extractions": len(list((directory / "extractions").glob("*.json"))),
            "bundles": len(jobs),
            "completed_debates": sum(
                len(b["debates"]) for r in rows for b in r["bundles"]
            ),
            "reviewed_bundles": sum(
                b["review"] is not None for r in rows for b in r["bundles"]
            ),
            "saved_analyst_target_labels": sum(
                b["review"] is not None
                and b["review"].get("review_status") == "saved_analyst_review"
                for r in rows
                for b in r["bundles"]
            ),
            "model_target_labels": sum(
                b["review"] is not None
                and b["review"].get("review_status") == "model_review"
                for r in rows
                for b in r["bundles"]
            ),
        },
        "provenance": {
            "study_manifest": str(directory / "study.json"),
            "study_sha256": file_sha(directory / "study.json"),
        },
        "costs": read_json(directory / "costs.json")
        if (directory / "costs.json").exists()
        else None,
    }


def render(directory: Path) -> Path:
    payload = build_payload(directory)
    assets = files("debate_asb.viewer") / "static" / "end_to_end"
    html = (assets / "report.html").read_text()
    html = html.replace("/* REPORT_CSS */", (assets / "report.css").read_text())
    html = html.replace("/* REPORT_JS */", (assets / "report.js").read_text())
    html = html.replace(
        "REPORT_DATA",
        json.dumps(payload, ensure_ascii=False, allow_nan=False).replace(
            "<", "\\u003c"
        ),
    )
    out = directory / "report.html"
    write_text(out, html)
    write_json(directory / "report-data.json", payload)
    return out
