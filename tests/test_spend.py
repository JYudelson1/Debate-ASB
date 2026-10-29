"""Real spend from logged model events, including failed attempts Inspect retried."""

from inspect_ai.event import ModelEvent, SpanBeginEvent
from inspect_ai.model import GenerateConfig, ModelCall, ModelOutput

from debate_asb.models import ModelSpec
from debate_asb.spend import sample_spend, spend_problems


def model_event(response: dict, error: str | None = None) -> ModelEvent:
    return ModelEvent(
        model="openrouter/m", input=[], tools=[], tool_choice="auto", config=GenerateConfig(),
        output=ModelOutput(), error=error, span_id="s1",
        call=ModelCall(request={"extra_body": {"provider": {"order": ["p"]}}}, response=response),
    )  # fmt: skip


def test_a_failed_attempt_is_not_a_served_call(monkeypatch):
    monkeypatch.setattr(ModelSpec, "provider_name", lambda self: "P")
    spend = sample_spend([
        SpanBeginEvent(id="s1", name="judge", type="participant"),
        model_event({"code": 429}, error="Error code: 429 - Rate limit exceeded"),
        model_event({"provider": "P", "usage": {"cost": 0.5}}),
    ])  # fmt: skip
    assert (spend.api_calls, spend.failed_attempts) == (1, 1)
    assert dict(spend.by_role) == {"judge": 0.5}
    assert dict(spend.served_by) == {("m", "p", "P"): 1}
    assert spend_problems(spend) == []
