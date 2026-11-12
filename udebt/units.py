"""The unit hierarchy: top-level units, each with optional `children` (sub-units).

A sub-unit's id is `<parent id>/<child id>`, so ids stay unique and records keyed by unit id
need no other change. Maps before version 3 have no children; every helper here treats them
as a flat list, so older maps keep working.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from .provenance import _under

Unit = dict[str, Any]
Split = Callable[[str], float]  # path -> the part of its lines a unit gets


def walk(units: list[Unit]) -> Iterator[tuple[Unit, Unit | None]]:
    """(unit, parent) for every unit, parents before their children."""
    for u in units:
        yield u, None
        for c in u.get("children") or []:
            yield c, u


def flat(units: list[Unit]) -> list[Unit]:
    return [u for u, _ in walk(units)]


def index(units: list[Unit]) -> dict[str, Unit]:
    return {u["id"]: u for u in flat(units)}


def parent_of(units: list[Unit]) -> dict[str, str]:
    """child id -> parent id."""
    return {u["id"]: p["id"] for u, p in walk(units) if p is not None}


def children_of(unit: Unit) -> list[Unit]:
    return list(unit.get("children") or [])


def leaves(units: list[Unit]) -> list[Unit]:
    """The finest units: each parent's children in its place, childless units as they are."""
    out: list[Unit] = []
    for u in units:
        out.extend(children_of(u) or [u])
    return out


def level(units: list[Unit], deep: bool) -> list[Unit]:
    return leaves(units) if deep else list(units)


def has_children(units: list[Unit]) -> bool:
    return any(children_of(u) for u in units)


def resolve(units: list[Unit], ref: str) -> Unit | None:
    """A unit by full id, or a sub-unit by its short id when that is unambiguous."""
    by_id = index(units)
    if ref in by_id:
        return by_id[ref]
    short = [u for uid, u in by_id.items() if "/" in uid and uid.split("/", 1)[1] == ref]
    return short[0] if len(short) == 1 else None


def resolve_all(units: list[Unit], refs: list[str]) -> list[Unit]:
    out, missing = [], []
    for r in refs:
        u = resolve(units, r)
        if u is None:
            missing.append(r)
        else:
            out.append(u)
    if missing:
        raise SystemExit(f"Unknown unit id(s): {', '.join(missing)}")
    return out


def at_level(ids: set[str], units: list[Unit], deep: bool) -> set[str]:
    """Normalise a set of unit ids to one view. Top level: drop sub-units whose parent is in
    the set (it covers them). Deep: replace each parent by its sub-units."""
    parents = parent_of(units)
    by_id = index(units)
    if deep:
        out: set[str] = set()
        for i in ids:
            u = by_id.get(i)
            if u is None:
                continue
            kids = children_of(u)
            out |= {c["id"] for c in kids} if kids else {i}
        return out
    return {i for i in ids if i in by_id and parents.get(i) not in ids}


def covers(ids: set[str], unit_id: str, units: list[Unit]) -> bool:
    """Whether a set of unit ids covers `unit_id`: the unit or its parent is in the set."""
    return unit_id in ids or parent_of(units).get(unit_id) in ids


def rows(units: list[Unit], deep: bool, also: set[str] | None = None) -> list[tuple[Unit, int]]:
    """(unit, depth) in display order. Top level shows parents only, plus any sub-unit in
    `also` (for example the ones in your responsibility set); deep shows every sub-unit."""
    also = also or set()
    out: list[tuple[Unit, int]] = []
    for u in units:
        out.append((u, 0))
        for c in children_of(u):
            if deep or c["id"] in also:
                out.append((c, 1))
    return out


class Membership:
    """How the lines of a file that several units list are split among them: evenly among
    the top-level units that list it, then a parent's part evenly among those of its
    sub-units that list it. A file in four top-level units gives each a quarter of its lines,
    so a hub file (a `main.py` every flow passes through) is not counted four times over.
    Build one per map and pass `split(unit_id)` to every attribution, so churn, provenance,
    decay, risk and footprint all split the same way."""

    def __init__(self, units: list[Unit]):
        self._top = [u.get("paths", []) for u in units]
        self._kids = {u["id"]: [c.get("paths", []) for c in children_of(u)] for u in units}
        self._parent = parent_of(units)
        self._n: dict[tuple[str | None, str], int] = {}

    def listing(self, path: str, parent: str | None = None) -> int:
        """How many top-level units (or sub-units of `parent`) list `path`."""
        key = (parent, path)
        n = self._n.get(key)
        if n is None:
            groups = self._top if parent is None else self._kids.get(parent, [])
            n = self._n[key] = sum(1 for ps in groups if _under(path, ps))
        return n

    def split(self, unit_id: str) -> Split:
        parent = self._parent.get(unit_id)

        def part(path: str) -> float:
            x = 1.0 / max(1, self.listing(path))
            if parent is not None:
                x /= max(1, self.listing(path, parent))
            return x

        return part
