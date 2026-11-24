"""The simple calibration variant drops every credence anchor but the base rate."""

import re

from debate_asb.prompts import SETTINGS

ANCHOR = re.compile(r"\d+\s*%")


def test_simple_calibration_keeps_only_the_base_rate_and_scale():
    asb = SETTINGS["asb"].judge("tools_execute")
    simple = SETTINGS["asb"].judge("tools_execute", calibration="simple")
    assert len(ANCHOR.findall(asb)) > 10  # ASB's bands are there by default
    assert ANCHOR.findall(simple) == ["50%", "0%", "100%"]
    assert "Sabotage Credence: <0-100>%" in simple  # the answer format is unchanged
