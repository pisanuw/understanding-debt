"""Prediction quiz: pick units, generate questions, ask, grade, record."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from . import repo, scoring
from . import units as hier
from .llm import LLM
from .mapping import commits_for, weights_for
from .prompts import GRADE_SCHEMA, QUESTIONS_SCHEMA, grade_prompt, questions_prompt
from .provenance import Commit, unit_provenance
from .responsibility import Responsibility, effective_scope
from .responsibility import load as load_responsibility
from .store import Store


def mass_since(
    commits: list[Commit], weights: dict[str, Any], paths: list[str], since_sha: str | None
) -> float:
    """Weighted mass landed on `paths` after commit `since_sha` (all of it if unknown)."""
    if since_sha is None:
        return 0.0
    idx = next((i for i, c in enumerate(commits) if c.sha == since_sha), -1)
    later = commits[idx + 1 :] if idx >= 0 else commits
    return round(
        sum(
            (weights[c.sha].weight if c.sha in weights else 1.0) * c.attribution(paths)
            for c in later
            if c.touches(paths)
        ),
        3,
    )


def compute_scores(
    root: Path,
    store: Store,
    units: list[dict[str, Any]],
    email: str,
    records: list[dict[str, Any]] | None = None,
    commits: list[Commit] | None = None,
    weights: dict[str, Any] | None = None,
    resp: Responsibility | None = None,
) -> list[scoring.UnitScore]:
    """Score every unit of the hierarchy for one developer, parents before their sub-units.
    Flat (version 1 and 2) maps simply have no sub-units. `commits`/`weights` let callers
    share one parse. The developer's own scope choices (responsibility.json) override the
    team-wide scope."""
    cfg = store.config
    recs = store.load_records(email) if records is None else records
    if commits is None:
        commits = commits_for(root, store)
    if weights is None:
        weights = weights_for(root, store, commits)
    resp = resp if resp is not None else load_responsibility(store)
    dev_scope = resp.scope(cfg.primary(email))
    team_scope = {u["id"]: u.get("scope", "architectural") for u in hier.flat(units)}
    mine = cfg.emails_for(email)
    own: dict[str, scoring.UnitScore] = {}
    for u, parent in hier.walk(units):
        paths = u.get("paths", [])
        unit_recs = [r for r in recs if r.get("unit") == u["id"]]
        since = scoring.last_record_head(unit_recs)
        m_since = mass_since(commits, weights, paths, since)
        prov = unit_provenance(commits, paths, mine, weights)
        prior = scoring.prior_understanding(
            prov.dev_share, prov.others_since_dev_w, cfg.decay_per_commit
        )
        s = scoring.score_unit(
            u,
            unit_recs,
            m_since,
            window=cfg.evidence_window,
            decay=cfg.decay_per_commit,
            prior=prior,
        )
        pid = parent["id"] if parent else None
        scope, origin = effective_scope(u["id"], pid, team_scope, dev_scope)
        own[u["id"]] = replace(s, parent=pid, scope=scope, scope_origin=origin)
    out = dict(own)
    for u in units:
        kids = [own[c["id"]] for c in hier.children_of(u)]
        if not kids:
            continue
        p = own[u["id"]]
        parent_quiz = p.understanding if p.source == "quiz" else None
        for k in kids:
            out[k.unit_id] = scoring.inherit(k, parent_quiz)
        out[u["id"]] = scoring.rollup(p, kids)
    return [out[u["id"]] for u in hier.flat(units)]


def pick_units(
    units: list[dict[str, Any]],
    scores: list[scoring.UnitScore],
    n: int,
    only: list[str] | None = None,
    *,
    deep: bool = False,
    within: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Units to quiz: the named ones, else the `n` with the most debt at the chosen level
    (top-level units, or sub-units with `deep`), restricted to `within` when given."""
    if only:
        return [{**u, **_parent_name(units, u["id"])} for u in hier.resolve_all(units, only)]
    by_id = hier.index(units)
    pool = {u["id"] for u in hier.level(units, deep)}
    if within:
        pool &= within
    ranked = scoring.quiz_priority([s for s in scores if s.unit_id in pool])
    return [{**by_id[s.unit_id], **_parent_name(units, s.unit_id)} for s in ranked[:n]]


def _parent_name(units: list[dict[str, Any]], uid: str) -> dict[str, str]:
    pid = hier.parent_of(units).get(uid)
    return {"parent_name": hier.index(units)[pid]["name"]} if pid else {}


def generate_questions(
    root: Path, llm: LLM, units: list[dict[str, Any]], per_unit: int = 1
) -> list[dict[str, Any]]:
    res = llm.structured(
        questions_prompt(units, per_unit), QUESTIONS_SCHEMA, cwd=root, read_tools=True
    )
    valid = {u["id"] for u in units}
    qs = [q for q in res.get("questions", []) if q.get("unit_id") in valid]
    if not qs:
        raise SystemExit("No questions were generated.")
    return qs


def ask_interactively(questions: list[dict[str, Any]], inp=None, out=print) -> list[dict[str, Any]]:
    """Collect answers and confidence. Empty answer means 'I don't know' (a known unknown)."""
    inp = inp or input  # resolved at call time so tests can patch builtins.input
    answered = []
    for i, q in enumerate(questions, 1):
        out(f"\n[{i}/{len(questions)}] unit: {q['unit_id']}")
        out(q["question"])
        out("(Enter on an empty line to finish. Leave blank if you don't know.)")
        lines = []
        while True:
            line = inp("> " if not lines else "  ")
            if line.strip() == "" and (lines or not line):
                break
            lines.append(line)
        answer = "\n".join(lines).strip()
        confidence = 1
        if answer:
            while True:
                c = inp("Confidence 1 (guess) / 2 (fairly sure) / 3 (certain): ").strip() or "2"
                if c in ("1", "2", "3"):
                    confidence = int(c)
                    break
        answered.append({**q, "answer": answer, "confidence": confidence})
    return answered


def grade(root: Path, llm: LLM, answered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    to_grade = [a for a in answered if a["answer"]]
    grades: dict[int, dict[str, Any]] = {}
    if to_grade:
        res = llm.structured(grade_prompt(to_grade), GRADE_SCHEMA, cwd=root, read_tools=True)
        for g in res.get("grades", []):
            grades[int(g["index"])] = g
    results, gi = [], 0
    for a in answered:
        if not a["answer"]:
            results.append(
                {
                    **a,
                    "verdict": "unknown",
                    "feedback": "Marked as a known unknown. No misconception recorded.",
                }
            )
            continue
        g = grades.get(gi, {"verdict": "wrong", "feedback": "Grader returned nothing for this."})
        gi += 1
        results.append({**a, "verdict": g["verdict"], "feedback": g["feedback"]})
    return results


def record(store: Store, root: Path, email: str, results: list[dict[str, Any]]) -> None:
    h = repo.head(root)
    for r in results:
        store.append_record(
            email,
            {
                "type": "quiz",
                "dev": email,
                "unit": r["unit_id"],
                "head": h,
                "question": r["question"],
                "reference": r["reference_answer"],
                "evidence": r.get("evidence", []),
                "answer": r["answer"],
                "confidence": r["confidence"],
                "verdict": r["verdict"],
                "feedback": r["feedback"],
            },
        )
