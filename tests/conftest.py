"""Shared fixtures: a scripted mock model standing in for every participant."""

import pytest
from inspect_ai.model import ModelOutput, get_model

from debate_asb import task as task_module
from debate_asb.datasets import ASB_ROOT
from debate_asb.models import ModelSpec, Price

needs_asb = pytest.mark.skipif(not ASB_ROOT.exists(), reason="ASB data not fetched")


@pytest.fixture
def script(monkeypatch):
    """Make every ModelSpec play back the given outputs, in call order, offline."""

    def install(outputs: list[ModelOutput]):
        mock = get_model("mockllm/model", custom_outputs=outputs)
        monkeypatch.setattr(ModelSpec, "get_model", lambda self: mock)
        monkeypatch.setattr(ModelSpec, "price", lambda self: Price(input=1e-6, output=2e-6))
        monkeypatch.setattr(ModelSpec, "context_length", lambda self: 1_000_000)
        monkeypatch.setattr(task_module, "check_openrouter_account", lambda: None)
        return mock

    return install


def tool_call(function: str, **arguments) -> ModelOutput:
    return ModelOutput.for_tool_call("mockllm/model", function, arguments)


def say(text: str) -> ModelOutput:
    return ModelOutput.from_content("mockllm/model", text)


def explore_then_answer(credence: int) -> list[ModelOutput]:
    return [
        tool_call("list_files"),
        tool_call("read_file", path="PAPER.md", num_lines=5),
        tool_call("search", pattern="seed"),
        say(f"Looks fine.\n\nSabotage Credence: {credence}%"),
    ]
