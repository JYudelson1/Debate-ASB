"""Shared input serialization and content fingerprints for offline reports."""

import hashlib
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(value: Any) -> str:
    """Use the analysis collector's exact JSON serialization, including ASCII."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
