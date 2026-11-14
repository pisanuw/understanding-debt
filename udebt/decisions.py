"""Decision records (Phase 3.3, `udebt decisions write`): one short ADR per top-level unit,
written into the repository only when asked (opt-in; nothing writes them by default).

Each record comes from the map, with no model call: the unit's context (summary and why), its
decision, the alternatives not chosen, the commits that shaped it and who made them, where it
lives, and a **Last confirmed by** line: the developer who most recently passed level 2 of
the question ladder (decision and alternatives) on the unit while their depth there is at
least 2. Once written, `decisions_dir` is kept in config, and every recorded level 2 pass
refreshes that line in the unit's file, leaving the rest of the file (and any hand edits) as
it is.

These files describe internal architecture, the alternatives rejected and who made each
call; they may not belong in a public repository (README)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import ladder, repo
from . import orientation as orient
from .store import Store

DEFAULT_DIR = "docs/decisions"
LINE = re.compile(r"^- \*\*Last confirmed by:\*\* .*$", re.M)


def filename(i: int, unit: dict[str, Any]) -> str:
    return f"{i:04d}-{unit['id'].replace('/', '-')}.md"


def confirmed(
    all_records: dict[str, list[dict[str, Any]]], uid: str, cfg
) -> tuple[str, str] | None:
    """(developer, date) of the latest level 2 pass on `uid` by a developer whose depth there
    is at least 2 now; None when nobody has one."""
    best: tuple[str, str] | None = None
    by_dev: dict[str, list[dict[str, Any]]] = {}
    for slug, recs in all_records.items():
        for r in recs:
            if r.get("unit") == uid:
                dev = cfg.primary(r.get("dev") or slug)
                by_dev.setdefault(dev, []).append(r)
    for dev, recs in by_dev.items():
        if (ladder.depth(recs) or 0) < 2:
            continue
        sit = ladder.sittings(recs).get(2, [])
        when = str(sit[-1].get("ts", ""))[:10] if sit else ""
        if when and (best is None or when > best[1]):
            best = (dev, when)
    return best


def confirmed_line(who: tuple[str, str] | None) -> str:
    if who is None:
        return "- **Last confirmed by:** nobody yet (filled when a developer passes level 2)"
    return f"- **Last confirmed by:** {who[0]} on {who[1]} (passed level 2 of the question ladder)"


def record(unit: dict[str, Any], i: int, head: str, who: tuple[str, str] | None) -> str:
    o = orient.cached(unit)
    where = o["where"] if o else [{"path": p, "what": ""} for p in unit.get("paths", [])[:4]]
    hist = [
        f"- `{h.get('sha', '')}` {h.get('date', '')} [{h.get('klass', '?')}] "
        f"{h.get('author') or '?'}: {h.get('subject', '')}"
        for h in unit.get("history") or []
    ]
    why = str(unit.get("why", "")).strip()
    out = [
        f"# {i}. {unit.get('name', unit['id'])}",
        "",
        f"- **Status:** in the code at `{head[:8]}`; recorded by `udebt decisions write` from "
        "`.udebt/map.json`",
        f"- **Unit:** `{unit['id']}` ({unit.get('kind', 'unit')})",
        confirmed_line(who),
        "",
        "## Context",
        "",
        " ".join(x for x in (str(unit.get("summary", "")).strip(), why) if x) or "Not recorded.",
        "",
        "## Decision",
        "",
        str(unit.get("decision", "")).strip() or "Not recorded.",
        "",
        "## Alternatives not chosen",
        "",
        str(unit.get("alternatives", "")).strip() or "Not recorded.",
        "",
        "## Who shaped it",
        "",
        *(hist or ["The history list does not say."]),
        "",
        "## Where it lives",
        "",
        *(f"- `{w['path']}`" + (f": {w['what']}" if w.get("what") else "") for w in where),
    ]
    return "\n".join(out) + "\n"


def index(units: list[dict[str, Any]], names: list[str]) -> str:
    rows = [f"- [{u.get('name', u['id'])}]({n})" for u, n in zip(units, names, strict=True)]
    return (
        "\n".join(
            [
                "# Decision records",
                "",
                "One record per architectural unit, written by `udebt decisions write` from the "
                "map in `.udebt/map.json`. They describe internal architecture, the alternatives "
                "rejected and who made each call: check before publishing them in a public "
                "repository. **Last confirmed by** names the developer who most recently passed "
                "level 2 of the question ladder (decision and alternatives) on the unit.",
                "",
                *rows,
            ]
        )
        + "\n"
    )


def render_all(root: Path, store: Store, map_data: dict[str, Any]) -> dict[str, str]:
    """file name -> text, for every top-level unit (in map order) and the README index."""
    cfg = store.config
    recs = store.all_records()
    head = repo.head(root)
    units = map_data["units"]
    names = [filename(i, u) for i, u in enumerate(units, 1)]
    out = {
        n: record(u, i, head, confirmed(recs, u["id"], cfg))
        for i, (u, n) in enumerate(zip(units, names, strict=True), 1)
    }
    out["README.md"] = index(units, names)
    return out


def write(root: Path, store: Store, map_data: dict[str, Any], rel: str) -> list[Path]:
    target = (root / rel).resolve()
    if root.resolve() not in (target, *target.parents):
        raise SystemExit(f"{rel} is outside the repository.")
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in render_all(root, store, map_data).items():
        p = target / name
        p.write_text(text, encoding="utf-8")
        written.append(p)
    cfg = store.config
    cfg.decisions_dir = rel
    store.save_config(cfg)
    return written


def refresh_confirmed(root: Path, store: Store, unit_ids: set[str]) -> list[Path]:
    """Rewrite the Last confirmed by line of each written record for `unit_ids`."""
    rel = store.config.decisions_dir
    m = store.load_map()
    if not rel or not m or not (root / rel).is_dir():
        return []
    recs = store.all_records()
    cfg = store.config
    changed = []
    for i, u in enumerate(m["units"], 1):
        if u["id"] not in unit_ids:
            continue
        p = root / rel / filename(i, u)
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        new = LINE.sub(confirmed_line(confirmed(recs, u["id"], cfg)), text, count=1)
        if new != text:
            p.write_text(new, encoding="utf-8")
            changed.append(p)
    return changed
