"""OpenRouter spending guard for a text-only, pinned ASB sprint.

Use one ledger file for every process and route every inference attempt through
this transport. Network verification of the endpoint, account credit and billing
terms is a prerequisite for a live launch. This module performs no preflight
network calls and cannot impose a cap on clients that do not use it.
"""

import json
import math
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

import httpx2
from inspect_ai._util.registry import RegistryInfo, registry_tag
from inspect_ai.model import GenerateConfig, Model
from inspect_ai.model._providers.openrouter import OpenRouterAPI

NANO_USD = 1_000_000_000
QUARTILE_USD = 25
MODEL = "openai/gpt-6-luna"
SOL_MODEL = "openai/gpt-6.1-sol"
TERRA_MODEL = "openai/gpt-6.1-terra"
SUPPORTED_MODELS = frozenset({MODEL, SOL_MODEL, TERRA_MODEL})
PROVIDER = "openai"
SERVING_PROVIDER = "OpenAI"
BASE_URL = "https://openrouter.ai/api/v1"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_FIELDS = frozenset(
    {
        "model",
        "provider",
        "messages",
        "tools",
        "tool_choice",
        "reasoning",
        "max_tokens",
        "max_completion_tokens",
        "temperature",
        "top_p",
        "seed",
        "stream",
        "n",
        "parallel_tool_calls",
        "usage",
    }
)


class BudgetBlocked(RuntimeError):
    """This attempt must stop before it is sent or the ledger must be frozen."""


def _usd_to_nano(value: object, *, ceiling: bool = True) -> int:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount >= 1_000_000:
            raise ValueError
        scaled = amount * NANO_USD
        if not ceiling and scaled != scaled.to_integral_value():
            raise ValueError
        return int(scaled.to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, ValueError) as error:
        raise BudgetBlocked("Invalid USD amount") from error


@dataclass(frozen=True)
class EndpointTerms:
    """Bounds established from the pinned endpoint before an authorized launch."""

    context_tokens: int
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    max_completion_tokens: int
    max_prompt_tokens: int | None = None
    base_input_usd_per_million: Decimal | None = None
    base_output_usd_per_million: Decimal | None = None
    nominal_tiers: tuple[tuple[int, Decimal, Decimal], ...] = ()
    model: str = MODEL
    served_version: str | None = None

    def __post_init__(self) -> None:
        if self.model not in SUPPORTED_MODELS:
            raise BudgetBlocked("The model is outside the sprint allowlist")
        if not 0 < self.max_completion_tokens <= self.context_tokens:
            raise BudgetBlocked("Invalid verified token bounds")
        if self.max_prompt_tokens is not None and not (
            0 < self.max_prompt_tokens <= self.context_tokens
        ):
            raise BudgetBlocked("Invalid verified prompt token bound")
        for price in (self.input_usd_per_million, self.output_usd_per_million):
            if _usd_to_nano(price) <= 0:
                raise BudgetBlocked("Invalid verified token price")

    @classmethod
    def from_endpoint(cls, endpoint: dict, *, model: str = MODEL) -> "EndpointTerms":
        """Accept only the verified, exact standard route with known text fees."""
        if (
            model not in SUPPORTED_MODELS
            or endpoint.get("tag") != PROVIDER
            or endpoint.get("model_id") != model
            or endpoint.get("provider_name") != SERVING_PROVIDER
            or endpoint.get("service_tier") not in (None, "standard")
        ):
            raise BudgetBlocked(
                "Expected the exact OpenAI standard endpoint for the requested model"
            )
        try:
            rates = endpoint["pricing"]
            overrides = rates.get("overrides") or []
            if any(
                Decimal(str(tier.get("request", 0))) != 0
                for tier in (rates, *overrides)
            ):
                raise BudgetBlocked("Unexpected flat request fee")

            def per_million(value: object) -> Decimal:
                rate = Decimal(str(value)) * 1_000_000
                _usd_to_nano(rate)
                return rate

            base_input = per_million(rates["prompt"])
            base_output = per_million(rates["completion"])
            inbound = [base_input]
            outbound = [base_output]
            for tier in (rates, *overrides):
                inbound.extend(
                    per_million(tier[key])
                    for key in ("prompt", "input_cache_read", "input_cache_write")
                    if key in tier
                )
                outbound.extend(
                    per_million(tier[key])
                    for key in ("completion", "internal_reasoning")
                    if key in tier
                )
            bounds = (
                endpoint["context_length"],
                endpoint["max_completion_tokens"],
                endpoint.get("max_prompt_tokens"),
            )
            if any(type(bound) is not int for bound in bounds[:2]) or (
                bounds[2] is not None and type(bounds[2]) is not int
            ):
                raise BudgetBlocked("Expected verified integer token limits")
            tiers = tuple(
                sorted(
                    (
                        int(tier["min_prompt_tokens"]),
                        per_million(tier.get("prompt", rates["prompt"])),
                        per_million(tier.get("completion", rates["completion"])),
                    )
                    for tier in overrides
                )
            )
            served_version = str(endpoint.get("name", "")).removeprefix("OpenAI | ")
            if not re.fullmatch(re.escape(model) + r"-[0-9]{8}", served_version):
                served_version = None
            return cls(
                bounds[0],
                max(inbound),
                max(outbound),
                bounds[1],
                bounds[2],
                base_input,
                base_output,
                tiers,
                model,
                served_version,
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            raise BudgetBlocked(
                "Invalid OpenRouter endpoint prices or token limits"
            ) from error

    def provider_pin(self) -> dict:
        def numeric_cap(price: Decimal) -> float:
            value = float(price)
            return math.nextafter(value, 0) if Decimal(str(value)) > price else value

        return {
            "order": [PROVIDER],
            "only": [PROVIDER],
            "allow_fallbacks": False,
            "require_parameters": True,
            "max_price": {
                "prompt": numeric_cap(self.input_usd_per_million),
                "completion": numeric_cap(self.output_usd_per_million),
                "request": 0,
            },
        }

    def reserve_nano(self, completion_tokens: int) -> int:
        if not 0 < completion_tokens <= self.max_completion_tokens:
            raise BudgetBlocked("Output cap exceeds verified endpoint bounds")
        # The endpoint's accepted input limit covers hidden formatter overhead.
        input_cost = (
            self.max_prompt_tokens or self.context_tokens
        ) * self.input_usd_per_million
        output_cost = completion_tokens * self.output_usd_per_million
        return _usd_to_nano((input_cost + output_cost) / 1_000_000)

    def nominal_nano(self, prompt: int, completion: int) -> int:
        """List price before provider caching discounts."""
        inbound = self.base_input_usd_per_million or self.input_usd_per_million
        outbound = self.base_output_usd_per_million or self.output_usd_per_million
        for minimum, tier_input, tier_output in self.nominal_tiers:
            if prompt >= minimum:
                inbound, outbound = tier_input, tier_output
        return _usd_to_nano((prompt * inbound + completion * outbound) / 1_000_000)


@dataclass(frozen=True)
class OpenRouterPreflight:
    terms: EndpointTerms
    endpoint: dict
    available_credit_usd: Decimal
    credit_source: str
    key_limit_remaining_usd: Decimal | None


def verified_preflight(
    api_key: str,
    *,
    model: str = MODEL,
    max_output_tokens: int,
    management_key: str | None = None,
    verified_credit_usd: Decimal | None = None,
) -> OpenRouterPreflight:
    """Read account and provider metadata without submitting inference."""
    if not api_key or model not in SUPPORTED_MODELS:
        raise BudgetBlocked("A paid OpenRouter key and allowlisted model are required")

    def number(value: object) -> Decimal:
        _usd_to_nano(value)
        return Decimal(str(value))

    with httpx2.Client(timeout=20, trust_env=False, follow_redirects=False) as client:

        def get(path: str, credential: str | None = None) -> dict | None:
            headers = {"Authorization": f"Bearer {credential}"} if credential else None
            response = client.get(f"{BASE_URL}/{path}", headers=headers)
            if path == "credits" and response.status_code in (401, 403):
                return None
            if not response.is_success:
                raise BudgetBlocked(
                    f"OpenRouter {path} returned HTTP {response.status_code}"
                )
            data = json.loads(response.content, parse_float=Decimal)["data"]
            if not isinstance(data, dict):
                raise BudgetBlocked(f"OpenRouter {path} returned unexpected data")
            return data

        key = get("key", api_key)
        if (
            key is None
            or key.get("is_management_key")
            or key.get("is_free_tier") is not False
        ):
            raise BudgetBlocked("A paid OpenRouter inference key is required")
        remaining = key.get("limit_remaining")
        key_remaining = None if remaining is None else number(remaining)
        credits = get("credits", management_key or api_key)
        if credits is not None:
            available = number(credits["total_credits"]) - number(
                credits["total_usage"]
            )
            credit_source = "OpenRouter credits API"
        elif verified_credit_usd is not None:
            available = number(verified_credit_usd)
            credit_source = "Operator-verified OpenRouter dashboard balance"
        else:
            raise BudgetBlocked(
                "Account credit unavailable; a verified dashboard balance is required"
            )
        if key_remaining is not None:
            available = min(available, key_remaining)
        if available <= 0:
            raise BudgetBlocked("Verified OpenRouter credit is exhausted or invalid")
        listing = get(f"models/{model}/endpoints")
    if listing is None or listing.get("id") != model:
        raise BudgetBlocked("OpenRouter endpoint listing returned an unexpected model")
    endpoints = [row for row in listing["endpoints"] if row.get("tag") == PROVIDER]
    if len(endpoints) != 1:
        raise BudgetBlocked("Expected one exact OpenAI standard endpoint")
    endpoint = endpoints[0]
    terms = EndpointTerms.from_endpoint(endpoint, model=model)
    terms.reserve_nano(max_output_tokens)
    return OpenRouterPreflight(terms, endpoint, available, credit_source, key_remaining)
