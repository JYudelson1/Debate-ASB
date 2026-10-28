import json


def test_review_context_stays_within_visible_codebase(tmp_path, monkeypatch):
    from debate_asb.end_to_end import review_contract

    root = tmp_path / "code"
    root.mkdir()
    (root / "PAPER.md").write_text("Paper.")
    (root / "visible.py").write_text("visible = True\n")
    (root / "CLAUDE.md").write_text("Hidden instructions.")
    secret = tmp_path / "secret.txt"
    secret.write_text("Private host data.")
    (root / "outside.txt").symlink_to(secret)
    monkeypatch.setattr(review_contract, "resolve_sabotage", lambda *args: {})
    monkeypatch.setattr(review_contract, "sample_catalog", lambda *args: {})
    packet = review_contract.review_packet(
        {
            "sample_id": "test",
            "bundle_number": 1,
            "extraction": {
                "bundles": [
                    {
                        "excerpts": [
                            {"path": p}
                            for p in [
                                "visible.py",
                                "CLAUDE.md",
                                "../secret.txt",
                                "outside.txt",
                                str(secret),
                            ]
                        ]
                    }
                ]
            },
        },
        root,
        [],
        "honest",
    )
    assert [s["path"] for s in packet["source_context"]] == ["visible.py"]


def test_review_retry_feedback_preserves_rejected_quotes_and_evidence(tmp_path):
    from debate_asb.end_to_end.review import save_validation_feedback
    from debate_asb.end_to_end.study import read_json
    from debate_asb.evidence import content_sha256

    packet = {
        "bundle": {"excerpts": [{"text": "alpha = result['alpha']"}]},
        "canonical_reference": {"entry_sha256": "reference"},
        "proposed_fixes": [{"debate_epoch": 1, "rank": 1}],
    }
    original = json.dumps(packet, sort_keys=True)
    bad_quote = {"excerpt": 1, "quote": "alpha_over_h = a / h**2"}
    raw = json.dumps({"target_citations": [bad_quote]})
    save_validation_feedback(tmp_path, "test", raw, packet, "Invalid citation", 2)
    feedback = read_json(tmp_path / "review-feedback/test.json")
    assert feedback["feedback"]["rejected_target_citations"] == [bad_quote]
    assert feedback["rejected_response_sha256"] == content_sha256(raw)
    assert json.dumps(packet, sort_keys=True) == original
    assert not (tmp_path / "reviews/test.json").exists()
    # An older failed attempt cannot displace more recent verification feedback.
    save_validation_feedback(tmp_path, "test", "{}", packet, "Older failure", 1)
    assert read_json(tmp_path / "review-feedback/test.json") == feedback
