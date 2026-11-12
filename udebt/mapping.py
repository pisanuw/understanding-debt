"""Build the architectural map: history mining + LLM proposes units; we attach churn,
importance and provenance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import repo, scoring
from .history import decision_candidates, rename_shas, tree_summary
from .llm import LLM
from .prompts import MAP_SCHEMA, map_prompt
from .provenance import Commit, load_commits, load_signatures, summarize, unit_provenance
from .store import Store, now_iso

IGNORED_PREFIXES = (".git/", ".udebt/", "node_modules/", "dist/", "build/", "__pycache__/")
FILE_LIST_LIMIT = 400


def _interesting(path: str) -> bool:
    return not path.startswith(IGNORED_PREFIXES) and not path.endswith((".lock", ".png", ".jpg"))


def commits_for(root: Path, store: Store) -> list[Commit]:
    sigs = load_signatures(store.config.extra.get("ai_signatures"))
    return load_commits(root, sigs)


def build_map(
    root: Path, store: Store, llm: LLM, *, max_units: int = 12, max_history: int = 40
) -> dict[str, Any]:
    files = [f for f in repo.tracked_files(root) if _interesting(f)]
    if not files:
        raise SystemExit("No tracked files to map. Commit something first.")
    commits = commits_for(root, store)
    candidates = decision_candidates(commits, rename_shas(root), max_history)
    tree = tree_summary(files) if len(files) > FILE_LIST_LIMIT else None

    result = llm.structured(
        map_prompt(files, max_units, candidates, tree), MAP_SCHEMA, cwd=root, read_tools=True
    )
    units = result.get("units", [])
    if not units:
        raise SystemExit("The mapper returned no units.")
    # The model treats max_units as advice; enforce it, keeping the most central units.
    units = sorted(units, key=lambda u: -int(u.get("centrality", 3)))[:max_units]

    previous = {u["id"]: u for u in (store.load_map() or {}).get("units", [])}
    by_sha = {c["sha"]: c for c in candidates}
    seen: set[str] = set()
    for u in units:
        uid = u["id"] = _unique(u["id"], seen)
        u["paths"] = [p.rstrip("/") for p in u.get("paths", []) if p]
        u["churn"] = repo.commit_count(root, u["paths"])
        u["scope"] = previous.get(uid, {}).get("scope", "architectural")
        hist = [by_sha[s[:7]] for s in u.pop("shaped_by", []) if s[:7] in by_sha]
        u["history"] = [
            {k: h.get(k) for k in ("sha", "date", "klass", "agent", "author", "email", "subject")}
            for h in hist
        ]
        prov = unit_provenance(commits, u["paths"], None)
        u["provenance"] = {
            "total": prov.total,
            "ai": prov.ai,
            "assisted": prov.assisted,
            "human": prov.human,
            "ai_share": round(prov.ai_share, 2),
            "first": (prov.first_date or "")[:10],
            "last": (prov.last_date or "")[:10],
        }

    max_churn = max((u["churn"] for u in units), default=0)
    for u in units:
        u["importance"] = scoring.importance(u.get("centrality", 3), u["churn"], max_churn)

    data = {
        "version": 2,
        "generated_at": now_iso(),
        "head": repo.head(root),
        "repo_provenance": summarize(commits),
        "units": units,
    }
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
