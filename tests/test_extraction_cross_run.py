import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from debate_asb.extraction_analysis.common import fingerprint
from debate_asb.extraction_analysis.cross_run import (
    build_data,
    build_report,
    owner_map,
    write_canvas,
)
from debate_asb.extraction_analysis.metrics import aggregate, association, measure
from debate_asb.extraction_analysis.traces import source_blocks


def tool(n, text, path="a.py", function="read_file"):
    return {
        "ordinal": n,
        "function": function,
        "arguments": {"path": path},
        "result": text,
        "failed": False,
    }


def inventory(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\nz = 3\n")
    return {
        "root": str(tmp_path),
        "files": {
            "a.py": {"role": "source", "readable": True, "lines": 3},
            "PAPER.md": {"role": "paper_docs", "readable": True, "lines": 100},
        },
    }


def test_unique_coverage_and_repeats_distinguish_worker_ownership(tmp_path):
    inv = inventory(tmp_path)
    ts = [
        tool(1, "1  x = 1\n2  y = 2"),
        tool(2, "1  x = 1\n2  y = 2"),
        tool(3, "1  x = 1\n2  y = 2"),
        tool(4, "a.py:3: z = 3", function="search"),
        tool(5, "a.py  (100 bytes)", function="list_files"),
    ]
    r = measure(
        {"codebase_root": str(tmp_path)},
        ts,
        inv,
        [{"path": "a.py", "start": 1, "end": 2}],
        {("a.py", 2): ["known"]},
        {
            "1": "worker1",
            "2": "worker2",
            "3": "worker2",
            "4": "worker1",
            "5": "worker1",
        },
    )
    assert r["coverage"] == pytest.approx(2 / 3)
    assert r["coverage_verified"] == pytest.approx(2 / 3)
    assert r["coverage_search"] == 1
    assert r["target_read_events"] == 3
    assert r["target_repetitions_per_line"] == 3
    assert r["same_agent_target_repeats"] == 2
    assert r["target_read_owners"] == 2
    assert r["target_region_fraction"] == 1
    assert r["attention_lines"]["target"] == 6
    assert r["attention_lines"]["known"] == 0  # target precedence


def test_returned_text_mismatch_does_not_receive_verified_credit(tmp_path):
    inv = inventory(tmp_path)
    r = measure(
        {"codebase_root": str(tmp_path)},
        [tool(1, "1  x = 9\n2  y = 2\n100  non-existent")],
        inv,
        [],
        {},
        {"1": "single"},
    )
    assert r["coverage"] == pytest.approx(2 / 3)
    assert r["coverage_verified"] == pytest.approx(1 / 3)
    assert r["source_mismatches"] == {"a.py": 1}


def test_frozen_source_text_survives_workspace_changes(tmp_path):
    inv = inventory(tmp_path)
    inv["files"]["a.py"]["snapshot_text"] = "x = 1\ny = 2\nz = 3\n"
    (tmp_path / "a.py").write_text("changed entirely")
    r = measure(
        {"codebase_root": str(tmp_path)},
        [tool(1, "1  x = 1")],
        inv,
        [],
        {},
        {"1": "single"},
    )
    assert r["coverage_verified"] == pytest.approx(1 / 3)
    assert not r["source_mismatches"]


def test_mechanism_source_blocks_keep_files_separate_and_exclude_execution():
    t = tool(
        1,
        '### SOURCE "a.py" lines 1-2\n1  x = 1\n### SOURCE "b.py" lines 1-1\n1  b = 2',
        function="trace_mechanism",
    )
    assert source_blocks(t, "/root") == [("a.py", {1: "x = 1"}), ("b.py", {1: "b = 2"})]
    t["function"] = "run_bash"
    assert source_blocks(t, "/root") == []
    t["function"] = "read_file"
    t["failed"] = True
    assert source_blocks(t, "/root") == []


def test_within_codebase_correlation_can_reverse_pooled_association():
    rows = [
        {
            "sample_id": sid,
            "variant": "v",
            "x": x,
            "captured": y,
            "eligible": True,
            "trace_complete": True,
        }
        for sid, x, y in [
            ("a", 0, False),
            ("a", 1, False),
            ("a", 2, False),
            ("b", 8, True),
            ("b", 9, True),
            ("b", 10, True),
        ]
    ]
    assert association(rows, "x")["r"] > 0.9
    assert association(rows, "x", ("sample_id",))["r"] is None
    rows[1]["trace_complete"] = False
    rows[2]["eligible"] = False
    assert association(rows, "x")["n"] == 4


def test_worker_names_in_both_protocol_generations_are_recognised():
    for name in ["hypothesis_worker/1", "hypothesis_worker_H1"]:
        logged = SimpleNamespace(
            events=[
                SimpleNamespace(event="span_begin", id="w", parent_id=None, name=name),
                SimpleNamespace(event="tool", span_id="w", function="read_file"),
            ]
        )
        assert owner_map(
            logged, [{"ordinal": 3, "event_index": 1, "function": "read_file"}]
        ) == {"3": name}


def write(path, value):
    path.write_text(json.dumps(value))


def inputs(tmp_path):
    inv = inventory(tmp_path)
    bundle = {
        "number": 1,
        "observation": "literal </script> content",
        "excerpts": [{"path": "a.py", "start_line": 1, "end_line": 1, "text": "x = 1"}],
        "known_issue_matches": [],
    }
    trace = {"tools": [tool(1, "1  x = 1")], "model_calls": 1}
    write(tmp_path / "trace.json", trace)
    reference = {"record": {"mechanism": "altered x"}, "usable": True}
    sample = {
        "key": "key",
        "id": "sample",
        "epoch": 1,
        "codebase_root": str(tmp_path),
        "trace_file": "trace.json",
        "trace_sha256": fingerprint(trace),
        "sabotage_reference": reference,
        "trace_status": "observed",
        "log_sha256": "log1",
        "run": "source.eval",
        "metrics": {"bundles": [bundle]},
    }
    review = {
        "sabotage_reference_sha256": fingerprint(reference),
        "included": "partial",
        "single_bundle_complete": False,
        "matching_bundles": [1],
        "sufficient_bundles": [],
        "summary": "original",
        "bundle_reviews": {
            "1": {
                "bug_status": "no",
                "sabotage_candidate": "yes",
                "paper_alignment": "consistent",
                "topic": "example",
                "reason": "reviewed",
            }
        },
    }
    write(
        tmp_path / "analysis.json",
        {
            "manifest": {
                "analysis_id": "test",
                "samples_sha256": fingerprint([sample]),
                "runs": [],
            },
            "samples": [sample],
        },
    )
    write(tmp_path / "review.json", {"analysis_id": "test", "samples": {"key": review}})
    write(
        tmp_path / "source.json",
        {
            "variants": [{"id": "source_v"}],
            "samples": [{"key": "key", "variant": "source_v", "tools": trace["tools"]}],
        },
    )
    write(tmp_path / "inventory.json", {"samples": {"sample": inv}})
    write(
        tmp_path / "units.json",
        {
            "samples": {
                "sample": {
                    "regions": [{"path": "a.py", "start": 1, "end": 1}],
                    "mechanism": "altered x",
                }
            }
        },
    )
    audit_row = {
        "included": "yes",
        "matching_bundles": [1],
        "related_bundles": [1],
        "bundles_sha256": fingerprint([bundle]),
        "reference_sha256": fingerprint(reference),
        "original_review_sha256": fingerprint(review),
        "reason": "explicit re-review",
    }
    write(tmp_path / "audit.json", {"policy": {}, "samples": {"v:key": audit_row}})
    config = {
        "schema_version": 1,
        "title": "test",
        "target_review": "audit.json",
        "target_units": "units.json",
        "inventory": "inventory.json",
        "variants": [
            {
                "id": "v",
                "label": "variant",
                "analysis": "analysis.json",
                "review": "review.json",
                "results": "source.json",
                "source_variant": "source_v",
            }
        ],
    }
    write(tmp_path / "config.json", config)
    return tmp_path / "config.json"


def test_audited_capture_preserves_original_and_embeds_html_safely(tmp_path):
    config = inputs(tmp_path)
    original = (tmp_path / "review.json").read_bytes()
    result = build_report(config, tmp_path / "out")
    assert result["summary"]["overall"]["caught"] == 1
    assert result["summary"]["overall"]["original_caught"] == 0
    assert result["samples"][0]["complete"] is False
    assert (tmp_path / "review.json").read_bytes() == original
    page = (tmp_path / "out/report.html").read_text()
    assert "literal </script> content" not in page
    assert "__DATA__" not in page
    assert "<script src=" not in page and "fetch(" not in page


@pytest.mark.parametrize("change", ["bundle", "reference", "review"])
def test_stale_semantic_audit_is_rejected(tmp_path, change):
    config = inputs(tmp_path)
    path = tmp_path / "audit.json"
    audit = json.loads(path.read_text())
    field = {
        "bundle": "bundles_sha256",
        "reference": "reference_sha256",
        "review": "original_review_sha256",
    }[change]
    audit["samples"]["v:key"][field] = "different"
    write(path, audit)
    with pytest.raises(ValueError, match="stale|changed"):
        build_data(config)


def test_zero_candidate_outputs_and_ragged_epochs_stay_in_denominators():
    rows = []
    for variant, epoch, caught, zero in [
        ("a", 1, True, False),
        ("a", 2, False, True),
        ("b", 4, True, False),
    ]:
        rows.append(
            {
                "variant": variant,
                "sample_id": "s",
                "epoch": epoch,
                "captured": caught,
                "original_included": "yes" if caught else "no",
                "eligible": True,
                "parsed": True,
                "trace_complete": True,
                "row_id": f"{variant}{epoch}",
                "stage": "miss",
                "no_qualifying_evidence": zero,
                "bundles": [],
                "attention_lines": {},
            }
        )
    result = aggregate(rows, [{"id": "a"}, {"id": "b"}])
    assert result["overall"]["eligible"] == 3
    assert result["overall"]["zero_candidates"] == 1
    h = result["hotspots"][0]
    assert h["runs_any"] == 2
    assert h["runs_all"] == 1
    assert h["cells"][1]["attempts"] == 1


@pytest.mark.parametrize(
    "change, message",
    [
        ("status", "capture status"),
        ("related", "missing related"),
        ("binary", "implementation region"),
    ],
)
def test_invalid_audit_and_reference_inputs_are_rejected(tmp_path, change, message):
    config = inputs(tmp_path)
    if change == "binary":
        path = tmp_path / "inventory.json"
        value = json.loads(path.read_text())
        value["samples"]["sample"]["files"]["a.py"]["lines"] = None
    else:
        path = tmp_path / "audit.json"
        value = json.loads(path.read_text())
        row = value["samples"]["v:key"]
        if change == "status":
            row["included"] = "typo"
            row["matching_bundles"] = []
        else:
            row["related_bundles"] = [99]
    write(path, value)
    with pytest.raises(ValueError, match=message):
        build_data(config)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")
def test_interaction_checker_accepts_an_arbitrary_cohort_from_another_directory(
    tmp_path,
):
    config = inputs(tmp_path)
    out = tmp_path / "report"
    data = build_report(config, out)
    summary = tmp_path / "summary.tsx"
    write_canvas(data, summary, out / "report.html")
    assert "__CANVAS_DATA__" not in summary.read_text()
    assert "12–19 October 2026" not in summary.read_text()
    checker = (
        Path(__file__).resolve().parents[1] / "scripts/check_extraction_cross_run.cjs"
    )
    result = subprocess.run(
        ["node", str(checker), str(out / "results.json")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["sample_selections"] == 1
