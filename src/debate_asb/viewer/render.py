"""Render a portable, bundle-first HTML report."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from debate_asb.viewer.schema import ReportView

# Inlined in this order into one classic <script>; later files use earlier ones.
SCRIPTS = ("dom.js", "components.js", "report.js")


def render_report(report: ReportView) -> str:
    """Return a self-contained report with no network dependencies."""
    package = files("debate_asb.viewer") / "static"
    template = (package / "report.html").read_text()
    css = (package / "report.css").read_text()
    javascript = "\n\n".join((package / name).read_text() for name in SCRIPTS)
    data = json.dumps(report.to_dict(), ensure_ascii=False).replace("</", "<\\/")
    return (
        template.replace("/* REPORT_CSS */", css)
        .replace("/* REPORT_JS */", javascript)
        .replace("REPORT_DATA", data)
    )


def write_report(report: ReportView, output: str | Path) -> Path:
    """Write a report and return its absolute path."""
    path = Path(output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(report))
    return path
