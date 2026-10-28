"""Retry billing and control identity invariants for the extraction viewer."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_extraction_run_viewer.py"
spec = importlib.util.spec_from_file_location("extraction_run_viewer", SCRIPT)
assert spec and spec.loader
viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viewer)


def test_retry_billing_deduplicates_copied_samples_but_keeps_new_attempts(
    tmp_path, monkeypatch
):
    paths = [tmp_path / "first.eval", tmp_path / "retry.eval"]
    for path in paths:
        path.write_text(path.name)
    original = SimpleNamespace(event="model", uuid="paid-1")
    new = SimpleNamespace(event="model", uuid="paid-2")
    logs = {
        str(paths[0]): SimpleNamespace(
            status="success",
            samples=[
                SimpleNamespace(id="control", epoch=1, events=[original]),
            ],
        ),
        str(paths[1]): SimpleNamespace(
            status="success",
            samples=[
                SimpleNamespace(id="control", epoch=1, events=[original, new]),
            ],
        ),
    }
    monkeypatch.setattr(viewer, "read_eval_log", lambda path: logs[path])

    def spend(events):
        assert [e.uuid for e in events] == ["paid-1", "paid-2"]
        return SimpleNamespace(
            usd=0.7, api_calls=2, unlogged_calls=0, unpinned_calls=0, served_by={}
        )

    monkeypatch.setattr(viewer, "sample_spend", spend)
    result = viewer.attempt_spend(paths)
    assert result["api_calls"] == 2
    assert result["billed_total"] == 0.7
    assert len(result["logs"]) == 2
