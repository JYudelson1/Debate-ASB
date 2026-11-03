"""Run with ``python -m debate_asb.end_to_end --help``."""

import argparse
import json
import time
from pathlib import Path

from dotenv import load_dotenv

from .study import import_extractions, prepare, read_json, status, write_json


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Reproducible extraction-to-debate studies"
    )
    parser.add_argument(
        "action",
        choices=[
            "prepare",
            "configure",
            "import",
            "extract",
            "debate",
            "review",
            "report",
            "export",
            "cost",
            "package",
            "validate",
            "validate-scores",
            "status",
        ],
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-log", action="append", type=Path, default=[])
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--judge-model")
    parser.add_argument("--judge-provider")
    parser.add_argument("--include-missing-sabotaged", action="store_true")
    parser.add_argument("--coordinator-format", choices=["array", "named_slots"])
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--max-connections", type=int)
    parser.add_argument("--exclude-jobs-from", type=Path)
    parser.add_argument("--jobs-from", type=Path)
    parser.add_argument("--watch-extractions", action="store_true")
    parser.add_argument("--watch-debates", action="store_true")
    args = parser.parse_args()
    directory = args.out.resolve()
    if args.action == "prepare":
        prepare(directory, args.source_log)
    elif args.action == "import":
        import_extractions(directory, args.source_log)
    elif args.action == "extract":
        from .run import run_extractions

        run_extractions(
            directory,
            args.max_attempts,
            max_samples=args.max_samples or 8,
            max_connections=args.max_connections or 32,
        )
    elif args.action == "configure":
        manifest = read_json(directory / "study.json")
        if (args.judge_model is None) != (args.judge_provider is None):
            parser.error("Supply both --judge-model and --judge-provider")
        if args.judge_model:
            if any((directory / "debates").glob("*.json")):
                parser.error("Cannot change the judge after debates have started")
            manifest["debate"].update(
                judge_model=args.judge_model, judge_provider=args.judge_provider
            )
        manifest["debate"]["repetitions"] = 3
        manifest["aggregation"] = (
            "max_over_all_extracted_bundles_of_mean_of_three_debate_credences"
        )
        if args.include_missing_sabotaged:
            manifest["allowed_new_extraction_ids"] = [
                s["id"] for s in manifest["samples"]
            ]
        if args.coordinator_format:
            manifest["extraction"]["coordinator_response_format"] = (
                args.coordinator_format
            )
        write_json(directory / "study.json", manifest)
    elif args.action == "debate":
        from .debate import run_debates

        while True:
            run_debates(
                directory,
                args.max_attempts,
                max_samples=args.max_samples or 64,
                max_connections=args.max_connections or 64,
                exclude_jobs_from=args.exclude_jobs_from,
                jobs_from=args.jobs_from,
            )
            if not args.watch_extractions or not status(directory)["missing"]:
                break
            time.sleep(15)
    elif args.action == "report":
        from .report import render

        print(render(directory))
    elif args.action == "review":
        from .report import render
        from .review import run_reviews

        while True:
            run_reviews(directory, args.max_attempts)
            render(directory)
            if not args.watch_debates or (
                status(directory)["debate_jobs_complete"] == 3600
                and len(list((directory / "reviews").glob("*.json"))) == 1200
            ):
                break
            time.sleep(15)
    elif args.action == "export":
        from .export import extraction_export

        print(extraction_export(directory))
    elif args.action == "package":
        from .export import package

        print(package(directory))
    elif args.action == "cost":
        from .export import recorded_spend

        audit = recorded_spend(directory)
        print(json.dumps(audit["categories"], indent=2))
    elif args.action in {"validate", "validate-scores"}:
        from .validate import validate

        print(
            json.dumps(
                validate(directory, require_reviews=args.action == "validate"), indent=2
            )
        )
    print(json.dumps(status(directory), indent=2))


if __name__ == "__main__":
    main()
