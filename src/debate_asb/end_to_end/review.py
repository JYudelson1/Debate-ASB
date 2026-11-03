"""Run source-bound post-hoc reviews and preserve retry evidence."""

from __future__ import annotations

import json
from pathlib import Path

from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.event import ModelEvent
from inspect_ai.log import read_eval_log, resolve_sample_attachments
from inspect_ai.model import ChatMessageSystem, ChatMessageUser
from inspect_ai.solver import Generate, TaskState, solver

from debate_asb.evidence import content_sha256
from debate_asb.models import Participant, check_openrouter_account
from debate_asb.protocol import generate as generate_response

from .debate import StudyModelSpec, debate_jobs
from .review_contract import (
    citation_options,
    review_packet,
    review_prompt,
    save_review,
    validate_review,
)
from .study import MODEL, PROVIDER, file_sha, freeze_sources, read_json, write_json


def save_validation_feedback(
    directory: Path,
    key: str,
    raw: str,
    packet: dict,
    error: str,
    attempt: int,
    source_log: str | None = None,
) -> None:
    """Retain failed verification as retry feedback, without repairing an answer."""
    path = directory / "review-feedback" / f"{key}.json"
    if path.exists() and read_json(path)["attempt"] > attempt:
        return
    try:
        result = json.loads(
            raw.strip().removeprefix("```json").removesuffix("```").strip()
        )
    except ValueError:
        result = {}
    rejected = []
    citations = result.get("target_citations", []) if isinstance(result, dict) else []
    for citation in citations if isinstance(citations, list) else []:
        if not isinstance(citation, dict):
            continue
        index, quote = citation.get("excerpt"), citation.get("quote")
        if not (
            type(index) is int
            and 1 <= index <= len(packet["bundle"]["excerpts"])
            and isinstance(quote, str)
            and quote
            and quote in packet["bundle"]["excerpts"][index - 1]["text"]
        ):
            rejected.append(citation)
    write_json(
        path,
        {
            "attempt": attempt,
            "source_log": source_log,
            "rejected_response_sha256": content_sha256(raw),
            "bundle_sha256": content_sha256(
                json.dumps(packet["bundle"], sort_keys=True)
            ),
            "reference_sha256": packet["canonical_reference"]["entry_sha256"],
            "feedback": {
                "verification_error": error,
                "rejected_target_citations": rejected,
                "citation_options": citation_options(packet),
                "required_fix_identities": [
                    {"debate_epoch": f["debate_epoch"], "rank": f["rank"]}
                    for f in packet["proposed_fixes"]
                ],
                "retry_instruction": (
                    "Return a fresh complete annotation under the same criteria. "
                    "Every target citation must be an exact substring of its numbered retained excerpt. "
                    "Do not borrow quotations from auxiliary source context, the paper, or the canonical reference. "
                    "Reconsider the capture category if the defining alteration is absent from the bundle. "
                    "Assess every supplied fix exactly once; honest controls require not_applicable. "
                    "The rejected quotations are failed claims, not additional evidence."
                ),
            },
        },
    )


def recover_reviews(directory: Path) -> None:
    """Reuse original judgments when support is literal but an extra quote is not.

    No response text is rewritten. Unsupported additional quotations remain
    visible; they do not gain source verification by being retained.
    """
    ledger_path = directory / "review-recovery.json"
    ledger = read_json(ledger_path) if ledger_path.exists() else {}
    for path in sorted((directory / "review-logs").glob("attempt-*/*.eval")):
        stat = path.stat()
        identity = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "feedback_policy": 2,
        }
        if ledger.get(str(path)) == identity:
            continue
        log = read_eval_log(str(path))
        for unresolved in log.samples or []:
            sample = resolve_sample_attachments(unresolved)
            if (directory / "reviews" / f"{sample.id}.json").exists() or not isinstance(
                sample.input, str
            ):
                continue
            events = [e for e in sample.events if isinstance(e, ModelEvent)]
            if not events:
                continue
            try:
                result = validate_review(
                    events[-1].output.completion, json.loads(sample.input)
                )
            except (ValueError, KeyError, TypeError) as error:
                save_validation_feedback(
                    directory,
                    str(sample.id),
                    events[-1].output.completion,
                    json.loads(sample.input),
                    str(error),
                    int(path.parent.name.split("-")[-1]),
                    str(path),
                )
                continue
            save_review(
                directory,
                str(sample.id),
                result,
                sample.input,
                recovered_from_log=str(path),
                source_log_sha256=file_sha(path),
                original_validation_error=sample.error.message
                if sample.error
                else None,
            )
        ledger[str(path)] = identity
    write_json(ledger_path, ledger)


def annotate(directory: str, attempt: int):
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        root = Path(directory)
        packet = json.loads(state.input_text)
        messages = [
            ChatMessageSystem(content=review_prompt(packet)),
            ChatMessageUser(content=state.input_text),
        ]
        spec = StudyModelSpec(
            MODEL,
            PROVIDER,
            namespace=f"{directory}/review/{state.sample_id}/attempt-{attempt}",
        )
        output = await generate_response(
            Participant(spec), "posthoc_reviewer", messages
        )
        try:
            result = validate_review(output.completion, packet)
        except (ValueError, KeyError, TypeError) as error:
            save_validation_feedback(
                root,
                str(state.sample_id),
                output.completion,
                packet,
                str(error),
                attempt,
            )
            raise
        save_review(root, str(state.sample_id), result, state.input_text)
        return state

    return solve


def run_reviews(directory: Path, max_attempts: int = 5) -> None:
    recover_reviews(directory)
    manifest = read_json(directory / "study.json")
    jobs = debate_jobs(directory)
    eligible = {
        key
        for key in jobs
        if all(
            (directory / "debates" / f"{key}__r{e}.json").exists() for e in range(1, 4)
        )
    }
    identities = {s["id"]: s for s in manifest["samples"]}
    check_openrouter_account()
    for _ in range(max_attempts):
        samples = []
        for key, job in jobs.items():
            if key not in eligible:
                continue
            if (directory / "reviews" / f"{key}.json").exists():
                continue
            fixes = []
            for epoch in range(1, 4):
                path = directory / "debates" / f"{key}__r{epoch}.json"
                if not path.exists():
                    break
                fixes.extend(
                    {**fix, "debate_epoch": epoch}
                    for fix in read_json(path)["top_fixes"]
                )
            if len(fixes) != 9:
                continue
            identity = identities[job["sample_id"]]
            packet = review_packet(
                job, Path(identity["root"]), fixes, identity["label"]
            )
            feedback_path = directory / "review-feedback" / f"{key}.json"
            if feedback_path.exists():
                feedback = read_json(feedback_path)
                if (
                    feedback["bundle_sha256"] != job["bundle_sha256"]
                    or feedback["reference_sha256"]
                    != packet["canonical_reference"]["entry_sha256"]
                ):
                    raise ValueError(f"Review retry feedback evidence changed: {key}")
                packet["previous_validation_feedback"] = {
                    **feedback["feedback"],
                    "citation_options": citation_options(packet),
                }
            samples.append(Sample(id=key, input=json.dumps(packet, ensure_ascii=False)))
        if not samples:
            return
        folder = (
            directory
            / "review-logs"
            / f"attempt-{len(list((directory / 'review-logs').glob('attempt-*'))) + 1}"
        )
        folder.mkdir(parents=True)
        attempt = int(folder.name.split("-")[-1])
        freeze_sources(directory, f"review-attempt-{attempt}")
        logs = eval(
            Task(
                dataset=samples,
                solver=annotate(str(directory), attempt),
                name="posthoc_bundle_review",
            ),
            model="none",
            max_samples=16,
            max_connections=16,
            fail_on_error=False,
            log_dir=str(folder),
            log_model_api=True,
            display="plain",
        )
        for log in logs:
            write_json(
                folder / "result.json", {"status": log.status, "log": log.location}
            )
        if not logs or any(log.status == "cancelled" for log in logs):
            raise KeyboardInterrupt("Review interrupted; valid annotations retained")
    if any(
        not (directory / "reviews" / f"{key}.json").exists()
        and all(
            (directory / "debates" / f"{key}__r{e}.json").exists() for e in range(1, 4)
        )
        for key in eligible
    ):
        raise RuntimeError(
            "Some post-hoc reviews remain incomplete; inspect failed review logs"
        )
