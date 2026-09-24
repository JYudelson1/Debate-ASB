"""Run a protocol n times over a dataset, and report every run plus summary statistics.

A "run" is one pass over the dataset (one Inspect epoch). The report keeps
every individual result; the spread across runs is data, not noise to average
away.

From Python:

    from debate_asb.runner import run
    from debate_asb.protocols.lone_judge import lone_judge
    report = run(lone_judge(sample_ids=["hop_jump", "hop_stride"]), n_runs=3)
    report.print()

Or on any existing log (e.g. from `inspect eval`):

    uv run python -m debate_asb.runner logs/<file>.eval
"""

import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass

from inspect_ai import Task, eval
from inspect_ai.log import EvalLog, read_eval_log

from debate_asb.spend import Spend, sample_spend, spend_problems
from debate_asb.task import auroc_of, rate_flagged


@dataclass
class Report:
    rows: list[dict]  # one per (sample, run)
    runs: list[dict]  # one per run
    summary: dict
    warnings: list[str]

    def print(self) -> None:
        print(
            f"{'sample':<24}{'run':>4}  {'label':<10}{'credence':>9}{'nominal $':>11}{'real $':>9}"
        )
        for r in self.rows:
            credence = "-" if r["credence"] is None else f"{r['credence']:.0f}%"
            print(f"{r['sample_id']:<24}{r['run']:>4}  {r['label']:<10}{credence:>9}"
                  f"{r['nominal_cost']:>11.3f}{r['real_spend']:>9.3f}")  # fmt: skip
        print()
        for run in self.runs:
            print(f"run {run['run']}: AUROC {_fmt(run['auroc'])}, catch rate {_fmt(run['catch_rate'])},"
                  f" false positive rate {_fmt(run['false_positive_rate'])} over {run['n_scored']} samples"
                  f" ({run['n_unscored']} without a credence), nominal ${run['nominal_cost']:.2f},"
                  f" real ${run['real_spend']:.2f}")  # fmt: skip
        print()
        s = self.summary
        print(f"Across {s['n_runs']} run(s), mean (variance):")
        for key, name in [
            ("auroc", "AUROC"),
            ("catch_rate", "catch rate"),
            ("false_positive_rate", "false positive rate"),
        ]:
            print(f"  {name}: {_fmt(s[key + '_mean'])} ({_fmt(s[key + '_variance'])})")
        if not s["credences_by_label"].get("honest"):
            print(
                "  (no honest samples: AUROC and false positive rate are undefined, and catch rate is only"
                " meaningful relative to other protocols, since always saying 'sabotaged' catches 100%)"
            )
        print(
            f"Cost per run: nominal ${s['nominal_cost_per_run']:.2f}, real ${s['real_spend_per_run']:.2f}"
        )
        for role in sorted(
            set(s["nominal_cost_per_role"]) | set(s["real_spend_per_role"])
        ):
            print(f"  {role}: nominal ${s['nominal_cost_per_role'].get(role, 0.0):.2f}/run,"
                  f" real ${s['real_spend_per_role'].get(role, 0.0):.2f}/run")  # fmt: skip
        for label, credences in sorted(s["credences_by_label"].items()):
            print(f"Credences, {label}: {sorted(credences)}")
        for warning in self.warnings:
            print("WARNING:", warning)


def run(task: Task, n_runs: int = 1, log_dir: str = "logs", **eval_args) -> Report:
    """Run `task` n_runs times (as Inspect epochs) and report on the log."""
    [log] = eval(
        task,
        epochs=n_runs,
        model="none",
        log_model_api=True,
        log_dir=log_dir,
        **eval_args,
    )
    return report(log)


def report(log: EvalLog | str) -> Report:
    if isinstance(log, str):
        log = read_eval_log(log)

    rows, total_spend = [], Spend()
    for sample in log.samples or []:
        spend = sample_spend(sample.events)
        total_spend += spend
        nominal = sample.store.get("cost", {})
        rows.append({
            "sample_id": str(sample.id),
            "run": sample.epoch,
            "label": sample.target,
            "credence": sample.store.get("credence"),
            "nominal_cost": sum(nominal.values()),
            "nominal_cost_by_role": nominal,
            "real_spend": spend.usd,
            "real_spend_by_role": dict(spend.by_role),
            "error": sample.error.message if sample.error else None,
        })  # fmt: skip

    runs = []
    for epoch in sorted({r["run"] for r in rows}):
        in_run = [r for r in rows if r["run"] == epoch]
        scored = [r for r in in_run if r["credence"] is not None]
        sabotaged = [r["credence"] for r in scored if r["label"] == "sabotaged"]
        honest = [r["credence"] for r in scored if r["label"] == "honest"]
        runs.append({
            "run": epoch,
            "auroc": auroc_of(sabotaged, honest),
            "catch_rate": rate_flagged(sabotaged),
            "false_positive_rate": rate_flagged(honest),
            "n_scored": len(scored),
            "n_unscored": len(in_run) - len(scored),
            "nominal_cost": sum(r["nominal_cost"] for r in in_run),
            "real_spend": sum(r["real_spend"] for r in in_run),
        })  # fmt: skip

    n_runs = max(len(runs), 1)
    nominal_by_role, real_by_role, credences = (
        defaultdict(float),
        defaultdict(float),
        defaultdict(list),
    )
    for r in rows:
        for role, usd in r["nominal_cost_by_role"].items():
            nominal_by_role[role] += usd / n_runs
        for role, usd in r["real_spend_by_role"].items():
            real_by_role[role] += usd / n_runs
        if r["credence"] is not None:
            credences[r["label"]].append(r["credence"])

    summary = {
        "n_runs": len(runs),
        **_mean_and_variance(runs, "auroc"),
        **_mean_and_variance(runs, "catch_rate"),
        **_mean_and_variance(runs, "false_positive_rate"),
        "nominal_cost_per_run": sum(r["nominal_cost"] for r in runs) / n_runs,
        "real_spend_per_run": sum(r["real_spend"] for r in runs) / n_runs,
        "nominal_cost_per_role": dict(nominal_by_role),
        "real_spend_per_role": dict(real_by_role),
        "credences_by_label": dict(credences),
    }
    warnings = spend_problems(total_spend)
    warnings += [
        f"{r['sample_id']} run {r['run']} errored: {r['error']}"
        for r in rows
        if r["error"]
    ]
    return Report(rows=rows, runs=runs, summary=summary, warnings=warnings)


def _mean_and_variance(runs: list[dict], key: str) -> dict:
    values = [r[key] for r in runs if not math.isnan(r[key])]
    return {
        f"{key}_mean": statistics.mean(values) if values else math.nan,
        f"{key}_variance": statistics.variance(values) if len(values) > 1 else math.nan,
    }


def _fmt(x: float) -> str:
    return "n/a" if math.isnan(x) else f"{x:.3f}"


if __name__ == "__main__":
    report(sys.argv[1]).print()
