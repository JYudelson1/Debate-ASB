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
    GuardedTransport,
    SpendLedger,
)

TERMS = EndpointTerms(1_050_000, Decimal("0.25"), Decimal("0.75"), 128_000)
PROMPT: list[ChatCompletionMessageParam] = [{"role": "user", "content": "Hello"}]


def body(**overrides):
    return {
        "model": MODEL,
        "messages": PROMPT,
        "provider": TERMS.provider_pin(),
        "max_tokens": 2048,
        "usage": {"include": True},
    } | overrides


def completion(*, cost=0.01, provider="OpenAI", is_byok=False, model=MODEL):
    return httpx2.Response(
        200,
        json={
            "id": "gen-mocked-result",
            "created": 1,
            "model": model,
            "provider": provider,
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "cost": cost,
                "is_byok": is_byok,
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "total_tokens": 3,
            },
        },
    )


def rows(path):
    with sqlite3.connect(path) as database:
        return database.execute(
            "SELECT reservation_nano, billed_nano, outcome FROM attempts ORDER BY id"
        ).fetchall()


def transport(path, responder, *, cap="1", reserve="0", finalist=False):
    ledger = SpendLedger(path, cap_usd=Decimal(cap), smoke_reserve_usd=Decimal(reserve))
    return GuardedTransport(
        ledger,
        TERMS,
        batch="mock",
        finalist=finalist,
        inner=httpx2.MockTransport(responder),
    )


def _reserve_process(path, amount):
    ledger = SpendLedger(path, cap_usd=Decimal(1))
    try:
        ledger.reserve(amount, batch="race", finalist=False)
    except BudgetBlocked:
        return False
    return True


def test_concurrent_processes_use_same_ceiling(tmp_path):
    path = tmp_path / "cost.sqlite"
    amount = TERMS.reserve_nano(2048)
    with ProcessPoolExecutor(max_workers=4) as pool:
        permitted = list(pool.map(_reserve_process, [path] * 8, [amount] * 8))
    assert sum(permitted) == 3
    assert rows(path) == [(amount, None, "reserved")] * 3
    with pytest.raises(BudgetBlocked, match="original cap"):
        SpendLedger(path, cap_usd=Decimal("0.9"))
    with pytest.raises(BudgetBlocked):
        SpendLedger(tmp_path / "oversized.sqlite", cap_usd=Decimal("100.000000001"))


def _reserve_in_quartile(path):
    ledger = SpendLedger(path, cap_usd=Decimal(100), smoke_reserve_usd=Decimal(70))
    try:
        ledger.reserve(3_000_000_000, batch="quartile-race", finalist=True)
    except BudgetBlocked:
        return False
    return True


def test_quartiles_preserve_billing_reserves_and_concurrent_ceiling(tmp_path):
    path = tmp_path / "quartile.sqlite"
    ledger = SpendLedger(path, cap_usd=Decimal(100), smoke_reserve_usd=Decimal(70))
    past = ledger.reserve(2_000_000_000, batch="past", finalist=False)
    ledger.record(past, "billed", billed=1_000_000_000, nominal=2_000_000_000)
    unresolved = ledger.reserve(2_000_000_000, batch="past", finalist=False)
    ledger.record(unresolved, "transport-unresolved")
    ledger.enable_quartile_stops()
    assert ledger.summary()["available_usd"] == 22
    with ProcessPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(_reserve_in_quartile, [path] * 12)) == 7
    summary = ledger.summary()
    assert summary["stage_limit_usd"] == 25
    assert summary["available_usd"] == 1
    assert summary["global_available_usd"] == 76
    assert summary["nominal_usd"] == 2
    with pytest.raises(sqlite3.IntegrityError, match="active budget stage"):
        with sqlite3.connect(path) as raw:
            raw.execute(
                "INSERT INTO attempts(batch,reservation_nano) VALUES ('old runner', 1000000001)"
            )
    ledger.advance_quartile(completed_limit_usd=25, checkpoint="Reviewed wave 1")
    ledger.enable_quartile_stops()
    summary = ledger.summary()
    assert summary["stage_limit_usd"] == 50
    assert summary["available_usd"] == 26
    assert summary["smoke_available_usd"] == 6
    assert summary["quarter_checkpoints"][0]["billed_usd"] == 1
    assert summary["quarter_checkpoints"][0]["unresolved_reserve_usd"] == 23
    assert summary["quarter_checkpoints"][0]["checkpoint"] == "Reviewed wave 1"
    with pytest.raises(BudgetBlocked, match="inactive"):
        ledger.advance_quartile(completed_limit_usd=25, checkpoint="Stale")
    with pytest.raises(BudgetBlocked, match="inactive"):
        ledger.advance_quartile(completed_limit_usd=75, checkpoint="Skip ahead")
    ledger.advance_quartile(completed_limit_usd=50, checkpoint="Reviewed wave 2")
    ledger.advance_quartile(completed_limit_usd=75, checkpoint="Reviewed wave 3")
    assert ledger.summary()["stage_limit_usd"] == 100
    ledger.reserve(76_000_000_000, batch="final", finalist=True)
    with pytest.raises(BudgetBlocked, match="available budget"):
        ledger.reserve(1, batch="too far", finalist=True)


@pytest.mark.asyncio
async def test_unknown_cost_and_transport_failure_retain_reserve(tmp_path):
    path = tmp_path / "cost.sqlite"
    outbound = 0

    async def responder(request):
        nonlocal outbound
        outbound += 1
        if outbound == 1:
            raise httpx2.ReadTimeout("mocked lost response", request=request)
        return httpx2.Response(502, json={"error": {"code": 502}})

    async with httpx2.AsyncClient(
        transport=transport(path, responder, cap="0.53")
    ) as client:
        with pytest.raises(httpx2.ReadTimeout):
            await client.post(CHAT_URL, json=body())
        assert (await client.post(CHAT_URL, json=body())).status_code == 502
        with pytest.raises(BudgetBlocked, match="available budget"):
            await client.post(CHAT_URL, json=body())
    assert outbound == 2
    amount = TERMS.reserve_nano(2048)
    assert rows(path) == [
        (amount, None, "transport-unresolved"),
        (amount, None, "billing-unresolved"),
    ]


@pytest.mark.asyncio
async def test_finalist_reserve_and_successful_reconciliation(tmp_path):
    path = tmp_path / "cost.sqlite"
    responder = lambda _: completion(cost=0.01)
    async with httpx2.AsyncClient(
        transport=transport(path, responder, cap="0.5", reserve="0.25")
    ) as client:
        with pytest.raises(BudgetBlocked, match="available budget"):
            await client.post(CHAT_URL, json=body())
    async with httpx2.AsyncClient(
        transport=transport(path, responder, finalist=True, cap="0.5", reserve="0.25")
    ) as client:
        for _ in range(3):
            assert (await client.post(CHAT_URL, json=body())).status_code == 200
    assert rows(path) == [(TERMS.reserve_nano(2048), 10_000_000, "billed")] * 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": {"order": ["openai"], "allow_fallbacks": True}},
        {"model": "openai/gpt-6-sol"},
        {"max_tokens": None},
        {"max_completion_tokens": 2048},
        {"n": 2},
        {"stream": True},
        {"plugins": [{"id": "web"}]},
        {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": "data:,x"}}],
                }
            ]
        },
        {"tools": [{"type": "web_search"}]},
        {"reasoning": {"max_tokens": 2049}},
    ],
)
async def test_unsupported_request_never_reaches_transport(tmp_path, overrides):
    path = tmp_path / "cost.sqlite"

    def responder(_):
        pytest.fail("Unexpected outbound request")

    async with httpx2.AsyncClient(transport=transport(path, responder)) as client:
        with pytest.raises(BudgetBlocked, match="unsupported"):
            await client.post(CHAT_URL, json=body(**overrides))
    assert rows(path) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, expected",
    [
        (completion(cost=0.3), "exceeded-reservation"),
        (completion(provider="Azure"), "provider-or-billing-unverified"),
        (completion(is_byok=True), "provider-or-billing-unverified"),
        (completion(model=SOL_MODEL), "unexpected-model"),
    ],
)
async def test_violation_freezes_all_later_attempts(tmp_path, response, expected):
    path = tmp_path / "cost.sqlite"
    async with httpx2.AsyncClient(
        transport=transport(path, lambda _: response)
    ) as client:
        with pytest.raises(BudgetBlocked, match=expected):
            await client.post(CHAT_URL, json=body())
        with pytest.raises(BudgetBlocked, match="Ledger frozen"):
            await client.post(CHAT_URL, json=body())
    assert rows(path)[0][2] == expected


@pytest.mark.asyncio
async def test_success_with_missing_billing_is_rejected_and_keeps_reserve(tmp_path):
    path = tmp_path / "cost.sqlite"
    responder = lambda _: httpx2.Response(200, json={"id": "gen-unsettled"})
    async with httpx2.AsyncClient(transport=transport(path, responder)) as client:
        with pytest.raises(BudgetBlocked, match="missing billable usage"):
            await client.post(CHAT_URL, json=body())
    assert rows(path) == [(TERMS.reserve_nano(2048), None, "billing-unresolved")]


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
