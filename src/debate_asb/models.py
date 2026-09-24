"""Model handles: every model call goes through a ModelSpec, which pins an OpenRouter provider.

Why pin: providers serve the same model with different quantizations, so an
unpinned OpenRouter call can silently be a different model between runs. A
ModelSpec cannot be constructed without a provider, and `get_model()` always
sends `provider.order=[provider], allow_fallbacks=False`.

Two things Inspect doesn't do for us, handled here:

- Cache keying. Inspect's disk cache key does NOT include model_args (where the
  provider pin lives), so two runs pinned to different providers would share
  cache entries. We put the provider in `CachePolicy.scopes`, which is keyed.
- Pricing. We look up the pinned endpoint's price on OpenRouter so cost can be
  computed from token usage, including on cache hits (where Inspect
  records no usage or cost at all). This is *nominal* cost. Real spend
  (what OpenRouter actually billed) is read from the eval log afterwards; see
  spend.py.

Failures: Inspect retries rate limits (429), server errors and timeouts with
backoff. It does not retry 402 (out of credits) or 401 (bad key), and by
default the first failed sample fails the eval. `check_openrouter_account()`
catches a dead key or empty balance before a run starts.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from functools import cache

from inspect_ai.model import CachePolicy, GenerateConfig, Model, ModelUsage, get_model

OPENROUTER_API = "https://openrouter.ai/api/v1"

# Default for every role. Gemini 3.1 Pro was ASB's best auditor.
DEFAULT_MODEL = "google/gemini-3.1-pro-preview"
DEFAULT_PROVIDER = "google-ai-studio"
SERVICE_TIER_SUFFIXES = {"flex", "priority", "fast"}


@dataclass(frozen=True)
class Price:
    """USD per token, as OpenRouter reports it for one endpoint.

    Some models charge more for long prompts (e.g. Gemini 3.1 Pro doubles its
    price above 200k prompt tokens). `tiers` holds (min_prompt_tokens, input,
    output) overrides, so cost has to be computed per call, not on summed usage.
    """

    input: float
    output: float
    tiers: tuple[tuple[int, float, float], ...] = ()

    def cost(self, usage: ModelUsage | None) -> float:
        """Nominal USD for one call: list price, no prompt-cache discounts."""
        if usage is None:
            return 0.0
        # Inspect's input_tokens excludes cached tokens; nominal cost counts them all.
        prompt_tokens = (
            usage.input_tokens
            + (usage.input_tokens_cache_read or 0)
            + (usage.input_tokens_cache_write or 0)
        )
        input_price, output_price = self.input, self.output
        for min_tokens, tier_input, tier_output in sorted(self.tiers):
            if prompt_tokens >= min_tokens:
                input_price, output_price = tier_input, tier_output
        return prompt_tokens * input_price + usage.output_tokens * output_price


@dataclass(frozen=True)
class ModelSpec:
    """An OpenRouter model pinned to one provider endpoint.

    Args:
        model: OpenRouter model id, e.g. "anthropic/claude-sonnet-4.5".
        provider: OpenRouter provider slug, e.g. "anthropic", or a full endpoint
            tag like "google-vertex/us-east5" or "deepinfra/turbo". A base slug
            matches every endpoint variant of that provider, so if those variants
            differ in quantization or price, you must use the full tag.
        temperature: Sampling temperature (None = provider default).
        max_tokens: Max output tokens per call (None = provider default).
    """

    model: str
    provider: str
    temperature: float | None = None
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.provider:
            raise ValueError(f"ModelSpec({self.model!r}) needs a pinned provider")

    def __str__(self) -> str:
        return f"{self.model}@{self.provider}"

    def get_model(self) -> Model:
        self.price()  # validates the pin against OpenRouter before any spend
        return get_model(
            f"openrouter/{self.model}",
            provider={"order": [self.provider], "allow_fallbacks": False},
            config=GenerateConfig(
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                cache=self.cache_policy(),
                # Give up on a call after 8 retries, or an attempt after 10 minutes,
                # rather than hanging on a stuck provider.
                max_retries=8,
                attempt_timeout=600,
            ),
        )

    def cache_policy(self) -> CachePolicy:
        # expiry=None: never expire (Inspect's default is one week).
        return CachePolicy(expiry=None, scopes={"openrouter_provider": self.provider})

    def price(self) -> Price:
        return _lookup_endpoint(self.model, self.provider)[0]

    def context_length(self) -> int:
        """Max tokens (prompt + output) the pinned endpoint accepts."""
        return _lookup_endpoint(self.model, self.provider)[2]

    def provider_name(self) -> str:
        """OpenRouter's display name for the pinned provider, e.g. "Google AI Studio".

        This is what OpenRouter reports as the serving provider in each response,
        so spend.py can check every call actually went where it was pinned.
        """
        return _lookup_endpoint(self.model, self.provider)[1]


class Side(Enum):
    """Which answer a debater argues for. Judges have no side."""

    SABOTAGED = "sabotaged"
    CLEAN = "clean"


@dataclass(frozen=True)
class Participant:
    """Everything that makes a model a participant in a protocol.

    An untrusted debater that knows where the sabotage is and argues it's fine
    is just a Participant with a particular system prompt: protocols don't
    special-case it.
    """

    model: ModelSpec
    system_prompt: str | None = None
    side: Side | None = None


class OpenRouterAccountError(RuntimeError):
    """The key is missing or rejected, or there's no money left to spend."""


def _openrouter_get(path: str, api_key: str | None = None) -> dict:
    request = urllib.request.Request(OPENROUTER_API + path)
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["data"]


def check_openrouter_account() -> None:
    """Fail fast, before any samples run, if the OpenRouter key can't pay for calls.

    Checks the key is set and accepted, the key's own spend limit isn't used up,
    and the account has credit left. Mid-run exhaustion is still possible; the
    run then stops on the first 402, and `inspect eval-retry <log>` resumes it
    (completed model calls replay from the disk cache for free).
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise OpenRouterAccountError("OPENROUTER_API_KEY is not set (put it in .env).")
    try:
        key = _openrouter_get("/key", api_key)
        credits = _openrouter_get("/credits", api_key)
    except urllib.error.HTTPError as e:
        raise OpenRouterAccountError(
            f"OpenRouter rejected the key: HTTP {e.code}"
        ) from e

    if key.get("limit") is not None and key.get("limit_remaining", 1) <= 0:
        raise OpenRouterAccountError(
            f"This key has used its whole spend limit (${key['limit']}). "
            "Raise the limit or use another key."
        )
    balance = credits["total_credits"] - credits["total_usage"]
    if balance <= 0:
        raise OpenRouterAccountError(
            f"The OpenRouter account has no credit left (balance ${balance:.2f}). "
            "Add credits at https://openrouter.ai/settings/credits."
        )


@cache
def _lookup_endpoint(model: str, provider: str) -> tuple[Price, str, int]:
    """Find the pinned endpoint on OpenRouter; return its price, provider name, context length.

    Raises if no endpoint matches, or if the provider slug matches several
    endpoints that differ in quantization or price: that means the pin is
    ambiguous and the caller should use the full endpoint tag.
    """
    endpoints = _openrouter_get(f"/models/{model}/endpoints")["endpoints"]

    # A base slug matches its regional/variant endpoints, but not service-tier
    # endpoints, which OpenRouter only routes to on explicit opt-in.
    matches = [
        e
        for e in endpoints
        if e["tag"] == provider
        or (
            e["tag"].startswith(provider + "/")
            and e["tag"].rsplit("/", 1)[-1] not in SERVICE_TIER_SUFFIXES
        )
    ]
    if not matches:
        available = sorted(e["tag"] for e in endpoints)
        raise ValueError(
            f"No OpenRouter endpoint for {model} matches provider {provider!r}. "
            f"Available: {available}"
        )

    variants = {
        (
            e.get("quantization"),
            e["pricing"]["prompt"],
            e["pricing"]["completion"],
            e["provider_name"],
        )
        for e in matches
    }
    if len(variants) > 1:
        tags = sorted(e["tag"] for e in matches)
        raise ValueError(
            f"Provider {provider!r} for {model} is ambiguous: it matches endpoints "
            f"{tags} which differ in quantization or price. Pin one of those tags."
        )

    pricing = matches[0]["pricing"]
    tiers = tuple(
        (
            int(o["min_prompt_tokens"]),
            float(o.get("prompt", pricing["prompt"])),
            float(o.get("completion", pricing["completion"])),
        )
        for o in pricing.get("overrides") or []
    )
    price = Price(float(pricing["prompt"]), float(pricing["completion"]), tiers)
    return price, matches[0]["provider_name"], int(matches[0]["context_length"])
