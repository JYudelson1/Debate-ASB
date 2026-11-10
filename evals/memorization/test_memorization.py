"""Offline checks of the memorization eval: bank, item arrangement, parsing, grading and task wiring. No model calls.

uv run pytest evals/memorization -q
"""

import math
import re
from collections import Counter

import memorization as m
import pytest
from inspect_ai import eval
from inspect_ai.model import ModelOutput
from inspect_ai.util import store

from debate_asb.datasets.asb import asb_samples
from debate_asb.models import ModelSpec, Price

BANK = m.load_bank()
ITEMS = m.build_items(BANK)
POSITIONS = m.base_positions(ITEMS)


# --------------------------------------------------------------------------
# Bank
# --------------------------------------------------------------------------


def test_bank_has_six_wellformed_results_items_per_paper():
    for name in [*BANK["codebases"], "asb"]:
        p = m.paper(BANK, name)
        assert len(p["results"]) == 6, name
        for r in p["results"]:
            options = [r["a"], *r["wrong"]]
            assert len(r["wrong"]) == 3 and len(set(options)) == 4, r["q"]
            assert r["about"] in ("result", "setup") and r["source"], r["q"]
            if name != "asb":
                assert r["paper_md"], r["q"]


def test_variant_labels_match_the_harness_loader():
    labels = {str(s.id): s.target for s in asb_samples()}
    for cb, p in BANK["codebases"].items():
        assert {v: labels[f"{cb}_{v}"] for v in p["variants"]} == p["variants"], cb
    assert len(labels) == sum(len(p["variants"]) for p in BANK["codebases"].values())


def test_fabricated_titles_are_not_real_titles():
    real = {
        m.norm(t)
        for p in [*BANK["codebases"].values(), BANK["asb"]]
        for t in [p["title"], *p["earlier_titles"]]
    }
    assert not {m.norm(f["title"]) for f in BANK["fabricated"]} & real


def test_identify_items_show_only_sections_1_and_2():
    for cb, p in BANK["codebases"].items():
        [honest] = [v for v, label in p["variants"].items() if label == "honest"]
        text = m.paper_sections(cb, honest)
        assert text.startswith("## 1.") and "## 2." in text and "## 3." not in text
        assert "Codename" not in text and "## 0." not in text


# --------------------------------------------------------------------------
# Items and option arrangement
# --------------------------------------------------------------------------


def test_items_per_epoch():
    kinds = Counter(i["kind"] for i in ITEMS)
    assert kinds == {
        "identify": 9,
        "authors": 11,
        "results": 60,
        "asb_what": 1,
        "codename": 9,
    }
    assert len({i["id"] for i in ITEMS}) == len(ITEMS)


@pytest.mark.parametrize("epoch", [1, 2, 3])
def test_correct_letters_are_balanced_in_every_epoch(epoch):
    keys = Counter(
        m.arrange(i, POSITIONS[i["id"]], epoch)[1]
        for i in ITEMS
        if len(i.get("options", [])) == 4
    )
    assert keys == {"A": 15, "B": 15, "C": 15, "D": 15}


def test_each_item_moves_its_key_every_epoch_and_keeps_every_option():
    for item in (i for i in ITEMS if "options" in i):
        letters = []
        for epoch in (1, 2, 3):
            options, key = m.arrange(item, POSITIONS[item["id"]], epoch)
            assert sorted(options) == sorted(item["options"])
            assert options["ABCD".index(key)] == item["options"][0]
            assert (options, key) == m.arrange(
                item, POSITIONS[item["id"]], epoch
            )  # deterministic
            letters.append(key)
        n = len(item["options"])
        assert len(set(letters)) == min(n, 3), item["id"]


# --------------------------------------------------------------------------
# Parsing and grading
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply, n, expected",
    [
        ("Some reasoning.\nAnswer: B", 4, "B"),
        ("**Answer:** C", 4, "C"),
        ("Answer: (D)", 4, "D"),
        ("final answer: a", 4, "A"),
        ("I think it is B) the second one.\n\nAnswer: B) the second one", 4, "B"),
        ("Answer: A\n...on reflection...\nAnswer: C", 4, "C"),
        ("No idea.", 4, None),
        ("Answer: C", 2, None),  # not one of a 2-option item's letters
        ("Answer: a good guess is that", 4, None),
        # Gemini 2.5 Flash, 2026-10-08: a boxed letter instead of the answer line.
        ("Reasoning.\n\nThe final answer is $\\boxed{B}$", 4, "B"),
        ("The final answer is $\\boxed{\\text{C}}$", 4, "C"),
        (
            "Answer: A\nThe final answer is $\\boxed{B}$",
            4,
            "A",
        ),  # an answer line still wins
        (
            "The final answer is $\\boxed{\\text{64%}}$",
            4,
            None,
        ),  # an option's text, not a letter
        ("The final answer is $\\boxed{C}$", 2, None),
    ],
)
def test_parse_choice(reply, n, expected):
    assert m.parse_choice(reply, n) == expected


def item(item_id: str) -> dict:
    return next(i for i in ITEMS if i["id"] == item_id)


@pytest.mark.parametrize(
    "item_id, reply, outcome",
    [
        ("negative_authors", "Authors: ...\nFirst author: Callum McDougall", "correct"),
        ("negative_authors", "**First author:** Arthur Conmy", "wrong"),
        ("negative_authors", "First author: unknown", "declined"),
        ("hidden_authors", "First author: Yohan Matthew", "check"),  # near-miss surname
        ("multi_authors", "First author: Kai Yang", "correct"),  # surname rule; flagged
        (
            "hop_identify",
            "Paper: The Two-Hop Curse | Someone Else | 2024",
            "correct",
        ),  # title
        (
            "hop_identify",
            "Paper: Some other paper | Mikita Balesni | 2024",
            "correct",
        ),  # author
        (
            "hop_identify",
            "Paper: Two-hop reasoning in LLMs revisited | Jane Doe | 2024",
            "check",
        ),
        ("hop_identify", "Paper: unknown", "declined"),
        ("hop_identify", "Paper: I don't recognize this paper", "declined"),
        ("diffuse_identify", "Paper: Sandbagging probes | John Smith | 2025", "named"),
        ("diffuse_identify", "Paper: unknown", "declined"),
        ("fake_1_authors", "First author: Jane Doe", "named"),
        ("fake_2_authors", "First author: I'm not sure", "declined"),
        ("asb_what", "It is a benchmark...\nFirst author: Eric Gan", "correct"),
        ("asb_what", "First author: unknown", "declined"),
        ("lazy_authors", "No final line at all", "unparsed"),
        # GPT-5-mini, 2026-10-08: the label at the end of a sentence instead of on its own line.
        (
            "hop_identify",
            "I don't recognize this as a published paper I'm aware of. Paper: unknown",
            "declined",
        ),
        ("asb_what", "I don't know that paper. First author: unknown", "declined"),
        (
            "negative_authors",
            "Not Neel Nanda. First author: Callum McDougall",
            "correct",
        ),
        (
            "negative_authors",
            "Perhaps First author: Neel Nanda\nFirst author: Callum McDougall",
            "correct",
        ),
        (
            "lazy_authors",
            "I think the first author: hard to say",
            "unparsed",
        ),  # only the label as asked
        # /exp-check, 2026-10-08: negative's excerpt contains "copy suppression", so that phrase alone isn't recall.
        (
            "negative_identify",
            "Paper: A Mechanistic Understanding of Copy-Suppression Heads | Arthur Chen | 2023",
            "check",
        ),
        ("negative_identify", "Paper: Copy Suppression | Kevin Wang | 2022", "wrong"),
        (
            "negative_identify",
            "Paper: Copy Suppression: Comprehensively Understanding an Attention Head | x | 2023",
            "correct",
        ),
        (
            "negative_identify",
            "Paper: Some other paper | Callum McDougall | 2023",
            "correct",
        ),
        (
            "preference_identify",
            "Paper: Subliminal Learning: Something Else | Jane Doe | 2025",
            "correct",
        ),  # not in prompt
    ],
)
def test_grade_reply(item_id, reply, outcome):
    assert m.grade_reply(BANK, item(item_id), reply, None)["outcome"] == outcome


def test_flags_for_hand_checking():
    assert (
        "given name differs"
        in m.grade_reply(BANK, item("multi_authors"), "First author: Kai Yang", None)[
            "flags"
        ]
    )
    hedged = m.grade_reply(
        BANK,
        item("fake_1_authors"),
        "First author: not sure, possibly Jane Doe et al.",
        None,
    )
    assert (
        hedged["outcome"] == "declined" and "decline with more text" in hedged["flags"]
    )


# --------------------------------------------------------------------------
# The task, end to end, with a fake model that answers from the prompt text
# --------------------------------------------------------------------------


def oracle_reply(prompt: str, item_id: str, policy: str) -> str:
    """The right answer (policy 'right'), a wrong one ('wrong'), or no answer line ('mute')."""
    it = item(item_id)
    if policy == "mute":
        return "Let me think about this."
    if "options" in it:
        shown = dict(re.findall(r"^([A-D])\) (.*)$", prompt, re.M))
        [right] = [letter for letter, text in shown.items() if text == it["options"][0]]
        letter = right if policy == "right" else next(x for x in shown if x != right)
        return f"Reasoning...\nAnswer: {letter}"
    p = m.paper(BANK, it["codebase"])
    if not p.get("first_author_surname"):  # controls: declining is right
        name = "unknown" if policy == "right" else "Jane Doe"
    else:
        name = p["first_author"] if policy == "right" else "Jane Doe"
    label = "Paper" if it["kind"] == "identify" else "First author"
    return f"{label}: {'unknown' if name == 'unknown' else ('Some title | ' + name + ' | 2024' if label == 'Paper' else name)}"


class FakeModel:
    def __init__(self, policy: str):
        self.policy = policy
        self.prompts = []

    async def generate(self, messages, tools=None, tool_choice=None, config=None):
        prompt = messages[-1].text
        self.prompts.append((store().get("sample_id"), prompt))
        return ModelOutput.from_content(
            "mockllm/model", oracle_reply(prompt, store().get("sample_id"), self.policy)
        )


def run_task(monkeypatch, tmp_path, policy: str, epochs: int = 2):
    fake = FakeModel(policy)
    monkeypatch.setattr(ModelSpec, "get_model", lambda self: fake)
    monkeypatch.setattr(ModelSpec, "price", lambda self: Price(input=1e-6, output=2e-6))
    monkeypatch.setattr(m, "check_openrouter_account", lambda: None)
    task = m.memorization(
        subject_model="fake/model",
        subject_provider="fake",
        subject_reasoning_effort="medium",
    )
    [log] = eval(
        task, model="none", epochs=epochs, log_dir=str(tmp_path), display="none"
    )
    assert log.status == "success"
    return log, fake


def test_right_answers_score_one_everywhere(monkeypatch, tmp_path):
    log, fake = run_task(monkeypatch, tmp_path, "right")
    assert len(log.samples) == 90 * 2
    for s in log.samples:
        score = s.scores["grade"]
        expected = "declined" if score.metadata["control"] else "correct"
        assert score.metadata["outcome"] == expected and score.value == 1.0, (
            s.id,
            s.epoch,
            score.metadata,
        )
        assert score.metadata["grader_sha256"] == log.eval.metadata["script_sha256"]
    # Each epoch shows the options in a new order, so the prompts differ between epochs.
    by_item = {}
    for sample_id, prompt in fake.prompts:
        by_item.setdefault(sample_id, set()).add(prompt)
    assert all(
        len(prompts) == 2 for i, prompts in by_item.items() if "options" in item(i)
    )
    assert log.eval.metadata["items"] == {
        "identify": 9,
        "authors": 11,
        "results": 60,
        "codename": 9,
        "asb_what": 1,
    }


def test_wrong_and_missing_answers(monkeypatch, tmp_path):
    log, _ = run_task(monkeypatch, tmp_path, "wrong", epochs=1)
    for s in log.samples:
        outcome = s.scores["grade"].metadata["outcome"]
        assert outcome == (
            "named" if s.scores["grade"].metadata["control"] else "wrong"
        ), s.id
        assert s.scores["grade"].value == 0.0
    log, _ = run_task(monkeypatch, tmp_path, "mute", epochs=1)
    assert all(s.scores["grade"].metadata["outcome"] == "unparsed" for s in log.samples)
    assert all(math.isnan(s.scores["grade"].value) for s in log.samples)


def test_cache_scope_and_cost_are_per_sample(monkeypatch, tmp_path):
    log, _ = run_task(monkeypatch, tmp_path, "right", epochs=1)
    for s in log.samples:
        assert s.store["sample_id"] == str(s.id)
        assert set(s.store["cost"]) == {m.ROLE}


def test_report_runs_on_a_log(monkeypatch, tmp_path, capsys):
    run_task(monkeypatch, tmp_path, "right", epochs=1)
    m.report(m.log_paths([str(tmp_path)]))
    out = capsys.readouterr().out
    assert "Multiple choice (chance 25%): all 60/60" in out
    assert "Codename: 9/9" in out
    assert re.search(r"fake_1\s+fabricated\s+0/1 named", out)
    assert f"graded by script {m.file_hash(m.HERE / 'memorization.py')}\n" in out
