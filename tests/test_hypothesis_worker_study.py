import json

import pytest
from conftest import say, tool_call
from inspect_ai import eval
from inspect_ai.dataset import Sample
from inspect_ai.log import resolve_sample_attachments

from debate_asb.models import ModelSpec, Participant, Side
from debate_asb.protocols.hypothesis_worker_study import (
    HypothesisWorkerStudy,
    allocate_worker_steps,
)
from debate_asb.task import protocol_task

BUNDLE = "# Bundle 1\nObservation: The function returns zero.\n\n## Excerpt\npath: f.py\nlines: 1-2\n```text\ndef f():\n    return 0\n```"


def hypotheses(count):
    return [
        {
            "hypothesis": f"mechanism {i}",
            "starting_paths": ["f.py"],
            "paper_requirement": "unverified",
        }
        for i in range(count)
    ]


def run_case(tmp_path, **options):
    workspace = tmp_path / "code"
    workspace.mkdir()
    (workspace / "f.py").write_text("def f():\n    return 0\n")
    (workspace / "PAPER.md").write_text("Measure the return value.\n")
    model = ModelSpec("openai/gpt-6-luna", "openai")
    protocol = HypothesisWorkerStudy(
        extractor=Participant(model),
        sabotaged=Participant(model, side=Side.SABOTAGED),
        clean=Participant(model, side=Side.CLEAN),
        judge=Participant(model),
        stop_after="extraction",
        variant="hypothesis_workers",
        **options,
    )
    task = protocol_task(
        protocol,
        [
            Sample(
                id="fixture",
                input="Inspect the implementation.",
                target="sabotaged",
                metadata={"artifacts": {"codebase": {"root": str(workspace)}}},
            )
        ],
    )
    [log] = eval(
        task, model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none"
    )
    assert log.status == "success", log.error
    assert log.samples is not None
    sample = resolve_sample_attachments(log.samples[0])
    assert sample.error is None, sample.error
    return sample


@pytest.mark.parametrize(
    "plan,count,expected",
    [
        (6, 6, [9] * 6),
        (6, 10, [6] * 4 + [5] * 6),
        (12, 6, [8] * 6),
        (12, 10, [5] * 8 + [4] * 2),
    ],
)
def test_exact_turn_allocation(plan, count, expected):
    assert allocate_worker_steps(60, plan, count) == expected
    assert plan + sum(expected) == 60


def test_more_hypotheses_retains_ten_outputs_and_correct_prompts(script, tmp_path):
    script([say(json.dumps(hypotheses(10)))] + [say(BUNDLE)] * 10)
    sample = run_case(tmp_path, n_bundles=10, plan_steps=6, extractor_max_steps=60)
    extraction = sample.store["stages"]["extraction"]
    assert len(extraction["bundles"]) == 10
    calls = [e for e in sample.events if e.event == "model"]
    assert "JSON array of 10" in calls[0].input[0].text
    assert all("up to 6 rounds" in c.input[0].text for c in calls[1:5])
    assert all("up to 5 rounds" in c.input[0].text for c in calls[5:])
    assert all("EXACTLY 1 evidence bundle" in c.input[0].text for c in calls[1:])


def spawn():
    return tool_call(
        "spawn_hypothesis_worker",
        hypothesis="different mechanism",
        paper_requirement="Measure return value",
        source_path="f.py",
        start_line=1,
        end_line=2,
        source_quote="return 0",
        mechanism_reason="A distinct transformation affects the measurement.",
    )


@pytest.mark.parametrize("share,parent_read_count", [(True, 3), (False, 8)])
def test_child_allocation_keeps_separate_bundle_and_cannot_recurse(
    script, tmp_path, share, parent_read_count
):
    child_reads = [tool_call("read_file", path="f.py")] * 5
    parent_reads = [tool_call("read_file", path="f.py")] * parent_read_count
    script(
        [
            say(json.dumps(hypotheses(6))),
            spawn(),
            *child_reads,
            say(BUNDLE),
            *parent_reads,
            say(BUNDLE),
            *[say(BUNDLE)] * 5,
        ]
    )
    sample = run_case(
        tmp_path,
        n_bundles=6,
        allow_children=True,
        extractor_max_steps=60,
        children_share_parent_steps=share,
    )
    extraction = sample.store["stages"]["extraction"]
    assert len(extraction["bundles"]) == 7
    assert sample.store["child_decisions"][0]["used_turns"] == 5
    assert (
        len([e for e in sample.events if e.event == "model"]) == 14 + parent_read_count
    )
    spans = {e.id: e.name for e in sample.events if e.event == "span_begin"}
    # Child calls declare only artifact tools; parent tool execution nests their spans.
    child_calls = [
        e
        for e in sample.events
        if e.event == "model" and "up to 5 rounds" in e.input[0].text
    ]
    assert len(child_calls) == 6  # five inspection calls, one forced answer
    assert all(
        "spawn_hypothesis_worker" not in [t.name for t in c.tools] for c in child_calls
    )
    assert any(name == "hypothesis_child/1" for name in spans.values())
    assert [b["number"] for b in extraction["bundles"]] == list(range(1, 8))


def test_second_child_request_is_rejected(script, tmp_path):
    script(
        [
            say(json.dumps(hypotheses(6))),
            spawn(),
            say(BUNDLE),
            spawn(),
            say(BUNDLE),
            *[say(BUNDLE)] * 5,
        ]
    )
    sample = run_case(tmp_path, n_bundles=6, allow_children=True)
    decisions = sample.store["child_decisions"]
    assert [d["accepted"] for d in decisions] == [True, False]
    assert len(sample.store["stages"]["extraction"]["bundles"]) == 7
