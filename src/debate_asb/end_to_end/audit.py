"""Offline human audit of saved reviews; never mutates experimental records."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

import inspect_ai.log  # noqa: F401 -- enables Inspect's Zstandard ZIP support


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def resolve_attachments(value, attachments):
    if isinstance(value, str) and value.startswith("attachment://"):
        return attachments[value.removeprefix("attachment://")]
    if isinstance(value, list):
        return [resolve_attachments(v, attachments) for v in value]
    if isinstance(value, dict):
        return {k: resolve_attachments(v, attachments) for k, v in value.items()}
    return value


def message_text(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    return "\n".join(c["text"] for c in content if c.get("type") == "text")


def logged_requests(directory: Path, reviews: dict):
    """Match persisted outputs to exact logged requests, including retry variants.

    Read only public input/output text, not private model reasoning. References
    and source context come from the logged packet, never a current reconstruction.
    """
    found = {}
    for path in sorted((directory / "review-logs").glob("attempt-*/*.eval")):
        with path.open("rb") as source:
            log_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if not name.startswith("samples/") or not name.endswith(".json"):
                    continue
                sample = json.loads(archive.read(name))
                key = str(sample["id"])
                if key not in reviews:
                    continue
                saved = reviews[key]
                attachments = sample.get("attachments", {})
                for event in sample.get("events", []):
                    if event.get("event") != "model":
                        continue
                    messages = event.get("input", [])
                    if not messages and event.get("input_refs"):
                        bank = sample.get("events_data", {}).get("messages", [])
                        messages = [
                            m
                            for start, end in event["input_refs"]
                            for m in bank[start:end]
                        ]
                    # Resolve only request fields and visible completion text.
                    messages = [
                        {
                            "role": m["role"],
                            "content": resolve_attachments(
                                m.get("content", ""), attachments
                            ),
                        }
                        for m in messages
                    ]
                    system = "\n".join(
                        message_text(m) for m in messages if m["role"] == "system"
                    )
                    user = next(
                        (
                            message_text(m)
                            for m in reversed(messages)
                            if m["role"] == "user"
                        ),
                        "",
                    )
                    if sha(user) != saved.get("packet_sha256") or sha(
                        system
                    ) != saved.get("prompt_sha256"):
                        continue
                    choices = (event.get("output") or {}).get("choices", [])
                    if not choices:
                        continue
                    output = message_text(choices[0]["message"])
                    output = resolve_attachments(output, attachments)
                    if not isinstance(output, str):
                        continue
                    try:
                        result = json.loads(
                            output.strip()
                            .removeprefix("```json")
                            .removesuffix("```")
                            .strip()
                        )
                    except (ValueError, TypeError):
                        continue
                    if any(
                        result.get(k) != saved.get(k)
                        for k in ("target_relation", "target_reason", "fix_reviews")
                    ):
                        continue
                    found[key] = {
                        "system": system,
                        "user": user,
                        "packet": json.loads(user),
                        "output": output,
                        "log": str(path.resolve()),
                        "log_sha256": log_sha256,
                        "log_member": name,
                        "event_uuid": event.get("uuid"),
                        "timestamp": event.get("timestamp"),
                        "model": event.get("model"),
                        "provider": event.get("config", {})
                        .get("cache", {})
                        .get("scopes", {})
                        .get("openrouter_provider"),
                        "packet_sha256": sha(user),
                        "prompt_sha256": sha(system),
                    }
    return found


def select_prompt_examples(requests: dict, items: list[dict]) -> list[str]:
    """Deterministic browsing examples, not a statistical audit sample.

    Include one per codebase/epoch and each observed prompt/retry/verdict group.
    No randomness or reviewer confidence influences selection.
    """
    groups = defaultdict(list)
    for item in items:
        key = item["key"]
        if key not in requests:
            continue
        request = requests[key]
        groups[("epoch", item["sample_id"], item["extraction_epoch"])].append(key)
        groups[("prompt", request["prompt_sha256"])].append(key)
        groups[
            ("coverage", item["sample_id"], item["model_review"]["target_relation"])
        ].append(key)
        for fix in item["model_review"]["fix_reviews"]:
            groups[("fix", fix["undoes_sabotage"])].append(key)
        if request["packet"].get("previous_validation_feedback"):
            groups[("retry", item["sample_id"])].append(key)
    return sorted({min(keys, key=sha) for keys in groups.values()})


def build_audit(directory: Path) -> dict:
    report_path = directory / "report-data.json"
    report_text = report_path.read_text()
    report = json.loads(report_text)
    reviews = {
        p.stem: json.loads(p.read_text())
        for p in (directory / "reviews").glob("*.json")
    }
    requests = logged_requests(directory, reviews)
    items = []
    contexts = {}
    samples = []
    unmatched = []
    for sample in report["samples"]:
        samples.append({k: sample[k] for k in ("id", "label", "reference")})
        for bundle in sample["bundles"]:
            key = bundle["key"]
            model = reviews.get(key)
            if (
                model
                and model.get("bundle_sha256", bundle["bundle_sha256"])
                != bundle["bundle_sha256"]
            ):
                raise ValueError(f"Review/bundle hash mismatch: {key}")
            if (
                model
                and model.get("reference_sha256") != sample["reference"]["entry_sha256"]
            ):
                raise ValueError(f"Review/reference hash mismatch: {key}")
            request = requests.get(key)
            if (
                request
                and sha(json.dumps(request["packet"]["bundle"], sort_keys=True))
                != bundle["bundle_sha256"]
            ):
                raise ValueError(f"Logged/bundle hash mismatch: {key}")
            if model and not request:
                unmatched.append(key)
            context_ids = []
            if request:
                for context in request["packet"].get("source_context", []):
                    context_id = sha(json.dumps(context, sort_keys=True))
                    contexts[context_id] = context
                    context_ids.append(context_id)
            fixes = [
                {**fix, "debate_epoch": debate["debate_epoch"]}
                for debate in bundle["debates"]
                for fix in debate["top_fixes"]
            ]
            expected = {(f["debate_epoch"], f["rank"]) for f in fixes}
            if model and (
                len(model["fix_reviews"]) != len(expected)
                or {(f["debate_epoch"], f["rank"]) for f in model["fix_reviews"]}
                != expected
            ):
                raise ValueError(f"Review/fix identity mismatch: {key}")
            if request and request["packet"]["proposed_fixes"] != fixes:
                raise ValueError(f"Logged/final fixes differ: {key}")
            saved = bundle.get("review") or {}
            # Saved analyst coverage and additional model coverage remain distinct.
            analyst = (
                {
                    k: v
                    for k, v in saved.items()
                    if k
                    not in {
                        "reviewed_bundle",
                        "supplementary_model_review",
                        "fix_reviews",
                    }
                }
                if saved.get("review_status") == "saved_analyst_review"
                else None
            )
            items.append(
                {
                    "key": key,
                    "sample_id": sample["id"],
                    "number": bundle["number"],
                    "extraction_epoch": bundle["extraction_epoch"],
                    "observation": bundle["observation"],
                    "excerpts": bundle["excerpts"],
                    "bundle_sha256": bundle["bundle_sha256"],
                    "extraction_sha256": bundle["extraction_sha256"],
                    "source_log": bundle["source_log"],
                    "model_review": model,
                    "review_sha256": sha(json.dumps(model, sort_keys=True)),
                    "analyst_review": analyst,
                    "fixes": fixes,
                    "context_ids": context_ids,
                    "request_source": {
                        k: v
                        for k, v in request.items()
                        if k not in {"system", "user", "packet", "output"}
                    }
                    if request
                    else None,
                }
            )
    items.sort(key=lambda b: (b["sample_id"], b["extraction_epoch"], b["number"]))
    example_keys = select_prompt_examples(requests, items)
    examples = [
        {"key": key, **{k: v for k, v in requests[key].items() if k != "packet"}}
        for key in example_keys
    ]
    return {
        "schema_version": 1,
        "title": "Review audit",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "report_path": str(report_path.resolve()),
            "report_sha256": sha(report_text),
            "study": report["study"],
            "study_provenance": report["provenance"],
            "audit_builder_sha256": sha(Path(__file__).read_text()),
        },
        "samples": samples,
        "items": items,
        "contexts": contexts,
        "prompt_examples": examples,
        "counts": {
            "bundles": len(items),
            "model_reviews": len(reviews),
            "logged_requests_matched": len(requests),
            "prompt_examples": len(examples),
            "fixes": sum(
                len(b["model_review"]["fix_reviews"])
                for b in items
                if b["model_review"]
            ),
            "prompt_versions": dict(
                Counter(r["prompt_sha256"] for r in requests.values())
            ),
        },
        "unmatched_requests": unmatched,
        "prompt_selection": "Deterministic examples: one per codebase/extraction epoch, coverage label per codebase, prompt hash, fix verdict, and retry per codebase; smallest SHA-256 of bundle key in each group. This is a browsing aid, not an unbiased statistical sample.",
    }


def render_audit(directory: Path) -> Path:
    payload = build_audit(directory)
    assets = files("debate_asb.viewer") / "static" / "review_audit"
    payload["source"]["viewer_sources_sha256"] = {
        name: sha((assets / name).read_text())
        for name in ("audit.html", "audit.css", "audit.js")
    }
    html = (assets / "audit.html").read_text()
    html = html.replace("/* AUDIT_CSS */", (assets / "audit.css").read_text())
    html = html.replace("/* AUDIT_JS */", (assets / "audit.js").read_text())
    html = html.replace(
        "AUDIT_DATA",
        json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).replace("<", "\\u003c"),
    )
    output = directory / "review-audit.html"
    output.write_text(html)
    return output
