"""Render model-authored markdown to HTML that is safe to inject into the report."""

from __future__ import annotations

from markdown_it import MarkdownIt

from debate_asb.viewer.schema import MarkdownText

# Raw HTML stays disabled: the report injects this output with innerHTML, and
# model text is untrusted. markdown-it also rejects javascript:/data: links.
_PARSER = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(
    ["table", "strikethrough"]
)


def render_markdown(source: str) -> str:
    return _PARSER.render(source)


def markdown_text(source: str) -> MarkdownText:
    return MarkdownText(source=source, html=render_markdown(source))
