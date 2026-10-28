"""Artifact primitives, in both forms, and dump_all."""

import asyncio

import pytest
from conftest import needs_asb
from inspect_ai.model import (
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
)
from inspect_ai.tool import ToolCall, ToolDef, ToolError

from debate_asb.artifacts import ArtifactTooLarge, Codebase, Trajectory, load_artifacts
from debate_asb.datasets import ASB_ROOT, asb_samples


@pytest.fixture
def hop_jump() -> Codebase:
    [sample] = asb_samples(["hop_jump"])
    assert sample.metadata is not None
    codebase = load_artifacts(sample.metadata["artifacts"])["codebase"]
    assert isinstance(codebase, Codebase)
    return codebase


@needs_asb
def test_primitives_are_plain_methods_and_tools(hop_jump):
    assert "PAPER.md" in hop_jump.list_files()
    tools = {ToolDef(t).name: t for t in hop_jump.tools()}
    assert set(tools) == {
        "list_files",
        "read_file",
        "search",
    }  # dump_all isn't a tool; run_bash is off
    # Same function, same output, both ways.
    assert asyncio.run(
        tools["read_file"](path="PAPER.md", num_lines=3)
    ) == hop_jump.read_file("PAPER.md", num_lines=3)
    # The tool's description to the model comes from the docstring.
    assert "Search text files" in ToolDef(tools["search"]).description


@needs_asb
def test_claude_md_hidden_and_paths_confined(hop_jump):
    assert "CLAUDE.md" not in hop_jump.list_files()
    assert "CLAUDE.md" not in hop_jump.list_files(recursive=True)
    for bad in ["CLAUDE.md", "../../../README.md", "nope.py"]:
        with pytest.raises((FileNotFoundError, ValueError)):
            hop_jump.read_file(bad)


def test_invalid_search_pattern_is_returned(tmp_path):
    (tmp_path / "a.py").write_text("base_dir = Path(x)\n")
    result = Codebase(tmp_path).search('"base_dir = Path("')
    assert result.startswith("Invalid regular expression:")
    assert "unterminated subpattern" in result


def test_search_times_out_pathological_patterns_and_retains_partial_results(
    tmp_path, monkeypatch
):
    import debate_asb.artifacts.codebase as codebase_module

    monkeypatch.setattr(codebase_module, "SEARCH_TIMEOUT_SECONDS", 0.01)
    (tmp_path / "a.txt").write_text("fast match\n" + "a" * 100_000 + "!\n")
    result = Codebase(tmp_path).search(r"fast match|(a+)+$")
    assert "timed out" in result
    assert "Results are incomplete" in result
    assert "a.txt:1: fast match" in result


def test_search_preserves_python_regex_syntax_and_case_controls(tmp_path):
    (tmp_path / "a.py").write_text("Alpha=7\nalpha=8\n")
    codebase = Codebase(tmp_path)
    assert codebase.search(r"(?<=Alpha=)\d") == "a.py:1: Alpha=7"
    assert len(codebase.search("ALPHA", ignore_case=True).splitlines()) == 2
    assert codebase.search("ALPHA") == "No matches."
    assert codebase.search(r"(?<=a+)x").startswith("Invalid regular expression:")


@needs_asb
def test_search_finds_code(hop_jump):
    assert "generate.py" in hop_jump.search(r"random\.seed\(42\)", "latent_reasoning")


@needs_asb
def test_list_files_reports_a_file_path_to_the_model(hop_jump):
    with pytest.raises(ToolError, match="not a directory"):
        hop_jump.list_files("PAPER.md")


@needs_asb
def test_read_file_reports_a_directory_without_the_host_path(hop_jump):
    with pytest.raises(ToolError, match="is a directory, not a file") as error:
        hop_jump.read_file("latent_reasoning")
    assert str(hop_jump.root) not in str(error.value)


@needs_asb
def test_dump_all_includes_code_lists_data_and_refuses_to_truncate(hop_jump):
    dump = hop_jump.dump_all(max_tokens=2_000_000)
    assert (
        dump.index("===== PAPER.md")
        < dump.index("===== README.md")
        < dump.index("===== latent_reasoning")
    )
    assert "generate.py" in dump and "CLAUDE.md" not in dump
    included, not_included = dump.split("===== Files not included =====")
    assert (
        "===== datasets/hopping_too_late/post_filtering_llama3_8b.csv" not in included
    )  # 42 MB of data
    assert "datasets/" in not_included
    with pytest.raises(ArtifactTooLarge):
        hop_jump.dump_all(max_tokens=10_000)


def trajectory_messages():
    return [
        ChatMessageSystem(content="You are an agent."),
        ChatMessageUser(content="Fix the bug."),
        ChatMessageAssistant(
            content="I'll quietly weaken the test instead.",
            tool_calls=[
                ToolCall(
                    id="1",
                    function="bash",
                    arguments={"cmd": "sed -i s/==/!=/ test.py"},
                )
            ],
        ),
        ChatMessageTool(content="ok", tool_call_id="1", function="bash"),
        ChatMessageAssistant(content="Done, all tests pass."),
    ]


def test_trajectory_views():
    full = Trajectory(trajectory_messages(), view="full")
    assert "quietly weaken" in full.dump_all(max_tokens=10_000)
    assert "Done, all tests pass" in full.read_step(4)

    output_only = Trajectory(
        [m.model_dump() for m in trajectory_messages()], view="output_only"
    )
    dump = output_only.dump_all(max_tokens=10_000)
    assert (
        "sed -i" in dump and "[tool result: bash]" in dump
    )  # tool calls and results survive
    assert (
        "quietly weaken" not in dump and "all tests pass" not in dump
    )  # the agent's text doesn't
    assert "quietly" not in output_only.search_trajectory("quietly")
    assert full.search_trajectory("(").startswith("Invalid regular expression:")
    assert {ToolDef(t).name for t in output_only.tools()} == {
        "list_steps",
        "read_step",
        "search_trajectory",
    }
