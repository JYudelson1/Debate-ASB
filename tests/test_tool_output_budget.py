"""Tool outputs fit Inspect's limit themselves, so Inspect never cuts their middle."""

import re

import pytest
from conftest import needs_asb
from inspect_ai.model._call_tools import truncate_tool_output
from inspect_ai.util import ExecResult

from debate_asb.artifacts import Codebase
from debate_asb.artifacts import codebase as codebase_module
from debate_asb.artifacts.codebase import TOOL_OUTPUT_BYTES
from debate_asb.datasets import ASB_ROOT

WIDE = "x" * 1500  # 300 of these lines would be ~450 KB


def fits(output: str) -> bool:
    return truncate_tool_output("tool", output, TOOL_OUTPUT_BYTES) is None


def shown(output: str) -> list[int]:
    return [int(n) for n in re.findall(r"^\s*(\d+)  ", output, re.M)]


@pytest.fixture
def wide_file(tmp_path):
    (tmp_path / "wide.txt").write_text("\n".join(f"{i} {WIDE}" for i in range(1, 201)))
    return Codebase(tmp_path)


def test_read_file_stops_at_a_whole_line_and_says_where_to_continue(wide_file):
    out = wide_file.read_file("wide.txt")
    assert fits(out)
    lines = shown(out)
    assert lines == list(range(1, len(lines) + 1)) and len(lines) < 200
    assert out.endswith(
        f"showed lines 1-{lines[-1]} of 200; output limit reached, "
        f"continue with start_line={lines[-1] + 1}]"
    )


def test_following_the_footer_reads_every_line_exactly_once(wide_file):
    seen, start = [], 1
    while start <= 200:
        out = wide_file.read_file("wide.txt", start_line=start)
        seen += shown(out)
        start = seen[-1] + 1
    assert seen == list(range(1, 201))


def test_search_and_list_files_fit_and_say_what_they_left_out(tmp_path):
    (tmp_path / "wide.txt").write_text("\n".join(WIDE for _ in range(400)))
    for i in range(1500):
        (tmp_path / f"{'f' * 60}_{i}.txt").write_text("")
    codebase = Codebase(tmp_path)
    found = codebase.search("x", directory="wide.txt")
    assert fits(found) and "stopped after" in found
    listed = codebase.list_files()
    assert fits(listed) and "more entries not shown" in listed


@pytest.mark.asyncio
async def test_run_bash_keeps_start_and_end_of_long_output(monkeypatch, tmp_path):
    long = "START\n" + "y" * 200_000 + "\nEND"

    class Sandbox:
        async def exec(self, *args, **kwargs):
            return ExecResult(success=True, returncode=0, stdout=long, stderr="")

    monkeypatch.setattr(codebase_module, "sandbox", lambda: Sandbox())
    out = await Codebase(tmp_path, code_execution=True).run_bash("cat big")
    assert fits(out)
    assert "START" in out and out.endswith("END") and "bytes of output omitted" in out


@needs_asb
def test_every_paper_fits_in_one_read():
    for paper in (ASB_ROOT / "codebases").glob("*/*/PAPER.md"):
        codebase = Codebase(paper.parent)
        out = codebase.read_file("PAPER.md")
        total = len(paper.read_text(errors="replace").splitlines())
        assert out.endswith(f"showed lines 1-{total} of {total}]"), paper
