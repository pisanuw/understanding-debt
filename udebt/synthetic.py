"""Synthetic developers: the first validation experiment (ROADMAP Phase 5).

A synthetic developer is the `claude` CLI taking the quiz with its knowledge frozen at an
older snapshot of the repository. Questions are written from HEAD and graded against HEAD,
exactly as `udebt quiz` does; only the answerer sees the snapshot, and is told the code it
sees is the only code it knows. Its stale knowledge should go wrong exactly where the code
moved since the snapshot, so the units with more weighted mass landed since should score
worse. That tests decay and unit attribution with no human subjects.

Three model calls per run, whatever the number of units: questions (read tools on HEAD),
answers (read tools on the snapshot only), grades (read tools on HEAD). Nothing is written to
`.udebt/records/`; the report goes to `.udebt/experiments/synthetic-<sha>-<date>.json`.

The snapshot is a temporary `git worktree` with its `.git` link removed, so the answerer has
no path back to the live repository; it is deleted and pruned afterwards.
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import repo, scoring
from . import units as hier
from .llm import LLM
from .mapping import commits_for, unit_mass, weights_for
from .prompts import ANSWER_SCHEMA, answer_prompt
from .provenance import _under
from .quiz import generate_questions, grade
from .store import Store, now_iso


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def resolve(root: Path, rev: str) -> str:
    try:
        return _git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").strip()
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"Not a commit: {rev}") from e


@contextmanager
def snapshot(root: Path, sha: str) -> Iterator[Path]:
    """A temporary checkout of `sha` with no link back to the repository."""
    tmp = Path(tempfile.mkdtemp(prefix="udebt-synthetic-"))
    wt = tmp / root.name
    try:
        _git(root, "worktree", "add", "--detach", "--quiet", str(wt), sha)
        (wt / ".git").unlink()  # cut the link: the answerer must not reach HEAD through it
        yield wt
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        subprocess.run(["git", "worktree", "prune"], cwd=root, capture_output=True)


def answer(snap: Path, llm: LLM, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The synthetic developer's answers, read from the snapshot only. One call."""
    res = llm.structured(
        answer_prompt(questions), ANSWER_SCHEMA, cwd=snap, read_tools=True, max_turns=60
    )
    by_index = {int(a["index"]): a for a in res.get("answers", []) if "index" in a}
    out = []
    for i, q in enumerate(questions):
        a = by_index.get(i, {})
        text = str(a.get("answer", "")).strip()
        conf = int(a.get("confidence", 1)) if text else 1
        out.append({**q, "answer": text, "confidence": max(1, min(3, conf))})
    return out


# -- statistics (stdlib only) ---------------------------------------------------------


def ranks(xs: list[float]) -> list[float]:
    """1-based ranks, ties sharing their average rank."""
    order = sorted(range(len(xs)), key=xs.__getitem__)
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman's rank correlation (Pearson on average ranks). None when undefined."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    return sxy / (sxx * syy) ** 0.5


def permutation_p(xs: list[float], ys: list[float], n: int = 10000, seed: int = 0) -> float | None:
    """One-sided p-value for a positive correlation: the share of random pairings whose
    Spearman correlation is at least the observed one."""
    rho = spearman(xs, ys)
    if rho is None:
        return None
    rng = random.Random(seed)
    ys_ = list(ys)
    hits = 0
    for _ in range(n):
        rng.shuffle(ys_)
        r = spearman(xs, ys_)
        if r is not None and r >= rho - 1e-12:
            hits += 1
    return (hits + 1) / (n + 1)


def reading(rho: float | None, p: float | None) -> str:
    if rho is None:
        return "undefined: no variation in mass or in wrongness"
    size = abs(rho)
    strength = (
        "no" if size < 0.1 else "weak" if size < 0.3 else "moderate" if size < 0.5 else "strong"
    )
    sign = "positive" if rho > 0 else "negative"
    sig = f", one-sided permutation p = {p:.3f}" if p is not None else ""
    verdict = (
        "consistent with decay"
        if rho > 0 and p is not None and p < 0.05
        else ("not distinguishable from chance" if rho > 0 else "against the decay model")
    )
    return f"{strength} {sign} correlation (rho = {rho:.2f}{sig}): {verdict}"


# -- the experiment ---------------------------------------------------------------------


def lisbon_date() -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Lisbon")).date().isoformat()
    except Exception:  # no tz database: fall back to UTC
        return datetime.now(UTC).date().isoformat()


def run(
    root: Path,
    store: Store,
    llm: LLM,
    rev: str,
    *,
    n_units: int | None = None,
    dev: str | None = None,
    out=print,
) -> tuple[dict[str, Any], Path]:
    m = store.require_map()
    units = m["units"]
    at = resolve(root, rev)
    head = repo.head(root)
    if subprocess.run(["git", "merge-base", "--is-ancestor", at, head], cwd=root).returncode:
        raise SystemExit(f"{rev} is not an ancestor of HEAD.")
    if at == head:
        raise SystemExit(f"{rev} is HEAD: nothing has changed since, so there is nothing to test.")
    pool = [u for u in units if u.get("scope", "architectural") == "architectural"]
    picked = sorted(pool, key=lambda u: -float(u.get("importance", 0)))[: n_units or len(pool)]
    if not picked:
        raise SystemExit("No architectural units to quiz.")

    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    since_shas = set(_git(root, "rev-list", f"{at}..{head}").split())
    since = [c for c in commits if c.sha in since_shas]
    members = hier.Membership(units)
    files_now = repo.tracked_files(root)
    files_then = [f for f in _git(root, "ls-tree", "-r", "--name-only", at).splitlines() if f]
    at_date = _git(root, "log", "-1", "--format=%aI", at).strip()

    rows: dict[str, dict[str, Any]] = {}
    for u in picked:
        paths, split = u.get("paths", []), members.split(u["id"])
        m_since = unit_mass(since, paths, weights, split)
        m_total = unit_mass(commits, paths, weights, split)
        rows[u["id"]] = {
            "unit": u["id"],
            "name": u.get("name", ""),
            "importance": u.get("importance", 0),
            "mass_since": m_since,
            "mass_total": m_total,
            "share_since": round(m_since / m_total, 3) if m_total else 0.0,
            "files_now": sum(1 for f in files_now if _under(f, paths)),
            "files_at_rev": sum(1 for f in files_then if _under(f, paths)),
            "questions": [],
        }

    out(f"Synthetic developer frozen at {at[:8]} ({at_date[:10]}), quizzed on HEAD {head[:8]}")
    out(f"{len(since)} commits since, on {len(picked)} units. 1/3: questions from HEAD...")
    questions = generate_questions(root, llm, picked, per_unit=1)
    out("2/3: answers from the snapshot only...")
    with snapshot(root, at) as snap:
        answered = answer(snap, llm, questions)
    out("3/3: grading against HEAD...")
    results = grade(root, llm, answered)

    for r in results:
        ev = scoring.evidence(r)
        rows[r["unit_id"]]["questions"].append(
            {
                "question": r["question"],
                "reference": r.get("reference_answer", ""),
                "answer": r["answer"],
                "confidence": r["confidence"],
                "verdict": r["verdict"],
                "feedback": r.get("feedback", ""),
                "evidence": ev,
            }
        )
    for row in rows.values():
        qs = row["questions"]
        if qs:
            ev = sum(q["evidence"] for q in qs) / len(qs)
            row["evidence"], row["wrongness"] = round(ev, 3), round(1 - ev, 3)
            row["verdict"] = "/".join(q["verdict"] for q in qs)
        else:
            row["evidence"] = row["wrongness"] = row["verdict"] = None

    scored = [r for r in rows.values() if r["wrongness"] is not None]
    xs = [r["mass_since"] for r in scored]
    shares = [r["share_since"] for r in scored]
    ys = [r["wrongness"] for r in scored]
    rho, p = spearman(xs, ys), permutation_p(xs, ys)
    rho_s, p_s = spearman(shares, ys), permutation_p(shares, ys)
    summary = {
        "units_scored": len(scored),
        "verdicts": dict(Counter(q["verdict"] for r in scored for q in r["questions"])),
        "mean_wrongness": round(sum(ys) / len(ys), 3) if ys else None,
        "spearman_mass_since_vs_wrongness": None if rho is None else round(rho, 3),
        "p_one_sided": None if p is None else round(p, 4),
        "spearman_share_since_vs_wrongness": None if rho_s is None else round(rho_s, 3),
        "p_one_sided_share": None if p_s is None else round(p_s, 4),
        "reading": reading(rho, p),
    }
    report = {
        "experiment": "synthetic",
        "version": 1,
        "repo": root.name,
        "head": head,
        "at": rev,
        "at_sha": at,
        "at_date": at_date,
        "dev": dev,
        "date": lisbon_date(),
        "generated_at": now_iso(),
        "commits_since": len(since),
        "llm_calls": 3,
        "units": list(rows.values()),
        "summary": summary,
    }
    path = store.dir / "experiments" / f"synthetic-{at[:8]}-{report['date']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report, path
