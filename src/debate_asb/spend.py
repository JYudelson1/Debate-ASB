"""Real spend: what OpenRouter actually billed, read from an Inspect eval log.

Every model call Inspect makes is logged as a model event holding the raw API
response. OpenRouter's response includes `usage.cost` (USD billed) and
`provider` (who actually served the call). Cache hits are logged with
`cache == "read"` and no API call, so they cost nothing. Each call is
attributed to a role through the span protocol.generate() wraps it in.

This is the complement of the nominal cost protocols record while running
(tokens x list price, identical whether or not a call was cached).

Needs `--log-model-api` on the eval: by default Inspect keeps the raw response
for only the first 5 calls per model. (Repeated message content is
de-duplicated in the log, so this doesn't blow up log size.)

    uv run python -m debate_asb.spend logs/<file>.eval
"""

import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from inspect_ai.event import ModelEvent, SpanBeginEvent
from inspect_ai.log import EvalLog, read_eval_log

from debate_asb.models import ModelSpec

UNATTRIBUTED = "(no role)"


@dataclass
class Spend:
    by_role: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    api_calls: int = 0
    cache_hits: int = 0
    # (model, pinned provider, provider that actually served it) -> number of calls
    served_by: Counter = field(default_factory=Counter)
    unpinned_calls: int = 0
    # API calls whose raw response wasn't logged (eval run without --log-model-api)
    unlogged_calls: int = 0

    @property
    def usd(self) -> float:
        return sum(self.by_role.values())

    def __iadd__(self, other: "Spend") -> "Spend":
        for role, usd in other.by_role.items():
            self.by_role[role] += usd
        self.api_calls += other.api_calls
        self.cache_hits += other.cache_hits
        self.served_by += other.served_by
        self.unpinned_calls += other.unpinned_calls
        self.unlogged_calls += other.unlogged_calls
        return self


def sample_spend(events: list) -> Spend:
    roles = {
        e.id: e.name for e in events if isinstance(e, SpanBeginEvent) and e.type == "participant"
    }
    spend = Spend()
    for event in events:
        if not isinstance(event, ModelEvent) or not event.model.startswith("openrouter/"):
            continue
        if event.cache == "read":
            spend.cache_hits += 1
            continue
        spend.api_calls += 1
        if event.call is None:
            spend.unlogged_calls += 1
            continue
        request = event.call.request or {}
        response = event.call.response or {}
        role = roles.get(event.span_id, UNATTRIBUTED)
        spend.by_role[role] += (response.get("usage") or {}).get("cost") or 0.0
        pin = request.get("provider") or (request.get("extra_body") or {}).get("provider")
        if not pin:
            spend.unpinned_calls += 1
            continue
        model = event.model.removeprefix("openrouter/")
        spend.served_by[(model, pin["order"][0], response.get("provider"))] += 1
    return spend


def log_spend(log: EvalLog) -> Spend:
    total = Spend()
    for sample in log.samples or []:
        total += sample_spend(sample.events)
    return total


def spend_problems(spend: Spend) -> list[str]:
    """Anything that makes the spend number untrustworthy, or a pin that didn't hold."""
    problems = []
    for (model, pinned, served), n in spend.served_by.items():
        expected = ModelSpec(model, pinned).provider_name()
        if served != expected:
            problems.append(f"{n} call(s) to {model} pinned to {pinned} were served by {served}")
    if spend.unlogged_calls:
        problems.append(
            f"{spend.unlogged_calls} call(s) have no logged response, so real spend is "
            "UNDERCOUNTED and their provider is unchecked. Run evals with --log-model-api."
        )
    if spend.unpinned_calls:
        problems.append(f"{spend.unpinned_calls} call(s) had no provider pin")
    return problems


if __name__ == "__main__":
    spend = log_spend(read_eval_log(sys.argv[1]))
    print(f"Real spend: ${spend.usd:.4f} over {spend.api_calls} API calls "
          f"({spend.cache_hits} cache hits, free)")  # fmt: skip
    for role, usd in sorted(spend.by_role.items()):
        print(f"  {role}: ${usd:.4f}")
    for problem in spend_problems(spend):
        print("WARNING:", problem)
