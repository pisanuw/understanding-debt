"""Prediction quiz: pick units, find each one's ladder level, orient, generate questions, ask,
grade, record (with the level)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from . import ladder, repo, scoring
from . import units as hier
from .ledger import Cutoff, Ledger
from .llm import LLM
from .mapping import commits_for, weights_for
from .prompts import (
    GRADE_SCHEMA,
    QUESTIONS_SCHEMA,
    QUIZ_QUESTIONS_SCHEMA,
    grade_prompt,
    questions_prompt,
    stable_questions_prompt,
)
from .provenance import Commit, landed, line_weight, provenance_of, touches_of
from .responsibility import Responsibility, effective_scope
from .responsibility import load as load_responsibility
from .sizes import share
from .store import Store, now_iso
from .weights import line_weights


def landed_since(
    commits: list[Commit],
    weights: dict[str, Any],
    paths: list[str],
    since_sha: str | None,
    split: hier.Split | None = None,
    line_weights: dict[str, float] | None = None,
) -> tuple[float, float]:
    """(weighted mass, weighted changed lines) landed on `paths` after commit `since_sha`
    (all of history if that commit is unknown; nothing without one). The lines are each
    commit's changed lines under the paths, capped at the unit's size just after it
    (provenance.touches_of), times its line weight (weights.line_weights): the numerator of
    the share-changed decay. Bot commits land nothing: automation does not erode anyone's
    understanding."""
    if since_sha is None:
        return 0.0, 0.0
    idx = next((i for i, c in enumerate(commits) if c.sha == since_sha), -1)
    mass = lines = 0.0
    for t in touches_of(commits, paths, split):
        if t.pos > idx:
            mass += landed(t.commit, weights) * t.att
            lines += line_weight(t.commit, line_weights, weights) * t.lines
    return round(mass, 3), round(lines, 1)


def mass_since(
    commits: list[Commit],
    weights: dict[str, Any],
    paths: list[str],
    since_sha: str | None,
    split: hier.Split | None = None,
) -> float:
    """Weighted mass landed on `paths` after commit `since_sha` (all of it if unknown)."""
    return landed_since(commits, weights, paths, since_sha, split)[0]


def unit_sizes_at(root: Path, store: Store, units: list[dict[str, Any]], rev: str = "HEAD"):
    """unit id -> size in lines at `rev` (sizes.py), the share-changed decay's denominator."""
    from .sizes import open_counts, unit_sizes

    return unit_sizes(units, open_counts(root, store.dir).at(rev))


def compute_scores(
    root: Path,
    store: Store,
    units: list[dict[str, Any]],
    email: str,
    records: list[dict[str, Any]] | None = None,
    commits: list[Commit] | None = None,
    weights: dict[str, Any] | None = None,
    resp: Responsibility | None = None,
    sizes: dict[str, float] | None = None,
    *,
    ledger: Ledger | None = None,
    cutoff: Cutoff | None = None,
) -> list[scoring.UnitScore]:
    """Score every unit of the hierarchy for one developer, parents before their sub-units.
    Flat (version 1 and 2) maps simply have no sub-units. `commits`/`weights` let callers
    share one parse, `ledger` (built from them and `units`) one pass over where each commit
    landed, `sizes` (unit id -> lines, unit_sizes_at) one count of the tree, which the share
    decay model needs. The developer's own scope choices (responsibility.json) override the
    team-wide scope.

    `cutoff` scores the repository as it stood at a past point: only the commits that had
    landed by then, only the evidence recorded by then, sizes from that tree. Without one,
    everything counts (`status`). The replay (replay.py) and `status` share this code."""
    cfg = store.config
    decay = cfg.decay
    recs = store.load_records(email) if records is None else records
    if cutoff is not None:
        recs = cutoff.records(recs)
    if commits is None:
        commits = commits_for(root, store)
    if weights is None:
        weights = weights_for(root, store, commits)
    if sizes is None and decay.model == "share":
        sizes = unit_sizes_at(root, store, units, cutoff.rev if cutoff else "HEAD")
    if ledger is None:
        ledger = Ledger(commits, units)
    lws = line_weights(weights)
    resp = resp if resp is not None else load_responsibility(store)
    dev_scope = resp.scope(cfg.primary(email))
    team_scope = {u["id"]: u.get("scope", "architectural") for u in hier.flat(units)}
    mine = cfg.emails_for(email)
    own: dict[str, scoring.UnitScore] = {}
    for u, parent in hier.walk(units):
        touches = ledger.touches(u)
        if cutoff is not None:
            touches = cutoff.touches(touches)
        size = sizes.get(u["id"], 0.0) if sizes is not None else None
        unit_recs = [r for r in recs if r.get("unit") == u["id"]]
        since = scoring.last_record_head(unit_recs)
        m_since, l_since = ledger.since(touches, since, weights, lws)
        prov = provenance_of(touches, mine, weights, lws)
        p_share = None if size is None else share(prov.others_since_dev_lines, size)
        prior = scoring.prior_understanding(prov.dev_share, prov.others_since_dev_w, decay, p_share)
        s = scoring.score_unit(
            u,
            unit_recs,
            m_since,
            window=cfg.evidence_window,
            decay=decay,
            prior=prior,
            share_since=None if size is None else share(l_since, size),
            model=cfg.evidence_model,
        )
        pid = parent["id"] if parent else None
        scope, origin = effective_scope(u["id"], pid, team_scope, dev_scope)
        own[u["id"]] = replace(
            s,
            parent=pid,
            scope=scope,
            scope_origin=origin,
            prior_share=p_share if decay.model == "share" else None,
        )
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


def levels_for(
    units: list[dict[str, Any]], records: list[dict[str, Any]], forced: int | None = None
) -> dict[str, int]:
    """unit id -> the level to ask it at: `forced` for every unit (`quiz --level N`), else
    each unit's next ladder level from the developer's records on it (ladder.next_level)."""
    if forced is not None:
        return {u["id"]: ladder.check(forced) for u in units}
    return {
        u["id"]: ladder.next_level([r for r in records if r.get("unit") == u["id"]]) for u in units
    }


def revealed_on(records_on_unit: list[dict[str, Any]], level: int, n: int = 3) -> list[str]:
    """The questions at `level` the developer missed and was shown the answer to since they
    last answered that level correctly (at most `n`, newest last): the next sitting asks the
    same fact in other words."""
    at = [r for r in ladder._ordered(records_on_unit) if ladder.level_of(r) == level]
    since = at
    for i in range(len(at) - 1, -1, -1):
        if at[i].get("verdict") == "correct":
            since = at[i + 1 :]
            break
    out = [str(r["question"]) for r in since if r.get("revealed") and r.get("question")]
    return list(dict.fromkeys(out))[-n:]


def one_fact(q: dict[str, Any], level: int) -> dict[str, Any]:
    """The question with one part: its own phrasing if that asks one fact, else the first of
    its `variants` that does, else its first clause (ladder.first_clause). No extra call:
    the variants came with the question. What was set aside is kept as `rejected`."""
    phrasings = [str(q.get("question", ""))] + [str(v) for v in q.get("variants") or []]
    phrasings = [p for p in phrasings if p.strip()]
    rejected = []
    chosen = None
    for text in phrasings:
        why = ladder.compound(text, level)
        if why is None:
            chosen = text
            break
        rejected.append({"question": text, "why": why})
    trimmed = False
    if chosen is None:
        chosen = ladder.first_clause(phrasings[0] if phrasings else "")
        trimmed = True
    variants = [p for p in phrasings if p != chosen and ladder.compound(p, level) is None]
    if len(variants) < 2:
        variants += [
            t
            for t in (ladder.first_clause(p) for p in phrasings)
            if t != chosen and t not in variants
        ]
    out = {**q, "question": chosen, "variants": variants[:2]}
    if rejected:
        out["rejected"] = rejected
    if trimmed:
        out["trimmed"] = True
    return out


POOL_PER_LEVEL = 6  # questions kept per unit and level in map.json (`questions`)


def pool_add(store: Store, map_data: dict[str, Any], questions: list[dict[str, Any]]) -> None:
    """Keep generated questions on their units in map.json, per level, for any later run
    (`udebt serve` asks a pooled question before it asks the model for a new one). Each
    entry keeps the unit's basis, so a re-map that changes the unit drops it."""
    from . import orientation as orient

    by_id = hier.index(map_data["units"])
    changed = False
    for q in questions:
        u = by_id.get(q.get("unit_id"))
        if u is None or not q.get("question") or q.get("from_pool"):
            continue
        pool = u.setdefault("questions", {}).setdefault(str(ladder.level_of(q)), [])
        if any(p["question"] == q["question"] for p in pool):
            continue
        pool.append(
            {
                "question": q["question"],
                "variants": list(q.get("variants") or []),
                "reference_answer": q.get("reference_answer", ""),
                "evidence": list(q.get("evidence") or []),
                "basis": orient.basis(u),
                "generated_at": now_iso(),
            }
        )
        del pool[:-POOL_PER_LEVEL]
        changed = True
    if changed:
        store.save_map(map_data)


def revealed_variant(
    unit: dict[str, Any], level: int, records_on_unit: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """A stored variant of the question the developer last missed and saw the answer to at
    `level` (the same fact in other words) that they have not been asked; None without one."""
    asked = {str(r.get("question")) for r in records_on_unit}
    at = [r for r in ladder._ordered(records_on_unit) if ladder.level_of(r) == level]
    for r in reversed(at):
        if r.get("verdict") == "correct":
            break
        if r.get("revealed"):
            for v in r.get("variants") or []:
                if v not in asked:
                    return {
                        "unit_id": unit["id"],
                        "level": level,
                        "question": v,
                        "variants": [x for x in r["variants"] if x != v],
                        "reference_answer": r.get("reference") or "(graded against the code)",
                        "evidence": list(r.get("evidence") or []),
                        "from_pool": True,
                    }
    return None


def pooled(unit: dict[str, Any], level: int) -> list[dict[str, Any]]:
    """The unit's pooled questions at `level` that are still current (written from the unit
    as the map has it now), as quiz questions."""
    from . import orientation as orient

    b = orient.basis(unit)
    return [
        {"unit_id": unit["id"], "level": level, **p, "from_pool": True}
        for p in (unit.get("questions") or {}).get(str(level), [])
        if p.get("basis") == b
    ]


def pool_pick(
    unit: dict[str, Any], level: int, records_on_unit: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """A question for `unit` at `level` with no model call: a stored variant of the question
    the developer last missed and saw the answer to at that level (the same fact in other
    words), else a pooled question they have not been asked; None when there is neither."""
    got = revealed_variant(unit, level, records_on_unit)
    if got is not None:
        return got
    asked = {str(r.get("question")) for r in records_on_unit}
    return next((q for q in pooled(unit, level) if q["question"] not in asked), None)


def generate_questions(
    root: Path,
    llm: LLM,
    units: list[dict[str, Any]],
    per_unit: int = 1,
    *,
    stable: bool = False,
    level: int = 3,
    max_turns: int = 40,
) -> list[dict[str, Any]]:
    """Every question of a run in one call. Each unit carries the `level` to ask it at (and,
    when the developer was just shown one, `shown_orientation`; when they missed and saw the
    answer to one, `revealed`); a question keeps its unit's level whatever the model returns,
    and asks one fact (one_fact: a compound phrasing is replaced by a variant from the same
    reply). `stable` asks for questions on each unit as it stands, never on what changed (the
    synthetic-developer experiment), all at `level`, and keeps at most `per_unit` per unit,
    in unit order."""
    if stable:
        units = [{**u, "level": ladder.check(level)} for u in units]
        prompt = stable_questions_prompt(units, per_unit, level)
        schema = QUESTIONS_SCHEMA
    else:
        prompt = questions_prompt(units, per_unit)
        schema = QUIZ_QUESTIONS_SCHEMA
    res = llm.structured(prompt, schema, cwd=root, read_tools=True, max_turns=max_turns)
    asked = {u["id"]: int(u.get("level", 1)) for u in units}
    qs = [
        {**q, "level": asked[q["unit_id"]]}
        for q in res.get("questions", [])
        if q.get("unit_id") in asked
    ]
    if not stable:
        qs = [one_fact(q, q["level"]) for q in qs]
    if stable:
        order = {u["id"]: i for i, u in enumerate(units)}
        kept: dict[str, int] = {}
        capped = []
        for q in qs:
            if kept.get(q["unit_id"], 0) < per_unit:
                kept[q["unit_id"]] = kept.get(q["unit_id"], 0) + 1
                capped.append(q)
        qs = sorted(capped, key=lambda q: order[q["unit_id"]])
    if not qs:
        raise SystemExit("No questions were generated.")
    return qs


REVEALED_VERDICTS = ("unknown", "wrong", "partial")


def reveals(q: dict[str, Any], verdict: str) -> bool:
    """A miss at levels 1-2 shows the reference answer: the purpose or the decision is what a
    developer cannot work out alone, and an "I don't know" that teaches nothing teaches
    nothing next time either. A partial answer is a miss too (Phase 3.4, owner): its
    feedback names what was missing, the reference shows the whole of it."""
    return ladder.level_of(q) in ladder.REVEAL and verdict in REVEALED_VERDICTS


def reveal_text(q: dict[str, Any], width: int = 92) -> str:
    import textwrap

    ref = " ".join(str(q.get("reference_answer") or q.get("reference") or "").split())
    if not ref or ref == "(graded against the code)":
        return ""
    return "\n".join(
        textwrap.wrap(f"The answer: {ref}", width, initial_indent="  ", subsequent_indent="  ")
    )


def mark_revealed(results: list[dict[str, Any]], out=None) -> list[dict[str, Any]]:
    """After grading: a wrong or partial answer at levels 1-2 gets its reference shown (by
    `out`, when given) and is marked `revealed`; a blank one was marked when it was asked."""
    for r in results:
        if r.get("revealed") or not reveals(r, r.get("verdict", "")):
            continue
        text = reveal_text(r)
        if text:
            r["revealed"] = True
            if out is not None:
                out(text)
    return results


def read_lines(inp) -> str:
    """Lines up to an empty one, joined; empty when the first line is."""
    lines: list[str] = []
    while True:
        line = inp("> " if not lines else "  ")
        if line.strip() == "" and (lines or not line):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def read_confidence(inp) -> int:
    while True:
        c = inp("Confidence 1 (guess) / 2 (fairly sure) / 3 (certain): ").strip() or "2"
        if c in ("1", "2", "3"):
            return int(c)


def ask_interactively(
    questions: list[dict[str, Any]],
    inp=None,
    out=print,
    orientations: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Collect answers and confidence. Empty answer means 'I don't know' (a known unknown):
    at levels 1-2 the reference answer is shown right away and the answer is `revealed`.
    Each question shows its ladder level; `orientations` (unit id -> rendered text) is shown
    before that unit's first question."""
    inp = inp or input  # resolved at call time so tests can patch builtins.input
    shown: set[str] = set()
    answered = []
    for i, q in enumerate(questions, 1):
        uid = q["unit_id"]
        out(f"\n[{i}/{len(questions)}] unit: {uid}, {ladder.label(ladder.level_of(q))}")
        if orientations and uid in orientations and uid not in shown:
            shown.add(uid)
            out(orientations[uid] + "\n")
        out(q["question"])
        out("(Enter on an empty line to finish. Leave blank if you don't know.)")
        answer = read_lines(inp)
        confidence = read_confidence(inp) if answer else 1
        a = {**q, "answer": answer, "confidence": confidence}
        if not answer and reveals(q, "unknown") and reveal_text(q):
            out(reveal_text(q))
            a["revealed"] = True
        answered.append(a)
    return answered


def grade(
    root: Path, llm: LLM, answered: list[dict[str, Any]], max_turns: int = 40
) -> list[dict[str, Any]]:
    """Grade the non-empty answers in one call; an empty answer is a known unknown."""
    to_grade = [a for a in answered if a["answer"]]
    grades: dict[int, dict[str, Any]] = {}
    if to_grade:
        res = llm.structured(
            grade_prompt(to_grade), GRADE_SCHEMA, cwd=root, read_tools=True, max_turns=max_turns
        )
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


def _after(ts: str, prior: list[dict[str, Any]]) -> str:
    """`ts`, or one second after the developer's latest record when that is not earlier: two
    runs within a second would otherwise share a timestamp and read as one sitting."""
    from datetime import timedelta

    from .ledger import parse_ts

    stamps = [r["ts"] for r in prior if r.get("ts")]
    if not stamps:
        return ts
    latest = max(parse_ts(t) for t in stamps)
    if parse_ts(ts) > latest:
        return ts
    return (latest + timedelta(seconds=1)).replace(microsecond=0).isoformat()


def record(
    store: Store,
    root: Path,
    email: str,
    results: list[dict[str, Any]],
    source: str = "quiz",
    *,
    forced: bool = False,
    prior: list[dict[str, Any]] | None = None,
    kind: str = "quiz",
) -> list[dict[str, Any]]:
    """Append graded answers to the developer's evidence, each with the ladder `level` it was
    asked at (`forced` when `quiz --level` chose it). `source` says where the question came
    from (`quiz`, `dig` for a walkthrough's check question, `review`, `tutor`, `teachback`);
    all are quiz evidence. One run's answers share one timestamp: on a unit they are one
    sitting (ladder.py). A revealed miss is `revealed` and keeps the question's `variants`;
    each record carries its spaced-review state after it (`review`, review.py), folded from
    `prior` (the developer's records before this run; read from the store when not given).
    Returns the records written."""
    from . import review

    h = repo.head(root)
    prior = store.load_records(email) if prior is None else prior
    ts = _after(now_iso(), prior)
    out = []
    for r in results:
        out.append(
            {
                "ts": ts,
                "type": kind,
                "source": source,
                "dev": email,
                "unit": r["unit_id"],
                "level": ladder.level_of(r),
                **({"forced": True} if forced else {}),
                "head": h,
                "question": r["question"],
                "reference": r["reference_answer"],
                "evidence": r.get("evidence", []),
                "answer": r["answer"],
                "confidence": r["confidence"],
                "verdict": r["verdict"],
                "feedback": r["feedback"],
                **({"revealed": True} if r.get("revealed") else {}),
                **({"variants": list(r["variants"])} if r.get("variants") else {}),
                **({k: r[k] for k in ("right", "missing", "misconception") if r.get(k)}),
            }
        )
    review.stamp(prior, out)
    for rec in out:
        store.append_record(email, rec)
    passed2 = {r["unit"] for r in out if r["level"] == 2 and r["verdict"] == "correct"}
    if passed2 and store.config.decisions_dir:
        from .decisions import refresh_confirmed

        refresh_confirmed(root, store, passed2)
    return out
