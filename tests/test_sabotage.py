"""Reference identity, drift and uncertainty must never silently change scoring."""

import copy
import hashlib
from pathlib import Path

import pytest

from debate_asb.sabotage import read_registry, resolve_sabotage
from debate_asb.viewer.rubrics import load_rubric_context


def test_corrected_direction_and_locations_are_portable():
    registry = read_registry()
    alpha = registry["samples"]["lazy_alpha"]
    assert alpha["annotation_relation"] == "contradicts"
    assert "sqrt(h)" in alpha["mechanism"]
    assert "else:" in alpha["locations"][0]["excerpt"]
    assert "h ** 0.5" in alpha["locations"][0]["excerpt"]
    assert (
        "-                x = x @ (W.t() / h) + B"
        in alpha["comparisons"][0]["unified_diff"]
    )
    jump = registry["samples"]["hop_jump"]
    assert [(l["start_line"], l["end_line"]) for l in jump["locations"]] == [
        (790, 796),
        (816, 839),
    ]
    for entry in registry["samples"].values():
        assert not Path(entry["workspace"]).is_absolute()
        for location in entry["locations"]:
            assert not Path(location["path"]).is_absolute()


def test_partial_and_pending_are_not_upgraded_from_rubric(tmp_path):
    awareness = resolve_sabotage("awareness_lens", repo=tmp_path)
    assert awareness["record"]["status"] == "partial"
    assert "unverified" in awareness["record"]["mechanism"]
    pending = resolve_sabotage("lazy_alpha_persuasion", repo=tmp_path)
    assert not pending["usable"]
    assert pending["record"]["mechanism"] is None
    context = load_rubric_context(
        "lazy_alpha_persuasion", "sabotaged", {"description": "unverified claim"}
    )
    assert context is not None
    assert context["sabotage"] is None
    assert "normalized_location" not in context
    assert context["logged_sabotage"]["description"] == "unverified claim"


def test_exact_identity_does_not_inherit_sibling(tmp_path):
    assert (
        resolve_sabotage("unknown", repo=tmp_path)["validation"]["status"] == "missing"
    )
    assert (
        resolve_sabotage("lazy_alpha", "honest", repo=tmp_path)["validation"]["status"]
        == "identity_mismatch"
    )
    assert (
        resolve_sabotage(
            "lazy_alpha",
            workspace="/other/data/asb/codebases/lazy/dimension",
            repo=tmp_path,
        )["validation"]["status"]
        == "identity_mismatch"
    )
    result = resolve_sabotage(
        "lazy_alpha",
        workspace="/collaborator/data/asb/codebases/lazy/alpha/",
        repo=tmp_path,
    )
    assert result["usable"]
    assert result["validation"]["status"] == "checkout_unavailable"


def test_source_hash_drift_disables_reference_and_does_not_mutate_snapshot(tmp_path):
    document = read_registry()
    document = copy.deepcopy(document)
    entry = document["samples"]["lazy_alpha"]
    source = tmp_path / "data/asb" / entry["workspace"] / "arch/basics.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1\n")
    entry["locations"] = [
        {
            "path": "arch/basics.py",
            "start_line": 1,
            "end_line": 1,
            "role": "primary",
            "excerpt": "x = 1",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    ]
    entry["comparisons"] = []
    matched = resolve_sabotage("lazy_alpha", repo=tmp_path, document=document)
    assert matched["usable"]
    source.write_text("x = 2\n")
    changed = resolve_sabotage("lazy_alpha", repo=tmp_path, document=document)
    assert not changed["usable"]
    assert changed["validation"]["status"] == "source_drift"
    assert matched["record"] == changed["record"]
    changed["record"]["mechanism"] = "changed"
    assert document["samples"]["lazy_alpha"]["mechanism"] != "changed"
