"""The permission model as code (ROADMAP "Permission model"; owner answers 2026-11-12).

    role    sees                                              can
    owner   everything                                        assign roles, set visibility,
                                                              opt into soft blocks, import,
                                                              acknowledge, record evidence
    lead    every developer                                   import, acknowledge, record
    member  own rows, team aggregates, and every developer    import and record their own
            when the owner sets members_see_individuals       evidence only
    viewer  team aggregates only                              nothing

Postgres enforces the same rules in row-level security (migrations/postgres/0002_rls.sql);
SQLite has only this. Every read goes through `scope_export`, so a payload never carries a
developer the caller may not see, whichever engine served it: rows of hidden developers are
dropped, holders and responsibility lists are counted rather than named, and a `team (mean)`
pseudo-developer carries the aggregates.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

ROLES = ("owner", "lead", "member", "viewer")
TEAM = "team (mean)"  # the pseudo-developer that carries team aggregates
ACTIONS = {
    "assign_roles": {"owner"},
    "set_visibility": {"owner"},
    "set_soft_block": {"owner"},
    "import_all": {"owner", "lead"},
    "import_own": {"owner", "lead", "member"},
    "acknowledge": {"owner", "lead"},
    "record_evidence": {"owner", "lead", "member"},
    "see_jobs": {"owner", "lead"},
    "read": {"owner", "lead", "member", "viewer"},
}


class Forbidden(Exception):
    pass


@dataclass(frozen=True)
class Viewer:
    """Who is asking, in one repository."""

    user_id: str
    role: str
    dev_key: str | None = None  # their developer key in udebt's data (primary email)
    members_see_individuals: bool = False
    login: str | None = None

    def __post_init__(self):
        if self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}")

    def can(self, action: str) -> bool:
        return self.role in ACTIONS[action]

    def require(self, action: str) -> None:
        if not self.can(action):
            raise Forbidden(f"a {self.role} cannot {action.replace('_', ' ')}")

    def sees(self, dev: str | None) -> bool:
        """May this viewer see that developer's individual numbers?"""
        if self.role in ("owner", "lead"):
            return True
        if self.role == "member":
            mine = bool(dev) and bool(self.dev_key) and dev.lower() == self.dev_key.lower()
            return mine or self.members_see_individuals
        return False

    @property
    def sees_everyone(self) -> bool:
        return self.role in ("owner", "lead") or (
            self.role == "member" and self.members_see_individuals
        )


def team_rows(points: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """t -> the team aggregate row at that point: mean index and mean understanding per unit
    over every developer row present (not over the visible ones only)."""
    out = {}
    for p in points:
        rows = [r for d, r in p.get("devs", {}).items() if d != TEAM]
        idx = [r["index"] for r in rows if r.get("index") is not None]
        units: dict[str, list[float]] = {}
        for r in rows:
            for u, v in (r.get("und") or {}).items():
                if v is not None:
                    units.setdefault(u, []).append(v)
        out[p["t"]] = {
            "index": round(sum(idx) / len(idx), 1) if idx else None,
            "und": {u: round(sum(v) / len(v), 3) for u, v in sorted(units.items())},
            "debt": {},
            "team": len(rows),
        }
    return out


def scope_export(data: dict[str, Any], v: Viewer, team: dict[str, dict] | None = None):
    """The export as this viewer may see it. `team` (t -> aggregate row) comes from SQL when
    row-level security hid the other rows; else it is computed from `data` before filtering."""
    out = copy.deepcopy(data)
    if team is None:
        team = team_rows(data.get("snapshots", []))
    if v.sees_everyone:
        return out
    visible = [d for d in data.get("devs", []) if v.sees(d)]
    out["devs"] = visible + [TEAM]
    for p in out.get("snapshots", []):
        p["devs"] = {d: r for d, r in p.get("devs", {}).items() if d in visible}
        if p["t"] in team:
            p["devs"][TEAM] = team[p["t"]]
    for u in out.get("units", []):
        for key in ("you", "understanding", "depth", "basis"):
            if isinstance(u.get(key), dict):
                u[key] = {d: x for d, x in u[key].items() if d in visible}
        n = len(u.get("holders") or [])
        mine = [d for d in u.get("holders") or [] if d in visible]
        u["holders"] = mine + [f"(teammate {i + 1})" for i in range(n - len(mine))]
        resp = u.get("responsible") or []
        u["responsible"] = [d for d in resp if d in visible] + (
            [f"({len(resp) - len([d for d in resp if d in visible])} more)"]
            if len([d for d in resp if d not in visible])
            else []
        )
    out["evidence"] = [e for e in out.get("evidence", []) if e.get("dev") in visible]
    out["risk_events"] = [e for e in out.get("risk_events", []) if e.get("dev") in visible]
    out["responsibility"] = {
        d: s for d, s in (out.get("responsibility") or {}).items() if d in visible
    }
    out["contributors"] = [c for c in out.get("contributors", []) if c.get("key") in visible]
    for h in out.get("heavy_commits", []):
        if h.get("dev") not in visible:
            h["dev"] = None
            h["author"] = (
                "(a teammate)" if h.get("klass") in ("human", "assisted") else h.get("author")
            )
    out["scoped"] = {"role": v.role, "individuals": "self" if visible else "none"}
    return out
