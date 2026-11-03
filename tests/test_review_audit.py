import json
import zipfile

import pytest

from debate_asb.end_to_end.audit import build_audit, logged_requests, sha


@pytest.fixture
def audit_study(tmp_path):
    key = "sample__x1__b01"
    bundle = {"number": 1, "observation": "Observed alteration", "excerpts": []}
    fix = {"rank": 1, "path": "f.py", "change": "Restore value", "rationale": "Target"}
    fixes = [{**fix, "debate_epoch": 1}]
    packet = {"bundle": bundle, "proposed_fixes": fixes, "source_context": []}
    user = json.dumps(packet)
    system = "Review the target; mark insufficiently specified fixes unknown."
    result = {
        "target_relation": "complete",
        "target_reason": "The defining alteration is present",
        "fix_reviews": [
            {
                "debate_epoch": 1,
                "rank": 1,
                "undoes_sabotage": "yes",
                "reason": "Restores target",
            }
        ],
    }
    review = {
        **result,
        "packet_sha256": sha(user),
        "prompt_sha256": sha(system),
        "reference_sha256": "ref",
    }
    sample = {
        "id": key,
        "attachments": {"sys": system, "user": user},
        "events_data": {
            "messages": [
                {"role": "system", "content": "attachment://sys"},
                {"role": "user", "content": "attachment://user"},
            ]
        },
        "events": [
            {
                "event": "model",
                "uuid": "accepted-event",
                "input_refs": [[0, 2]],
                "output": {
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {
                                        "type": "reasoning",
                                        "reasoning": "NOT_FOR_EXPORT",
                                    },
                                    {"type": "text", "text": json.dumps(result)},
                                ]
                            }
                        }
                    ]
                },
            }
        ],
    }
    logs = tmp_path / "review-logs" / "attempt-1"
    logs.mkdir(parents=True)
    with zipfile.ZipFile(logs / "review.eval", "w") as archive:
        archive.writestr("samples/sample.json", json.dumps(sample))
    (tmp_path / "reviews").mkdir()
    (tmp_path / "reviews" / f"{key}.json").write_text(json.dumps(review))
    report = {
        "study": {},
        "provenance": {},
        "samples": [
            {
                "id": "sample",
                "label": "sabotaged",
                "reference": {"entry_sha256": "ref"},
                "bundles": [
                    {
                        **bundle,
                        "key": key,
                        "extraction_epoch": 1,
                        "bundle_sha256": sha(json.dumps(bundle, sort_keys=True)),
                        "extraction_sha256": "extract",
                        "source_log": "source.eval",
                        "debates": [{"debate_epoch": 1, "top_fixes": [fix]}],
                        "review": {
                            "review_status": "saved_analyst_review",
                            "target_relation": "clue",
                            "reason": "Prior analyst reason",
                        },
                    }
                ],
            }
        ],
    }
    (tmp_path / "report-data.json").write_text(json.dumps(report))
    return tmp_path, key, review


def test_matches_exact_logged_request_and_preserves_analyst_disagreement(audit_study):
    directory, key, review = audit_study
    result = build_audit(directory)
    assert result["counts"]["logged_requests_matched"] == 1
    item = result["items"][0]
    assert item["analyst_review"]["target_relation"] == "clue"
    assert item["model_review"]["target_relation"] == "complete"
    example = result["prompt_examples"][0]
    assert sha(example["user"]) == review["packet_sha256"]
    assert sha(example["system"]) == review["prompt_sha256"]
    assert example["event_uuid"] == "accepted-event"
    assert "NOT_FOR_EXPORT" not in json.dumps(result)
    assert result["unmatched_requests"] == []
    assert result["items"][0]["key"] == key


def test_does_not_substitute_current_prompt_or_different_output(audit_study):
    directory, key, review = audit_study
    assert logged_requests(directory, {key: {**review, "prompt_sha256": "wrong"}}) == {}
    assert logged_requests(directory, {key: {**review, "packet_sha256": "wrong"}}) == {}
    assert (
        logged_requests(
            directory, {key: {**review, "target_reason": "different output"}}
        )
        == {}
    )


def test_rejects_mismatched_fix_identity(audit_study):
    directory, key, review = audit_study
    review["fix_reviews"][0]["rank"] = 2
    (directory / "reviews" / f"{key}.json").write_text(json.dumps(review))
    with pytest.raises(ValueError, match="Review/fix identity mismatch"):
        build_audit(directory)


def test_rejects_changed_reference_and_logged_bundle(audit_study):
    directory, key, review = audit_study
    review["reference_sha256"] = "changed"
    (directory / "reviews" / f"{key}.json").write_text(json.dumps(review))
    with pytest.raises(ValueError, match="Review/reference hash mismatch"):
        build_audit(directory)
    review["reference_sha256"] = "ref"
    (directory / "reviews" / f"{key}.json").write_text(json.dumps(review))
    report = json.loads((directory / "report-data.json").read_text())
    report["samples"][0]["bundles"][0]["bundle_sha256"] = "different"
    (directory / "report-data.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="Logged/bundle hash mismatch"):
        build_audit(directory)
