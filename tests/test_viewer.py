from types import SimpleNamespace

from debate_asb.protocols.simple_debate import _activity_span_name
from debate_asb.viewer.adapter import adapt_log
from debate_asb.viewer.render import render_report
from debate_asb.viewer.rubrics import RUBRIC_WARNING, load_rubric_context


class Event(SimpleNamespace):
    def model_dump(self, **_: object) -> dict:
        return dict(self.data)


def test_adapter_groups_partial_results_by_bundle() -> None:
    report = adapt_log(_log_fixture(), "/tmp/example.eval")

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
    assert sample.bundles[1].turns[0].argument == "Transcript-only argument"
    assert sample.bundles[1].metadata["turns_inferred_from_transcript"] is True
    assert sample.bundles[0].trace[0].role == "sabotaged"


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


def test_html_report_is_self_contained_and_escapes_script_end() -> None:
    log = _log_fixture()
    log.samples[0].store["stages"]["extraction"]["bundles"][0]["observation"] = (
        "</script><script>alert(1)</script>"
    )

    html = render_report(adapt_log(log, "/tmp/example.eval"))

    assert "Evidence bundles" in html
    assert "bundle-canvas" in html
    assert "<\\/script><script>alert(1)<\\/script>" in html
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
        events=[outer, inner, model],
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
