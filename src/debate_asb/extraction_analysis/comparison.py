"""Build reusable extraction-iteration reports from saved analyses and reviews.

Derived from hypothesis-workers-20261012/build_report.py and its HTML template.
Rendering is offline: it neither runs evaluations nor changes saved judgments.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from debate_asb.extraction_analysis.common import fingerprint, read_json, sha
from debate_asb.extraction_analysis.traces import source_presentations

ASSETS = Path(__file__).parents[1] / "viewer/static/extraction_comparison"
ELIGIBLE = {"yes", "partial", "no"}


def heatmap(trace: dict) -> dict:
    """Keep the original report's 20 tool-order bins and three read phases."""
    files: dict[str, list[int]] = defaultdict(lambda: [0] * 20)
    phases = [
        {
            "read_lines": 0,
            "files": set(),
            "new_lines": 0,
            "by_file": Counter(),
            "calls": Counter(),
        }
        for _ in range(3)
    ]
    seen = set()
    total = max(1, len(trace["tools"]))
    for i, tool in enumerate(trace["tools"]):
        phase = phases[min(2, i * 3 // total)]
        phase["calls"][tool["function"]] += 1
        for path, lines in source_presentations(tool):
            numbers = [number for number, _ in lines]
            files[path][min(19, i * 20 // total)] += len(numbers)
            phase["read_lines"] += len(numbers)
            phase["files"].add(path)
            phase["by_file"][path] += len(numbers)
            for n in numbers:
                if (path, n) not in seen:
                    phase["new_lines"] += 1
                    seen.add((path, n))
    for phase in phases:
        phase["read_files"] = len(phase.pop("files"))
        phase["top3_share"] = (
            sum(n for _, n in phase["by_file"].most_common(3)) / phase["read_lines"]
            if phase["read_lines"]
            else None
        )
        phase["by_file"] = dict(phase["by_file"])
        phase["calls"] = dict(phase["calls"])
    return {
        "files": [
            {"path": p, "counts": ns, "total": sum(ns)}
            for p, ns in sorted(
                files.items(), key=lambda item: (-sum(item[1]), item[0])
            )
        ],
        "phases": phases,
        "tool_calls": len(trace["tools"]),
    }


def summarize(rows: list[dict]) -> dict:
    eligible = [r for r in rows if r["included"] in ELIGIBLE and r["parsed"]]
    sufficient = [r for r in eligible if r["complete"] is not None]
    return {
        "attempts": len(rows),
        "parsed": sum(r["parsed"] for r in rows),
        "presence": sum(r["included"] == "yes" for r in eligible),
        "eligible": len(eligible),
        "excluded": len(rows) - len(eligible),
        "complete": sum(r["complete"] is True for r in sufficient),
        "sufficiency_eligible": len(sufficient),
        "partial": sum(r["included"] == "partial" for r in eligible),
        "miss": sum(r["included"] == "no" for r in eligible),
        "failed": sum(not r["parsed"] for r in rows),
        "reviewed_exposure": sum(r["exposure"] == "yes" for r in rows),
        "exposed_dropped": sum(
            r["parsed"]
            and r["exposure"] == "yes"
            and r["included"] in {"partial", "no"}
            for r in rows
        ),
        "by_sample": {
            sid: summarize_sample([r for r in rows if r["sample_id"] == sid])
            for sid in sorted({r["sample_id"] for r in rows})
        },
    }


def summarize_sample(rows: list[dict]) -> dict:
    return {
        "attempts": len(rows),
        "presence": sum(r["included"] == "yes" and r["parsed"] for r in rows),
        "complete": sum(r["complete"] is True for r in rows),
        "eligible": sum(r["included"] in ELIGIBLE and r["parsed"] for r in rows),
        "parsed": sum(r["parsed"] for r in rows),
        "epochs": sorted(r["epoch"] for r in rows),
    }


def load_variant(spec: dict, base: Path) -> tuple[dict, list[dict]]:
    def path(key):
        return (base / spec[key]).resolve()

    analysis_path, review_path = path("analysis"), path("review")
    analysis, review = read_json(analysis_path), read_json(review_path)
    if analysis["manifest"]["analysis_id"] != review.get("analysis_id"):
        raise ValueError(f"Review belongs to another analysis: {spec['id']}")
    samples = analysis["samples"]
    if analysis["manifest"].get("samples_sha256") not in (None, fingerprint(samples)):
        raise ValueError("Analysis measurements changed since collection")
    if set(review["samples"]) != {s["key"] for s in samples}:
        raise ValueError("Review must cover exactly the collected sample epochs")
    slots = set()
    rows = []
    traces = []
    for sample in samples:
        slot = (sample["id"], sample["epoch"])
        if slot in slots:
            raise ValueError(f"Duplicate sample/epoch in {spec['id']}: {slot}")
        slots.add(slot)
        r = review["samples"][sample["key"]]
        if r["included"] not in ELIGIBLE | {"unknown", "mismatch", "not_applicable"}:
            raise ValueError(f"Unreviewed sample: {slot}")
        reference = sample.get("sabotage_reference")
        if reference and r.get("sabotage_reference_sha256") != fingerprint(reference):
            raise ValueError(f"Review uses another sabotage reference: {slot}")
        trace_path = (analysis_path.parent / sample["trace_file"]).resolve()
        trace = read_json(trace_path)
        if sample.get("trace_sha256") and fingerprint(trace) != sample["trace_sha256"]:
            raise ValueError(f"Trace changed after collection: {slot}")
        bundles = sample["metrics"]["bundles"]
        parsed = sample.get("extraction_complete", bool(bundles))
        no_candidates = sample.get("no_qualifying_evidence", False)
        if no_candidates and (
            not parsed or bundles or r["included"] not in {"no", "not_applicable"}
        ):
            raise ValueError(f"Invalid zero-candidate extraction/review: {slot}")
        numbers = {b["number"] for b in bundles}
        if not set(r.get("matching_bundles", [])).issubset(numbers):
            raise ValueError(f"Invalid matching bundle: {slot}")
        if not set(r.get("sufficient_bundles", [])).issubset(
            r.get("matching_bundles", [])
        ):
            raise ValueError(f"Invalid sufficient bundle: {slot}")
        if bool(r.get("sufficient_bundles")) != (
            r.get("single_bundle_complete") is True
        ):
            raise ValueError(f"Inconsistent completeness: {slot}")
        bundle_reviews = r.get("bundle_reviews", {})
        if {str(n) for n in numbers} != set(bundle_reviews):
            raise ValueError(f"Every retained bundle needs a review: {slot}")
        for br in bundle_reviews.values():
            if (
                br.get("bug_status") not in {"yes", "no", "unclear"}
                or br.get("sabotage_candidate") not in {"yes", "no", "unclear"}
                or br.get("paper_alignment")
                not in {"consistent", "contradicts", "not_specified", "unclear"}
            ):
                raise ValueError(f"Incomplete bundle review: {slot}")
        rows.append(
            {
                "key": sample["key"],
                "variant": spec["id"],
                "sample_id": sample["id"],
                "epoch": sample["epoch"],
                "parsed": parsed,
                "honest_control": sample.get("target") == "honest",
                "review_status": "reviewed",
                "no_qualifying_evidence": no_candidates,
                "investigation": sample.get("investigation"),
                "extraction_recovery": sample.get("extraction_recovery"),
                "included": r["included"],
                "complete": r.get("single_bundle_complete"),
                "matching": r.get("matching_bundles", []),
                "sufficient": r.get("sufficient_bundles", []),
                "summary": r["summary"],
                "exposure": r.get("exposure_status", "unknown"),
                "exposure_summary": r.get("exposure_summary", "Not reviewed"),
                "error": sample.get("sample_error"),
                "bundles": bundles,
                "bundle_reviews": bundle_reviews,
                "heatmap": heatmap(trace),
                "tools": trace["tools"],
                "metrics": {
                    k: sample["metrics"].get(k)
                    for k in [
                        "repeat_read_share",
                        "top_three_read_share",
                        "categories",
                        "evidence_categories",
                        "queries",
                        "flow",
                        "reasoning",
                    ]
                },
                "model_calls": trace.get("model_calls"),
                "nominal_cost": sample.get("cost_usd"),
                "review_note": r.get("confidence_note"),
                "reference": (reference or {}).get("record"),
                "extraction_metadata": sample.get("extraction_metadata"),
            }
        )
        traces.append({"path": str(trace_path), "sha256": sha(trace_path)})
    status = read_json(path("status")) if spec.get("status") else {}
    variant = {
        "id": spec["id"],
        "label": spec["label"],
        "historical": spec.get("historical", False),
        "honest_control": bool(rows) and all(r["honest_control"] for r in rows),
        "review_status": "reviewed",
        "settings": spec.get("settings", {}),
        **summarize(rows),
        "billed_cost": status.get("billed_total"),
        "cost_scope": status.get("cost_scope"),
        "api_calls": status.get("api_calls_total"),
        "first_pass_parsed": status.get("first_pass_parsed"),
        "retry_attempts": status.get("retry_attempts"),
        "provenance": {
            "analysis": str(analysis_path),
            "analysis_sha256": sha(analysis_path),
            "review": str(review_path),
            "review_sha256": sha(review_path),
            "manifest": analysis["manifest"],
            "reviewer": {
                k: review.get(k)
                for k in [
                    "reviewer",
                    "reviewed_at",
                    "review_model",
                    "review_provider",
                    "notes",
                ]
            },
            "traces": traces,
            "status": status,
        },
    }
    if spec.get("prompt"):
        variant["prompt"] = path("prompt").read_text()
        variant["provenance"]["prompt_sha256"] = sha(path("prompt"))
    return variant, rows


def build_data(config_path: Path) -> dict:
    config = read_json(config_path)
    if config.get("schema_version") != 1 or not config.get("variants"):
        raise ValueError("Expected schema_version 1 and at least one variant")
    ids = [v["id"] for v in config["variants"]]
    if len(set(ids)) != len(ids) or any(
        not re.fullmatch(r"[a-zA-Z0-9_-]+", v) for v in ids
    ):
        raise ValueError("Variant IDs must be unique alphanumeric slugs")
    variants, samples = [], []
    for spec in config["variants"]:
        variant, rows = load_variant(spec, config_path.parent)
        variants.append(variant)
        samples.extend(rows)
    baseline = config.get("baseline")
    if baseline and baseline not in ids:
        raise ValueError("Baseline must name a configured variant")
    # Same sample/epoch labels align table slots; they are not paired random seeds.
    comparisons = []
    if baseline:
        control = {
            (r["sample_id"], r["epoch"]): r for r in samples if r["variant"] == baseline
        }
        for variant in variants:
            if variant["id"] == baseline:
                continue
            matched = [
                (control[(r["sample_id"], r["epoch"])], r)
                for r in samples
                if r["variant"] == variant["id"]
                and (r["sample_id"], r["epoch"]) in control
            ]
            eligible = [
                (a, b)
                for a, b in matched
                if a["parsed"]
                and b["parsed"]
                and a["included"] in ELIGIBLE
                and b["included"] in ELIGIBLE
            ]
            comparisons.append(
                {
                    "variant": variant["id"],
                    "baseline": baseline,
                    "common_slots": len(matched),
                    "jointly_reviewed_slots": len(eligible),
                    "baseline_presence": sum(
                        a["included"] == "yes" for a, _ in eligible
                    ),
                    "variant_presence": sum(
                        b["included"] == "yes" for _, b in eligible
                    ),
                }
            )
    return {
        "schema_version": 1,
        "title": config.get("title", "Extraction prompt comparison"),
        "description": config.get("description", ""),
        "hypothesis": config.get("hypothesis", ""),
        "date": config.get("date", ""),
        "notes": config.get("notes", []),
        "methods": config.get("methods", []),
        "variants": variants,
        "samples": samples,
        "comparisons": comparisons,
        "baseline": baseline,
        "other_costs": config.get("other_costs", []),
        "provenance": {
            "config": str(config_path.resolve()),
            "config_sha256": sha(config_path),
            "generator": str(Path(__file__).resolve()),
            "generator_sha256": sha(Path(__file__)),
            "implementation_sha256": {
                p.name: sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))
            },
            "asset_sha256": {
                n: sha(ASSETS / n) for n in ["report.html", "report.css", "report.js"]
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "derived_from": "artifacts/extraction-iteration/hypothesis-workers-20261012/build_report.py and report.template.html",
        },
    }


def markdown(data: dict) -> str:
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = [
        f"# {data['title']}",
        "",
        data["description"],
        "",
        "| Variant | Target captured / reviewed eligible | Complete / resolved | Parsed / all slots |",
        "|---|---:|---:|---:|",
    ]
    for v in data["variants"]:
        capture = (
            "Not applicable"
            if v.get("honest_control")
            else "Unreviewed"
            if v.get("review_status") == "unreviewed"
            else f"{v['presence']}/{v['eligible']}"
        )
        completeness = (
            "Not applicable"
            if v.get("honest_control")
            else "Unreviewed"
            if v.get("review_status") == "unreviewed"
            else f"{v['complete']}/{v['sufficiency_eligible']}"
        )
        lines.append(
            f"| {cell(v['label'])} | {capture} | {completeness} | {v['parsed']}/{v['attempts']} |"
        )
    lines += [
        "",
        "All-slot yields count output failures as zero; reviewed rates exclude unresolved, mismatched and honest cases. Historical comparisons are descriptive; epoch labels do not identify matched random seeds.",
        "",
    ]
    for v in data["variants"]:
        lines.extend(
            [
                f"## {v['label']}",
                "",
                "| Sample | Epoch | Capture | Matching bundles | Complete | Reason |",
                "|---|---:|---|---|---|---|",
            ]
        )
        for row in [r for r in data["samples"] if r["variant"] == v["id"]]:
            lines.append(
                "| "
                + " | ".join(
                    cell(x)
                    for x in [
                        row["sample_id"],
                        row["epoch"],
                        row["included"],
                        row["matching"],
                        row["complete"],
                        row["summary"],
                    ]
                )
                + " |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def build_report(config_path: Path, out: Path) -> dict:
    data = build_data(config_path.resolve())
    return render_data(data, out)


def render_data(data: dict, out: Path) -> dict:
    """Render saved comparison data, including explicitly unreviewed evidence."""
    out.mkdir(parents=True, exist_ok=True)
    # Escape raw-text HTML terminators, including adversarial artifact contents.
    payload = (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    template = (ASSETS / "report.html").read_text()
    html = template.replace("__REPORT_CSS__", (ASSETS / "report.css").read_text())
    html = html.replace("__REPORT_JS__", (ASSETS / "report.js").read_text()).replace(
        "__REPORT_DATA__", payload
    )
    (out / "report.html").write_text(html)
    (out / "results.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    )
    (out / "report.md").write_text(markdown(data))
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    data = build_report(args.config, args.out)
    print(
        json.dumps(
            {
                "report": str(args.out / "report.html"),
                "variants": len(data["variants"]),
                "sample_epochs": len(data["samples"]),
            }
        )
    )


if __name__ == "__main__":
    main()
