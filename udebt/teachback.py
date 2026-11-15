"""Teach-back (Phase 3.3, `udebt teachback <unit>`): the developer explains a unit in their own
words and the model says what was right, what was missing and one misconception if any. One
call, recorded with `type: teachback` like a quiz answer.

Graded at the developer's current ladder level for the unit (Phase 3.4, owner): the level the
next quiz would ask (ladder.next_level), so level 1 for a developer with no evidence there, 3
for one who has passed 1 and 2. The explanation asked for grows with the level (purpose; then
the decision and what was not chosen; then what reversing it would change; then failure
behaviour; then how the code does it), and a pass moves the ladder like a quiz answer. At
levels 1-2 a miss (partial, wrong, unknown) shows the reference answer, as in the quiz."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import ladder
from .llm import LLM
from .prompts import TEACHBACK_SCHEMA, teachback_prompt

LEVEL = 2  # the level before Phase 3.4, kept for callers that name none
VERDICTS = ("correct", "partial", "wrong", "unknown")

# what an explanation at each level must cover, cumulatively
ASKS = {
    1: "what it is for and where it sits in the system",
    2: "what it is for, the decision behind it, and what was not chosen",
    3: "the decision behind it and what else in the system would have to change if it were "
    "reversed",
    4: "how it behaves when one of its parts fails, times out or gets bad input",
    5: "how its code does its job: the main path through it and why it is written that way",
}


def level_for(records_on_unit: list[dict[str, Any]]) -> int:
    """The level a teach-back on this unit is graded at: the developer's next ladder level
    (1 with no evidence)."""
    return ladder.next_level(records_on_unit)


def question(unit: dict[str, Any], level: int = LEVEL) -> str:
    return f"Explain {unit.get('name', unit['id'])} in your own words: {ASKS[ladder.check(level)]}."


def grade(
    root: Path,
    llm: LLM,
    unit: dict[str, Any],
    explanation: str,
    confidence: int = 2,
    orientation: str | None = None,
    max_turns: int = 40,
    *,
    level: int = LEVEL,
) -> dict[str, Any]:
    """The graded teach-back at `level`, shaped like a graded quiz answer (quiz.record takes
    it)."""
    level = ladder.check(level)
    res = llm.structured(
        teachback_prompt(unit, explanation, orientation, level), TEACHBACK_SCHEMA, cwd=root,
        read_tools=True, max_turns=max_turns,
    )  # fmt: skip
    verdict = res.get("verdict") if res.get("verdict") in VERDICTS else "wrong"
    right = str(res.get("right", "")).strip()
    missing = str(res.get("missing", "")).strip()
    mis = str(res.get("misconception", "")).strip()
    if mis.lower() in ("none", "none.", "n/a", "-"):
        mis = ""
    feedback = " ".join(x for x in (right, f"Missing: {missing}" if missing else "") if x)
    return {
        "unit_id": unit["id"],
        "level": level,
        "question": question(unit, level),
        "reference_answer": str(res.get("reference_answer", "")).strip()
        or "(graded against the code)",
        "evidence": list(unit.get("paths", [])),
        "answer": explanation,
        "confidence": confidence,
        "verdict": verdict,
        "feedback": feedback or "No feedback came back.",
        "right": right,
        "missing": missing,
        "misconception": mis,
    }
