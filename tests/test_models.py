"""ModelSpec pinning rules, checked against a canned OpenRouter endpoints listing."""

import pytest
from inspect_ai.tool import ToolInfo, ToolParams

from debate_asb import models
from debate_asb.models import ModelSpec


def endpoint(
    tag, name, prompt="0.000002", completion="0.000012", quantization="unknown"
):
    return {"tag": tag, "provider_name": name, "quantization": quantization, "context_length": 1_000_000,
            "pricing": {"prompt": prompt, "completion": completion}}  # fmt: skip


@pytest.fixture(autouse=True)
def canned_endpoints(monkeypatch):
    listing = [
        endpoint("google-ai-studio", "Google AI Studio"),
        endpoint("google-ai-studio/flex", "Google AI Studio", prompt="0.000001"),
        endpoint("amazon-bedrock", "Amazon Bedrock"),
        endpoint("amazon-bedrock/eu-west-1", "Amazon Bedrock", prompt="0.0000022"),
        endpoint("deepinfra", "DeepInfra", quantization="bf16"),
        endpoint("deepinfra/turbo", "DeepInfra", quantization="fp8"),
    ]
    monkeypatch.setattr(
        models, "_openrouter_get", lambda path, key=None: {"endpoints": listing}
    )
    models._lookup_endpoint.cache_clear()


def test_provider_is_required():
    with pytest.raises(ValueError, match="needs a pinned provider"):
        ModelSpec("some/model", "")


def test_base_slug_ignores_opt_in_service_tiers():
    spec = ModelSpec("m", "google-ai-studio")
    assert spec.price().input == 2e-6
    assert spec.provider_name() == "Google AI Studio"


@pytest.mark.parametrize("slug", ["amazon-bedrock", "deepinfra"])
def test_ambiguous_base_slug_raises(slug):
    with pytest.raises(ValueError, match="ambiguous"):
        ModelSpec("m", slug).price()


def test_full_tag_resolves_ambiguity():
    assert ModelSpec("m", "deepinfra/turbo").price().input == 2e-6
    assert ModelSpec("m", "amazon-bedrock/eu-west-1").price().input == 2.2e-6


def test_unknown_provider_lists_options():
    with pytest.raises(ValueError, match="Available"):
        ModelSpec("m", "nope").price()


def test_cache_is_scoped_by_provider():
    a = ModelSpec("m", "google-ai-studio").cache_policy()
    b = ModelSpec("m", "google-vertex").cache_policy()
    assert a.scopes != b.scopes
    assert a.expiry is None


def request_body(spec: ModelSpec) -> dict:
    """The OpenRouter-specific part of the request `spec` would send (built offline)."""
    model = spec.get_model()
    return model.api.completion_params(model.config, tools=False)["extra_body"]


@pytest.mark.parametrize("effort", ["none", "medium", "xhigh"])
def test_reasoning_effort_reaches_the_request(monkeypatch, effort):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline")
    body = request_body(ModelSpec("m", "google-ai-studio", reasoning_effort=effort))
    assert body["reasoning"] == {"effort": effort}
    assert body["provider"] == {"order": ["google-ai-studio"], "allow_fallbacks": False}


def test_no_reasoning_effort_sends_none(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline")
    assert "reasoning" not in request_body(ModelSpec("m", "google-ai-studio"))


def test_tools_are_sent_without_strict_mode(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline")
    model = ModelSpec("m", "google-ai-studio").get_model()
    tool = ToolInfo(name="t", description="A tool.", parameters=ToolParams())
    assert model.api.tools_to_openai([tool])[0]["function"]["strict"] is False
