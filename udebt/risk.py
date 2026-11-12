"""Risk of a change: weight landed in each unit against the author's understanding of it.

The rule is min over units. A change touching several units is as risky as the unit the
author understands least, and the weight it lands there (the commit's weight times the square
root of its line share under that unit) says how much that matters.

With responsibility sets, the strongest signal is OUTSIDE: the change lands real weight in a
unit outside the author's set that they do not understand, so nobody expected them to know it
and nothing says they do. Levels, strongest first: OUTSIDE, HIGH, CAUTION, OK.

CAUTION is not raised where the author's understanding is only the own-commit prior: the
prior stops at 0.5, under the 0.6 threshold, so every change a sole developer makes to their
own code would be CAUTION (308 of digitalme's 312 replayed events). Such a unit is OK and
`muted`; `udebt risk` names it in one line. HIGH and OUTSIDE still fire on a prior.
Verdicts are advisory; `--strict` turns OUTSIDE and HIGH into a non-zero exit for teams that
opt in.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import units as hier
from .filekinds import VERSION_PATHSPECS, version_candidate
from .provenance import Commit, _under, landed
from .scoring import UnitScore
from .weights import CommitWeight, heuristic_weight, parse_version_diff, toml_diff_opts

HIGH_MASS, HIGH_UND = 0.3, 0.3
CAUTION_MASS = 0.1


@dataclass
class UnitRisk:
    unit_id: str
    name: str
    mass: float
    understanding: float
    source: str
    level: str  # OK | CAUTION | HIGH | OUTSIDE
    files: list[str]
    in_set: bool | None = None  # None: the developer has no responsibility set
    parts: list[str] | None = None  # sub-units of a top-level unit that the change touched
    muted: bool = False  # CAUTION from the own-commit prior alone: reported OK, named apart


ORDER = {"OUTSIDE": 0, "HIGH": 1, "CAUTION": 2, "OK": 3}


def verdict(
    mass: float, understanding: float, known_threshold: float, in_set: bool | None = None
) -> str:
    if in_set is False and mass >= CAUTION_MASS and understanding < known_threshold:
        return "OUTSIDE"
    if mass >= HIGH_MASS and understanding < HIGH_UND:
        return "HIGH"
    if mass >= CAUTION_MASS and understanding < known_threshold:
        return "CAUTION"
    return "OK"


def commits_in_range(root: Path, rev_range: str, commits: list[Commit]) -> list[Commit]:
    out = subprocess.run(
        ["git", "rev-list", rev_range], cwd=root, capture_output=True, text=True, encoding="utf-8"
    )
    if out.returncode != 0:
        raise SystemExit(f"git rev-list {rev_range}: {out.stderr.strip()}")
    shas = set(out.stdout.split())
    return [c for c in commits if c.sha in shas]


def staged_commit(root: Path) -> tuple[Commit, CommitWeight]:
    """The staged diff as a pseudo-commit with a heuristic weight."""

    def numstat(*extra: str) -> tuple[list[str], int, int]:
        out = subprocess.run(
            ["git", "diff", "--cached", "--numstat", *extra],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
        files, ins, dels, per_file = [], 0, 0, {}
        for ln in out.splitlines():
            m = re.match(r"^(\d+|-)\t(\d+|-)\t(.+)$", ln)
            if m:
                files.append(m.group(3))
                a = int(m.group(1)) if m.group(1).isdigit() else 0
                d = int(m.group(2)) if m.group(2).isdigit() else 0
                ins += a
                dels += d
                per_file[m.group(3)] = a + d
        return files, ins, dels, per_file

    files, ins, dels, per_file = numstat()
    _, eins, edels, _ = numstat("-w", "--ignore-blank-lines")
    added = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=A"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    diff = subprocess.run(
        ["git", *toml_diff_opts(), "diff", "--cached", "-p", "-U0", "--no-renames"]
        + ["--no-ext-diff", "--no-color", "--", *VERSION_PATHSPECS],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    bumped = {f for f, ok in parse_version_diff(diff).items() if ok and version_candidate(f)}
    c = Commit(
        sha="staged",
        date="",
        author_name="",
        author_email="",
        subject="(staged)",
        body="",
        files=files,
        insertions=ins,
        deletions=dels,
        file_lines=per_file,
        version_files=bumped,
    )
    return c, heuristic_weight(c, eins + edels, set(added), set(), set(), bumped)


def assess(
    units: list[dict[str, Any]],
    scores: dict[str, UnitScore],
    changes: list[Commit],
    weights: dict[str, CommitWeight],
    known_threshold: float,
    *,
    responsible: set[str] | None = None,
    hierarchy: list[dict[str, Any]] | None = None,
    deep: bool = False,
    mute_prior: bool = True,
) -> list[UnitRisk]:
    """Risk per unit that the changes touch. Top level by default; with `deep`, per sub-unit,
    plus a `<parent> (rest)` row for files only the parent covers, so nothing drops out of
    view. `responsible` is the developer's set (None when they have none); `hierarchy` is
    the whole map, needed to tell whether a unit is in the set through its parent or a
    touched sub-unit. With `mute_prior`, a CAUTION whose understanding is only the
    own-commit prior is OK and `muted`."""
    hierarchy = hierarchy if hierarchy is not None else units
    members = hier.Membership(hierarchy)
    rows: list[tuple[dict[str, Any], list[str]]] = []  # (unit, paths to exclude)
    for u in units:
        kids = hier.children_of(u)
        if deep and kids:
            rows += [(k, []) for k in kids]
            rows.append((u, [p for k in kids for p in k.get("paths", [])]))
        else:
            rows.append((u, []))
    out = []
    for u, exclude in rows:
        s = scores[u["id"]]
        if s.scope != "architectural":
            continue
        paths = u.get("paths", [])
        files = sorted(
            {
                f
                for c in changes
                for f in c.files
                if _under(f, paths) and not (exclude and _under(f, exclude))
            }
        )
        if not files:
            continue
        touched = [c for c in changes if any(f in files for f in c.files)]
        mass = round(
            sum(
                landed(c, weights) * c.attribution(paths, exclude or None, members.split(u["id"]))
                for c in touched
                if c.sha in weights
            ),
            3,
        )
        parts = [
            k for k in hier.children_of(u) if any(c.touches(k.get("paths", [])) for c in touched)
        ]
        in_set = None
        if responsible is not None:
            in_set = hier.covers(responsible, u["id"], hierarchy) or (
                not exclude and any(k["id"] in responsible for k in parts)
            )
        level = verdict(mass, s.understanding, known_threshold, in_set)
        muted = mute_prior and level == "CAUTION" and s.source == "prior"
        out.append(
            UnitRisk(
                u["id"] + (" (rest)" if exclude else ""),
                u["name"],
                mass,
                s.understanding,
                s.source,
                "OK" if muted else level,
                files[:6],
                in_set,
                None if exclude else ([k["id"].split("/", 1)[1] for k in parts] or None),
                muted,
            )
        )
    return sorted(out, key=lambda r: (ORDER[r.level], -r.mass))


def overall(risks: list[UnitRisk]) -> str:
    return min((r.level for r in risks), key=ORDER.__getitem__, default="OK")


def touched_units(units: list[dict[str, Any]], files: list[str]) -> set[str]:
    """Ids of the units in `units` whose paths cover any of `files`."""
    return {u["id"] for u in units if any(_under(f, u.get("paths", [])) for f in files)}


def working_files(root: Path) -> list[str]:
    """Files the developer is touching right now: uncommitted changes (staged or not) and
    commits not yet on the upstream branch. Empty when git cannot tell."""
    out: set[str] = set()
    for args in (["diff", "--name-only", "HEAD"], ["diff", "--name-only", "@{upstream}...HEAD"]):
        p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
        if p.returncode == 0:
            out |= {ln for ln in p.stdout.splitlines() if ln}
    return sorted(out)


PRE_PUSH_HOOK = """#!/bin/sh
# udebt advisory pre-push hook: warns about heavy changes to units you understand little.
# Never blocks. Remove this file or run `git push --no-verify` to skip.
command -v udebt >/dev/null 2>&1 || exit 0
zero=0000000000000000000000000000000000000000
while read local_ref local_sha remote_ref remote_sha; do
  [ "$local_sha" = "$zero" ] && continue
  if [ "$remote_sha" = "$zero" ]; then
    range="$local_sha --not --remotes"
  else
    range="$remote_sha..$local_sha"
  fi
  udebt risk $range || true
done
exit 0
"""
