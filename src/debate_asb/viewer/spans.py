"""Attribute Inspect events to the protocol activity that produced them.

Protocols wrap each activity in a span named by
``simple_debate._activity_span_name`` (``bundle/3/debate/round/1/clean``,
``bundle/3/judgment``, or the same without the ``bundle/{n}/`` prefix for a
whole-codebase debate) and extract-and-evaluate wraps extraction in an
``extraction`` span. Model, tool and info events sit in nested spans below
these, so an event's activity is its nearest ancestor with such a name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal

ActivityKind = Literal["extraction", "debate", "judgment"]

ACTIVITY_SPAN = re.compile(
    r"^(?:bundle/(?P<bundle>\d+)/)?"
    r"(?:debate/round/(?P<round>\d+)/(?P<side>[a-z_]+)"
    r"|(?P<judgment>judgment)"
    r"|(?P<extraction>extraction))$"
)


@dataclass(frozen=True)
class Activity:
    kind: ActivityKind
    bundle: int | None = None
    round: int | None = None
    side: str | None = None

    @property
    def participant(self) -> str:
        """Who acted: a debate side ("sabotaged"/"clean"), "judge" or "extractor"."""
        if self.kind == "debate":
            return str(self.side)
        return "judge" if self.kind == "judgment" else "extractor"


def parse_activity(name: str) -> Activity | None:
    match = ACTIVITY_SPAN.match(name)
    if match is None:
        return None
    bundle = int(match["bundle"]) if match["bundle"] else None
    if match["round"]:
        return Activity("debate", bundle, int(match["round"]), match["side"])
    return Activity("judgment" if match["judgment"] else "extraction", bundle)


class SpanIndex:
    """Span tree of one sample's events, for looking up an event's activity."""

    def __init__(self, events: Iterable[Any]):
        self._spans: dict[str, tuple[str | None, str]] = {
            event.id: (event.parent_id, event.name)
            for event in events
            if event.event == "span_begin"
        }
        self._activities: dict[str, Activity | None] = {}

    def activity(self, span_id: str | None) -> Activity | None:
        if span_id is None:
            return None
        if span_id not in self._activities:
            self._activities[span_id] = self._find_activity(span_id)
        return self._activities[span_id]

    def _find_activity(self, span_id: str) -> Activity | None:
        current: str | None = span_id
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            parent, name = self._spans.get(current, (None, ""))
            activity = parse_activity(name)
            if activity is not None:
                return activity
            current = parent
        return None
