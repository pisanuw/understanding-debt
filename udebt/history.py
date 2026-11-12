"""Find the commits most likely to carry an architectural decision.

Cheap heuristics only; the LLM decides what the decision actually was. A commit's decision
score starts from its commit weight (weights.py: size, dependency and infrastructure
changes, new code files, spread; LLM-refined where refined) and adds what makes a commit a
decision rather than just a big change:
  +0.3 introduces a new top-level directory
  +0.2 renames or moves files
  +0.1 per decision keyword in the subject (max 2)
That sum is multiplied by the commit's survival, the share of the files it touched that
still exist at HEAD (directly or as a renamed successor), because the mapper can link a
commit only to code that is still there; a founding commit (the first three) adds +0.5
after that, and is the only kind kept when none of its files survive. Dependencies and
infrastructure are reasons, but count only through the weight.

The cut gives each period of the history (equal quarters of the weighted mass by default) a
floor of slots, filled by its strongest candidates, so a long pre-AI past cannot crowd out
the recent, AI-built era, and the larger, fully surviving AI-era commits cannot crowd out the
past. By default the floors take every slot (4 periods x 10 of 40), so slots are
proportional to mass; a smaller quota leaves the rest to the strongest candidates anywhere,
and a period with too few candidates leaves its slots to them too. Ties go to the more
recent commit.

Bot commits (dependabot, release automation) are never candidates.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .filekinds import INFRA, MANIFESTS
from .provenance import Commit, git_log

if TYPE_CHECKING:
    from .weights import CommitWeight

KEYWORDS = re.compile(
    r"\b(migrat\w*|switch\w*|replac\w*|adopt\w*|introduc\w*|rewrit\w*|refactor\w*|restructur\w*|"
    r"move[sd]? to|drop\w*|remov\w*|split\w*|persist\w*|database|db|auth\w*|cach\w*|deploy\w*|"
    r"stream\w*|async|queue|schema|architect\w*|redesign\w*|consolidat\w*|extract\w*|"
    r"decouple\w*|cron|pipeline)\b",
    re.I,
)
SKIP_TOPLEVEL = {".github", ".vscode", ".idea", "node_modules", "__pycache__", ".udebt"}

FOUNDING = 3  # the first commits in history
BONUS_FOUNDING = 0.5  # added after survival: a founding commit set the repository up
BONUS_NEW_DIR = 0.3
BONUS_RENAMES = 0.2
BONUS_KEYWORD = 0.1  # per keyword, at most two
MIN_SCORE = 0.5  # weight plus bonuses, before survival, that makes a commit a candidate
PERIODS = 4  # the history is cut into this many periods of equal weighted mass
QUOTA = 10  # slots each period is guaranteed, if it has that many: 4 x 10 of 40, by mass


def _parse_renames(out: str) -> dict[str, list[tuple[str, str]]]:
    pairs: dict[str, list[tuple[str, str]]] = {}
    current = None
    for line in out.splitlines():
        if re.fullmatch(r"[0-9a-f]{40}", line.strip()):
            current = line.strip()
        elif line.startswith("R") and current:
            parts = line.split("\t")
            if len(parts) == 3:
                pairs.setdefault(current, []).append((parts[1], parts[2]))
            else:
                pairs.setdefault(current, [])
    return pairs


def _rename_log(root: Path, shas: list[str] | None) -> str:
    return git_log(root, ["-M", "--diff-filter=R", "--format=%H", "--name-status"], shas)


def rename_shas(root: Path, shas: list[str] | None = None) -> set[str]:
    """Commits that rename or move a file, over history from HEAD or over exactly `shas`."""
    if shas is not None and not shas:
        return set()
    return set(_parse_renames(_rename_log(root, shas)))


def rename_pairs(root: Path, shas: list[str] | None = None) -> dict[str, list[tuple[str, str]]]:
    """sha -> the (old path, new path) pairs it renames, over history or exactly `shas`."""
    if shas is not None and not shas:
        return {}
    return _parse_renames(_rename_log(root, shas))


def successors(commits: list[Commit], moves: Mapping[str, list[tuple[str, str]]]) -> dict[str, str]:
    """old path -> the path it was renamed to, in history order (a later rename wins)."""
    succ: dict[str, str] = {}
    for c in commits:
        for old, new in moves.get(c.sha, ()):
            succ[old] = new
    return succ


def alive_as(path: str, alive: set[str], succ: Mapping[str, str]) -> str | None:
    """`path` as it exists at HEAD: itself, or its renamed successor; None if it is gone."""
    seen: set[str] = set()
    while path not in alive and path in succ and path not in seen:
        seen.add(path)
        path = succ[path]
    return path if path in alive else None


def survival(files: list[str], alive: set[str] | None, succ: Mapping[str, str]) -> float:
    """Share of `files` that still exist at HEAD, directly or as a renamed successor. With
    no `alive` set every file counts as surviving."""
    if not files:
        return 0.0
    if alive is None:
        return 1.0
    return sum(1 for f in files if alive_as(f, alive, succ) is not None) / len(files)


def _toplevel(path: str) -> str | None:
    if "/" not in path:
        return None
    top = path.split("/", 1)[0]
    return None if top in SKIP_TOPLEVEL else top


def _when(c: Commit) -> float:
    try:
        return datetime.fromisoformat(c.date).timestamp()
    except ValueError:
        return 0.0


def periods(commits: list[Commit], mass: Mapping[str, float], n: int) -> dict[str, int]:
    """sha -> period 0..n-1, cutting the history (oldest first) into `n` stretches of equal
    weighted mass; a commit belongs where the middle of its own mass falls. Falls back to
    equal commit counts when nothing has mass."""
    n = max(1, n)
    total = sum(mass.get(c.sha, 0.0) for c in commits)
    out: dict[str, int] = {}
    cum = 0.0
    for i, c in enumerate(commits):
        m = mass.get(c.sha, 0.0)
        frac = (cum + m / 2) / total if total > 0 else (i + 0.5) / len(commits)
        out[c.sha] = min(n - 1, int(frac * n))
        cum += m
    return out


def decision_candidates(
    commits: list[Commit],
    renames: set[str],
    max_n: int = 40,
    *,
    weights: Mapping[str, CommitWeight],
    alive: set[str] | None = None,
    moves: Mapping[str, list[tuple[str, str]]] | None = None,
    n_periods: int = PERIODS,
    quota: int = QUOTA,
) -> list[dict[str, Any]]:
    """Score commits (oldest first) and return the top `max_n`, oldest first.

    `weights` are the commit weights (weights.ensure_weights; a commit missing from them
    weighs 0), `alive` the files tracked at HEAD (without it every file survives) and
    `moves` the renames per commit, so a renamed file survives as its successor. Each of
    `n_periods` periods of equal weighted mass gets up to `quota` slots first; the rest go
    to the highest scores overall. Ties go to the more recent commit."""
    if not commits:
        return []
    from .weights import decision_weight

    succ = successors(commits, moves or {})
    mass = {c.sha: weights[c.sha].weight for c in commits if c.sha in weights and c.klass != "bot"}
    period = periods(commits, mass, n_periods)
    seen_dirs: set[str] = set()
    pool: list[dict[str, Any]] = []
    for i, c in enumerate(commits):
        new_dirs = {d for d in (_toplevel(f) for f in c.files) if d and d not in seen_dirs}
        seen_dirs |= new_dirs
        if c.klass == "bot" or not c.files:  # automation decides nothing; merges land nothing
            continue
        founding = i < FOUNDING
        w = decision_weight(weights[c.sha]) if c.sha in weights else 0.0
        raw, reasons = w, []
        if founding:
            reasons.append("founding")
        if any(MANIFESTS.search(f) for f in c.files if f not in c.version_files):
            reasons.append("dependencies")
        if any(INFRA.search(f) for f in c.files):
            reasons.append("infra")
        if new_dirs and not founding:
            raw += BONUS_NEW_DIR
            reasons.append("new dir " + ",".join(sorted(new_dirs))[:40])
        if c.sha in renames:
            raw += BONUS_RENAMES
            reasons.append("renames")
        kw = KEYWORDS.findall(c.subject)
        if kw:
            raw += BONUS_KEYWORD * min(2, len(kw))
            reasons.append("keywords")
        surv = survival(c.files, alive, succ)
        if (surv == 0 and not founding) or raw + (BONUS_FOUNDING if founding else 0) < MIN_SCORE:
            continue
        score = raw * surv + (BONUS_FOUNDING if founding else 0.0)
        if surv < 1:
            reasons.append(f"{surv:.0%} of files alive")
        pool.append(
            {
                "commit": c,
                "index": i,
                "when": _when(c),
                "score": round(score, 3),
                "weight": round(w, 3),
                "survival": round(surv, 2),
                "period": period.get(c.sha, 0),
                "reasons": reasons,
            }
        )

    def key(e: dict[str, Any]) -> tuple[float, float, int]:
        return (-e["score"], -e["when"], -e["index"])  # ties: the more recent commit

    per = min(max(0, quota), max_n // max(1, n_periods))
    chosen: dict[int, dict[str, Any]] = {}
    for p in range(max(1, n_periods)):
        for e in sorted((e for e in pool if e["period"] == p), key=key)[:per]:
            chosen[e["index"]] = e
    for e in sorted(pool, key=key):
        if len(chosen) >= max_n:
            break
        chosen.setdefault(e["index"], e)
    top = sorted(chosen.values(), key=lambda e: (e["when"], e["index"]))
    out = []
    for e in top:
        c = e["commit"]
        if alive is None:
            sample = sorted(c.files)
        else:  # files still alive first: they are what the mapper can link to a unit
            sample = sorted(c.files, key=lambda f: (alive_as(f, alive, succ) is None, f))
        out.append(
            {
                "sha": c.short,
                "date": c.date[:10],
                "klass": c.klass,
                "agent": c.agent,
                "author": c.author_name,
                "email": c.author_email,
                "subject": c.subject[:120],
                "files": len(c.files),
                "score": e["score"],
                "reasons": e["reasons"],
                "sample_files": sample[:8],
                "survival": e["survival"],
                "weight": e["weight"],
                "period": e["period"],
            }
        )
    return out


def tree_summary(files: list[str], max_lines: int = 150) -> str:
    """Directory-level view for big repos: 'dir/ (n files)' lines plus root files."""
    counts: dict[str, int] = {}
    roots = []
    for f in files:
        parts = f.split("/")
        if len(parts) == 1:
            roots.append(f)
            continue
        for depth in (1, 2):
            if len(parts) > depth:
                key = "/".join(parts[:depth]) + "/"
                counts[key] = counts.get(key, 0) + 1
    lines = [f"{k} ({v} files)" for k, v in sorted(counts.items())]
    if len(lines) > max_lines:
        lines = [ln for ln in lines if ln.count("/") == 1][:max_lines]
    return "\n".join([*lines, *roots])
