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


class SpendLedger:
    """SQLite reservations held across processes and surviving a crash/retry."""

    def __init__(
        self, path: Path, *, cap_usd: Decimal, smoke_reserve_usd: Decimal = Decimal(0)
    ):
        self.path = path.resolve()
        self.cap = _usd_to_nano(cap_usd, ceiling=False)
        self.smoke_reserve = _usd_to_nano(smoke_reserve_usd)
        if not 0 < self.cap <= 100 * NANO_USD or self.smoke_reserve >= self.cap:
            raise BudgetBlocked("Require a positive cap and a smaller finalist reserve")
        with self._write() as database:
            database.execute("""CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cap_nano INTEGER NOT NULL, smoke_reserve_nano INTEGER NOT NULL,
                frozen INTEGER NOT NULL DEFAULT 0
            )""")
            database.execute("""CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY, batch TEXT NOT NULL,
                reservation_nano INTEGER NOT NULL, billed_nano INTEGER,
                generation_id TEXT, outcome TEXT NOT NULL DEFAULT 'reserved',
                nominal_nano INTEGER, served_model TEXT, served_provider TEXT,
                prompt_tokens INTEGER, completion_tokens INTEGER,
                reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                recorded_at TEXT
            )""")
            database.execute(
                "INSERT OR IGNORE INTO settings VALUES (1, ?, ?, 0)",
                (self.cap, self.smoke_reserve),
            )
            saved = database.execute(
                "SELECT cap_nano, smoke_reserve_nano FROM settings WHERE id = 1"
            ).fetchone()
            if saved != (self.cap, self.smoke_reserve):
                raise BudgetBlocked(
                    "All processes must use the ledger's original cap and reserve"
                )

    def enable_quartile_stops(self) -> None:
        """Install the first cumulative $25 stage on the existing $100 ledger."""
        if self.cap != 100 * NANO_USD:
            raise BudgetBlocked("Quartile stops require a $100 ledger")
        with self._write() as database:
            database.execute("""CREATE TABLE IF NOT EXISTS budget_stages (
                limit_nano INTEGER PRIMARY KEY CHECK (limit_nano IN
                    (25000000000, 50000000000, 75000000000, 100000000000)),
                opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                closed_at TEXT, checkpoint TEXT, billed_nano INTEGER,
                unresolved_reserve_nano INTEGER
            )""")
            database.execute("""CREATE TRIGGER IF NOT EXISTS enforce_budget_stage
                BEFORE INSERT ON attempts
                WHEN NEW.reservation_nano + (
                    SELECT COALESCE(SUM(COALESCE(billed_nano, reservation_nano)), 0)
                    FROM attempts
                ) > (SELECT MAX(limit_nano) FROM budget_stages)
                BEGIN
                    SELECT RAISE(ABORT, 'Attempt would exceed the active budget stage');
                END""")
            database.execute(
                "INSERT OR IGNORE INTO budget_stages (limit_nano) VALUES (?)",
                (QUARTILE_USD * NANO_USD,),
            )
            billed, unresolved = self._charged_and_reserved(database)
            if billed + unresolved > self._stage_limit(database):
                raise BudgetBlocked(
                    "Existing commitments exceed the active budget stage"
                )

    @staticmethod
    def _charged_and_reserved(database: sqlite3.Connection) -> tuple[int, int]:
        billed, unresolved = database.execute(
            """SELECT COALESCE(SUM(billed_nano), 0),
                COALESCE(SUM(CASE WHEN billed_nano IS NULL THEN reservation_nano END), 0)
                FROM attempts"""
        ).fetchone()
        return billed, unresolved

    def _stage_limit(self, database: sqlite3.Connection) -> int:
        if (
            database.execute(
                "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'budget_stages'"
            ).fetchone()
            is None
        ):
            return self.cap
        (active,) = database.execute(
            "SELECT MAX(limit_nano) FROM budget_stages"
        ).fetchone()
        if active is None:
            raise BudgetBlocked("Budget stages have no active ceiling")
        return active

    def advance_quartile(self, *, completed_limit_usd: int, checkpoint: str) -> None:
        """Record a checkpoint and open the next quartile exactly once."""
        if completed_limit_usd not in (25, 50, 75) or not checkpoint.strip():
            raise BudgetBlocked(
                "Require the completed $25, $50 or $75 stage and a checkpoint"
            )
        previous = completed_limit_usd * NANO_USD
        following = previous + QUARTILE_USD * NANO_USD
        with self._write() as database:
            (frozen,) = database.execute(
                "SELECT frozen FROM settings WHERE id = 1"
            ).fetchone()
            if (
                frozen
                or self._stage_limit(database) != previous
                or following > self.cap
            ):
                raise BudgetBlocked(
                    "The expected stage is inactive or the ledger is frozen"
                )
            billed, unresolved = self._charged_and_reserved(database)
            database.execute(
                """UPDATE budget_stages
                SET closed_at = CURRENT_TIMESTAMP, checkpoint = ?, billed_nano = ?,
                    unresolved_reserve_nano = ? WHERE limit_nano = ?""",
                (checkpoint.strip(), billed, unresolved, previous),
            )
            database.execute(
                "INSERT INTO budget_stages (limit_nano) VALUES (?)", (following,)
            )

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        database = sqlite3.connect(self.path, timeout=10)
        try:
            database.execute("BEGIN IMMEDIATE")
            with database:
                yield database
        finally:
            database.close()

    def reserve(self, amount: int, *, batch: str, finalist: bool) -> int:
        with self._write() as database:
            (frozen,) = database.execute(
                "SELECT frozen FROM settings WHERE id = 1"
            ).fetchone()
            if frozen:
                raise BudgetBlocked(
                    "Ledger frozen after an unbounded or misrouted response"
                )
            billed, unresolved = self._charged_and_reserved(database)
            ceiling = min(
                self._stage_limit(database),
                self.cap if finalist else self.cap - self.smoke_reserve,
            )
            if amount <= 0 or billed + unresolved + amount > ceiling:
                raise BudgetBlocked("Attempt would exceed the available budget")
            attempt = database.execute(
                "INSERT INTO attempts (batch, reservation_nano) VALUES (?, ?)",
                (batch, amount),
            ).lastrowid
            if attempt is None:
                raise BudgetBlocked("Reservation ID unavailable")
            return attempt

    def record(
        self,
        attempt: int,
        outcome: str,
        *,
        billed: int | None = None,
        generation_id: str | None = None,
        nominal: int | None = None,
        served_model: str | None = None,
        served_provider: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        freeze: bool = False,
    ) -> None:
        with self._write() as database:
            (reserved,) = database.execute(
                "SELECT reservation_nano FROM attempts WHERE id = ? AND outcome = 'reserved'",
                (attempt,),
            ).fetchone()
            if billed is not None and billed > reserved:
                freeze = True
                outcome = "exceeded-reservation"
            if freeze:
                database.execute("UPDATE settings SET frozen = 1 WHERE id = 1")
            database.execute(
                """UPDATE attempts
                SET billed_nano = ?, generation_id = ?, outcome = ?, nominal_nano = ?,
                served_model = ?, served_provider = ?, prompt_tokens = ?,
                completion_tokens = ?, recorded_at = CURRENT_TIMESTAMP
                WHERE id = ?""",
                (
                    billed,
                    generation_id,
                    outcome,
                    nominal,
                    served_model,
                    served_provider,
                    prompt_tokens,
                    completion_tokens,
                    attempt,
                ),
            )
        if freeze:
            raise BudgetBlocked(f"Ledger frozen after response outcome {outcome}")

    def summary(self) -> dict:
        """Return totals without any request text, credentials or response bodies."""
        with self._write() as database:
            billed, unresolved, nominal, count = database.execute(
                """SELECT COALESCE(SUM(billed_nano), 0),
                COALESCE(SUM(CASE WHEN billed_nano IS NULL THEN reservation_nano END), 0),
                COALESCE(SUM(nominal_nano), 0), COUNT(*) FROM attempts"""
            ).fetchone()
            (frozen,) = database.execute(
                "SELECT frozen FROM settings WHERE id = 1"
            ).fetchone()
            serving = [
                dict(model=model, provider=provider, calls=calls)
                for model, provider, calls in database.execute(
                    """SELECT served_model, served_provider, COUNT(*) FROM attempts
                    WHERE served_model IS NOT NULL AND served_provider IS NOT NULL
                    GROUP BY served_model, served_provider"""
                )
            ]
            outcomes = dict(
                database.execute(
                    "SELECT outcome, COUNT(*) FROM attempts GROUP BY outcome"
                )
            )
            stage = self._stage_limit(database)
            stages = []
            if database.execute(
                "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'budget_stages'"
            ).fetchone():
                stages = [
                    dict(
                        limit_usd=limit / NANO_USD,
                        opened_at=opened_at,
                        closed_at=closed_at,
                        checkpoint=checkpoint,
                        billed_usd=None
                        if stage_billed is None
                        else stage_billed / NANO_USD,
                        unresolved_reserve_usd=None
                        if stage_unresolved is None
                        else stage_unresolved / NANO_USD,
                    )
                    for limit, opened_at, closed_at, checkpoint, stage_billed, stage_unresolved in database.execute(
                        """SELECT limit_nano, opened_at, closed_at, checkpoint, billed_nano,
                            unresolved_reserve_nano FROM budget_stages ORDER BY limit_nano"""
                    )
                ]
        return dict(
            limit_usd=self.cap / NANO_USD,
            stage_limit_usd=stage / NANO_USD,
            global_available_usd=(self.cap - billed - unresolved) / NANO_USD,
            quarter_checkpoints=stages,
            billed_usd=billed / NANO_USD,
            unresolved_reserve_usd=unresolved / NANO_USD,
            nominal_usd=nominal / NANO_USD,
            available_usd=(stage - billed - unresolved) / NANO_USD,
            smoke_available_usd=(
                min(stage, self.cap - self.smoke_reserve) - billed - unresolved
            )
            / NANO_USD,
            frozen=bool(frozen),
            attempts=count,
            serving=serving,
            outcomes=outcomes,
        )


class GuardedTransport(httpx2.AsyncBaseTransport):
    """Reserve every HTTP attempt, including SDK retries and force-answer calls."""

    def __init__(
        self,
        ledger: SpendLedger,
        terms: EndpointTerms,
        *,
        batch: str,
        finalist: bool = False,
        inner: httpx2.AsyncBaseTransport | None = None,
    ):
        self.ledger = ledger
        self.terms = terms
        self.batch = batch
        self.finalist = finalist
        self.inner = inner or httpx2.AsyncHTTPTransport(retries=0, trust_env=False)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        output_tokens = await self._check_request(request)
        attempt = self.ledger.reserve(
            self.terms.reserve_nano(output_tokens),
            batch=self.batch,
            finalist=self.finalist,
        )
        try:
            response = await self.inner.handle_async_request(request)
            await response.aread()
        except BaseException:
            # Timeout/cancellation/crash may happen after the provider accepted
            # the request. A missing response cannot release the reservation.
            self.ledger.record(attempt, "transport-unresolved")
            raise
        self._record_response(attempt, response)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()

    async def _check_request(self, request: httpx2.Request) -> int:
        if request.method != "POST" or str(request.url) != CHAT_URL:
            raise BudgetBlocked("Unexpected inference endpoint")
        if any(name.startswith("x-openrouter-") for name in request.headers):
            raise BudgetBlocked("OpenRouter request overrides are unsupported")
        try:
            body = json.loads(await request.aread())
            if not isinstance(body, dict):
                raise ValueError
            if set(body) - REQUEST_FIELDS or body["model"] != self.terms.model:
                raise ValueError
            if body["provider"] != self.terms.provider_pin():
                raise ValueError
            if (
                body.get("stream", False) is not False
                or type(body.get("n", 1)) is not int
                or body.get("n", 1) != 1
            ):
                raise ValueError
            if (
                body["usage"] != {"include": True}
                or body["usage"]["include"] is not True
            ):
                raise ValueError
            tokens = [
                body[name]
                for name in ("max_tokens", "max_completion_tokens")
                if name in body
            ]
            if len(tokens) != 1 or type(tokens[0]) is not int:
                raise ValueError
            if not 0 < tokens[0] <= self.terms.max_completion_tokens:
                raise ValueError
            reasoning = body.get("reasoning", {})
            if set(reasoning) - {"enabled", "effort", "exclude", "max_tokens"}:
                raise ValueError
            if "max_tokens" in reasoning and not (
                type(reasoning["max_tokens"]) is int
                and 0 < reasoning["max_tokens"] <= tokens[0]
            ):
                raise ValueError
            if not isinstance(body["messages"], list) or not body["messages"]:
                raise ValueError
            for message in body["messages"]:
                content = message.get("content")
                if "audio" in message or "attachments" in message:
                    raise ValueError
                if content is None or isinstance(content, str):
                    continue
                if not isinstance(content, list) or any(
                    set(part) != {"type", "text"}
                    or part["type"] != "text"
                    or not isinstance(part["text"], str)
                    for part in content
                ):
                    raise ValueError
            if any(tool.get("type") != "function" for tool in body.get("tools", [])):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise BudgetBlocked(
                "Unbounded or unsupported OpenRouter request"
            ) from error
        return tokens[0]

    def _record_response(self, attempt: int, response: httpx2.Response) -> None:
        try:
            body = json.loads(response.content, parse_float=Decimal)
        except ValueError:
            body = None
        if not isinstance(body, dict):
            body = {}
        generation = body.get("id")
        # Generation IDs are reconciliation keys. Never log the request, auth
        # headers, error body, or provider response contents.
        if not isinstance(generation, str) or not re.fullmatch(
            r"gen-[A-Za-z0-9-]{1,120}", generation
        ):
            generation = None
        usage = body.get("usage")
        if not isinstance(usage, dict) or usage.get("cost") is None:
            self.ledger.record(attempt, "billing-unresolved", generation_id=generation)
            if response.is_success:
                raise BudgetBlocked("Successful response is missing billable usage")
            return
        try:
            billed = _usd_to_nano(usage["cost"])
        except BudgetBlocked:
            self.ledger.record(
                attempt, "invalid-billing", generation_id=generation, freeze=True
            )
            raise
        if (
            body.get("provider") != SERVING_PROVIDER
            or usage.get("is_byok") is not False
        ):
            self.ledger.record(
                attempt,
                "provider-or-billing-unverified",
                generation_id=generation,
                freeze=True,
            )
        provider = body["provider"]
        model = body.get("model")
        if not isinstance(model, str) or not re.fullmatch(
            r"[A-Za-z0-9_.:/-]{1,120}", model
        ):
            model = None
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if type(prompt) is not int or prompt < 0:
            prompt = None
        if type(completion) is not int or completion < 0:
            completion = None
        nominal = (
            self.terms.nominal_nano(prompt, completion)
            if prompt is not None and completion is not None
            else None
        )
        if model is None or model not in {self.terms.model, self.terms.served_version}:
            self.ledger.record(
                attempt,
                "unexpected-model",
                billed=billed,
                generation_id=generation,
                nominal=nominal,
                served_model=model,
                served_provider=provider,
                prompt_tokens=prompt,
                completion_tokens=completion,
                freeze=True,
            )
        self.ledger.record(
            attempt,
            "billed",
            billed=billed,
            generation_id=generation,
            nominal=nominal,
            served_model=model,
            served_provider=provider,
            prompt_tokens=prompt,
            completion_tokens=completion,
        )


class _GuardedOpenRouterAPI(OpenRouterAPI):
    """Keep Inspect reinitialization from replacing a closed guarded client."""

    def __init__(
        self, transport: GuardedTransport, *, api_key: str, config: GenerateConfig
    ):
        self._guard = transport
        super().__init__(
            model_name=transport.terms.model,
            base_url=BASE_URL,
            api_key=api_key,
            config=config,
            provider=transport.terms.provider_pin(),
            stream=False,
            strict_tools=False,
            max_retries=0,
        )

    def _create_http_client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=self._guard, follow_redirects=False, trust_env=False
        )

    def completion_params(self, config: GenerateConfig, tools: bool) -> dict:
        params = super().completion_params(config, tools)
        params.setdefault("extra_body", {})["usage"] = {"include": True}
        return params


def guarded_model(
    transport: GuardedTransport, *, api_key: str, config: GenerateConfig
) -> Model:
    """Build one guarded Inspect model after authenticated preflight succeeds."""
    api = _GuardedOpenRouterAPI(transport, api_key=api_key, config=config)
    # Use the standard provider identity without registering an unguarded
    # reconstruction route or including credentials in registry parameters.
    registry_tag(lambda: None, api, RegistryInfo(type="modelapi", name="openrouter"))
    model = Model(api, config)
    api.qualified_model_name = str(model)
    return model
