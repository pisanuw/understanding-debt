"""Risk of a change: weight landed in each unit against the author's understanding of it.

The rule is min over units. A change touching several units is as risky as the unit the
author understands least, and the weight it lands there (the commit's weight times the square
root of its line share under that unit) says how much that matters.
Verdicts are advisory; `--strict` turns HIGH into a non-zero exit for teams that opt in.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .provenance import Commit
from .scoring import UnitScore
from .weights import CommitWeight, heuristic_weight

HIGH_MASS, HIGH_UND = 0.3, 0.3
CAUTION_MASS = 0.1


@dataclass
class UnitRisk:
    unit_id: str
    name: str
    mass: float
    understanding: float
    source: str
    level: str  # OK | CAUTION | HIGH
    files: list[str]


def verdict(mass: float, understanding: float, known_threshold: float) -> str:
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
    )
    return c, heuristic_weight(c, eins + edels, set(added), set(), set())


def assess(
    units: list[dict[str, Any]],
    scores: dict[str, UnitScore],
    changes: list[Commit],
    weights: dict[str, CommitWeight],
    known_threshold: float,
) -> list[UnitRisk]:
    out = []
    for u in units:
        if u.get("scope") != "architectural":
            continue
        paths = u.get("paths", [])
        touched = [c for c in changes if c.touches(paths)]
        if not touched:
            continue
        mass = round(
            sum(weights[c.sha].weight * c.attribution(paths) for c in touched if c.sha in weights),
            3,
        )
        files = sorted(
            {
                f
                for c in touched
                for f in c.files
                if any(f == p or f.startswith(p.rstrip("/") + "/") for p in paths)
            }
        )
        s = scores[u["id"]]
        out.append(
            UnitRisk(
                u["id"],
                u["name"],
                mass,
                s.understanding,
                s.source,
                verdict(mass, s.understanding, known_threshold),
                files[:6],
            )
        )
    order = {"HIGH": 0, "CAUTION": 1, "OK": 2}
    return sorted(out, key=lambda r: (order[r.level], -r.mass))


def overall(risks: list[UnitRisk]) -> str:
    if any(r.level == "HIGH" for r in risks):
        return "HIGH"
    if any(r.level == "CAUTION" for r in risks):
        return "CAUTION"
    return "OK"


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
