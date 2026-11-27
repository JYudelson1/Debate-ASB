"""P01 entity-target regressions, with project imports and model calls stubbed."""

import ast
import asyncio
import hashlib
import json
import random
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Target

from debate_asb.datasets import asb, code_repairs

ENTRYPOINTS = (
    "experiments/fully_synthetic/run_evaluation.py",
    "experiments/fully_synthetic/evaluate_openai_ft_models.py",
)

# Snapshot of the README entry point's actual functions at ASB f8efd68.
# Both entry points use this identical target constructor expression.
ORIGINAL_ENTRYPOINT = """def create_sample(input_text, target, id, metadata) -> Sample:
    return Sample(input=input_text, target=target, id=id, metadata=metadata)

def spouses_grader():
    async def score(state: TaskState, target: Target) -> Score:
        graded_sample = await grade(
            question=state.metadata["question"],
            generated=state.output.completion,
            expected=target.text,
            first_hop_reference_answer=state.metadata.get("answer_intermediate"),
            prompt=state.input_text,
        )
        return Score(value=CORRECT if graded_sample.correct else INCORRECT)

    return score

def create_task(
    name: str,
    n: int,
    dataset_path: Path,
    max_few_shots: int = None,
    config: GenerateConfig = None,
) -> Task:
    dataset = load_dataset(dataset_path / (name + ".jsonl"))
    rng = random.Random(42)

    # Add few-shot examples based on task type
    few_shots_path = None
    if "2hop_cot" in name:
        few_shots_path = DATASET_PATH / "2hop_fewshots_cot.jsonl"
    elif "2hop_nocot" in name:
        few_shots_path = DATASET_PATH / "2hop_fewshots_nocot.jsonl"

    few_shot_messages = []
    if few_shots_path and few_shots_path.exists():
        few_shots = load_dataset(few_shots_path)
        if max_few_shots is not None:
            few_shots = few_shots[:max_few_shots]
        for example in few_shots:
            few_shot_messages.extend(
                [msg for msg in example["messages"] if msg["role"] != "system"]
            )

    if "2hop" not in name:
        dataset = rng.choices(dataset, k=50)
    dataset = dataset[:n]

    def record_to_sample(record: Dict[str, Any]) -> Sample:
        messages = [record["messages"][1]]
        if few_shots_path and few_shots_path.exists():
            messages = few_shot_messages + messages
        return create_sample(
            messages, record["messages"][2]["content"], hash(messages[-1]["content"]), record
        )

    inspect_dataset = MemoryDataset(samples=[record_to_sample(record) for record in dataset])

    return Task(
        dataset=inspect_dataset,
        plan=[
            system_message(dataset[0]["messages"][0]["content"]),
            generate(cache=CachePolicy(expiry=None)),
        ],
        scorer=spouses_grader(),
        name=name,
        config=config or GenerateConfig(temperature=0),
    )
"""

# Only the original grading data class and function; no torch/transformers imports.
ORIGINAL_GRADE = """@dataclass
class GradedSample:
    question: str
    generated: str
    expected: str
    correct: bool
    is_first_hop_correct: bool | None = None
    prompt: str | None = None
    valid_cot: bool | None = None

async def grade(
    question: str,
    generated: str,
    expected: str | list[str],
    first_hop_reference_answer: str | list[str] | None = None,
    force_no_cot: bool = False,
    prompt: list[dict[str, str]] | None = None,
) -> GradedSample:
    expected = expected if isinstance(expected, list) else [expected]
    if first_hop_reference_answer is not None:
        first_hop_reference_answer = (
            first_hop_reference_answer
            if isinstance(first_hop_reference_answer, list)
            else [first_hop_reference_answer]
        )

    correct = False
    correct_generated_e3 = ""
    for exp in expected:
        if exp.lower() in generated.lower():
            correct = True
            correct_generated_e3 = exp.lower()
            break

    is_first_hop_correct = False
    correct_generated_e2 = ""
    if first_hop_reference_answer is not None and not force_no_cot:
        for reference_option in first_hop_reference_answer:
            # TODO: we might want model-grading here for more robustness
            if reference_option.lower() in generated.lower():
                is_first_hop_correct = True
                correct_generated_e2 = reference_option.lower()
                break
    return GradedSample(
        question=question,
        generated=generated,
        expected=str(expected),
        correct=correct,
        is_first_hop_correct=is_first_hop_correct,
        valid_cot=(
            correct
            and is_first_hop_correct
            and generated.lower().index(correct_generated_e2)
            < generated.lower().index(correct_generated_e3)  # name is before year
        ),
        prompt=str(prompt),
    )
"""


def _functions(source: str, names: tuple[str, ...], namespace: dict[str, Any]):
    tree = ast.parse(source)
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name in names
    ]
    assert {node.name for node in selected} == set(names)
    for node in selected:
        if not isinstance(node, ast.ClassDef):
            node.decorator_list = []
    # Avoid evaluating the experiment's third-party-only annotations.
    body: list[ast.stmt] = [
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0
        ),
        *selected,
    ]
    module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    exec(compile(module, "<P01 functions>", "exec"), namespace)
    return namespace


@pytest.fixture
def hop_overlay(tmp_path, monkeypatch):
    packaged_root = asb.ASB_ROOT / "codebases/hop/stride"
    original = tmp_path / "asb/codebases/hop/stride"
    repairs = {}
    for relative in ENTRYPOINTS:
        path = original / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        packaged = packaged_root / relative
        path.write_text(
            packaged.read_text() if packaged.exists() else ORIGINAL_ENTRYPOINT
        )
        repair = code_repairs.SOURCE_REPAIRS["hop_stride"][relative]
        if packaged.exists():
            assert (
                hashlib.sha256(path.read_bytes()).hexdigest() == repair.expected_sha256
            )
        repairs[relative] = replace(
            repair, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        )
    monkeypatch.setitem(code_repairs.SOURCE_REPAIRS, "hop_stride", repairs)
    monkeypatch.setattr(asb, "ASB_ROOT", tmp_path / "asb")
    monkeypatch.setattr(asb, "PATCHED_ROOT", tmp_path / "patched")
    return original


@pytest.fixture
def grade():
    return _functions(
        ORIGINAL_GRADE, ("GradedSample", "grade"), {"dataclass": dataclass}
    )["grade"]


@pytest.mark.parametrize("relative", ENTRYPOINTS)
def test_loader_repairs_both_entity_targets_preserving_originals(
    hop_overlay, relative, tmp_path, grade
):
    original = hop_overlay
    source = (original / relative).read_bytes()
    fixed = asb._workspace("hop_stride", "codebases/hop/stride")
    repaired = (fixed / relative).read_text()
    assert (original / relative).read_bytes() == source
    assert fixed != original

    namespace = {
        "Sample": Sample,
        "Path": Path,
        "random": random,
        "DATASET_PATH": tmp_path / "fewshots-not-present",
        "PATH_TO_DATASETS": str(tmp_path / "fewshots-not-present"),
        "MemoryDataset": lambda samples: samples,
        "Task": lambda **kwargs: SimpleNamespace(**kwargs),
        "GenerateConfig": lambda **kwargs: SimpleNamespace(**kwargs),
        "CachePolicy": lambda **kwargs: SimpleNamespace(**kwargs),
        "system_message": lambda text: text,
        "generate": lambda **kwargs: None,
        "grade": grade,
        "Score": lambda **kwargs: SimpleNamespace(**kwargs),
        "CORRECT": "C",
        "INCORRECT": "I",
        "load_dataset": lambda path: [
            json.loads(line) for line in path.read_text().splitlines()
        ],
    }
    namespace = _functions(
        repaired, ("create_sample", "spouses_grader", "create_task"), namespace
    )
    rows = {
        "a_undemoed": ("Cam", "The person Hard is hitched to is named Cam."),
        "b_undemoed": ("Beira", "Carrier first saw the light of day in Beira."),
        "2hop_cot": ("Beira", "Seed's spouse Carrier was born in Beira."),
        "2hop_nocot": ("Beira", "Beira"),
    }
    for task_name, (answer, reference) in rows.items():
        record = {
            "messages": [
                {"role": "system", "content": "Answer the question."},
                {"role": "user", "content": "Who or where?"},
                {"role": "assistant", "content": reference},
            ],
            "question": "Who or where?",
            "answer": answer,
            "answer_intermediate": "",
        }
        (tmp_path / f"{task_name}.jsonl").write_text(json.dumps(record) + "\n")
        task = namespace["create_task"](name=task_name, n=1, dataset_path=tmp_path)
        sample = task.dataset[0]
        assert sample.target == answer
        assert sample.metadata == record
        assert sample.metadata["messages"][2]["content"] == reference
        state = SimpleNamespace(
            metadata=sample.metadata,
            output=SimpleNamespace(completion=answer),
            input_text=sample.input,
        )
        result = asyncio.run(task.scorer(state, Target(sample.target)))
        assert result.value == "C"


def test_saved_cam_paraphrase_and_wrong_answers(grade):
    question = "What's the name of the person Hard is hitched to?"
    response = "The name of the person Hard is hitched to is Cam."
    reference = "The person Hard is hitched to is named Cam."
    assert not asyncio.run(
        grade(question=question, generated=response, expected=reference)
    ).correct
    assert asyncio.run(
        grade(question=question, generated=response, expected="Cam")
    ).correct
    assert not asyncio.run(
        grade(question=question, generated="It is John.", expected="Cam")
    ).correct


def test_no_cot_entity_grading_is_unchanged(grade):
    assert asyncio.run(
        grade(question="Where?", generated="Beira", expected="Beira")
    ).correct
    assert not asyncio.run(
        grade(question="Where?", generated="Paris", expected="Beira")
    ).correct
