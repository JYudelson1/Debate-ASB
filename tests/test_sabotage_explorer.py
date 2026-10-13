import json
import re

from debate_asb.sabotage import read_registry
from debate_asb.sabotage_explorer import comparison_hunks, write_explorer


def test_source_comparison_shows_actual_changed_readout_and_local_context():
    entry = read_registry()["samples"]["lazy_alpha"]
    comparison = next(c for c in entry["comparisons"] if c["path"] == "arch/basics.py")
    hunks = comparison_hunks(comparison["unified_diff"], 56, 62)
    old = "\n".join(r["text"] for h in hunks for r in h["honest"])
    new = "\n".join(r["text"] for h in hunks for r in h["sabotaged"])
    assert "(W.t() / h) + B" in old and "(W.t() / h ** 0.5) + B" in new
    entry = read_registry()["samples"]["negative_mute"]
    comparison = next(
        c for c in entry["comparisons"] if c["path"] == "qk_circuit_analysis.py"
    )
    hunks = comparison_hunks(comparison["unified_diff"], 179, 196)
    assert hunks and all(
        176 <= r["opposite_line"] <= 199 for h in hunks for r in h["honest"]
    )


def test_explorer_is_portable_and_preserves_uncertainty(tmp_path):
    output = write_explorer(tmp_path / "index.html")
    html = output.read_text()
    assert "https://" not in html.split('<script id="explorer-data"')[0]
    assert "EXPLORER_DATA" not in html and "/* EXPLORER_JS */" not in html
    match = re.search(
        r'<script id="explorer-data" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    assert match is not None
    match = re.search(
        r'<script id="explorer-data" type="application/json">(.*?)</script>', html, re.S
    )
    assert match is not None
    data = json.loads(match[1])
    samples = {s["id"]: s for s in data["samples"]}
    assert len(samples) == 76
    assert samples["awareness_lens"]["status"] == "partial"
    assert samples["lazy_alpha_persuasion"]["status"] == "pending_review"
    assert "hardcoded_token_id_13" in {
        e["id"] for e in samples["awareness_lens"]["known_issues"]
    }
    assert 'aria-label="Codebases"' in html
