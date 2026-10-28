"""Present saved run-viewer evidence in the existing comparison viewer offline.

This adapter does not assign capture, bug, candidate, or paper judgments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from debate_asb.extraction_analysis.comparison import (
    ASSETS,
    heatmap,
    render_data,
    summarize,
)


def build(source: Path, out: Path) -> dict:
    content = source.read_text()
    match = re.search(
        r'<script id="report-data" type="application/json">(.*?)</script>',
        content,
        re.S,
    )
    if not match:
        raise ValueError("Expected a saved run viewer with embedded data")
    original = json.loads(match[1])
    rows = []
    variants = []
    offset = 0
    for run in original["runs"]:
        count = run["expected"]
        members = original["samples"][offset : offset + count]
        assert len(members) == count
        offset += count
        selected = []
        for sample in members:
            assert sample["run_label"] == run["label"]
            honest = sample["target"] == "honest"
            parsed = (
                sample["stage_progress"]["extraction"] == "complete"
                and not sample["error"]
            )
            tools = [
                dict(
                    ordinal=i + 1,
                    function=e["function"],
                    arguments=e["arguments"],
                    result=e["content"] or "",
                    failed=None,
                    owner=e["role"],
                )
                for i, e in enumerate(
                    t for t in sample["extraction_trace"] if t["kind"] == "tool"
                )
            ]
            bundles = [
                dict(
                    number=b["number"],
                    observation=b["observation"],
                    excerpts=b["excerpts"],
                )
                for b in sample["bundles"]
            ]
            details = sample.get("extraction_details", {})
            reviews = {
                str(b["number"]): dict(
                    topic="Unreviewed",
                    bug_status="unreviewed",
                    sabotage_candidate="unreviewed",
                    paper_alignment="unreviewed",
                    reason="No semantic review assigned.",
                    basis=[],
                )
                for b in bundles
            }
            row = dict(
                key=f"{run['id']}:{sample['id']}:{sample['epoch']}",
                variant=run["id"],
                sample_id=sample["id"],
                epoch=sample["epoch"],
                parsed=bool(parsed),
                review_status="unreviewed",
                honest_control=honest,
                included="not_applicable" if honest else "unknown",
                complete=None,
                matching=[],
                sufficient=[],
                no_qualifying_evidence=False,
                summary="Unsabotaged control; target capture is not applicable. Evidence is unreviewed."
                if honest
                else "Target capture and bundle judgments have not been reviewed.",
                exposure="not_applicable" if honest else "unreviewed",
                exposure_summary="No exposure judgment assigned.",
                error=sample["error"],
                bundles=bundles,
                bundle_reviews=reviews,
                investigation=None,
                coordinator_details=details,
                heatmap=heatmap(dict(tools=tools)),
                tools=tools,
                metrics=dict(inspect_events=sample["provenance"]["inspect_events"]),
                model_calls=sample["provenance"]["inspect_events"].get("model"),
                nominal_cost=sample["provenance"].get("cost", {}).get("extractor"),
                reference=(sample.get("rubric") or {})
                .get("sabotage_reference", {})
                .get("record"),
                review_note="Unreviewed; no semantic labels inferred from paths, counts or inspection.",
                extraction_metadata=sample["provenance"],
                source_log=run["source_log"],
            )
            selected.append(row)
        rows.extend(selected)
        variants.append(
            dict(
                id=run["id"],
                label=run["label"],
                historical=False,
                review_status="unreviewed",
                honest_control=all(s["honest_control"] for s in selected),
                settings=dict(
                    model=original["task_args"]["model"],
                    provider=original["task_args"]["provider"],
                    epochs=original["task_args"]["epochs"],
                    **original["task_args"]["conditions"][run["id"]],
                ),
                **summarize(selected),
                billed_cost=run["billed_total"],
                api_calls=run["spending_audit"]["api_calls"],
                cost_scope="Recorded billed extraction charges, including format retries.",
                first_pass_parsed=run["status"]["first_pass_parsed"],
                retry_attempts=run["status"]["retry_attempts"],
                provenance=run,
            )
        )
    assert offset == len(original["samples"])
    data = dict(
        schema_version=1,
        title="Hypothesis-worker extraction variants · GPT-6 Luna",
        description="Ten workers on nine unsabotaged codebases; fifteen and twenty workers on four sabotaged codebases. Three epochs per codebase. Existing extraction outputs, without new model calls.",
        hypothesis="Increased worker counts retain the ten-worker per-worker allowance; coordinator allocation stays fixed.",
        date="21 October 2026",
        variants=variants,
        samples=rows,
        comparisons=[],
        baseline=None,
        notes=[
            "Semantic capture, completeness, bug, candidate and paper judgments are unreviewed. Unknown is not a miss.",
            "Honest controls have no target sabotage; no false-positive verdict is inferred from retained evidence.",
            "Two protocol errors remain after the original limit of twelve format-retry rounds: diffuse_owl epoch 2 with 15 workers; chain_justification epoch 1 with 20 workers.",
        ],
        methods=[
            "Uses the comparison viewer from the bounded-validity report, with explicit unreviewed/control states.",
            "Extraction settings, sample identities, source log hashes, retry history, models and costs are preserved in provenance.",
            "Six coordinator turns/$0.30. Ten workers: 60 turns/$3 total; fifteen: 87/$4.35; twenty: 114/$5.70. Each worker retains $0.27 nominal and an average of 5.4 turns.",
            "Inspection heatmaps count returned source lines, retaining repeats; they do not establish target exposure or capture.",
        ],
        other_costs=[
            dict(
                label="Superseded fixed-total attempts",
                billed_cost=sum(
                    r["billed_total"] for r in original["stats"]["superseded_attempts"]
                ),
                api_calls=None,
                scope="Separate from selected variants; interrupted calls may be absent from the recorded cost.",
            )
        ],
        provenance=dict(
            source_viewer=str(source.resolve()),
            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            adapter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            asset_sha256={
                name: hashlib.sha256((ASSETS / name).read_bytes()).hexdigest()
                for name in ["report.html", "report.css", "report.js"]
            },
            run_config=original["task_args"],
            review_status="unreviewed",
            source_run_stats=original["stats"],
        ),
    )
    return render_data(data, out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    data = build(args.source, args.out)
    print(
        json.dumps(
            dict(
                variants=len(data["variants"]),
                slots=len(data["samples"]),
                bundles=sum(len(s["bundles"]) for s in data["samples"]),
            )
        )
    )
