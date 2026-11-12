"""Build the architectural map: structural clusters and history mining feed the LLM, which
proposes a two-level hierarchy of units; we attach weighted churn, importance and
provenance. `rescore_map` redoes the numbers without the LLM.

The model's paths are checked against the tracked files before anything is scored
(`resolve_paths`): a path that names a tracked file or directory is kept, one that is the tail
of exactly one tracked file or directory (a package-relative `hf_api.py` for
`src/huggingface_hub/hf_api.py`) is replaced by it, and the rest are dropped and listed under
the unit's `unresolved`. When the units then cover under half of the clustered source files,
or a unit is left with no path, the call is retried once with the paths it got wrong; a map
still below that is written with `warnings`, which `status` shows first."""

from __future__ import annotations

import posixpath
from collections.abc import Callable
from dataclasses import dataclass, field
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
MIN_COVERAGE = 0.5  # below this share of clustered source files the map is retried, then flagged
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


@dataclass
class PathReport:
    """How the model's paths resolved against the tracked files (resolve_paths)."""

    exact: int = 0  # a tracked file or directory as given
    suffix: int = 0  # the tail of exactly one tracked file or directory
    unresolved: list[str] = field(default_factory=list)  # dropped: no match, or several
    empty: list[str] = field(default_factory=list)  # unit ids left with no path at all

    @property
    def given(self) -> int:
        return self.exact + self.suffix + len(self.unresolved)

    def summary(self) -> dict[str, Any]:
        return {
            "given": self.given,
            "exact": self.exact,
            "suffix": self.suffix,
            "unresolved": len(self.unresolved),
            "empty_units": list(self.empty),
        }


class PathIndex:
    """The tracked files and their directories, to resolve the paths a model gives."""

    def __init__(self, files: list[str]):
        self.files = set(files)
        self.dirs: set[str] = set()
        for f in files:
            d = posixpath.dirname(f)
            while d and d not in self.dirs:
                self.dirs.add(d)
                d = posixpath.dirname(d)
        self._all = sorted(self.files | self.dirs)

    def resolve(self, path: str) -> tuple[str | None, str]:
        """(the root-relative path, how: exact | suffix | none). Exact first: a tracked file
        or a directory holding tracked files. Else the path is the tail of exactly one
        tracked file or directory (`hf_api.py`, `huggingface_hub/hf_api.py`). Ambiguous
        tails and everything else resolve to None."""
        p = path.strip().replace("\\", "/")
        while p.startswith("./"):
            p = p[2:]
        p = p.strip("/")
        if not p or p == ".":
            return None, "none"
        if p in self.files or p in self.dirs:
            return p, "exact"
        tail = "/" + p
        hits = [f for f in self._all if f.endswith(tail)]
        if len(hits) == 1:
            return hits[0], "suffix"
        return None, "none"


def resolve_paths(units: list[dict[str, Any]], index: PathIndex) -> PathReport:
    """Replace every unit's and sub-unit's paths by what they resolve to among the tracked
    files (PathIndex.resolve), dropping the rest into the unit's `unresolved`. A parent's own
    paths are resolved before its sub-units' are merged into them."""
    rep = PathReport()
    for u, _parent in hier.walk(units):
        kept: list[str] = []
        lost: list[str] = []
        for given in u.get("paths", []):
            got, how = index.resolve(given)
            if got is None:
                lost.append(given)
                continue
            if how == "exact":
                rep.exact += 1
            else:
                rep.suffix += 1
            if got not in kept:
                kept.append(got)
        u["paths"] = kept
        if lost:
            u["unresolved"] = lost
            rep.unresolved += lost
        else:
            u.pop("unresolved", None)
    return rep


def path_examples(files: list[str]) -> list[str]:
    """Two root-relative paths from the tracked files, a file and a directory, for the map
    prompt to show what a path looks like here: the deepest directory holding at least half
    of the source files (else the one with most source files of its own), its shallowest
    source file and its largest subdirectory. In a package under `src/` both carry the
    prefixes a package-relative answer drops."""
    src = [f for f in files if structure.is_source(f) and not structure.is_test(f)] or list(files)
    nested = [f for f in src if "/" in f]
    if not nested:
        return sorted(src)[:2]
    direct: dict[str, int] = {}
    under: dict[str, int] = {}
    for f in nested:
        d = posixpath.dirname(f)
        direct[d] = direct.get(d, 0) + 1
        while d:
            under[d] = under.get(d, 0) + 1
            d = posixpath.dirname(d)
    half = [d for d in under if 2 * under[d] >= len(src)]
    if half:
        top = max(half, key=lambda d: (d.count("/"), under[d], d))
    else:
        top = max(direct, key=lambda d: (direct[d], d.count("/"), d))
    inside = [f for f in nested if f.startswith(top + "/")]
    file_ex = min(
        inside,
        key=lambda f: (f.count("/"), posixpath.basename(f).startswith("_"), f),
    )
    subs = [d for d in under if posixpath.dirname(d) == top]
    dir_ex = max(subs, key=lambda d: (under[d], d)) if subs else top
    return [file_ex, dir_ex]


def retry_note(rep: PathReport, cover: float, examples: list[str], limit: int = 40) -> str:
    """What the retried map prompt adds: the paths that did not resolve and the form a path
    must take."""
    shown = rep.unresolved[:limit]
    more = f" (and {len(rep.unresolved) - limit} more)" if len(rep.unresolved) > limit else ""
    lines = "\n".join(f"- {p}" for p in shown) or "- (none)"
    empty = (
        f"\nThese units ended up with no path that exists: {', '.join(rep.empty)}."
        if rep.empty
        else ""
    )
    ex = " and ".join(f"`{e}`" for e in examples) or "the paths in the file list"
    return f"""
RETRY: your previous answer covered only {round(100 * cover)}% of the clustered source files,
because these paths are not files or directories of this repository{more}:
{lines}{empty}
Every path must be relative to the repository ROOT, exactly as the file list and the
clusters write it, for example {ex}. Keep every leading directory (a `src/` prefix, the
package directory). Answer again in full, with the same schema.
"""


def _units_from(
    result: dict[str, Any],
    previous: dict[str, Any],
    by_sha: dict[str, Any],
    index: PathIndex,
    max_units: int,
    max_children: int,
) -> tuple[list[dict[str, Any]], PathReport]:
    units = result.get("units", [])
    if not units:
        raise SystemExit("The mapper returned no units.")
    # The model treats the caps as advice; enforce them, keeping the most central units.
    units = _by_centrality(units)[:max_units]
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
    rep = resolve_paths(units, index)
    for u in units:
        kids = hier.children_of(u)
        if kids:
            u["paths"] = _union(u["paths"], *(c["paths"] for c in kids))
    rep.empty = [u["id"] for u, _p in hier.walk(units) if not u.get("paths")]
    return units, rep


def build_map(
    root: Path,
    store: Store,
    llm: LLM,
    *,
    max_units: int = 12,
    max_children: int = 6,
    max_history: int = 40,
    retry: bool = True,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Ask the model for the units, resolve their paths against the tracked files, and retry
    once (unless `retry` is off) when they cover under MIN_COVERAGE of the clustered source
    files or a unit has no path left. A map still short of that is written with
    `warnings`. `out` gets the warnings as they happen."""
    files = mappable_files(root)
    if not files:
        raise SystemExit("No tracked files to map. Commit something first.")
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    candidates = history_candidates(root, store, commits, weights, max_history)
    tree = tree_summary(files) if len(files) > FILE_LIST_LIMIT else None
    struct = discover(root, store, files, commits, weights)
    examples = path_examples([f for c in struct.clusters for f in c.files] or files)

    prompt = map_prompt(
        files,
        max_units,
        candidates,
        tree,
        structure.prompt_block(struct) if struct.clusters else None,
        max_children,
        store.config.cochange_max_files,
        examples,
    )
    previous = hier.index((store.load_map() or {}).get("units", []))
    by_sha = {c["sha"]: c for c in candidates}
    index = PathIndex(repo.tracked_files(root))

    def ask(text: str) -> tuple[list[dict[str, Any]], PathReport, float]:
        # two levels take more reading than one; leave room so the answer is not cut off
        result = llm.structured(text, MAP_SCHEMA, cwd=root, read_tools=True, max_turns=80)
        units, rep = _units_from(result, previous, by_sha, index, max_units, max_children)
        return units, rep, coverage(units, struct)

    def short_of(rep: PathReport, cover: float) -> str | None:
        why = []
        if struct.clusters and cover < MIN_COVERAGE:  # no source files: nothing to cover
            why.append(
                f"the units cover {round(100 * cover)}% of the clustered non-test source files "
                f"(under {round(100 * MIN_COVERAGE)}%)"
            )
        if rep.empty:
            why.append(f"{len(rep.empty)} unit(s) have no path that exists ({_ids(rep.empty)})")
        if not why:
            return None
        lost = f"; {len(rep.unresolved)} path(s) did not resolve" if rep.unresolved else ""
        return "; ".join(why) + lost

    units, rep, cover = ask(prompt)
    problem = short_of(rep, cover)
    retried = False
    if problem and retry:
        out(f"Warning: {problem}. Asking the model once more with the paths it got wrong.")
        retried = True
        units2, rep2, cover2 = ask(prompt + retry_note(rep, cover, examples))
        if (cover2, -len(rep2.empty), -len(rep2.unresolved)) >= (
            cover,
            -len(rep.empty),
            -len(rep.unresolved),
        ):
            units, rep, cover = units2, rep2, cover2
        problem = short_of(rep, cover)
    attach_scores(units, commits, weights)

    summary = struct.summary()
    summary["coverage"] = round(cover, 2)
    data: dict[str, Any] = {
        "version": MAP_VERSION,
        "generated_at": now_iso(),
        "head": repo.head(root),
        "repo_provenance": summarize(commits),
        "structure": summary,
        "paths": {**rep.summary(), "retried": retried},
        "units": units,
    }
    if problem:
        hint = "re-run `udebt map`" if retried or not retry else "re-run `udebt map` (it retries)"
        data["warnings"] = [
            f"the map may be wrong: {problem}. Churn, importance and provenance only count "
            f"the files the units cover; {hint}, or fix the paths in .udebt/map.json and run "
            "`udebt map --rescore`."
        ]
        out(f"Warning: {data['warnings'][0]}")
    store.save_map(data)
    return data


def _ids(ids: list[str], n: int = 5) -> str:
    return ", ".join(ids[:n]) + (f" and {len(ids) - n} more" if len(ids) > n else "")


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
    return round(sum(1 for f in clustered if _under(f, paths)) / len(clustered), 3)


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
