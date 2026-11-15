"""What the hosted API serves, rebuilt from the tables in the export's shapes, so the SPA's
data layer reads the hosted API exactly as it reads `udebt serve`: `dashboard` is the
export (udebt-export v1) the dashboard page draws, `state` and `unit` the shapes of the local
`/api/state` and `/api/unit` (with what a metadata-only store can fill), and every one of them
passes through permissions.scope_export.

In Postgres with row-level security the rows another developer owns never leave the
database; the team aggregates then come from `udebt_team_series`, a security-definer function
that averages without naming anyone. In SQLite (no RLS) the rows are read whole and scoped in
code before anything is returned.
"""

from __future__ import annotations

from typing import Any

from .. import contract, ladder
from .db import Conn
from .permissions import TEAM, Viewer, scope_export

FEATURES = ["dashboard", "unit"]  # tabs a metadata-only store can serve (no code, no model)


def _team(c: Conn, repo_id: str) -> dict[str, dict[str, Any]] | None:
    if c.kind != "postgres":
        return None
    rows = c.q("select * from udebt_team_series(?)", [repo_id])
    return {r["t"]: {"index": None if r["mean_index"] is None else round(r["mean_index"], 1),
                     "und": r["und"] or {}, "debt": {}, "team": r["devs"]}
            for r in rows}  # fmt: skip


def evidence_rows(c: Conn, repo_id: str, dev: str | None = None) -> list[dict[str, Any]]:
    sql = "select * from evidence where repo_id = ?" + (" and dev_key = ?" if dev else "")
    rows = c.q(sql + " order by ts, key", [repo_id, dev.lower()] if dev else [repo_id])
    return [
        {"ts": r["ts"], "dev": r["dev_key"], "unit": r["unit_id"], "type": r["type"],
         "level": r["level"], "forced": r["forced"], "verdict": r["verdict"],
         "confidence": r["confidence"], "evidence": r["evidence"], "head": r["head"],
         "question": r["question"], "origin": r["origin"]}
        for r in rows
    ]  # fmt: skip


def export_of(c: Conn, repo: dict[str, Any]) -> dict[str, Any]:
    """The repository's export as the rows visible in this transaction give it."""
    rid = repo["id"]
    s = repo.get("settings") or {}
    points = []
    rows: dict[str, dict[str, Any]] = {}
    for r in c.q("select * from snapshots where repo_id = ? order by t, dev_key", [rid]):
        row = {"index": r["idx"], "und": r["und"], "debt": r["debt"]}
        if r["depth"]:
            row["depth"] = r["depth"]
        if r["quizzed"]:
            row["quizzed"] = r["quizzed"]
        rows.setdefault(r["t"], {})[r["dev_key"]] = row
    for p in c.q("select * from snapshot_points where repo_id = ? order by t", [rid]):
        points.append({"t": p["t"], "sha": p["sha"], "commits": p["commits"], "mass": p["mass"],
                       "units": p["units"], "devs": rows.get(p["t"], {})})  # fmt: skip
    units = []
    for u in c.q("select * from units where repo_id = ?", [rid]):
        d = dict(u["data"] or {})
        units.append({"id": u["unit_id"], "name": u["name"], "kind": u["kind"],
                      "parent": u["parent_id"], "scope": u["scope"],
                      "importance": u["importance"], "ai_share": u["ai_share"], **d})  # fmt: skip
    units.sort(key=lambda u: u.pop("ord", 0))
    for u in units:
        for k, default in (("you", {}), ("understanding", {}), ("depth", {}), ("basis", {}),
                           ("holders", []), ("responsible", [])):  # fmt: skip
            u.setdefault(k, default)
    risk = [
        {"sha": r["sha"], "unit": r["unit_id"], "dev": r["dev_key"], "level": r["level"],
         "mass": r["mass"], "understanding": r["understanding"], **(r["data"] or {})}
        for r in c.q("select * from risk_events where repo_id = ? order by key", [rid])
    ]  # fmt: skip
    return {
        "format": "udebt-export",
        "version": 1,
        "contract": contract.CONTRACT,
        "repo": s.get("repo") or repo["name"],
        "head": repo.get("head") or "",
        "generated_at": s.get("generated_at"),
        "settings": s.get("export") or {"known_threshold": 0.6, "bus_target": 2},
        "devs": list(s.get("devs") or []),
        "units": units,
        "responsibility": {
            r["dev_key"]: r["units"]
            for r in c.q("select * from responsibility where repo_id = ?", [rid])
        },
        "contributors": [
            r["data"] for r in c.q("select * from contributors where repo_id = ?", [rid])
        ],
        "snapshots": points,
        "evidence": evidence_rows(c, rid),
        "risk_events": risk,
        "heavy_commits": [
            r["data"]
            for r in c.q("select * from heavy_commits where repo_id = ? order by sha", [rid])
        ],
        "fingerprint": s.get("fingerprint"),
    }


def dashboard(c: Conn, repo: dict[str, Any], v: Viewer) -> dict[str, Any]:
    return scope_export(export_of(c, repo), v, _team(c, repo["id"]))


def _map(c: Conn, repo_id: str) -> dict[str, Any]:
    row = c.one("select data from maps where repo_id = ?", [repo_id])
    return (row or {}).get("data") or {"units": []}


def state(c: Conn, repo: dict[str, Any], org: dict[str, Any], v: Viewer) -> dict[str, Any]:
    """The local server's /api/state, for one hosted repository and viewer."""
    d = dashboard(c, repo, v)
    me = (v.dev_key or "").lower()
    mine = [e for e in d["evidence"] if e["dev"] == me]
    last = d["snapshots"][-1] if d["snapshots"] else {"devs": {}}
    row = last["devs"].get(me) or {}
    basis = {"measured": 0, "estimated": 0, "none": 0}
    units = []
    for u in d["units"]:
        und = (u.get("understanding") or {}).get(me)
        b = (u.get("basis") or {}).get(me) or ("estimated" if und else "none")
        if not u.get("parent") and u.get("scope", "architectural") == "architectural":
            basis[b] = basis.get(b, 0) + 1
        on = [e for e in mine if e["unit"] == u["id"]]
        units.append({
            "id": u["id"], "name": u["name"], "parent": u.get("parent"), "kind": u.get("kind"),
            "scope": u.get("scope", "architectural"), "importance": u.get("importance"),
            "understanding": und or 0.0, "basis": b, "depth": (u.get("depth") or {}).get(me),
            "level": ladder.next_level(on), "debt": None,
            "in_set": u["id"] in (d["responsibility"].get(me) or []),
            "oriented": True, "walkthrough": False,
        })  # fmt: skip
    m = _map(c, repo["id"])
    return {
        "mode": "hosted",
        "features": FEATURES,
        "role": v.role,
        "org": org["slug"],
        "repo": repo["name"],
        "email": v.dev_key or v.login or "",
        "head": (repo.get("head") or "")[:8],
        "index": row.get("index") if row else (last["devs"].get(TEAM) or {}).get("index"),
        "basis": basis,
        "units": units,
        "tour": bool(m.get("tour")),
        "review_due": 0,
        "session_minutes": 10,
        "ladder": {str(n): lv.name for n, lv in ladder.LEVELS.items()},
        "scoped": d.get("scoped"),
    }


def unit(c: Conn, repo: dict[str, Any], v: Viewer, uid: str) -> dict[str, Any] | None:
    """The local server's /api/unit, from the stored (sanitized) map and the visible rows: no
    walkthrough, no excerpts, no connections (they need the code)."""
    from .. import decisions
    from .. import orientation as orient
    from .. import units as hier
    from ..markdown import to_html

    m = _map(c, repo["id"])
    u = hier.resolve(m.get("units") or [], uid) if m.get("units") else None
    d = dashboard(c, repo, v)
    row = next((x for x in d["units"] if x["id"] == uid), None)
    if u is None and row is None:
        return None
    u = u or {"id": uid, "name": row["name"], "paths": []}
    me = (v.dev_key or "").lower()
    on = [e for e in d["evidence"] if e["dev"] == me and e["unit"] == uid]
    pos = next((i for i, x in enumerate(hier.flat(m.get("units") or []), 1) if x["id"] == uid), 0)
    und = ((row or {}).get("understanding") or {}).get(me)
    nxt = ladder.next_level(on)
    return {
        "id": uid,
        "name": u.get("name", uid),
        "kind": u.get("kind"),
        "summary": u.get("summary", ""),
        "paths": u.get("paths", []),
        "history": u.get("history") or [],
        "orientation": orient.cached(u) or orient.fallback(u),
        "decision_html": to_html(decisions.record(u, pos, repo.get("head") or "", None)),
        "walkthrough": None,
        "walkthrough_stale": False,
        "score": {
            "understanding": und or 0.0,
            "verified": None,
            "basis": ((row or {}).get("basis") or {}).get(me) or ("estimated" if und else "none"),
            "source": None,
            "depth": ((row or {}).get("depth") or {}).get(me),
            "level": nxt,
            "label": ladder.label(nxt),
            "debt": None,
            "importance": (row or {}).get("importance"),
        },
        "connections": {"uses": [], "used_by": [], "changes_with": []},
        "records": [
            {k: e.get(k) for k in ("ts", "level", "type", "verdict", "question")}
            | {"source": e.get("type"), "revealed": False, "feedback": None}
            for e in sorted(on, key=lambda e: e["ts"], reverse=True)
        ],
        "reviews": [],
    }
