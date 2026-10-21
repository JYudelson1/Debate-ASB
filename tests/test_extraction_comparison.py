import json
from pathlib import Path

import pytest

from debate_asb.extraction_analysis.common import fingerprint
from debate_asb.extraction_analysis.comparison import (
    build_data,
    build_report,
    heatmap,
    summarize,
)


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def variant(tmp_path, name, epochs=(1,), included="yes", text="x = 1"):
    folder = tmp_path / name
    folder.mkdir()
    reference = {
        "record": {"mechanism": "A checked target – literal source"},
        "usable": True,
    }
    samples = []
    reviews = {}
    for epoch in epochs:
        key = f"sample-{epoch}"
        trace = {
            "tools": [
                {
                    "ordinal": 1,
                    "function": "read_file",
                    "path": "a.py",
                    "arguments": {"path": "a.py"},
                    "result": f"1  {text}",
                    "failed": False,
                }
            ],
            "model_calls": 1,
        }
        write(folder / f"{key}.json", trace)
        samples.append(
            {
                "key": key,
                "id": "sample",
                "epoch": epoch,
                "trace_file": f"{key}.json",
                "trace_sha256": fingerprint(trace),
                "sabotage_reference": reference,
                "metrics": {
                    "bundles": [
                        {
                            "number": 1,
                            "observation": text,
                            "excerpts": [
                                {
                                    "path": "a.py",
                                    "start_line": 1,
                                    "end_line": 1,
                                    "text": text,
                                }
                            ],
                        }
                    ]
                },
            }
        )
        reviews[key] = {
            "included": included,
            "single_bundle_complete": included == "yes",
            "matching_bundles": [1] if included == "yes" else [],
            "sufficient_bundles": [1] if included == "yes" else [],
            "summary": "Checked exact code",
            "exposure_status": "yes",
            "sabotage_reference_sha256": fingerprint(reference),
            "bundle_reviews": {
                "1": {
                    "bug_status": "yes",
                    "sabotage_candidate": "yes",
                    "paper_alignment": "not_specified",
                    "topic": "Metric",
                    "reason": "The formula differs",
                    "basis": [],
                }
            },
        }
    analysis = {
        "manifest": {"analysis_id": name, "samples_sha256": fingerprint(samples)},
        "samples": samples,
    }
    write(folder / "analysis.json", analysis)
    write(folder / "review.json", {"analysis_id": name, "samples": reviews})
    return {
        "id": name,
        "label": name,
        "analysis": f"{name}/analysis.json",
        "review": f"{name}/review.json",
    }


def test_arbitrary_variants_and_ragged_epochs(tmp_path):
    a = variant(tmp_path, "control", epochs=(1, 2))
    b = variant(tmp_path, "new_prompt", epochs=(2, 4), included="no")
    c = variant(tmp_path, "third", epochs=(7,))
    config = write(
        tmp_path / "config.json",
        {"schema_version": 1, "baseline": "control", "variants": [a, b, c]},
    )
    data = build_report(config, tmp_path / "out")
    assert [v["attempts"] for v in data["variants"]] == [2, 2, 1]
    assert data["variants"][1]["by_sample"]["sample"]["epochs"] == [2, 4]
    assert data["comparisons"][0] == {
        "variant": "new_prompt",
        "baseline": "control",
        "common_slots": 1,
        "jointly_reviewed_slots": 1,
        "baseline_presence": 1,
        "variant_presence": 0,
    }
    assert data["comparisons"][1]["common_slots"] == 0
    single = write(tmp_path / "single.json", {"schema_version": 1, "variants": [c]})
    assert build_data(single)["comparisons"] == []


def test_capture_denominators_and_output_failures_are_distinct():
    rows = [
        {
            "sample_id": "s",
            "epoch": i,
            "included": status,
            "parsed": parsed,
            "complete": complete,
            "exposure": "unknown",
        }
        for i, (status, parsed, complete) in enumerate(
            [
                ("yes", True, True),
                ("partial", True, False),
                ("no", True, False),
                ("unknown", False, None),
                ("mismatch", True, None),
                ("not_applicable", True, None),
            ]
        )
    ]
    counts = summarize(rows)
    assert (counts["presence"], counts["eligible"], counts["attempts"]) == (1, 3, 6)
    assert (
        counts["failed"],
        counts["excluded"],
        counts["complete"],
        counts["sufficiency_eligible"],
    ) == (1, 3, 1, 3)


def test_report_embeds_data_safely_and_works_without_external_assets(tmp_path):
    payload = '</script><script>alert("x")</script>'
    spec = variant(tmp_path, "unsafe", text=payload)
    config = write(
        tmp_path / "config.json",
        {"schema_version": 1, "title": payload, "variants": [spec]},
    )
    out = tmp_path / "out"
    build_report(config, out)
    html = (out / "report.html").read_text()
    assert payload not in html
    embedded = html.split('<script type="application/json" id="report-data">', 1)[
        1
    ].split("</script>", 1)[0]
    assert json.loads(embedded)["title"] == payload
    assert (
        "<script src=" not in html and "<base " not in html and "__REPORT_" not in html
    )
    assert "fetch(" not in html


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("identity", "another analysis"),
        ("reference", "another sabotage reference"),
        ("measurements", "measurements changed"),
        ("unreviewed", "Incomplete bundle review"),
        ("trace", "Trace changed after collection"),
    ],
)
def test_invalid_or_stale_review_is_rejected(tmp_path, mutation, match):
    spec = variant(tmp_path, "v")
    review_path = tmp_path / spec["review"]
    review = json.loads(review_path.read_text())
    if mutation == "identity":
        review["analysis_id"] = "another"
    elif mutation == "reference":
        review["samples"]["sample-1"]["sabotage_reference_sha256"] = "stale"
    elif mutation == "unreviewed":
        review["samples"]["sample-1"]["bundle_reviews"]["1"]["bug_status"] = (
            "unreviewed"
        )
    elif mutation == "trace":
        trace_path = tmp_path / "v" / "sample-1.json"
        trace = json.loads(trace_path.read_text())
        trace["tools"][0]["result"] = "1  changed after collection"
        write(trace_path, trace)
    else:
        analysis_path = tmp_path / spec["analysis"]
        analysis = json.loads(analysis_path.read_text())
        analysis["samples"][0]["epoch"] = 99
        write(analysis_path, analysis)
    write(review_path, review)
    config = write(tmp_path / "config.json", {"schema_version": 1, "variants": [spec]})
    with pytest.raises(ValueError, match=match):
        build_data(config)


def test_heatmap_counts_returned_source_and_repeats_but_not_search_or_failed_reads():
    tools = [
        {
            "function": "read_file",
            "path": "a.py",
            "arguments": {},
            "result": "8  first\n9  second",
            "failed": False,
        },
        {"function": "search", "result": "a.py:10: not a full read", "failed": False},
        {
            "function": "read_file",
            "path": "a.py",
            "arguments": {},
            "result": "8  failed",
            "failed": True,
        },
        {
            "function": "trace_mechanism",
            "result": '\n### SOURCE "a.py" selected\n9  second\n10  third\n',
            "failed": False,
        },
    ]
    result = heatmap({"tools": tools})
    assert result["files"][0]["total"] == 4
    assert sum(p["new_lines"] for p in result["phases"]) == 3
    assert result["phases"][0]["calls"]["search"] == 1


@pytest.mark.parametrize("complete", [True, False])
def test_zero_candidates_stay_in_denominator_and_are_distinct_from_failures(
    tmp_path, complete
):
    spec = variant(tmp_path, "workers", included="no")
    analysis_path = tmp_path / spec["analysis"]
    review_path = tmp_path / spec["review"]
    analysis, review = (
        json.loads(analysis_path.read_text()),
        json.loads(review_path.read_text()),
    )
    sample = analysis["samples"][0]
    sample["metrics"]["bundles"] = []
    sample["extraction_complete"] = complete
    sample["no_qualifying_evidence"] = complete
    sample["investigation"] = {
        "workers": [{"hypothesis_id": "H1", "report": {"status": "inconclusive"}}]
    }
    analysis["manifest"]["samples_sha256"] = fingerprint(analysis["samples"])
    review["samples"]["sample-1"]["bundle_reviews"] = {}
    write(analysis_path, analysis)
    write(review_path, review)
    config = write(tmp_path / "config.json", {"schema_version": 1, "variants": [spec]})
    data = build_report(config, tmp_path / "out")
    counts = data["variants"][0]
    assert counts["attempts"] == 1
    assert counts["presence"] == 0
    assert counts["parsed"] == int(complete)
    assert counts["eligible"] == int(complete)
    assert counts["failed"] == int(not complete)
    assert data["samples"][0]["investigation"] == sample["investigation"]


def test_heatmap_retains_repeated_presentations_inside_one_tool_result():
    result = heatmap(
        {
            "tools": [
                {
                    "function": "read_file",
                    "path": "a.py",
                    "result": "1  x\n1  x",
                    "arguments": {},
                }
            ]
        }
    )
    assert result["files"][0]["total"] == 2
    assert result["phases"][0]["new_lines"] == 1
