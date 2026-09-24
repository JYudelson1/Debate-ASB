"""Code execution: run_bash in a Docker sandbox holding a read-only copy of the codebase."""

import subprocess

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


@needs_asb
def test_task_with_code_execution(script, fake_sandbox, tmp_path):
    script(
        [
            tool_call("run_bash", command="python -c 'print(1)'"),
            say("Sabotage Credence: 40%"),
        ]
    )
    task = lone_judge(sample_ids="hop_jump", code_execution=True)
    [sample] = task.dataset
    assert sample.files == {
        "/workspace": asb_samples(["hop_jump"])[0].metadata["artifacts"]["codebase"][
            "root"
        ]
    }
    assert task.sandbox.type == "docker" and task.sandbox.config.endswith(
        "docker/compose.yaml"
    )

    # Run it without Docker: the fake sandbox stands in.
    task.sandbox = None
    for s in task.dataset:
        s.files = None
    log = run(task, tmp_path)
    [sample] = log.samples
    [setup, command] = fake_sandbox.calls
    assert (
        setup["user"] == "root" and "chmod -R a+rX,a-w /workspace" in setup["cmd"][-1]
    )
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
def test_real_sandbox(script, tmp_path):
    commands = [
        "ls",
        "ls CLAUDE.md",
        "touch /workspace/x",
        "echo scratch > ~/notes && cat ~/notes",
        "python -c \"import urllib.request; urllib.request.urlopen('http://1.1.1.1', timeout=5)\"",
        "python -c 'import numpy, pandas; print(numpy.__version__)'",
        "python -c 'import torch, transformers; print(torch.cuda.is_available())'",
        "sleep 75; echo finished",
    ]
    script(
        [tool_call("run_bash", command=c) for c in commands]
        + [say("Sabotage Credence: 50%")]
    )
    log = run(lone_judge(sample_ids="lazy_alpha", code_execution=True), tmp_path)
    [sample] = log.samples
    results = [
        m.text for m in model_calls(sample, "judge")[-1].input if m.role == "tool"
    ]
    ls, claude_md, touch, scratch, network, numpy, torch, sleep = results
    assert "PAPER.md" in ls and "CLAUDE.md" not in ls
    assert "No such file" in claude_md
    assert "Permission denied" in touch or "Read-only" in touch
    assert "scratch" in scratch
    assert "[exit code 0]" not in network  # no network
    assert "[exit code 0]" in numpy
    assert "[exit code 0]" in torch and "False" in torch  # CPU-only torch
    assert "killed after 60 seconds" in sleep and "finished" not in sleep
