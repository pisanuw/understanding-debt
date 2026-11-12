"""Pure scoring functions. No I/O here so they are easy to test and reason about.

Model
-----
For each architectural unit u and developer d:

    evidence(record)   = 1.0 correct | 0.5 partial | 0.0 wrong | -0.5 confidently wrong
    verified(u, d)     = clamp(mean(evidence of last K records), 0, 1)
    understanding(u,d) = verified * decay ** mass_landed_on_u_since_last_record
    prior(u, d)        = 0.5 * dev_share * decay ** others_mass_since_dev  [no quiz records]
    debt(u, d)         = importance(u) * (1 - understanding(u, d))   [architectural units only]
    debt_index(d)      = 100 * sum(debt) / sum(importance)            [0 = none, 100 = total]

Team view: team_understanding(u) = max over developers, bus_factor(u) = number of developers
above the known threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VERDICT_SCORE = {"correct": 1.0, "partial": 0.5, "wrong": 0.0, "unknown": 0.0}


def evidence(record: dict[str, Any]) -> float:
    """Evidence value of one quiz record. Confident wrong answers count against you."""
    verdict = record.get("verdict", "wrong")
    base = VERDICT_SCORE.get(verdict, 0.0)
    if verdict == "wrong" and int(record.get("confidence", 1)) >= 3:
        return -0.5
    return base


def verified_understanding(records: list[dict[str, Any]], window: int = 5) -> float | None:
    """Mean evidence over the last `window` records, clamped to [0, 1]. None without records."""
    if not records:
        return None
    recent = sorted(records, key=lambda r: r.get("ts", ""))[-window:]
    mean = sum(evidence(r) for r in recent) / len(recent)
    return max(0.0, min(1.0, mean))


def decayed(verified: float | None, mass_since: float, decay: float = 0.9) -> float:
    """`mass_since` is the summed weight of commits that touched the unit after the evidence
    was recorded (a plain count when weights are unavailable)."""
    if verified is None:
        return 0.0
    return verified * (decay ** max(0.0, mass_since))


def importance(centrality: int, churn: float, max_churn: float) -> float:
    """Blend the mapper's 1-5 centrality rating with observed churn. Range (0, 1]."""
    c = max(1, min(5, int(centrality))) / 5.0
    ch = (churn / max_churn) if max_churn > 0 else 0.0
    return round(0.6 * c + 0.4 * ch, 3)


PRIOR_CAP = 0.5


def prior_understanding(dev_share: float, others_mass_since: float, decay: float = 0.9) -> float:
    """Weak prior from the developer's own commits to a unit, capped at PRIOR_CAP and
    decayed by the weighted mass others (human or AI) landed on it since."""
    return PRIOR_CAP * max(0.0, min(1.0, dev_share)) * (decay ** max(0.0, others_mass_since))


@dataclass
class UnitScore:
    unit_id: str
    importance: float
    verified: float | None
    commits_since: float  # weighted mass since the last evidence
    understanding: float
    scope: str
    source: str = "none"  # quiz | prior | none
    prior: float = 0.0

    @property
    def measured(self) -> bool:
        return self.source == "quiz"

    @property
    def debt(self) -> float:
        if self.scope != "architectural":
            return 0.0
        return self.importance * (1.0 - self.understanding)


def score_unit(
    unit: dict[str, Any],
    records: list[dict[str, Any]],
    commits_since: int,
    *,
    window: int = 5,
    decay: float = 0.9,
    prior: float = 0.0,
) -> UnitScore:
    v = verified_understanding(records, window)
    if v is not None:
        understanding, source = decayed(v, commits_since, decay), "quiz"
    elif prior > 0:
        understanding, source = prior, "prior"
    else:
        understanding, source = 0.0, "none"
    return UnitScore(
        unit_id=unit["id"],
        importance=float(unit.get("importance", 0.5)),
        verified=v,
        commits_since=commits_since,
        understanding=understanding,
        scope=unit.get("scope", "architectural"),
        source=source,
        prior=prior,
    )


def debt_index(scores: list[UnitScore]) -> float:
    arch = [s for s in scores if s.scope == "architectural"]
    total_imp = sum(s.importance for s in arch)
    if total_imp == 0:
        return 0.0
    return round(100.0 * sum(s.debt for s in arch) / total_imp, 1)


def quiz_priority(scores: list[UnitScore]) -> list[UnitScore]:
    """Units worth quizzing first: high importance, low understanding, unmeasured first."""
    arch = [s for s in scores if s.scope == "architectural"]
    return sorted(arch, key=lambda s: (-(s.importance * (1 - s.understanding)), s.measured))


def last_record_head(records: list[dict[str, Any]]) -> str | None:
    if not records:
        return None
    return sorted(records, key=lambda r: r.get("ts", ""))[-1].get("head")


def team_view(
    per_dev: dict[str, list[UnitScore]], known_threshold: float = 0.6
) -> dict[str, dict[str, Any]]:
    """unit_id -> {understanding (max), bus_factor, holders}."""
    out: dict[str, dict[str, Any]] = {}
    for dev, scores in per_dev.items():
        for s in scores:
            t = out.setdefault(
                s.unit_id,
                {
                    "understanding": 0.0,
                    "bus_factor": 0,
                    "holders": [],
                    "importance": s.importance,
                    "scope": s.scope,
                },
            )
            t["understanding"] = max(t["understanding"], s.understanding)
            if s.understanding >= known_threshold:
                t["bus_factor"] += 1
                t["holders"].append(dev)
    return out
