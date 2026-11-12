"""Build the architectural map: structural clusters and history mining feed the LLM, which
proposes a two-level hierarchy of units; we attach weighted churn, importance and
provenance. `rescore_map` redoes the numbers without the LLM."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import repo, scoring, structure
from . import units as hier
from .cache import open_cache
from .history import decision_candidates, tree_summary
from .llm import LLM
from .prompts import MAP_SCHEMA, map_prompt
from .provenance import (
    Commit,
    _under,
    assign_directed,
    landed,
    load_bots,
    load_signatures,
    summarize,
    unit_provenance,
)
from .store import Store, now_iso
from .weights import CommitWeight, ensure_weights

MAP_VERSION = 3
IGNORED_PREFIXES = (".git/", ".udebt/", "node_modules/", "dist/", "build/", "__pycache__/")
FILE_LIST_LIMIT = 400
HISTORY_FIELDS = ("sha", "date", "klass", "agent", "author", "email", "subject")


def _interesting(path: str) -> bool:
    return not path.startswith(IGNORED_PREFIXES) and not path.endswith((".lock", ".png", ".jpg"))


def mappable_files(root: Path) -> list[str]:
    return [f for f in repo.tracked_files(root) if _interesting(f)]


def commits_for(root: Path, store: Store) -> list[Commit]:
    """Every commit reachable from HEAD, oldest first, classified with the configured
    signatures and marked with the agent work each developer directed. Read through the
    parsed-commit cache (cache.py): only commits it has not seen are parsed."""
    cfg = store.config
    sigs = load_signatures(cfg.extra.get("ai_signatures"))
    bots = load_bots(cfg.extra.get("bot_signatures"))
    commits = open_cache(root, store.dir).commits(sigs, bots)
    assign_directed(commits, cfg.directed, cfg.primary)
    return commits


def weights_for(root: Path, store: Store, commits: list[Commit]) -> dict[str, CommitWeight]:
    """Cached commit weights. Also marks each commit's version-bump files, which line share
    leaves out, so call it before taking any share or attribution. The weighing inputs come
    from the parsed-commit cache, and only when the weights need recomputing."""
    pc = open_cache(root, store.dir)
    versions = pc.versions(commits)
    for c in commits:
        c.version_files = versions.get(c.sha, set())
        c.__dict__.pop("counted_lines", None)  # recomputed without the version lines
    return ensure_weights(
        root,
        store.weights_path,
        commits,
        None,
        store.config.group_max_commits,
        lambda: pc.inputs(commits),
    )


def history_candidates(
    root: Path,
    store: Store,
    commits: list[Commit],
    weights: dict[str, CommitWeight],
    max_n: int = 40,
) -> list[dict[str, Any]]:
    """The decision candidates the mapper sees (history.decision_candidates), scored from
    the commit weights, discounted by how much of each commit survives at HEAD (renames
    followed through the parsed-commit cache) and spread over the history's periods by the
    configured quota. No LLM call. Call after weights_for, which marks version bumps."""
    pc = open_cache(root, store.dir)
    cfg = store.config
    return decision_candidates(
        commits,
        pc.renames(commits),
        max_n,
        weights=weights,
        alive=set(repo.tracked_files(root)),
        moves=pc.moves(commits),
        n_periods=cfg.history_periods,
        quota=cfg.history_quota,
    )


def unit_mass(
    commits: list[Commit],
    paths: list[str],
    weights: dict[str, CommitWeight],
    split: hier.Split | None = None,
) -> float:
    """Weighted mass landed on `paths`: each commit's weight times its attribution to the
    paths (square root of the line share, shared files split by `split`). Bot commits land
    nothing."""
    return round(
        sum(
            landed(c, weights) * c.attribution(paths, split=split)
            for c in commits
            if c.sha in weights and c.touches(paths)
        ),
        3,
    )


def _attach_one(
    u: dict[str, Any],
    commits: list[Commit],
    weights: dict[str, CommitWeight],
    members: hier.Membership,
):
    split = members.split(u["id"])
    u["churn"] = unit_mass(commits, u["paths"], weights, split)
    prov = unit_provenance(commits, u["paths"], None, weights, split)
    u["provenance"] = {
        "total": prov.total,
        "ai": prov.ai,
        "assisted": prov.assisted,
        "human": prov.human,
        "bot": prov.bot,
        "ai_share": round(prov.ai_share, 2),
        "mass": prov.total_w,
        "first": (prov.first_date or "")[:10],
        "last": (prov.last_date or "")[:10],
    }


def attach_scores(
    units: list[dict[str, Any]], commits: list[Commit], weights: dict[str, CommitWeight]
) -> None:
    """Churn, provenance and importance for every unit. Top-level importance blends
    centrality with churn relative to the busiest top-level unit; a parent's importance is
    then split among its sub-units by the same blend among siblings. A file several units list
    counts a part in each (units.Membership)."""
    members = hier.Membership(units)
    for u, _parent in hier.walk(units):
        _attach_one(u, commits, weights, members)
    set_importance(units, {u["id"]: u["churn"] for u in hier.flat(units)})


def set_importance(units: list[dict[str, Any]], churn: dict[str, float]) -> None:
    """Importance from centrality and churn (unit id -> weighted mass): a top-level unit
    against the busiest top-level unit, a sub-unit's share of its parent's by the same blend
    among its siblings. The replay (replay.py) calls it with the churn up to a cut-off."""
    max_churn = max((churn.get(u["id"], 0.0) for u in units), default=0)
    for u in units:
        u["importance"] = scoring.importance(
            u.get("centrality", 3), churn.get(u["id"], 0.0), max_churn
        )
        kids = hier.children_of(u)
        if not kids:
            continue
        sib_max = max(churn.get(c["id"], 0.0) for c in kids)
        raw = [
            scoring.importance(c.get("centrality", 3), churn.get(c["id"], 0.0), sib_max)
            for c in kids
        ]
        for c, imp in zip(kids, scoring.split_importance(u["importance"], raw), strict=True):
            c["importance"] = imp


def rescore_map(root: Path, store: Store) -> dict[str, Any]:
    """Recompute churn, importance and provenance for the existing units with the current
    commit weights. No LLM call. Works on flat (version 1 and 2) maps as well."""
    data = store.require_map()
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    for u in data["units"]:
        u.setdefault("paths", [])
        for c in hier.children_of(u):
            c.setdefault("paths", [])
    attach_scores(data["units"], commits, weights)
    data["rescored_at"] = now_iso()
    data["head"] = repo.head(root)
    data["repo_provenance"] = summarize(commits)
    store.save_map(data)
    return data


def discover(root: Path, store: Store, files: list[str] | None = None, commits=None, weights=None):
    """The structural clusters the mapper sees, without calling the LLM."""
    files = files if files is not None else mappable_files(root)
    commits = commits if commits is not None else commits_for(root, store)
    weights = weights if weights is not None else weights_for(root, store, commits)
    return structure.build(root, files, commits, weights, max_files=store.config.cochange_max_files)


def build_map(
    root: Path,
    store: Store,
    llm: LLM,
    *,
    max_units: int = 12,
    max_children: int = 6,
    max_history: int = 40,
) -> dict[str, Any]:
    files = mappable_files(root)
    if not files:
        raise SystemExit("No tracked files to map. Commit something first.")
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    candidates = history_candidates(root, store, commits, weights, max_history)
    tree = tree_summary(files) if len(files) > FILE_LIST_LIMIT else None
    struct = discover(root, store, files, commits, weights)

    prompt = map_prompt(
        files,
        max_units,
        candidates,
        tree,
        structure.prompt_block(struct) if struct.clusters else None,
        max_children,
        store.config.cochange_max_files,
    )
    # two levels take more reading than one; leave room so the answer is not cut off
    result = llm.structured(prompt, MAP_SCHEMA, cwd=root, read_tools=True, max_turns=80)
    units = result.get("units", [])
    if not units:
        raise SystemExit("The mapper returned no units.")
    # The model treats the caps as advice; enforce them, keeping the most central units.
    units = _by_centrality(units)[:max_units]

    previous = hier.index((store.load_map() or {}).get("units", []))
    by_sha = {c["sha"]: c for c in candidates}
    seen: set[str] = set()
    for u in units:
        u["id"] = _unique(u["id"], seen)
        _normalise(u, previous, by_sha)
        kids = _by_centrality(u.pop("children", None) or [])[:max_children]
        kid_seen: set[str] = set()
        for c in kids:
            short = _unique(str(c.get("id", "")).split("/")[-1], kid_seen)
            c["id"] = f"{u['id']}/{short}"
            c.pop("children", None)
            _normalise(c, previous, by_sha)
        if len(kids) >= 2:  # one sub-unit is the unit itself
            u["children"] = kids
            u["paths"] = _union(u["paths"], *(c["paths"] for c in kids))
    attach_scores(units, commits, weights)

    summary = struct.summary()
    summary["coverage"] = coverage(units, struct)
    data = {
        "version": MAP_VERSION,
        "generated_at": now_iso(),
        "head": repo.head(root),
        "repo_provenance": summarize(commits),
        "structure": summary,
        "units": units,
    }
    store.save_map(data)
    return data


def coverage(units: list[dict[str, Any]], struct: structure.Structure) -> float:
    """Share of the clustered non-test source files that some unit's paths cover."""
    clustered = [
        f
        for c in struct.clusters
        for f in c.files
        if structure.is_source(f) and not structure.is_test(f)
    ]
    if not clustered:
        return 0.0
    paths = _union(*(u.get("paths", []) for u in units))
    return round(sum(1 for f in clustered if _under(f, paths)) / len(clustered), 2)


def _by_centrality(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(units, key=lambda u: -int(u.get("centrality", 3)))


def _normalise(u: dict[str, Any], previous: dict[str, Any], by_sha: dict[str, Any]) -> None:
    u["paths"] = _union([p.rstrip("/") for p in u.get("paths", []) if p])
    u["scope"] = previous.get(u["id"], {}).get("scope", "architectural")
    hist = [by_sha[s[:7]] for s in u.pop("shaped_by", None) or [] if s[:7] in by_sha]
    u["history"] = [{k: h.get(k) for k in HISTORY_FIELDS} for h in hist]


def _union(*lists: list[str]) -> list[str]:
    out: list[str] = []
    for ls in lists:
        for p in ls:
            if p not in out:
                out.append(p)
    return out


def _unique(uid: str, seen: set[str]) -> str:
    base = uid.strip().lower().replace(" ", "-").replace("/", "-") or "unit"
    cand, n = base, 2
    while cand in seen:
        cand = f"{base}-{n}"
        n += 1
    seen.add(cand)
    return cand
