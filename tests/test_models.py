"""ModelSpec pinning rules, checked against a canned OpenRouter endpoints listing."""

import pytest

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
