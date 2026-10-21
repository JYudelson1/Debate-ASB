"""Offline cross-run extraction analysis, separate from per-run reports.

Inputs are immutable collected traces, existing semantic reviews, and an explicit
source-bound target audit. This module never calls a model or edits input ledgers.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import sys
import tarfile
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from statistics import median
from typing import Any

from debate_asb.extraction_analysis.common import fingerprint, read_json, sha
from debate_asb.extraction_analysis.comparison import load_variant
from debate_asb.extraction_analysis.coverage import build_inventory
from debate_asb.extraction_analysis.metrics import (
    aggregate,
    association,
    capture_summary,
    catalogue_lines,
    measure,
)

ASSETS = Path(__file__).parents[1] / "viewer/static/extraction_cross_run"


def save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def resolve(base: Path, value: str) -> Path:
    return (base / value).resolve()


def owner_map(logged: Any, tools: list[dict]) -> dict[str, str]:
    spans = {
        e.id: (e.parent_id, e.name) for e in logged.events if e.event == "span_begin"
    }
    result = {}
    for tool in tools:
        index = tool.get("event_index")
        if index is None:
            continue
        if not 0 <= index < len(logged.events):
            raise ValueError("Worker-owner event index is out of range")
        event = logged.events[index]
        if event.event != "tool" or event.function != tool["function"]:
            raise ValueError("Worker-owner event does not match retained trace")
        span = logged.events[index].span_id
        seen = set()
        owner = "coordinator"
        while span and span not in seen:
            seen.add(span)
            parent, name = spans.get(span, (None, ""))
            if name.startswith(
                ("hypothesis_worker/", "hypothesis_child/", "hypothesis_worker_")
            ):
                owner = name
                break
            span = parent
        result[str(tool["ordinal"])] = owner
    return result


def build_data(config_path: Path) -> dict:
    config = read_json(config_path)
    base = config_path.parent
    if config.get("schema_version") != 1 or not config.get("variants"):
        raise ValueError("Expected schema_version 1 and nonempty variants")
    ids = [v["id"] for v in config["variants"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate variants")
    audit_path = resolve(base, config["target_review"])
    audit = read_json(audit_path)
    units_path = resolve(base, config["target_units"])
    units = read_json(units_path)
    inventory_path = resolve(base, config["inventory"])
    inventories = read_json(inventory_path)
    variants, rows, provenance = [], [], []
    log_cache = {}
    identity = set()
    seen_audit = set()
    for spec in config["variants"]:
        _, loaded = load_variant(spec, base)
        analysis_path = resolve(base, spec["analysis"])
        analysis = read_json(analysis_path)
        review_path = resolve(base, spec["review"])
        reviews = read_json(review_path)
        source_path = resolve(base, spec["results"])
        source = read_json(source_path)
        extras = {
            s["key"]: s
            for s in source["samples"]
            if s["variant"] == spec["source_variant"]
        }
        samples = {s["key"]: s for s in analysis["samples"]}
        if set(extras) != set(samples):
            raise ValueError("Source report does not cover the selected analysis")
        known = {}
        for row in loaded:
            sample = samples[row["key"]]
            ident = (sample["log_sha256"], row["sample_id"], row["epoch"])
            if ident in identity:
                raise ValueError("Duplicate underlying log/sample/epoch")
            identity.add(ident)
            rid = spec["id"] + ":" + row["key"]
            checked = audit["samples"][rid]
            seen_audit.add(rid)
            if checked["bundles_sha256"] != fingerprint(row["bundles"]) or checked[
                "reference_sha256"
            ] != fingerprint(sample["sabotage_reference"]):
                raise ValueError("Target audit is stale: " + rid)
            if checked["original_review_sha256"] != fingerprint(
                reviews["samples"][row["key"]]
            ):
                raise ValueError("Original review changed: " + rid)
            numbers = {b["number"] for b in row["bundles"]}
            if not set(checked["matching_bundles"]) <= numbers:
                raise ValueError("Audit cites missing bundle")
            if checked["included"] not in {
                "yes",
                "partial",
                "no",
                "unknown",
                "mismatch",
                "not_applicable",
            }:
                raise ValueError("Unknown audited capture status")
            if not set(checked.get("related_bundles", [])) <= numbers:
                raise ValueError("Audit cites missing related bundle")
            if (checked["included"] == "yes") != bool(checked["matching_bundles"]):
                # Partial matches are separately retained in related_bundles.
                raise ValueError("Audit capture/matches disagree")
            row["row_id"] = rid
            row["original_included"] = row["included"]
            row["original_matching"] = row["matching"]
            row["included"] = checked["included"]
            row["matching"] = checked["matching_bundles"]
            row["captured"] = checked["included"] == "yes"
            row["audit"] = checked
            row["eligible"] = row["parsed"] and row["included"] in {
                "yes",
                "partial",
                "no",
            }
            row["trace_complete"] = (
                sample["trace_status"] == "observed"
                and row["parsed"]
                and not sample.get("sample_error")
            )
            row["failure_stage_original"] = reviews["samples"][row["key"]].get(
                "failure_stage"
            )
            row["failure_modes"] = reviews["samples"][row["key"]].get(
                "failure_modes", []
            )
            row["log"] = sample["run"]
            row["diagnostics"] = sample.get("diagnostics", [])
            row["hypotheses"] = extras.get(row["key"], {}).get("hypotheses")
            inventory = inventories["samples"][row["sample_id"]]
            for region in units["samples"][row["sample_id"]]["regions"]:
                file = inventory["files"].get(region["path"])
                if (
                    not file
                    or file.get("lines") is None
                    or not 1 <= region["start"] <= region["end"] <= file["lines"]
                ):
                    raise ValueError("Invalid reviewed implementation region")
                if region.get("reference_sha256") and region[
                    "reference_sha256"
                ] != file.get("sha256"):
                    raise ValueError(
                        "Implementation region belongs to another source snapshot"
                    )
            if row["sample_id"] not in known:
                known[row["sample_id"]] = catalogue_lines(sample, inventory)
            extra_tools = extras.get(row["key"], {}).get("tools", [])
            if extra_tools and "worker" in extra_tools[0]:
                signature = lambda tools: [
                    (t["ordinal"], t["function"], t.get("arguments"), t.get("result"))
                    for t in tools
                ]
                if signature(extra_tools) != signature(row["tools"]):
                    raise ValueError(
                        "Worker attribution belongs to different tool results"
                    )
                owners = {
                    str(t["ordinal"]): str(t.get("worker") or "coordinator")
                    for t in extra_tools
                }
            elif spec.get("worker_log_owners"):
                from inspect_ai.log import read_eval_log

                run = next(
                    r
                    for r in analysis["manifest"]["runs"]
                    if Path(r["path"]).name == sample["run"]
                )
                path = run["path"]
                if path not in log_cache:
                    log = read_eval_log(path)
                    log_cache[path] = {
                        (str(s.id), s.epoch): s for s in log.samples or []
                    }
                owners = owner_map(
                    log_cache[path][(row["sample_id"], row["epoch"])], row["tools"]
                )
            else:
                owners = {str(t["ordinal"]): "single" for t in row["tools"]}
            row.update(
                measure(
                    sample,
                    row["tools"],
                    inventory,
                    units["samples"][row["sample_id"]]["regions"],
                    known[row["sample_id"]],
                    owners,
                )
            )
            row["stage"] = (
                "captured"
                if row["captured"]
                else "trace incomplete"
                if not row["trace_complete"]
                else "no target-region read"
                if not row["target_read_events"]
                else "read target region; not retained"
            )
            catalog = (sample.get("rubric") or {}).get("known_issue_catalog") or {}
            row["known_catalog"] = catalog.get("entries", [])
            for bundle in row["bundles"]:
                br = row["bundle_reviews"][str(bundle["number"])]
                bundle["review"] = br
                bundle["target"] = bundle["number"] in row["matching"]
                strengths = {
                    m.get("match_kind") for m in bundle.get("known_issue_matches", [])
                }
                bundle["attention_group"] = (
                    "target"
                    if bundle["target"]
                    else "known"
                    if "code_match" in strengths
                    else "known_possible"
                    if strengths
                    else "paper"
                    if bundle["excerpts"]
                    and all(
                        Path(e["path"]).name.lower() == "paper.md"
                        for e in bundle["excerpts"]
                    )
                    else "other"
                )
                bundle["related"] = bundle["number"] in checked.get(
                    "related_bundles", []
                )
            rows.append(row)
        group = [r for r in rows if r["variant"] == spec["id"]]
        source_variant = next(
            v
            for v in source.get("variants", source.get("conditions", []))
            if v["id"] == spec["source_variant"]
        )
        coverages = [r["coverage"] for r in group if r["coverage"] is not None]
        variants.append(
            {
                "id": spec["id"],
                "label": spec["label"],
                "short_label": spec.get("short_label", spec["label"]),
                "settings": spec.get("settings", {}),
                "billed_cost": source_variant.get("billed_cost"),
                "cost_scope": source_variant.get(
                    "cost_scope",
                    "Source report extraction spend; retry accounting differs by study.",
                ),
                **capture_summary(group),
                "median_coverage": median(coverages) if coverages else None,
                "median_target_events": median(r["target_read_events"] for r in group),
                "read_presentations": sum(r["read_presentations"] for r in group),
            }
        )
        provenance.append(
            {
                "variant": spec["id"],
                "analysis": str(analysis_path),
                "analysis_sha256": sha(analysis_path),
                "review": str(review_path),
                "review_sha256": sha(review_path),
                "results": str(source_path),
                "results_sha256": sha(source_path),
                "manifest": analysis["manifest"],
                "settings": spec.get("settings", {}),
            }
        )
    if seen_audit != set(audit["samples"]):
        raise ValueError("Audit must cover exactly selected samples")
    return {
        "schema_version": 1,
        "title": config["title"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notes": config.get("notes", []),
        "example_rows": config.get("example_rows", []),
        "variants": variants,
        "samples": rows,
        "summary": aggregate(rows, variants),
        "audit_policy": audit["policy"],
        "target_units": units,
        "provenance": {
            "config": str(config_path.resolve()),
            "config_sha256": sha(config_path),
            "target_review_sha256": sha(audit_path),
            "target_units_sha256": sha(units_path),
            "inventory_sha256": sha(inventory_path),
            "generator_sha256": sha(Path(__file__)),
            "sources": provenance,
        },
        "inventory_summary": {
            sid: {
                "source_config_lines": sum(
                    f["lines"] or 0
                    for f in inv["files"].values()
                    if f["role"] in {"source", "configuration"}
                ),
                "files": len(inv["files"]),
            }
            for sid, inv in inventories["samples"].items()
        },
    }


def build_report(config: Path, out: Path) -> dict:
    data = build_data(config)
    out.mkdir(parents=True, exist_ok=True)
    assets = {
        name: (ASSETS / name).read_text()
        for name in ("report.html", "report.css", "report.js")
    }
    data["provenance"]["assets"] = {name: sha(ASSETS / name) for name in assets}
    root = Path(__file__).resolve().parents[3]
    snapshot_path = out / "generator-source.tar.gz"
    snapshot_files = [
        p
        for p in (root / "src/debate_asb").rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ] + [
        root / name
        for name in [
            "pyproject.toml",
            "uv.lock",
            "scripts/build_extraction_cross_run.py",
            "scripts/check_extraction_cross_run.cjs",
            "docs/extraction-cross-run.md",
        ]
        if (root / name).is_file()
    ]
    with tarfile.open(snapshot_path, "w:gz") as archive:
        for file in sorted(snapshot_files):
            archive.add(file, arcname=str(file.relative_to(root)))
    data["provenance"]["generator_source_archive"] = {
        "path": str(snapshot_path.resolve()),
        "sha256": sha(snapshot_path),
    }
    data["provenance"]["runtime"] = {
        "python": sys.version,
        "inspect_ai": version("inspect-ai"),
    }
    save(out / "results.json", data)
    save(out / "provenance.json", data["provenance"])
    template = assets["report.html"].replace("__TITLE__", html.escape(data["title"]))
    template = template.replace("__CSS__", assets["report.css"]).replace(
        "__JS__", assets["report.js"]
    )
    template = template.replace(
        "__DATA__",
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029"),
    )
    (out / "report.html").write_text(template)
    fields = [
        "row_id",
        "variant",
        "sample_id",
        "epoch",
        "parsed",
        "eligible",
        "captured",
        "original_included",
        "coverage",
        "coverage_search",
        "source_coverage",
        "file_coverage",
        "code_lines_read",
        "code_lines_total",
        "target_read_events",
        "target_repetitions_per_line",
        "same_agent_target_repeats",
        "target_region_fraction",
        "target_read_share",
        "known_read_share",
        "repeat_share",
        "stage",
        "log",
    ]
    with (out / "sample-metrics.csv").open("w") as handle:
        writer = csv.DictWriter(handle, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data["samples"])
    md = [
        "# " + data["title"],
        "",
        f"Offline analysis; no model calls. {len(data['variants'])} selected variants, {len({r['sample_id'] for r in data['samples']})} codebases, {len(data['samples'])} retained sample epochs.",
        "",
    ]
    for variant in data["variants"]:
        md.append(
            f"- {variant['label']}: {variant['caught']}/{variant['eligible']} mechanism captures (original {variant['original_caught']}/{variant['eligible']}); {variant['bundles']} bundles."
        )
    associations = data["summary"]["associations"]
    coverage = associations["coverage"]
    rereads = associations["target_read_events"]
    within_agent = associations["same_agent_target_repeats"]
    misses = [r for r in data["samples"] if r["eligible"] and not r["captured"]]
    attention = data["summary"]["attention_bundles"]
    total_bundles = sum(attention.values())
    fmt_r = lambda value: "unresolved" if value is None else f"{value:.2f}"
    md += [
        "",
        "## Requested questions",
        "",
        f"- **Code coverage:** pooled r={fmt_r(coverage['pooled']['r'])}; within codebase r={fmt_r(coverage['within_codebase']['r'])}; within codebase × variant r={fmt_r(coverage['within_cell']['r'])}.",
        f"- **Repeated target reads:** within codebase × variant, target-region read-call r={fmt_r(rereads['within_cell']['r'])}; repeated target lines within the same agent r={fmt_r(within_agent['within_cell']['r'])}. These logs do not establish that requiring rereads would improve capture.",
        f"- **Selection failures:** {sum(r['target_read_events'] > 0 for r in misses)}/{len(misses)} remaining misses had at least one target-region read. Location overlap does not establish that the complete mechanism was understood.",
        "- **Persistent hotspots:** "
        + "; ".join(
            f"{h['sample_id']} {h['caught']}/{h['eligible']} epochs; any success in {h['runs_any']}/{h['runs']} runs, every epoch caught in {h['runs_all']}/{h['runs']} runs"
            for h in data["summary"]["hotspots"][:4]
        )
        + ".",
        "- **Retained bundle content:** "
        + "; ".join(
            f"{name} {attention.get(key, 0)}/{total_bundles} ({100 * attention.get(key, 0) / max(1, total_bundles):.1f}%)"
            for key, name in [
                ("target", "target mechanism"),
                ("known", "verified non-target catalog signatures"),
                ("known_possible", "possible catalog matches"),
                ("other", "other/unclassified"),
            ]
        )
        + ". Signature containment is not proof that the issue was the bundle's focus; uncatalogued material is not automatically a new bug or wasted work.",
    ]
    md += [
        "",
        "Correlations are descriptive; browsing was not randomized. See the interactive HTML for within-codebase/variant comparisons, source-bound scoring corrections, and all bundles inline.",
        "",
        "Coverage denominator: saved local reference inventory, not a complete historical snapshot. Source mismatches and trace incompleteness are explicit. Region reads measure presented code, not understanding.",
    ]
    (out / "report.md").write_text("\n".join(md) + "\n")
    return data


def snapshot_inventory(config_path: Path) -> Path:
    """Freeze denominators and reference code without any experiment/model calls."""
    config = read_json(config_path)
    path = resolve(config_path.parent, config["inventory"])
    if path.exists():
        raise ValueError("Inventory exists; select a new output path to preserve it")
    inventories = {}
    for spec in config["variants"]:
        analysis = read_json(resolve(config_path.parent, spec["analysis"]))
        for sample in analysis["samples"]:
            sid = sample["id"]
            root = Path(sample["codebase_root"])
            if sid in inventories:
                if str(root) != inventories[sid]["root"]:
                    raise ValueError("Same sample ID maps to different codebases")
                continue
            inventory = build_inventory(root)
            for name, record in inventory["files"].items():
                if record["readable"] and record["role"] in {
                    "source",
                    "configuration",
                    "paper_docs",
                }:
                    record["snapshot_text"] = (root / name).read_text(errors="replace")
            inventories[sid] = inventory
    save(
        path,
        {
            "schema_version": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "policy": "Frozen local reference inventory and source/configuration/document text. Normalizes historical returned lines; does not certify the contents of unread historical files.",
            "samples": inventories,
        },
    )
    return path


def write_canvas(data: dict, path: Path, report_path: Path) -> None:
    """A compact inline-data companion; all raw evidence stays in the HTML."""
    metrics = [
        ("coverage", "Source + config coverage"),
        ("target_read_events", "Target-region read calls"),
        ("same_agent_target_repeats", "Repeated target lines within one agent"),
    ]

    def summary(original: bool) -> dict:
        rows = [
            {
                **r,
                "captured": r["original_included"] == "yes"
                if original
                else r["captured"],
            }
            for r in data["samples"]
        ]
        totals = aggregate(rows, data["variants"])
        allocation = Counter()
        for row in rows:
            for bundle in row["bundles"]:
                target = (
                    row["original_included"] == "yes"
                    and bundle["number"] in row["original_matching"]
                    if original
                    else bundle["target"]
                )
                kinds = {m["match_kind"] for m in bundle.get("known_issue_matches", [])}
                group = (
                    "Target mechanism"
                    if target
                    else "Known, verified signature"
                    if "code_match" in kinds
                    else "Possible catalog match"
                    if kinds
                    else "Other / unclassified"
                )
                allocation[group] += 1
        return {
            "caught": sum(r["captured"] for r in rows if r["eligible"]),
            "read_misses": sum(
                r["eligible"] and not r["captured"] and r["target_read_events"] > 0
                for r in rows
            ),
            "correlations": [
                {
                    "label": name,
                    "pooled": association(rows, metric)["r"],
                    "within": association(rows, metric, ("sample_id",))["r"],
                    "cell": association(rows, metric, ("sample_id", "variant"))["r"],
                }
                for metric, name in metrics
            ],
            "hotspots": [
                {
                    "sample_id": h["sample_id"],
                    "caught": h["caught"],
                    "eligible": h["eligible"],
                    "runs": h["runs"],
                    "any": h["runs_any"],
                    "all": h["runs_all"],
                }
                for h in totals["hotspots"]
            ],
            "allocation": [
                {"label": key, "count": value} for key, value in allocation.items()
            ],
        }

    examples = []
    selected_ids = data.get("example_rows", [])
    chosen_rows = (
        [r for r in data["samples"] if r["row_id"] in selected_ids]
        if selected_ids
        else data["samples"][:8]
    )
    for row in data["samples"]:
        if row not in chosen_rows:
            continue
        examples.append(
            {
                "row_id": row["row_id"],
                "sample_id": row["sample_id"],
                "epoch": row["epoch"],
                "label": next(
                    v["label"] for v in data["variants"] if v["id"] == row["variant"]
                ),
                "original": row["original_included"],
                "revised": row["included"],
                "bundles": [
                    {
                        "number": b["number"],
                        "topic": b["review"]["topic"],
                        "observation": b["observation"],
                        "catalog": "; ".join(
                            m["title"] + " (" + m["match_kind"] + ")"
                            for m in b.get("known_issue_matches", [])
                        ),
                        "excerpts": b["excerpts"],
                    }
                    for b in row["bundles"]
                ],
            }
        )
    compact = {
        "title": data["title"],
        "attempts": len(data["samples"]),
        "eligible": data["summary"]["overall"]["eligible"],
        "bundle_count": sum(len(r["bundles"]) for r in data["samples"]),
        "codebase_count": len({r["sample_id"] for r in data["samples"]}),
        "changed": sum(
            r["original_included"] != r["included"] for r in data["samples"]
        ),
        "report": str(report_path.resolve()),
        "revised": summary(False),
        "original": summary(True),
        "variants": [
            {
                "short": v.get("short_label", v["label"]),
                "attempts": v["attempts"],
                "eligible": v["eligible"],
                "revised": v["caught"],
                "original": v["original_caught"],
            }
            for v in data["variants"]
        ],
        "codebases": list(dict.fromkeys(e["sample_id"] for e in examples)),
        "examples": examples,
    }
    template = (ASSETS / "summary.tsx").read_text()
    path.write_text(
        template.replace(
            "__CANVAS_DATA__",
            json.dumps(compact, ensure_ascii=False).replace("<", "\\u003c"),
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--snapshot-inventory", action="store_true")
    parser.add_argument("--canvas-out", type=Path)
    args = parser.parse_args()
    if args.snapshot_inventory:
        print(snapshot_inventory(args.config))
        return
    if args.out is None:
        parser.error("--out is required for report generation")
    data = build_report(args.config, args.out)
    if args.canvas_out:
        write_canvas(data, args.canvas_out, args.out / "report.html")
    print(
        json.dumps(
            {
                "report": str((args.out / "report.html").resolve()),
                **data["summary"]["overall"],
            }
        )
    )


if __name__ == "__main__":
    main()
