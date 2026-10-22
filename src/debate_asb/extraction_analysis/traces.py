"""Explicit source presentations shared by comparison and cross-run reports."""

import json
import re

NUMBERED = re.compile(r"^\s*(\d+) {2}(.*)$")


def normalise(path: str, root: str) -> str:
    if path.startswith("/workspace/"):
        return path[len("/workspace/") :]
    if path.startswith(root.rstrip("/") + "/"):
        return path[len(root.rstrip("/")) + 1 :]
    return path.removeprefix("./")


def source_presentations(
    tool: dict, root: str | None = None
) -> list[tuple[str, list[tuple[int, str]]]]:
    """Explicit reads and trace_mechanism source blocks; no execution inference."""
    if tool.get("failed"):
        return []
    result = str(tool.get("result", ""))
    if tool["function"] == "read_file":
        path = tool.get("path") or tool.get("arguments", {}).get("path", "")
        blocks = [(normalise(path, root) if root is not None else path, result)]
    elif tool["function"] == "trace_mechanism":
        parts = re.split(r'^### SOURCE ("[^"\n]+") [^\n]+\n', result, flags=re.M)
        blocks = [
            (
                normalise(json.loads(parts[i]), root)
                if root is not None
                else json.loads(parts[i]),
                parts[i + 1],
            )
            for i in range(1, len(parts), 2)
        ]
    else:
        return []
    return [
        (
            path,
            [
                (int(m[1]), m[2])
                for line in text.splitlines()
                if (m := NUMBERED.match(line))
            ],
        )
        for path, text in blocks
    ]


def source_blocks(
    tool: dict, root: str | None = None
) -> list[tuple[str, dict[int, str]]]:
    """One text per line within each returned source block for coverage metrics."""
    return [(path, dict(lines)) for path, lines in source_presentations(tool, root)]
