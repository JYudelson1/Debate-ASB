import json

import pytest

from debate_asb import materiality as m


@pytest.fixture
def study(tmp_path):
    bundle = {"number": 1, "observation": "Frozen", "excerpts": []}
    jobs = {
        f"{kind}__bundle": dict(protocol=kind, bundle_key="bundle", bundle=bundle)
        for kind in m.KINDS
    }
    m.write_json(tmp_path / "inputs.json", jobs)
    manifest = dict(
        selected=[dict(key="bundle", baseline_worker_credence=50)],
        inputs_sha256=m.file_sha(tmp_path / "inputs.json"),
    )
    m.write_json(tmp_path / "study.json", manifest)
    for key, job in jobs.items():
        for repetition in range(1, 4):
            m.write_json(
                tmp_path / "results" / f"{key}__r{repetition}.json",
                dict(
                    protocol=job["protocol"],
                    bundle_key="bundle",
                    repetition=repetition,
                    input_sha256=m.digest(job),
                    bundle_sha256=m.digest(bundle),
                    credence=50,
                    materiality={},
                ),
            )
    return tmp_path


def test_unrelated_results_cannot_complete_report(study):
    assert m.report(study)["complete"]
    for index, path in enumerate((study / "results").glob("*.json")):
        path.rename(path.with_name(f"unrelated-{index}.json"))
    result = m.report(study)
    assert not result["complete"] and result["completed"] == 0
    assert all(row["mean"] is None for row in result["rows"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("protocol", "other"),
        ("bundle_key", "other"),
        ("repetition", True),
        ("input_sha256", "stale"),
        ("bundle_sha256", "stale"),
        ("credence", float("nan")),
        ("credence", True),
        ("credence", 101),
    ],
)
def test_report_and_complete_resume_reject_result_drift(
    study, monkeypatch, field, value
):
    path = study / "results/worker__bundle__r1.json"
    record = json.loads(path.read_text())
    record[field] = value
    path.write_text(json.dumps(record))

    def unexpected(*args, **kwargs):
        pytest.fail("Invalid results must fail before dataset, Docker, or model work")

    monkeypatch.setattr(m, "validate_dataset", unexpected)
    monkeypatch.setattr(m.subprocess, "check_output", unexpected)
    monkeypatch.setattr(m, "eval", unexpected)
    for action in (m.report, m.run):
        with pytest.raises(ValueError, match="Completed result drift"):
            action(study)
