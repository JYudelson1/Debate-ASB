"""Read transcripts in a browser: every log under a directory, every sample, every turn.

    uv run python -m debate_asb.browse ../experiments    # then open http://127.0.0.1:8765

A sample's page shows its participants' turns in order. Each turn's final
reply (a debater's argument, the judge's answer) is open; what led to it (the
prompt for the turn, tool calls and their results, reasoning summaries) is
folded. So is the answer key, so a transcript can be read blind first. Keys:
n and p go to the next and previous sample.

It works for any protocol: turns come from the per-role spans that generate()
opens (protocol.py), not from protocol-specific state. A stage reused from an
earlier log (-T reuse=debate) left no events, so its saved output is shown.
`inspect view` shows the same logs event by event.
"""

import argparse
import html
import json
import math
import traceback
from collections import Counter
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from inspect_ai.event import InfoEvent, ModelEvent, SpanBeginEvent, ToolEvent
from inspect_ai.log import (
    EvalLog,
    EvalSample,
    EvalSampleSummary,
    read_eval_log,
    read_eval_log_sample,
    read_eval_log_sample_summaries,
)
from inspect_ai.model import ChatMessage
from inspect_ai.tool import ToolCall
from markdown_it import MarkdownIt  # installed with inspect_ai (through rich)

from debate_asb.task import VERDICT_THRESHOLD

MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("table")

# --------------------------------------------------------------------------
# Turns
# --------------------------------------------------------------------------


@dataclass
class Turn:
    """One participant's consecutive model calls, up to a reply without tool calls."""

    role: str
    number: int  # the role's 1st, 2nd, ... turn (for a debater, the round)
    steps: list[ModelEvent | InfoEvent] = field(default_factory=list)

    @property
    def calls(self) -> list[ModelEvent]:
        return [s for s in self.steps if isinstance(s, ModelEvent)]

    @property
    def final(self) -> ModelEvent | None:
        """The call whose reply ended the turn (None if it never ended).

        A reply followed by a generation_error note was retried (protocol.generate),
        so it didn't end the turn. Which replies get retried has changed over time,
        so the note decides, not the reply's stop reason.
        """
        last = self.steps[-1]
        if isinstance(last, ModelEvent) and not last.output.message.tool_calls:
            return last
        return None


def served(call: ModelEvent) -> bool:
    """Whether the conversation continued from this call's reply.

    Inspect also logs failed attempts (with an error) and, in some E03 logs, an
    empty twin of a call that never completed; neither is part of the conversation.
    """
    return call.error is None and call.completed is not None


def transcript_items(sample: EvalSample) -> list[Turn | InfoEvent]:
    """The sample's turns in order, with the protocol's notes (protocol.note) between them."""
    roles = {
        e.id: e.name
        for e in sample.events
        if isinstance(e, SpanBeginEvent) and e.type == "participant"
    }
    items: list[Turn | InfoEvent] = []
    counts: Counter[str] = Counter()
    turn = None
    for event in sample.events:
        if isinstance(event, ModelEvent) and served(event):
            role = roles.get(event.span_id or "", "model")
            if turn is None or turn.role != role or turn.final:
                counts[role] += 1
                turn = Turn(role, counts[role])
                items.append(turn)
            turn.steps.append(event)
        elif isinstance(event, InfoEvent) and event.source != "argument":
            # An "argument" note repeats a debater's final reply.
            data = event.data if isinstance(event.data, dict) else {}
            if turn and data.get("role") == turn.role:
                turn.steps.append(event)
            else:
                items.append(event)
    return items


def turn_prompt(turn: Turn) -> list[ChatMessage]:
    """What the participant was given at the start of the turn (its system prompt on turn 1)."""
    messages = turn.calls[0].input
    start = len(messages)
    while start > 0 and messages[start - 1].role == "user":
        start -= 1
    system = [m for m in messages if m.role == "system"] if turn.number == 1 else []
    return system + messages[start:]


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def esc(value: object) -> str:
    return html.escape(str(value))


def markdown(text: str) -> str:
    return MARKDOWN.render(text)


def fold(summary: str, body: str, cls: str = "") -> str:
    return f'<details class="{cls}"><summary>{esc(summary)}</summary>{body}</details>'


def link(route: str, **params: object) -> str:
    return esc(f"{route}?{urlencode(params)}")


def role_name(role: str) -> str:
    if role.startswith("debater_"):
        return f"Debater arguing {role.removeprefix('debater_')}"
    return role.replace("_", " ").capitalize()


def side_class(role: str) -> str:
    return next((s for s in ("sabotaged", "clean", "judge") if s in role), "other")


def render_turn(turn: Turn, tools: dict[str, ToolEvent]) -> str:
    final = turn.final
    n_tools = sum(len(c.output.message.tool_calls or []) for c in turn.calls)
    investigation = "".join(
        render_note(s) if isinstance(s, InfoEvent) else render_call(s, tools)
        for s in turn.steps
        if s is not final
    )
    prompt = "".join(
        f'<div class="message"><div class="meta">{esc(m.role)}</div>{markdown(m.text)}</div>'
        for m in turn_prompt(turn)
    )
    return f"""
<section class="turn {side_class(turn.role)}">
  <h3>{esc(role_name(turn.role))} <span class="meta">turn {turn.number}
    &middot; {n_tools} tool call{"" if n_tools == 1 else "s"}</span></h3>
  {fold("Prompt for this turn", prompt, "prompt")}
  {fold(f"Investigation ({n_tools} tool calls, {len(turn.calls)} model calls)", investigation, "investigation") if investigation else ""}
  {render_call(final, tools, final=True) if final else '<p class="error">This turn ended without a reply.</p>'}
</section>"""


def render_call(call: ModelEvent, tools: dict[str, ToolEvent], final=False) -> str:
    message = call.output.message
    parts = []
    for content in message.content if isinstance(message.content, list) else []:
        if content.type == "reasoning":
            text = content.summary if content.redacted else content.reasoning
            if text:
                parts.append(
                    fold("Reasoning summary", markdown(text), "reasoning")
                    if content.redacted
                    else fold("Reasoning", markdown(text), "reasoning")
                )
    if message.text.strip():
        parts.append(
            f'<div class="{"answer" if final else "aside"}">{markdown(message.text)}</div>'
        )
    elif final:
        parts.append('<p class="meta">(empty reply)</p>')
    parts += [render_tool_call(c, tools.get(c.id)) for c in message.tool_calls or []]
    if call.error:
        parts.append(f'<p class="error">Model error: {esc(call.error)}</p>')
    return "".join(parts)


def render_tool_call(call: ToolCall, event: ToolEvent | None) -> str:
    args = ", ".join(
        f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in call.arguments.items()
    )
    head = f"{call.function}({args})"
    short = head if len(head) <= 160 else head[:157] + "..."
    if event is None:
        result, failed = "(no result in the log)", True
    elif event.error:
        result, failed = event.error.message, True
    elif isinstance(event.result, list):
        result = "\n".join(
            getattr(c, "text", None) or f"[{c.type}]" for c in event.result
        )
        failed = False
    else:
        result, failed = str(event.result), False
    return f"""
<details class="tool{" failed" if failed else ""}">
  <summary><code>{esc(short)}</code>{' <span class="error">error</span>' if failed else ""}</summary>
  {f"<pre>{esc(head)}</pre>" if short != head else ""}<pre>{esc(result)}</pre>
</details>"""


def render_note(note: InfoEvent, stages: dict | None = None) -> str:
    data = note.data if isinstance(note.data, dict) else {"data": note.data}
    if note.source == "stage_replayed" and stages is not None:
        return render_replayed(data.get("stage"), stages.get(data.get("stage")))
    fields = ", ".join(f"{k}={v}" for k, v in data.items() if k != "event")
    return f'<p class="note">Note: {esc(note.source)} ({esc(fields)})</p>'


def render_replayed(stage: object, saved: object) -> str:
    head = f'<p class="note">Stage "{esc(stage)}" was reused from an earlier log; its saved output follows.</p>'
    if isinstance(saved, list) and all(
        isinstance(a, dict) and "argument" in a for a in saved
    ):  # simple_debate's arguments
        return head + "".join(
            f"""<section class="turn {side_class(str(a.get("side")))}">
  <h3>Debater arguing {esc(a.get("side"))} <span class="meta">round {esc(a.get("round"))} (reused)</span></h3>
  <div class="answer">{markdown(a["argument"])}</div></section>"""
            for a in saved
        )
    return head + f"<pre>{esc(json.dumps(saved, indent=2, ensure_ascii=False))}</pre>"


def render_answer_key(key: object) -> str:
    if isinstance(key, dict):
        return "".join(
            f'<div class="meta">{esc(k)}</div>'
            + (
                markdown(v)
                if isinstance(v, str)
                else f"<pre>{esc(json.dumps(v, indent=2))}</pre>"
            )
            for k, v in key.items()
        )
    return (
        markdown(key)
        if isinstance(key, str)
        else f"<pre>{esc(json.dumps(key, indent=2))}</pre>"
    )


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------


def index_page(root: Path) -> str:
    groups: dict[Path, list[Path]] = {}
    for path in sorted(root.rglob("*.eval")):
        groups.setdefault(path.parent, []).append(path)
    sections = "".join(
        log_group(root, directory, paths) for directory, paths in sorted(groups.items())
    )
    return page(
        "Transcripts",
        f"<h1>Logs under {esc(root)}</h1>{sections or '<p>No .eval logs here.</p>'}",
    )


def log_group(root: Path, directory: Path, paths: list[Path]) -> str:
    """One folder's logs. Rows show only the task args that differ within the folder."""
    logs: dict[Path, EvalLog | Exception] = {}
    for path in paths:
        try:
            logs[path] = read_eval_log(path, header_only=True)
        except Exception as e:
            logs[path] = e
    args = [log.eval.task_args for log in logs.values() if isinstance(log, EvalLog)]
    varying = {
        k for a in args for k in a if len({str(b[k]) for b in args if k in b}) > 1
    }
    rows = "".join(log_row(root, p, log, varying) for p, log in logs.items())
    return f"""<details class="group"{" open" if directory.name == "logs" else ""}>
  <summary>{esc(directory.relative_to(root).as_posix() or ".")} <span class="meta">{len(paths)} logs</span></summary>
  <table><tr><th>log</th><th>task</th><th>samples</th><th>commit</th>
  <th>{"task args that differ here (hover for all)" if varying else "task args"}</th></tr>
  {rows}</table></details>"""


def log_row(root: Path, path: Path, log: EvalLog | Exception, shown: set[str]) -> str:
    name = f'<a href="{link("/log", path=path.relative_to(root).as_posix())}">{esc(path.stem[:19])}</a>'
    if isinstance(log, Exception):
        return f'<tr><td>{name}</td><td colspan="4" class="error">{esc(log)}</td></tr>'
    return f"""<tr><td>{name}</td><td>{esc(log.eval.task)}</td><td>{esc(log_size(log))}</td>
  <td>{esc(commit(log))}</td><td class="meta" title="{esc(task_args(log))}">{esc(task_args(log, shown or None))}</td></tr>"""


def log_size(log: EvalLog) -> str:
    epochs = log.eval.config.epochs or 1
    size = f"{log.eval.dataset.samples} samples, {epochs} epoch{'' if epochs == 1 else 's'}"
    return size if log.status == "success" else f"{size}, {log.status}"


def commit(log: EvalLog) -> str:
    revision = log.eval.revision
    return (
        ""
        if revision is None
        else revision.commit + (" (dirty)" if revision.dirty else "")
    )


def task_args(log: EvalLog, keys: set[str] | None = None) -> str:
    return ", ".join(
        f"{k}={v}"
        for k, v in log.eval.task_args.items()
        if v not in (None, "", []) and (keys is None or k in keys)
    )


def log_page(root: Path, rel: str) -> str:
    path = log_path(root, rel)
    log = read_eval_log(path, header_only=True)
    rows = []
    for s in ordered_summaries(path):
        label, credence, correct = outcome(s)
        cost = (
            sum((next(iter(s.scores.values())).metadata or {}).get("cost", {}).values())
            if s.scores
            else 0.0
        )
        rows.append(f"""<tr class="{"" if correct is None else "right" if correct else "wrong"}">
  <td><a href="{link("/sample", path=rel, id=s.id, epoch=s.epoch)}">{esc(s.id)}</a></td><td>{s.epoch}</td>
  <td><span class="label {esc(label)}">{esc(label)}</span></td><td>{credence}</td>
  <td>{"" if correct is None else "&check;" if correct else "&cross;"}</td><td>${cost:.3f}</td>
  <td class="error">{esc(s.error or "")}</td></tr>""")
    return page(
        path.stem,
        f"""
<p><a href="/">all logs</a></p>
<h1>{esc(log.eval.task)} <span class="meta">{esc(path.relative_to(root).as_posix())}</span></h1>
<p class="meta">{esc(log_size(log))} &middot; commit {esc(commit(log))} &middot; {esc(task_args(log))}</p>
<label><input type="checkbox" id="wrong-only"> only wrong verdicts</label>
<table><tr><th>sample</th><th>epoch</th><th>label</th><th>credence</th><th>verdict</th><th>nominal $</th><th></th></tr>
{"".join(rows)}</table>""",
    )


def sample_page(root: Path, rel: str, sample_id: str, epoch: int) -> str:
    path = log_path(root, rel)
    summaries = ordered_summaries(path)
    i = [(str(s.id), s.epoch) for s in summaries].index((sample_id, epoch))
    sample = read_eval_log_sample(
        path, summaries[i].id, epoch, resolve_attachments=True
    )
    tools = {e.id: e for e in sample.events if isinstance(e, ToolEvent)}
    stages = sample.store.get("stages", {})
    items = "".join(
        render_turn(x, tools) if isinstance(x, Turn) else render_note(x, stages)
        for x in transcript_items(sample)
    )
    label, credence, correct = outcome(summaries[i])
    score = next(iter(sample.scores.values()), None) if sample.scores else None
    cost = (
        ", ".join(
            f"{role} ${c:.3f}"
            for role, c in (score.metadata or {}).get("cost", {}).items()
        )
        if score
        else ""
    )

    def nav(j: int, rel_: str, text: str) -> str:
        if not 0 <= j < len(summaries):
            return f'<span class="meta">{text}</span>'
        s = summaries[j]
        return f'<a rel="{rel_}" href="{link("/sample", path=rel, id=s.id, epoch=s.epoch)}">{text}</a>'

    answer_key = sample.metadata.get("answer_key")
    return page(
        f"{sample_id} (epoch {epoch})",
        f"""
<p class="nav">{nav(i - 1, "prev", "&larr; previous (p)")} &middot;
  <a href="{link("/log", path=rel)}">{esc(path.stem[:19])} {esc(sample.metadata.get("paper_name", ""))}</a> &middot;
  {nav(i + 1, "next", "next (n) &rarr;")}</p>
<h1>{esc(sample_id)} <span class="meta">epoch {epoch}</span></h1>
<p><span class="label {esc(label)}">{esc(label)}</span> &middot; judge credence {credence}
  {"" if correct is None else "&middot; verdict " + ("right" if correct else '<span class="error">wrong</span>')}
  <span class="meta">&middot; nominal {esc(cost)}</span></p>
{f'<p class="error">Sample error: {esc(sample.error.message)}</p>' if sample.error else ""}
{fold("Answer key (spoiler)", render_answer_key(answer_key), "key") if answer_key else ""}
<p class="meta">Task: {esc(sample.input if isinstance(sample.input, str) else "")}</p>
<p><button data-open="1">expand all</button> <button data-open="">collapse all</button></p>
<div class="transcript">{items}</div>""",
    )


def ordered_summaries(path: Path) -> list[EvalSampleSummary]:
    return sorted(
        read_eval_log_sample_summaries(path), key=lambda s: (str(s.id), s.epoch)
    )


def outcome(s: EvalSampleSummary) -> tuple[str, str, bool | None]:
    """The label, the judge's credence as text, and whether the verdict was right."""
    label = s.target if isinstance(s.target, str) else ",".join(s.target)
    value = next(iter(s.scores.values())).value if s.scores else None
    if not isinstance(value, (int, float)) or math.isnan(value):
        return label, "-", None
    return label, f"{value:g}%", (value >= VERDICT_THRESHOLD) == (label == "sabotaged")


def log_path(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or path.suffix != ".eval":
        raise ValueError(f"Not a log under {root}: {rel}")
    return path


def page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style></head>
<body><main>{body}</main><script>{JS}</script></body></html>"""


CSS = """
:root { --bg: #fbfaf8; --fg: #1f2328; --muted: #6a6f76; --line: #dcd9d3; --card: #ffffff;
  --code: #f3f1ed; --sabotaged: #c2410c; --clean: #0f766e; --judge: #6d28d9; --other: #6a6f76;
  --bad: #b42318; --good: #2f7a3e; }
@media (prefers-color-scheme: dark) { :root { --bg: #17181a; --fg: #e6e4e0; --muted: #9a9ea5;
  --line: #34363a; --card: #1f2023; --code: #26282b; --sabotaged: #f0834f; --clean: #4fc1b3;
  --judge: #b39af5; --other: #9a9ea5; --bad: #f2786b; --good: #7cc68a; } }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 900px; margin: 0 auto; padding: 24px 16px 80px; }
a { color: var(--judge); }
h1 { font-size: 1.5rem; margin: 0.5em 0; } .turn > h3 { margin: 0 0 0.5em; font-size: 1.05rem; }
.meta { color: var(--muted); font-size: 0.85rem; font-weight: normal; }
.error { color: var(--bad); } .note { color: var(--muted); font-size: 0.9rem; font-style: italic; }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; margin: 0.5em 0 1.5em; }
th, td { text-align: left; padding: 4px 10px 4px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
td:first-child { white-space: nowrap; }
tr.wrong td:nth-child(5) { color: var(--bad); } tr.right td:nth-child(5) { color: var(--good); }
.label { padding: 1px 8px; border-radius: 10px; font-size: 0.85rem; border: 1px solid; }
.label.sabotaged { color: var(--sabotaged); } .label.honest { color: var(--clean); }
details > summary { cursor: pointer; } .group > summary { font-size: 1.15rem; font-weight: 600; margin: 1em 0 0.3em; }
.turn { background: var(--card); border: 1px solid var(--line); border-left: 4px solid var(--other);
  border-radius: 6px; padding: 14px 18px; margin: 18px 0; }
.turn.sabotaged { border-left-color: var(--sabotaged); } .turn.sabotaged > h3 { color: var(--sabotaged); }
.turn.clean { border-left-color: var(--clean); } .turn.clean > h3 { color: var(--clean); }
.turn.judge { border-left-color: var(--judge); } .turn.judge > h3 { color: var(--judge); }
.turn > details { margin: 4px 0; color: var(--muted); font-size: 0.9rem; }
.turn > details[open] { color: var(--fg); border-left: 2px solid var(--line); padding-left: 12px; }
.investigation .aside { font-size: 0.92rem; }
.tool { margin: 3px 0; } .tool summary code { font-size: 0.82rem; }
.tool.failed summary code { color: var(--bad); }
.reasoning { font-size: 0.9rem; color: var(--muted); }
.message { border-bottom: 1px dashed var(--line); margin-bottom: 8px; }
.answer { margin-top: 10px; }
.key { background: var(--card); border: 1px solid var(--line); border-radius: 6px; padding: 8px 14px; margin: 12px 0; }
pre, code { font-family: ui-monospace, Consolas, monospace; }
pre { background: var(--code); padding: 10px; border-radius: 4px; overflow-x: auto;
  font-size: 0.8rem; line-height: 1.45; white-space: pre-wrap; word-break: break-word; max-height: 600px; }
:not(pre) > code { background: var(--code); padding: 0 3px; border-radius: 3px; font-size: 0.88em; }
button { font: inherit; font-size: 0.85rem; }
"""

JS = """
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea") || e.ctrlKey || e.metaKey || e.altKey) return;
  const a = document.querySelector({ n: "a[rel=next]", p: "a[rel=prev]" }[e.key] || "#none");
  if (a) location.href = a.href;
});
for (const b of document.querySelectorAll("button[data-open]")) {
  b.onclick = () => document.querySelectorAll(".transcript details").forEach((d) => (d.open = !!b.dataset.open));
}
const wrongOnly = document.getElementById("wrong-only");
if (wrongOnly) wrongOnly.onchange = () =>
  document.querySelectorAll("tr.right").forEach((r) => (r.hidden = wrongOnly.checked));
"""

# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        root = self.server.root  # type: ignore[attr-defined]
        try:
            if url.path == "/":
                body = index_page(root)
            elif url.path == "/log":
                body = log_page(root, query["path"])
            elif url.path == "/sample":
                body = sample_page(
                    root, query["path"], query["id"], int(query["epoch"])
                )
            else:
                return self.send_error(404)
        except Exception:
            body = page("Error", f"<pre>{esc(traceback.format_exc())}</pre>")
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        pass  # no line per request


def main() -> None:
    parser = argparse.ArgumentParser(description="Read transcripts in a browser.")
    parser.add_argument(
        "dir",
        nargs="?",
        default="logs",
        help="where to look for .eval logs (recursively)",
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.root = root = Path(args.dir).resolve()  # type: ignore[attr-defined]
    print(f"Serving logs under {root} at http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
