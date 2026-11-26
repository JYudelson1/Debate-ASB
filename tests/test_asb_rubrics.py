"""Rubric corrections reach saved metadata without changing ASB workspaces."""

import copy
import hashlib
import json

import pytest
import yaml

from debate_asb.datasets import asb
from debate_asb.datasets import asb_rubrics as corrections


def test_loader_applies_correction_and_retains_original(monkeypatch, tmp_path):
    root = tmp_path / "asb"
    rubrics = root / "codebases/_rubrics"
    rubrics.mkdir(parents=True)
    entry = {
        "id": "example",
        "workspace": "codebases/example",
        "sabotage": {"description": "old", "location": "code.py:1"},
        "rubric": {"acceptable_fixes": ["old repair"]},
    }
    honest = {"id": "control", "workspace": "codebases/control"}
    upstream = {"paper_name": "Example", "sabotaged": [entry], "honest": honest}
    (rubrics / "example.yaml").write_text(yaml.safe_dump(upstream))
    overrides = tmp_path / "corrections"
    overrides.mkdir()
    override = {
        "version": 1,
        "sample_id": "example",
        "source_commit": "pinned-source",
        "source_entry_sha256": corrections.entry_digest(entry),
        "reason": "The original annotation names the wrong mechanism.",
        "updates": {
            "sabotage": {"description": "corrected"},
            "rubric": {"acceptable_fixes": ["working repair"]},
        },
    }
    (overrides / "example.json").write_text(json.dumps(override))
    monkeypatch.setattr(corrections, "CORRECTIONS_DIR", overrides)
    monkeypatch.setattr(asb, "ASB_ROOT", root)
    monkeypatch.setattr(asb, "RUBRIC_DIRS", [rubrics])
    [sample, control] = asb.asb_samples(["example", "control"])
    metadata = sample.metadata
    assert metadata is not None
    assert metadata["answer_key"] == {
        "description": "corrected",
        "location": "code.py:1",
    }
    assert metadata["rubric"]["acceptable_fixes"] == ["working repair"]
    assert metadata["rubric_correction"]["original_sabotage"] == entry["sabotage"]
    assert metadata["rubric_correction"]["original_rubric"] == entry["rubric"]
    assert len(metadata["rubric_correction"]["correction_sha256"]) == 64
    assert metadata["artifacts"]["codebase"]["root"] == str(root / entry["workspace"])
    assert (sample.id, sample.target, sample.input) == (
        "example",
        "sabotaged",
        asb.ASB_TASK,
    )
    assert control.metadata is not None
    assert control.metadata["answer_key"] is None
    assert "rubric_correction" not in control.metadata
    assert yaml.safe_load((rubrics / "example.yaml").read_text()) == upstream

    changed = copy.deepcopy(entry)
    changed["sabotage"]["description"] = "new upstream description"
    with pytest.raises(ValueError, match="re-review example"):
        corrections.corrected_entry("example", changed)


@pytest.mark.skipif(not asb.ASB_ROOT.exists(), reason="ASB data not fetched")
@pytest.mark.parametrize("path", sorted(corrections.CORRECTIONS_DIR.glob("*.json")))
def test_reviewed_corrections_match_sources_and_load(path):
    correction = json.loads(path.read_text())
    sid = correction["sample_id"]
    upstream = next(
        entry
        for rubric_path in (asb.ASB_ROOT / "codebases/_rubrics").glob("*.yaml")
        if not rubric_path.name.startswith("_")
        for entry in yaml.safe_load(rubric_path.read_text()).get("sabotaged", [])
        if entry["id"] == sid
    )
    assert corrections.entry_digest(upstream) == correction["source_entry_sha256"]
    for evidence in correction["evidence"]:
        raw = (asb.ASB_ROOT / evidence["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == evidence["sha256"]
        lines = raw.decode().splitlines()
        assert (
            "\n".join(lines[evidence["start_line"] - 1 : evidence["end_line"]])
            == evidence["excerpt"]
        )
    original = copy.deepcopy(upstream)
    expected, _ = corrections.corrected_entry(sid, upstream)
    assert upstream == original
    [sample] = asb.asb_samples([sid])
    assert sample.metadata is not None
    assert sample.metadata["answer_key"] == expected["sabotage"]
    assert sample.metadata["rubric"] == expected["rubric"]
    assert (
        sample.metadata["rubric_correction"]["original_sabotage"]
        == upstream["sabotage"]
    )
    assert sample.metadata["rubric_correction"]["original_rubric"] == upstream["rubric"]
    assert sample.metadata["artifacts"]["codebase"]["root"] == str(
        asb.ASB_ROOT / upstream["workspace"]
    )
    assert sample.target == "sabotaged"
