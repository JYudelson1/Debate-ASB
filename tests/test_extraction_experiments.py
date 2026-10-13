import json

import pytest
from conftest import say
from inspect_ai import eval
from inspect_ai.log import resolve_sample_attachments

from debate_asb.artifacts.codebase import Codebase
from debate_asb.protocols.extraction_experiments import (
    extraction_experiment,
    trace_source,
)


def test_trace_connects_definition_caller_and_config(tmp_path):
    (tmp_path / "pipeline.py").write_text(
        "SEED = 17\n"
        "def transform(values):\n    return sorted(values)\n"
        "def generate(values):\n    return transform(values)[SEED:]\n"
    )
    (tmp_path / "consumer.py").write_text(
        "from pipeline import generate\n"
        "def evaluate(values):\n    return len(generate(values))\n"
    )
    result = trace_source(Codebase(tmp_path), "pipeline.py", "generate")
    assert 'SOURCE "pipeline.py" selected' in result
    assert 'SOURCE "consumer.py" caller candidate' in result
    assert "SEED = 17" in result
    assert "return sorted(values)" in result
    assert "Static name matches only" in result


def test_trace_hides_answer_context_and_blocks_escape(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("private context")
    (tmp_path / "f.py").write_text("def f():\n    return 0\n")
    artifact = Codebase(tmp_path)
    assert "private context" not in trace_source(artifact, "f.py", "f")
    with pytest.raises(ValueError):
        trace_source(artifact, "../outside.py", "f")
    with pytest.raises(FileNotFoundError):
        trace_source(artifact, "CLAUDE.md", "f")


def test_trace_reports_missing_symbols_and_caps_long_body(tmp_path):
    (tmp_path / "f.py").write_text("def f():\n" + "    a = 1\n" * 800)
    artifact = Codebase(tmp_path)
    with pytest.raises(ValueError, match="No definition"):
        trace_source(artifact, "f.py", "missing")
    assert "f: 1-801" in trace_source(artifact, "f.py")
    result = trace_source(artifact, "f.py", "f")
    assert "   300  " in result and "   301  " not in result


def test_workers_retain_one_bundle_each_and_share_allocation(script, tmp_path):
    hypotheses = [
        {
            "hypothesis": f"mechanism {i}",
            "starting_paths": ["f.py"],
            "paper_requirement": "unverified",
        }
        for i in range(6)
    ]
    bundle = "# Bundle 1\nObservation: The function returns zero.\n\n## Excerpt\npath: f.py\nlines: 1-2\n```text\ndef f():\n    return 0\n```"
    script([say(json.dumps(hypotheses))] + [say(bundle)] * 6)
    [log] = eval(
        extraction_experiment(variant="hypothesis_workers", sample_ids="hop_jump"),
        model="mockllm/model",
        log_dir=str(tmp_path),
        display="none",
    )
    assert log.status == "success", log.error
    assert log.samples is not None
    sample = resolve_sample_attachments(log.samples[0])
    extraction = sample.store["stages"]["extraction"]
    assert [b["number"] for b in extraction["bundles"]] == list(range(1, 7))
    config = extraction["metadata"]["config"]
    assert config["plan_steps"] + 6 * config["worker_steps"] == 60
    assert config["recursive_workers"] is False
    assert sample.store["hypotheses"] == hypotheses
    assert all(sample.store[f"worker_{i}_raw"] == bundle for i in range(1, 7))
    calls = [e for e in sample.events if e.event == "model"]
    assert "up to 6 rounds" in calls[0].input[0].text
    assert all("up to 9 rounds" in call.input[0].text for call in calls[1:])
    assert all("up to 60 rounds" not in call.input[0].text for call in calls)
