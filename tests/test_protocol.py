"""Focused tests for protocol helpers that do not require the ASB dataset."""

import pytest
from inspect_ai.model import ChatMessage, ChatMessageTool, ChatMessageUser, ModelOutput
from inspect_ai.tool import tool

from debate_asb import protocol
from debate_asb.models import ModelSpec, Participant, Price, Side
from debate_asb.protocols.extract_and_evaluate import ExtractAndEvaluateDebate


@pytest.mark.parametrize("n_bundles", [0, -1, 1.5, True, "6"])
def test_extract_and_evaluate_requires_positive_integer_bundle_count(n_bundles):
    spec = ModelSpec("m", "p")
    with pytest.raises(ValueError, match="n_bundles must be a positive integer"):
        ExtractAndEvaluateDebate(
            extractor=Participant(spec),
            sabotaged=Participant(spec, side=Side.SABOTAGED),
            clean=Participant(spec, side=Side.CLEAN),
            judge=Participant(spec),
            n_bundles=n_bundles,
        )


class InvalidArgument(RuntimeError):
    status_code = 400


@pytest.mark.asyncio
async def test_invalid_tool_history_is_flattened_and_retried_once(monkeypatch):
    tool_output = ModelOutput.for_tool_call("mockllm/model", "search", {"pattern": "x"})
    assert tool_output.message.tool_calls is not None
    messages: list[ChatMessage] = [
        ChatMessageUser(content="Find x."),
        tool_output.message,
        ChatMessageTool(content="a.py:1:x", tool_call_id=tool_output.message.tool_calls[0].id, function="search"),
    ]  # fmt: skip
    calls = []

    async def fake_generate(
        participant, role, call_messages, tools=[], tool_choice=None
    ):
        calls.append(list(call_messages))
        if len(calls) == 1:
            raise InvalidArgument("400 INVALID_ARGUMENT")
        output = ModelOutput.from_content("mockllm/model", "Found it.")
        call_messages.append(output.message)
        return output

    notes = []
    monkeypatch.setattr(protocol, "generate", fake_generate)
    monkeypatch.setattr(
        protocol, "note", lambda event, **data: notes.append((event, data))
    )

    output = await protocol._generate_with_tool_history_recovery(
        Participant(ModelSpec("m", "p")), "debater", messages
    )

    assert output.completion == "Found it."
    assert [message.role for message in calls[0]] == ["user", "assistant", "tool"]
    assert [message.role for message in calls[1]] == ["user", "assistant", "user"]
    assert calls[1][1].tool_calls is None
    assert "[called search" in calls[1][1].text
    assert "[result of search]" in calls[1][2].text
    assert notes == [
        ("flattened_tool_history_after_invalid_argument", {"role": "debater"})
    ]


@pytest.mark.asyncio
async def test_other_generation_errors_are_not_retried(monkeypatch):
    messages: list[ChatMessage] = [ChatMessageUser(content="Hello")]
    calls = 0

    async def fake_generate(
        participant, role, call_messages, tools=[], tool_choice=None
    ):
        nonlocal calls
        calls += 1
        raise InvalidArgument("400 INVALID_ARGUMENT")

    monkeypatch.setattr(protocol, "generate", fake_generate)

    with pytest.raises(InvalidArgument):
        await protocol._generate_with_tool_history_recovery(
            Participant(ModelSpec("m", "p")), "judge", messages
        )
    assert calls == 1


@tool
def noop():
    async def execute() -> str:
        """Do nothing."""
        return ""

    return execute


def play_back(monkeypatch, replies: list[str]):
    """Make protocol.generate answer with `replies` as plain text, in order; record calls and notes."""
    calls: list[list[ChatMessage]] = []
    notes: list[str] = []

    async def fake_generate(
        participant, role, call_messages, tools=[], tool_choice=None
    ):
        calls.append(list(call_messages))
        output = ModelOutput.from_content("mockllm/model", replies[len(calls) - 1])
        call_messages.append(output.message)
        return output

    monkeypatch.setattr(protocol, "generate", fake_generate)
    monkeypatch.setattr(protocol, "note", lambda event, **data: notes.append(event))
    monkeypatch.setattr(ModelSpec, "price", lambda self: Price(input=1e-6, output=2e-6))
    return calls, notes


ANSWER = "Fix 1: utils.py:215 computes capacity wrongly.\n\nSabotage Credence: 60%"
TEXT_CALL = '{"path": "utils.py", "start_line": 200}'


# The forms GPT-5.2 wrote in E08 (experiments/E08_strong_debaters/check_malformed.py).
@pytest.mark.parametrize(
    "text_call",
    [
        TEXT_CALL,
        'to=functions.read_file {"path": "utils.py"}',
        '{"tool_uses": [{"recipient_name": "functions.read_file", "parameters": {}}]}',
    ],
)
@pytest.mark.asyncio
async def test_tool_call_written_as_text_is_answered_and_the_loop_goes_on(
    monkeypatch, text_call
):
    calls, notes = play_back(monkeypatch, [text_call, ANSWER])
    messages: list[ChatMessage] = [ChatMessageUser(content="Audit.")]

    answer = await protocol.agent_loop(
        Participant(ModelSpec("m", "p")), "judge", messages, [noop()]
    )

    assert answer == ANSWER
    assert calls[1][-1].text == protocol.TOOL_CALL_WRITTEN_AS_TEXT
    assert notes == ["tool_call_written_as_text"]


@pytest.mark.asyncio
async def test_an_answer_that_mentions_code_is_not_a_tool_call(monkeypatch):
    calls, notes = play_back(monkeypatch, [ANSWER])

    answer = await protocol.agent_loop(
        Participant(ModelSpec("m", "p")),
        "judge",
        [ChatMessageUser(content="Audit.")],
        [noop()],
    )

    assert answer == ANSWER
    assert len(calls) == 1 and notes == []


@pytest.mark.asyncio
async def test_text_tool_calls_get_two_answers_then_stand(monkeypatch):
    calls, notes = play_back(monkeypatch, [TEXT_CALL] * 3)

    answer = await protocol.agent_loop(
        Participant(ModelSpec("m", "p")),
        "judge",
        [ChatMessageUser(content="Audit.")],
        [noop()],
    )

    assert answer == TEXT_CALL
    assert len(calls) == 3
    assert notes == ["tool_call_written_as_text"] * 2


@pytest.mark.asyncio
async def test_text_tool_call_without_tools_is_the_answer(monkeypatch):
    calls, notes = play_back(monkeypatch, [TEXT_CALL])

    answer = await protocol.agent_loop(
        Participant(ModelSpec("m", "p")),
        "judge",
        [ChatMessageUser(content="Argue.")],
        [],
    )

    assert answer == TEXT_CALL
    assert len(calls) == 1 and notes == []


@pytest.mark.asyncio
async def test_text_tool_call_on_the_last_step_gets_the_out_of_tool_calls_message(
    monkeypatch,
):
    calls, notes = play_back(monkeypatch, [TEXT_CALL, ANSWER])

    answer = await protocol.agent_loop(
        Participant(ModelSpec("m", "p")),
        "judge",
        [ChatMessageUser(content="Audit.")],
        [noop()],
        max_steps=1,
    )

    assert answer == ANSWER
    assert calls[1][-1].text == protocol.OUT_OF_TOOL_CALLS
    assert notes == ["tool_call_written_as_text"]
