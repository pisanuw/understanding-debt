"""Guided walkthrough of one unit, ending in a recorded check question."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .llm import LLM
from .prompts import dig_prompt


def walkthrough(root: Path, llm: LLM, unit: dict[str, Any]) -> tuple[str, str | None]:
    """Return (markdown body, check question or None)."""
    text = llm.text(dig_prompt(unit), cwd=root, read_tools=True)
    m = re.search(r"^CHECK QUESTION:\s*(.+)$", text, flags=re.M)
    question = m.group(1).strip() if m else None
    body = text[: m.start()].rstrip() if m else text.rstrip()
    return body, question
