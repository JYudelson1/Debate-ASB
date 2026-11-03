"""Persistent study manifests and validated extraction handoffs.

Labels/references are kept in the offline manifest; participants receive only
the original audit task, their codebase tools, and the selected bundle.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from inspect_ai.log import read_eval_log, resolve_sample_attachments

from debate_asb.artifacts.codebase import Codebase
from debate_asb.datasets.asb import asb_samples
from debate_asb.evidence import content_sha256, extraction_result_from_dict
from debate_asb.models import ModelSpec, Participant, Side
from debate_asb.protocols.evidence_closure import EvidenceClosure

MODEL = "openai/gpt-6-luna"
PROVIDER = "openai"
ROOT = Path(__file__).resolve().parents[3]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def write_json(path: Path, value: object) -> None:
    write_text(
        path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(text)
    Path(temporary.name).replace(path)


def file_sha(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def freeze_sources(directory: Path, stage: str) -> Path:
    """Archive the code actually launched, including uncommitted implementation."""
    output = directory / "source-snapshots" / stage
    source_files = [
        p
        for p in (ROOT / "src").rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ]
    source_files.extend(ROOT / name for name in ["pyproject.toml", "uv.lock"])
    hashes = {str(p.relative_to(ROOT)): file_sha(p) for p in source_files}
    if output.exists():
        if read_json(output / "manifest.json")["sha256"] != hashes:
            raise ValueError(f"Archived execution sources changed: {stage}")
        return output
    for path in source_files:
        target = output / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    write_json(
        output / "manifest.json",
        {
            "sha256": hashes,
            "git_revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "git_status": subprocess.check_output(
                ["git", "status", "--short"], cwd=ROOT, text=True
            ),
        },
    )
    return output


def closure_protocol(**kwargs) -> EvidenceClosure:
    spec = ModelSpec(MODEL, PROVIDER)
    return EvidenceClosure(
        extractor=Participant(spec),
        sabotaged=Participant(spec, side=Side.SABOTAGED),
        clean=Participant(spec, side=Side.CLEAN),
        judge=Participant(spec),
        **kwargs,
    )


def extraction_path(directory: Path, sample_id: str, epoch: int) -> Path:
    return directory / "extractions" / f"{sample_id}-e{epoch}.json"


def import_extractions(directory: Path, log_paths: list[Path]) -> int:
    """Import exact matching stages; reject conflicts and other conditions."""
    imported = 0
    for path in log_paths:
        log_sha = file_sha(path)
        for unresolved in read_eval_log(str(path)).samples or []:
            sample = resolve_sample_attachments(unresolved)
            raw = sample.store.get("stages", {}).get("extraction")
            if sample.error or not raw:
                continue
            extraction = extraction_result_from_dict(raw)
            config = extraction.metadata.config
            if not (
                len(extraction.bundles) == 20
                and config.get("evidence_closure") is True
                and config.get("plan_steps") == 6
                and config.get("max_steps") == 114
                and config.get("worker_steps") == [6] * 8 + [5] * 12
                and not config.get("coordinator_execution")
                and not config.get("worker_execution")
                and extraction.metadata.models["extractor"].model == MODEL
                and extraction.metadata.models["extractor"].provider == PROVIDER
            ):
                raise ValueError(
                    f"Not the requested twenty-worker closure condition: {path}, {sample.id}"
                )
            target = extraction_path(directory, str(sample.id), sample.epoch)
            record = {
                "sample_id": str(sample.id),
                "extraction_epoch": sample.epoch,
                "extraction": raw,
                "extraction_sha256": content_sha256(json.dumps(raw, sort_keys=True)),
                "source_log": str(path.resolve()),
                "source_log_sha256": log_sha,
                "imported": True,
            }
            if target.exists():
                if (
                    read_json(target)["extraction_sha256"]
                    != record["extraction_sha256"]
                ):
                    raise ValueError(f"Conflicting extraction: {target}")
                continue
            write_json(target, record)
            imported += 1
    return imported


def prepare(directory: Path, logs: list[Path]) -> dict:
    path = directory / "study.json"
    if path.exists():
        raise ValueError(
            f"Study already exists: {path}; use import to add saved extractions"
        )
    samples = []
    for sample in asb_samples():
        metadata = sample.metadata or {}
        root = Path(metadata["artifacts"]["codebase"]["root"])
        artifact = Codebase(root)
        samples.append(
            {
                "id": sample.id,
                "label": sample.target,
                "root": str(root),
                "visible_files_sha256": {
                    str(p.relative_to(root)): file_sha(p) for p in artifact._files(root)
                },
            }
        )
    protocol = closure_protocol(stop_after="extraction")
    original, plan, workers = protocol.prompts_for_extraction()
    for name, text in [
        ("extract", original),
        ("coordinator", plan),
        *[(f"worker-{i}", p) for i, p in enumerate(workers, 1)],
    ]:
        output = directory / "prompts" / f"{name}.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "git_status": subprocess.check_output(
            ["git", "status", "--short"], cwd=ROOT, text=True
        ),
        "model": MODEL,
        "provider": PROVIDER,
        "allow_fallbacks": False,
        "temperature": None,
        "max_tokens": None,
        "seed": None,
        "extraction_epochs": 3,
        "samples": samples,
        "extraction": {
            "workers": 20,
            "coordinator_rounds": 6,
            "total_rounds": 114,
            "worker_rounds": [6] * 8 + [5] * 12,
            "coordinator_nominal_usd": 0.30,
            "worker_nominal_usd": 0.27,
            "worker_parallelism": 4,
        },
        "allowed_new_extraction_ids": [
            s["id"] for s in samples if s["label"] == "honest"
        ],
        "debate": {
            "repetitions": None,
            "judge_model": None,
            "judge_provider": None,
            "debater_model": MODEL,
            "debater_provider": PROVIDER,
            "rounds": 3,
            "debater_max_steps": 8,
            "debater_max_cost": 0.40,
            "judge_max_steps": 12,
            "judge_max_cost": 0.60,
            "artifact_access": "read/search/list",
            "top_fixes": 3,
        },
        "aggregation": None,
        "source_sha256": {
            str(p.relative_to(ROOT)): file_sha(p)
            for p in [
                *ROOT.glob("src/debate_asb/**/*.py"),
                *ROOT.glob("src/debate_asb/prompts/**/*.md"),
            ]
        },
        "prompt_sha256": {
            p.name: file_sha(p) for p in (directory / "prompts").glob("*.md")
        },
        "reviews": [],
    }
    write_json(path, manifest)
    import_extractions(directory, logs)
    return manifest


def validate_dataset(manifest: dict, ids: set[str]) -> None:
    for sample in manifest["samples"]:
        if sample["id"] not in ids:
            continue
        root = Path(sample["root"])
        current = {
            str(p.relative_to(root)): file_sha(p) for p in Codebase(root)._files(root)
        }
        if current != sample["visible_files_sha256"]:
            raise ValueError(f"Dataset changed since preparation: {sample['id']}")


def status(directory: Path) -> dict:
    manifest = read_json(directory / "study.json")
    complete = {
        (r["sample_id"], r["extraction_epoch"])
        for p in (directory / "extractions").glob("*.json")
        if (r := read_json(p))
    }
    missing = [
        (s["id"], e)
        for s in manifest["samples"]
        for e in range(1, manifest["extraction_epochs"] + 1)
        if (s["id"], e) not in complete
    ]
    return {
        "extractions_complete": len(complete),
        "extractions_expected": len(manifest["samples"])
        * manifest["extraction_epochs"],
        "missing": missing,
        "debate_jobs_complete": len(list((directory / "debates").glob("*.json"))),
    }
