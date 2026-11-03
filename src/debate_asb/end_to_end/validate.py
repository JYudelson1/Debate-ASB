"""Validate a completed study before delivering its scores and archive."""

from pathlib import Path

from debate_asb.datasets.asb import asb_samples

from .debate import debate_jobs
from .metrics import summarize_repetitions
from .study import read_json, status, validate_dataset, write_json


def validate(directory: Path, *, require_reviews: bool = True) -> dict:
    manifest = read_json(directory / "study.json")
    if {s["id"]: s["label"] for s in manifest["samples"]} != {
        s.id: s.target for s in asb_samples()
    }:
        raise ValueError("Study identities/labels differ from the core ASB cohort")
    state = status(directory)
    if state["missing"]:
        raise ValueError(f"Missing extraction epochs: {state['missing']}")
    jobs = debate_jobs(directory)
    expected_samples = {s["id"] for s in manifest["samples"]}
    expected_bundles = {
        (sid, e, n)
        for sid in expected_samples
        for e in range(1, 4)
        for n in range(1, 21)
    }
    actual = {
        (j["sample_id"], j["extraction_epoch"], j["bundle_number"])
        for j in jobs.values()
    }
    if actual != expected_bundles or len(jobs) != 1200:
        raise ValueError(
            "Extraction handoff does not cover exactly 20 × 3 × 20 bundles"
        )
    expected_debates = {
        f"{key}__r{epoch}.json" for key in jobs for epoch in range(1, 4)
    }
    actual_debates = {p.name for p in (directory / "debates").glob("*.json")}
    if actual_debates != expected_debates:
        raise ValueError(
            "Debate result identities do not cover exactly the expected 3,600 repetitions"
        )
    validate_dataset(manifest, expected_samples)
    for key, job in jobs.items():
        records = []
        for epoch in range(1, 4):
            path = directory / "debates" / f"{key}__r{epoch}.json"
            if not path.exists():
                raise ValueError(f"Missing debate: {path}")
            row = read_json(path)
            if (
                row["sample_id"],
                row["extraction_epoch"],
                row["bundle_number"],
                row["debate_epoch"],
            ) != (
                job["sample_id"],
                job["extraction_epoch"],
                job["bundle_number"],
                epoch,
            ):
                raise ValueError(f"Debate identity drift: {path}")
            if (
                row["bundle_sha256"] != job["bundle_sha256"]
                or row["extraction_sha256"] != job["extraction_sha256"]
            ):
                raise ValueError(f"Debate evidence drift: {path}")
            if [(t["round"], t["side"]) for t in row["turns"]] != [
                (r, side) for r in range(1, 4) for side in ["sabotaged", "clean"]
            ]:
                raise ValueError(f"Incomplete or reordered debate: {path}")
            if [f["rank"] for f in row["top_fixes"]] != [1, 2, 3]:
                raise ValueError(f"Missing proposed fixes: {path}")
            for role, model, provider in [
                (
                    "debaters",
                    manifest["debate"]["debater_model"],
                    manifest["debate"]["debater_provider"],
                ),
                (
                    "judge",
                    manifest["debate"]["judge_model"],
                    manifest["debate"]["judge_provider"],
                ),
            ]:
                if row["models"][role] != {"model": model, "provider": provider}:
                    raise ValueError(f"Model/provider drift: {path}")
            records.append(row)
        summarize_repetitions(records)
        review_path = directory / "reviews" / f"{key}.json"
        if require_reviews and (
            not review_path.exists()
            or len(read_json(review_path).get("fix_reviews", [])) != 9
        ):
            raise ValueError(f"Incomplete source-bound/fix review: {key}")
    result = {
        "status": "passed",
        "codebases": 20,
        "extraction_epochs": 60,
        "bundles": 1200,
        "debates": 3600,
        "public_arguments": 21600,
        "proposed_fixes": 10800,
        "source_fix_reviews_checked": require_reviews,
        "aggregation": "max across all 60 bundle means, each requiring 3 repetitions",
    }
    write_json(
        directory / ("validation.json" if require_reviews else "score-validation.json"),
        result,
    )
    return result
