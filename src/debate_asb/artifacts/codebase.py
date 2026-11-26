"""A codebase: a directory of files, navigated read-only, optionally with code execution.

The read-only primitives read the dataset's copy on the host. run_bash runs in
a Docker sandbox holding a copy of the codebase at /workspace (see
docker/ and task.protocol_task(code_execution=True)); it only exists as a
tool when the sample was set up with code execution.
"""

import re
import time
from collections import Counter
from pathlib import Path

import regex
from inspect_ai.tool import ToolError
from inspect_ai.util import sandbox

from debate_asb.artifacts.base import (
    Artifact,
    ArtifactTooLarge,
    estimate_tokens,
    primitive,
)

# ASB ships a CLAUDE.md (instructions for Claude Code auditors) in each codebase.
# Participants get that context through their prompts instead, so it's hidden here.
# PATCH_MARKER is the stamp our loader writes into a patched copy
# (datasets/asb.py); it would tell participants the codebase was modified.
PATCH_MARKER = ".asb_patch"
HIDDEN_NAMES = {"CLAUDE.md", "__pycache__", ".git", PATCH_MARKER}

# Inspect cuts any tool output over max_tool_output by dropping its MIDDLE, with
# no marker, so a read's footer would claim lines it never showed. Every tool
# here therefore fits its own output under TOOL_OUTPUT_BUDGET (whole lines,
# saying what it left out), and protocols pass TOOL_OUTPUT_BYTES to
# execute_tools as max_output, so Inspect's cut is only a backstop.
TOOL_OUTPUT_BYTES = 64 * 1024
TOOL_OUTPUT_BUDGET = TOOL_OUTPUT_BYTES - 1024  # room for the footer
MAX_LIST_ENTRIES = 1200
MAX_READ_LINES = 1200
# A line is shown in full unless it alone would overflow a call's output; such a
# line (e.g. a whole JSON results file on one line) is cut to this many bytes.
MAX_LINE_BYTES = TOOL_OUTPUT_BUDGET - 1024
SEARCH_LINE_CHARS = (
    300  # each search match shows this much of its line, around the match
)
MAX_SEARCH_MATCHES = 400
MAX_SEARCH_FILE_BYTES = 10_000_000
SEARCH_TIMEOUT_SECONDS = 10.0

# dump_all() includes code, docs and config in full; everything else (datasets,
# logs, results, lockfiles, binaries) is listed by name and size only.
DUMP_EXTENSIONS = {".py", ".md", ".yaml", ".yml", ".toml", ".cfg", ".sh", ".ipynb"}
DUMP_MAX_FILE_BYTES = 200_000

# Code execution (see docker/Dockerfile). The codebase copy at WORKSPACE is
# read-only for the participant user, so one participant can't change files
# another will run; participants write scratch files in their home directory.
WORKSPACE = "/workspace"
SANDBOX_USER = "auditor"
COMMAND_TIMEOUT_SECONDS = 300  # every run_bash command is killed after this


class Codebase(Artifact):
    def __init__(self, root: str | Path, code_execution: bool = False):
        self.root = Path(root).resolve()
        self.code_execution = code_execution

    # --- primitives: plain methods, also exposed to participants as tools ---

    @primitive
    def list_files(self, directory: str = ".", recursive: bool = False) -> str:
        """List the contents of a directory in the codebase, with file sizes.

        Shows at most 1200 entries and about 63 KB per call, and says how many
        it left out.

        Args:
            directory: Directory relative to the codebase root. Defaults to the root.
            recursive: List every file under the directory instead of one level.
        """
        target = self._resolve(directory)
        if not target.is_dir():
            # iterdir() would raise NotADirectoryError, which fails the sample.
            raise ToolError(f"{directory} is a file, not a directory")
        if recursive:
            lines = [self._describe(p) for p in self._files(target)]
        else:
            lines = []
            for p in sorted(target.iterdir()):
                if p.name in HIDDEN_NAMES:
                    continue
                if p.is_dir():
                    lines.append(
                        f"{p.relative_to(self.root).as_posix()}/  ({len(self._files(p))} files)"
                    )
                else:
                    lines.append(self._describe(p))
        shown = _fit(lines[:MAX_LIST_ENTRIES])
        if len(shown) < len(lines):
            shown.append(
                f"... {len(lines) - len(shown)} more entries not shown. "
                "List a subdirectory to see them."
            )
        return "\n".join(shown) or "(no files)"

    @primitive
    def read_file(self, path: str, start_line: int = 1, num_lines: int = 1200) -> str:
        """Read lines from a text file in the codebase. Output lines are numbered.

        Shows at most 1200 lines and about 63 KB per call. It stops early at a
        whole line if the output would be longer; the last line of the output
        says which lines were shown and where to continue. Lines are shown in
        full, except a single line over about 62 KB, which is cut.

        Args:
            path: File path relative to the codebase root.
            start_line: First line to read (1-indexed).
            num_lines: How many lines to read (at most 1200 per call).
        """
        file = self._resolve(path)
        if file.is_dir():
            # Opening a directory raises an OS error whose message Inspect shows the
            # model, and on Windows it contains the host path (with the variant name).
            raise ToolError(f"{path} is a directory, not a file")
        if _is_binary(file):
            return f"{path} is a binary file ({file.stat().st_size:,} bytes)."
        all_lines = file.read_text(errors="replace").splitlines()
        start = max(start_line, 1)
        end = min(start + min(num_lines, MAX_READ_LINES), len(all_lines) + 1)
        out = []
        for n in range(start, end):
            line = all_lines[n - 1]
            out.append(f"{n:>6}  {_cut_line(line)}")
        out = _fit(out)
        last = start + len(out) - 1
        footer = f"[{path}: showed lines {start}-{last} of {len(all_lines)}"
        if last < end - 1:
            footer += f"; output limit reached, continue with start_line={last + 1}"
        return "\n".join([*out, footer + "]"])

    @primitive
    def search(
        self, pattern: str, directory: str = ".", ignore_case: bool = False
    ) -> str:
        """Search text files in the codebase for a regular expression (like grep -rn).

        Args:
            pattern: Python regular expression.
            directory: Directory to search, relative to the codebase root.
            ignore_case: Case-insensitive matching.

        Shows at most 400 matches and about 63 KB per call, with up to 300
        characters of each matching line around the match, and says when it
        stopped early. Files over 10 MB are not searched; the output lists them.
        """
        try:
            # Preserve the advertised Python-re syntax/error contract; VERSION0
            # supplies compatible matching with an enforceable CPU timeout.
            re.compile(pattern, re.IGNORECASE if ignore_case else 0)
            compiled = regex.compile(
                pattern, regex.VERSION0 | (regex.IGNORECASE if ignore_case else 0)
            )
        except re.error as error:
            return f"Invalid regular expression: {error}"
        matches = []
        deadline = time.monotonic() + SEARCH_TIMEOUT_SECONDS
        # A file target is searched directly; rglob would otherwise find no children.
        target = self._resolve(directory)
        files = [target] if target.is_file() else self._files(target)
        too_large = [
            f
            for f in files
            if f.stat().st_size > MAX_SEARCH_FILE_BYTES and not _is_binary(f)
        ]
        note = self._not_searched_note(too_large)
        for file in files:
            if file.stat().st_size > MAX_SEARCH_FILE_BYTES or _is_binary(file):
                continue
            for n, line in enumerate(file.read_text(errors="replace").splitlines(), 1):
                remaining = deadline - time.monotonic()
                try:
                    if remaining <= 0:
                        raise TimeoutError
                    found = compiled.search(line, timeout=remaining)
                except TimeoutError:
                    return (
                        f"Search timed out after {SEARCH_TIMEOUT_SECONDS:g} seconds. "
                        "Results are incomplete; narrow the pattern or directory.\n"
                        + "\n".join(matches)
                        + note
                    )
                if found:
                    matches.append(
                        f"{file.relative_to(self.root).as_posix()}:{n}: "
                        + _around(line, found.start())
                    )
                    if len(_fit(matches)) < len(matches):
                        matches.pop()
                        matches.append(
                            f"... stopped after {len(matches)} matches (output limit). "
                            "Narrow the search."
                        )
                        return "\n".join(matches) + note
                    if len(matches) >= MAX_SEARCH_MATCHES:
                        matches.append(
                            f"... stopped at {MAX_SEARCH_MATCHES} matches. Narrow the search."
                        )
                        return "\n".join(matches) + note
        return ("\n".join(matches) or "No matches.") + note

    def _not_searched_note(self, files: list[Path]) -> str:
        """Which files a search skipped for size, so 'No matches' isn't read as 'not there'."""
        if not files:
            return ""
        shown = ", ".join(
            f"{f.relative_to(self.root).as_posix()} ({f.stat().st_size / 1e6:.0f} MB)"
            for f in files[:20]
        )
        more = f" and {len(files) - 20} more" if len(files) > 20 else ""
        how = " Use run_bash (e.g. grep) to search them." if self.code_execution else ""
        return (
            f"\n[Not searched, over {MAX_SEARCH_FILE_BYTES // 1_000_000} MB: "
            f"{shown}{more}.{how}]"
        )

    # --- plain only (not tools) ---

    def dump_all(self, max_tokens: int) -> str:
        """Code, docs and config in full; other files listed by name and size.

        Raises ArtifactTooLarge if the result is estimated to exceed max_tokens.
        """
        included, excluded = [], []
        for file in self._files(self.root):
            dumpable = (
                file.suffix in DUMP_EXTENSIONS
                and file.stat().st_size <= DUMP_MAX_FILE_BYTES
                and not _is_binary(file)
            )
            (included if dumpable else excluded).append(file)
        # Paper and README first: they're what the rest is read against.
        first = [self.root / "PAPER.md", self.root / "README.md"]
        included = [f for f in first if f in included] + [
            f for f in included if f not in first
        ]

        parts = [
            f"The codebase has {len(included) + len(excluded)} files. The {len(included)} "
            "code, documentation and configuration files are included in full below. "
            f"The other {len(excluded)} (data, logs, results, large or binary files) are "
            "listed by name at the end but not included."
        ]
        for file in included:
            parts.append(
                f"===== {file.relative_to(self.root).as_posix()} =====\n{file.read_text(errors='replace')}"
            )
        parts.append("===== Files not included =====\n" + self._summarize(excluded))
        dump = "\n\n".join(parts)

        tokens = estimate_tokens(dump)
        if tokens > max_tokens:
            raise ArtifactTooLarge(
                f"{self.root.name}: dump is ~{tokens:,} tokens, over the {max_tokens:,} limit"
            )
        return dump

    # --- code execution: a tool only when the sample was set up with a sandbox ---

    @primitive(enabled_if="code_execution")
    async def run_bash(self, command: str) -> str:
        """Run a bash command in a sandboxed copy of the codebase, and return its output.

        The command runs in /workspace, which holds the codebase and is read-only.
        To modify code, copy it to your home directory (~) first. Each command is
        killed after 300 seconds. There is no network access and no GPU. Output
        over about 63 KB keeps its start and end, with a note saying how much of
        the middle was left out.

        Args:
            command: The bash command to run.
        """
        try:
            result = await sandbox().exec(
                ["bash", "-c", command],
                cwd=WORKSPACE,
                user=SANDBOX_USER,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return f"Command killed after {COMMAND_TIMEOUT_SECONDS} seconds."
        output = result.stdout + (
            f"\n[stderr]\n{result.stderr}" if result.stderr else ""
        )
        return f"[exit code {result.returncode}]\n{_head_and_tail(output)}"

    # --- helpers ---

    def _files(self, directory: Path) -> list[Path]:
        return sorted(
            p
            for p in directory.rglob("*")
            if p.is_file() and not HIDDEN_NAMES & set(p.relative_to(self.root).parts)
        )

    def _resolve(self, path: str) -> Path:
        """Resolve a participant-supplied path, refusing anything outside the codebase."""
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"{path} is outside the codebase")
        if HIDDEN_NAMES & set(resolved.relative_to(self.root).parts):
            raise FileNotFoundError(path)
        if not resolved.exists():
            raise FileNotFoundError(path)
        return resolved

    def _describe(self, file: Path) -> str:
        return (
            f"{file.relative_to(self.root).as_posix()}  ({file.stat().st_size:,} bytes)"
        )

    def _summarize(self, files: list[Path]) -> str:
        if len(files) <= MAX_LIST_ENTRIES:
            return "\n".join(self._describe(f) for f in files) or "(none)"
        by_dir = Counter(f.parent.relative_to(self.root).as_posix() for f in files)
        return "\n".join(f"{d}/  ({n} files)" for d, n in sorted(by_dir.items()))


def _bytes(text: str) -> int:
    return len(text.encode("utf-8", errors="replace"))


def _fit(lines: list[str], budget: int = TOOL_OUTPUT_BUDGET) -> list[str]:
    """The longest prefix of `lines` whose joined UTF-8 size is within budget."""
    used = 0
    for i, line in enumerate(lines):
        used += _bytes(line) + 1  # + newline
        if used > budget:
            return lines[:i]
    return lines


def _head_and_tail(text: str, budget: int = TOOL_OUTPUT_BUDGET) -> str:
    """Text within budget: its start and end, with a marker for the dropped middle."""
    data = text.encode("utf-8", errors="replace")
    if len(data) <= budget:
        return text
    half = budget // 2
    head = data[:half].decode("utf-8", errors="ignore")
    tail = data[-half:].decode("utf-8", errors="ignore")
    return f"{head}\n[... {len(data) - 2 * half:,} bytes of output omitted ...]\n{tail}"


def _cut_line(line: str) -> str:
    """The line, or its start with a marker if it alone would overflow a tool call."""
    data = line.encode("utf-8")
    if len(data) <= MAX_LINE_BYTES:
        return line
    start = data[:MAX_LINE_BYTES].decode("utf-8", errors="ignore")
    # Same marker as before (extraction_analysis parses it).
    return f"{start} [... line truncated, {len(line):,} chars]"


def _around(line: str, at: int) -> str:
    """Up to SEARCH_LINE_CHARS of the line, starting a little before position `at`."""
    if len(line) <= SEARCH_LINE_CHARS:
        return line
    start = max(0, min(at - 100, len(line) - SEARCH_LINE_CHARS))
    end = start + SEARCH_LINE_CHARS
    return (
        ("..." if start else "") + line[start:end] + ("..." if end < len(line) else "")
    )


def _is_binary(path: Path) -> bool:
    with open(path, "rb") as f:
        return b"\0" in f.read(8192)
