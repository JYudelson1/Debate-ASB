import asyncio
import json
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal

import httpx2
import pytest
from inspect_ai.model import GenerateConfig
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from debate_asb.openrouter_budget import (
    BASE_URL,
    CHAT_URL,
    MODEL,
    SOL_MODEL,
    TERRA_MODEL,
    BudgetBlocked,
    EndpointTerms,
)

TERMS = EndpointTerms(1_050_000, Decimal("0.25"), Decimal("0.75"), 128_000)
PROMPT: list[ChatCompletionMessageParam] = [{"role": "user", "content": "Hello"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_id,date,price_ratio,reserve",
    [
        (SOL_MODEL, "20260929", Decimal("1"), 4_794_320_000),
        (TERRA_MODEL, "20261026", Decimal("0.5"), 2_397_160_000),
    ],
)
async def test_allowlisted_model_uses_exact_standard_endpoint(
    tmp_path, model_id, date, price_ratio, reserve
):
    version = f"{model_id}-{date}"

    def rate(sol_rate):
        return str(Decimal(sol_rate) * price_ratio)

    endpoint = {
        "model_id": model_id,
        "name": f"OpenAI | {version}",
        "tag": "openai",
        "provider_name": "OpenAI",
        "context_length": 1_050_000,
        "max_prompt_tokens": 922_000,
        "max_completion_tokens": 128_000,
        "pricing": {
            "prompt": rate("0.000002"),
            "completion": rate("0.00001"),
            "input_cache_write": rate("0.0000025"),
            "overrides": [
                {
                    "min_prompt_tokens": 272_000,
                    "prompt": rate("0.000004"),
                    "completion": rate("0.000015"),
                    "input_cache_write": rate("0.000005"),
                }
            ],
        },
    }
    terms = EndpointTerms.from_endpoint(endpoint, model=model_id)
    assert terms.reserve_nano(12_288) == reserve
    with pytest.raises(BudgetBlocked, match="exact"):
        EndpointTerms.from_endpoint(endpoint | {"tag": "openai/flex"}, model=model_id)
    with pytest.raises(BudgetBlocked, match="exact"):
        EndpointTerms.from_endpoint(endpoint)
