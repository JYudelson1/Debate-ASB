#!/usr/bin/env python3
"""Refresh local saved analyses and HTML viewers offline, preserving old artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path

from debate_asb.sabotage import resolve_sabotage
from debate_asb.sabotage_explorer import write_explorer
from debate_asb.viewer.render import render_report_data
from debate_asb.viewer.rubrics import load_rubric_context

REPO = Path(__file__).resolve().parents[1]
FROZEN = {
    "source_snapshot",
    "source_snapshot_recovery",
    "source",
    "reference-history",
    "cache",
    "eval",
}


def active(path: Path) -> bool:
    return not FROZEN.intersection(path.parts)


def backup_file(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    backup = path.parent / "reference-history" / digest / path.name
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(path.read_bytes())


def refresh_html(path: Path) -> bool:
    """Refresh standard viewers, or attach references to bespoke offline reports."""
    html = path.read_text()
    match = re.search(
        r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S
    )
    if match:
        payload = json.loads(match[1])
        for sample in payload["samples"]:
            old = sample.get("rubric") or {}
            logged = old.get("logged_sabotage")
            if (
                "logged_sabotage" not in old
                and old.get("sabotage_source") == "eval sample metadata"
            ):
                logged = old.get("sabotage")
            sample["rubric"] = load_rubric_context(
                sample["id"], sample["target"], logged, sample["codebase_root"]
            )
        updated = render_report_data(payload)
    else:
        if 'id="analysis-data"' in html:
            return False  # Regenerated with the bound analysis/review, not patched.
        # Bespoke replay and overview pages retain their saved judgments; attach
        # the same reusable reference in a safe text-only expandable section.
        ids = []
        replay = re.search(
            r'<script id="data" type="application/json">(.*?)</script>', html, re.S
        )
        if replay:
            ids = sorted({row["sample_id"] for row in json.loads(replay[1])})
        elif path.name == "index.html" and path.with_name("analysis.json").exists():
            ids = sorted(
                {
                    s["id"]
                    for s in json.loads(path.with_name("analysis.json").read_text())[
                        "samples"
                    ]
                }
            )
        if not ids:
            return False
        references = {sid: resolve_sabotage(sid) for sid in ids}
        data = json.dumps(references, ensure_ascii=False).replace("<", "\\u003c")
        block = (
            """<!-- SABOTAGE_REFERENCE_START -->
<section id="saved-sabotage-references"><h2>Saved sabotage references</h2>
<p>These versioned mechanisms correct rubric claims. Existing run judgments are preserved; source snapshots do not establish historical exposure.</p></section>
<script id="sabotage-reference-data" type="application/json">"""
            + data
            + """</script>
<script>
(() => {
 const refs = JSON.parse(document.getElementById("sabotage-reference-data").textContent);
 const panel = document.getElementById("saved-sabotage-references");
 for (const [id, ref] of Object.entries(refs)) {
  const detail = document.createElement("details"), title = document.createElement("summary"), text = document.createElement("pre");
  title.textContent = `${id} · ${ref.record?.status || "missing"}`;
  text.textContent = JSON.stringify(ref, null, 2); detail.append(title, text); panel.append(detail);
 }
})();
</script><!-- SABOTAGE_REFERENCE_END -->"""
        )
        html = re.sub(
            r"<!-- SABOTAGE_REFERENCE_START -->.*?<!-- SABOTAGE_REFERENCE_END -->",
            "",
            html,
            flags=re.S,
        )
        # Bespoke reports may omit body tags. Append valid section/script content.
        updated = (
            html.replace("</html>", block + "</html>")
            if "</html>" in html
            else html + block
        )
    if updated != path.read_text():
        backup_file(path)
        path.write_text(updated)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        type=Path,
        help="Artifact/log roots; defaults to artifacts and logs",
    )
    args = parser.parse_args()
    roots = args.root or [REPO / "artifacts", REPO / "logs"]
    spec = importlib.util.spec_from_file_location(
        "extraction_analysis", REPO / "skills/extraction-analysis/scripts/analyze.py"
    )
    assert spec and spec.loader
    analyzer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analyzer)
    corrections = [
        path
        for root in roots
        for path in root.rglob("audit-judgments.json")
        if active(path) and path.with_name("analysis.json").exists()
    ]
    updated, viewers, failures = 0, 0, []
    for root in roots:
        for path in sorted(root.rglob("analysis.json")):
            if not active(path) or not path.with_name("review.json").exists():
                continue
            try:
                was_rendered = path.with_name("report.html").exists()
                analyzer.refresh_reference(path, path.with_name("review.json"))
                analyzer.reuse_saved_corrections(
                    path, path.with_name("review.json"), corrections
                )
                if was_rendered:
                    analyzer.render(path, path.with_name("review.json"))
                updated += 1
            except ValueError as exc:
                failures.append(f"{path}: {exc}")
        for path in sorted(root.rglob("*.html")):
            if active(path) and refresh_html(path):
                viewers += 1
    analyses = [
        path
        for root in roots
        for path in sorted(root.rglob("analysis.json"))
        if active(path) and path.with_name("report.html").exists()
    ]
    explorer_path = REPO / "artifacts/sabotage-explorer/index.html"
    if explorer_path.exists():
        backup_file(explorer_path)
    write_explorer(explorer_path, analyses)
    print(
        f"Updated {updated} saved analyses and {viewers} viewers/overview pages; explorer: {explorer_path}"
    )
    for failure in failures:
        print(f"Needs attention: {failure}")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
