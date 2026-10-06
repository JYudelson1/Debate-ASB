import json

import pytest

from debate_asb.evidence import (
    EXPECTED_BUNDLE_COUNT,
    AggregationResult,
    BundleDebateResult,
    DebateTurn,
    EvaluationResult,
    EvidenceBundle,
    EvidenceExcerpt,
    EvidenceParseError,
    ExtractionResult,
    ModelMetadata,
    StageMetadata,
    aggregate_max_credence,
    aggregation_result_from_dict,
    content_sha256,
    evaluation_result_from_dict,
    extraction_result_from_dict,
    load_result,
    parse_evidence_bundles,
    render_evidence_bundles,
    result_to_dict,
    save_result,
)


def markdown_bundle(number: int, excerpt: str = "x = 1") -> str:
    return f"""# Bundle {number}
Observation: Candidate mechanism {number}.

## Excerpt
path: src/file_{number}.py
lines: {number}-{number + 1}
```text
{excerpt}
```"""


def extractor_output() -> str:
    return "\n\n".join(
        markdown_bundle(number) for number in range(1, EXPECTED_BUNDLE_COUNT + 1)
    )


def metadata(stage: str = "extraction") -> StageMetadata:
    return StageMetadata(
        sample_id="hop_jump",
        sample_epoch=1,
        stage=stage,
        models={
            "extractor": ModelMetadata(
                "google/gemini-3.1-pro-preview",
                "google-ai-studio",
                temperature=0.2,
                max_tokens=8_000,
            )
        },
        prompt_versions={"extract": content_sha256("prompt text")},
        config={"bundle_count": 10, "nested": {"enabled": True}},
        costs_usd={"extractor": 0.25},
        source_log="logs/source.eval",
        code_version="abc123",
    )


def extraction_result() -> ExtractionResult:
    raw = extractor_output()
    return ExtractionResult.from_raw(metadata(), raw)


def bundle_result(
    bundle: EvidenceBundle, credence: float | None = 75
) -> BundleDebateResult:
    return BundleDebateResult(
        metadata("bundle_debate"),
        bundle,
        (
            DebateTurn(1, "sabotaged", "The shared seed creates a shortcut."),
            DebateTurn(1, "clean", "The seed is for reproducibility."),
        ),
        "The shortcut is material.\n\nSabotage Credence: 75%",
        credence,
    )


def test_parse_and_render_extractor_contract():
    raw = extractor_output()

    bundles = parse_evidence_bundles(raw)

    assert [bundle.number for bundle in bundles] == list(range(1, 11))
    assert bundles[0].observation == "Candidate mechanism 1."
    assert bundles[0].excerpts == (EvidenceExcerpt("src/file_1.py", 1, 2, "x = 1"),)
    assert parse_evidence_bundles(render_evidence_bundles(bundles)) == bundles


def test_four_backtick_fence_can_contain_triple_backticks():
    first = markdown_bundle(1).replace(
        "```text\nx = 1\n```",
        "````text\n```python\nx = 1\n```\n````",
    )
    raw = "\n\n".join([first, *(markdown_bundle(number) for number in range(2, 11))])

    bundles = parse_evidence_bundles(raw)

    assert bundles[0].excerpts[0].text == "```python\nx = 1\n```"


def test_language_specific_excerpt_fence_is_accepted():
    raw = extractor_output().replace("```text", "```python", 1)

    bundles = parse_evidence_bundles(raw)

    assert bundles[0].excerpts[0].text == "x = 1"


@pytest.mark.parametrize("inner_open", ["```", "```bash"])
def test_same_delimiter_nested_fences_preserve_excerpt(inner_open):
    excerpt = f"Paper text\n{inner_open}\n# Bundle 99\nx = 1\n```\nMore paper text"
    first = markdown_bundle(1).replace("x = 1", excerpt)
    raw = "\n\n".join([first, *(markdown_bundle(n) for n in range(2, 11))])

    bundles = parse_evidence_bundles(raw)

    assert bundles[0].excerpts[0].text == excerpt
    assert parse_evidence_bundles(render_evidence_bundles(bundles)) == bundles


def test_final_nested_markdown_fence_can_implicitly_close_outer_excerpt():
    excerpt = "README text\n```bash\npython run.py\n```"
    raw = extractor_output().removesuffix("x = 1\n```") + excerpt

    bundles = parse_evidence_bundles(raw)

    assert bundles[-1].excerpts[0].text == excerpt
    assert parse_evidence_bundles(render_evidence_bundles(bundles)) == bundles


def test_nested_source_fence_preserved_when_outer_missing_before_next_bundle():
    excerpt = "Paper text\n```\nx = 1\n```"
    first = markdown_bundle(1).replace("x = 1\n```", excerpt)
    raw = "\n\n".join([first, *(markdown_bundle(n) for n in range(2, 11))])

    bundles = parse_evidence_bundles(raw)

    assert bundles[0].excerpts[0].text == excerpt
    assert len(bundles) == 10


def test_discontiguous_line_hints_retain_literal_excerpt_and_range_envelope():
    raw = extractor_output().replace("lines: 1-2", "lines: 1, 4-5", 1)

    bundles = parse_evidence_bundles(raw)

    assert bundles[0].excerpts[0] == EvidenceExcerpt("src/file_1.py", 1, 5, "x = 1")
    assert ExtractionResult.from_raw(metadata(), raw).raw_output == raw


def test_result_recovers_plain_preamble_without_changing_bundles_or_raw_output():
    raw = "Let me compile the evidence bundles.\n\n" + extractor_output()

    result = ExtractionResult.from_raw(metadata(), raw)

    assert result.bundles == parse_evidence_bundles(extractor_output())
    assert result.raw_output == raw
    with pytest.raises(EvidenceParseError, match="expected '# Bundle N'"):
        parse_evidence_bundles(raw)


@pytest.mark.parametrize("prefix", ["# Bundle 2", "path: lost.py", "```markdown"])
def test_result_never_discards_evidence_or_fences_in_prefix(prefix):
    with pytest.raises(EvidenceParseError):
        ExtractionResult.from_raw(metadata(), prefix + "\n" + extractor_output())


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (markdown_bundle(1), "exactly 10 bundles"),
        (
            extractor_output().replace("# Bundle 2", "# Bundle 3", 1),
            "expected bundle 2",
        ),
        (
            extractor_output().replace("lines: 1-2", "lines: unknown", 1),
            "line range must be",
        ),
        (
            extractor_output().replace("path: src/file_1.py", "path: ../secret", 1),
            "relative and contained",
        ),
        (
            extractor_output().removesuffix("\n```"),
            "unclosed",
        ),
        ("preamble\n" + extractor_output(), "expected '# Bundle N'"),
        (extractor_output() + "\nclosing summary", "expected '# Bundle N'"),
    ],
)
def test_parser_rejects_malformed_extractor_output(raw, message):
    with pytest.raises(EvidenceParseError, match=message):
        parse_evidence_bundles(raw)


def test_result_types_validate_invariants():
    extraction = extraction_result()
    first = bundle_result(extraction.bundles[0])

    assert first.judge_verdict == "sabotaged"
    assert (
        BundleDebateResult(
            metadata("bundle_debate"), first.bundle, first.turns, "No score.", None
        ).judge_verdict
        is None
    )

    with pytest.raises(ValueError, match="numbered"):
        ExtractionResult(metadata(), extraction.bundles[:-1], extraction.raw_output)
    changed = extraction.raw_output.replace("Candidate mechanism 1", "Changed", 1)
    with pytest.raises(ValueError, match="do not match"):
        ExtractionResult(metadata(), extraction.bundles, changed)
    with pytest.raises(ValueError, match="at least one turn"):
        BundleDebateResult(
            metadata("bundle_debate"), first.bundle, (), "No debate.", 50
        )
    with pytest.raises(ValueError, match="between 0 and 100"):
        BundleDebateResult(
            metadata("bundle_debate"), first.bundle, first.turns, "Invalid.", 101
        )
    with pytest.raises(ValueError, match="duplicate debate turn"):
        BundleDebateResult(
            metadata("bundle_debate"),
            first.bundle,
            (first.turns[0], first.turns[0]),
            "Duplicate.",
            50,
        )


def test_evaluation_requires_one_result_per_extracted_bundle():
    extraction = extraction_result()
    results = tuple(bundle_result(bundle) for bundle in extraction.bundles)

    evaluation = EvaluationResult(metadata("evaluation"), extraction, results)

    assert evaluation.bundle_debates == results
    with pytest.raises(ValueError, match="missing bundle debate"):
        EvaluationResult(metadata("evaluation"), extraction, results[:-1])


def test_evaluation_allows_replayed_results_from_an_earlier_epoch():
    extraction = extraction_result()
    debates = tuple(bundle_result(bundle) for bundle in extraction.bundles)
    replay_metadata = StageMetadata(
        **{
            **metadata("evaluation").__dict__,
            "sample_epoch": 2,
            "source_log": "logs/original.eval",
        }
    )

    evaluation = EvaluationResult(replay_metadata, extraction, debates)

    restored_extraction = extraction_result_from_dict(result_to_dict(extraction))
    restored_evaluation = evaluation_result_from_dict(result_to_dict(evaluation))
    assert restored_extraction == extraction
    assert restored_evaluation == evaluation


def test_max_aggregation_is_canonical_and_retains_ties_and_missing_scores():
    extraction = extraction_result()
    credences = (20, 90, None, 90, 45, 10, 80, 35, 50, 60)
    debates = tuple(
        bundle_result(bundle, credence)
        for bundle, credence in zip(extraction.bundles, credences, strict=True)
    )
    evaluation = EvaluationResult(
        metadata("evaluation"), extraction, tuple(reversed(debates))
    )

    aggregation = aggregate_max_credence(evaluation)

    assert aggregation.method == "max_bundle_credence"
    assert aggregation.bundle_credences == credences
    assert aggregation.credence == 90
    assert aggregation.winning_bundle_numbers == (2, 4)
    assert aggregation.missing_bundle_numbers == (3,)
    assert aggregation.verdict == "sabotaged"
    assert aggregation_result_from_dict(result_to_dict(aggregation)) == aggregation


def test_max_aggregation_is_unscored_when_every_bundle_score_is_missing():
    extraction = extraction_result()
    debates = tuple(bundle_result(bundle, None) for bundle in extraction.bundles)

    aggregation = aggregate_max_credence(
        EvaluationResult(metadata("evaluation"), extraction, debates)
    )

    assert aggregation.credence is None
    assert aggregation.winning_bundle_numbers == ()
    assert aggregation.missing_bundle_numbers == tuple(range(1, 11))
    assert aggregation.verdict is None


def test_aggregation_rejects_inconsistent_derived_fields():
    with pytest.raises(ValueError, match="maximum bundle credence"):
        AggregationResult(
            "max_bundle_credence",
            (10, 20, 30, 40, 50, 60, 70, 80, 90, 100),
            99,
            (10,),
            (),
        )


@pytest.mark.parametrize(
    "kind", ["extraction", "bundle_debate", "evaluation", "aggregation"]
)
def test_result_json_round_trip(tmp_path, kind):
    extraction = extraction_result()
    debates = tuple(bundle_result(bundle) for bundle in extraction.bundles)
    evaluation = EvaluationResult(metadata("evaluation"), extraction, debates)
    results = {
        "extraction": extraction,
        "bundle_debate": debates[0],
        "evaluation": evaluation,
        "aggregation": aggregate_max_credence(evaluation),
    }
    destination = tmp_path / f"{kind}.json"

    save_result(results[kind], destination)

    assert load_result(destination) == results[kind]
    document = json.loads(destination.read_text())
    assert document["schema_version"] == 1
    assert document["result_type"] == kind
    assert "localization" not in destination.read_text()


def test_loader_rejects_unknown_schema_and_fields(tmp_path):
    path = save_result(extraction_result(), tmp_path / "result.json")
    document = json.loads(path.read_text())
    document["schema_version"] = 999
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="unsupported result schema"):
        load_result(path)

    document["schema_version"] = 1
    document["result"]["unexpected"] = True
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="extraction result fields"):
        load_result(path)
