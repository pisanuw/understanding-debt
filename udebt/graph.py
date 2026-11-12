"""A weighted undirected graph and Louvain community detection, stdlib only.

Used to propose candidate units from the structure of the code: files are nodes, imports
and co-changes are edges, and communities are groups of files more connected to each other
than to the rest. The result is deterministic: node order is shuffled by a seeded
`random.Random`, and ties are broken by community id.

Louvain (Blondel et al., 2008) alternates two phases until modularity stops improving:
move each node to the neighbouring community with the best modularity gain, then collapse
each community into a single node and repeat on the smaller graph.
"""

from __future__ import annotations

import random
from collections.abc import Iterable


class Graph:
    """Undirected weighted graph keyed by node name. Parallel edges add their weights."""

    def __init__(self) -> None:
        self.adj: dict[str, dict[str, float]] = {}

    def add_node(self, n: str) -> None:
        self.adj.setdefault(n, {})

    def add_edge(self, a: str, b: str, w: float = 1.0) -> None:
        if a == b or w <= 0:
            self.add_node(a)
            return
        self.adj.setdefault(a, {})
        self.adj.setdefault(b, {})
        self.adj[a][b] = self.adj[a].get(b, 0.0) + w
        self.adj[b][a] = self.adj[b].get(a, 0.0) + w

    @property
    def nodes(self) -> list[str]:
        return sorted(self.adj)

    def edges(self) -> Iterable[tuple[str, str, float]]:
        for a in sorted(self.adj):
            for b, w in sorted(self.adj[a].items()):
                if a < b:
                    yield a, b, w

    def n_edges(self) -> int:
        return sum(len(v) for v in self.adj.values()) // 2

    def total_weight(self) -> float:
        return sum(w for _, _, w in self.edges())

    def degree(self, n: str) -> float:
        return sum(self.adj.get(n, {}).values())

    def weight(self, a: str, b: str) -> float:
        return self.adj.get(a, {}).get(b, 0.0)

    def subgraph(self, keep: Iterable[str]) -> Graph:
        ks = set(keep)
        g = Graph()
        for a in sorted(ks):
            g.add_node(a)
            for b, w in self.adj.get(a, {}).items():
                if b in ks and a < b:
                    g.add_edge(a, b, w)
        return g

    def merged(self, other: Graph, scale: float = 1.0) -> Graph:
        """A new graph with this graph's edges plus `other`'s scaled by `scale`."""
        g = Graph()
        for n in self.adj:
            g.add_node(n)
        for n in other.adj:
            g.add_node(n)
        for a, b, w in self.edges():
            g.add_edge(a, b, w)
        for a, b, w in other.edges():
            g.add_edge(a, b, w * scale)
        return g


def modularity(g: Graph, partition: dict[str, int], resolution: float = 1.0) -> float:
    """Newman modularity Q of `partition` (node -> community id)."""
    m = g.total_weight()
    if m == 0:
        return 0.0
    internal: dict[int, float] = {}
    degree: dict[int, float] = {}
    for a, b, w in g.edges():
        if partition[a] == partition[b]:
            internal[partition[a]] = internal.get(partition[a], 0.0) + w
    for n in g.adj:
        c = partition[n]
        degree[c] = degree.get(c, 0.0) + g.degree(n)
    return sum(internal.get(c, 0.0) / m - resolution * (degree[c] / (2 * m)) ** 2 for c in degree)


# -- Louvain ---------------------------------------------------------------------------
# The working graph is a list of dicts over integer node ids. A self-loop adj[i][i] holds
# the weight of edges collapsed inside node i and counts twice towards its degree.


def _one_level(
    adj: list[dict[int, float]], rng: random.Random, resolution: float, max_passes: int = 50
) -> tuple[list[int], bool]:
    n = len(adj)
    k = [sum(w for j, w in adj[i].items() if j != i) + 2 * adj[i].get(i, 0.0) for i in range(n)]
    m2 = sum(k)  # twice the total edge weight
    comm = list(range(n))
    if m2 == 0:
        return comm, False
    tot = k[:]
    order = list(range(n))
    rng.shuffle(order)
    improved = False
    for _ in range(max_passes):
        moved = False
        for i in order:
            ci = comm[i]
            links: dict[int, float] = {}
            for j, w in adj[i].items():
                if j != i:
                    links[comm[j]] = links.get(comm[j], 0.0) + w
            tot[ci] -= k[i]
            best = ci
            best_gain = links.get(ci, 0.0) - resolution * tot[ci] * k[i] / m2
            for c in sorted(links):
                gain = links[c] - resolution * tot[c] * k[i] / m2
                if gain > best_gain + 1e-12:
                    best, best_gain = c, gain
            tot[best] += k[i]
            if best != ci:
                comm[i] = best
                moved = improved = True
        if not moved:
            break
    return comm, improved


def _renumber(comm: list[int]) -> list[int]:
    ids: dict[int, int] = {}
    return [ids.setdefault(c, len(ids)) for c in comm]


def _aggregate(adj: list[dict[int, float]], comm: list[int]) -> list[dict[int, float]]:
    size = max(comm) + 1 if comm else 0
    out: list[dict[int, float]] = [{} for _ in range(size)]
    for i, nbrs in enumerate(adj):
        ci = comm[i]
        for j, w in nbrs.items():
            cj = comm[j]
            if i == j:
                out[ci][ci] = out[ci].get(ci, 0.0) + w
            elif ci == cj:
                if i < j:
                    out[ci][ci] = out[ci].get(ci, 0.0) + w
            else:
                out[ci][cj] = out[ci].get(cj, 0.0) + w
    return out


def louvain(
    g: Graph, *, seed: int = 0, resolution: float = 1.0, max_levels: int = 20
) -> dict[str, int]:
    """Partition `g` into communities. Returns node -> community id, where ids are dense,
    ordered by community size (largest first) and then by the smallest node name, so the
    output does not depend on dict ordering. Isolated nodes get a community of their own."""
    nodes = g.nodes
    if not nodes:
        return {}
    idx = {n: i for i, n in enumerate(nodes)}
    adj: list[dict[int, float]] = [{} for _ in nodes]
    for a, nbrs in g.adj.items():
        for b, w in nbrs.items():
            adj[idx[a]][idx[b]] = w
    rng = random.Random(seed)
    membership = list(range(len(nodes)))
    for _ in range(max_levels):
        comm, improved = _one_level(adj, rng, resolution)
        if not improved:
            break
        comm = _renumber(comm)
        membership = [comm[c] for c in membership]
        adj = _aggregate(adj, comm)
        if len(adj) == 1:
            break
    groups: dict[int, list[str]] = {}
    for n, c in zip(nodes, membership, strict=True):
        groups.setdefault(c, []).append(n)
    ordered = sorted(groups.values(), key=lambda ms: (-len(ms), ms[0]))
    return {n: cid for cid, ms in enumerate(ordered) for n in ms}


def communities(partition: dict[str, int]) -> list[list[str]]:
    """partition -> list of member lists, in community id order, members sorted."""
    out: dict[int, list[str]] = {}
    for n, c in partition.items():
        out.setdefault(c, []).append(n)
    return [sorted(out[c]) for c in sorted(out)]
