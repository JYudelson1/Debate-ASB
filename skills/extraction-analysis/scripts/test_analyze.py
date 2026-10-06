"""Tests for trace attribution, historical exposure and coverage denominators."""

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from analyze import (
    classification_basis,
    enrich_review_locations,
    extraction_trace,
    fingerprint,
    measure,
    totals,
    validate_review,
    verify_excerpt,
)


def tool(n, function, result, **args):
    return {
        "ordinal": n,
        "function": function,
        "result": result,
        "arguments": args,
        "path": args.get("path"),
        "failed": False,
        "truncated": None,
    }


def trace(tools):
    return {
        "tools": tools,
        "readable_reasoning": [],
        "redacted_blocks": 0,
        "model_calls": 1,
    }


def test_measures_returned_lines_and_distinguishes_search_listing():
    tools = [
        tool(1, "list_files", "a.py  (30 bytes)\nb/  (20 files)"),
        tool(
            2,
            "read_file",
            "     7  x = 1\n     8  y = 2\n[a.py: showed lines 7-8 of 8]",
            path="a.py",
            start_line=7,
            num_lines=300,
        ),
        tool(3, "read_file", "     8  y = 2", path="a.py"),
        tool(4, "search", "b.py:42: value = 10", pattern="value"),
    ]
    m = measure(trace(tools), [], None, None, {})
    assert m["read_presentations"] == 3
    assert m["unique_read_lines"] == 2
    assert m["unique_exposed_lines"] == 3
    assert m["repeat_read_share"] == pytest.approx(1 / 3)
    assert m["flow"]["listed_files"] == 1
    assert m["flow"]["read_files"] == 1
    assert m["flow"]["search_files"] == 1
    assert next(f for f in m["files"] if f["path"] == "b.py")["read_calls"] == 0


def test_failed_binary_and_clipped_outputs_do_not_fabricate_source():
    failed = tool(1, "read_file", "     1  x = 1", path="a.py")
    failed["failed"] = True
    m = measure(
        trace(
            [
                failed,
                tool(2, "read_file", "x.pt is a binary file (100 bytes).", path="x.pt"),
            ]
        ),
        [],
        None,
        None,
        {},
    )
    assert m["read_presentations"] == 0
    assert m["failed_tools"] == [1]
    clipped = "value [... line truncated, 5,000 chars]"
    e = {"path": "a.py", "text": clipped}
    assert (
        verify_excerpt(
            e, [tool(3, "read_file", f"     1  {clipped}", path="a.py")], None
        )["status"]
        == "unverified"
    )


def test_verifies_exact_text_and_recovers_stale_line_hints():
    tools = [tool(1, "read_file", "     9      x = 1\n    10      y = 2", path="a.py")]
    excerpt = {"path": "a.py", "text": "    x = 1\n    y = 2", "start_line": 90}
    v = verify_excerpt(excerpt, tools, None)
    assert v["status"] == "verified_read" and v["actual_start"] == 9
    assert v["line_hint_matches"] is False
    assert (
        verify_excerpt({**excerpt, "text": "    x = 10"}, tools, None)["status"]
        == "unverified"
    )
    assert (
        verify_excerpt(
            excerpt, [tool(2, "search", "a.py:9:     x = 1\na.py:10:     y = 2")], None
        )["status"]
        == "search_only"
    )


def test_extract_stage_only_and_no_encrypted_reasoning():
    spans = [
        NS(event="span_begin", id="x", parent_id=None, name="extraction", span_id=None),
        NS(
            event="span_begin",
            id="d",
            parent_id=None,
            name="bundle/1/judgment",
            span_id=None,
        ),
    ]

    def event(span, function):
        return NS(
            event="tool",
            span_id=span,
            arguments={"path": "a.py"},
            result="     1  x = 1",
            timestamp="now",
            function=function,
            error=None,
            failed=False,
            pending=None,
            truncated=None,
        )

    content = [
        {"type": "reasoning", "reasoning": "secret-ciphertext", "redacted": True},
        {"type": "reasoning", "reasoning": "inspect epoch config", "redacted": False},
    ]
    model = NS(
        event="model",
        model="example/model",
        span_id="x",
        output=NS(choices=[NS(message=NS(content=content))]),
    )
    sample = NS(
        metadata={},
        events=spans + [event("x", "read_file"), event("d", "read_file"), model],
    )
    t = extraction_trace(sample)
    assert len(t["tools"]) == 1
    assert t["redacted_blocks"] == 1
    assert t["readable_reasoning"] == [
        {"event_index": 4, "kind": "reasoning", "text": "inspect epoch config"}
    ]
    assert "secret-ciphertext" not in str(t)


def ledger():
    a = {
        "manifest": {"analysis_id": "run-hash"},
        "samples": [
            {
                "key": "k",
                "id": "sample",
                "target": "sabotaged",
                "trace_status": "observed",
                "annotation_conflict": False,
                "metrics": {"bundles": [{"number": 1}], "sequence": [{"tool": 1}]},
            }
        ],
    }
    r = {
        "analysis_id": "run-hash",
        "samples": {
            "k": {
                "included": "yes",
                "matching_bundles": [1],
                "sufficient_bundles": [1],
                "single_bundle_complete": True,
                "observation_explains": "yes",
                "failure_stage": "none",
                "trace_refs": [1],
                "summary": "Contains the bug.",
            }
        },
    }
    return a, r


def test_semantic_review_is_not_generated_by_path_overlap():
    context = {
        "normalized_location": {
            "sabotage_locations": [{"path": "a.py", "line_ranges": []}]
        }
    }
    bundles = [
        {
            "number": 1,
            "observation": "A file.",
            "excerpts": [{"path": "a.py", "text": "x = 1"}],
        }
    ]
    m = measure(
        trace([tool(1, "read_file", "     1  x = 1", path="a.py")]),
        bundles,
        context,
        None,
        {},
    )
    assert m["targets"][0]["cited_bundles"] == [1]
    assert "included" not in m


def test_denominators_exclude_unknown_mismatch_and_honest():
    def s(status, complete):
        return {"review": {"included": status, "single_bundle_complete": complete}}

    result = totals(
        [
            s("yes", True),
            s("yes", False),
            s("partial", False),
            s("no", False),
            s("mismatch", None),
            s("unknown", None),
            s("not_applicable", None),
        ]
    )
    assert result["captured"] == 2 and result["eligible"] == 4
    assert result["single_bundle"] == 1 and result["sufficiency_eligible"] == 4
    assert result["excluded"] == 3


def test_rejects_stale_review_and_nonexistent_bundles():
    a, r = ledger()
    validate_review(a, r)
    other = deepcopy(r)
    other["analysis_id"] = "wrong"
    with pytest.raises(ValueError, match="another analysis"):
        validate_review(a, other)
    other = deepcopy(r)
    other["samples"]["k"]["matching_bundles"] = [2]
    with pytest.raises(ValueError, match="bundle reference"):
        validate_review(a, other)


def test_unknown_trace_cannot_establish_non_exposure():
    a, r = ledger()
    a["samples"][0]["trace_status"] = "unavailable"
    r["samples"]["k"]["failure_stage"] = "not_exposed"
    with pytest.raises(ValueError, match="Missing trace"):
        validate_review(a, r)


def test_override_roles_are_used():
    m = measure(
        trace([tool(1, "read_file", "     1  x = 1", path="main.py")]),
        [],
        None,
        None,
        {"main.py": "model/training"},
    )
    assert m["categories"] == {"model/training": 1}


def test_modified_measurements_cannot_reuse_the_review():
    a, r = ledger()
    a["manifest"]["samples_sha256"] = fingerprint(a["samples"])
    validate_review(a, r)
    a["samples"][0]["metrics"]["bundles"][0]["number"] = 2
    with pytest.raises(ValueError, match="measurements changed"):
        validate_review(a, r)


def test_repeated_epochs_remain_separate_units():
    a, r = ledger()
    second = deepcopy(a["samples"][0])
    second["key"] = "epoch2"
    a["samples"].append(second)
    r["samples"]["epoch2"] = deepcopy(r["samples"]["k"])
    validate_review(a, r)
    merged = [{**s, "review": r["samples"][s["key"]]} for s in a["samples"]]
    assert totals(merged)["eligible"] == 2


def location_ledger():
    a, r = ledger()
    a["samples"][0]["metrics"]["sequence"] = [
        {
            "tool": 1,
            "function": "read_file",
            "failed": False,
            "exposed": {"a.py": [9, 10]},
        }
    ]
    r["schema_version"] = 2
    r["samples"]["k"].update(
        exposure_status="yes",
        exposure_tool_refs=[1],
        exposure_summary="The original read contains the mechanism.",
        locations_used=[
            dict(
                path="a.py",
                start_line=9,
                end_line=10,
                tool_refs=[1],
                source="runtime_read",
                assessment="confirmed",
                mechanism="Contains the bug.",
                annotation_relation="relocated",
                annotation_note="Original hint was stale.",
            )
        ],
    )
    return a, r


def test_reviewed_ranges_must_be_returned_not_merely_requested():
    a, r = location_ledger()
    validate_review(a, r)
    r["samples"]["k"]["locations_used"][0]["end_line"] = 11
    with pytest.raises(ValueError, match="lines were not returned"):
        validate_review(a, r)


def test_annotation_cannot_establish_historical_mechanism_exposure():
    a, r = location_ledger()
    location = r["samples"]["k"]["locations_used"][0]
    location.update(source="annotation_only", tool_refs=[], assessment="unverified")
    r["samples"]["k"]["exposure_tool_refs"] = []
    with pytest.raises(ValueError, match="needs tool references"):
        validate_review(a, r)
    r["samples"]["k"].update(
        exposure_status="unknown", exposure_summary="Only the annotation is available."
    )
    validate_review(a, r)


def test_search_and_read_location_references_are_distinct():
    a, r = location_ledger()
    r["samples"]["k"]["locations_used"][0]["source"] = "runtime_search"
    with pytest.raises(ValueError, match="wrong or failed tool"):
        validate_review(a, r)


def test_checked_location_text_is_literal_and_limited_to_reviewed_range():
    _, r = location_ledger()
    tools = [
        tool(
            1,
            "read_file",
            "     8  other\n     9      x = 1\n    10      y = 2",
            path="a.py",
        )
    ]
    enriched = enrich_review_locations(r["samples"]["k"], trace(tools), None)
    assert enriched[0]["returned_excerpt"] == "9:     x = 1\n10:     y = 2"
    assert enriched[0]["assessment"] == "confirmed"  # Saved agent judgment.


def test_role_provenance_uses_saved_rules_and_explicit_overrides():
    manifest = {
        "role_rules": [[r"\.py$", "model/training"]],
        "role_overrides": {"main.py": "analysis/plots"},
    }
    assert classification_basis("main.py", manifest) == {
        "method": "explicit override",
        "rule": "main.py",
        "role": "analysis/plots",
    }
    assert (
        classification_basis("other.py", manifest)["method"]
        == "filename/path heuristic"
    )
    assert classification_basis("other.dat", manifest)["method"] == "heuristic fallback"
