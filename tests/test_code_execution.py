"""Code execution: run_bash in a Docker sandbox holding a read-only copy of the codebase."""

import subprocess
import tarfile
from pathlib import Path

import pytest
from conftest import needs_asb, say, tool_call
from inspect_ai import eval
from inspect_ai.tool import ToolDef
from inspect_ai.util import ExecResult
from test_protocols import model_calls, run

from debate_asb import task as task_module
from debate_asb.artifacts import Codebase
from debate_asb.artifacts import codebase as codebase_module
from debate_asb.datasets import asb_samples
from debate_asb.models import Side
from debate_asb.prompts import SETTINGS
from debate_asb.protocols.lone_judge import lone_judge


def test_run_bash_is_a_tool_only_with_code_execution(tmp_path):
    names = lambda cb: {ToolDef(t).name for t in cb.tools()}  # noqa: E731
    assert "run_bash" not in names(Codebase(tmp_path))
    assert "run_bash" in names(Codebase(tmp_path, code_execution=True))


class FakeSandbox:
    """Records exec calls; answers like a sandbox would."""

    def __init__(self):
        self.calls = []

    async def exec(self, cmd, cwd=None, user=None, timeout=None, **kwargs):
        self.calls.append({"cmd": cmd, "cwd": cwd, "user": user})
        return ExecResult(success=True, returncode=0, stdout="hello\n", stderr="")


def test_participants_are_told_the_real_timeout():
    stated = f"killed after {codebase_module.COMMAND_TIMEOUT_SECONDS} seconds"
    told = [
        Codebase.run_bash.__doc__ or "",  # the tool description models see
        SETTINGS["asb"].judge("tools_execute"),
        SETTINGS["asb"].debater(Side.SABOTAGED, 2, 15, "tools_execute", True),
    ]
    assert all(stated in " ".join(text.split()) for text in told)


@pytest.fixture
def fake_sandbox(monkeypatch):
    fake = FakeSandbox()
    monkeypatch.setattr(codebase_module, "sandbox", lambda: fake)
    monkeypatch.setattr(task_module, "sandbox", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_run_bash_runs_in_workspace_as_participant_user(fake_sandbox, tmp_path):
    out = await Codebase(tmp_path, code_execution=True).run_bash("echo hello")
    assert out == "[exit code 0]\nhello\n"
    [call] = fake_sandbox.calls
    assert call == {
        "cmd": ["bash", "-c", "echo hello"],
        "cwd": "/workspace",
        "user": "auditor",
    }


@pytest.fixture
def archive_cache(monkeypatch, tmp_path):
    cache = tmp_path / "archives"
    monkeypatch.setattr(task_module, "ARCHIVE_CACHE", cache)
    return cache


def test_codebase_archive_is_cached_and_owned_by_root(archive_cache, tmp_path):
    root = tmp_path / "codebase"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "run.sh").write_bytes(b"echo hi\n")
    (root / "pkg" / "run.sh").chmod(0o755)
    (root / "CLAUDE.md").write_bytes(b"hidden later, in the sandbox\n")

    archive = task_module._codebase_archive(root)
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        assert sorted(m.name for m in members) == ["CLAUDE.md", "pkg/run.sh"]
        assert all(m.isfile() for m in members)
        assert {(m.mode, m.uid, m.gid, m.uname, m.gname) for m in members} == {
            (0o644, 0, 0, "root", "root")
        }
        run_sh = tar.extractfile("pkg/run.sh")
        assert run_sh is not None and run_sh.read() == b"echo hi\n"

    built = archive.stat().st_mtime_ns
    assert task_module._codebase_archive(root) == archive  # cached, not rebuilt
    assert archive.stat().st_mtime_ns == built
    (root / "pkg" / "run.sh").write_bytes(b"echo changed\n")
    assert task_module._codebase_archive(root) != archive
    assert len(list(archive_cache.iterdir())) == 2  # no partial files left


@needs_asb
def test_task_with_code_execution(script, fake_sandbox, archive_cache, tmp_path):
    script(
        [
            tool_call("run_bash", command="python -c 'print(1)'"),
            say("Sabotage Credence: 40%"),
        ]
    )
    task = lone_judge(sample_ids="lazy_alpha", code_execution=True)
    [sample] = task.dataset
    root = Path(
        (asb_samples(["lazy_alpha"])[0].metadata or {})["artifacts"]["codebase"]["root"]
    )
    archive = task_module._codebase_archive(root)
    assert sample.files == {"/tmp/workspace.tar.gz": str(archive)}
    with tarfile.open(archive) as tar:
        assert set(tar.getnames()) == {
            p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
        }
    assert task.sandbox is not None
    # as_posix: the config path has backslashes on Windows.
    assert task.sandbox.type == "docker" and Path(
        task.sandbox.config
    ).as_posix().endswith("docker/compose.yaml")

    # Run it without Docker: the fake sandbox stands in.
    task.sandbox = None
    for s in task.dataset:
        s.files = None
    log = run(task, tmp_path)
    assert log.samples is not None
    [sample] = log.samples
    [setup, command] = fake_sandbox.calls
    assert setup["user"] == "root"
    assert setup["cmd"][-1].startswith(
        "tar -xzf /tmp/workspace.tar.gz -C /workspace --touch && rm /tmp/workspace.tar.gz && "
    )
    assert "chmod -R a+rX,a-w /workspace" in setup["cmd"][-1]
    assert "CLAUDE.md" in setup["cmd"][-1]
    assert command["cmd"] == ["bash", "-c", "python -c 'print(1)'"]
    system = model_calls(sample, "judge")[0].input[0].text
    assert "DO NOT RUN EXPERIMENTS" in system and "`run_bash`" in system


def test_execute_prompts_are_asb_text_minus_modal():
    judge = SETTINGS["asb"].judge("tools_execute")
    assert "**Ability to run code** to inspect data and test functions" in judge
    assert "modal" not in judge.lower()
    assert "no network access and no GPU" in judge


def docker_running() -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "info"], capture_output=True, timeout=10
            ).returncode
            == 0
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@needs_asb
@pytest.mark.skipif(not docker_running(), reason="Docker isn't running")
def test_real_sandbox(script, archive_cache, tmp_path, monkeypatch):
    # A short timeout, so the test needn't wait out the real one.
    monkeypatch.setattr(codebase_module, "COMMAND_TIMEOUT_SECONDS", 5)
    commands = [
        "ls",
        "ls CLAUDE.md",
        "touch /workspace/x",
        "echo scratch > ~/notes && cat ~/notes",
        "python -c \"import urllib.request; urllib.request.urlopen('http://1.1.1.1', timeout=5)\"",
        "python -c 'import numpy, pandas; print(numpy.__version__)'",
        "python -c 'import torch, transformers; print(torch.cuda.is_available())'",
        "sleep 10; echo finished",
        "find /workspace -type f -printf '%u %m\\n' | sort | uniq -c",
        "find /workspace -type d -printf '%u %m\\n' | sort | uniq -c",
        "ls /tmp",
    ]
    script(
        [tool_call("run_bash", command=c) for c in commands]
        + [say("Sabotage Credence: 50%")]
    )
    log = run(lone_judge(sample_ids="lazy_alpha", code_execution=True), tmp_path)
    assert log.samples is not None
    [sample] = log.samples
    results = [
        m.text for m in model_calls(sample, "judge")[-1].input if m.role == "tool"
    ]
    ls, claude_md, touch, scratch, network, numpy, torch, sleep, files, dirs, tmp = (
        results
    )
    root = Path(
        (asb_samples(["lazy_alpha"])[0].metadata or {})["artifacts"]["codebase"]["root"]
    )
    shown = [
        p
        for p in root.rglob("*")
        if p.is_file()
        and not codebase_module.HIDDEN_NAMES & set(p.relative_to(root).parts)
    ]
    # Every file but the hidden ones, root-owned and read-only, as with Inspect's copy.
    assert files.split() == ["[exit", "code", "0]", str(len(shown)), "root", "444"]
    assert dirs.split()[-2:] == ["root", "555"] and len(dirs.splitlines()) == 2
    assert "workspace.tar.gz" not in tmp
    assert "PAPER.md" in ls and "CLAUDE.md" not in ls
    assert "No such file" in claude_md
    assert "Permission denied" in touch or "Read-only" in touch
    assert "scratch" in scratch
    assert "[exit code 0]" not in network  # no network
    assert "[exit code 0]" in numpy
    assert "[exit code 0]" in torch and "False" in torch  # CPU-only torch
    assert "killed after 5 seconds" in sleep and "finished" not in sleep
