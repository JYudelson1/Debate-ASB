"""Flag failures and edge cases a reader should see before trusting a transcript.

Each check is a function ``(DiagnosticContext) -> Iterable[DiagnosticView]``
listed in ``CHECKS``. To flag something new, write a check and add it there;
``diagnose`` handles ordering and merging repeats.

Checks read only what the log retains: protocol ``note()`` entries (located by
their Inspect span, see spans.py), model events, and the adapted bundles.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any

from debate_asb.protocol import CREDENCE_REMINDER, OUT_OF_TOOL_CALLS
from debate_asb.viewer.schema import BundleView, DiagnosticView, Severity
from debate_asb.viewer.spans import Activity, SpanIndex

# The plain-text form protocol.flatten_tool_use gives a tool call.
TOOL_CALL_TEXT = re.compile(r"^\[called [\w.-]+\(.*\)\]$")
TRUNCATED_STOP_REASONS = {"max_tokens", "model_length"}
SEVERITY_ORDER: dict[Severity, int] = {"error": 0, "warning": 1, "info": 2}
PARTICIPANT_LABELS = {
    "extractor": "the extractor",
    "sabotaged": "the debater arguing SABOTAGED",
    "clean": "the debater arguing CLEAN",
    "judge": "the judge",
}
PARTICIPANT_ORDER = {name: position for position, name in enumerate(PARTICIPANT_LABELS)}


@dataclass(frozen=True)
class LocatedNote:
    """One protocol note() entry and the activity it was recorded in, if known."""

    entry: dict[str, Any]
    activity: Activity | None


@dataclass(frozen=True)
class LocatedModelCall:
    event: Any
    activity: Activity | None


@dataclass(frozen=True)
class DiagnosticContext:
    bundles: tuple[BundleView, ...]
    notes: tuple[LocatedNote, ...]
    model_calls: tuple[LocatedModelCall, ...]
    error: dict[str, Any] | None = None
    replay_source: str | None = None

    @classmethod
    def from_sample(
        cls,
        events: list[Any],
        spans: SpanIndex,
        transcript: list[dict[str, Any]],
        bundles: tuple[BundleView, ...],
        error: dict[str, Any] | None,
        replay_source: str | None,
    ) -> DiagnosticContext:
        return cls(
            bundles=bundles,
            notes=_located_notes(events, spans, transcript),
            model_calls=tuple(
                LocatedModelCall(event, spans.activity(event.span_id))
                for event in events
                if event.event == "model"
            ),
            error=error,
            replay_source=replay_source,
        )

    def notes_named(self, name: str) -> Iterable[LocatedNote]:
        return (note for note in self.notes if note.entry.get("event") == name)


Check = Callable[[DiagnosticContext], Iterable[DiagnosticView]]


def diagnose(
    context: DiagnosticContext, checks: Iterable[Check] | None = None
) -> tuple[DiagnosticView, ...]:
    """Run every check; identical findings at one location merge into a count."""
    merged: dict[tuple[Any, ...], DiagnosticView] = {}
    for check in CHECKS if checks is None else checks:
        for found in check(context):
            key = (found.code, found.bundle, found.round, found.participant)
            if key in merged:
                merged[key] = replace(merged[key], count=merged[key].count + 1)
            else:
                merged[key] = found
    return tuple(sorted(merged.values(), key=_sort_key))


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


def flattened_tool_history(context: DiagnosticContext) -> Iterable[DiagnosticView]:
    for note in context.notes_named("flattened_tool_history_after_invalid_argument"):
        who = _who(note.activity, note.entry.get("role"))
        kind = note.activity.kind if note.activity else None
        persistence = (
            "This debater keeps the flattened history for its remaining turns "
            "in this debate."
            if kind == "debate"
            else "The rest of this activity runs on the flattened history."
        )
        yield _diagnostic(
            "flattened_tool_history",
            "warning",
            "Tool history flattened after provider rejection",
            f"The provider rejected a request from {who} with HTTP 400 "
            "INVALID_ARGUMENT, so its earlier tool calls and results were "
            "rewritten as plain text ('[called …]', '[result of …]') and the "
            f"request retried. {persistence} Models sometimes imitate the "
            "'[called …]' text instead of calling tools.",
            note.activity,
        )


def ignored_tool_choice(context: DiagnosticContext) -> Iterable[DiagnosticView]:
    for note in context.notes_named("ignored_tool_choice_none"):
        who = _who(note.activity, note.entry.get("role"))
        yield _diagnostic(
            "ignored_tool_choice_none",
            "warning",
            "Model called tools when told to answer",
            f"{_capitalize(who)} made tool calls when asked for a plain answer "
            "(tool_choice='none'). The calls were discarded and it answered "
            "from a flattened copy of the conversation.",
            note.activity,
        )


def text_only_tool_calls(context: DiagnosticContext) -> Iterable[DiagnosticView]:
    for bundle in context.bundles:
        for turn in bundle.turns:
            activity = Activity("debate", bundle.number, turn.round, turn.side)
            found = _text_tool_call_diagnostic(
                turn.argument.source, "Argument", activity
            )
            if found:
                yield found
        if bundle.judge_response is not None:
            found = _text_tool_call_diagnostic(
                bundle.judge_response.source,
                "Judge response",
                Activity("judgment", bundle.number),
            )
            if found:
                yield found


def empty_arguments(context: DiagnosticContext) -> Iterable[DiagnosticView]:
    for bundle in context.bundles:
        for turn in bundle.turns:
            if turn.argument.source.strip() in {"", "[no text]"}:
                yield _diagnostic(
                    "empty_argument",
                    "error",
                    "Empty argument",
                    "This debater's final message had no text, so the turn "
                    "contributed nothing to the debate.",
                    Activity("debate", bundle.number, turn.round, turn.side),
                )


def missing_credence(context: DiagnosticContext) -> Iterable[DiagnosticView]:
    for bundle in context.bundles:
        if bundle.judge_response is not None and bundle.judge_credence is None:
            yield _diagnostic(
                "missing_credence",
                "warning",
                "No parsable judge credence",
                "The judge's retained response has no 'Sabotage Credence: N%' "
                "line, even after one reminder, so this bundle is left out of "
                "the max-credence aggregate.",
                Activity("judgment", bundle.number),
            )


def tool_budget_exhausted(context: DiagnosticContext) -> Iterable[DiagnosticView]:
    for activity in _activities_where_last_input_is(context, OUT_OF_TOOL_CALLS):
        yield _diagnostic(
            "tool_budget_exhausted",
            "info",
            "Tool budget exhausted",
            f"{_capitalize(_who(activity))} hit its step or cost limit "
            "mid-investigation and was told to answer without more tool calls.",
            activity,
        )


def credence_reminder(context: DiagnosticContext) -> Iterable[DiagnosticView]:
    for activity in _activities_where_last_input_is(context, CREDENCE_REMINDER):
        yield _diagnostic(
            "credence_reminder",
            "info",
            "Credence reminder needed",
            f"{_capitalize(_who(activity))}'s answer had no 'Sabotage Credence: "
            "N%' line, so it was asked once more. The retained response is its "
            "reply to that reminder.",
            activity,
        )


def truncated_output(context: DiagnosticContext) -> Iterable[DiagnosticView]:
    for call in context.model_calls:
        output = getattr(call.event, "output", None)
        stop_reason = getattr(output, "stop_reason", None)
        if stop_reason in TRUNCATED_STOP_REASONS:
            yield _diagnostic(
                "truncated_output",
                "warning",
                "Model output hit the token limit",
                f"A reply from {_who(call.activity)} stopped with "
                f"stop_reason={stop_reason!r} and may be cut off.",
                call.activity,
            )


def replayed_stages(context: DiagnosticContext) -> Iterable[DiagnosticView]:
    for note in context.notes_named("stage_replayed"):
        stage = note.entry.get("stage", "unknown")
        source = context.replay_source or "an earlier log"
        yield _diagnostic(
            f"stage_replayed:{stage}",
            "info",
            f"Stage replayed: {stage}",
            f"The '{stage}' stage was loaded from {source} rather than run "
            "in this eval.",
        )


def inferred_from_transcript(context: DiagnosticContext) -> Iterable[DiagnosticView]:
    for bundle in context.bundles:
        if bundle.metadata.get("turns_inferred_from_transcript"):
            yield _diagnostic(
                "turns_inferred_from_transcript",
                "info",
                "Debate turns reconstructed from transcript notes",
                "The bundle_debates stage was not saved (the sample likely "
                "stopped early), so these turns come from the protocol's "
                "note() entries instead.",
                bundle=bundle.number,
            )
        if bundle.metadata.get("judgment_inferred_from_transcript"):
            yield _diagnostic(
                "judgment_inferred_from_transcript",
                "info",
                "Judge credence reconstructed from transcript notes",
                "The bundle_judgments stage was not saved, so only the credence "
                "from the protocol's note() entries is available.",
                bundle=bundle.number,
            )


def sample_error(context: DiagnosticContext) -> Iterable[DiagnosticView]:
    if not context.error:
        return
    message = str(context.error.get("message") or "Unknown error")
    first_line = next((line for line in message.splitlines() if line.strip()), "")
    yield _diagnostic(
        "sample_error",
        "error",
        "Sample stopped with an error",
        f"Stages after the failure did not run. {first_line[:300]}",
        evidence=str(context.error.get("traceback") or message),
    )


CHECKS: tuple[Check, ...] = (
    sample_error,
    flattened_tool_history,
    ignored_tool_choice,
    text_only_tool_calls,
    empty_arguments,
    missing_credence,
    truncated_output,
    tool_budget_exhausted,
    credence_reminder,
    replayed_stages,
    inferred_from_transcript,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _located_notes(
    events: list[Any], spans: SpanIndex, transcript: list[dict[str, Any]]
) -> tuple[LocatedNote, ...]:
    """note() entries, located via their Inspect info events when the log has them."""
    from_events = tuple(
        LocatedNote(event.data, spans.activity(event.span_id))
        for event in events
        if event.event == "info"
        and isinstance(getattr(event, "data", None), dict)
        and "event" in event.data
    )
    if from_events:
        return from_events
    return tuple(LocatedNote(entry, None) for entry in transcript)


def _activities_where_last_input_is(
    context: DiagnosticContext, text: str
) -> list[Activity | None]:
    """Activities with a model call whose newest input is this user message, once each."""
    seen: list[Activity | None] = []
    for call in context.model_calls:
        messages = getattr(call.event, "input", None) or []
        last = messages[-1] if messages else None
        if (
            last is not None
            and getattr(last, "role", None) == "user"
            and last.text == text
            and call.activity not in seen
        ):
            seen.append(call.activity)
    return seen


def _text_tool_call_diagnostic(
    text: str, subject: str, activity: Activity
) -> DiagnosticView | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    calls = [line for line in lines if TOOL_CALL_TEXT.match(line)]
    if not calls:
        return None
    if len(calls) == len(lines):
        return _diagnostic(
            "text_only_tool_call",
            "error",
            f"{subject} is an unexecuted tool call",
            f"{_capitalize(_who(activity))} wrote a tool call as plain text "
            "instead of calling the tool, so nothing ran and this text was "
            f"recorded as the final {subject.lower()}. Anyone shown this turn "
            "afterwards (the opponent's next turn, the judge) saw only this "
            "text. This usually follows tool-history flattening.",
            activity,
            evidence="\n".join(calls),
        )
    return _diagnostic(
        "partial_text_tool_call",
        "warning",
        f"{subject} contains text-only tool calls",
        "Some lines look like '[called …]' tool calls written as text. Those "
        "calls were not executed.",
        activity,
        evidence="\n".join(calls),
    )


def _diagnostic(
    code: str,
    severity: Severity,
    title: str,
    detail: str,
    activity: Activity | None = None,
    *,
    bundle: int | None = None,
    evidence: str | None = None,
) -> DiagnosticView:
    return DiagnosticView(
        code=code,
        severity=severity,
        title=title,
        detail=detail,
        bundle=activity.bundle if activity else bundle,
        round=activity.round if activity else None,
        participant=activity.participant if activity else None,
        evidence=evidence,
    )


def _who(activity: Activity | None, role: str | None = None) -> str:
    participant = (
        activity.participant
        if activity
        else (role or "").removeprefix("debater_") or None
    )
    if participant is None:
        return "a participant"
    return PARTICIPANT_LABELS.get(participant, participant)


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def _sort_key(found: DiagnosticView) -> tuple[Any, ...]:
    return (
        SEVERITY_ORDER[found.severity],
        found.bundle is not None,
        found.bundle or 0,
        found.participant == "judge",
        found.round or 0,
        PARTICIPANT_ORDER.get(found.participant or "", len(PARTICIPANT_ORDER)),
    )
