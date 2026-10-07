"""Focused tests for protocol helpers that do not require the ASB dataset."""

import pytest
from inspect_ai.model import ChatMessage, ChatMessageTool, ChatMessageUser, ModelOutput

from debate_asb import protocol
from debate_asb.models import ModelSpec, Participant


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
