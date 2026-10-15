"""Command-line entry point for the eval-log viewer."""

from __future__ import annotations

import argparse
from pathlib import Path

from debate_asb.viewer.adapter import load_report
from debate_asb.viewer.render import write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a bundle-first HTML report from an Inspect eval log."
    )
    parser.add_argument("log", type=Path, help="Path to an .eval log")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output HTML path (default: artifacts/viewer/<log>.html)",
    )
    args = parser.parse_args(argv)

    if not args.log.exists():
        parser.error(f"log does not exist: {args.log}")
    output = args.output or (Path("artifacts") / "viewer" / f"{args.log.stem}.html")
    path = write_report(load_report(args.log), output)
    print(path)
    return 0
