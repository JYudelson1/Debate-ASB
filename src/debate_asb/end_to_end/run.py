"""Resumable execution of study stages using the existing Inspect protocols."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from inspect_ai import eval
from inspect_ai.util import store

from debate_asb.datasets.asb import asb_samples
from debate_asb.evidence import content_sha256, extraction_result_from_dict
from debate_asb.models import check_openrouter_account
from debate_asb.protocol import AuditSample, ToolSet, note
from debate_asb.protocols.evidence_closure import EvidenceClosure
from debate_asb.task import protocol_task

from .study import (
    closure_protocol,
    extraction_path,
    freeze_sources,
    read_json,
    validate_dataset,
    write_json,
)


@dataclass
class StudyExtraction(EvidenceClosure):
    directory: Path = Path(".")

    async def extract(self, sample: AuditSample, tools: ToolSet) -> dict:
        path = extraction_path(self.directory, sample.id, sample.epoch)
        if path.exists():
            record = read_json(path)
            extraction_result_from_dict(record["extraction"])
            note(
                "extraction_reused",
                source=str(path),
                sha256=record["extraction_sha256"],
            )
            return record["extraction"]
        result = await super().extract(sample, tools)
        import json

        write_json(
            path,
            {
                "sample_id": sample.id,
                "extraction_epoch": sample.epoch,
                "extraction": result,
                "extraction_sha256": content_sha256(json.dumps(result, sort_keys=True)),
                "imported": False,
            },
        )
        return result


def run_extractions(
    directory: Path,
    max_attempts: int = 5,
    *,
    max_samples: int = 8,
    max_connections: int = 32,
) -> None:
    manifest = read_json(directory / "study.json")
    allowed = set(manifest["allowed_new_extraction_ids"])
    ids = {
        s["id"]
        for s in manifest["samples"]
        if s["id"] in allowed
        and any(
            not extraction_path(directory, s["id"], e).exists()
            for e in range(1, manifest["extraction_epochs"] + 1)
        )
    }
    if not ids:
        print("All authorized extractions are complete.", flush=True)
        return
    validate_dataset(manifest, ids)
    check_openrouter_account()
    base = closure_protocol(
        stop_after="extraction",
        checkpoint_dir=directory / "checkpoints",
        coordinator_format=manifest["extraction"].get(
            "coordinator_response_format", "array"
        ),
    )
    protocol = StudyExtraction(**base.__dict__, directory=directory)
    logs_dir = directory / "extraction-logs"
    for _ in range(max_attempts):
        attempt = len(list(logs_dir.glob("attempt-*"))) + 1
        folder = logs_dir / f"attempt-{attempt}"
        folder.mkdir(parents=True)
        freeze_sources(directory, f"extraction-attempt-{attempt}")
        os.environ["INSPECT_CACHE_DIR"] = str(folder / "cache")
        write_json(
            folder / "settings.json",
            {
                "study": str(directory / "study.json"),
                "sample_ids": sorted(ids),
                "attempt": attempt,
                "max_samples": max_samples,
                "max_connections": max_connections,
            },
        )
        print(
            f"Extraction attempt {attempt}: {len(ids)} codebases × 3 epochs", flush=True
        )
        logs = eval(
            protocol_task(protocol, asb_samples(sorted(ids))),
            model="none",
            epochs=manifest["extraction_epochs"],
            log_dir=str(folder),
            log_model_api=True,
            max_samples=max_samples,
            max_connections=max_connections,
            fail_on_error=False,
            display="plain",
            metadata={"study_manifest": str(directory / "study.json")},
        )
        for log in logs:
            write_json(
                folder / "result.json", {"status": log.status, "log": log.location}
            )
        if not logs or any(log.status == "cancelled" for log in logs):
            raise KeyboardInterrupt(
                "Extraction interrupted; valid checkpoints retained"
            )
        ids = {
            sid
            for sid in ids
            if any(
                not extraction_path(directory, sid, e).exists()
                for e in range(1, manifest["extraction_epochs"] + 1)
            )
        }
        if not ids:
            return
    raise RuntimeError(
        f"Extraction incomplete after {max_attempts} attempts: {sorted(ids)}"
    )
