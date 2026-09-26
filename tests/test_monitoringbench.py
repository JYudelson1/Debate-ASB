import pytest

from debate_asb.datasets.monitoringbench import (
    PILOT_V1_SAMPLE_IDS,
    _select_rows,
)


def test_pilot_v1_is_complete_unique_and_ordered():
    assert len(PILOT_V1_SAMPLE_IDS) == 12
    assert len(set(PILOT_V1_SAMPLE_IDS)) == 12

    rows = [
        {"sample_uuid": sample_id, "eval_log_filename": f"{sample_id}.eval"}
        for sample_id in reversed(PILOT_V1_SAMPLE_IDS)
    ]
    selected = _select_rows(rows, None, None, None, 0, "pilot_v1")

    assert [row["sample_uuid"] for row in selected] == list(PILOT_V1_SAMPLE_IDS)


def test_preset_rejects_filters_and_missing_ids():
    rows = [
        {"sample_uuid": sample_id, "eval_log_filename": f"{sample_id}.eval"}
        for sample_id in PILOT_V1_SAMPLE_IDS
    ]

    with pytest.raises(ValueError, match="cannot be combined"):
        _select_rows(rows, 1, None, None, 0, "pilot_v1")
    with pytest.raises(ValueError, match="absent from the index"):
        _select_rows(rows[:-1], None, None, None, 0, "pilot_v1")
