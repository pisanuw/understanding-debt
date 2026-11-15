"""How the top-level units connect (Phase 3.3): who uses whom, and what changes together.

Unit A *uses* unit B when a file under A imports a file under B that A does not list itself
(imports.scan, the same parsers the structural discovery uses). A file k units list is a
k-th of each of them, as for line share, so an import from a hub file listed by five units
counts a fifth for each: the strength of A uses B is the sum over imports of 1 / (owners of
the importer x owners of the imported file), and only edges of strength >= `MIN_USES` (half
an import between two files of their own) order the learning path. Units *change
together* by the commits that touch both: each commit of at most `cochange_max_files` files
adds its landed weight / (units touched - 1) to every pair of units it touches, as the file
co-change graph does for files.

A file listed under more than `MAX_OWNERS` (two) top-level units is ignored when drawing
connections (Phase 3.4, owner): neither its imports nor the commits touching it connect
anything, since a hub file every flow passes through (digitalme's `backend/main.py`, in five
units) says only that the units share an entry point. It still counts, split, wherever mass
is attributed (units.Membership); only the connections leave it out. `hubs` lists them.

`udebt path` orders units by the uses edges (what others use first) and `udebt tour` lists
both kinds of connection; neither calls the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import repo
from .imports import parser_for, scan
from .provenance import Commit, _under, landed

MIN_USES = 0.5
MAX_OWNERS = 2  # a file more top-level units list than this connects nothing


@dataclass
class UnitGraph:
    ids: list[str]
    uses: dict[tuple[str, str], float] = field(default_factory=dict)  # (a, b) -> strength
    together: dict[tuple[str, str], float] = field(default_factory=dict)  # sorted pair -> w
    hubs: list[str] = field(default_factory=list)  # files ignored: listed by > MAX_OWNERS units

    def depends_on(self, a: str, floor: float = MIN_USES) -> list[str]:
        """The units `a` uses with at least `floor` strength, strongest first."""
        got = [(b, n) for (x, b), n in self.uses.items() if x == a and n >= floor]
        return [b for b, _ in sorted(got, key=lambda bn: (-bn[1], bn[0]))]

    def used_by(self, b: str, floor: float = MIN_USES) -> list[str]:
        got = [(a, n) for (a, y), n in self.uses.items() if y == b and n >= floor]
        return [a for a, _ in sorted(got, key=lambda an: (-an[1], an[0]))]

    def changes_with(self, a: str, n: int = 3, floor: float = 0.05) -> list[str]:
        """The units most often changed in the same commits as `a` (at most `n`)."""
        got = [
            (y if x == a else x, w)
            for (x, y), w in self.together.items()
            if a in (x, y) and w >= floor
        ]
        return [u for u, _ in sorted(got, key=lambda uw: (-uw[1], uw[0]))[:n]]

    def edges(self) -> list[list[Any]]:
        """Every connection as [a, b, kind, strength], for the tour's basis and the server."""
        out: list[list[Any]] = [
            [a, b, "uses", round(n, 2)] for (a, b), n in sorted(self.uses.items())
        ]
        out += [[a, b, "together", round(w, 2)] for (a, b), w in sorted(self.together.items())]
        return out


def build(
    root: Path,
    units: list[dict[str, Any]],
    commits: list[Commit],
    weights: dict[str, Any],
    *,
    max_files: int = 30,
    files: list[str] | None = None,
    max_owners: int = MAX_OWNERS,
) -> UnitGraph:
    """The graph over the top-level units of `units` (sub-units are not split out). Files
    more than `max_owners` units list are left out of both kinds of connection."""
    ids = [u["id"] for u in units]
    tracked = files if files is not None else repo.tracked_files(root)
    owners: dict[str, list[str]] = {}
    for f in tracked:
        for u in units:
            if _under(f, u.get("paths", [])):
                owners.setdefault(f, []).append(u["id"])
    hubs = sorted(f for f, o in owners.items() if len(o) > max_owners)
    for f in hubs:
        del owners[f]
    sources = sorted(f for f in owners if parser_for(f) is not None)
    g = UnitGraph(ids, hubs=hubs)
    for f, imp in scan(root, sources).items() if sources else []:
        mine = set(owners.get(f, []))
        for dst in imp.internal:
            theirs = owners.get(dst, [])
            for b in theirs:
                if b in mine:
                    continue
                for a in mine:
                    part = 1.0 / (len(mine) * len(theirs))
                    g.uses[(a, b)] = g.uses.get((a, b), 0.0) + part
    for c in commits:
        if not c.files or len(c.files) > max_files:
            continue
        w = landed(c, weights, 0.0)
        if w <= 0:
            continue
        hit = sorted({uid for f in c.files for uid in owners.get(f, [])})
        if len(hit) < 2:
            continue
        per = w / (len(hit) - 1)
        for i, a in enumerate(hit):
            for b in hit[i + 1 :]:
                g.together[(a, b)] = g.together.get((a, b), 0.0) + per
    g.uses = {k: round(v, 3) for k, v in g.uses.items()}
    g.together = {k: round(v, 3) for k, v in g.together.items()}
    return g


def learning_order(ids: list[str], graph: UnitGraph, priority: dict[str, float]) -> list[str]:
    """`ids` ordered so a unit comes after the units it uses (edges of at least MIN_USES):
    units are taken by `priority` (importance x debt), highest first, and each one's
    prerequisites, the units it uses, are placed before it, recursively and also by priority.
    A cycle is broken where the walk meets it: a prerequisite already being placed is
    skipped. So the foundations of the units that matter most come first, and a unit nothing
    uses and that uses nothing comes when its priority says.

    (Kahn's algorithm, as first planned, put digitalme's two isolated, least important
    units first because nothing held them back, and in its many cycles fell back to the
    highest priority, which put the chat flow, which uses four units, third.)"""
    rank = {u: (-priority.get(u, 0.0), i) for i, u in enumerate(ids)}
    known = set(ids)
    placed: list[str] = []
    state: dict[str, int] = {}  # 1 being placed, 2 placed

    def visit(u: str) -> None:
        if state.get(u):
            return
        state[u] = 1
        for d in sorted((d for d in graph.depends_on(u) if d in known), key=rank.__getitem__):
            visit(d)
        state[u] = 2
        placed.append(u)

    for u in sorted(ids, key=rank.__getitem__):
        visit(u)
    return placed
