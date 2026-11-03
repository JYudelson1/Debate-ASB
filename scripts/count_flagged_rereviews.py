"""Count preserved verdicts with warnings after an explicit user scoring decision."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from debate_asb.end_to_end.study import write_json
from debate_asb.extraction_analysis.rereview import (
    frozen_hash,
    read,
    result_files,
    sha,
    validate_review,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--user-decision", required=True)
    args = parser.parse_args()
    out = args.directory.resolve()
    manifest = read(out / "manifest.json")
    jobs = {j["id"]: j for j in manifest["jobs"]}
    saved = result_files(out)
    invalid = read(out / "invalid-responses.json")
    pending = set(jobs) - set(saved)
    if not pending or pending != set(invalid):
        raise ValueError(
            "Expected preserved failed responses for exactly the unscored jobs"
        )
    if (out / "scoring-policy.json").exists():
        raise ValueError("An inclusion decision is already frozen")
    if not args.user_decision.strip():
        raise ValueError("Record the user's explicit scoring decision")

    records, selected = {}, {}
    counted_at = datetime.now(timezone.utc).isoformat()
    for key in sorted(pending):
        job = jobs[key]
        attempts = invalid[key]
        latest = attempts[-1]
        if any(a["completed_at"] > latest["completed_at"] for a in attempts):
            raise ValueError(f"Preserved attempt order is not chronological: {key}")
        packet_text = (out / "packets" / f"{key}.json").read_text()
        if sha(packet_text) != job["packet_sha256"]:
            raise ValueError(f"Frozen packet changed: {key}")
        raw = latest["raw_response"]
        warnings = {}
        value = validate_review(raw, json.loads(packet_text), warnings=warnings)
        if value != latest["parsed_response"] or not warnings:
            raise ValueError(f"Unchanged, flagged parsed response required: {key}")
        selected[key] = {
            "log": latest["log"],
            "sample_member": latest["sample_member"],
            "visible_output_sha256": sha(raw),
            "started_at": latest["started_at"],
            "completed_at": latest["completed_at"],
            "preserved_attempt_number": len(attempts),
            "citation_warning_count": len(warnings.get("citations", [])),
            "schema_warning_count": len(warnings.get("schema", [])),
        }
        records[key] = {
            "review": value,
            "packet_sha256": job["packet_sha256"],
            "prompt_sha256": manifest["prompt_sha256"],
            "bundle_sha256": job["bundle_sha256"],
            "model": manifest["model"],
            "provider": manifest["provider"],
            "counted_at": counted_at,
            "validation_status": "counted_with_warnings",
            "citation_warnings": warnings.get("citations", []),
            "schema_warnings": warnings.get("schema", []),
            "source_response": selected[key],
        }
    policy = {
        "version": 1,
        "user_decision": args.user_decision,
        "recorded_at": counted_at,
        "response_selection": "Latest preserved completed response per remaining bundle",
        "raw_response_policy": "Verdicts, quotations and missing fields remain unchanged",
        "allowed_warnings": [
            "Non-literal quotations with valid retained-evidence source identifiers",
            "Omitted distractor_capture.matches when distractor verdict is no",
        ],
        "strict_review_file_sha256": {
            key: sha((out / "reviews" / f"{key}.json").read_text())
            for key in sorted(saved)
        },
        "selected_responses": selected,
    }
    write_json(out / "scoring-policy.json", policy)
    for key, record in records.items():
        record["scoring_policy_sha256"] = frozen_hash(policy)
        write_json(out / "reviews" / f"{key}.json", record)
    for key, expected in policy["strict_review_file_sha256"].items():
        assert sha((out / "reviews" / f"{key}.json").read_text()) == expected
    print(json.dumps({"counted_with_warnings": len(records), "new_model_calls": 0}))


if __name__ == "__main__":
    main()
