import random

from udebt.graph import Graph, communities, louvain, modularity


def clique(g: Graph, names: list[str], w: float = 1.0) -> None:
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            g.add_edge(a, b, w)


def ring_of_cliques(k: int, size: int) -> tuple[Graph, list[list[str]]]:
    g, groups = Graph(), []
    for c in range(k):
        names = [f"c{c}n{i}" for i in range(size)]
        clique(g, names)
        groups.append(names)
    for c in range(k):  # one weak bridge between neighbouring cliques
        g.add_edge(groups[c][0], groups[(c + 1) % k][-1], 0.5)
    return g, groups


def test_graph_basics():
    g = Graph()
    g.add_edge("a", "b", 2)
    g.add_edge("b", "a", 1)  # parallel edges add
    g.add_edge("c", "c", 5)  # self loops are ignored but the node exists
    g.add_node("d")
    assert g.nodes == ["a", "b", "c", "d"]
    assert g.weight("a", "b") == 3 and g.n_edges() == 1 and g.total_weight() == 3
    assert g.subgraph(["a", "c"]).n_edges() == 0
    h = Graph()
    h.add_edge("a", "b", 1)
    h.add_edge("b", "d", 4)
    m = g.merged(h, scale=0.5)
    assert m.weight("a", "b") == 3.5 and m.weight("b", "d") == 2.0


def test_two_cliques_and_a_bridge():
    g = Graph()
    clique(g, list("abcde"))
    clique(g, list("vwxyz"))
    g.add_edge("e", "v", 0.2)
    part = louvain(g)
    assert communities(part) == [list("abcde"), list("vwxyz")]
    assert modularity(g, part) > 0.4


def test_ring_of_cliques_finds_every_clique():
    g, groups = ring_of_cliques(6, 5)
    part = louvain(g, seed=3)
    assert sorted(communities(part)) == sorted(sorted(x) for x in groups)
    # better than lumping neighbours together
    lumped = {n: i // 2 for i, grp in enumerate(groups) for n in grp}
    assert modularity(g, part) > modularity(g, lumped)


def test_deterministic_and_insertion_order_independent():
    g1, _ = ring_of_cliques(5, 4)
    edges = list(g1.edges())
    random.Random(1).shuffle(edges)
    g2 = Graph()
    for a, b, w in edges:
        g2.add_edge(b, a, w)
    assert louvain(g1, seed=7) == louvain(g1, seed=7) == louvain(g2, seed=7)


def test_ids_ordered_by_size_and_isolated_nodes_alone():
    g = Graph()
    clique(g, ["p", "q", "r", "s"])
    clique(g, ["x", "y"])
    g.add_node("lonely")
    part = louvain(g)
    assert part["p"] == 0 and part["x"] == 1 and part["lonely"] == 2
    assert louvain(Graph()) == {}


def test_modularity_of_trivial_partitions():
    g, groups = ring_of_cliques(3, 4)
    one = {n: 0 for n in g.nodes}
    assert abs(modularity(g, one)) < 1e-9  # everything together scores zero
    assert modularity(Graph(), {}) == 0.0
