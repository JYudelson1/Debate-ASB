import json

import pytest
from conftest import say
from inspect_ai import eval
from inspect_ai.dataset import Sample

from debate_asb.end_to_end.study import closure_protocol
from debate_asb.protocols.evidence_closure import allocate_worker_steps, validate_plan
from debate_asb.task import protocol_task

BUNDLE = """# Bundle 1
Observation: The function returns zero.

## Excerpt
path: f.py
lines: 1-2
```text
def f():
    return 0
```
"""


def test_historical_allocation_and_prompt_contract():
    protocol = closure_protocol()
    assert allocate_worker_steps(114, 6, 20) == [6] * 8 + [5] * 12
    original, plan, workers = protocol.prompts_for_extraction()
    assert "up to 114 rounds" in original
    assert "up to 6 rounds" in plan
    assert "six distinct" not in plan
    assert "20 distinct" in plan
    assert all(
        "1 through 1" in p and "Standalone evidence closure" in p for p in workers
    )
    assert all("up to 6 rounds" in p for p in workers[:8])
    assert all("up to 5 rounds" in p for p in workers[8:])
    with pytest.raises(ValueError):
        validate_plan("[]", 20)
    with pytest.raises(ValueError):
        allocate_worker_steps(5, 6, 20)
    hypotheses = {
        f"h{i:02}": {
            "hypothesis": str(i),
            "starting_paths": [],
            "paper_requirement": "unspecified",
        }
        for i in range(1, 21)
    }
    assert len(validate_plan(json.dumps(hypotheses), 20)) == 20
    hypotheses["h21"] = hypotheses["h20"]
    with pytest.raises(ValueError, match="exactly 20"):
        validate_plan(json.dumps(hypotheses), 20)


def test_failed_worker_reuses_valid_stages_without_repair(script, tmp_path):
    root = tmp_path / "codebase"
    root.mkdir()
    (root / "f.py").write_text("def f():\n    return 0\n")
    sample = Sample(
        id="test",
        input="Audit.",
        target="honest",
        metadata={"setting": "asb", "artifacts": {"codebase": {"root": str(root)}}},
    )
    protocol = closure_protocol(
        stop_after="extraction",
        checkpoint_dir=tmp_path / "checkpoints",
        worker_parallelism=1,
    )
    hypotheses = [
        {
            "hypothesis": str(i),
            "starting_paths": ["f.py"],
            "paper_requirement": "unspecified",
        }
        for i in range(20)
    ]
    worker_outputs = [BUNDLE] * 20
    worker_outputs[4] = "A neutral preface.\n\n" + BUNDLE
    script(
        [say(json.dumps(hypotheses))]
        + [say(raw) for raw in worker_outputs[:19]]
        + [say("malformed")]
    )
    [failed] = eval(
        protocol_task(protocol, [sample]),
        model="mockllm/model",
        log_dir=str(tmp_path / "first"),
        display="none",
        fail_on_error=False,
    )
    assert failed.samples and failed.samples[0].error
    assert (tmp_path / "checkpoints/test/1/coordinator.json").exists()
    assert (tmp_path / "checkpoints/test/1/worker_1.json").exists()
    assert not (tmp_path / "checkpoints/test/1/worker_20.json").exists()
    protocol.coordinator_format = "named_slots"
    script([say(BUNDLE)])
    [passed] = eval(
        protocol_task(protocol, [sample]),
        model="mockllm/model",
        log_dir=str(tmp_path / "second"),
        display="none",
    )
    assert passed.samples and not passed.samples[0].error
    extraction = passed.samples[0].store["stages"]["extraction"]
    assert [b["number"] for b in extraction["bundles"]] == list(range(1, 21))
    assert extraction["bundles"][0]["observation"] == "The function returns zero."
    assert extraction["investigation"]["worker_raw_outputs"][4] == worker_outputs[4]
    assert all(
        b["excerpts"] == extraction["bundles"][0]["excerpts"]
        for b in extraction["bundles"]
    )
    assert len([e for e in passed.samples[0].events if e.event == "model"]) == 1
