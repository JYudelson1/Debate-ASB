"""The lone judge with a follow-up question (protocols/followup_judge.py), offline."""

import pytest
from conftest import explore_then_answer, needs_asb, say
from inspect_ai import eval
from inspect_ai.event import ModelEvent, SpanBeginEvent
from inspect_ai.log import EvalSample, resolve_sample_attachments

from debate_asb.prompts import PROMPTS
from debate_asb.protocol import CREDENCE_REMINDER
from debate_asb.protocols.followup_judge import CRITERIA, followup_judge, parse_ratings
from debate_asb.protocols.lone_judge import lone_judge

pytestmark = needs_asb


def run(task, tmp_path):
    [log] = eval(
        task,
        model="mockllm/model",
        max_samples=1,
        log_dir=str(tmp_path),
        display="none",
    )
    assert log.status == "success", log.error
    assert log.samples is not None
    log.samples = [resolve_sample_attachments(s) for s in log.samples]
    return log


def calls(sample: EvalSample, role: str) -> list[ModelEvent]:
    spans = {
        e.id for e in sample.events if isinstance(e, SpanBeginEvent) and e.name == role
    }
    return [
        e for e in sample.events if isinstance(e, ModelEvent) and e.span_id in spans
    ]


def requests(sample: EvalSample) -> list[list[tuple[str, str]]]:
    """Each judge call's input, as (role, text) pairs, for comparing two runs' requests."""
    return [[(m.role, m.text) for m in e.input] for e in calls(sample, "judge")]


@pytest.mark.parametrize("followup", ["unbanded", "criteria"])
def test_judge_sends_the_lone_judges_requests_then_one_followup(
    script, tmp_path, followup
):
    script(explore_then_answer(80))
    [lone] = run(lone_judge(sample_ids="hop_jump"), tmp_path / "lone").samples or []

    reply = "Executed: 90\nChanges key findings: 70\nMore likely than alternatives: 50\nSabotage Credence: 65%"
    script(explore_then_answer(80) + [say(reply)])
    log = run(followup_judge(sample_ids="hop_jump", followup=followup), tmp_path / "f")
    [sample] = log.samples or []

    assert requests(sample) == requests(lone)  # so a lone judge's calls replay
    [asked] = calls(sample, "followup")
    question = (PROMPTS / "asb" / "followup" / f"{followup}.md").read_text().strip()
    assert asked.input[-1].text == question
    assert asked.input[:-2] == calls(sample, "judge")[-1].input  # same conversation
    assert asked.tool_choice == "none"

    assert sample.store["first_credence"] == 80
    assert sample.store["credence"] == 65  # the score is the follow-up's credence
    record = sample.store["followup"]
    assert (record["kind"], record["credence"]) == (followup, 65)
    if followup == "criteria":
        assert record["ratings"] == dict(zip(CRITERIA, (90.0, 70.0, 50.0)))
    else:
        assert "ratings" not in record
    assert sample.store["cost"]["followup"] > 0


def test_followup_without_a_credence_is_asked_again(script, tmp_path):
    script(
        explore_then_answer(30)
        + [say("Executed: 80\nChanges key findings: 40"), say("Sabotage Credence: 20%")]
    )
    log = run(followup_judge(sample_ids="hop_stride", followup="criteria"), tmp_path)
    [sample] = log.samples or []
    asked = calls(sample, "followup")
    assert len(asked) == 2 and asked[1].input[-1].text == CREDENCE_REMINDER
    assert (sample.store["first_credence"], sample.store["credence"]) == (30, 20)
    assert sample.store["followup"]["ratings"] == {
        "Executed": 80.0,
        "Changes key findings": 40.0,
        "More likely than alternatives": None,
    }


def test_parse_ratings_reads_the_last_line_of_each_criterion():
    text = "Executed: 10\n**Executed:** 95\n- Changes key findings: 120\nMore likely than alternatives : 33.5"
    assert parse_ratings(text) == {
        "Executed": 95.0,
        "Changes key findings": 100.0,
        "More likely than alternatives": 33.5,
    }


def test_unknown_followup_fails():
    with pytest.raises(ValueError, match="followup"):
        followup_judge(sample_ids="hop_jump", followup="other")  # type: ignore[arg-type]


def test_followup_prompts_name_only_sections_the_base_prompt_has():
    base = "".join(
        (PROMPTS / "asb" / name).read_text()
        for name in ("judge.md", "game_rules.md", "judge_answer.md")
    )
    for section in (
        "Burden of Proof for Sabotage",
        "Confidence Levels",
        "Self-Consistency Check",
        "Step 3: Evaluate Sabotages",
        "Fix 1",
    ):
        assert section in base
