"""Combine extraction logs in the existing bundle viewer, including honest controls.

No model calls or semantic labels. Source logs and separately billed attempts
remain explicit. This also supports a partially completed run for monitoring.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from inspect_ai.log import read_eval_log

from debate_asb.spend import sample_spend
from debate_asb.viewer.adapter import load_report
from debate_asb.viewer.render import render_report_data


def attempt_spend(paths: list[Path]) -> dict:
    """Count each persisted model event once across Inspect retry logs."""
    seen = set()
    events = []
    manifest = []
    for path in paths:
        log = read_eval_log(str(path))
        manifest.append(
            dict(
                path=str(path.resolve()),
                status=log.status,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
        for sample in log.samples or []:
            for event in sample.events:
                if event.event == "span_begin":
                    events.append(event)
                elif event.event == "model":
                    identity = (str(sample.id), sample.epoch, event.uuid)
                    if identity not in seen:
                        seen.add(identity)
                        events.append(event)
    spend = sample_spend(events)
    return dict(
        billed_total=spend.usd,
        api_calls=spend.api_calls,
        unlogged_calls=spend.unlogged_calls,
        unpinned_calls=spend.unpinned_calls,
        providers=[
            dict(model=model, pinned=pinned, served=served, calls=count)
            for (model, pinned, served), count in spend.served_by.items()
        ],
        logs=manifest,
        scope="Distinct persisted model events across all attempts; interrupted/in-flight calls may be absent.",
    )


def build(config_path: Path, output: Path) -> dict:
    config_path = config_path.resolve()
    base = config_path.parent
    config = json.loads(config_path.read_text())
    samples = []
    runs = []
    for name, options in config["conditions"].items():
        folder = base / name
        status_path = folder / "status.json"
        status = json.loads(status_path.read_text()) if status_path.exists() else {}
        logs = sorted(
            folder.rglob("*.eval"),
            key=lambda p: (
                int(p.parent.name.removeprefix("round-"))
                if p.parent.name.startswith("round-")
                else 0
            ),
        )
        if not logs:
            continue
        spending = attempt_spend(logs)
        log = Path(status["log"]) if status.get("log") else logs[-1]
        report = load_report(log).to_dict()
        expected = {
            (str(sid), epoch)
            for sid in options["sample_ids"]
            for epoch in range(1, config["epochs"] + 1)
        }
        actual = [(sample["id"], sample["epoch"]) for sample in report["samples"]]
        assert len(set(actual)) == len(actual), f"Duplicate sample/epoch in {name}"
        assert set(actual) <= expected, f"Unexpected sample/epoch in {name}"
        for key in (
            "hypotheses",
            "total_steps",
            "plan_steps",
            "plan_cost_fraction",
            "max_cost",
        ):
            chosen = options.get(key, config.get(key))
            if chosen is not None:
                assert report["task_args"][key] == chosen, (name, key)
        for sample in report["samples"]:
            if sample["stage_progress"]["extraction"] == "complete":
                assert len(sample["bundles"]) == options["hypotheses"], (
                    name,
                    sample["id"],
                )
        if status.get("status") == "complete":
            assert set(actual) == expected
            assert hashlib.sha256(log.read_bytes()).hexdigest() == status["log_sha256"]
        labels = {sample["target"] for sample in report["samples"]}
        cohort = (
            "unsabotaged"
            if labels == {"honest"}
            else "sabotaged"
            if labels == {"sabotaged"}
            else "mixed or pending"
        )
        label = f"{options['hypotheses']} workers · {cohort}"
        start = len(samples)
        for sample in report["samples"]:
            sample["run_label"] = label
            sample["provenance"]["source_log"] = str(log.resolve())
            sample["provenance"]["task_args"] = report["task_args"]
            samples.append(sample)
        runs.append(
            dict(
                id=name,
                label=label,
                first_sample_index=start,
                expected=len(options["sample_ids"]) * config["epochs"],
                completed=sum(
                    s["stage_progress"]["extraction"] == "complete" and not s["error"]
                    for s in report["samples"]
                ),
                bundles=sum(len(s["bundles"]) for s in report["samples"]),
                billed_total=spending["billed_total"],
                spending_audit=spending,
                status=status,
                source_log=str(log.resolve()),
                log_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),
                task_args=report["task_args"],
                stats=report["stats"],
            )
        )
    superseded = [
        dict(id=name, **attempt_spend(list((base / name).rglob("*.eval"))))
        for name in config.get("superseded_conditions", [])
    ]
    terminal = len(runs) == len(config["conditions"]) and all(
        r["status"].get("status") in {"complete", "errors"} for r in runs
    )
    payload = dict(
        source_log=str(config_path),
        task="Hypothesis workers · extraction variants",
        status="complete"
        if terminal and all(r["status"].get("status") == "complete" for r in runs)
        else "error"
        if terminal
        else "in progress",
        task_args=config,
        revision={"commit": config.get("git_commit"), "dirty": True},
        stats={
            "runs": runs,
            "superseded_attempts": superseded,
            "semantic_review": "not assigned; inspect retained evidence",
        },
        runs=runs,
        samples=samples,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_report_data(payload))
    summary = {k: v for k, v in payload.items() if k != "samples"}
    summary["sample_epochs"] = len(samples)
    summary["retained_bundles"] = sum(len(s["bundles"]) for s in samples)
    summary["viewer_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    summary["generator_sha256"] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    summary["config_sha256"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    output.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.config, args.out)
    print(
        json.dumps(
            {
                "status": result["status"],
                "sample_epochs": result["sample_epochs"],
                "bundles": result["retained_bundles"],
            }
        )
    )
