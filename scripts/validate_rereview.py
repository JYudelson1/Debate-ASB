"""Verify an entire frozen re-review against its actual logged model requests."""

import argparse
import hashlib
import json
import zipfile
from collections import Counter
from pathlib import Path

from debate_asb.end_to_end.audit import message_text, resolve_attachments
from debate_asb.end_to_end.study import write_json
from debate_asb.extraction_analysis.rereview import (
    build_results,
    frozen_hash,
    read,
    sha,
    validate_review,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--allow-unscored", action="store_true")
    args = parser.parse_args()
    out = args.directory.resolve()
    manifest = read(out / "manifest.json")
    jobs = {j["id"]: j for j in manifest["jobs"]}
    accepted = {p.stem: read(p) for p in (out / "reviews").glob("*.json")}
    assert set(accepted) <= set(jobs), "Unexpected reviews"
    assert args.allow_unscored or set(accepted) == set(jobs), "Reviews remain unscored"
    policy = (
        read(out / "scoring-policy.json")
        if (out / "scoring-policy.json").exists()
        else None
    )
    flagged = {
        key
        for key, row in accepted.items()
        if row.get("validation_status") == "counted_with_warnings"
    }
    if flagged:
        assert (
            policy
            and policy["user_decision"] == "Count verdicts, flag citation/schema issues"
        )
        assert flagged == set(policy["selected_responses"])
        assert set(accepted) - flagged == set(policy["strict_review_file_sha256"])
        invalid = read(out / "invalid-responses.json")
    else:
        invalid = {}
    prompt = (out / "prompt.md").read_text()
    assert sha(prompt) == manifest["prompt_sha256"]
    for key, job in jobs.items():
        text = (out / "packets" / f"{key}.json").read_text()
        assert sha(text) == job["packet_sha256"]
        packet = json.loads(text)
        assert set(packet) == {
            "ground_truth_sabotage",
            "known_distractors",
            "evidence_bundle",
        }
        assert packet["evidence_bundle"]["sample_id"] == job["sample_id"]
        assert packet["evidence_bundle"]["bundle_id"] == key
        assert frozen_hash(job["bundle"]) == job["bundle_sha256"]
        if key not in accepted:
            continue
        record = accepted[key]
        assert record["packet_sha256"] == job["packet_sha256"]
        assert record["prompt_sha256"] == manifest["prompt_sha256"]
        assert record["bundle_sha256"] == job["bundle_sha256"]
        assert record["model"] == manifest["model"]
        assert record["provider"] == manifest["provider"]
        if key in flagged:
            assert policy is not None
            latest = invalid[key][-1]
            warnings = {}
            assert (
                validate_review(latest["raw_response"], packet, warnings=warnings)
                == record["review"]
            )
            assert record["review"] == latest["parsed_response"]
            assert record["citation_warnings"] == warnings.get("citations", [])
            assert record["schema_warnings"] == warnings.get("schema", [])
            assert record["scoring_policy_sha256"] == frozen_hash(policy)
            selected = policy["selected_responses"][key]
            assert record["source_response"] == selected
            assert selected["log"] == latest["log"]
            assert selected["sample_member"] == latest["sample_member"]
            assert selected["visible_output_sha256"] == sha(latest["raw_response"])
        else:
            assert (
                validate_review(json.dumps(record["review"]), packet)
                == record["review"]
            )
            if policy:
                assert (
                    sha((out / "reviews" / f"{key}.json").read_text())
                    == policy["strict_review_file_sha256"][key]
                )

    bound, errors, events, logged_jobs = {}, Counter(), set(), set()
    provider_unreported_errors = []
    logs = sorted((out / "logs").glob("launch-*/*.eval"))
    for path in logs:
        with path.open("rb") as file:
            log_sha256 = hashlib.file_digest(file, "sha256").hexdigest()
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if not name.startswith("samples/") or not name.endswith(".json"):
                    continue
                sample = json.loads(archive.read(name))
                key = str(sample["id"])
                assert key in jobs, f"Unrequested sample in log: {key}"
                logged_jobs.add(key)
                if sample.get("error"):
                    errors[sample["error"].get("message", "unknown")] += 1
                attachments = sample.get("attachments", {})
                for event in sample.get("events", []):
                    if event.get("event") != "model":
                        continue
                    if event.get("uuid"):
                        assert event["uuid"] not in events, (
                            "Duplicate persisted model event"
                        )
                        events.add(event["uuid"])
                    call = event.get("call") or {}
                    response = call.get("response") or {}
                    if call and response.get("provider") is None:
                        assert event.get("error"), (
                            "Successful response lacks served-provider metadata"
                        )
                        provider_unreported_errors.append(
                            {
                                "event_uuid": event.get("uuid"),
                                "error": event["error"],
                                "log": str(path),
                            }
                        )
                    messages = event.get("input", [])
                    if not messages:
                        bank = sample.get("events_data", {}).get("messages", [])
                        messages = [
                            m for a, b in event.get("input_refs", []) for m in bank[a:b]
                        ]
                    messages = [
                        {
                            "role": m["role"],
                            "content": resolve_attachments(
                                m.get("content", ""), attachments
                            ),
                        }
                        for m in messages
                    ]
                    assert [m["role"] for m in messages] == ["system", "user"]
                    assert sha(message_text(messages[0])) == manifest["prompt_sha256"]
                    assert sha(message_text(messages[1])) == jobs[key]["packet_sha256"]
                    assert event["model"] == "openrouter/openai/gpt-6-luna"
                    assert not event.get("tools"), "Reviewer was supplied tools"
                    choices = (event.get("output") or {}).get("choices", [])
                    if not choices:
                        continue
                    text = resolve_attachments(
                        message_text(choices[0]["message"]), attachments
                    )
                    if not isinstance(text, str):
                        continue
                    try:
                        value = json.loads(text)
                    except (TypeError, ValueError):
                        continue
                    if key in accepted and value == accepted[key]["review"]:
                        bound[key] = {
                            "log": str(path),
                            "log_sha256": log_sha256,
                            "member": name,
                            "model_event_uuid": event.get("uuid"),
                            "packet_sha256": jobs[key]["packet_sha256"],
                            "prompt_sha256": manifest["prompt_sha256"],
                            "visible_output_sha256": sha(text),
                        }
    assert set(bound) == set(accepted), "Accepted outputs lack exact logged matches"
    for key in flagged:
        assert policy is not None
        selected = policy["selected_responses"][key]
        assert bound[key]["log"] == selected["log"]
        assert bound[key]["member"] == selected["sample_member"]
        assert bound[key]["visible_output_sha256"] == selected["visible_output_sha256"]
    assert logged_jobs == set(jobs), "Some requested bundles have no model event"
    accounting = [read(p) for p in (out / "logs").glob("launch-*/accounting.json")]
    assert len(accounting) == len(logs), "An eval log lacks accounting"
    assert not any(a["unlogged_calls"] or a["unpinned_calls"] for a in accounting)
    assert all(
        route["pinned"] == "openai" and route["served"] in {"OpenAI", None}
        for a in accounting
        for route in a["served_by"]
    )
    assert sum(
        route["calls"]
        for a in accounting
        for route in a["served_by"]
        if route["served"] is None
    ) == len(provider_unreported_errors)
    result = build_results(out)
    assert (args.allow_unscored or result["complete"]) and result["counts"][
        "total"
    ] == 4380
    assert len(result["cases"]) == 231
    assert len([j for j in jobs.values() if len(j["source_reports"]) == 2]) == 240
    for variant in result["variants"]:
        for metric in ("complete", "presence"):
            m = variant[metric]
            assert m["n"] + m["unresolved"] == variant["epochs"]
            assert m["new"] - m["old"] == m["up"] - m["down"]
    source_checks = [
        {
            "path": s["path"],
            "unchanged": sha(Path(s["path"]).read_text()) == s["sha256"],
        }
        for s in manifest["source_reports"]
    ]
    write_json(out / "review-log-bindings.json", bound)
    summary = {
        "scored_reviews": len(accepted),
        "strict_validation_passed": len(accepted) - len(flagged),
        "scored_with_warnings": len(flagged),
        "citation_warnings": sum(
            len(r.get("citation_warnings", [])) for r in accepted.values()
        ),
        "schema_warnings": sum(
            len(r.get("schema_warnings", [])) for r in accepted.values()
        ),
        "unscored": len(jobs) - len(accepted),
        "all_requested_bundles_have_responses": logged_jobs == set(jobs),
        "exact_logged_matches": len(bound),
        "distinct_logged_model_events": len(events),
        "failed_attempts_by_reason": dict(errors),
        "provider_verified": True,
        "provider_metadata_missing_on_failed_calls": provider_unreported_errors,
        "source_reports": source_checks,
        "billed_usd": result["counts"]["billed_usd"],
        "counts": result["counts"],
    }
    write_json(out / "validation.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
