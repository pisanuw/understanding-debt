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

Hierarchy (map version 3): a unit may have sub-units.

    importance(sub-unit)  = importance(parent) split among its sub-units by the same
                            centrality/churn blend, so a parent's sub-units sum to it
    understanding(parent) = max(its own, importance-weighted mean of its sub-units' own),
                            the mean only once a sub-unit has quiz evidence
    understanding(sub)    = its own quiz evidence; else max(own-commit prior,
                            INHERIT * the parent's quiz understanding)

Team view: team_understanding(u) = max over developers, bus_factor(u) = number of developers
above the known threshold; a unit is below target when bus_factor < bus_target.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    source: str = "none"  # quiz | prior | children | parent | none
    prior: float = 0.0
    parent: str | None = None  # parent unit id for a sub-unit
    scope_origin: str = "team"  # team | you: where the effective scope comes from

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


INHERIT = 0.5  # share of a parent's quiz understanding a sub-unit inherits


def inherit(own: UnitScore, parent_quiz: float | None) -> UnitScore:
    """A sub-unit without quiz evidence of its own inherits half of its parent's quiz
    understanding when that beats its own-commit prior."""
    if own.source == "quiz" or parent_quiz is None:
        return own
    inherited = INHERIT * parent_quiz
    if inherited > own.understanding:
        return replace(own, understanding=inherited, source="parent")
    return own


def rollup(direct: UnitScore, children: list[UnitScore]) -> UnitScore:
    """A parent is understood at least as well as the importance-weighted mean of its
    architectural sub-units (their own understanding, not what they inherit from it), once
    any of them has quiz evidence. Without a quiz below it, the parent's own prior stands:
    the sub-units' priors come from the same commits."""
    arch = [c for c in children if c.scope == "architectural"]
    total = sum(c.importance for c in arch)
    if not arch or total <= 0 or not any(c.source == "quiz" for c in arch):
        return direct
    mean = sum(c.importance * c.understanding for c in arch) / total
    if mean > direct.understanding:
        return replace(direct, understanding=mean, source="children")
    return direct


def split_importance(parent_importance: float, raw: list[float]) -> list[float]:
    """Share a parent's importance among its sub-units in proportion to `raw`."""
    total = sum(raw)
    if total <= 0:
        return [round(parent_importance / len(raw), 3) for _ in raw] if raw else []
    return [round(parent_importance * r / total, 3) for r in raw]


def debt_index(scores: list[UnitScore], only: set[str] | None = None) -> float:
    """100 x sum(debt) / sum(importance) over architectural units (restricted to `only`)."""
    arch = [s for s in scores if s.scope == "architectural" and (only is None or s.unit_id in only)]
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


def below_target(team: dict[str, dict[str, Any]], unit_ids: list[str], target: int) -> list[str]:
    """Architectural units with fewer than `target` holders, in the order given."""
    return [
        u
        for u in unit_ids
        if u in team and team[u]["scope"] == "architectural" and team[u]["bus_factor"] < target
    ]


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
