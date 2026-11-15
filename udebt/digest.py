"""Change digests (Phase 3.4, `udebt digest`): a short briefing, per unit a developer
understands below the threshold, of what landed there since their last evidence on it.

For each unit it lists the commits in the range (who made them, their class and weight, the
mass each landed in the unit) and asks the model, in one call for every unit of the run, what
changed in terms of the unit's decision and whether the decision itself moved (`held`,
`extended` or `changed`). The framing is a briefing for a colleague coming back to that part
of the system, never a warning; it ends with the next step (a quiz or a walkthrough).

The range of a unit runs from a start commit (exclusive) to HEAD:

- `--since <rev|date>`: that commit, or the last first-parent commit at or before that date;
- else the head of the developer's latest evidence on the unit (`udebt quiz`, `dig`, ...);
- else, with no evidence there, the last `DAYS` (30) days before HEAD's commit date, since
  "everything since the first commit" is the orientation's job, not a digest's.

Briefings are cached per (unit, start sha, end sha) in `.udebt/digests/` (gitignored), shared
by every developer whose range is the same; a unit with nothing landed in its range costs no
call. Only the model's briefing needs the call: the commits come from the parsed history.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from . import orientation as orient
from . import repo, scoring
from . import units as hier
from .filekinds import mechanical
from .ledger import parse_ts
from .llm import LLM
from .prompts import DIGEST_SCHEMA, digest_prompt
from .provenance import Commit, _under, landed
from .store import Store, now_iso

DIR = "digests"
DAYS = 30
MAX_COMMITS = 15  # commits per unit shown to the model (the heaviest); the rest are counted
DIFF_LINES = 160  # lines of diff per unit shown to the model
DECISION = ("held", "extended", "changed")


@dataclass
class Range:
    start: str | None  # the commit after which the range begins (None: from the first commit)
    end: str  # HEAD
    how: str  # "since" | "evidence" | "days"
    note: str  # one line on why the range starts there


@dataclass
class Item:
    unit: dict[str, Any]
    score: scoring.UnitScore | None
    range: Range
    commits: list[dict[str, Any]] = field(default_factory=list)
    mass: float = 0.0
    digest: dict[str, Any] | None = None  # the cached or new briefing
    cached: bool = False

    def as_dict(self) -> dict[str, Any]:
        s = self.score
        return {
            "unit": self.unit["id"],
            "name": self.unit.get("name", self.unit["id"]),
            "understanding": None if s is None else round(s.understanding, 3),
            "basis": None if s is None else s.basis,
            "depth": None if s is None else s.depth,
            "range": {
                "start": self.range.start,
                "end": self.range.end,
                "how": self.range.how,
                "note": self.range.note,
            },
            "commits": self.commits,
            "mass": round(self.mass, 3),
            "digest": self.digest,
            "cached": self.cached,
        }


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def _date(sha: str | None, by_sha: dict[str, Commit]) -> str:
    c = by_sha.get(sha or "")
    return c.date[:10] if c else ""


def resolve_since(root: Path, since: str) -> str | None:
    """`since` as a commit sha: a revision, else a date (the last first-parent commit at or
    before it; None when the date is before the first commit)."""
    p = _git(root, "rev-parse", "--verify", "--quiet", f"{since}^{{commit}}")
    if p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}", since.strip()):
        raise SystemExit(f"--since takes a revision or a date (YYYY-MM-DD), not {since!r}")
    p = _git(root, "rev-list", "-1", "--first-parent", f"--before={since.strip()}", "HEAD")
    return p.stdout.strip() or None


def days_start(root: Path, commits: list[Commit], days: int = DAYS) -> str | None:
    """The last first-parent commit `days` days before HEAD's commit date."""
    head = repo.head(root)
    c = next((c for c in commits if c.sha == head), None)
    if c is None:
        return None
    cut = parse_ts(c.date) - timedelta(days=days)
    p = _git(root, "rev-list", "-1", "--first-parent", f"--before={cut.isoformat()}", "HEAD")
    return p.stdout.strip() or None


def range_for(
    root: Path,
    records_on_unit: list[dict[str, Any]],
    commits: list[Commit],
    *,
    since: str | None = None,
    since_sha: str | None = None,
    days: int = DAYS,
) -> Range:
    """Where a unit's digest starts for this developer (the rules in the module docstring).
    `since_sha` is `since` already resolved, so a run resolves it once."""
    head = repo.head(root)
    by_sha = {c.sha: c for c in commits}
    if since is not None:
        what = f"since {since}" + (f" ({since_sha[:8]})" if since_sha else " (the first commit)")
        return Range(since_sha, head, "since", what)
    last = scoring.last_record_head(records_on_unit)
    if last and last in by_sha:
        return Range(last, head, "evidence", f"since your last answer here ({_date(last, by_sha)})")
    start = days_start(root, commits, days)
    gone = " (your last answer's commit is no longer in the history)" if last else ""
    return Range(start, head, "days", f"the last {days} days before HEAD{gone}")


def _in_range(root: Path, r: Range) -> set[str]:
    spec = [f"{r.start}..{r.end}"] if r.start else [r.end]
    p = _git(root, "rev-list", *spec)
    return set(p.stdout.split()) if p.returncode == 0 else set()


def landed_in(
    commits: list[Commit],
    weights: dict[str, Any],
    unit: dict[str, Any],
    members: hier.Membership,
    shas: set[str],
) -> tuple[list[dict[str, Any]], float]:
    """The commits of the range that touch the unit, oldest first, each with who made it, its
    class, weight and the mass it landed in the unit; bots, which land nothing, are left
    out. And the total mass."""
    paths = unit.get("paths", [])
    split = members.split(unit["id"])
    rows, total = [], 0.0
    for c in commits:
        if c.sha not in shas or c.klass == "bot" or not c.touches(paths):
            continue
        w = landed(c, weights, 0.0)
        m = w * c.attribution(paths, split=split)
        total += m
        files = sorted(
            ((f, n) for f, n in c.file_lines.items() if f in c.files and _under(f, paths)),
            key=lambda fn: -fn[1],
        )
        who = c.author_name or c.author_email
        if c.klass == "ai":
            who = f"{c.agent or 'an agent'}" + (f" for {c.directed_by}" if c.directed_by else "")
        rows.append(
            {
                "sha": c.sha,
                "date": c.date[:10],
                "who": who,
                "klass": c.klass,
                "agent": c.agent,
                "weight": round(w, 3),
                "mass": round(m, 3),
                "subject": c.subject[:120],
                "files": [f for f, _ in files[:4]],
            }
        )
    return rows, total


def diff_excerpt(root: Path, r: Range, paths: list[str], limit: int = DIFF_LINES) -> str:
    """The range's diff stat and the first `limit` lines of its diff under `paths`, without
    lockfiles and generated files. Empty from the first commit (the stat would be the whole
    unit: the code itself is there to read)."""
    if not r.start or not paths:
        return ""
    files = _git(root, "diff", "--name-only", f"{r.start}..{r.end}", "--", *paths).stdout.split()
    files = [f for f in files if not mechanical(f)]
    if not files:
        return ""
    stat = _git(root, "diff", "--stat=100", f"{r.start}..{r.end}", "--", *files).stdout
    patch = _git(
        root, "diff", "--no-color", "--no-ext-diff", "-U1", f"{r.start}..{r.end}", "--", *files
    ).stdout.splitlines()
    cut = len(patch) - limit
    body = "\n".join(patch[:limit]) + (f"\n... ({cut} more diff lines)" if cut > 0 else "")
    return stat.rstrip() + "\n" + body


def _file(unit_id: str, r: Range) -> str:
    uid = re.sub(r"[^A-Za-z0-9_.-]+", "--", unit_id)
    return f"{uid}--{(r.start or 'root')[:12]}--{r.end[:12]}.json"


def cached(udebt_dir: Path, unit: dict[str, Any], r: Range) -> dict[str, Any] | None:
    p = udebt_dir / DIR / _file(unit["id"], r)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("basis") != orient.basis(unit) or not isinstance(data.get("brief"), dict):
        return None
    return data["brief"]


def _save(udebt_dir: Path, it: Item) -> None:
    from .cache import ensure_gitignore

    d = udebt_dir / DIR
    d.mkdir(parents=True, exist_ok=True)
    ensure_gitignore(udebt_dir)
    data = {
        "version": 1,
        "unit": it.unit["id"],
        "start": it.range.start,
        "end": it.range.end,
        "basis": orient.basis(it.unit),
        "generated_at": now_iso(),
        "commits": [c["sha"] for c in it.commits],
        "brief": it.digest,
    }
    (d / _file(it.unit["id"], it.range)).write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def plan(
    root: Path,
    store: Store,
    units: list[dict[str, Any]],
    picked: list[dict[str, Any]],
    scores: dict[str, scoring.UnitScore],
    records: list[dict[str, Any]],
    commits: list[Commit],
    weights: dict[str, Any],
    *,
    since: str | None = None,
    days: int = DAYS,
) -> list[Item]:
    """The digest items for `picked` units: range, commits and any cached briefing (no call)."""
    members = hier.Membership(units)
    since_sha = resolve_since(root, since) if since is not None else None
    ranges: dict[tuple[str | None, str], set[str]] = {}
    out = []
    for u in picked:
        on = [r for r in records if r.get("unit") == u["id"]]
        r = range_for(root, on, commits, since=since, since_sha=since_sha, days=days)
        key = (r.start, r.end)
        if key not in ranges:
            ranges[key] = _in_range(root, r)
        rows, mass = landed_in(commits, weights, u, members, ranges[key])
        it = Item(u, scores.get(u["id"]), r, rows, mass)
        if rows:
            it.digest = cached(store.dir, u, r)
            it.cached = it.digest is not None
        out.append(it)
    return out


def ensure(root: Path, store: Store, llm: LLM, items: list[Item]) -> int:
    """Write the missing briefings, all in one call, and cache them. Returns the number of
    calls made (0 or 1)."""
    todo = [it for it in items if it.commits and it.digest is None]
    if not todo:
        return 0
    blocks = []
    for it in todo:
        top = sorted(it.commits, key=lambda c: -c["mass"])[:MAX_COMMITS]
        shown = [c for c in it.commits if c in top]
        blocks.append(
            {
                "unit": it.unit,
                "range": it.range,
                "commits": shown,
                "more": len(it.commits) - len(shown),
                "diff": diff_excerpt(root, it.range, it.unit.get("paths", [])),
            }
        )
    res = llm.structured(digest_prompt(blocks), DIGEST_SCHEMA, cwd=root, read_tools=True,
                         max_turns=60)  # fmt: skip
    got = {d.get("unit_id"): d for d in res.get("digests", []) if isinstance(d, dict)}
    for it in todo:
        d = got.get(it.unit["id"])
        if not d:
            continue
        shas = {c["sha"][:7] for c in it.commits}
        moved = d.get("decision") if d.get("decision") in DECISION else "held"
        it.digest = {
            "headline": " ".join(str(d.get("headline", "")).split()),
            "changes": [
                {
                    "what": " ".join(str(ch.get("what", "")).split()),
                    "commits": [s[:7] for s in ch.get("commits") or [] if str(s)[:7] in shas],
                }
                for ch in d.get("changes") or []
                if isinstance(ch, dict) and str(ch.get("what", "")).strip()
            ][:4],
            "decision": moved,
            "decision_note": " ".join(str(d.get("decision_note", "")).split()),
            "look_at": [str(p) for p in d.get("look_at") or []][:3],
        }
        _save(store.dir, it)
    return 1


def pick(
    units: list[dict[str, Any]],
    scores: dict[str, scoring.UnitScore],
    threshold: float,
    *,
    only: str | None = None,
    within: set[str] | None = None,
) -> list[dict[str, Any]]:
    """The units a digest covers: `only` (any unit, any understanding), else the top-level
    architectural units (in `within`, the developer's set, when given) understood below
    `threshold`, the most debt first."""
    if only:
        u = hier.resolve(units, only)
        if u is None:
            raise SystemExit(f"Unknown unit id: {only}")
        return [u]
    pool = [u for u in units if within is None or u["id"] in within]
    pool = [
        u
        for u in pool
        if scores[u["id"]].scope == "architectural" and scores[u["id"]].understanding < threshold
    ]
    return sorted(pool, key=lambda u: -scores[u["id"]].debt)


DECISION_WORDS = {
    "held": "held: the decision stands; the changes work within it",
    "extended": "extended: the decision stands and now covers more",
    "changed": "changed: the decision itself moved",
}


def render(it: Item, by_sha: dict[str, Commit] | None = None, width: int = 92) -> str:
    """One unit's digest for the terminal."""
    import textwrap

    s = it.score
    head = f"== {it.unit.get('name', it.unit['id'])} (`{it.unit['id']}`)"
    if s is not None:
        head += f": understanding {s.understanding:.2f} ({s.basis})"
    start = (it.range.start or "")[:8] or "the first commit"
    lines = [head, f"   {it.range.note}: {start}..{it.range.end[:8]}"]
    if not it.commits:
        lines.append("   Nothing landed here in that range: what you knew then still holds.")
        return "\n".join(lines)
    lines.append(f"   {len(it.commits)} commit(s), mass {it.mass:.2f} in the unit:")
    for c in sorted(it.commits, key=lambda c: -c["mass"])[:8]:
        cls = c["klass"] + (f"/{c['agent']}" if c.get("agent") else "")
        lines.append(
            f"     {c['date']} {c['sha'][:8]} {c['who'][:22]:<22} {cls[:18]:<18} "
            f"w {c['weight']:.2f} m {c['mass']:.2f}  {c['subject'][:60]}"
        )
    if len(it.commits) > 8:
        lines.append(f"     ... and {len(it.commits) - 8} lighter commit(s)")
    d = it.digest
    if not d:
        lines.append("   (No briefing: the model's reply missed this unit.)")
        return "\n".join(lines)

    def wrap(text: str, first: str, rest: str = "     ") -> list[str]:
        return textwrap.wrap(text, width, initial_indent=first, subsequent_indent=rest)

    lines += wrap(d["headline"], "   In brief: ")
    for ch in d["changes"]:
        cite = f" ({', '.join(ch['commits'])})" if ch["commits"] else ""
        lines += wrap(ch["what"] + cite, "   - ")
    lines += wrap(f"{DECISION_WORDS[d['decision']]}. {d['decision_note']}".strip(), "   Decision ")
    if d["look_at"]:
        lines.append(f"   Look at: {', '.join(d['look_at'])}")
    lines.append(
        f"   Then: `udebt dig {it.unit['id']}` to see the code, or `udebt quiz --unit "
        f"{it.unit['id']}` to show what you know."
    )
    return "\n".join(lines)
