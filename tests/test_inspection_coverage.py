import pytest

from debate_asb.extraction_analysis.coverage import (
    artifact_role,
    build_inventory,
    observed_coverage,
)


@pytest.fixture
def inventory(tmp_path):
    (tmp_path / "train.py").write_text("x = 1\ny = 2\nprint(x + y)\n")
    (tmp_path / "PAPER.md").write_text("# Paper\nA relevant statement.\n")
    (tmp_path / "config.yaml").write_text("seed: 42\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/large.json").write_text("x" * 6000 + "\n")
    (tmp_path / "data/cache.pt").write_bytes(b"\x00opaque")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/result.txt").write_text("one\ntwo\n")
    (tmp_path / "CLAUDE.md").write_text("hidden")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__/hidden.py").write_text("hidden")
    return build_inventory(tmp_path)


def tool(function, result, **arguments):
    return {
        "function": function,
        "result": result,
        "arguments": arguments,
        "failed": False,
    }


def test_inventory_separates_all_requested_artifacts_and_hides_instructions(inventory):
    files = inventory["files"]
    assert set(files) == {
        "train.py",
        "PAPER.md",
        "config.yaml",
        "data/large.json",
        "data/cache.pt",
        "logs/result.txt",
    }
    assert files["PAPER.md"]["role"] == "paper_docs"
    assert (
        files["data/large.json"]["role"] == files["data/cache.pt"]["role"] == "datasets"
    )
    assert files["logs/result.txt"]["role"] == "logs_results"
    assert files["data/cache.pt"]["lines"] is None
    assert files["data/large.json"]["characters"] == 6000
    paper = observed_coverage(inventory, [])["final"]["paper"]
    assert paper["files"] == 1 and paper["text_lines"] == 2
    assert (
        artifact_role("experiments/gemma/generations/all/generated.json") == "datasets"
    )
    assert (
        artifact_role("experiments/gemma/cached_activations/filtered/generated.json")
        == "datasets"
    )
    assert artifact_role("runs/h300_seed1.json") == "logs_results"
    assert (
        artifact_role("uv.lock")
        == artifact_role("cfgs/common/model.json")
        == "configuration"
    )


def test_listings_are_discovery_and_repeated_reads_are_unique(inventory):
    tools = [
        tool("list_files", "train.py  (29 bytes)\ndata/  (2 files)"),
        tool("read_file", "     1  x = 1\n     2  y = 2", path="train.py"),
        tool("read_file", "     1  x = 1\n     2  y = 2", path="train.py"),
        tool("search", "train.py:3: print(x + y)", pattern="print"),
    ]
    result = observed_coverage(inventory, tools)
    assert result["quartiles"][0]["by_role"]["source"]["discovered_files"] == 1
    assert result["quartiles"][0]["by_role"]["source"]["read_lines"] == 0
    final = result["final"]["source"]
    assert final["read_lines"] == 2
    assert final["read_characters"] == 10
    assert final["read_line_fraction"] == pytest.approx(2 / 3)
    assert final["read_plus_search_line_fraction"] == 1
    assert (
        result["final"]["datasets"]["discovered_files"] == 0
    )  # directory summary is not file discovery


def test_one_line_dataset_truncation_never_claims_complete_text(inventory):
    result = observed_coverage(
        inventory,
        [
            tool(
                "read_file",
                "     1  " + "x" * 2000 + " [... line truncated, 6,000 chars]",
                path="data/large.json",
            ),
            tool("search", "data/large.json:1: " + "x" * 300, pattern="x"),
        ],
    )
    data = result["final"]["datasets"]
    assert data["read_line_fraction"] == 1
    assert data["read_character_fraction"] == pytest.approx(1 / 3)
    assert data["read_plus_search_character_fraction"] == pytest.approx(1 / 3)
    assert data["truncated_read_lines"] == 1


def test_binary_discovery_leaves_content_unknown(inventory):
    result = observed_coverage(
        inventory,
        [
            tool("list_files", "data/cache.pt  (7 bytes)"),
            tool(
                "read_file",
                "data/cache.pt is a binary file (7 bytes).",
                path="data/cache.pt",
            ),
        ],
    )
    assert result["final"]["binary"]["discovered_file_fraction"] == 1
    assert result["final"]["binary"]["read_files"] == 0
    assert result["final"]["binary"]["binary_content_fraction"] is None


def test_failed_tools_outside_paths_and_invalid_ranges_give_no_content_credit(
    inventory,
):
    failed = tool("read_file", "     1  x = 1", path="train.py")
    failed["failed"] = True
    result = observed_coverage(
        inventory,
        [
            failed,
            tool("read_file", "     1  secret", path="../secret.py"),
            tool("read_file", "   100  nonexistent", path="train.py"),
        ],
    )
    assert result["final"]["all"]["read_lines"] == 0


def test_quartile_cutoffs_and_monotonic_coverage(inventory):
    calls = [tool("list_files", "train.py  (29 bytes)")] * 4 + [
        tool("read_file", "     1  x = 1", path="train.py")
    ]
    result = observed_coverage(inventory, calls)
    assert [q["tool_calls_completed"] for q in result["quartiles"]] == [2, 3, 4, 5]
    assert [q["by_role"]["source"]["read_lines"] for q in result["quartiles"]] == [
        0,
        0,
        0,
        1,
    ]
    empty = observed_coverage(inventory, [])
    assert all(q["tool_calls_completed"] == 0 for q in empty["quartiles"])
    assert empty["final"]["all"]["read_lines"] == 0
