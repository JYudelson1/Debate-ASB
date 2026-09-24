"""Datasets: functions returning Inspect Samples whose metadata says what they carry.

To add a dataset, write a function returning samples with:
- target: "sabotaged" or "honest"
- metadata["artifacts"]: artifact specs, e.g. {"codebase": {"root": "/path"}}
- metadata["setting"]: which prompt folder fits (see prompts/), e.g. "asb"
and add it to load_samples below. Protocols need no changes.
"""

from typing import Literal

from inspect_ai.dataset import Sample

from debate_asb.datasets.asb import ASB_ROOT, CORE_SAMPLE_IDS, asb_samples
from debate_asb.datasets.monitoringbench import monitoringbench_samples

Dataset = Literal["asb", "monitoringbench"]


def load_samples(
    dataset: Dataset, sample_ids: str | list[str] | None = None
) -> list[Sample]:
    """The samples every protocol task runs on. `-T sample_ids=a,b` arrives as a list."""
    ids = sample_ids.split(",") if isinstance(sample_ids, str) else sample_ids
    if dataset == "asb":
        return asb_samples(ids or CORE_SAMPLE_IDS)
    if dataset == "monitoringbench":
        return monitoringbench_samples(ids)
    raise ValueError(f"Unknown dataset {dataset!r}")


__all__ = [
    "ASB_ROOT",
    "CORE_SAMPLE_IDS",
    "asb_samples",
    "load_samples",
    "monitoringbench_samples",
]
