"""The question ladder (Phase 3.2): a quiz starts at what a unit is for and descends into
implementation detail only once the developer has shown the levels above.

    level  asks                                            identifiers in the question
    1      purpose and placement: what the unit is for,    none: components by role
           what depends on it, what it depends on
    2      decision and alternatives: what was chosen,     none
           what was rejected, what constraint forced it
    3      consequence across units: what else changes     none
           if the decision were reversed or replaced
    4      behaviour under failure: module-level edge      file or component names,
           cases and failure modes                         no code
    5      implementation: code paths, specific functions  any

Levels 1-3 are architectural understanding and drive the score; 4 and 5 are optional depth
(scoring.ladder_verified). A developer's place on a unit's ladder is derived from their
records, never kept apart: every record carries `level`.

A *sitting* is the answers one quiz run gave on a unit at one level (usually one answer;
`--per-unit N` gives N). They share a timestamp (quiz.record writes one per run). A level is
*passed* when its latest sitting is all correct. Then:

- depth = the highest level n such that every level 1..n is passed (0: none yet);
- the next quiz asks at depth + 1 (at most 5): every developer starts each unit at level 1,
  a correct answer at n unlocks n + 1, partial, wrong or unknown repeats n;
- except that a confidently wrong answer at n >= 2 in the latest sitting drops the next
  question to n - 1: a misconception about a decision or a consequence says the picture of
  the unit's role may be wrong too, so that is checked again first.

Records from before the ladder carry no level and count as level 5 (the implementation
questions the old prompt produced); answers at a forced level (`quiz --level N`) carry the
level they were asked at. Both count in the score by their level, and since the ladder
needs levels 1..n passed in turn, neither moves anyone past a level they have not shown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

TOP = 5
ARCHITECTURAL = (1, 2, 3)
DEPTH = (4, 5)
LEGACY = 5  # the level of a record written before the ladder


@dataclass(frozen=True)
class Level:
    n: int
    name: str
    asks: str
    names: str  # what a question at this level may name
    example: str


LEVELS: dict[int, Level] = {
    1: Level(
        1,
        "purpose and placement",
        "what the unit is for, what depends on it and what it depends on",
        "nothing from the code: components by their role, in plain language",
        "What is <the part that does X> for, and which other parts of the system would stop "
        "working without it?",
    ),
    2: Level(
        2,
        "decision and alternatives",
        "what was chosen, what was rejected and what constraint forced the choice",
        "nothing from the code",
        "<X> was chosen over <Y>. What constraint made that the better choice here?",
    ),
    3: Level(
        3,
        "consequence across units",
        "what else in the system would have to change if the decision were reversed or "
        "replaced by an alternative",
        "nothing from the code",
        "If <the decision> were replaced by <the alternative>, which other parts of the "
        "system would have to change, and how?",
    ),
    4: Level(
        4,
        "behaviour under failure",
        "module-level edge cases and failure modes: what happens when a part fails, times "
        "out, gets empty input or runs twice",
        "a file or a component, but no code (no function, class or variable names)",
        "What happens when <a component> fails halfway through <an operation>, and what does "
        "the user see?",
    ),
    5: Level(
        5,
        "implementation",
        "code paths and specific functions: what a given piece of code does and why",
        "anything: files, functions, variables, code",
        "If you removed <a specific call or handler>, what would happen when <an event>?",
    ),
}


def label(n: int) -> str:
    """ "level 1 of 5: purpose and placement"."""
    return f"level {n} of {TOP}: {LEVELS[n].name}"


def check(n: int) -> int:
    if n not in LEVELS:
        raise SystemExit(f"A level is 1 to {TOP}, not {n}.")
    return n


def level_of(record: dict[str, Any]) -> int:
    """The level a record was asked at; a record from before the ladder is level 5."""
    try:
        n = int(record.get("level", LEGACY))
    except (TypeError, ValueError):
        return LEGACY
    return min(TOP, max(1, n))


def architectural(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The answers at levels 1-3."""
    return [r for r in records if "level" in r and level_of(r) in ARCHITECTURAL]


def _ordered(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda r: r.get("ts", ""))  # stable: file order within a ts


def sittings(records: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Level -> its latest sitting: the answers at that level sharing the latest timestamp
    among them (one quiz run's answers on the unit)."""
    by_level: dict[int, list[dict[str, Any]]] = {}
    for r in _ordered(records):
        n = level_of(r)
        cur = by_level.get(n)
        if cur and cur[-1].get("ts") == r.get("ts"):
            cur.append(r)
        else:
            by_level[n] = [r]
    return by_level


def passed(sitting: list[dict[str, Any]]) -> bool:
    return bool(sitting) and all(r.get("verdict") == "correct" for r in sitting)


def _confidently_wrong(r: dict[str, Any]) -> bool:
    return r.get("verdict") == "wrong" and int(r.get("confidence", 1)) >= 3


def depth(records: list[dict[str, Any]]) -> int | None:
    """The highest level n with every level 1..n passed; None without any record."""
    if not records:
        return None
    latest = sittings(records)
    n = 0
    while n < TOP and passed(latest.get(n + 1, [])):
        n += 1
    return n


def next_level(records: list[dict[str, Any]]) -> int:
    """The level the next question on this unit is asked at (the rules above)."""
    if not records:
        return 1
    nxt = min(TOP, (depth(records) or 0) + 1)
    ordered = _ordered(records)
    last = ordered[-1]
    n = level_of(last)
    sitting = [r for r in ordered if r.get("ts") == last.get("ts") and level_of(r) == n]
    if n >= 2 and any(_confidently_wrong(r) for r in sitting):
        return min(nxt, n - 1)
    return nxt


# -- the identifier check -----------------------------------------------------------------

_EXT = r"(?:py|pyi|js|jsx|ts|tsx|mjs|cjs|json|ya?ml|toml|md|sql|html|css|sh|rs|go|java|cpp|h)"
_CODE_DIRS = (
    r"(?:src|lib|app|api|backend|frontend|tests?|scripts|pkg|cmd|internal|components|utils)"
)
_PATTERNS = [
    re.compile(r"`[^`]+`"),  # code spans
    re.compile(r"\b[A-Za-z_][\w.]*\(\)"),  # calls
    re.compile(rf"\b[\w-]+(?:\.[\w-]+)*\.{_EXT}\b"),  # file names
    # paths: two slashes, a trailing slash, a code-like segment or a usual code directory
    re.compile(r"\b[\w.-]+/[\w.-]+/[\w./-]*|\b[\w.-]+/(?=\s|$)"),
    re.compile(r"\b[\w-]*[_.\d][\w.-]*/[\w.-]+\b|\b[\w.-]+/[\w-]*[_.\d][\w.-]*\b"),
    re.compile(rf"\b{_CODE_DIRS}/[\w.-]+\b|\b[\w.-]+/{_CODE_DIRS}\b"),
    re.compile(r"\b[a-z]+_[a-z0-9_]+\b|(?<![\w])_[a-z]\w*\b"),  # snake_case, _private
    re.compile(r"\b[a-z_]\w*\.[a-z_]\w*\b"),  # dotted names
    re.compile(r"\b[a-z]+[A-Z]\w*\b"),  # camelCase
    re.compile(r"\b[A-Z][a-z]+[A-Z][a-z]\w*\b"),  # PascalCase class names
    re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b"),  # CONSTANT_CASE
]
# product and technology names a level 1-3 question may use: the choice is often about them
_NAMES = {
    "GitHub", "GitLab", "JavaScript", "TypeScript", "WebSocket", "WebSockets", "LinkedIn",
    "YouTube", "PowerPoint", "OpenAI", "FastAPI", "PostgreSQL", "MySQL", "SQLite", "MongoDB",
    "DynamoDB", "CloudFlare", "ElevenLabs", "SendGrid", "PyPI", "NumPy", "PyTorch",
    "HuggingFace", "LangChain", "ChromaDB", "DuckDB", "BigQuery", "GraphQL", "OAuth", "macOS",
    "iOS", "iPhone", "JWTs", "e.g", "i.e",
}  # fmt: skip


def identifiers_in(text: str) -> list[str]:
    """Code-like tokens in a question: code spans, calls, file names, paths, snake_case,
    dotted names, camelCase, PascalCase and CONSTANT_CASE. A heuristic for the rule that
    level 1-3 questions name no identifiers: product names (PostgreSQL, GitHub) pass,
    plain-word names of functions do not get caught, and a few product names (Node.js) are
    flagged. Use it to review questions, not to reject them."""
    found: list[str] = []
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            tok = m.group(0).rstrip(".")
            if not tok or tok in _NAMES:
                continue
            if tok not in found:
                found.append(tok)
    return [t for t in found if not any(t != o and t in o for o in found)]
