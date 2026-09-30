from pathlib import Path

import yaml

from debate_asb.datasets.sabotage_locations import normalize_asb, normalize_location


def write_rubric(asb_root: Path) -> Path:
    rubric_dir = asb_root / "codebases/_rubrics"
    rubric_dir.mkdir(parents=True)
    workspace = asb_root / "codebases/example/sabotaged"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src/bug.py").write_text("\n" * 100)
    (workspace / "PAPER.md").write_text("paper\n")
    (rubric_dir / "example.yaml").write_text(
        yaml.safe_dump(
            {
                "sabotaged": [
                    {
                        "id": "example_bad",
                        "workspace": "codebases/example/sabotaged/",
                        "sabotage": {
                            "location": (
                                "src/bug.py:10-12, lines 20-21, line 30 (bad behavior)"
                            )
                        },
                    },
                    {
                        "id": "example_mixed",
                        "workspace": "codebases/example/sabotaged/",
                        "sabotage": {
                            "location": "bug.py lines ~40-42; PAPER.md Appendix A"
                        },
                    },
                    {
                        "id": "example_unknown",
                        "workspace": "codebases/example/sabotaged/",
                        "sabotage": {"location": "See diff"},
                    },
                ],
                "honest": [
                    {
                        "id": "example_good",
                        "workspace": "codebases/example/honest/",
                    }
                ],
            },
            sort_keys=False,
        )
    )
    return rubric_dir


def test_normalize_asb_writes_scoring_artifact_beside_rubrics(tmp_path):
    rubric_dir = write_rubric(tmp_path)

    [(output, document)] = normalize_asb(tmp_path)

    assert output == rubric_dir / "_sabotage_locations.yaml"
    assert yaml.safe_load(output.read_text()) == document
    assert set(document["samples"]) == {
        "example_bad",
        "example_mixed",
        "example_unknown",
    }

    exact = document["samples"]["example_bad"]
    assert exact["normalization_status"] == "exact"
    assert exact["manual_review_required"] is False
    assert exact["sabotage_locations"] == [
        {
            "path": "src/bug.py",
            "line_ranges": [
                {"start": 10, "end": 12, "approximate": False},
                {"start": 20, "end": 21, "approximate": False},
                {"start": 30, "end": 30, "approximate": False},
            ],
            "path_status": "present",
        }
    ]


def test_normalize_asb_preserves_ambiguity_instead_of_inventing_spans(tmp_path):
    write_rubric(tmp_path)

    [(_, document)] = normalize_asb(tmp_path)

    mixed = document["samples"]["example_mixed"]
    assert mixed["normalization_status"] == "mixed"
    assert mixed["manual_review_required"] is True
    assert mixed["sabotage_locations"][0]["path"] == "src/bug.py"
    assert mixed["sabotage_locations"][0]["line_ranges"] == [
        {"start": 40, "end": 42, "approximate": True}
    ]
    assert mixed["sabotage_locations"][1]["line_ranges"] == []

    unknown = document["samples"]["example_unknown"]
    assert unknown["normalization_status"] == "unresolved"
    assert unknown["sabotage_locations"] == []
    assert unknown["source_location"] == "See diff"
    assert unknown["manual_review_required"] is True


def test_approximation_before_line_and_repeated_missing_path_are_preserved(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    approximate = normalize_location("bug.py (~line 25)", workspace)
    assert approximate["sabotage_locations"][0]["line_ranges"] == [
        {"start": 25, "end": 25, "approximate": True}
    ]
    assert approximate["manual_review_required"] is True

    missing = normalize_location("PAPER.md:1; PAPER.md:2", workspace)
    assert missing["sabotage_locations"] == [
        {
            "path": "PAPER.md",
            "line_ranges": [
                {"start": 1, "end": 1, "approximate": False},
                {"start": 2, "end": 2, "approximate": False},
            ],
            "path_status": "missing",
        }
    ]
    assert missing["manual_review_required"] is True


def test_out_of_bounds_range_requires_review(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "short.py").write_text("one\ntwo\n")

    normalized = normalize_location("short.py:2-5", workspace)

    assert normalized["normalization_status"] == "exact"
    assert normalized["manual_review_required"] is True
    assert normalized["manual_review_reasons"] == [
        "line range 2-5 exceeds short.py (2 lines)"
    ]
