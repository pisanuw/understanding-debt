"""The learning path (Phase 3.3, `udebt path`): which unit to learn next, in what order, how
long each takes and what to do about it. No model call.

Order: unitgraph.learning_order over the developer's units (their responsibility set, else
every top-level unit; blackbox units left out), by importance x debt, each unit after the
units it uses. Minutes per unit, to be read as a rough budget:

    1  to read the orientation, while level 1 is not passed
    4  for the walkthrough with the code (`udebt dig`), while level 1 is not passed
    2  per architectural level (1-3) still to pass, a question and its feedback each
    1  per spaced-review item due on the unit
    3  to re-check a unit known to level 3 whose understanding faded under the threshold

The next action is the cheapest step that moves the unit: a due review, the walkthrough at
first contact, the next ladder level (a teach-back is the alternative at level 2), a
re-check when it faded, else nothing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import ladder, review, scoring
from . import units as hier
from .unitgraph import UnitGraph, learning_order

SESSION_MINUTES = 10
READ_ORIENTATION, WALKTHROUGH, PER_LEVEL, RECHECK = 1, 4, 2, 3


@dataclass
class Step:
    unit: str
    name: str
    after: list[str]  # units earlier in the path this one uses
    importance: float
    understanding: float
    basis: str
    depth: int | None
    level: int
    debt: float
    minutes: int
    action: str
    command: str
    due_reviews: int = 0
    used_by: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _action(uid: str, s: scoring.UnitScore, level: int, due: int, threshold: float):
    depth = s.depth or 0
    if due:
        return f"review {due} missed question(s)", "udebt review"
    if depth == 0 and level == 1:
        return "walk through the code, then the level 1 check", f"udebt dig {uid}"
    if depth < 3 or level <= 3:
        what = ladder.LEVELS[level].name
        if level == 2:
            return f"level 2 ({what}), or explain it back", f"udebt quiz --unit {uid}"
        return f"level {level} ({what})", f"udebt quiz --unit {uid}"
    if s.understanding < threshold:
        return f"re-check: faded to {s.understanding:.2f}", f"udebt quiz --unit {uid}"
    return "nothing now (levels 4-5 are optional depth)", ""


def minutes(s: scoring.UnitScore, due: int, threshold: float) -> int:
    depth = s.depth or 0
    m = due * review.MINUTES_PER_ITEM
    if depth == 0:
        m += READ_ORIENTATION + WALKTHROUGH
    m += PER_LEVEL * max(0, 3 - depth)
    if depth >= 3 and s.understanding < threshold:
        m += RECHECK
    return m


def path(
    units: list[dict[str, Any]],
    scores: list[scoring.UnitScore],
    records: list[dict[str, Any]],
    graph: UnitGraph,
    *,
    within: set[str] | None = None,
    threshold: float = 0.6,
) -> list[Step]:
    """The steps of the developer's path over top-level units (in `within` when given)."""
    by = {s.unit_id: s for s in scores}
    pool = [
        u["id"]
        for u in units
        if by[u["id"]].scope == "architectural" and (not within or u["id"] in within)
    ]
    order = learning_order(pool, graph, {uid: by[uid].debt for uid in pool})
    items = review.due(records, unit_ids=set(pool))
    names = hier.index(units)
    out: list[Step] = []
    for i, uid in enumerate(order):
        s = by[uid]
        on = [r for r in records if r.get("unit") == uid]
        level = ladder.next_level(on)
        due = sum(1 for it in items if it.unit == uid)
        action, command = _action(uid, s, level, due, threshold)
        out.append(
            Step(
                unit=uid,
                name=names[uid].get("name", uid),
                after=[d for d in graph.depends_on(uid) if d in order[:i]],
                importance=s.importance,
                understanding=round(s.understanding, 3),
                basis=s.basis,
                depth=s.depth,
                level=level,
                debt=round(s.debt, 3),
                minutes=minutes(s, due, threshold),
                action=action,
                command=command,
                due_reviews=due,
                used_by=[d for d in graph.used_by(uid) if d in pool],
            )
        )
    return out


def session(steps: list[Step], budget: int = SESSION_MINUTES) -> list[Step]:
    """The first steps with something to do that fit a session of `budget` minutes (at least
    one, so a long first unit still gets started)."""
    out, total = [], 0
    for st in steps:
        if not st.command:
            continue
        if out and total + st.minutes > budget:
            break
        out.append(st)
        total += st.minutes
    return out
