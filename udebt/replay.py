"""Replay: understanding and debt at past points of the history (ROADMAP Phase 3).

For a series of points along the first-parent line (weekly from the first commit by
default, or daily, or every N units of weighted mass), every developer with a footprint,
a responsibility set or evidence is scored on every top-level unit exactly as `status`
scores them (quiz.compute_scores), with an explicit cut-off (ledger.Cutoff):

- only the commits that had landed on the first-parent line by then (a branch lands when
  its merge does), with the weights as computed today;
- only the evidence recorded by then;
- unit sizes from the tree at that point, for the share decay;
- **the units of the current map**, replayed against the past: units are defined now and
  history is replayed against them. A unit no commit had touched yet does not exist at that
  point and is left out of every index; importance is recomputed from the churn up to the
  point (mapping.set_importance), centrality is today's;
- responsibility sets and scope as they are now.

Each point also records the repository's mass so far by class (ai, assisted, human; bot
commits' weight beside them, though bots land none) and each developer's debt index over
their set. Points are kept in `.udebt/snapshots.json` (committed, one point per line) and
`udebt snapshot` only appends points newer than the last, so running it twice changes
nothing. A different map or decay model makes the stored series stale: `--rebuild`.

Risk events are the "high-debt member landed a heavy change" moments: every commit a
developer authored (or directed) with weight >= 0.1, assessed by risk.assess against that
developer's understanding just before it landed. They are kept in the same file and
extended the same way.
"""

from __future__ import annotations

import bisect
import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import repo, scoring
from . import units as hier
from .contributors import as_dict, contributors
from .ledger import Cutoff, Ledger, parse_ts
from .mapping import commits_for, set_importance, weights_for
from .provenance import Commit, landed
from .quiz import compute_scores
from .responsibility import FOOTPRINT_MIN_MASS, Responsibility
from .responsibility import load as load_responsibility
from .risk import CAUTION_MASS, ORDER, assess
from .sizes import open_counts, unit_sizes
from .store import Store, now_iso
from .weights import CommitWeight

NAME = "snapshots.json"
VERSION = 1
# the scoring rules a stored series was made with, beyond the settings the fingerprint names:
# 2 = Phase 3.1 (each commit's changed lines capped at its unit's size; no CAUTION event when
# the understanding is only the own-commit prior)
RULES = 2
EVERY = "week"
HEAVY = 0.5  # a commit this heavy by someone else is marked on that developer's line
LEVELS = ("OUTSIDE", "HIGH", "CAUTION")


# -- the history, loaded once ------------------------------------------------------------


@dataclass
class History:
    root: Path
    store: Store
    map: dict[str, Any]
    commits: list[Commit]
    weights: dict[str, CommitWeight]
    ledger: Ledger
    resp: Responsibility
    fp: list[str]  # first-parent line, oldest first
    fp_time: list[datetime]  # when each step landed (author time, never going back)
    arrival: tuple[int, ...]  # per commit (history order): the step it landed at
    records: dict[str, list[dict[str, Any]]]  # developer key -> evidence
    devs: list[str]
    _units: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    _sizes: dict[int, dict[str, float]] = field(default_factory=dict)

    @property
    def units(self) -> list[dict[str, Any]]:
        return self.map["units"]

    @property
    def cfg(self):
        return self.store.config

    def cutoff(self, step: int, when: datetime | None = None) -> Cutoff:
        return Cutoff(when or self.fp_time[step], self.fp[step], step, self.arrival)

    def units_at(self, step: int) -> list[dict[str, Any]]:
        """The current map's units with importance recomputed from the churn up to `step`."""
        got = self._units.get(step)
        if got is None:
            cut = self.cutoff(step)
            got = copy.deepcopy(self.units)
            churn = {
                u["id"]: sum(
                    landed(t.commit, self.weights) * t.att
                    for t in cut.touches(self.ledger.touches(u))
                )
                for u in hier.flat(got)
            }
            set_importance(got, churn)
            self._units[step] = got
        return got

    def existing(self, step: int) -> set[str]:
        """Top-level units some commit had touched by `step`."""
        cut = self.cutoff(step)
        return {u["id"] for u in self.units if cut.touches(self.ledger.touches(u))}

    def sizes_at(self, step: int) -> dict[str, float] | None:
        if self.cfg.decay.model != "share":
            return None
        got = self._sizes.get(step)
        if got is None:
            lines = open_counts(self.root, self.store.dir).at(self.fp[step])
            got = self._sizes[step] = unit_sizes(self.units, lines)
        return got

    def dev_of(self, c: Commit) -> str | None:
        """The developer behind a commit: its human author, or who directed the agent."""
        if c.klass in ("human", "assisted"):
            return self.cfg.primary(c.author_email)
        if c.klass == "ai" and c.directed_by:
            return self.cfg.primary(c.directed_by)
        return None

    def my_set(self, dev: str) -> set[str]:
        return hier.at_level(self.resp.ids(dev) & set(hier.index(self.units)), self.units, False)

    def scores(self, dev: str, step: int, when: datetime | None = None):
        return compute_scores(
            self.root,
            self.store,
            self.units_at(step),
            dev,
            self.records.get(dev, []),
            self.commits,
            self.weights,
            self.resp,
            self.sizes_at(step),
            ledger=self.ledger,
            cutoff=self.cutoff(step, when),
        )


def first_parent(commits: list[Commit], head: str) -> tuple[list[str], tuple[int, ...]]:
    """(first-parent line from the root to `head`, the step each commit landed at). A commit
    lands at the first step of the line that has it as an ancestor: a branch with its merge."""
    by_sha = {c.sha: c for c in commits}
    pos = {c.sha: i for i, c in enumerate(commits)}
    line: list[str] = []
    cur: str | None = head
    while cur in by_sha:
        line.append(cur)
        ps = by_sha[cur].parents
        cur = ps[0] if ps else None
    line.reverse()
    arrival = [-1] * len(commits)
    for step, sha in enumerate(line):
        stack = [sha]
        while stack:
            s = stack.pop()
            i = pos.get(s)
            if i is None or arrival[i] != -1:
                continue
            arrival[i] = step
            stack.extend(by_sha[s].parents)
    last = max(0, len(line) - 1)
    return line, tuple(a if a >= 0 else last for a in arrival)


def _dev_records(store: Store, cfg) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for slug, recs in store.all_records().items():
        email = next((r["dev"] for r in recs if r.get("dev")), slug)
        out.setdefault(cfg.primary(email), []).extend(recs)
    return out


def load(root: Path, store: Store) -> History:
    m = store.require_map()
    cfg = store.config
    commits = commits_for(root, store)
    if not commits:
        raise SystemExit("No commits to replay.")
    weights = weights_for(root, store, commits)
    line, arrival = first_parent(commits, repo.head(root))
    by_sha = {c.sha: c for c in commits}
    times: list[datetime] = []
    for sha in line:
        t = parse_ts(by_sha[sha].date).astimezone(UTC)
        times.append(max(t, times[-1]) if times else t)
    records = _dev_records(store, cfg)
    resp = load_responsibility(store)
    h = History(
        root, store, m, commits, weights, Ledger(commits, m["units"]), resp, line, times,
        arrival, records, [],
    )  # fmt: skip
    footprint: dict[str, float] = {}
    for c in commits:
        dev = h.dev_of(c)
        if dev is not None:
            share = 0.5 if c.klass == "ai" else 1.0
            footprint[dev] = footprint.get(dev, 0.0) + share * landed(c, weights)
    devs = {d for d, w in footprint.items() if w >= FOOTPRINT_MIN_MASS}
    devs |= set(records) | {d for d in resp.developers if resp.has_set(d)}
    h.devs = sorted(devs)
    return h


# -- points ------------------------------------------------------------------------------


def parse_every(every: str | None) -> tuple[str, float]:
    """`week` (default), `day`, or `mass:N`."""
    every = (every or EVERY).strip().lower()
    if every in ("week", "weekly", "day", "daily"):
        return ("week" if every.startswith("week") else "day"), 0.0
    if every.startswith("mass:"):
        n = float(every.split(":", 1)[1])
        if n <= 0:
            raise SystemExit("--every mass:N needs N > 0")
        return "mass", n
    raise SystemExit(f"--every takes week, day or mass:N, not {every!r}")


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).replace(microsecond=0).isoformat()


def plan(h: History, every: str | None = None) -> list[tuple[datetime, int]]:
    """(time, first-parent step) of every point, oldest first. Calendar intervals take the
    last step landed by each boundary (weekly boundaries are Mondays 00:00 UTC), skipping a
    boundary at which neither a commit nor evidence arrived; `mass:N` takes the step at
    which each further N units of weighted mass have landed. The last point is the latest
    activity: HEAD, or the newest evidence if that is later."""
    kind, n = parse_every(every)
    rec_times = sorted(parse_ts(r["ts"]) for rs in h.records.values() for r in rs if r.get("ts"))
    end = max([h.fp_time[-1], *rec_times[-1:]])
    out: list[tuple[datetime, int]] = []
    if kind == "mass":
        per_step = [0.0] * len(h.fp)
        for i, c in enumerate(h.commits):
            per_step[h.arrival[i]] += landed(c, h.weights, 0.0)
        cum, nxt = 0.0, n
        for s, m in enumerate(per_step):
            cum += m
            if cum >= nxt:
                out.append((h.fp_time[s], s))
                while cum >= nxt:
                    nxt += n
    else:
        delta = timedelta(days=7 if kind == "week" else 1)
        start = h.fp_time[0].astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        if kind == "week":
            start -= timedelta(days=start.weekday())
        b, last_step, prev_b = start + delta, None, start
        while b < end:
            step = bisect.bisect_right(h.fp_time, b) - 1
            new_evidence = bisect.bisect_right(rec_times, b) > bisect.bisect_right(
                rec_times, prev_b
            )
            if step >= 0 and (step != last_step or new_evidence):
                out.append((b, step))
                last_step = step
            prev_b, b = b, b + delta
    if not out or out[-1][0] < end:
        out.append((end, len(h.fp) - 1))
    return out


def snapshot_at(h: History, t: datetime, step: int) -> dict[str, Any]:
    """One point: the commits and mass so far by class, the units that existed with their
    importance and size, and per developer the understanding of every existing top-level unit,
    the debt each contributes to their index, and the index over their set."""
    units_t = h.units_at(step)
    existing = h.existing(step)
    sizes = h.sizes_at(step)
    cut = h.cutoff(step, t)
    mass = {"ai": 0.0, "assisted": 0.0, "human": 0.0, "bot": 0.0}
    n = 0
    for i, c in enumerate(h.commits):
        if not cut.has(i):
            continue
        n += 1
        w = h.weights.get(c.sha)
        mass[c.klass] += w.weight if w else 0.0
    devs: dict[str, Any] = {}
    for dev in h.devs:
        scores = h.scores(dev, step, t)
        top = {s.unit_id: s for s in scores if s.parent is None and s.unit_id in existing}
        mine = h.my_set(dev)
        ids = (mine & existing) if mine else set(existing)
        arch = [s for uid, s in top.items() if uid in ids and s.scope == "architectural"]
        total = sum(s.importance for s in arch)
        row: dict[str, Any] = {
            "index": scoring.debt_index(scores, only=ids) if arch else None,
            "und": {uid: round(s.understanding, 3) for uid, s in sorted(top.items())},
            "debt": {s.unit_id: round(100 * s.debt / total, 2) for s in arch if total},
        }
        quizzed = sorted(uid for uid, s in top.items() if s.source in ("quiz", "children"))
        if quizzed:
            row["quizzed"] = quizzed
        devs[dev] = row
    return {
        "t": _iso(t),
        "sha": h.fp[step],
        "commits": n,
        "mass": {k: round(v, 2) for k, v in mass.items()},
        "units": {
            u["id"]: [u["importance"], sizes.get(u["id"]) if sizes else None]
            for u in units_t
            if u["id"] in existing
        },
        "devs": devs,
    }


# -- risk events -------------------------------------------------------------------------


def risk_events(h: History, after_step: int = -1) -> list[dict[str, Any]]:
    """Every commit a developer authored or directed, landed after `after_step` and weighing
    at least CAUTION_MASS, assessed by risk.assess against that developer's understanding at
    the step before it landed, with the evidence recorded before its author time. One event
    per unit whose level is not OK."""
    out: list[dict[str, Any]] = []
    devs = set(h.devs)
    memo: dict[tuple[str, int, int], dict[str, scoring.UnitScore]] = {}
    threshold = h.cfg.known_threshold
    for i, c in enumerate(h.commits):
        step = h.arrival[i]
        dev = h.dev_of(c)
        if step <= after_step or step == 0 or dev not in devs:
            continue
        w = landed(c, h.weights, 0.0)
        if w < CAUTION_MASS or not c.files:
            continue
        when = parse_ts(c.date)
        prev = step - 1
        seen = sum(1 for r in h.records.get(dev, []) if r.get("ts") and parse_ts(r["ts"]) <= when)
        key = (dev, prev, seen)
        if key not in memo:
            memo[key] = {s.unit_id: s for s in h.scores(dev, prev, when)}
        mine = h.my_set(dev)
        for r in assess(
            h.units_at(prev),
            memo[key],
            [c],
            h.weights,
            threshold,
            responsible=mine or None,
        ):
            if r.level == "OK":
                continue
            out.append(
                {
                    "sha": c.sha,
                    "date": c.date,
                    "landed": _iso(h.fp_time[step]),
                    "dev": dev,
                    "klass": c.klass,
                    "subject": c.subject[:100],
                    "weight": round(w, 3),
                    "unit": r.unit_id,
                    "level": r.level,
                    "mass": r.mass,
                    "understanding": round(r.understanding, 3),
                    "source": r.source,
                    "in_set": r.in_set,
                }
            )
    return out


def interesting(events: list[dict[str, Any]], n: int | None = None) -> list[dict[str, Any]]:
    """Strongest first: OUTSIDE, then HIGH, then CAUTION; within a level the mass landed
    times what the developer did not understand."""
    ranked = sorted(
        events, key=lambda e: (ORDER[e["level"]], -e["mass"] * (1 - e["understanding"]))
    )
    return ranked[:n] if n else ranked


# -- the file ----------------------------------------------------------------------------


def fingerprint(h: History, every: str) -> dict[str, Any]:
    """What the stored series depends on; a change makes it stale."""
    d = h.cfg.decay
    units = json.dumps(
        [[u["id"], u.get("paths", []), u.get("centrality", 3)] for u in hier.flat(h.units)]
    )
    return {
        "every": every,
        "map": h.map.get("generated_at"),
        "units": hashlib.sha1(units.encode()).hexdigest()[:12],
        "decay": [d.model, d.rate, d.per_mass],
        "window": h.cfg.evidence_window,
        "rules": RULES,
    }


def path(store: Store) -> Path:
    return store.dir / NAME


def read(store: Store) -> dict[str, Any] | None:
    p = path(store)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("version") == VERSION else None


def write(store: Store, data: dict[str, Any]) -> None:
    """Compact JSON, one point and one risk event per line, so a commit of the file is an
    append in its diff."""
    head = {k: v for k, v in data.items() if k not in ("points", "risk")}
    lines = ["{"]
    for k, v in head.items():
        lines.append(f"{json.dumps(k)}: {json.dumps(v, separators=(',', ':'))},")
    for key, rows in (("points", data["points"]), ("risk", data["risk"]["events"])):
        body = ",\n".join(json.dumps(r, separators=(",", ":"), sort_keys=True) for r in rows)
        if key == "points":
            lines.append(f'"points": [\n{body}\n],' if rows else '"points": [],')
        else:
            through = json.dumps(data["risk"]["through"])
            ev = f'"events": [\n{body}\n]' if rows else '"events": []'
            lines.append(f'"risk": {{"through": {through},\n{ev}}}')
    lines.append("}")
    p = path(store)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass
class Result:
    data: dict[str, Any]
    appended: int = 0
    events_added: int = 0
    note: str | None = None
    written: bool = False


def ensure(
    h: History, every: str | None = None, *, rebuild: bool = False, save: bool = True
) -> Result:
    """The stored series brought up to date: points newer than the last one are appended,
    risk events for commits landed since are added. Idempotent. A series made with another
    map, decay model or interval, or whose last point is no longer on the first-parent
    line, is kept as it is with a note, unless `rebuild`."""
    old = None if rebuild else read(h.store)
    every = (
        every or (old or {}).get("fingerprint", {}).get("every") or h.cfg.snapshot_every or EVERY
    )
    parse_every(every)
    fp = fingerprint(h, every)
    if old is not None:
        stale = None
        if old.get("fingerprint") != fp:
            stale = "made with another map, decay model, interval or scoring rules"
        elif old["points"] and old["points"][-1]["sha"] not in set(h.fp):
            stale = "its last point is no longer on the first-parent line (history rewritten)"
        if stale:
            note = f"{path(h.store).name} was {stale}; `udebt snapshot --rebuild` recomputes it"
            return Result(old, note=note)
    data = old or {
        "version": VERSION,
        "repo": h.root.name,
        "fingerprint": fp,
        "points": [],
        "risk": {"through": None, "events": []},
    }
    last = parse_ts(data["points"][-1]["t"]) if data["points"] else None
    new = [snapshot_at(h, t, s) for t, s in plan(h, every) if last is None or t > last]
    data["points"].extend(new)
    through = data["risk"]["through"]
    after = h.fp.index(through) if through in h.fp else -1
    events = risk_events(h, after)
    data["risk"]["events"].extend(events)
    data["risk"]["through"] = h.fp[-1]
    data["devs"] = h.devs
    data["head"] = h.fp[-1]
    data["updated_at"] = now_iso() if (new or events or old is None) else data.get("updated_at")
    res = Result(data, len(new), len(events))
    if save and (new or events or old is None or through != h.fp[-1]):
        write(h.store, data)
        res.written = True
    return res


# -- what the dashboard and the export read --------------------------------------------


def evidence(h: History, with_answers: bool = False) -> list[dict[str, Any]]:
    """Every evidence record, oldest first, without answer text (the answer, the grader's
    feedback and the reference answer) unless `with_answers`."""
    out = []
    for dev, recs in h.records.items():
        for r in recs:
            kind = r.get("source") or (
                "dig" if r.get("reference") == "(graded against the code)" else r.get("type")
            )
            row = {
                "ts": r.get("ts"),
                "dev": dev,
                "unit": r.get("unit"),
                "type": kind or "quiz",
                "verdict": r.get("verdict"),
                "confidence": r.get("confidence"),
                "evidence": scoring.evidence(r),
                "head": r.get("head"),
                "question": r.get("question"),
            }
            if with_answers:
                row.update(
                    answer=r.get("answer"), feedback=r.get("feedback"), reference=r.get("reference")
                )
            out.append(row)
    return sorted(out, key=lambda r: r["ts"] or "")


def heavy_commits(h: History, min_weight: float = HEAVY) -> list[dict[str, Any]]:
    """Commits weighing at least `min_weight`, with who made them and the top-level units
    they landed most on: the steps in a developer's line that someone else caused."""
    members = hier.Membership(h.units)
    out = []
    for i, c in enumerate(h.commits):
        w = landed(c, h.weights, 0.0)
        if w < min_weight:
            continue
        units = {}
        for u in h.units:
            if c.touches(u.get("paths", [])):
                m = w * c.attribution(u.get("paths", []), split=members.split(u["id"]))
                if m >= 0.05:
                    units[u["id"]] = round(m, 2)
        out.append(
            {
                "sha": c.sha,
                "date": c.date,
                "landed": _iso(h.fp_time[h.arrival[i]]),
                "dev": h.dev_of(c),
                "author": c.author_name,
                "klass": c.klass,
                "agent": c.agent,
                "weight": round(w, 3),
                "subject": c.subject[:100],
                "units": units,
            }
        )
    return out


def unit_table(h: History, last: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The units as they stand: importance, ai%, each developer's you% and understanding,
    holders against the bus target, and who has the unit in their set."""
    cfg = h.cfg
    from .provenance import unit_provenance

    members = hier.Membership(h.units)
    rows = []
    devs = (last or {}).get("devs", {})
    for u, parent in hier.walk(h.units):
        prov = unit_provenance(
            h.commits, u.get("paths", []), None, h.weights, members.split(u["id"])
        )
        you = {}
        for dev in h.devs:
            p = unit_provenance(
                h.commits,
                u.get("paths", []),
                cfg.emails_for(dev),
                h.weights,
                members.split(u["id"]),
            )
            if p.dev_share > 0:
                you[dev] = round(p.dev_share, 3)
        und = {d: row["und"][u["id"]] for d, row in devs.items() if u["id"] in row.get("und", {})}
        rows.append(
            {
                "id": u["id"],
                "name": u.get("name", u["id"]),
                "kind": u.get("kind"),
                "parent": parent["id"] if parent else None,
                "scope": u.get("scope", "architectural"),
                "importance": u.get("importance"),
                "ai_share": round(prov.ai_share, 3),
                "you": you,
                "understanding": und,
                "holders": sorted(d for d, x in und.items() if x >= cfg.known_threshold),
                "responsible": h.resp.holders_of(u["id"], h.units),
            }
        )
    return rows


def export_data(
    h: History, res: Result, *, with_answers: bool = False, risk_levels=LEVELS
) -> dict[str, Any]:
    """Everything the dashboard draws and the hosted import will read, as one JSON-able
    dict: settings, the units, the contributors, the snapshots, the evidence, the risk events
    and the heavy commits."""
    cfg = h.cfg
    data = res.data
    points = data["points"]
    cons = contributors(h.commits, cfg.identities, set())
    d = cfg.decay
    return {
        "format": "udebt-export",
        "version": 1,
        "repo": h.root.name,
        "head": h.fp[-1],
        "generated_at": now_iso(),
        "settings": {
            "every": data["fingerprint"]["every"],
            "decay": {"model": d.model, "rate": d.rate, "per_mass": d.per_mass},
            "known_threshold": cfg.known_threshold,
            "bus_target": cfg.bus_target,
            "heavy": HEAVY,
            "units_replayed_as_defined_now": True,
        },
        "devs": h.devs,
        "units": unit_table(h, points[-1] if points else None),
        "responsibility": {dev: sorted(h.my_set(dev)) for dev in h.devs if h.my_set(dev)},
        "contributors": [
            {k: v for k, v in as_dict(c).items() if k != "you"}
            | {"key": cfg.primary(c.emails[0]) if c.emails else None}
            for c in cons
        ],
        "snapshots": points,
        "evidence": evidence(h, with_answers),
        "risk_events": [e for e in data["risk"]["events"] if e["level"] in risk_levels],
        "heavy_commits": heavy_commits(h),
    }
