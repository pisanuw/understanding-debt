"""Pure scoring functions. No I/O here so they are easy to test and reason about.

Model
-----
For each architectural unit u and developer d:

    evidence(record)   = 1.0 correct | 0.5 partial | 0.0 wrong | -0.5 confidently wrong
    v_n(u, d)          = mean evidence of the latest sitting at ladder level n (ladder.py;
                         one answer unless --per-unit), 0 with none; a record without a
                         level (before the ladder) is level 5
    verified(u, d)     = max(0, (v_1 + v_2 + v_3) / 3 + 0.1 * (v_4 + v_5))   [0 to 1.2]
                         levels 1-3 are the architecture, a third each; 4-5 optional depth
    understanding(u,d) = min(1, verified * fade(since the last record))
    prior(u, d)        = 0.5 * dev_share * fade(others' changes since dev's last commit)
                         [no quiz records]
    debt(u, d)         = importance(u) * (1 - understanding(u, d))   [architectural units only]
    debt_index(d)      = 100 * sum(debt) / sum(importance)            [0 = none, 100 = total]

    So passing levels 1 to 3 is full understanding, and depth is never needed to reach the
    known threshold; levels 4-5 lift verified past 1, which is headroom against decay (a
    unit known to level 5 stays at 1.0 until fade < 1/1.2) and lifts a decayed
    understanding back over the threshold. A confidently wrong answer at any level is
    negative evidence for that level. evidence_model "flat" is the rule before the ladder:
    verified = clamp(mean evidence of the last K answers on u, 0, 1), levels ignored.

    fade, decay model "share" (default) = exp(-k * share changed), k = 1 (config decay_k),
        share changed = weighted changed lines landed on u since / u's size in lines, each
        commit's lines capped at u's size just after it (provenance.touches_of)
        (understanding falls to 1/e, 37%, once a unit's worth of change has landed)
    fade, decay model "mass"            = per_mass ** weighted mass landed on u since

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

import math
from dataclasses import dataclass, replace
from typing import Any

from . import ladder

VERDICT_SCORE = {"correct": 1.0, "partial": 0.5, "wrong": 0.0, "unknown": 0.0}


def evidence(record: dict[str, Any]) -> float:
    """Evidence value of one quiz record. Confident wrong answers count against you."""
    verdict = record.get("verdict", "wrong")
    base = VERDICT_SCORE.get(verdict, 0.0)
    if verdict == "wrong" and int(record.get("confidence", 1)) >= 3:
        return -0.5
    return base


def verified_understanding(records: list[dict[str, Any]], window: int = 5) -> float | None:
    """The flat model (before the ladder): mean evidence over the last `window` records,
    clamped to [0, 1], levels ignored. None without records."""
    if not records:
        return None
    recent = sorted(records, key=lambda r: r.get("ts", ""))[-window:]
    mean = sum(evidence(r) for r in recent) / len(recent)
    return max(0.0, min(1.0, mean))


DEPTH_BONUS = 0.1  # per depth level (4, 5) passed: up to 0.2 over the architecture's 1.0
EVIDENCE_MODELS = ("ladder", "flat")


def level_values(records: list[dict[str, Any]]) -> dict[int, float]:
    """Ladder level -> mean evidence of its latest sitting (ladder.sittings)."""
    return {
        n: sum(evidence(r) for r in sit) / len(sit) for n, sit in ladder.sittings(records).items()
    }


def ladder_verified(records: list[dict[str, Any]]) -> float | None:
    """Verified understanding on the ladder, before decay: a third for each architectural
    level (1-3) by its latest sitting, plus DEPTH_BONUS for each depth level (4-5), at least
    0 and at most 1.2. None without records."""
    if not records:
        return None
    v = level_values(records)
    arch = sum(v.get(n, 0.0) for n in ladder.ARCHITECTURAL) / len(ladder.ARCHITECTURAL)
    depth = DEPTH_BONUS * sum(v.get(n, 0.0) for n in ladder.DEPTH)
    return max(0.0, arch + depth)


def verified(records: list[dict[str, Any]], window: int = 5, model: str = "ladder") -> float | None:
    """Verified understanding under `model`: "ladder" (ladder_verified) or "flat"
    (verified_understanding over the last `window` answers)."""
    if model not in EVIDENCE_MODELS:
        raise ValueError(f"evidence_model must be one of {', '.join(EVIDENCE_MODELS)}: {model}")
    if model == "flat":
        return verified_understanding(records, window)
    return ladder_verified(records)


# k in exp(-k x share): pooled least-squares fit on every recorded synthetic unit-snapshot,
# shares capped per commit (Phase 3.1, ROADMAP Phase 5: 0.998 with huggingface_hub's summary,
# 1.06 without; per repository 2.72, 0.42 and about 0.6). Phase 1.4 used ln 4 = 1.386.
DECAY_K = 1.0
PER_MASS = 0.9  # the mass model's factor per unit of weighted mass
MODELS = ("share", "mass")


@dataclass(frozen=True)
class Decay:
    """How understanding fades while a unit changes without you.

    `share` (the default): by the share of the unit that changed, exp(-rate * share), where
    the share is the weighted changed lines landed on the unit over its size in lines, no
    commit counting more lines than the unit holds just after it. A fixed amount of change is
    a large part of a small unit and a small part of a large one, so the rate (config
    `decay_k`) means the same for every unit. The exponential composes: fading over two
    stretches of change is fading over their sum. `floor` (config `decay_floor`, default 0)
    keeps that part of an understanding however much changes: floor + (1 - floor) x
    exp(-rate * share), the form the Phase 3.1 refit tried and did not adopt (ROADMAP Phase 5).

    `mass`: the Phase 1 rule, per_mass ** weighted mass landed, kept to compare the two."""

    model: str = "share"
    rate: float = DECAY_K
    per_mass: float = PER_MASS
    floor: float = 0.0

    def __post_init__(self) -> None:
        if self.model not in MODELS:
            raise ValueError(f"decay_model must be one of {', '.join(MODELS)}: {self.model}")
        if not 0.0 <= self.floor < 1.0:
            raise ValueError(f"decay_floor must be in [0, 1): {self.floor}")

    def factor(self, mass: float, share: float | None = None) -> float:
        """What remains of an understanding after `mass` (weighted mass) or `share` (share
        of the unit changed) has landed on its unit. The share model needs the share; with
        none (no size known) it falls back to the mass rule."""
        if self.model == "share" and share is not None:
            fade = math.exp(-self.rate * max(0.0, share))
            return self.floor + (1.0 - self.floor) * fade if self.floor else fade
        return self.per_mass ** max(0.0, mass)

    @classmethod
    def of(cls, decay: float | Decay) -> Decay:
        """A Decay, or a plain per-mass factor (the mass model with that factor)."""
        return decay if isinstance(decay, Decay) else cls("mass", per_mass=float(decay))


def decayed(
    verified: float | None,
    mass_since: float,
    decay: float | Decay = PER_MASS,
    share_since: float | None = None,
) -> float:
    """`mass_since` is the summed weight of commits that touched the unit after the evidence
    was recorded, `share_since` the share of the unit their changed lines make (the share
    model's input). A plain number for `decay` is the mass model's factor."""
    if verified is None:
        return 0.0
    return verified * Decay.of(decay).factor(mass_since, share_since)


def importance(centrality: int, churn: float, max_churn: float) -> float:
    """Blend the mapper's 1-5 centrality rating with observed churn. Range (0, 1]."""
    c = max(1, min(5, int(centrality))) / 5.0
    ch = (churn / max_churn) if max_churn > 0 else 0.0
    return round(0.6 * c + 0.4 * ch, 3)


PRIOR_CAP = 0.5


def prior_understanding(
    dev_share: float,
    others_mass_since: float,
    decay: float | Decay = PER_MASS,
    others_share_since: float | None = None,
) -> float:
    """Weak prior from the developer's own commits to a unit, capped at PRIOR_CAP and eroded
    by what others (human or AI) changed on it since: by the share of the unit their changed
    lines make (share model) or by the weighted mass they landed (mass model)."""
    fade = Decay.of(decay).factor(others_mass_since, others_share_since)
    return PRIOR_CAP * max(0.0, min(1.0, dev_share)) * fade


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
    share_since: float | None = None  # share of the unit changed since the last evidence
    prior_share: float | None = None  # share others changed since the developer's last commit
    depth: int | None = None  # highest ladder level passed (ladder.depth); None: no evidence

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
    commits_since: float,
    *,
    window: int = 5,
    decay: float | Decay = PER_MASS,
    prior: float = 0.0,
    share_since: float | None = None,
    model: str = "ladder",
) -> UnitScore:
    """`model` is the evidence model (config evidence_model): "ladder" or "flat"."""
    v = verified(records, window, model)
    if v is not None:
        understanding = min(1.0, decayed(v, commits_since, decay, share_since))
        source = "quiz"
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
        share_since=share_since if v is not None else None,
        depth=ladder.depth(records),
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
