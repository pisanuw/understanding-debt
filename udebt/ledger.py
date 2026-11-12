"""Where every commit landed, computed once per map, and the cut-off a score is taken at.

Scoring a developer reads, for each unit, the commits that touched it: their mass, their
weighted changed lines, who wrote them. `status` needs that at HEAD; a replay (replay.py)
needs it at every past point, for every developer, and before every commit it assesses for
risk. So the per-unit work (which commits touch the unit, the attribution and changed lines
of each) is done once, in a Ledger, and each score filters the ledger by a Cutoff.

A Cutoff is the repository as it stood at one point of its first-parent line: the commits
that had landed on it by then (a branch lands when its merge does), the evidence recorded up
to that moment, and the tree whose sizes the share decay divides by. With no cut-off,
everything counts: that is `status`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from . import units as hier
from .provenance import Commit, Touch, landed, line_weight, touches_of


class Ledger:
    """Each unit's touches (provenance.Touch), oldest first, for one history and one map.
    Units are keyed by id, so build one per map."""

    def __init__(self, commits: list[Commit], units: list[dict[str, Any]]):
        self.commits = commits
        self.pos = {c.sha: i for i, c in enumerate(commits)}
        self.members = hier.Membership(units)
        self._touches: dict[str, list[Touch]] = {}

    def touches(self, unit: dict[str, Any]) -> list[Touch]:
        uid = unit["id"]
        got = self._touches.get(uid)
        if got is None:
            got = touches_of(self.commits, unit.get("paths", []), self.members.split(uid))
            self._touches[uid] = got
        return got

    def since(
        self,
        touches: list[Touch],
        since_sha: str | None,
        weights: dict[str, Any],
        line_weights: dict[str, float] | None = None,
    ) -> tuple[float, float]:
        """(weighted mass, weighted changed lines) of `touches` after commit `since_sha`: all
        of them if that commit is unknown (rebased away), none without one. Bots land none."""
        if since_sha is None:
            return 0.0, 0.0
        after = self.pos.get(since_sha, -1)
        mass = lines = 0.0
        for t in touches:
            if t.pos > after:
                mass += landed(t.commit, weights) * t.att
                lines += line_weight(t.commit, line_weights, weights) * t.lines
        return round(mass, 3), round(lines, 1)


def parse_ts(ts: str) -> datetime:
    """An ISO 8601 time as an aware datetime (UTC when it carries no offset)."""
    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Cutoff:
    """The repository at step `step` of its first-parent line. `arrival[i]` is the step at
    which commit i (history order) landed; the commits with arrival <= step are in. Evidence
    counts when recorded at or before `when`; sizes are read from the tree of `rev`."""

    when: datetime
    rev: str
    step: int
    arrival: tuple[int, ...]

    def has(self, pos: int) -> bool:
        return self.arrival[pos] <= self.step

    def touches(self, touches: list[Touch]) -> list[Touch]:
        return [t for t in touches if self.arrival[t.pos] <= self.step]

    def records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in records if r.get("ts") and parse_ts(r["ts"]) <= self.when]
