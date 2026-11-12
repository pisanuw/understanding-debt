"""Guided walkthrough of one unit, ending in a recorded check question.

The walkthrough's first section is the unit's orientation (orientation.py, written once and
cached in map.json); the model writes the rest, and the check question is asked at the
developer's ladder level for the unit (ladder.next_level)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .llm import LLM
from .prompts import dig_prompt


def walkthrough(
    root: Path, llm: LLM, unit: dict[str, Any], level: int = 1, orientation: str | None = None
) -> tuple[str, str | None]:
    """Return (markdown body after the orientation, check question or None)."""
    text = llm.text(dig_prompt(unit, level, orientation), cwd=root, read_tools=True)
    m = re.search(r"^CHECK QUESTION:\s*(.+)$", text, flags=re.M)
    question = m.group(1).strip() if m else None
    body = text[: m.start()].rstrip() if m else text.rstrip()
    return body, question
