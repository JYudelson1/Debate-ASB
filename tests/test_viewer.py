import json
from html.parser import HTMLParser
from types import SimpleNamespace
from typing import cast

import pytest
from inspect_ai.log import EvalLog

from debate_asb.protocol import OUT_OF_TOOL_CALLS
from debate_asb.protocols.simple_debate import _activity_span_name
from debate_asb.viewer.adapter import adapt_log
from debate_asb.viewer.markdown import render_markdown
from debate_asb.viewer.render import render_report
from debate_asb.viewer.rubrics import RUBRIC_WARNING, load_rubric_context
from debate_asb.viewer.spans import Activity, parse_activity


class Event(SimpleNamespace):
    def model_dump(self, **_: object) -> dict:
        return dict(self.data)


def _diagnostics(report, code: str) -> list:
    return [d for d in report.samples[0].diagnostics if d.code == code]


def test_adapter_groups_partial_results_by_bundle() -> None:
    report = adapt_log(cast(EvalLog, _log_fixture()), "/tmp/example.eval")

    sample = report.samples[0]
    assert sample.stage_progress == {
        "extraction": "complete",
        "bundles": 2,
        "debates": 2,
        "judgments": 1,
        "aggregation": "complete",
        "replayed_stages": [],
    }
    assert sample.bundles[0].judge_credence == 82
    assert sample.bundles[0].judge_verdict == "sabotaged"
    assert sample.bundles[1].turns[0].argument.source == "Transcript-only argument"
    assert sample.bundles[1].metadata["turns_inferred_from_transcript"] is True
    assert sample.bundles[0].trace[0].role == "sabotaged"


def test_flattened_tool_history_is_located_at_its_turn() -> None:
    report = adapt_log(cast(EvalLog, _log_fixture()), "/tmp/example.eval")

    [found] = _diagnostics(report, "flattened_tool_history")
    assert found.severity == "warning"
    assert (found.bundle, found.round, found.participant) == (1, 1, "sabotaged")


def test_text_only_tool_call_argument_is_an_error() -> None:
    log = _log_fixture()
    turn = log.samples[0].store["stages"]["bundle_debates"][0]["turns"][0]
    turn["argument"] = '[called read_file({"path": "PAPER.md"})]'
    log.samples[0].store["stages"]["bundle_judgments"][0]["turns"] = [turn]

    report = adapt_log(cast(EvalLog, log), "/tmp/example.eval")

    [found] = _diagnostics(report, "text_only_tool_call")
    assert found.severity == "error"
    assert (found.bundle, found.round, found.participant) == (1, 1, "sabotaged")
    assert found.evidence == '[called read_file({"path": "PAPER.md"})]'


def test_argument_quoting_a_tool_call_is_only_a_warning() -> None:
    log = _log_fixture()
    turn = {
        "round": 1,
        "side": "sabotaged",
        "argument": "My point.\n[called search({})]",
    }
    log.samples[0].store["stages"]["bundle_judgments"][0]["turns"] = [turn]

    report = adapt_log(cast(EvalLog, log), "/tmp/example.eval")

    assert not _diagnostics(report, "text_only_tool_call")
    assert _diagnostics(report, "partial_text_tool_call")[0].severity == "warning"


def test_missing_credence_and_tool_budget_are_flagged() -> None:
    log = _log_fixture()
    log.samples[0].store["stages"]["bundle_judgments"][0]["judge_credence"] = None
    out_of_budget = SimpleNamespace(role="user", text=OUT_OF_TOOL_CALLS)
    log.samples[0].events.append(
        Event(event="model", span_id="inner", input=[out_of_budget], data={})
    )

    report = adapt_log(cast(EvalLog, log), "/tmp/example.eval")

    [missing] = _diagnostics(report, "missing_credence")
    assert (missing.bundle, missing.participant) == (1, "judge")
    [budget] = _diagnostics(report, "tool_budget_exhausted")
    assert (budget.bundle, budget.round, budget.participant) == (1, 1, "sabotaged")


def test_sample_error_is_a_sample_level_diagnostic() -> None:
    log = _log_fixture()
    log.samples[0].error = {"message": "boom\nmore", "traceback": "Traceback..."}

    report = adapt_log(cast(EvalLog, log), "/tmp/example.eval")

    [found] = _diagnostics(report, "sample_error")
    assert found.bundle is None
    assert "boom" in found.detail
    assert found.evidence == "Traceback..."


def test_repeated_diagnostics_at_one_location_merge_into_a_count() -> None:
    log = _log_fixture()
    log.samples[0].events.append(log.samples[0].events[-1])

    report = adapt_log(cast(EvalLog, log), "/tmp/example.eval")

    [found] = _diagnostics(report, "flattened_tool_history")
    assert found.count == 2


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("bundle/3/debate/round/2/clean", Activity("debate", 3, 2, "clean")),
        ("bundle/3/judgment", Activity("judgment", 3)),
        ("debate/round/1/sabotaged", Activity("debate", None, 1, "sabotaged")),
        ("judgment", Activity("judgment")),
        ("extraction", Activity("extraction")),
        ("debater_sabotaged", None),
        ("bundle/3/judgment/extra", None),
    ],
)
def test_parse_activity(name: str, expected: Activity | None) -> None:
    assert parse_activity(name) == expected


def test_markdown_renders_formatting_but_escapes_raw_html() -> None:
    html = render_markdown(
        "**bold** `code`\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))"
    )

    assert "<strong>bold</strong>" in html
    assert "<code>code</code>" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'href="javascript:' not in html


def test_rubric_context_retains_warning_and_normalization() -> None:
    context = load_rubric_context("hop_jump", "sabotaged", None)

    assert context is not None
    assert context["warning"] == RUBRIC_WARNING
    assert context["sabotage"]["impact"]
    assert context["tags"]["localization"] == "distributed"
    assert context["normalized_location"]["normalization_status"] == "exact"


def test_honest_rubric_has_no_sabotage_annotation() -> None:
    context = load_rubric_context("hop_stride", "honest", None)

    assert context is not None
    assert context["label"] == "honest"
    assert context["sabotage"] is None


@pytest.mark.parametrize(
    "observation",
    ["</script><script>alert(1)</script>", "<!--<script>text</script>"],
)
def test_html_report_is_self_contained_and_escapes_script_end(observation: str) -> None:
    log = _log_fixture()
    log.samples[0].store["stages"]["extraction"]["bundles"][0]["observation"] = (
        observation
    )

    html = render_report(adapt_log(cast(EvalLog, log), "/tmp/example.eval"))

    assert "Evidence bundles" in html
    assert "bundle-canvas" in html

    class EmbeddedData(HTMLParser):
        in_data = False
        payload = ""

        def handle_starttag(self, tag, attrs):
            self.in_data = tag == "script" and dict(attrs).get("id") == "report-data"

        def handle_endtag(self, tag):
            if tag == "script":
                self.in_data = False

        def handle_data(self, data):
            if self.in_data:
                self.payload += data

    parser = EmbeddedData()
    parser.feed(html)
    assert "<" not in parser.payload
    decoded = json.loads(parser.payload)
    assert decoded["samples"][0]["bundles"][0]["observation"] == observation
    assert "/* REPORT_CSS */" not in html
    assert "/* REPORT_JS */" not in html


def test_activity_span_names_encode_bundle_round_and_role() -> None:
    assert (
        _activity_span_name({"bundle": 3}, "debate", round=2, role="clean")
        == "bundle/3/debate/round/2/clean"
    )
    assert _activity_span_name({"bundle": 3}, "judgment") == "bundle/3/judgment"
    assert _activity_span_name({}, "judgment") == "judgment"


def _log_fixture() -> SimpleNamespace:
    outer = Event(
        event="span_begin",
        id="outer",
        parent_id=None,
        name="bundle/1/debate/round/1/sabotaged",
        span_id="outer",
        data={},
    )
    inner = Event(
        event="span_begin",
        id="inner",
        parent_id="outer",
        name="debater_sabotaged",
        span_id="inner",
        data={},
    )
    model = Event(
        event="model",
        span_id="inner",
        data={
            "event": "model",
            "timestamp": "2026-09-30T00:00:00Z",
            "model": "provider/model",
            "output": {"completion": "Investigating", "usage": {"input_tokens": 3}},
        },
    )
    flattened = Event(
        event="info",
        span_id="outer",
        data={
            "event": "flattened_tool_history_after_invalid_argument",
            "role": "debater_sabotaged",
        },
    )
    extraction = {
        "metadata": {
            "sample_id": "hop_jump",
            "sample_epoch": 1,
            "stage": "extraction",
        },
        "raw_output": "retained",
        "bundles": [
            {
                "number": 1,
                "observation": "Candidate one",
                "excerpts": [
                    {
                        "path": "one.py",
                        "start_line": 1,
                        "end_line": 2,
                        "text": "code",
                    }
                ],
            },
            {
                "number": 2,
                "observation": "Candidate two",
                "excerpts": [
                    {
                        "path": "two.py",
                        "start_line": 5,
                        "end_line": 5,
                        "text": "more code",
                    }
                ],
            },
        ],
    }
    stages = {
        "extraction": extraction,
        "bundle_debates": [
            {
                "metadata": {"stage": "bundle_debate"},
                "bundle_number": 1,
                "turns": [{"round": 1, "side": "sabotaged", "argument": "Argument"}],
            }
        ],
        "bundle_judgments": [
            {
                "metadata": {"stage": "bundle_judgment"},
                "bundle": extraction["bundles"][0],
                "turns": [{"round": 1, "side": "sabotaged", "argument": "Argument"}],
                "judge_response": "Decision\nSabotage Credence: 82%",
                "judge_credence": 82,
            }
        ],
    }
    sample = SimpleNamespace(
        id="hop_jump",
        epoch=1,
        target="sabotaged",
        metadata={
            "paper_name": "Two-Hop Latent Reasoning",
            "artifacts": {"codebase": {"root": "/tmp/hop/jump"}},
            "answer_key": None,
        },
        store={
            "stages": stages,
            "transcript": [
                {
                    "event": "argument",
                    "bundle": 2,
                    "round": 1,
                    "side": "clean",
                    "argument": "Transcript-only argument",
                }
            ],
            "aggregation": {"method": "max_bundle_credence", "credence": 82},
            "cost": {"extractor": 1.25},
            "usage": {},
            "replayed_stages": {},
        },
        events=[outer, inner, model, flattened],
        error=None,
        total_time=12.0,
        working_time=10.0,
    )
    return SimpleNamespace(
        eval=SimpleNamespace(
            task="extract_and_evaluate_debate",
            task_args={"n_rounds": 2},
            revision={"commit": "abc123", "dirty": False},
        ),
        status="success",
        stats={"started_at": "2026-09-30T00:00:00Z"},
        samples=[sample],
    )
