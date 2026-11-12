"""Who has contributed, and what kind of contributor each one is.

Labels:
  AI                 an agent authored effectively every commit under this identity
  Joint human-AI     a human whose commits carry agent trailers at a material rate
  Human              no agent signal and no AI-looking metadata
  Bot                CI, release, dependency and pipeline automation (bot signatures)
  Most likely AI     no explicit signature, but cadence and message shape look generated
  Most likely human  weak or mixed signals

AI, Bot and Joint come from the commit classes (provenance.py), so a contributor labelled
Bot is exactly one whose commits are classed `bot` and left out of every mass. "Most likely"
labels come from metadata only (commit cadence, message shape, when trailers start) and are
advisory: scoring never uses them unless a person confirms the label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from typing import Any

from .provenance import Commit

CONVENTIONAL = re.compile(
    r"^(feat|fix|chore|docs|refactor|test|perf|ci|style|revert|build)(\(|:|!)"
)
BULLET = re.compile(r"^\s*[-*•] ", re.M)


@dataclass
class Contributor:
    name: str
    emails: list[str]
    commits: int = 0
    ai: int = 0
    assisted: int = 0
    human: int = 0
    bot: int = 0
    first: str = ""
    last: str = ""
    label: str = "Human"
    confidence: float = 1.0
    reasons: list[str] = field(default_factory=list)
    is_you: bool = False
    _commits: list[Commit] = field(default_factory=list, repr=False)

    @property
    def assisted_share(self) -> float:
        return self.assisted / self.commits if self.commits else 0.0


def _groups(commits: list[Commit], identities: dict[str, list[str]]) -> dict[str, list[str]]:
    """email -> canonical key, honouring configured identity groups."""
    canon: dict[str, str] = {}
    for primary, aliases in identities.items():
        for e in [primary, *aliases]:
            canon[e.lower()] = primary.lower()
    out: dict[str, list[str]] = {}
    for c in commits:
        e = c.author_email.lower()
        key = canon.get(e, e)
        out.setdefault(key, [])
        if e not in out[key]:
            out[key].append(e)
    return out


def _ai_metadata_score(cs: list[Commit]) -> tuple[float, list[str]]:
    """0..1 from cadence and message shape. Needs a handful of commits to say anything."""
    if len(cs) < 5:
        return 0.0, ["too few commits to judge"]
    reasons = []
    times = sorted(datetime.fromisoformat(c.date) for c in cs)
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)]
    burst = sum(1 for g in gaps if g < 90) / len(gaps) if gaps else 0.0
    bodies = [c.body for c in cs]
    with_body = sum(1 for b in bodies if b.strip()) / len(cs)
    bullets = sum(1 for b in bodies if BULLET.search(b)) / len(cs)
    med_body = median(len(b) for b in bodies)
    conventional = sum(1 for c in cs if CONVENTIONAL.match(c.subject)) / len(cs)
    score = 0.0
    if burst >= 0.3:
        score += 0.3
        reasons.append(f"{int(burst * 100)}% of commits within 90s of the previous one")
    if with_body >= 0.8 and med_body >= 200:
        score += 0.3
        reasons.append(f"long bodies on {int(with_body * 100)}% of commits")
    if bullets >= 0.5:
        score += 0.2
        reasons.append(f"bullet lists in {int(bullets * 100)}% of bodies")
    if conventional >= 0.95 and with_body >= 0.8:
        score += 0.2
        reasons.append("uniformly conventional subjects with bodies")
    return min(1.0, score), reasons


def contributors(
    commits: list[Commit], identities: dict[str, list[str]], you: set[str]
) -> list[Contributor]:
    groups = _groups(commits, identities)
    by_key: dict[str, Contributor] = {}
    for c in commits:
        key = next(k for k, emails in groups.items() if c.author_email.lower() in emails)
        con = by_key.get(key)
        if con is None:
            con = by_key[key] = Contributor(name=c.author_name, emails=groups[key])
        con.commits += 1
        setattr(con, c.klass, getattr(con, c.klass) + 1)
        con.first = con.first or c.date[:10]
        con.last = c.date[:10]
        con._commits.append(c)
    you_l = {e.lower() for e in you}
    for con in by_key.values():
        con.is_you = any(e in you_l for e in con.emails)
        _label(con)
    return sorted(by_key.values(), key=lambda c: -c.commits)


def _label(con: Contributor) -> None:
    n = con.commits
    if con.ai / n >= 0.95:
        con.label, con.confidence = "AI", con.ai / n
        con.reasons = ["agent signature on the author identity"]
        return
    if con.bot / n >= 0.5:
        bots = sorted({c.agent or "bot" for c in con._commits if c.klass == "bot"})
        con.label, con.confidence = "Bot", con.bot / n
        con.reasons = [f"bot signature on the author identity ({', '.join(bots)})"]
        return
    if con.assisted_share >= 0.2:
        con.label, con.confidence = "Joint human-AI", con.assisted_share
        con.reasons = [f"{int(con.assisted_share * 100)}% of commits carry an agent trailer"]
        return
    score, reasons = _ai_metadata_score(con._commits)
    if con.assisted_share > 0:
        reasons.append(f"{int(con.assisted_share * 100)}% of commits carry an agent trailer")
    if score >= 0.6:
        con.label, con.confidence = "Most likely AI", score
    elif score >= 0.3 or 0 < con.assisted_share < 0.2:
        con.label, con.confidence = "Most likely human", 1.0 - score
    else:
        con.label, con.confidence = "Human", 1.0 - score
    con.reasons = reasons or ["no agent signal"]


def grouped(cons: list[Contributor]) -> dict[str, list[Contributor]]:
    order = ["AI", "Most likely AI", "Joint human-AI", "Most likely human", "Human", "Bot"]
    out: dict[str, list[Contributor]] = {k: [] for k in order}
    for c in cons:
        out.setdefault(c.label, []).append(c)
    return out


def as_dict(c: Contributor) -> dict[str, Any]:
    return {
        "name": c.name,
        "emails": c.emails,
        "commits": c.commits,
        "ai": c.ai,
        "assisted": c.assisted,
        "human": c.human,
        "bot": c.bot,
        "first": c.first,
        "last": c.last,
        "label": c.label,
        "confidence": round(c.confidence, 2),
        "reasons": c.reasons,
        "you": c.is_you,
    }
