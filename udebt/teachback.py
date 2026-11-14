"""Teach-back (Phase 3.3, `udebt teachback <unit>`): the developer explains a unit in their own
words and the model says what was right, what was missing and one misconception if any. One
call. Graded as level 2 evidence (decision and alternatives), recorded with `type:
teachback`, so a good explanation passes level 2 like a quiz answer would; at level 2 a miss
shows the reference answer, as in the quiz."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .llm import LLM
from .prompts import TEACHBACK_SCHEMA, teachback_prompt

LEVEL = 2
VERDICTS = ("correct", "partial", "wrong", "unknown")


def question(unit: dict[str, Any]) -> str:
    return (
        f"Explain {unit.get('name', unit['id'])} in your own words: what it is for, the "
        "decision behind it, and what was not chosen."
    )


def grade(
    root: Path,
    llm: LLM,
    unit: dict[str, Any],
    explanation: str,
    confidence: int = 2,
    orientation: str | None = None,
    max_turns: int = 40,
) -> dict[str, Any]:
    """The graded teach-back, shaped like a graded quiz answer (quiz.record takes it)."""
    res = llm.structured(
        teachback_prompt(unit, explanation, orientation), TEACHBACK_SCHEMA, cwd=root,
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
        "level": LEVEL,
        "question": question(unit),
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
