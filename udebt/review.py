"""Spaced review (Phase 3.3): the questions a developer missed come back, at growing intervals,
until they stick. SM-2 style, one item per (unit, level), folded from the records.

An item starts on a miss (partial, wrong or unknown, a revealed answer included) at a unit
and level; a correct answer at that unit and level before any miss starts nothing. Then
every later answer there (quiz, review, dig, teach-back, tutor) moves it:

    quality      correct 5 (confidence 3), 4 (2), 3 (1); partial 2; wrong or unknown 1;
                 confidently wrong 0
    on q < 3     reps 0, interval 1 day, ease - 0.2 (at least 1.3)
    on q >= 3    reps + 1; interval 1 day at the first, 3 days at the second, then the
                 last interval x ease; ease + 0.1 - (5 - q) x (0.08 + (5 - q) x 0.02)
    due          the answer's time + the interval

The state after each answer is also written on the new record (`review`), so an export or
the hosted import can read it without folding; the fold is the source of truth. A review
asks a stored phrasing of the missed question (the question itself or one of its `variants`,
not the one asked last) with its stored reference, so a review costs one call: the grading.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from . import ladder
from .ledger import parse_ts

EASE = 2.5
EASE_MIN = 1.3
FIRST, SECOND = 1.0, 3.0  # days, after the first and the second correct answer
MINUTES_PER_ITEM = 1  # a review of five items is about five minutes


@dataclass
class State:
    ease: float
    interval: float  # days
    reps: int
    due: str  # ISO time

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def quality(r: dict[str, Any]) -> int:
    v = r.get("verdict")
    conf = int(r.get("confidence", 2) or 2)
    if v == "correct":
        return {3: 5, 2: 4}.get(conf, 3)
    if v == "partial":
        return 2
    if v == "wrong" and conf >= 3:
        return 0
    return 1


def step(state: State | None, r: dict[str, Any]) -> State | None:
    """The item's state after answer `r`; None while no miss has started one."""
    q = quality(r)
    if state is None and q >= 3:
        return None
    ease, interval, reps = (EASE, 0.0, 0) if state is None else (
        state.ease, state.interval, state.reps
    )  # fmt: skip
    if q < 3:
        reps, interval, ease = 0, FIRST, max(EASE_MIN, ease - 0.2)
    else:
        reps += 1
        interval = FIRST if reps == 1 else SECOND if reps == 2 else round(interval * ease, 1)
        ease = max(EASE_MIN, round(ease + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02), 3))
    when = parse_ts(r["ts"]) if r.get("ts") else datetime.now(UTC)
    due = (when + timedelta(days=interval)).astimezone(UTC).replace(microsecond=0)
    return State(round(ease, 3), interval, reps, due.isoformat())


def key(r: dict[str, Any]) -> tuple[str, int]:
    return str(r.get("unit")), ladder.level_of(r)


def fold(records: list[dict[str, Any]]) -> dict[tuple[str, int], State]:
    """(unit, level) -> state, over every record in time order."""
    out: dict[tuple[str, int], State] = {}
    for r in ladder._ordered(records):
        if not r.get("unit") or not r.get("verdict"):
            continue
        k = key(r)
        s = step(out.get(k), r)
        if s is not None:
            out[k] = s
    return out


def stamp(prior: list[dict[str, Any]], new: list[dict[str, Any]]) -> None:
    """Write each new record's item state (after it) as `review`, folding `prior` first."""
    states = fold(prior)
    for r in new:
        if not r.get("unit") or not r.get("verdict"):
            continue
        s = step(states.get(key(r)), r)
        if s is not None:
            states[key(r)] = s
            r["review"] = s.as_dict()


@dataclass
class Item:
    unit: str
    level: int
    state: State
    question: dict[str, Any]  # unit_id, level, question, reference_answer, evidence, variants

    @property
    def due(self) -> datetime:
        return parse_ts(self.state.due)


def _phrasing(at_key: list[dict[str, Any]]) -> dict[str, Any] | None:
    """What to ask for an item: the latest missed question's phrasings (the question and its
    variants), the first one not asked last, with that miss's reference answer."""
    misses = [r for r in at_key if r.get("verdict") != "correct" and r.get("question")]
    if not misses:
        return None
    miss = misses[-1]
    options = [str(miss["question"])] + [str(v) for v in miss.get("variants") or []]
    last_asked = str(at_key[-1].get("question", ""))
    pick = next((o for o in options if o != last_asked), options[0])
    return {
        "unit_id": miss["unit"],
        "level": ladder.level_of(miss),
        "question": pick,
        "reference_answer": miss.get("reference") or "(graded against the code)",
        "evidence": list(miss.get("evidence") or []),
        "variants": [o for o in options if o != pick][:2],
    }


def items(records: list[dict[str, Any]], unit_ids: set[str] | None = None) -> list[Item]:
    """Every review item with something to ask, soonest due first."""
    states = fold(records)
    ordered = ladder._ordered(records)
    out = []
    for (uid, level), st in states.items():
        if unit_ids is not None and uid not in unit_ids:
            continue
        at_key = [r for r in ordered if r.get("unit") == uid and ladder.level_of(r) == level]
        q = _phrasing(at_key)
        if q is not None:
            out.append(Item(uid, level, st, q))
    return sorted(out, key=lambda it: (it.due, it.level, it.unit))


def due(
    records: list[dict[str, Any]],
    now: datetime | None = None,
    unit_ids: set[str] | None = None,
    *,
    everything: bool = False,
) -> list[Item]:
    """The items due by `now` (all of them with `everything`), most overdue first."""
    now = now or datetime.now(UTC)
    return [it for it in items(records, unit_ids) if everything or it.due <= now]
