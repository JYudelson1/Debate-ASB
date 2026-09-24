"""Artifact types. A dataset says which artifacts a sample carries; protocols get them built.

Samples store artifacts as JSON-able specs in their metadata (Inspect logs every
sample), e.g. `{"codebase": {"root": "/path"}}`. `load_artifacts` turns specs
into objects. To add an artifact type: write the class, register it here.
"""

from debate_asb.artifacts.base import Artifact, ArtifactTooLarge, primitive
from debate_asb.artifacts.codebase import Codebase
from debate_asb.artifacts.trajectory import Trajectory

ARTIFACT_TYPES: dict[str, type[Artifact]] = {
    "codebase": Codebase,
    "trajectory": Trajectory,
}


def load_artifacts(specs: dict[str, dict]) -> dict[str, Artifact]:
    return {name: ARTIFACT_TYPES[name](**spec) for name, spec in specs.items()}


__all__ = [
    "Artifact",
    "ArtifactTooLarge",
    "Codebase",
    "Trajectory",
    "load_artifacts",
    "primitive",
]
