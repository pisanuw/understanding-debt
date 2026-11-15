"""The web app ports udebt's ladder and scoring to TypeScript (web/src/core/). This test
writes the cases and the Python answers to web/test/fixtures/parity.json, which the web app's
vitest suite checks its port against, and fails when the committed file no longer matches
what the Python core computes (regenerate with UDEBT_WRITE_PARITY=1 pytest
tests/test_hosted_parity.py). It also checks that web/src/core/prompt-parts.json is current."""

from __future__ import annotations

import json
import os
from pathlib import Path

from udebt import ladder, scoring
from udebt.hosted import prompt_parts

ROOT = Path(__file__).resolve().parent.parent
PARITY = ROOT / "web" / "test" / "fixtures" / "parity.json"
PARTS = ROOT / "web" / "src" / "core" / "prompt-parts.json"


def _r(ts: str, level: int | None, verdict: str, confidence: int = 2, **kw) -> dict:
    r = {"ts": f"2026-11-0{ts}T10:00:00+00:00", "unit": "u", "verdict": verdict,
         "confidence": confidence, **kw}  # fmt: skip
    if level is not None:
        r["level"] = level
    return r


RECORD_SETS = {
    "none": [],
    "l1 correct": [_r("1", 1, "correct")],
    "l1 unknown then correct": [_r("1", 1, "unknown", 1, revealed=True), _r("2", 1, "correct")],
    "l1-l2 correct, l3 partial": [
        _r("1", 1, "correct"),
        _r("2", 2, "correct"),
        _r("3", 3, "partial"),
    ],  # fmt: skip
    "l1-l5, l5 confidently wrong": [
        _r("1", 1, "correct"),
        _r("2", 2, "correct"),
        _r("3", 3, "correct"),
        _r("4", 4, "correct"),
        _r("5", 5, "wrong", 3),
    ],  # fmt: skip
    "l2 confidently wrong last": [_r("1", 1, "correct"), _r("2", 2, "wrong", 3)],
    "l3 confidently wrong last": [
        _r("1", 1, "correct"),
        _r("2", 2, "correct"),
        _r("3", 3, "wrong", 3),
    ],  # fmt: skip
    "two-answer sitting": [_r("1", 1, "correct"), _r("1", 1, "partial")],
    "legacy record": [_r("1", None, "correct")],
    "everything passed": [_r(str(i), i, "correct") for i in range(1, 6)],
    "l1 wrong plain": [_r("1", 1, "wrong", 2)],
}
SHARES = [None, 0.0, 0.5, 2.31]


def ladder_cases() -> list[dict]:
    out = []
    decay = scoring.Decay()
    for name, recs in RECORD_SETS.items():
        unit = {"id": "u", "importance": 0.8, "scope": "architectural"}
        per_share = []
        for share in SHARES:
            s = scoring.score_unit(unit, recs, 0.0, decay=decay, share_since=share)
            per_share.append({
                "share": share,
                "faded": scoring.faded(recs, 0.0, decay, share),
                "understanding": s.understanding,
                "debt": s.debt,
                "source": s.source,
                "note": scoring.decay_note(s, decay),
            })  # fmt: skip
        out.append({
            "name": name,
            "records": recs,
            "level_values": {str(k): v for k, v in scoring.level_values(recs).items()},
            "verified": scoring.ladder_verified(recs),
            "depth": ladder.depth(recs),
            "next_level": ladder.next_level(recs),
            "levels": list(scoring.score_unit(unit, recs, 0.0).levels),
            "scores": per_share,
        })  # fmt: skip
    return out


UNITS = [
    {"id": "a", "importance": 0.9, "scope": "architectural", "parent": None},
    {"id": "a1", "importance": 0.6, "scope": "architectural", "parent": "a"},
    {"id": "a2", "importance": 0.3, "scope": "architectural", "parent": "a"},
    {"id": "b", "importance": 0.5, "scope": "architectural", "parent": None},
    {"id": "c", "importance": 0.4, "scope": "blackbox", "parent": None},
    {"id": "d", "importance": 0.7, "scope": "architectural", "parent": None},
]


def _on(uid: str, recs: list[dict]) -> list[dict]:
    return [{**r, "unit": uid} for r in recs]


HIERARCHY = {
    "nothing": {},
    "parent quizzed": {"a": _on("a", RECORD_SETS["l1-l2 correct, l3 partial"])},
    "children quizzed": {
        "a1": _on("a1", RECORD_SETS["everything passed"]),
        "a2": _on("a2", RECORD_SETS["l1 correct"]),
    },  # fmt: skip
    "mixed": {
        "a": _on("a", RECORD_SETS["l1 correct"]),
        "a1": _on("a1", RECORD_SETS["everything passed"]),
        "b": _on("b", RECORD_SETS["l2 confidently wrong last"]),
        "d": _on("d", RECORD_SETS["l1 unknown then correct"]),
    },  # fmt: skip
}


def hierarchy_scores(records: dict[str, list[dict]], share: float | None) -> list:
    """compute_scores without the own-commit prior (the hosted app's learner): every unit
    scored on its own records, sub-units inherit half a quizzed parent, parents roll up."""
    own = {
        u["id"]: scoring.replace(
            scoring.score_unit(
                u, records.get(u["id"], []), 0.0, decay=scoring.Decay(), share_since=share
            ),  # fmt: skip
            parent=u["parent"],
        )
        for u in UNITS
    }
    out = dict(own)
    for u in UNITS:
        kids = [own[c["id"]] for c in UNITS if c["parent"] == u["id"]]
        if not kids:
            continue
        p = own[u["id"]]
        pq = p.understanding if p.source == "quiz" else None
        for k in kids:
            out[k.unit_id] = scoring.inherit(k, pq)
        out[u["id"]] = scoring.rollup(p, kids)
    return [out[u["id"]] for u in UNITS]


def hierarchy_cases() -> list[dict]:
    top = {u["id"] for u in UNITS if u["parent"] is None}
    cases = []
    for name, records in HIERARCHY.items():
        for share in (0.0, 1.0):
            sc = hierarchy_scores(records, share)
            cases.append({
                "name": name,
                "share": share,
                "records": records,
                "scores": [{"unit": s.unit_id, "understanding": s.understanding,
                            "source": s.source, "basis": s.basis, "debt": s.debt,
                            "depth": s.depth} for s in sc],  # fmt: skip
                "index": scoring.debt_index(sc, only=top),
                "basis_counts": scoring.basis_counts([s for s in sc if s.unit_id in top]),
                "priority": [s.unit_id for s in scoring.quiz_priority(
                    [s for s in sc if s.unit_id in top])],  # fmt: skip
            })  # fmt: skip
    return cases


QUESTIONS = [
    "What job does the part that streams replies do for the rest of the system?",
    "Which other parts rely on it, and what does it depend on?",
    "What does it do? Why does it matter?",
    "What is it for; who calls it?",
    "List the components that read the cache.",
    "What does the user see when the store fails halfway? Explain.",
    "Name the units that change with it.",
    "Why was a queue chosen over direct calls, and what would break without it?",
]


def text_cases() -> list[dict]:
    return [
        {
            "question": q,
            "level": lv,
            "compound": ladder.compound(q, lv),
            "first_clause": ladder.first_clause(q),
        }  # fmt: skip
        for q in QUESTIONS
        for lv in (1, 4)
    ]


def parity() -> dict:
    return {
        "comment": "Generated by tests/test_hosted_parity.py from udebt/scoring.py and "
        "udebt/ladder.py; do not edit by hand.",
        "level_k": {str(k): v for k, v in scoring.LEVEL_K.items()},
        "ladder": ladder_cases(),
        "hierarchy": {"units": UNITS, "cases": hierarchy_cases()},
        "text": text_cases(),
    }


def _dump(data: dict) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def test_parity_fixture_is_current():
    want = _dump(parity())
    if os.environ.get("UDEBT_WRITE_PARITY") == "1" or not PARITY.exists():
        PARITY.parent.mkdir(parents=True, exist_ok=True)
        PARITY.write_text(want, encoding="utf-8")
    assert PARITY.read_text(encoding="utf-8") == want, (
        "web/test/fixtures/parity.json is stale: UDEBT_WRITE_PARITY=1 pytest "
        "tests/test_hosted_parity.py"
    )


def test_prompt_parts_are_current():
    assert PARTS.read_text(encoding="utf-8") == prompt_parts.text(), (
        "web/src/core/prompt-parts.json is stale: python -m udebt.hosted.prompt_parts > "
        "web/src/core/prompt-parts.json"
    )


def test_prompt_parts_are_the_cli_prompts():
    p = prompt_parts.parts()
    from udebt import prompts

    units = [{"id": "x", "name": "X", "level": 1, "shown_orientation": "O", "revealed": ["Q?"]}]
    text = prompts.questions_prompt(units, 1)
    for key in ("one_fact", "no_identifiers", "orientation_rule", "revealed_rule",
                "objective_rule", "reference_rule", "ladder_block"):  # fmt: skip
        assert p[key] in text, key
    g = prompts.grade_prompt([{"unit_id": "x", "level": 1, "question": "q",
                               "reference_answer": "r", "answer": "a"}])  # fmt: skip
    assert p["grade_rules"] in g
