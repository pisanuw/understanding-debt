"""Synthetic developers: the first validation experiment (ROADMAP Phase 5).

A synthetic developer is the `claude` CLI taking the quiz with its knowledge frozen at an
older snapshot of the repository. Questions are written from HEAD and graded against HEAD,
exactly as `udebt quiz` does; only the answerer sees the snapshot, and is told the code it
sees is the only code it knows. Its stale knowledge should go wrong where the code moved
since the snapshot, so the units with more weighted mass landed since should score worse.
That tests decay and unit attribution with no human subjects.

One run can freeze several developers at once, at several snapshots, all answering one
shared question set written from HEAD, so the snapshot is the only variable. The questions
ask about each unit's decision and current behaviour, never about what changed, so a unit
that has not moved since a snapshot can be answered correctly from it. Model calls: one for
the questions (read tools on HEAD), then per snapshot one for the answers (read tools on
the snapshot only) and one for the grades (read tools on HEAD): 1 + 2 x snapshots.

Per snapshot and unit the report gives the mass landed since (the same attribution as
everywhere else), the mean score (correct 1, partial 0.5, wrong and unknown 0), confident
wrong answers, and whether the mass since went mostly to files that existed at the snapshot
(edits to code the developer saw: the misconceptions) or mostly to new files (the known
gaps). The report is written after every call, so `--resume` can finish a run that stopped
without paying again for the calls already made. Nothing is written to `.udebt/records/`;
the report goes to `.udebt/experiments/`.

Each snapshot is a temporary `git worktree` with its `.git` link removed, so the answerer has
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
from .filekinds import mechanical
from .llm import LLM
from .mapping import commits_for, unit_mass, weights_for
from .prompts import ANSWER_SCHEMA, answer_prompt
from .provenance import Commit, Touch, _under, landed, line_weight, touches_of
from .quiz import generate_questions, grade, unit_sizes_at
from .scoring import Decay
from .sizes import share as share_of
from .store import Store, now_iso
from .weights import CommitWeight, line_weights


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


def answer(
    snap: Path, llm: LLM, questions: list[dict[str, Any]], max_turns: int = 60
) -> list[dict[str, Any]]:
    """The synthetic developer's answers, read from the snapshot only. One call."""
    res = llm.structured(
        answer_prompt(questions), ANSWER_SCHEMA, cwd=snap, read_tools=True, max_turns=max_turns
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
    sig = f", one-sided permutation p = {p:.3f}" if p is not None else ""
    size = abs(rho)
    if size < 0.1:
        return f"no correlation (rho = {rho:.2f}{sig}): not distinguishable from chance"
    strength = "weak" if size < 0.3 else "moderate" if size < 0.5 else "strong"
    sign = "positive" if rho > 0 else "negative"
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


REPORT_VERSION = 2  # 2: several snapshots, N questions per unit, one shared question set
UNCHANGED_MASS = 0.005  # below this a unit has not moved since the snapshot
MAX_TURNS = {"questions": 100, "answers": 100, "grades": 80}  # room for 2 x 12 questions


def resolve_snapshots(root: Path, revs: list[str], head: str) -> list[tuple[str, str]]:
    """(rev as given, sha) for each snapshot, nearest to HEAD first, duplicates dropped. Each
    must be a proper ancestor of HEAD."""
    out: dict[str, str] = {}
    for rev in revs:
        at = resolve(root, rev)
        if at == head:
            raise SystemExit(
                f"{rev} is HEAD: nothing has changed since, so there is nothing to test."
            )
        if subprocess.run(["git", "merge-base", "--is-ancestor", at, head], cwd=root).returncode:
            raise SystemExit(f"{rev} is not an ancestor of HEAD.")
        out.setdefault(at, rev)
    since = {at: len(_git(root, "rev-list", f"{at}..{head}").split()) for at in out}
    return [(out[at], at) for at in sorted(out, key=since.__getitem__)]


def pick_units(units: list[dict[str, Any]], n_units: int | None) -> list[dict[str, Any]]:
    """The architectural top-level units, most important first, the first `n_units`."""
    pool = [u for u in units if u.get("scope", "architectural") == "architectural"]
    picked = sorted(pool, key=lambda u: -float(u.get("importance", 0)))[: n_units or len(pool)]
    if not picked:
        raise SystemExit("No architectural units to quiz.")
    return picked


def change_lines(
    since: list[Commit],
    paths: list[str],
    files_then: set[str],
    split: hier.Split | None = None,
) -> tuple[float, float]:
    """(lines in files that existed at the snapshot, lines in files that did not) changed
    under `paths` by the commits since, counted as line share counts them (no lockfiles,
    generated files or version bumps; a shared file split among the units listing it). Bots
    land nothing and are left out. A commit with no line counts (binary files) counts its
    files instead."""
    part = split or (lambda _f: 1.0)
    old = new = 0.0
    for c in since:
        if c.klass == "bot":
            continue
        lines = c.counted_lines or {
            f: 1 for f in c.files if not mechanical(f) and f not in c.version_files
        }
        for f, n in lines.items():
            if _under(f, paths):
                if f in files_then:
                    old += n * part(f)
                else:
                    new += n * part(f)
    return round(old, 1), round(new, 1)


def classify_change(mass_since: float, old: float, new: float) -> tuple[str, float | None]:
    """unchanged, mostly-edited (most changed lines in files the snapshot had) or mostly-new,
    and the edited share."""
    share = round(old / (old + new), 3) if old + new else None
    if mass_since < UNCHANGED_MASS:
        return "unchanged", share
    return ("mostly-edited" if (share or 0.0) >= 0.5 else "mostly-new"), share


def unit_rows(
    root: Path,
    at: str,
    head: str,
    units: list[dict[str, Any]],
    all_units: list[dict[str, Any]],
    commits: list[Commit],
    weights: dict[str, CommitWeight],
    sizes: dict[str, float] | None = None,
    decay: Decay | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """The snapshot's header and one row per unit, before any question is answered. With
    `sizes` (unit id -> lines at HEAD) each row also carries the share of the unit changed
    since the snapshot, as the share decay model counts it, and what `decay` predicts for a
    developer who knew the unit fully at the snapshot (`predicted`, with the mass model's
    0.9 per unit of mass beside it)."""
    since_shas = set(_git(root, "rev-list", f"{at}..{head}").split())
    since = [c for c in commits if c.sha in since_shas]
    members = hier.Membership(all_units)
    files_now = repo.tracked_files(root)
    files_then = {f for f in _git(root, "ls-tree", "-r", "--name-only", at).splitlines() if f}
    lws = line_weights(weights)
    decay = decay or Decay()
    rows: dict[str, dict[str, Any]] = {}
    for u in units:
        paths, split = u.get("paths", []), members.split(u["id"])
        m_since = unit_mass(since, paths, weights, split)
        m_total = unit_mass(commits, paths, weights, split)
        old, new = change_lines(since, paths, files_then, split)
        change, edited = classify_change(m_since, old, new)
        extra: dict[str, Any] = {}
        if sizes is not None:
            # capped at the unit's size after each commit (provenance.touches_of)
            lines = sum(
                line_weight(t.commit, lws, weights) * t.lines
                for t in touches_of(commits, paths, split)
                if t.commit.sha in since_shas
            )
            s = share_of(lines, sizes.get(u["id"], 0.0))
            uncapped = sum(
                line_weight(c, lws, weights) * c.lines_in(paths, split)
                for c in since
                if c.touches(paths)
            )
            extra = {
                "lines_changed": round(lines, 1),
                "lines_uncapped": round(uncapped, 1),
                "size_lines": sizes.get(u["id"], 0.0),
                "share_changed": s,
                "predicted": round(decay.factor(m_since, s), 3),
                "predicted_mass": round(Decay("mass").factor(m_since), 3),
            }
        rows[u["id"]] = {
            "unit": u["id"],
            "name": u.get("name", ""),
            "importance": u.get("importance", 0),
            "mass_since": m_since,
            "mass_total": m_total,
            "share_since": round(m_since / m_total, 3) if m_total else 0.0,
            **extra,
            "files_now": sum(1 for f in files_now if _under(f, paths)),
            "files_at_rev": sum(1 for f in files_then if _under(f, paths)),
            "lines_since_existing": old,
            "lines_since_new": new,
            "edited_share": edited,
            "change": change,
            "questions": [],
        }
    header = {
        "at_sha": at,
        "at_date": _git(root, "log", "-1", "--format=%aI", at).strip(),
        "commits_since": len(since),
        "mass_since": round(sum(landed(c, weights, 0.0) for c in since), 3),
    }
    if rows:
        header["mean_share_since"] = round(
            sum(r["share_since"] for r in rows.values()) / len(rows), 3
        )
    if sizes is not None and rows:
        shares = sorted(r["share_changed"] for r in rows.values())
        header["mean_share_changed"] = round(sum(shares) / len(shares), 3)
        mid = len(shares) // 2
        header["median_share_changed"] = round(
            shares[mid] if len(shares) % 2 else (shares[mid - 1] + shares[mid]) / 2, 3
        )
        header["mean_predicted"] = round(sum(r["predicted"] for r in rows.values()) / len(rows), 3)
    return header, rows


def _score(r: dict[str, Any]) -> float:
    return scoring.VERDICT_SCORE.get(r["verdict"], 0.0)


def fill(rows: dict[str, dict[str, Any]], results: list[dict[str, Any]]) -> None:
    """Attach graded answers to their unit rows and score each unit."""
    for row in rows.values():
        row["questions"] = []
    for r in results:
        rows[r["unit_id"]]["questions"].append(
            {
                "question": r["question"],
                "reference": r.get("reference_answer", ""),
                "answer": r["answer"],
                "confidence": r["confidence"],
                "verdict": r["verdict"],
                "feedback": r.get("feedback", ""),
                "score": _score(r),
                "evidence": scoring.evidence(r),
            }
        )
    for row in rows.values():
        qs = row["questions"]
        if not qs:
            row.update(score=None, wrongness=None, evidence=None, verdict=None)
            row.update(confident_wrong=0, unknown=0)
            continue
        sc = sum(q["score"] for q in qs) / len(qs)
        ev = sum(q["evidence"] for q in qs) / len(qs)
        row["score"], row["wrongness"] = round(sc, 3), round(1 - sc, 3)
        row["evidence"] = round(ev, 3)
        row["verdict"] = "/".join(q["verdict"] for q in qs)
        row["confident_wrong"] = sum(
            1 for q in qs if q["verdict"] == "wrong" and q["confidence"] >= 3
        )
        row["unknown"] = sum(1 for q in qs if q["verdict"] == "unknown")


def correlation(points: list[tuple[float, float]]) -> dict[str, Any]:
    xs, ys = [x for x, _ in points], [y for _, y in points]
    rho, p = spearman(xs, ys), permutation_p(xs, ys)
    return {
        "n": len(points),
        "rho": None if rho is None else round(rho, 3),
        "p_one_sided": None if p is None else round(p, 4),
        "reading": reading(rho, p),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One snapshot's summary: verdicts, mean score, confident wrong answers, the Spearman
    correlation of mass since with wrongness (1 - mean score), the same with the evidence
    udebt records (confident wrong counts -0.5), and the split by kind of change."""
    scored = [r for r in rows if r["wrongness"] is not None]
    qs = [q for r in scored for q in r["questions"]]
    by_change: dict[str, dict[str, Any]] = {}
    for kind in ("unchanged", "mostly-edited", "mostly-new"):
        group = [r for r in scored if r["change"] == kind]
        gq = [q for r in group for q in r["questions"]]
        by_change[kind] = {
            "units": len(group),
            "questions": len(gq),
            "mean_mass_since": round(sum(r["mass_since"] for r in group) / len(group), 3)
            if group
            else None,
            "mean_wrongness": round(sum(r["wrongness"] for r in group) / len(group), 3)
            if group
            else None,
            "confident_wrong": sum(r["confident_wrong"] for r in group),
            "unknown": sum(r["unknown"] for r in group),
        }
    return {
        "units_scored": len(scored),
        "questions": len(qs),
        "verdicts": dict(Counter(q["verdict"] for q in qs)),
        "mean_score": round(sum(q["score"] for q in qs) / len(qs), 3) if qs else None,
        "confident_wrong": sum(r["confident_wrong"] for r in scored),
        "unknown_share": round(sum(r["unknown"] for r in scored) / len(qs), 3) if qs else None,
        "mass_since_vs_wrongness": correlation([(r["mass_since"], r["wrongness"]) for r in scored]),
        "mass_since_vs_evidence_wrongness": correlation(
            [(r["mass_since"], 1 - r["evidence"]) for r in scored]
        ),
        "share_since_vs_wrongness": correlation(
            [(r["share_since"], r["wrongness"]) for r in scored]
        ),
        **_share_summary(scored),
        "by_change": by_change,
    }


def _share_summary(scored: list[dict[str, Any]]) -> dict[str, Any]:
    """The share decay model against the observed scores, when the rows carry its share:
    the correlation of share changed with wrongness, and the mean predicted score beside the
    mean observed one (rows weigh alike), with the error of each unit's prediction."""
    rows = [r for r in scored if "share_changed" in r]
    if not rows:
        return {}
    n = len(rows)
    err = [r["predicted"] - r["score"] for r in rows]
    err_m = [r["predicted_mass"] - r["score"] for r in rows]
    return {
        "share_changed_vs_wrongness": correlation(
            [(r["share_changed"], r["wrongness"]) for r in rows]
        ),
        "mean_observed": round(sum(r["score"] for r in rows) / n, 3),
        "mean_predicted": round(sum(r["predicted"] for r in rows) / n, 3),
        "mean_predicted_mass": round(sum(r["predicted_mass"] for r in rows) / n, 3),
        "rmse_predicted": round((sum(e * e for e in err) / n) ** 0.5, 3),
        "rmse_predicted_mass": round((sum(e * e for e in err_m) / n) ** 0.5, 3),
    }


def paired(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Within each unit, across snapshots: the same questions, different knowledge. A pair of
    snapshots is concordant when the one with more mass since scored worse, discordant when
    it scored better. Pairs with equal mass since are skipped."""
    done = [s for s in snapshots if s.get("graded")]
    conc = disc = ties = 0
    for i, a in enumerate(done):
        for b in done[i + 1 :]:
            for uid, ra in a["units"].items():
                rb = b["units"].get(uid)
                if rb is None or ra["wrongness"] is None or rb["wrongness"] is None:
                    continue
                if abs(ra["mass_since"] - rb["mass_since"]) < UNCHANGED_MASS:
                    continue
                lo, hi = (ra, rb) if ra["mass_since"] < rb["mass_since"] else (rb, ra)
                if hi["wrongness"] > lo["wrongness"]:
                    conc += 1
                elif hi["wrongness"] < lo["wrongness"]:
                    disc += 1
                else:
                    ties += 1
    tau = round((conc - disc) / (conc + disc), 3) if conc + disc else None
    return {"concordant": conc, "discordant": disc, "tied": ties, "tau": tau}


def pooled(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Every graded unit-snapshot together (not independent: the same questions): the
    correlation of mass since with wrongness at the top level, as before, and under
    `share_changed` the share changed since (the share decay model's input, the variable that
    predicts in every run so far) and under `mass_share` the mass since over the unit's mass."""
    rows = [r for s in snapshots for r in _rows_list(s) if r["wrongness"] is not None]
    out = correlation([(r["mass_since"], r["wrongness"]) for r in rows])
    with_share = [r for r in rows if "share_changed" in r]
    if with_share:
        pts = [(r["share_changed"], r["wrongness"]) for r in with_share]
        out["share_changed"] = correlation(pts)
    out["mass_share"] = correlation([(r["share_since"], r["wrongness"]) for r in rows])
    return out


def _rows_list(snap: dict[str, Any]) -> list[dict[str, Any]]:
    return list(snap["units"].values())


def run(
    root: Path,
    store: Store,
    llm: LLM,
    revs: list[str],
    *,
    n_units: int | None = None,
    per_unit: int = 2,
    dev: str | None = None,
    resume: Path | None = None,
    out=print,
) -> tuple[dict[str, Any], Path]:
    m = store.require_map()
    head = repo.head(root)
    snaps = resolve_snapshots(root, revs, head)
    picked = pick_units(m["units"], n_units)
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    sizes = unit_sizes_at(root, store, m["units"], head)
    decay = store.config.decay

    prior: dict[str, Any] | None = None
    if resume is not None:
        prior = json.loads(Path(resume).read_text(encoding="utf-8"))
        if prior.get("head") != head or prior.get("version") != REPORT_VERSION:
            raise SystemExit(f"{resume} is for another HEAD or report version; start afresh.")
    date = lisbon_date()
    name = "_".join(at[:8] for _, at in snaps)
    path = Path(resume) if resume else store.dir / "experiments" / f"synthetic-{name}-{date}.json"
    report: dict[str, Any] = {
        "experiment": "synthetic",
        "version": REPORT_VERSION,
        "repo": root.name,
        "head": head,
        "dev": dev,
        "date": date,
        "generated_at": now_iso(),
        "per_unit": per_unit,
        "unit_ids": [u["id"] for u in picked],
        "llm_calls": 0,
        "questions": None,
        "snapshots": [],
    }
    report["decay"] = {
        "model": decay.model,
        "rate": decay.rate,
        "per_mass": decay.per_mass,
        "floor": decay.floor,
    }
    for rev, at in snaps:
        header, rows = unit_rows(root, at, head, picked, m["units"], commits, weights, sizes, decay)
        report["snapshots"].append({"at": rev, **header, "graded": False, "units": rows})

    def save() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def call(what: str) -> None:
        """Count a model call before making it, and save, so a call that fails still counts."""
        report["llm_calls"] += 1
        save()
        out(f"call {report['llm_calls']}: {what}...")

    if prior is not None:
        report["llm_calls"] = int(prior.get("llm_calls", 0))
    out(
        f"Synthetic developers frozen at {len(snaps)} snapshot(s), quizzed on HEAD {head[:8]}: "
        f"{len(picked)} units x {per_unit} questions, one question set for every snapshot"
    )
    same_units = prior is not None and prior.get("unit_ids") == report["unit_ids"]
    if same_units and prior.get("questions") and prior.get("per_unit") == per_unit:
        questions = prior["questions"]
        out("Questions: reused from the report being resumed")
    else:
        call("questions from HEAD, on each unit's decision and current behaviour")
        questions = generate_questions(
            root, llm, picked, per_unit, stable=True, max_turns=MAX_TURNS["questions"]
        )
    report["questions"] = questions
    save()

    done = {s["at_sha"]: s for s in (prior or {}).get("snapshots", []) if s.get("graded")}
    for snap in report["snapshots"]:
        at = snap["at_sha"]
        if same_units and at in done:
            snap.update(graded=True, units=done[at]["units"], summary=done[at].get("summary"))
            out(f"{at[:8]}: reused from the report being resumed")
            continue
        out(
            f"Snapshot {at[:8]} ({snap['at_date'][:10]}): {snap['commits_since']} commits, "
            f"mass {snap['mass_since']} since"
        )
        call(f"answers from {at[:8]} only")
        with snapshot(root, at) as wt:
            answered = answer(wt, llm, questions, MAX_TURNS["answers"])
        snap["answers"] = [
            {"index": i, "answer": a["answer"], "confidence": a["confidence"]}
            for i, a in enumerate(answered)
        ]
        save()
        if any(a["answer"] for a in answered):
            call(f"grading {at[:8]}'s answers against HEAD")
        results = grade(root, llm, answered, MAX_TURNS["grades"])
        fill(snap["units"], results)
        snap.pop("answers")
        snap["graded"] = True
        snap["summary"] = summarize(_rows_list(snap))
        save()

    graded = [s for s in report["snapshots"] if s["graded"]]
    report["pooled"] = pooled(graded)
    report["paired"] = paired(graded)
    save()
    return report, path


def suggest(
    root: Path,
    store: Store,
    targets: list[float],
    n_units: int | None = None,
    by: str = "mass",
) -> list[dict[str, Any]]:
    """For each target, the first-parent commit from HEAD at which it is first reached, with
    how many of the picked units have not moved since. `by` "mass": the landed weight of
    `rev..HEAD`; "share": the picked units' mean share changed since (the share decay
    model's input, weighted changed lines over each unit's size at HEAD); "mshare": their
    mean mass since over their mass in all (the measure the Phase 1.2 and 1.3 runs report).
    Each grows along the first-parent line, so a binary search finds the commit. No model
    call."""
    m = store.require_map()
    head = repo.head(root)
    picked = pick_units(m["units"], n_units)
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    sizes = unit_sizes_at(root, store, m["units"], head)
    lws = line_weights(weights)
    members = hier.Membership(m["units"])
    by_sha = {c.sha: c for c in commits}
    line = _git(root, "rev-list", "--first-parent", head).split()
    totals: dict[str, float] = {}
    unit_touches: dict[str, list[Touch]] = {}

    def measure(at: str) -> float:
        shas = [s for s in _git(root, "rev-list", f"{at}..{head}").split() if s in by_sha]
        if by == "mass":
            return sum(landed(by_sha[s], weights, 0.0) for s in shas)
        if by == "mshare":
            since = [by_sha[s] for s in shas]
            parts = []
            for u in picked:
                paths, split = u.get("paths", []), members.split(u["id"])
                whole = totals.setdefault(u["id"], unit_mass(commits, paths, weights, split))
                parts.append(unit_mass(since, paths, weights, split) / whole if whole else 0.0)
            return sum(parts) / len(parts)
        total = 0.0
        in_range = set(shas)
        for u in picked:
            touches = unit_touches.setdefault(
                u["id"], touches_of(commits, u.get("paths", []), members.split(u["id"]))
            )
            lines = sum(
                line_weight(t.commit, lws, weights) * t.lines
                for t in touches
                if t.commit.sha in in_range
            )
            total += share_of(lines, sizes.get(u["id"], 0.0))
        return total / len(picked)

    out = []
    for target in targets:
        lo, hi = 1, len(line) - 1  # mass and share since grow along the first-parent line
        if hi < lo:
            break
        while lo < hi:
            mid = (lo + hi) // 2
            if measure(line[mid]) >= target:
                hi = mid
            else:
                lo = mid + 1
        at = line[lo]
        header, rows = unit_rows(
            root, at, head, picked, m["units"], commits, weights, sizes, store.config.decay
        )
        out.append(
            {
                "target": target,
                **header,
                "unchanged": sum(1 for r in rows.values() if r["change"] == "unchanged"),
                "units": len(rows),
                "changes": dict(Counter(r["change"] for r in rows.values())),
            }
        )
    return out
