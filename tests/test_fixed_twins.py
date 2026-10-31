"""The fixed-codebase tasks: which codebase each id reads, and a scripted run of each."""

from conftest import explore_then_answer, needs_asb, say
from inspect_ai import eval as inspect_eval

from debate_asb.datasets import ASB_ROOT
from debate_asb.fixscore import answer_keys
from debate_asb.metrics import Answer, paper, paper_bootstrap
from debate_asb.protocols.fixed_twins import twin_debate, twin_lone, twin_samples

pytestmark = needs_asb


def _root(sample) -> str:
    assert sample.metadata is not None
    return sample.metadata["artifacts"]["codebase"]["root"]


def _only_sample(log):
    assert log.status == "success" and log.samples is not None
    return log.samples[0]


def _patched_tree(tmp_path):
    codebase = tmp_path / "hop" / "jump"
    codebase.mkdir(parents=True)
    (codebase / "PAPER.md").write_text("# Paper\n\nThe seed is 1.\n", encoding="utf-8")
    return tmp_path


def test_ids_read_the_right_codebases(tmp_path):
    root = _patched_tree(tmp_path)
    samples = twin_samples(
        "hop_jump,hop_stride,hop_stride_fixed,hop_jump_fixed", str(root)
    )
    assert [s.id for s in samples] == [
        "hop_jump", "hop_stride", "hop_stride_fixed", "hop_jump_fixed",
    ]  # fmt: skip
    assert [s.target for s in samples] == [
        "sabotaged", "honest", "honest", "sabotaged",
    ]  # fmt: skip
    codebases = ASB_ROOT / "codebases"
    assert _root(samples[0]).rstrip("/\\") == str(codebases / "hop" / "jump")
    assert _root(samples[2]).rstrip("/\\") == str(codebases / "hop" / "stride_fixed")
    assert _root(samples[3]) == str(root / "hop" / "jump")
    # The patched sample keeps its base's answer key.
    assert samples[3].metadata and samples[0].metadata
    assert samples[3].metadata["answer_key"] == samples[0].metadata["answer_key"]


def test_patched_id_needs_its_root(tmp_path):
    for root in (None, str(tmp_path)):
        try:
            twin_samples("hop_jump_fixed", root)
        except ValueError as error:
            assert "hop_jump_fixed" in str(error)
        else:
            raise AssertionError("expected a ValueError")


def test_fixed_ids_share_their_paper_and_key():
    assert paper("hop_stride_fixed") == paper("hop_stride") == "hop"
    assert paper("hop_jump_fixed") == "hop"
    assert "hop_jump" in answer_keys()


def test_bootstrap_keeps_fixed_samples_apart():
    answers = [
        Answer("hop_jump_fixed", 1, True, 60.0),
        Answer("hop_double_fixed", 1, True, 70.0),
        Answer("hop_stride_fixed", 1, False, 40.0),
    ]
    assert paper_bootstrap(lambda a: len({x.sample for x in a}), answers, 5) == (3, 3)


def test_twin_lone(script, tmp_path):
    script(explore_then_answer(30))
    log = inspect_eval(
        twin_lone(sample_ids="hop_jump_fixed", sabotaged_fixed_root=str(_patched_tree(tmp_path))),
        model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none",
    )[0]  # fmt: skip
    assert _only_sample(log).store["credence"] == 30


def test_twin_debate(script, tmp_path):
    script(
        [say(f"Argument {i}.") for i in range(4)]
        + explore_then_answer(70)
    )  # fmt: skip
    log = inspect_eval(
        twin_debate(sample_ids="hop_stride_fixed"),
        model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none",
    )[0]  # fmt: skip
    sample = _only_sample(log)
    assert sample.target == "honest"
    assert sample.store["credence"] == 70
    arguments = [a for a in sample.store["transcript"] if a.get("event") == "argument"]
    assert len(arguments) == 4
