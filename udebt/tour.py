"""The architecture tour (Phase 3.3, `udebt tour`): a ten-minute read on the whole system
before any question, so a developer meets the architecture as a map rather than one quiz at a
time.

One `TASK: tour` call writes an overview and one paragraph per top-level unit (its purpose,
where it sits among the units it uses and that use it, the decision, the alternative not
chosen, who shaped it and when). The order and the connections are computed here, not by the
model: units in learning order with importance as the priority (a team-wide order, since the
tour is shared), and for each the units it uses, that use it and that change with it
(unitgraph.py). The result is cached in map.json (`tour`) with the `basis` it was written
from (each unit's orientation basis and the order); a re-map that changes a unit, or new
connections, make it stale, and the next `udebt tour` writes it again.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import orientation as orient
from . import repo
from .llm import LLM
from .prompts import TOUR_SCHEMA, tour_prompt
from .store import Store, now_iso
from .unitgraph import UnitGraph, learning_order

WORDS_PER_MINUTE = 200


def order(units: list[dict[str, Any]], graph: UnitGraph) -> list[str]:
    ids = [u["id"] for u in units]
    return learning_order(ids, graph, {u["id"]: float(u.get("importance", 0.5)) for u in units})


def basis(units: list[dict[str, Any]], graph: UnitGraph) -> str:
    key = [[u["id"], orient.basis(u)] for u in units] + [order(units, graph)]
    key += [[u["id"], graph.depends_on(u["id"])] for u in units]
    return hashlib.sha1(json.dumps(key).encode()).hexdigest()[:12]


def cached(map_data: dict[str, Any], graph: UnitGraph) -> dict[str, Any] | None:
    t = map_data.get("tour")
    if (
        isinstance(t, dict)
        and t.get("sections")
        and t.get("basis") == basis(map_data["units"], graph)
    ):
        return t
    return None


def connections(uid: str, graph: UnitGraph, names: dict[str, str]) -> dict[str, list[str]]:
    return {
        "uses": graph.depends_on(uid),
        "used_by": graph.used_by(uid),
        "changes_with": graph.changes_with(uid),
    }


def ensure(
    root: Path,
    store: Store,
    llm: LLM,
    map_data: dict[str, Any],
    graph: UnitGraph,
    *,
    refresh: bool = False,
    max_turns: int = 60,
) -> tuple[dict[str, Any], bool]:
    """(the tour, whether it was written now): the cached one when it is current, else one
    call, stored in map.json."""
    if not refresh:
        got = cached(map_data, graph)
        if got is not None:
            return got, False
    units = map_data["units"]
    by_id = {u["id"]: u for u in units}
    names = {u["id"]: u.get("name", u["id"]) for u in units}
    ids = order(units, graph)
    asked = []
    for uid in ids:
        u = by_id[uid]
        o = orient.cached(u)
        asked.append(
            {
                **u,
                "connections": connections(uid, graph, names),
                **({"oriented": orient.text(o)} if o else {}),
            }
        )
    res = llm.structured(
        tour_prompt(asked, names), TOUR_SCHEMA, cwd=root, read_tools=True, max_turns=max_turns
    )
    got = {str(s.get("unit_id")): str(s.get("paragraph", "")).strip() for s in res["units"]}
    sections = []
    for uid in ids:
        para = got.get(uid) or got.get(uid.split("/")[-1]) or ""
        if not para:
            u = by_id[uid]
            para = " ".join(
                x for x in (u.get("summary", ""), u.get("decision", ""), u.get("why", "")) if x
            )
        sections.append({"unit_id": uid, "paragraph": para})
    tour = {
        "overview": str(res.get("overview", "")).strip(),
        "sections": sections,
        "basis": basis(units, graph),
        "head": repo.head(root),
        "generated_at": now_iso(),
    }
    map_data["tour"] = tour
    store.save_map(map_data)
    return tour, True


def _names(uids: list[str], names: dict[str, str]) -> str:
    return ", ".join(names.get(u, u) for u in uids)


def markdown(tour: dict[str, Any], map_data: dict[str, Any], graph: UnitGraph, repo_name: str):
    """The tour as Markdown: the overview, the map (every unit in order with its
    connections) and one section per unit."""
    units = map_data["units"]
    by_id = {u["id"]: u for u in units}
    names = {u["id"]: u.get("name", u["id"]) for u in units}
    words = len(tour["overview"].split()) + sum(
        len(s["paragraph"].split()) for s in tour["sections"]
    )
    mins = max(1, round(words / WORDS_PER_MINUTE))
    when = (tour.get("generated_at") or datetime.now(UTC).isoformat())[:10]
    out = [
        f"# Architecture tour: {repo_name}",
        "",
        f"_Written by `udebt tour` at `{str(tour.get('head', ''))[:8]}` on {when}; "
        f"{len(tour['sections'])} units, about {mins} minutes to read. Units come in the order "
        "to learn them: each after the units it uses._",
        "",
        tour["overview"],
        "",
        "## The map",
        "",
        "*Uses*: its code imports the other unit's. *Changes with*: commits often touch both.",
        "",
    ]
    for i, s in enumerate(tour["sections"], 1):
        uid = s["unit_id"]
        c = connections(uid, graph, names)
        bits = []
        if c["uses"]:
            bits.append("uses " + _names(c["uses"], names))
        if c["used_by"]:
            bits.append("used by " + _names(c["used_by"], names))
        if c["changes_with"]:
            bits.append("changes with " + _names(c["changes_with"], names))
        out.append(f"{i}. **{names[uid]}** (`{uid}`): " + ("; ".join(bits) or "stands alone"))
    for i, s in enumerate(tour["sections"], 1):
        uid = s["unit_id"]
        u = by_id.get(uid, {"id": uid})
        o = orient.cached(u) if uid in by_id else None
        where = [w["path"] for w in o["where"]] if o else u.get("paths", [])[:3]
        out += ["", f"## {i}. {names.get(uid, uid)} (`{uid}`)", "", s["paragraph"]]
        if where:
            out += ["", "Open first: " + ", ".join(f"`{p}`" for p in where[:3]) + "."]
    out += [
        "",
        "---",
        "",
        "Next: `udebt path` for the order to learn these in and the time each takes, "
        "`udebt dig <unit>` for a walkthrough of one with its code.",
    ]
    return "\n".join(out) + "\n", mins
