"""What every artifact type shares: primitives, and their two forms.

A primitive is an ordinary method on an artifact, marked with @primitive. It
exists in two forms without being written twice:

- Plain: a protocol calls it directly, e.g. `sample.codebase.read_file("x.py")`,
  to read files itself and paste excerpts into a prompt.
- Tool: `artifact.tools()` wraps every @primitive method as an Inspect tool that
  can be handed to a participant. Its name, description and argument docs come
  from the method's signature and docstring, so write the docstring for the
  model that will read it.

A primitive can be conditional, e.g. `@primitive(enabled_if="code_execution")`
is only a tool when the artifact's `code_execution` attribute is true.
Primitives can be async (e.g. anything using the sandbox).

Every artifact also has `dump_all()`: the whole thing as one string, for
full-context baselines. It raises ArtifactTooLarge instead of truncating: a
silently truncated dump makes the baseline meaningless.
"""

import functools
import inspect
from typing import Any, Callable

from inspect_ai.tool import Tool, ToolDef


class ArtifactTooLarge(Exception):
    """dump_all() won't fit in the context window. Deliberately not truncated."""


def primitive(
    method: Callable[..., Any] | None = None, *, enabled_if: str | None = None
):
    """Mark an artifact method as a primitive, so tools() exposes it to participants."""

    def mark(method: Callable[..., Any]) -> Callable[..., Any]:
        method._is_primitive = True
        method._enabled_if = enabled_if
        return method

    return mark(method) if method is not None else mark


class Artifact:
    """Base class for artifact types (codebase, trajectory, ...)."""

    def tools(self) -> list[Tool]:
        """Every @primitive method of this artifact, as an Inspect tool."""
        tools = []
        for name in dir(type(self)):
            method = getattr(type(self), name)
            if not getattr(method, "_is_primitive", False):
                continue
            if method._enabled_if and not getattr(self, method._enabled_if):
                continue
            tools.append(_as_tool(getattr(self, name)))
        return tools

    def dump_all(self, max_tokens: int) -> str:
        raise NotImplementedError


def _as_tool(bound_method: Callable[..., Any]) -> Tool:
    # Inspect tools are async; primitives are plain functions so protocols can
    # call them directly. The wrapper keeps the signature and docstring, which
    # is what Inspect reads to describe the tool to the model.
    @functools.wraps(bound_method)
    async def execute(*args: Any, **kwargs: Any) -> Any:
        result = bound_method(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    return ToolDef(execute, name=bound_method.__name__).as_tool()


def estimate_tokens(text: str) -> int:
    """Deliberately conservative (overestimates), since it gates dump_all()."""
    return len(text) // 3
