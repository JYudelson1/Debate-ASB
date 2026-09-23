"""A codebase: a directory of files, navigated read-only, optionally with code execution.

The read-only primitives read the dataset's copy on the host. run_bash runs in
a Docker sandbox holding a copy of the codebase at /workspace (see
docker/ and task.protocol_task(code_execution=True)); it only exists as a
tool when the sample was set up with code execution.
"""

import re
from collections import Counter
from pathlib import Path

from inspect_ai.util import sandbox

from debate_asb.artifacts.base import Artifact, ArtifactTooLarge, estimate_tokens, primitive

# ASB ships a CLAUDE.md (instructions for Claude Code auditors) in each codebase.
# Participants get that context through their prompts instead, so it's hidden here.
HIDDEN_NAMES = {"CLAUDE.md", "__pycache__", ".git"}
MAX_LIST_ENTRIES = 300
MAX_READ_LINES = 300
MAX_LINE_CHARS = 2000
MAX_SEARCH_MATCHES = 100
MAX_SEARCH_FILE_BYTES = 10_000_000

# dump_all() includes code, docs and config in full; everything else (datasets,
# logs, results, lockfiles, binaries) is listed by name and size only.
DUMP_EXTENSIONS = {".py", ".md", ".yaml", ".yml", ".toml", ".cfg", ".sh", ".ipynb"}
DUMP_MAX_FILE_BYTES = 200_000

# Code execution (see docker/Dockerfile). The codebase copy at WORKSPACE is
# read-only for the participant user, so one participant can't change files
# another will run; participants write scratch files in their home directory.
WORKSPACE = "/workspace"
SANDBOX_USER = "auditor"
MAX_OUTPUT_CHARS = 20_000
COMMAND_TIMEOUT_SECONDS = 60  # every run_bash command is killed after this


class Codebase(Artifact):
    def __init__(self, root: str | Path, code_execution: bool = False):
        self.root = Path(root).resolve()
        self.code_execution = code_execution

    # --- primitives: plain methods, also exposed to participants as tools ---

    @primitive
    def list_files(self, directory: str = ".", recursive: bool = False) -> str:
        """List the contents of a directory in the codebase, with file sizes.

        Args:
            directory: Directory relative to the codebase root. Defaults to the root.
            recursive: List every file under the directory instead of one level.
        """
        target = self._resolve(directory)
        if recursive:
            lines = [self._describe(p) for p in self._files(target)]
        else:
            lines = []
            for p in sorted(target.iterdir()):
                if p.name in HIDDEN_NAMES:
                    continue
                if p.is_dir():
                    lines.append(f"{p.relative_to(self.root)}/  ({len(self._files(p))} files)")
                else:
                    lines.append(self._describe(p))
        if len(lines) > MAX_LIST_ENTRIES:
            extra = len(lines) - MAX_LIST_ENTRIES
            lines = lines[:MAX_LIST_ENTRIES]
            lines.append(f"... {extra} more files. List a subdirectory to see them.")
        return "\n".join(lines) or "(no files)"

    @primitive
    def read_file(self, path: str, start_line: int = 1, num_lines: int = 300) -> str:
        """Read lines from a text file in the codebase. Output lines are numbered.

        Args:
            path: File path relative to the codebase root.
            start_line: First line to read (1-indexed).
            num_lines: How many lines to read (at most 300 per call).
        """
        file = self._resolve(path)
        if _is_binary(file):
            return f"{path} is a binary file ({file.stat().st_size:,} bytes)."
        all_lines = file.read_text(errors="replace").splitlines()
        start = max(start_line, 1)
        end = min(start + min(num_lines, MAX_READ_LINES), len(all_lines) + 1)
        out = []
        for n in range(start, end):
            line = all_lines[n - 1]
            if len(line) > MAX_LINE_CHARS:
                line = line[:MAX_LINE_CHARS] + f" [... line truncated, {len(line):,} chars]"
            out.append(f"{n:>6}  {line}")
        out.append(f"[{path}: showed lines {start}-{end - 1} of {len(all_lines)}]")
        return "\n".join(out)

    @primitive
    def search(self, pattern: str, directory: str = ".", ignore_case: bool = False) -> str:
        """Search text files in the codebase for a regular expression (like grep -rn).

        Args:
            pattern: Python regular expression.
            directory: Directory to search, relative to the codebase root.
            ignore_case: Case-insensitive matching.
        """
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        matches = []
        for file in self._files(self._resolve(directory)):
            if file.stat().st_size > MAX_SEARCH_FILE_BYTES or _is_binary(file):
                continue
            for n, line in enumerate(file.read_text(errors="replace").splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{file.relative_to(self.root)}:{n}: {line[:300]}")
                    if len(matches) >= MAX_SEARCH_MATCHES:
                        matches.append("... stopped at 100 matches. Narrow the search.")
                        return "\n".join(matches)
        return "\n".join(matches) or "No matches."

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
        included = [f for f in first if f in included] + [f for f in included if f not in first]

        parts = [
            f"The codebase has {len(included) + len(excluded)} files. The {len(included)} "
            "code, documentation and configuration files are included in full below. "
            f"The other {len(excluded)} (data, logs, results, large or binary files) are "
            "listed by name at the end but not included."
        ]
        for file in included:
            parts.append(f"===== {file.relative_to(self.root)} =====\n{file.read_text(errors='replace')}")
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
        killed after 60 seconds. There is no network access and no GPU.

        Args:
            command: The bash command to run.
        """
        try:
            result = await sandbox().exec(
                ["bash", "-c", command], cwd=WORKSPACE, user=SANDBOX_USER, timeout=COMMAND_TIMEOUT_SECONDS
            )
        except TimeoutError:
            return f"Command killed after {COMMAND_TIMEOUT_SECONDS} seconds."
        output = result.stdout + (f"\n[stderr]\n{result.stderr}" if result.stderr else "")
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + f"\n[... output truncated, {len(output):,} chars]"
        return f"[exit code {result.returncode}]\n{output}"

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
        return f"{file.relative_to(self.root)}  ({file.stat().st_size:,} bytes)"

    def _summarize(self, files: list[Path]) -> str:
        if len(files) <= MAX_LIST_ENTRIES:
            return "\n".join(self._describe(f) for f in files) or "(none)"
        by_dir = Counter(str(f.parent.relative_to(self.root)) for f in files)
        return "\n".join(f"{d}/  ({n} files)" for d, n in sorted(by_dir.items()))


def _is_binary(path: Path) -> bool:
    with open(path, "rb") as f:
        return b"\0" in f.read(8192)
