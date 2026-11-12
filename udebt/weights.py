"""How much understanding debt a commit can create: a weight in [0, 1] per commit.

Not all commits are equal. A whitespace reformat of 200 files or a lockfile bump teaches
nothing and so cannot put anyone in debt; a new dependency, a schema change or a large
insert-heavy change across several directories can. The weight scales everything that used
to be a commit count: churn, decay, provenance shares and the own-commit prior.

The weight is heuristic from cheap git signals (one extra `git log -w` pass gives the
whitespace-ignored line counts for every commit). `refine()` lets an LLM re-classify the
heaviest commits; results are cached in .udebt/commits.json so the cost is paid once.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .history import INFRA, KEYWORDS, MANIFESTS, _toplevel
from .provenance import Commit

LOCK = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock|Pipfile\.lock|"
    r"Cargo\.lock|go\.sum|composer\.lock|Gemfile\.lock|flake\.lock|[^/]*\.lock)$"
)
GENERATED = re.compile(
    r"(^|/)(dist|build|out|target|node_modules|__pycache__|\.next|coverage|__snapshots__)/|"
    r"\.(min\.js|min\.css|map|pyc|snap|ipynb_checkpoints)$|(^|/)[^/]*\.generated\.[^/]+$"
)
DOCS = re.compile(r"(^|/)(docs?|doc)/|\.(md|rst|txt|adoc)$|(^|/)(LICENSE|CHANGELOG|README)[^/]*$")
TESTS = re.compile(r"(^|/)(tests?|__tests__|spec|e2e)/|(^|/)test_[^/]+$|[._](test|spec)\.[a-z]+$")
MEDIA = re.compile(r"\.(png|jpe?g|gif|svg|ico|webp|mp3|mp4|wav|pdf|woff2?|ttf)$")

# LLM refinement classes and the weight each implies.
CLASS_WEIGHT = {"architectural": 1.0, "behavioural": 0.6, "refactor": 0.3, "cosmetic": 0.05}


@dataclass
class CommitWeight:
    sha: str
    weight: float
    reasons: list[str] = field(default_factory=list)
    effective_lines: int = 0  # whitespace-ignored insertions + deletions
    refined: str | None = None  # LLM class, if refined


def _kind(path: str) -> str:
    if LOCK.search(path):
        return "lock"
    if GENERATED.search(path):
        return "generated"
    if MEDIA.search(path):
        return "media"
    if MANIFESTS.search(path):
        return "manifest"
    if INFRA.search(path):
        return "infra"
    if TESTS.search(path):
        return "test"
    if DOCS.search(path):
        return "docs"
    return "code"


def effective_lines(root: Path) -> dict[str, int]:
    """sha -> insertions + deletions ignoring whitespace and blank lines, one git pass."""
    out = subprocess.run(
        ["git", "log", "--format=%x1e%H", "--numstat", "-w", "--ignore-blank-lines"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    result: dict[str, int] = {}
    for rec in out.split("\x1e"):
        lines = rec.strip().splitlines()
        if not lines:
            continue
        sha, total = lines[0].strip(), 0
        for ln in lines[1:]:
            m = re.match(r"^(\d+|-)\t(\d+|-)\t", ln)
            if m:
                total += (int(m.group(1)) if m.group(1).isdigit() else 0) + (
                    int(m.group(2)) if m.group(2).isdigit() else 0
                )
        result[sha] = total
    return result


def added_files(root: Path) -> dict[str, set[str]]:
    """sha -> files first added in that commit."""
    out = subprocess.run(
        ["git", "log", "--format=%x1e%H", "--name-status", "--diff-filter=A", "--no-renames"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    result: dict[str, set[str]] = {}
    for rec in out.split("\x1e"):
        lines = rec.strip().splitlines()
        if not lines:
            continue
        sha = lines[0].strip()
        result[sha] = {ln.split("\t", 1)[1] for ln in lines[1:] if ln.startswith("A\t")}
    return result


def heuristic_weight(
    c: Commit, eff_lines: int, added: set[str], renames: set[str], new_dirs: set[str]
) -> CommitWeight:
    if not c.files:  # merge commits and empty commits land nothing themselves
        return CommitWeight(c.sha, 0.0, ["no diff (merge or empty)"], 0)
    kinds = [_kind(f) for f in c.files]
    kindset = set(kinds)
    reasons: list[str] = []
    raw_lines = c.insertions + c.deletions

    if kindset <= {"lock", "generated", "media"}:
        return CommitWeight(c.sha, 0.02, ["only lockfiles, generated or media files"], eff_lines)
    if raw_lines > 0 and eff_lines == 0:
        return CommitWeight(c.sha, 0.05, ["whitespace-only change"], 0)
    if c.sha in renames and eff_lines == 0:
        return CommitWeight(c.sha, 0.05, ["pure rename or move"], 0)
    if kindset <= {"docs", "lock", "generated", "media"}:
        return CommitWeight(c.sha, 0.05, ["documentation only"], eff_lines)
    if re.match(r"^Revert\b", c.subject):
        return CommitWeight(c.sha, 0.3, ["revert"], eff_lines)

    # Size: log scale, 500 effective lines saturate.
    size = min(1.0, math.log1p(eff_lines) / math.log1p(500)) if eff_lines else 0.1
    w = 0.1 + 0.6 * size
    reasons.append(f"{eff_lines} effective lines")

    code_files = [f for f, k in zip(c.files, kinds, strict=True) if k == "code"]
    if not code_files and kindset <= {"test", "docs", "lock", "generated", "media"}:
        w *= 0.4
        reasons.append("tests only")
    if "manifest" in kindset:
        w += 0.3
        reasons.append("dependencies changed")
    if "infra" in kindset:
        w += 0.15
        reasons.append("infrastructure changed")
    new_code = [f for f in code_files if f in added]
    if new_code:
        w += min(0.2, 0.05 * len(new_code))
        reasons.append(f"{len(new_code)} new code files")
    if new_dirs:
        w += 0.3
        reasons.append("new top-level dir " + ",".join(sorted(new_dirs))[:30])
    tops = {t for t in (_toplevel(f) for f in c.files) if t}
    if len(tops) > 1:
        w += min(0.2, 0.1 * (len(tops) - 1))
        reasons.append(f"spans {len(tops)} top-level dirs")
    if KEYWORDS.search(c.subject):
        w += 0.1
        reasons.append("decision keyword in subject")
    return CommitWeight(c.sha, round(max(0.0, min(1.0, w)), 3), reasons, eff_lines)


def compute_weights(
    root: Path, commits: list[Commit], renames: set[str]
) -> dict[str, CommitWeight]:
    eff = effective_lines(root)
    added = added_files(root)
    seen_dirs: set[str] = set()
    out: dict[str, CommitWeight] = {}
    for i, c in enumerate(commits):  # oldest first
        dirs = {d for d in (_toplevel(f) for f in c.files) if d}
        new_dirs = (dirs - seen_dirs) if i >= 3 else set()
        seen_dirs |= dirs
        out[c.sha] = heuristic_weight(
            c, eff.get(c.sha, 0), added.get(c.sha, set()), renames, new_dirs
        )
    return out


# -- cache -----------------------------------------------------------------------


def load_cache(path: Path) -> dict[str, CommitWeight]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {sha: CommitWeight(**d) for sha, d in data.get("commits", {}).items()}


def save_cache(path: Path, weights: dict[str, CommitWeight]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": 1, "commits": {sha: asdict(w) for sha, w in weights.items()}}
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def ensure_weights(
    root: Path, cache_path: Path, commits: list[Commit], renames: set[str]
) -> dict[str, CommitWeight]:
    """Cached weights, recomputing heuristics only for commits the cache has not seen.
    Refined entries are never overwritten."""
    cached = load_cache(cache_path)
    missing = [c for c in commits if c.sha not in cached]
    if missing:
        fresh = compute_weights(root, commits, renames)
        for c in missing:
            cached[c.sha] = fresh[c.sha]
        save_cache(cache_path, cached)
    return cached


def mass(commits: list[Commit], weights: dict[str, CommitWeight]) -> float:
    return round(sum(weights[c.sha].weight for c in commits if c.sha in weights), 3)


def apply_refinement(weights: dict[str, CommitWeight], grades: list[dict[str, Any]]) -> int:
    """Blend LLM classes into the heuristic weights. Returns how many changed."""
    n = 0
    for g in grades:
        w = weights.get(g["sha"]) or next(
            (v for k, v in weights.items() if k.startswith(g["sha"])), None
        )
        cls = g.get("klass")
        if w is None or cls not in CLASS_WEIGHT:
            continue
        w.weight = round(0.5 * w.weight + 0.5 * CLASS_WEIGHT[cls], 3)
        w.refined = cls
        w.reasons = [*w.reasons, f"llm: {cls}"]
        n += 1
    return n
