"""Full pipeline runs: the guard, image pinning, spend ledger, required runs, cleanup."""

import time

import anyio
import pytest
from conftest import say

from debate_asb import pipelines
from debate_asb.models import ModelSpec, Participant
from debate_asb.pipelines import Guard, PipelineSpec
from debate_asb.protocol import agent_loop

LAUNCHER = """\
import modal

app = modal.App("paper")
image = modal.Image.debian_slim(python_version="3.11").pip_install("torch")


@app.function(gpu="T4", image=image, timeout=3600)
def run():
    return compute()


@app.local_entrypoint()
def main(n: int = 500):
    run.remote()
"""


def _guard_check(tmp_path, edited: str, guard: Guard, original: str = LAUNCHER):
    (tmp_path / "original").mkdir()
    (tmp_path / "edited").mkdir()
    (tmp_path / "original" / guard.file).write_text(original)
    (tmp_path / "edited" / guard.file).write_text(edited)
    pipelines._check_guard(guard, tmp_path / "original", tmp_path / "edited")


def test_guard_allows_edits_to_what_the_pipeline_computes(tmp_path):
    guard = Guard("run_experiments.py", decorators=True)
    _guard_check(tmp_path, LAUNCHER.replace("compute()", "compute(fixed=True)"), guard)


@pytest.mark.parametrize(
    "old,new",
    [
        ('gpu="T4"', 'gpu="H100"'),  # hardware
        ("timeout=3600", "timeout=7200"),  # time limit
        ("n: int = 500", "n: int = 5000"),  # entrypoint default (scale)
        ("    run.remote()", "    run.remote()\n    run.remote()"),  # remote calls
    ],
)
def test_guard_refuses_launch_changes(tmp_path, old, new):
    guard = Guard("run_experiments.py", decorators=True)
    with pytest.raises(ValueError):
        _guard_check(tmp_path, LAUNCHER.replace(old, new), guard)


def test_guard_refuses_scale_constants(tmp_path):
    guard = Guard("run_evaluation.py", constants=("EPOCHS",))
    original = "EPOCHS = 3\nMODELS = ['a']\n"
    with pytest.raises(ValueError, match="EPOCHS"):
        _guard_check(tmp_path, original.replace("3", "1"), guard, original)


def test_guard_allows_constants_it_does_not_name(tmp_path):
    guard = Guard("run_evaluation.py", constants=("EPOCHS",))
    original = "EPOCHS = 3\nMODELS = ['a']\n"
    _guard_check(tmp_path, original.replace("['a']", "['b']"), guard, original)


def test_pin_pip_installs_uses_the_locked_environment():
    source = 'image = modal.Image.debian_slim(python_version="3.11").pip_install("torch", "transformer_lens", "modal")\n'
    locked = [
        "torch==2.1.0",
        "transformer-lens==2.15.4",
        "transformers==4.40.0",  # a dependency: added
        "modal==0.62.0",  # the Modal client: left to Modal
        'numpy==1.26.0 ; python_version < "3.10"',  # not for this image's Python
    ]
    pinned = pipelines._pin_pip_installs(source, locked)
    assert '"torch==2.1.0"' in pinned
    assert '"transformer_lens==2.15.4"' in pinned
    assert '"transformers==4.40.0"' in pinned
    assert '"modal"' in pinned and "modal==" not in pinned
    assert "numpy" not in pinned


def test_ledger_refuses_runs_over_the_cap_and_settles_actual_cost(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(pipelines, "LEDGER", tmp_path / "spend.json")
    monkeypatch.setitem(pipelines.PROVIDER_CAPS_USD, "modal", 20.0)
    assert pipelines._Ledger.reserve("modal", 15.0)
    assert not pipelines._Ledger.reserve("modal", 10.0)  # 15 reserved + 10 > 20
    pipelines._Ledger.settle("modal", 15.0, 4.0)
    assert pipelines._Ledger.reserve("modal", 10.0)  # 4 spent + 10 <= 20


@pytest.mark.asyncio
async def test_openrouter_runs_take_turns(tmp_path, monkeypatch):
    monkeypatch.setattr(pipelines, "LEDGER", tmp_path / "spend.json")
    real_sleep = anyio.sleep
    monkeypatch.setattr(pipelines.anyio, "sleep", lambda s: real_sleep(0.01))
    spec = PipelineSpec("cmd", "d", "h", 1, "openrouter", needs_openrouter_key=True)
    spans = []

    async def run(name):
        async with pipelines._one_openrouter_run_at_a_time(spec):
            start = time.monotonic()
            await real_sleep(0.1)
            spans.append((start, time.monotonic()))

    async with anyio.create_task_group() as tg:
        tg.start_soon(run, "a")
        tg.start_soon(run, "b")
    (s1, e1), (s2, e2) = sorted(spans)
    assert s2 >= e1  # the second run started only after the first ended


def test_required_runs_send_the_judge_back_at_most_twice(monkeypatch):
    monkeypatch.setattr(pipelines, "note", lambda event, **data: None)
    runs = pipelines.Pipelines("negative_mute", pipelines.Path("."), "judge", runs=1)
    assert runs.reminder() is not None
    assert runs.reminder() is not None
    assert runs.reminder() is None  # after MAX_REMINDERS it may stop


def test_paper_app_id_is_read_from_modal_run_output():
    log = "✓ Initialized. View run at \nhttps://modal.com/apps/ws/main/ap-N1TtZrAHauoNpiMC0XESm1\n✓ Created objects."
    assert pipelines.MODAL_APP_ID.findall(log) == ["ap-N1TtZrAHauoNpiMC0XESm1"]


@pytest.mark.asyncio
async def test_agent_loop_before_stop_sends_the_participant_back(script):
    script([say("Done."), say("Done, really.")])
    reminders = iter(["You have runs left.", None])
    messages = []
    final = await agent_loop(
        Participant(ModelSpec("m", "p")),
        "judge",
        messages,
        [],
        before_stop=lambda: next(reminders),
    )
    assert final == "Done, really."
    assert any(m.role == "user" and m.text == "You have runs left." for m in messages)
