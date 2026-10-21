"""Prompt selection and shortfall acceptance use scripted models, never paid calls."""

import pytest
from conftest import needs_asb, say
from inspect_ai import eval
from inspect_ai.log import resolve_sample_attachments

from debate_asb.evidence import EvidenceParseError
from debate_asb.protocols.extract_and_evaluate import (
    declared_bundle_count,
    extract_and_evaluate_debate,
)


def shortfall(count, limit=6):
    return f"Shortfall: {count} of {limit} qualifying bundles found within the available inspection budget."


@pytest.mark.parametrize("count", [0, 1, 5])
def test_shortfalls_preserve_declared_count(count):
    assert declared_bundle_count(shortfall(count), 6) == count
    assert declared_bundle_count("# Bundle 1", 6) == 6


@pytest.mark.parametrize(
    "raw", [shortfall(6), shortfall(1, 7), shortfall(0) + "\nextra", "Shortfall: none"]
)
def test_shortfalls_reject_invalid_limits_and_extra_zero_output(raw):
    with pytest.raises(EvidenceParseError):
        declared_bundle_count(raw, 6)


@needs_asb
@pytest.mark.parametrize("count", [0, 1])
def test_prompt_and_shortfall_are_logged_without_filling_or_retrying(
    script, tmp_path, count
):
    raw = shortfall(count)
    if count:
        raw += "\n\n# Bundle 1\nObservation: A fact.\n\n## Excerpt\npath: a.py\nlines: 1\n```text\nx = 1\n```"
    script([say(raw)])
    [log] = eval(
        extract_and_evaluate_debate(
            sample_ids="hop_jump",
            stop_after="extraction",
            extraction_prompt="extract_and_evaluate/extract_potential_validity_threats.md",
            output_policy="shortfall",
        ),
        model="mockllm/model",
        log_dir=str(tmp_path),
        display="none",
    )
    assert log.status == "success", log.error
    assert log.samples is not None
    saved = resolve_sample_attachments(log.samples[0])
    extraction = saved.store["stages"]["extraction"]
    assert len(extraction["bundles"]) == count
    assert extraction["raw_output"] == raw
    assert extraction["metadata"]["config"]["max_bundles"] == 6
    calls = [e for e in saved.events if e.event == "model"]
    assert len(calls) == 1
    assert (
        "Extract Potential Threats to Experimental Validity" in calls[0].input[0].text
    )
    assert saved.store.get("credence") is None


@needs_asb
def test_shortfall_full_evaluation_requires_scoring_policy():
    with pytest.raises(ValueError, match="explicit zero_candidate_score"):
        extract_and_evaluate_debate(sample_ids="hop_jump", output_policy="shortfall")
