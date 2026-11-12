"""Structural unit discovery: candidate units from the code and its history, before the LLM.

1. An import graph between tracked files, from the parsers in imports.py.
2. A co-change graph from history: two files are linked by every commit that changed both,
   by the commit's weight divided by (files in the commit - 1), so each commit spreads at
   most its weight per file. Commits touching more than `max_files` files (reformats, bulk
   renames) are ignored so they do not glue everything together.
3. The two are merged with equal total weight (when both exist) and partitioned by Louvain.
   Large communities are partitioned again on their own subgraph to give sub-clusters.
4. Each cluster is summarised for the map prompt: name candidates, member files ranked by
   the commit mass they received, the external packages its files import, and the other
   clusters it imports from.

Only code and test files are clustered. Docs, config and data files would glue unrelated code
together (a journal changes with everything), so each is attached afterwards to the cluster it
co-changes with most; those that change with nearly everything are hubs, listed separately.
"""

from __future__ import annotations

import posixpath
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .filekinds import kind as _kind
from .filekinds import mechanical
from .graph import Graph, communities, louvain
from .imports import PARSERS, Imports, scan
from .provenance import Commit
from .weights import CommitWeight

COCHANGE_MAX_FILES = 30
IMPORT_SHARE = 0.5  # share of the merged graph's total weight carried by import edges
# Clustered: source files in any language (a parser or not). Docs, config and data files
# attach to a cluster afterwards.
SOURCE_EXTS = {
    *PARSERS,
    *[
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".h",
        ".hh",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".swift",
        ".m",
        ".mm",
        ".ex",
        ".exs",
        ".erl",
        ".hs",
        ".ml",
        ".clj",
        ".dart",
        ".lua",
        ".r",
        ".jl",
        ".sh",
        ".bash",
        ".zsh",
        ".sql",
    ],
}
HUB_SHARE = 0.15  # a non-code file in more than this share of co-change commits is a hub
HUB_MIN_COMMITS = 20
SUB_MIN_FILES = 8  # clusters at least this big are partitioned again into sub-clusters
SEED = 7
_HEXISH = re.compile(r"[0-9a-f]*[0-9][0-9a-f]*|[0-9a-f]{8,}")  # numbers, revision ids

_GENERIC = {
    "index",
    "init",
    "main",
    "utils",
    "util",
    "helpers",
    "common",
    "base",
    "test",
    "tests",
    "conftest",
    "src",
    "lib",
    "app",
    "py",
    "js",
    "ts",
    "jsx",
    "tsx",
    "md",
    "json",
}


@dataclass
class Cluster:
    id: str
    files: list[str]
    mass: float
    label: str
    dirs: list[tuple[str, int]]
    tokens: list[str]
    external: list[tuple[str, int]] = field(default_factory=list)
    uses: list[tuple[str, int]] = field(default_factory=list)  # other cluster ids imported
    children: list[Cluster] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "files": len(self.files),
            "mass": round(self.mass, 2),
            "dirs": [f"{d} ({n})" for d, n in self.dirs],
        }
        if self.children:
            d["children"] = [
                {"id": c.id, "label": c.label, "files": len(c.files)} for c in self.children
            ]
        return d


@dataclass
class Structure:
    clusters: list[Cluster]
    singletons: list[str]
    hubs: list[str]
    nodes: int
    import_edges: int
    cochange_edges: int
    edges: int
    seconds: dict[str, float]
    languages: dict[str, int]

    def summary(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "import_edges": self.import_edges,
            "cochange_edges": self.cochange_edges,
            "languages": self.languages,
            "hubs": self.hubs,
            "singletons": len(self.singletons),
            "seconds": self.seconds,
            "clusters": [c.summary() for c in self.clusters],
        }


def graph_files(files: list[str]) -> list[str]:
    """Tracked files worth clustering: no lockfiles, generated or media files."""
    return [f for f in files if not mechanical(f)]


def cochange_graph(
    commits: list[Commit],
    weights: dict[str, CommitWeight],
    nodes: set[str],
    max_files: int = COCHANGE_MAX_FILES,
) -> tuple[Graph, Counter[str], int]:
    """Co-change graph over `nodes`, how many eligible commits touched each file, and the
    number of eligible commits (those with 2..max_files files on the graph)."""
    g = Graph()
    touches: Counter[str] = Counter()
    eligible = 0
    for c in commits:
        if not c.files or len(c.files) > max_files:
            continue
        w = weights[c.sha].weight if c.sha in weights else 0.0
        fs = sorted({f for f in c.files if f in nodes})
        if len(fs) < 2 or w <= 0:
            continue
        eligible += 1
        touches.update(fs)
        per = w / (len(fs) - 1)
        for i, a in enumerate(fs):
            for b in fs[i + 1 :]:
                g.add_edge(a, b, per)
    return g, touches, eligible


def import_graph(imports: dict[str, Imports], nodes: set[str]) -> Graph:
    g = Graph()
    for src, imp in imports.items():
        if src not in nodes:
            continue
        for dst in imp.internal:
            if dst in nodes:
                g.add_edge(src, dst, 1.0)
    return g


def file_mass(commits: list[Commit], weights: dict[str, CommitWeight]) -> dict[str, float]:
    """Commit weight landed on each file, split by line share (square root, as for units)."""
    out: dict[str, float] = {}
    for c in commits:
        w = weights[c.sha].weight if c.sha in weights else 0.0
        if w <= 0 or not c.files:
            continue
        lines = c.counted_lines
        total = sum(lines.values())
        for f in c.files:
            share = (lines.get(f, 0) / total) if total else 1 / len(c.files)
            out[f] = out.get(f, 0.0) + w * share**0.5
    return out


def build(
    root: Path,
    files: list[str],
    commits: list[Commit],
    weights: dict[str, CommitWeight],
    *,
    max_files: int = COCHANGE_MAX_FILES,
    seed: int = SEED,
) -> Structure:
    t0 = time.perf_counter()
    nodes_all = graph_files(files)
    core = {f for f in nodes_all if is_source(f)}
    imports = scan(root, sorted(core))
    t1 = time.perf_counter()
    co, touches, eligible = cochange_graph(commits, weights, set(nodes_all), max_files)
    hubs = sorted(
        f
        for f, n in touches.items()
        if f not in core and n >= HUB_MIN_COMMITS and eligible and n / eligible > HUB_SHARE
    )
    co_core = co.subgraph(core)
    imp = import_graph(imports, core)
    t2 = time.perf_counter()
    if imp.total_weight() > 0 and co_core.total_weight() > 0:
        scale = co_core.total_weight() / imp.total_weight() * IMPORT_SHARE / (1 - IMPORT_SHARE)
        merged = co_core.merged(imp, scale)
    else:
        merged = co_core.merged(imp)
    for f in core:
        merged.add_node(f)
    part = louvain(merged, seed=seed)
    t3 = time.perf_counter()

    mass = file_mass(commits, weights)
    groups = communities(part)
    multi = _by_mass([g for g in groups if len(g) >= 2], mass)
    cid_of = {f: f"C{i + 1}" for i, g in enumerate(multi) for f in g}
    attached = _attach(co, cid_of, set(nodes_all) - core - set(hubs))
    singletons = sorted(
        [f for g in groups if len(g) == 1 for f in g]
        + [f for f in set(nodes_all) - core - set(hubs) if f not in attached]
    )
    clusters = []
    for i, g in enumerate(multi):
        cid = f"C{i + 1}"
        extra = sorted(f for f, c in attached.items() if c == cid)
        c = _cluster(cid, g, extra, mass, imports)
        c.uses = _uses(c, imports, cid_of)
        if len(g) >= SUB_MIN_FILES:
            sub = louvain(merged.subgraph(g), seed=seed)
            parts = _by_mass([p for p in communities(sub) if len(p) >= 2], mass)
            if len(parts) >= 2:
                sub_of = {f: j for j, p in enumerate(parts) for f in p}
                sub_extra = _attach(co, {f: str(j) for f, j in sub_of.items()}, set(extra))
                c.children = [
                    _cluster(
                        f"{cid}.{j + 1}",
                        p,
                        sorted(f for f, k in sub_extra.items() if k == str(j)),
                        mass,
                        imports,
                    )
                    for j, p in enumerate(parts)
                ]
        clusters.append(c)
    t4 = time.perf_counter()
    langs = Counter(posixpath.splitext(f)[1] for f in imports)
    return Structure(
        clusters=clusters,
        singletons=singletons,
        hubs=hubs,
        nodes=len(core),
        import_edges=imp.n_edges(),
        cochange_edges=co_core.n_edges(),
        edges=merged.n_edges(),
        seconds={
            "imports": round(t1 - t0, 2),
            "cochange": round(t2 - t1, 2),
            "louvain": round(t3 - t2, 2),
            "summaries": round(t4 - t3, 2),
        },
        languages=dict(sorted(langs.items(), key=lambda kv: -kv[1])),
    )


def _attach(co: Graph, cid_of: dict[str, str], others: set[str]) -> dict[str, str]:
    """Each non-code file goes to the cluster it co-changes with most (if any)."""
    out: dict[str, str] = {}
    for f in sorted(others):
        score: Counter[str] = Counter()
        for g, w in co.adj.get(f, {}).items():
            if g in cid_of:
                score[cid_of[g]] += w
        if score:
            out[f] = min(score, key=lambda c: (-score[c], c))
    return out


def _by_mass(groups: list[list[str]], mass: dict[str, float]) -> list[list[str]]:
    return sorted(groups, key=lambda g: (-sum(mass.get(f, 0.0) for f in g), g[0]))


def _cluster(
    cid: str,
    members: list[str],
    extra: list[str],
    mass: dict[str, float],
    imports: dict[str, Imports],
) -> Cluster:
    """`members` are the clustered code and test files; `extra` the docs and config files
    attached to the cluster by co-change. Names come from the non-test code."""
    ranked = sorted(members, key=lambda f: (is_test(f), -mass.get(f, 0.0), f))
    ranked += sorted(extra, key=lambda f: (-mass.get(f, 0.0), f))
    naming = [f for f in members if not is_test(f)] or members
    dirs = Counter(posixpath.dirname(f) or "." for f in naming).most_common(3)
    prefix = "" if not dirs or dirs[0][0] == "." else dirs[0][0]
    toks: Counter[str] = Counter()
    for f in naming:
        stem = posixpath.splitext(posixpath.basename(f))[0]
        for t in re.split(r"[^a-zA-Z0-9]+|(?<=[a-z])(?=[A-Z])", stem):
            t = t.lower()
            if len(t) > 2 and t not in _GENERIC and not _HEXISH.fullmatch(t):
                toks[t] += 1.0 + mass.get(f, 0.0)  # heavy files name the cluster
    tokens = [t for t, _ in toks.most_common(4)]
    label = (prefix + "/" if prefix else "./") + (" " + " ".join(tokens[:3]) if tokens else "")
    ext: Counter[str] = Counter()
    for f in naming:
        if f in imports:
            ext.update(imports[f].external)
    return Cluster(
        id=cid,
        files=ranked,
        mass=sum(mass.get(f, 0.0) for f in members),
        label=label.strip(),
        dirs=dirs,
        tokens=tokens,
        external=ext.most_common(5),
    )


def _uses(c: Cluster, imports: dict[str, Imports], cid_of: dict[str, str]) -> list[tuple[str, int]]:
    n: Counter[str] = Counter()
    for f in c.files:
        if f in imports:
            for dst in imports[f].internal:
                other = cid_of.get(dst)
                if other and other != c.id:
                    n[other] += 1
    return n.most_common(3)


def is_source(f: str) -> bool:
    return posixpath.splitext(f)[1].lower() in SOURCE_EXTS


def is_test(f: str) -> bool:
    return _kind(f) == "test"


def prompt_block(s: Structure, max_clusters: int = 30, files_per_cluster: int = 10) -> str:
    """The clusters as compact text for the map prompt."""
    lines = []
    shown = s.clusters[:max_clusters]
    for c in shown:
        lines.append(_cluster_lines(c, files_per_cluster, indent=""))
        for sub in c.children:
            lines.append(_cluster_lines(sub, max(4, files_per_cluster // 2), indent="    "))
    rest = s.clusters[max_clusters:]
    if rest:
        n = sum(len(c.files) for c in rest)
        dirs = Counter(d for c in rest for d, _ in c.dirs).most_common(6)
        lines.append(
            f"- {len(rest)} smaller clusters ({n} files) not shown, mostly in: "
            + ", ".join(d for d, _ in dirs)
        )
    if s.singletons:
        dirs = Counter(posixpath.dirname(f) or "." for f in s.singletons).most_common(6)
        lines.append(
            f"- {len(s.singletons)} files with no structural links, mostly in: "
            + ", ".join(f"{d}/ ({k})" for d, k in dirs)
        )
    if s.hubs:
        lines.append("- hubs left out (they change with everything): " + ", ".join(s.hubs[:10]))
    return "\n".join(lines)


def _cluster_lines(c: Cluster, n_files: int, indent: str) -> str:
    more = len(c.files) - n_files
    files = ", ".join(c.files[:n_files]) + (f" (+{more} more)" if more > 0 else "")
    head = f"{indent}- {c.id} [{c.label}] {len(c.files)} files, mass {c.mass:.1f}"
    out = [head, f"{indent}  files: {files}"]
    extra = []
    if c.external:
        extra.append("imports " + ", ".join(n for n, _ in c.external))
    if c.uses:
        extra.append("uses " + ", ".join(u for u, _ in c.uses))
    if extra:
        out.append(f"{indent}  " + "; ".join(extra))
    return "\n".join(out)
