"""Find the commits most likely to carry an architectural decision.

Cheap heuristics only; the LLM decides what the decision actually was. Scores:
  +3 founding commit (first three in history)
  +3 dependency manifest changed
  +3 introduces a new top-level directory
  +2 infrastructure file changed (Dockerfile, CI workflow, Makefile, deploy config)
  +2 contains renames or moves
  +1 per decision keyword in the subject (max 2)
  +1 unusually many files changed (top decile)
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .filekinds import INFRA, MANIFESTS
from .provenance import Commit

KEYWORDS = re.compile(
    r"\b(migrat\w*|switch\w*|replac\w*|adopt\w*|introduc\w*|rewrit\w*|refactor\w*|restructur\w*|"
    r"move[sd]? to|drop\w*|remov\w*|split\w*|persist\w*|database|db|auth\w*|cach\w*|deploy\w*|"
    r"stream\w*|async|queue|schema|architect\w*|redesign\w*|consolidat\w*|extract\w*|"
    r"decouple\w*|cron|pipeline)\b",
    re.I,
)
SKIP_TOPLEVEL = {".github", ".vscode", ".idea", "node_modules", "__pycache__", ".udebt"}


def rename_shas(root: Path) -> set[str]:
    out = subprocess.run(
        ["git", "log", "-M", "--diff-filter=R", "--format=%H", "--name-status"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    shas = set()
    current = None
    for line in out.splitlines():
        if re.fullmatch(r"[0-9a-f]{40}", line.strip()):
            current = line.strip()
        elif line.startswith("R") and current:
            shas.add(current)
    return shas


def _toplevel(path: str) -> str | None:
    if "/" not in path:
        return None
    top = path.split("/", 1)[0]
    return None if top in SKIP_TOPLEVEL else top


def decision_candidates(
    commits: list[Commit], renames: set[str], max_n: int = 40
) -> list[dict[str, Any]]:
    """Score commits (oldest first) and return the top `max_n`, oldest first."""
    if not commits:
        return []
    sizes = sorted(len(c.files) for c in commits)
    p90 = sizes[int(len(sizes) * 0.9)] if sizes else 0
    seen_dirs: set[str] = set()
    scored: list[tuple[int, list[str], Commit]] = []
    for i, c in enumerate(commits):
        score, reasons = 0, []
        if i < 3:
            score += 3
            reasons.append("founding")
        if any(MANIFESTS.search(f) for f in c.files):
            score += 3
            reasons.append("dependencies")
        new_dirs = {d for d in (_toplevel(f) for f in c.files) if d and d not in seen_dirs}
        seen_dirs |= new_dirs
        if new_dirs and i >= 3:
            score += 3
            reasons.append("new dir " + ",".join(sorted(new_dirs))[:40])
        if any(INFRA.search(f) for f in c.files):
            score += 2
            reasons.append("infra")
        if c.sha in renames:
            score += 2
            reasons.append("renames")
        kw = KEYWORDS.findall(c.subject)
        if kw:
            score += min(2, len(kw))
            reasons.append("keywords")
        if p90 and len(c.files) >= p90 and len(c.files) >= 5:
            score += 1
            reasons.append(f"{len(c.files)} files")
        if score >= 2:
            scored.append((score, reasons, c))
    top = sorted(scored, key=lambda t: (-t[0], t[2].date))[:max_n]
    top.sort(key=lambda t: t[2].date)
    return [
        {
            "sha": c.short,
            "date": c.date[:10],
            "klass": c.klass,
            "agent": c.agent,
            "author": c.author_name,
            "email": c.author_email,
            "subject": c.subject[:120],
            "files": len(c.files),
            "score": score,
            "reasons": reasons,
            "sample_files": sorted(c.files)[:8],
        }
        for score, reasons, c in top
    ]


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
