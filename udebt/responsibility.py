"""Responsibility sets: the units each developer is expected to understand.

A developer's set is seeded from CODEOWNERS when CODEOWNERS names them, and from their
contribution footprint otherwise, then curated by a lead with `udebt responsibility`. The
developer's debt index is computed over the set (plus any unit they are touching), and
`udebt risk` treats a change to a unit outside the set as the strongest signal.

The same file holds per-developer scope: a unit one person declares `blackbox` (or
`architectural`, overriding a team-wide blackbox) without changing it for everyone.

.udebt/responsibility.json (committed):

    {"version": 1,
     "seeded_at": "...",
     "developers": {
        "dev@example.com": {"units": {"api": "codeowners", "db/models": "manual"},
                            "scope": {"web": "blackbox"}}},
     "pending": {"@carol": ["web"]}}

`units` maps a unit id to where it came from (codeowners, footprint, manual). `curated`
marks a set a lead edited by hand: a plain re-seed leaves it alone, `seed --force` re-seeds
it and keeps the manual entries. `pending` holds CODEOWNERS handles with no known email:
fill them in under `handles` in config.json and re-run `udebt responsibility seed`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import units as hier
from .contributors import contributors
from .provenance import Commit, _under
from .store import Config, Store, now_iso
from .weights import CommitWeight

CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
OWNER_SHARE = 0.3  # a CODEOWNERS owner of at least this share of a unit's files owns the unit
FOOTPRINT_MIN_MASS = 0.1  # and at least this much weighted mass, so one typo fix is not enough
NOT_DEVELOPERS = {"AI", "Bot"}  # contributor labels never given a responsibility set
PENDING = "pending:"  # marks a CODEOWNERS handle with no known email while seeding
_NOREPLY = re.compile(r"^(?:\d+\+)?([^@]+)@users\.noreply\.github\.com$", re.I)


# -- CODEOWNERS -------------------------------------------------------------------------


@dataclass
class Rule:
    pattern: str
    regex: re.Pattern
    owners: list[str]


def find_codeowners(root: Path) -> Path | None:
    for p in CODEOWNERS_PATHS:
        if (root / p).is_file():
            return root / p
    return None


def _glob(pattern: str) -> re.Pattern:
    """CODEOWNERS (gitignore-style) pattern -> regex over repo-relative file paths."""
    dir_only = pattern.endswith("/")
    pat = pattern.rstrip("/")
    anchored = pat.startswith("/") or "/" in pat
    pat = pat.lstrip("/")
    out, i = [], 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    last = pat.rsplit("/", 1)[-1]
    if dir_only:
        tail = "/.*"
    elif "*" in last.replace("**", ""):
        tail = ""  # GitHub: `docs/*` matches docs/a.md but not docs/sub/b.md
    else:
        tail = "(?:/.*)?"  # a file, or a directory and everything under it
    return re.compile(("^" if anchored else "^(?:.*/)?") + "".join(out) + tail + "$")


def parse_codeowners(text: str) -> list[Rule]:
    rules = []
    for line in text.splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        rules.append(Rule(parts[0], _glob(parts[0]), parts[1:]))
    return rules


def owners_of(path: str, rules: list[Rule]) -> list[str]:
    """Owners of one file: the last matching rule wins, as on GitHub."""
    for r in reversed(rules):
        if r.regex.match(path):
            return r.owners
    return []


def unit_files(unit: dict[str, Any], files: list[str]) -> list[str]:
    paths = unit.get("paths", [])
    return [f for f in files if _under(f, paths)] or list(paths)


def unit_owners(unit: dict[str, Any], files: list[str], rules: list[Rule]) -> dict[str, float]:
    """owner -> share of the unit's files they own."""
    fs = unit_files(unit, files)
    if not fs:
        return {}
    count: dict[str, int] = {}
    for f in fs:
        for o in owners_of(f, rules):
            count[o] = count.get(o, 0) + 1
    return {o: n / len(fs) for o, n in count.items()}


def owner_emails(owner: str, cfg: Config, known: set[str]) -> list[str] | None:
    """Developer keys for a CODEOWNERS owner, or None when the handle is not mapped yet.
    Emails map directly; handles through config `handles`, else through a GitHub noreply
    address (`123+handle@users.noreply.github.com`) seen in history or the aliases."""
    if not owner.startswith("@") and "@" in owner:
        return [cfg.primary(owner)]
    for h, v in cfg.handles.items():
        if h.lower() == owner.lower():
            if not v:
                return None
            vals = [v] if isinstance(v, str) else list(v)
            return sorted({cfg.primary(e) for e in vals})
    if "/" not in owner:  # a team handle (@org/team) cannot be inferred
        name = owner.lstrip("@").lower()
        for e in sorted(known):
            m = _NOREPLY.match(e)
            if m and m.group(1).lower() == name:
                return [cfg.primary(e)]
    return None


# -- footprint ---------------------------------------------------------------------------


def footprint(
    commits: list[Commit],
    weights: dict[str, CommitWeight],
    unit: dict[str, Any],
    cfg: Config,
) -> tuple[dict[str, float], dict[str, float]]:
    """(developer -> share, developer -> mass) of the human-led work on a unit. AI-authored
    commits are left out of both sides: responsibility is a human matter, and a unit only an
    agent touched has no footprint at all. Assisted commits count in full, since the human
    who committed them directed the work."""
    paths = unit.get("paths", [])
    mass: dict[str, float] = {}
    total = 0.0
    for c in commits:
        if c.klass == "ai" or not c.touches(paths):
            continue
        w = (weights[c.sha].weight if c.sha in weights else 1.0) * c.attribution(paths)
        key = cfg.primary(c.author_email)
        mass[key] = mass.get(key, 0.0) + w
        total += w
    if total <= 0:
        return {}, {}
    return {k: v / total for k, v in mass.items()}, mass


# -- the file ------------------------------------------------------------------------------


@dataclass
class Responsibility:
    developers: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: dict[str, list[str]] = field(default_factory=dict)
    seeded_at: str | None = None

    @classmethod
    def load(cls, path: Path) -> Responsibility:
        if not path.exists():
            return cls()
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            developers={k.lower(): v for k, v in d.get("developers", {}).items()},
            pending=d.get("pending", {}),
            seeded_at=d.get("seeded_at"),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        devs = {
            k: {
                **({"curated": True} if v.get("curated") else {}),
                "units": dict(sorted(v.get("units", {}).items())),
                **({"scope": dict(sorted(v["scope"].items()))} if v.get("scope") else {}),
            }
            for k, v in sorted(self.developers.items())
        }
        data = {
            "version": 1,
            "seeded_at": self.seeded_at,
            "developers": devs,
            "pending": dict(sorted(self.pending.items())),
        }
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def _dev(self, dev: str) -> dict[str, Any]:
        return self.developers.setdefault(dev.lower(), {"units": {}})

    def units(self, dev: str) -> dict[str, str]:
        return dict(self.developers.get(dev.lower(), {}).get("units", {}))

    def ids(self, dev: str) -> set[str]:
        return set(self.units(dev))

    def has_set(self, dev: str) -> bool:
        return bool(self.units(dev))

    def curated(self, dev: str) -> bool:
        """A lead has edited this set by hand; a plain `seed` leaves it alone."""
        return bool(self.developers.get(dev.lower(), {}).get("curated"))

    def add(self, dev: str, ids: list[str], origin: str = "manual") -> list[str]:
        self._dev(dev)["curated"] = True
        d = self._dev(dev).setdefault("units", {})
        added = [i for i in ids if i not in d]
        for i in ids:
            d.setdefault(i, origin)
        return added

    def remove(self, dev: str, ids: list[str]) -> list[str]:
        self._dev(dev)["curated"] = True
        d = self._dev(dev).setdefault("units", {})
        return [i for i in ids if d.pop(i, None) is not None]

    def scope(self, dev: str) -> dict[str, str]:
        return dict(self.developers.get(dev.lower(), {}).get("scope", {}))

    def set_scope(self, dev: str, unit_id: str, scope: str | None) -> None:
        d = self._dev(dev)
        sc = d.setdefault("scope", {})
        if scope is None:
            sc.pop(unit_id, None)
        else:
            sc[unit_id] = scope
        if not sc:
            d.pop("scope", None)

    def holders_of(self, unit_id: str, units: list[dict[str, Any]]) -> list[str]:
        """Developers whose set covers `unit_id` (directly or through its parent)."""
        return sorted(d for d in self.developers if hier.covers(self.ids(d), unit_id, units))

    def unassigned(self, units: list[dict[str, Any]], ids: list[str] | None = None) -> list[str]:
        """Units (top-level, or `ids`) no developer's set covers, even in part."""
        out = []
        for u in hier.flat(units):
            if (ids is None and hier.parent_of(units).get(u["id"])) or (
                ids is not None and u["id"] not in ids
            ):
                continue
            parts = [u["id"], *(c["id"] for c in hier.children_of(u))]
            if not any(self.holders_of(i, units) for i in parts):
                out.append(u["id"])
        return out

    def stale(self, units: list[dict[str, Any]]) -> dict[str, list[str]]:
        """developer -> unit ids in their set that the current map no longer has."""
        known = set(hier.index(units))
        out = {}
        for d in self.developers:
            gone = sorted(i for i in self.ids(d) if i not in known)
            if gone:
                out[d] = gone
        return out


def load(store: Store) -> Responsibility:
    return Responsibility.load(store.responsibility_path)


def effective_scope(
    unit_id: str, parent: str | None, team: dict[str, str], dev_scope: dict[str, str]
) -> tuple[str, str]:
    """(scope, origin). A developer's own choice for the unit, then for its parent, beats the
    team's; a team-wide blackbox parent makes its sub-units blackbox too."""
    for key in (unit_id, parent):
        if key and key in dev_scope:
            return dev_scope[key], "you"
    if team.get(unit_id) == "blackbox" or (parent and team.get(parent) == "blackbox"):
        return "blackbox", "team"
    return "architectural", "team"


# -- seeding ---------------------------------------------------------------------------------


@dataclass
class SeedReport:
    source: str  # codeowners+footprint | footprint
    codeowners: str | None
    seeded: dict[str, dict[str, str]]  # developer -> unit -> origin, as computed
    written: list[str]  # developers whose set was written
    kept: list[str]  # curated developers left alone (no --force)
    pending: dict[str, list[str]]
    unassigned: list[str]  # top-level units no developer's set covers


def _pick(
    units: list[dict[str, Any]], qualifies, origin: str, out: dict[str, dict[str, str]]
) -> None:
    """Top level first; where a developer does not qualify for a parent, try its sub-units."""
    for u in units:
        top = qualifies(u)
        for dev in top:
            out.setdefault(dev, {})[u["id"]] = origin
        for c in hier.children_of(u):
            for dev in qualifies(c):
                if dev not in top:
                    out.setdefault(dev, {})[c["id"]] = origin


def seed(
    root: Path,
    store: Store,
    units: list[dict[str, Any]],
    commits: list[Commit],
    weights: dict[str, CommitWeight],
    files: list[str],
    *,
    force: bool = False,
) -> SeedReport:
    cfg = store.config
    resp = load(store)
    known = {c.author_email.lower() for c in commits}
    for p, aliases in cfg.identities.items():
        known |= {p.lower(), *(a.lower() for a in aliases)}
    cons = contributors(commits, cfg.identities, set())
    humans = {cfg.primary(e) for c in cons if c.label not in NOT_DEVELOPERS for e in c.emails}

    seeded: dict[str, dict[str, str]] = {}
    pending: dict[str, list[str]] = {}
    co_path = find_codeowners(root)
    named: set[str] = set()
    if co_path is not None:
        rules = parse_codeowners(co_path.read_text(encoding="utf-8", errors="replace"))

        def by_owners(u: dict[str, Any]) -> set[str]:
            devs: set[str] = set()
            for owner, share in unit_owners(u, files, rules).items():
                if share < OWNER_SHARE:
                    continue
                emails = owner_emails(owner, cfg, known)
                devs |= set(emails) if emails is not None else {PENDING + owner}
            return devs

        _pick(units, by_owners, "codeowners", seeded)
        for k in [k for k in seeded if k.startswith(PENDING)]:
            pending[k[len(PENDING) :]] = sorted(seeded.pop(k))
        named = set(seeded)

    shares: dict[str, tuple[dict[str, float], dict[str, float]]] = {}

    def by_footprint(u: dict[str, Any]) -> set[str]:
        if u["id"] not in shares:
            shares[u["id"]] = footprint(commits, weights, u, cfg)
        share, mass = shares[u["id"]]
        return {
            d
            for d, s in share.items()
            if d in humans
            and d not in named
            and s >= cfg.footprint_threshold
            and mass[d] >= FOOTPRINT_MIN_MASS
        }

    _pick(units, by_footprint, "footprint", seeded)

    written, kept = [], []
    for dev in sorted(set(seeded) | (set(resp.developers) if force else set())):
        picks = seeded.get(dev, {})
        if force:  # recompute the seeded part; keep what a lead added by hand
            manual = {k: v for k, v in resp.units(dev).items() if v == "manual"}
            resp._dev(dev)["units"] = {**picks, **manual}
            written.append(dev)
        elif not resp.curated(dev):
            resp._dev(dev)["units"] = dict(picks)
            written.append(dev)
        else:
            kept.append(dev)
    resp.pending = {h: sorted(us) for h, us in sorted(pending.items())}
    resp.seeded_at = now_iso()
    resp.save(store.responsibility_path)
    if pending:  # leave a slot for each unmapped handle so the owner can fill it in
        for h in pending:
            if not any(k.lower() == h.lower() for k in cfg.handles):
                cfg.handles[h] = None
        store.save_config(cfg)
    unassigned = resp.unassigned(units)
    return SeedReport(
        source="codeowners+footprint" if co_path else "footprint",
        codeowners=str(co_path.relative_to(root)) if co_path else None,
        seeded=seeded,
        written=written,
        kept=kept,
        pending=resp.pending,
        unassigned=unassigned,
    )
