"""Orientation: what a developer reads before their first question on a unit (Phase 3.2).

A developer with no answer at levels 1-3 on a unit gets a short orientation before its level 1
question: what the unit is for, the decision with its alternatives and why, two or three
paths to open first, and who shaped it (from the unit's `history`). The model writes it once
per unit and it is cached on the unit in map.json (`orientation`), shared by the team; a quiz
run asks for every missing one in a single call. `udebt dig` shows it as its first section.

An orientation records the `basis` it was written from (a hash of the unit's summary,
decision, alternatives, why, paths and history): when `udebt map` rewrites a unit, the old
orientation no longer matches and the next quiz writes a new one, while a re-map that leaves a
unit's fields unchanged carries the orientation over (mapping._normalise).
"""

from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path
from typing import Any

from . import ladder, repo
from . import units as hier
from .llm import LLM
from .prompts import ORIENTATION_SCHEMA, orientation_prompt
from .store import Store, now_iso

MAX_PATHS = 3
MAX_TURNS = 60


def basis(unit: dict[str, Any]) -> str:
    """What an orientation was written from; a change to any of it makes the cache stale."""
    key = [unit.get(k, "") for k in ("summary", "decision", "alternatives", "why")]
    key += [unit.get("paths", []), [h.get("sha") for h in unit.get("history") or []]]
    return hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:12]


def cached(unit: dict[str, Any]) -> dict[str, Any] | None:
    """The unit's orientation, if it has one written from its current fields."""
    o = unit.get("orientation")
    if isinstance(o, dict) and o.get("purpose") and o.get("basis") == basis(unit):
        return o
    return None


def wanted(records_on_unit: list[dict[str, Any]], level: int) -> bool:
    """Before a level 1 question, for a developer with no answer at levels 1-3 on the unit.
    Answers from before the ladder (level 5) do not count: they were about the code."""
    return level == 1 and not ladder.architectural(records_on_unit)


def _where(given: list[dict[str, Any]] | None, unit: dict[str, Any], root: Path):
    """The model's paths that name tracked files or directories, at most MAX_PATHS; the
    unit's own first paths when none does."""
    from .mapping import PathIndex

    index = PathIndex(repo.tracked_files(root))
    out: list[dict[str, str]] = []
    for w in given or []:
        got, _how = index.resolve(str(w.get("path", "")))
        if got and got not in {x["path"] for x in out}:
            out.append({"path": got, "what": str(w.get("what", "")).strip()})
    if not out:
        out = [{"path": p, "what": ""} for p in unit.get("paths", [])]
    return out[:MAX_PATHS]


def ensure(
    root: Path,
    store: Store,
    llm: LLM,
    map_data: dict[str, Any],
    unit_ids: list[str],
    *,
    max_turns: int = MAX_TURNS,
) -> dict[str, dict[str, Any]]:
    """unit id -> orientation for each of `unit_ids`, the cached ones as they are and the
    missing or stale ones written in one call, stored on the units of `map_data` and saved."""
    units = map_data["units"]
    by_id = hier.index(units)
    parents = hier.parent_of(units)
    out: dict[str, dict[str, Any]] = {}
    missing: list[dict[str, Any]] = []
    for uid in dict.fromkeys(unit_ids):
        o = cached(by_id[uid])
        if o is not None:
            out[uid] = o
        else:
            missing.append(by_id[uid])
    if not missing:
        return out
    asked = [
        {**u, "parent_name": by_id[parents[u["id"]]]["name"]} if u["id"] in parents else u
        for u in missing
    ]
    res = llm.structured(
        orientation_prompt(asked), ORIENTATION_SCHEMA, cwd=root, read_tools=True,
        max_turns=max_turns,
    )  # fmt: skip
    got = {str(o.get("unit_id", "")): o for o in res.get("orientations", [])}
    head = repo.head(root)
    for u in missing:
        o = got.get(u["id"]) or got.get(u["id"].split("/")[-1])
        if not o or not str(o.get("purpose", "")).strip():
            continue
        u["orientation"] = out[u["id"]] = {
            "purpose": str(o["purpose"]).strip(),
            "decision": str(o.get("decision", "")).strip(),
            "where": _where(o.get("where"), u, root),
            "shaped_by": str(o.get("shaped_by", "")).strip(),
            "basis": basis(u),
            "head": head,
            "generated_at": now_iso(),
        }
    store.save_map(map_data)
    return out


def _where_text(o: dict[str, Any]) -> str:
    return "; ".join(
        f"{w['path']} ({w['what']})" if w.get("what") else w["path"] for w in o["where"]
    )


def text(o: dict[str, Any]) -> str:
    """The orientation as plain sentences, for the question writer and the dig prompt."""
    return (
        f"What it is for: {o['purpose']} The decision: {o['decision']} "
        f"Where it lives: {_where_text(o)}. Who shaped it: {o['shaped_by']}"
    )


def render(o: dict[str, Any], unit: dict[str, Any], width: int = 92) -> str:
    """For the terminal: a titled block, each part wrapped and indented."""
    lines = [f"Orientation: {unit.get('name', unit['id'])}"]
    for head, body in (
        ("What it is for", o["purpose"]),
        ("The decision", o["decision"]),
        ("Where it lives", _where_text(o)),
        ("Who shaped it", o["shaped_by"]),
    ):
        lines += textwrap.wrap(
            f"{head}: {body}", width, initial_indent="  ", subsequent_indent="    "
        )
    return "\n".join(lines)


def markdown(o: dict[str, Any]) -> str:
    where = "\n".join(
        f"  - `{w['path']}`" + (f": {w['what']}" if w.get("what") else "") for w in o["where"]
    )
    return (
        f"- **What it is for.** {o['purpose']}\n- **The decision.** {o['decision']}\n"
        f"- **Where it lives.**\n{where}\n- **Who shaped it.** {o['shaped_by']}"
    )
