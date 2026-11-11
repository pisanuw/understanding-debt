"""Build the architectural map: LLM proposes units, we attach churn and importance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import repo, scoring
from .llm import LLM
from .prompts import MAP_SCHEMA, map_prompt
from .store import Store, now_iso

IGNORED_PREFIXES = (".git/", ".udebt/", "node_modules/", "dist/", "build/", "__pycache__/")


def _interesting(path: str) -> bool:
    return not path.startswith(IGNORED_PREFIXES) and not path.endswith((".lock", ".png", ".jpg"))


def build_map(root: Path, store: Store, llm: LLM, *, max_units: int = 12) -> dict[str, Any]:
    files = [f for f in repo.tracked_files(root) if _interesting(f)]
    if not files:
        raise SystemExit("No tracked files to map. Commit something first.")
    result = llm.structured(map_prompt(files, max_units), MAP_SCHEMA, cwd=root, read_tools=True)
    units = result.get("units", [])
    if not units:
        raise SystemExit("The mapper returned no units.")
    # The model treats max_units as advice; enforce it, keeping the most central units.
    units = sorted(units, key=lambda u: -int(u.get("centrality", 3)))[:max_units]

    previous = {u["id"]: u for u in (store.load_map() or {}).get("units", [])}
    seen: set[str] = set()
    for u in units:
        uid = u["id"] = _unique(u["id"], seen)
        u["paths"] = [p.rstrip("/") for p in u.get("paths", []) if p]
        u["churn"] = repo.commit_count(root, u["paths"])
        # Preserve scope decisions the team already made.
        u["scope"] = previous.get(uid, {}).get("scope", "architectural")

    max_churn = max((u["churn"] for u in units), default=0)
    for u in units:
        u["importance"] = scoring.importance(u.get("centrality", 3), u["churn"], max_churn)

    data = {"version": 1, "generated_at": now_iso(), "head": repo.head(root), "units": units}
    store.save_map(data)
    return data


def _unique(uid: str, seen: set[str]) -> str:
    base = uid.strip().lower().replace(" ", "-") or "unit"
    cand, n = base, 2
    while cand in seen:
        cand = f"{base}-{n}"
        n += 1
    seen.add(cand)
    return cand
