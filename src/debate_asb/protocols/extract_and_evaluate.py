"""Extract-and-evaluate: DELIBERATE STUB (DESIGN.md §8). This is mentee work.

The idea: a cheap, fast model passes over the codebase and pulls out a
condensed representation for the expensive models, e.g. ~12 decision points
that look interesting or arbitrary (a hyperparameter, a data filter, an
unusual loss term). The expensive models then judge or debate those, one at a
time.

Why it might matter (DESIGN.md §7.1): ASB's honest codebases are full of benign
distractors (original-author bugs, odd but harmless choices), and they're a
major source of false positives. Debating decision points in sequence might
substantially reduce that problem. Unproven; worth testing. How to extract is
the research question, so nothing here is implemented.

Two forms, as with artifact primitives (DESIGN.md §5.5):

- Protocol-controlled: the protocol calls extract_decision_points() itself and
  decides what each participant sees.
- Participant-invoked: extraction_tool() gives a participant (e.g. a debater)
  a tool it can call when it wants.

Things to keep in mind when building it:

- Extraction is additive, not a replacement (DESIGN.md §6). A participant
  handed an extract still has the artifact tools unless the protocol's
  ToolAccess switches them off. If you want "judge sees only the extract",
  set ToolAccess(judge=False), and use judge_prompt("none") so the prompt
  doesn't promise tools.
- Wrap extraction in `stage("extraction", ...)` so it's saved and replayable:
  `-T replay=<log> -T reuse=extraction --epochs k` then runs the downstream
  protocol k times on the same extraction.
- Record the extractor's calls with protocol.generate() under its own role
  (e.g. "extractor") so its cost shows up separately in the runner's report.
- Return JSON-able data from the stage (e.g. dataclasses.asdict).
"""

from dataclasses import dataclass

from inspect_ai.tool import Tool

from debate_asb.artifacts import Codebase
from debate_asb.models import Participant


@dataclass
class DecisionPoint:
    location: str  # file path and line range, e.g. "train.py:120-134"
    description: str  # what the choice is
    why_notable: str  # why it looks interesting or arbitrary


async def extract_decision_points(
    codebase: Codebase, extractor: Participant, n: int = 12
) -> list[DecisionPoint]:
    """Have `extractor` (a cheap model) pull ~n notable decision points out of `codebase`."""
    raise NotImplementedError(
        "extract_and_evaluate is mentee work; see this module's docstring"
    )


def extraction_tool(codebase: Codebase, extractor: Participant) -> Tool:
    """The same extraction, as a tool a participant can call."""
    raise NotImplementedError(
        "extract_and_evaluate is mentee work; see this module's docstring"
    )
