"""Repair examples must apply, restore the mechanism, and retain non-target bugs."""

import ast
import copy
import glob
import hashlib
import itertools
import json
import math
import random
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from conftest import needs_asb

from debate_asb.datasets.asb import CORE_SAMPLE_IDS, PROJECT_ROOT
from debate_asb.sabotage import read_registry, resolve_sabotage
from debate_asb.sabotage_fixes import (
    apply_text_edits,
    resolve_fixes,
    validate_fix_records,
)


def source(sid, path):
    entry = read_registry()["samples"][sid]
    return (PROJECT_ROOT / "data/asb" / entry["workspace"] / path).read_text()


def repaired(sid, path, alternative=0):
    entry = read_registry()["samples"][sid]
    change = next(
        c
        for c in entry["acceptable_fixes"][alternative]["changes"]
        if c["path"] == path
    )
    return apply_text_edits(source(sid, path), change)


def node(text, name):
    return next(n for n in ast.parse(text).body if getattr(n, "name", None) == name)


def function(text, name, **bindings) -> Any:
    """Execute only a selected function, with offline test doubles for its globals."""
    namespace = dict(bindings)
    code = "from __future__ import annotations\n" + ast.unparse(node(text, name))
    exec(compile(code, "<repair-invariant>", "exec"), namespace)
    return namespace[name]


def test_original_line_edits_support_insertions_and_deletions_and_check_results():
    text = "a\nb\nc\n"
    change = {
        "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "result_sha256": hashlib.sha256(b"first\na\nlast\n").hexdigest(),
        "edits": [
            {"start_line": 1, "end_line": 0, "before": "", "after": "first\n"},
            {"start_line": 2, "end_line": 3, "before": "b\nc\n", "after": "last\n"},
        ],
    }
    assert apply_text_edits(text, change) == "first\na\nlast\n"
    change["edits"][1]["after"] = "incomplete\n"
    with pytest.raises(ValueError, match="result hash"):
        apply_text_edits(text, change)
