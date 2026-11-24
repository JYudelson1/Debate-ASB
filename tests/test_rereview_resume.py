import json

import pytest

from debate_asb.extraction_analysis import rereview


@pytest.mark.parametrize("field", ["model", "provider"])
def test_resume_rejects_reviewer_drift_before_loading_credentials(
    tmp_path, monkeypatch, field
):
    manifest = {"model": rereview.MODEL, "provider": rereview.PROVIDER}
    manifest[field] = "changed"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    def unexpected(*args, **kwargs):
        pytest.fail(
            "Reviewer drift must fail before loading credentials or checking API"
        )

    monkeypatch.setattr(rereview, "load_dotenv", unexpected)
    monkeypatch.setattr(rereview, "check_openrouter_account", unexpected)
    with pytest.raises(ValueError, match="Frozen reviewer model/provider"):
        rereview.run_reviews(tmp_path)


@pytest.mark.parametrize("field", ["model", "provider"])
def test_resume_rejects_accepted_review_with_different_reviewer(
    tmp_path, monkeypatch, field
):
    prompt, packet = "Frozen instructions", "{}"
    manifest = {
        "model": rereview.MODEL,
        "provider": rereview.PROVIDER,
        "prompt_sha256": rereview.sha(prompt),
        "jobs": [{"id": "bundle", "packet_sha256": rereview.sha(packet)}],
    }
    accepted = {
        "model": rereview.MODEL,
        "provider": rereview.PROVIDER,
        "prompt_sha256": manifest["prompt_sha256"],
        "packet_sha256": rereview.sha(packet),
    }
    accepted[field] = "changed"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "prompt.md").write_text(prompt)
    for folder, content in (("packets", packet), ("reviews", json.dumps(accepted))):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "bundle.json").write_text(content)
    monkeypatch.setattr(rereview, "load_dotenv", lambda *args: None)
    monkeypatch.setattr(rereview, "check_openrouter_account", lambda: None)
    with pytest.raises(ValueError, match="Completed review drift: bundle"):
        rereview.run_reviews(tmp_path)
