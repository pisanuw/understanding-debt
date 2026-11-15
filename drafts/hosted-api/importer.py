"""`POST /api/r/<org>/<repo>/import`: an export (contract `import`) into the tables, idempotent.

Every row has a natural key (a point's time; a developer row's time and developer; an
evidence record's hash of developer, time, unit and question; a risk event's commit, unit and
developer), so a second push of the same export inserts nothing and reports zeros. Points and
developer rows are replaced in place (the export is the newest computation of them); when the
series' fingerprint changed (a re-map, a new decay rule, `udebt snapshot --rebuild`), the stored
series and its risk events are dropped first and the new one is loaded whole.

Metadata only: evidence keeps the question, verdict, level and confidence, never the answer,
the feedback or the reference (dropped even from an export made `--with-answers`); the map is
stored sanitized (contract.sanitize_map).

An owner or lead imports everything; a member imports only evidence of their own developer
key (the rest is counted as skipped); a viewer imports nothing.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .. import contract
from .db import Conn
from .permissions import Viewer

EVIDENCE_FIELDS = ("ts", "unit", "level", "forced", "type", "verdict", "confidence",
                   "evidence", "head", "question")  # fmt: skip


def evidence_key(dev: str, ts: str, unit: str, question: str | None) -> str:
    raw = "\x1f".join([dev.lower(), ts, unit, question or ""])
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def evidence_row(repo_id: str, e: dict[str, Any], origin: str = "push") -> dict[str, Any]:
    dev = str(e["dev"]).lower()
    return {
        "repo_id": repo_id,
        "key": evidence_key(dev, e["ts"], e["unit"], e.get("question")),
        "dev_key": dev,
        "ts": e["ts"],
        "unit_id": e["unit"],
        "level": e.get("level"),
        "forced": bool(e.get("forced")),
        "type": e.get("type") or "quiz",
        "verdict": e["verdict"],
        "confidence": e.get("confidence"),
        "evidence": e.get("evidence"),
        "head": e.get("head"),
        "question": e.get("question"),
        "origin": origin,
    }


def _contributor_key(c: dict[str, Any]) -> str:
    if c.get("key"):
        return str(c["key"]).lower()
    return "name:" + hashlib.sha1(str(c.get("name", "")).encode()).hexdigest()[:12]


def import_export(c: Conn, repo: dict[str, Any], payload: dict[str, Any], v: Viewer) -> dict:
    """Load `payload` into `repo` as `v` may; returns what was added, replaced and skipped."""
    contract.check("import", payload)
    v.require("import_own")
    rid = repo["id"]
    counts = {"points": 0, "dev_rows": 0, "evidence": 0, "risk_events": 0, "units": 0,
              "contributors": 0, "skipped": 0, "rebuilt": False}  # fmt: skip
    if not v.can("import_all"):
        mine = (v.dev_key or "").lower()
        for e in payload["evidence"]:
            if mine and str(e["dev"]).lower() == mine:
                counts["evidence"] += c.insert_new(
                    "evidence", evidence_row(rid, e), ("repo_id", "key")
                )
            else:
                counts["skipped"] += 1
        counts["skipped"] += len(payload["snapshots"]) + len(payload["risk_events"])
        return counts

    settings = dict(repo.get("settings") or {})
    fp = payload.get("fingerprint")
    if fp is not None and settings.get("fingerprint") not in (None, fp):
        for table in ("snapshot_points", "snapshots", "risk_events"):
            c.exec(f"delete from {table} where repo_id = ?", [rid])
        counts["rebuilt"] = True
    have_points = {r["t"] for r in c.q("select t from snapshot_points where repo_id = ?", [rid])}
    have_rows = {(r["t"], r["dev_key"]) for r in
                 c.q("select t, dev_key from snapshots where repo_id = ?", [rid])}  # fmt: skip
    for p in payload["snapshots"]:
        c.upsert("snapshot_points", {"repo_id": rid, "t": p["t"], "sha": p["sha"],
                 "commits": p["commits"], "mass": p["mass"], "units": p["units"]},
                 ("repo_id", "t"))  # fmt: skip
        counts["points"] += p["t"] not in have_points
        for dev, row in p["devs"].items():
            c.upsert("snapshots", {
                "repo_id": rid, "t": p["t"], "dev_key": dev.lower(), "idx": row.get("index"),
                "und": row.get("und") or {}, "debt": row.get("debt") or {},
                "depth": row.get("depth") or {}, "quizzed": row.get("quizzed") or [],
            }, ("repo_id", "t", "dev_key"))  # fmt: skip
            counts["dev_rows"] += (p["t"], dev.lower()) not in have_rows
    for e in payload["evidence"]:
        counts["evidence"] += c.insert_new("evidence", evidence_row(rid, e), ("repo_id", "key"))
    for e in payload["risk_events"]:
        key = f"{e['sha']}:{e['unit']}:{str(e['dev']).lower()}"
        data = {k: e.get(k) for k in ("date", "landed", "klass", "subject", "weight", "source",
                                      "in_set")}  # fmt: skip
        counts["risk_events"] += c.insert_new("risk_events", {
            "repo_id": rid, "key": key, "sha": e["sha"], "unit_id": e["unit"],
            "dev_key": str(e["dev"]).lower(), "level": e["level"], "mass": e["mass"],
            "understanding": e["understanding"], "data": data,
        }, ("repo_id", "key"))  # fmt: skip
    have_units = {r["unit_id"] for r in c.q("select unit_id from units where repo_id = ?", [rid])}
    keep = ("you", "understanding", "depth", "basis", "holders", "responsible")
    for i, u in enumerate(payload["units"]):
        c.upsert("units", {
            "repo_id": rid, "unit_id": u["id"], "parent_id": u.get("parent"), "name": u["name"],
            "kind": u.get("kind"), "scope": u.get("scope") or "architectural",
            "importance": u.get("importance"), "ai_share": u.get("ai_share"),
            "data": {**{k: u.get(k) for k in keep if k in u}, "ord": i},
        }, ("repo_id", "unit_id"))  # fmt: skip
        counts["units"] += u["id"] not in have_units
    gone = have_units - {u["id"] for u in payload["units"]}
    for uid in gone:  # a unit the map no longer has
        c.exec("delete from units where repo_id = ? and unit_id = ?", [rid, uid])
    have_cons = {r["key"] for r in c.q("select key from contributors where repo_id = ?", [rid])}
    for con in payload["contributors"]:
        k = _contributor_key(con)
        c.upsert("contributors", {"repo_id": rid, "key": k, "dev_key": (con.get("key") or None)
                 and str(con["key"]).lower(), "data": con}, ("repo_id", "key"))  # fmt: skip
        counts["contributors"] += k not in have_cons
    for h in payload.get("heavy_commits", []):
        c.upsert("heavy_commits", {"repo_id": rid, "sha": h["sha"], "data": h}, ("repo_id", "sha"))
    c.exec("delete from responsibility where repo_id = ?", [rid])
    for dev, units in (payload.get("responsibility") or {}).items():
        c.upsert("responsibility", {"repo_id": rid, "dev_key": dev.lower(), "units": units},
                 ("repo_id", "dev_key"))  # fmt: skip
    if payload.get("map"):
        m = contract.sanitize_map(payload["map"])
        c.upsert("maps", {"repo_id": rid, "data": m, "generated_at": m.get("generated_at")},
                 ("repo_id",))  # fmt: skip
    settings.update({
        "export": payload["settings"], "fingerprint": fp, "devs": [d.lower() for d in
        payload["devs"]], "generated_at": payload.get("generated_at"), "repo": payload["repo"],
        "contract": payload.get("contract", contract.CONTRACT),
    })  # fmt: skip
    c.exec("update repos set head = ?, imported_at = CURRENT_TIMESTAMP, settings = ? where id = ?",
           [payload["head"], settings, rid])  # fmt: skip
    auto_map_devs(c, repo["org_id"], settings["devs"], payload["contributors"])
    return counts


def auto_map_devs(c: Conn, org_id: str, devs: list[str], contributors: list[dict]) -> int:
    """Members with no developer key get one when it is unambiguous: their verified email is
    a developer key of the export, or their GitHub login appears in a contributor's noreply
    address (`123+login@users.noreply.github.com`) whose key is a developer. Returns how many
    were mapped; an owner can set or change any mapping (`PUT .../members`)."""
    by_login: dict[str, str] = {}
    for con in contributors:
        key = str(con.get("key") or "").lower()
        if key not in devs:
            continue
        for e in con.get("emails") or []:
            local, _, host = str(e).lower().partition("@")
            if host == "users.noreply.github.com":
                by_login[local.split("+")[-1]] = key
    n = 0
    rows = c.q(
        "select m.user_id, u.email, u.login from memberships m join users u on u.id = m.user_id "
        "where m.org_id = ? and m.dev_key is null",
        [org_id],
    )
    for r in rows:
        key = None
        if r.get("email") and r["email"].lower() in devs:
            key = r["email"].lower()
        elif r.get("login") and r["login"].lower() in by_login:
            key = by_login[r["login"].lower()]
        if key:
            c.exec("update memberships set dev_key = ? where org_id = ? and user_id = ?",
                   [key, org_id, r["user_id"]])  # fmt: skip
            n += 1
    return n
