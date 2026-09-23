"""An agent trajectory: the message history of one agent run, replayed step by step.

Mainly for fast iteration: one known-subtle sabotage trajectory is a much
tighter loop than a whole codebase.

A trajectory is a list of Inspect chat messages (the same format as the
`messages` of any sample in an Inspect eval log). Two views:

- "full": everything the agent saw and did, including its text and reasoning.
- "output_only": the prompts, the agent's tool calls, and what those calls
  returned; the agent's own text and reasoning are dropped. This is the
  standard monitoring ablation (and what MonitoringBench's monitors see). It's
  applied when the artifact is built, so no primitive can leak what the view
  hides.
"""

import json
import re
from typing import Literal

from inspect_ai.log import read_eval_log_sample
from inspect_ai.model import ChatMessage, ChatMessageAssistant, ChatMessageTool
from pydantic import TypeAdapter

from debate_asb.artifacts.base import Artifact, ArtifactTooLarge, estimate_tokens, primitive

View = Literal["full", "output_only"]
MAX_STEP_CHARS = 20_000
_MESSAGES = TypeAdapter(list[ChatMessage])


class Trajectory(Artifact):
    """Give either `messages`, or `eval_log` + `sample_uuid` to read one sample of an Inspect log."""

    def __init__(
        self,
        messages: list[ChatMessage] | list[dict] | None = None,
        view: View = "full",
        eval_log: str | None = None,
        sample_uuid: str | None = None,
    ):
        if messages is None:
            sample = read_eval_log_sample(eval_log, uuid=sample_uuid, resolve_attachments=True)
            messages = sample.messages
        messages = _MESSAGES.validate_python(messages)
        self.view = view
        self.steps = messages if view == "full" else _output_only(messages)

    @primitive
    def list_steps(self) -> str:
        """List every step of the agent trajectory with a one-line summary."""
        return "\n".join(f"step {i}: {_summary(m)}" for i, m in enumerate(self.steps))

    @primitive
    def read_step(self, step: int) -> str:
        """Read one step of the agent trajectory in full.

        Args:
            step: Step number, as shown by list_steps.
        """
        if not 0 <= step < len(self.steps):
            raise ValueError(f"No step {step}; the trajectory has steps 0-{len(self.steps) - 1}")
        text = _render(self.steps[step])
        if len(text) > MAX_STEP_CHARS:
            text = text[:MAX_STEP_CHARS] + f"\n[... step truncated, {len(text):,} chars]"
        return text

    @primitive
    def search_trajectory(self, pattern: str, ignore_case: bool = False) -> str:
        """Search the whole trajectory for a regular expression; returns matching steps.

        Args:
            pattern: Python regular expression.
            ignore_case: Case-insensitive matching.
        """
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        hits = [f"step {i}: {_summary(m)}" for i, m in enumerate(self.steps) if regex.search(_render(m))]
        return "\n".join(hits) or "No matches."

    def dump_all(self, max_tokens: int) -> str:
        dump = "\n\n".join(f"===== step {i} =====\n{_render(m)}" for i, m in enumerate(self.steps))
        tokens = estimate_tokens(dump)
        if tokens > max_tokens:
            raise ArtifactTooLarge(f"trajectory dump is ~{tokens:,} tokens, over {max_tokens:,}")
        return dump


def _output_only(messages: list[ChatMessage]) -> list[ChatMessage]:
    kept: list[ChatMessage] = []
    for m in messages:
        if isinstance(m, ChatMessageAssistant):
            if m.tool_calls:
                kept.append(ChatMessageAssistant(content="", tool_calls=m.tool_calls))
            continue
        kept.append(m)
    return kept


def _render(m: ChatMessage) -> str:
    lines = [f"[{m.role}]"]
    if m.text:
        lines.append(m.text)
    if isinstance(m, ChatMessageAssistant):
        for part in m.content if isinstance(m.content, list) else []:
            if part.type == "reasoning" and part.reasoning:
                lines.append(f"(reasoning) {part.reasoning}")
        for call in m.tool_calls or []:
            lines.append(f"(tool call) {call.function}({json.dumps(call.arguments)})")
    if isinstance(m, ChatMessageTool):
        lines[0] = f"[tool result: {m.function}]"
    return "\n".join(lines)


def _summary(m: ChatMessage) -> str:
    if isinstance(m, ChatMessageAssistant) and m.tool_calls:
        calls = ", ".join(f"{c.function}(...)" for c in m.tool_calls)
        return f"assistant calls {calls}"
    text = " ".join(m.text.split())
    return f"{m.role}: {text[:120]}{'...' if len(text) > 120 else ''}"
