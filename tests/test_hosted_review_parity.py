"""The web app ports the spaced review queue (udebt/review.py, SM-2) to TypeScript
(web/src/core/review.ts). This test writes the cases and the Python answers to
web/test/fixtures/review-parity.json, which the web app's vitest suite checks its port against
case by case, and fails when the committed file no longer matches what udebt/review.py computes
(regenerate with UDEBT_WRITE_PARITY=1 pytest tests/test_hosted_review_parity.py).

The records are the CLI's shape (variants, reference and evidence at the top level); the web
test moves those three into a hosted row's `data`, as the app keeps them. `stamped` is the
`review` state each record carries when the records are written sitting by sitting through
`review.stamp`, which is what the hosted app writes on `data.review`, so a hosted row read
back into a CLI record is the record the CLI would have written."""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime
from pathlib import Path

from udebt import review
from udebt.ledger import parse_ts

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "web" / "test" / "fixtures" / "review-parity.json"

Q, A, B = "What job does it do?", "Why is it needed?", "What would break first without it?"


def _r(day: int, level: int | None, verdict: str | None, confidence: int | None = 2,
       unit: str | None = "u", hour: int = 10, **kw) -> dict:  # fmt: skip
    r: dict = {"ts": f"2026-11-{day:02d}T{hour:02d}:00:00+00:00"}
    if unit is not None:
        r["unit"] = unit
    if verdict is not None:
        r["verdict"] = verdict
    if confidence is not None:
        r["confidence"] = confidence
    if level is not None:
        r["level"] = level
    return {**r, **kw}


def _q(question: str = Q, variants: tuple[str, ...] = (A, B), reference: str | None = "R",
       evidence: tuple[str, ...] = ("src/a.py:1-10",)) -> dict:  # fmt: skip
    out: dict = {"question": question}
    if variants:
        out["variants"] = list(variants)
    if reference is not None:
        out["reference"] = reference
    if evidence:
        out["evidence"] = list(evidence)
    return out


CASES: dict[str, list[dict]] = {
    "nothing": [],
    "correct first: no item": [_r(1, 1, "correct", 2, **_q())],
    "unknown starts an item": [_r(1, 1, "unknown", 1, revealed=True, **_q())],
    "confidently wrong starts at quality 0": [_r(1, 1, "wrong", 3, revealed=True, **_q())],
    "partial starts, then passes": [
        _r(1, 2, "partial", 2, revealed=True, **_q()),
        _r(2, 2, "partial", 2, revealed=True, **_q(A, (Q, B))),
        _r(3, 2, "correct", 3, **_q(B, (Q, A))),
    ],  # fmt: skip
    "miss then correct at each confidence": [
        _r(1, 1, "unknown", 1, revealed=True, **_q()),
        _r(2, 1, "correct", 1, **_q(A, (Q, B))),
        _r(3, 1, "correct", 2, **_q(B, (Q, A))),
        _r(6, 1, "correct", 3, **_q(Q, (A, B))),
        _r(14, 1, "correct", 3, **_q(A, (Q, B))),
        _r(28, 1, "correct", 3, **_q(B, (Q, A))),
    ],  # fmt: skip
    "long run of certain answers": [_r(1, 3, "wrong", 2, **_q())]
    + [_r(d, 3, "correct", 3, **_q(A, (Q, B))) for d in (2, 3, 4, 5, 6, 7, 8, 9, 10)],
    "lapse after two passes": [
        _r(1, 1, "unknown", 1, revealed=True, **_q()),
        _r(2, 1, "correct", 3, **_q(A, (Q, B))),
        _r(5, 1, "correct", 3, **_q(B, (Q, A))),
        _r(12, 1, "wrong", 3, revealed=True, **_q(Q, (A, B))),
        _r(13, 1, "correct", 2, **_q(A, (Q, B))),
    ],  # fmt: skip
    "ease floor": [_r(d, 4, "wrong", 2, **_q()) for d in range(1, 8)]
    + [_r(d, 4, "correct", 1, **_q(A, (Q, B))) for d in (8, 9, 12, 20)],
    "two levels, two units": [
        _r(1, 1, "unknown", 1, revealed=True, **_q()),
        _r(1, 2, "wrong", 2, revealed=True, **_q("What was chosen?", ("Which alternative lost?",))),
        _r(2, 1, "partial", 2, unit="v", revealed=True, **_q("What is v for?", ())),
        _r(3, 1, "correct", 3, **_q(A, (Q, B))),
        _r(3, 3, "correct", 3, unit="v", **_q("What breaks if v changes?")),
        _r(4, 2, "correct", 2, **_q("Which alternative lost?", ("What was chosen?",))),
    ],  # fmt: skip
    "legacy record is level 5": [
        _r(1, None, "wrong", 2, **_q()),
        _r(3, None, "correct", 3, **_q(A)),
    ],  # fmt: skip
    "records without a unit or a verdict": [
        _r(1, 1, "unknown", 1, revealed=True, **_q()),
        _r(2, 1, "wrong", 2, unit=None, **_q("No unit?")),
        _r(3, 1, None, 2, **_q("No verdict?", ())),
    ],  # fmt: skip
    "a sitting of two on one key": [
        _r(1, 1, "correct", 2, **_q()),
        _r(1, 1, "unknown", 1, revealed=True, **_q(A, (Q, B))),
        _r(2, 1, "correct", 3, **_q(B, (Q, A))),
        _r(2, 1, "partial", 2, **_q(Q, (A, B))),
    ],  # fmt: skip
    "hosted timestamps": [
        {
            "ts": "2026-11-15T09:47:59.123Z",
            "unit": "u",
            "level": 1,
            "verdict": "unknown",
            "confidence": 1,
            "revealed": True,
            **_q(),
        },
        {
            "ts": "2026-11-16T10:12:03.999Z",
            "unit": "u",
            "level": 1,
            "verdict": "correct",
            "confidence": 3,
            **_q(A, (Q, B)),
        },
        {
            "ts": "2026-11-17T23:59:59.500Z",
            "unit": "u",
            "level": 1,
            "verdict": "correct",
            "confidence": 2,
            **_q(B, (Q, A)),
        },
    ],  # fmt: skip
    "naive and offset timestamps": [
        {
            "ts": "2026-11-03T10:00:00",
            "unit": "u",
            "level": 2,
            "verdict": "wrong",
            "confidence": 2,
            **_q(),
        },
        {
            "ts": "2026-11-04T12:30:00+01:00",
            "unit": "u",
            "level": 2,
            "verdict": "correct",
            "confidence": 3,
            **_q(A, (Q, B)),
        },
        {
            "ts": "2026-11-07T01:00:00-08:00",
            "unit": "u",
            "level": 2,
            "verdict": "correct",
            "confidence": 3,
            **_q(B, (Q, A)),
        },
    ],  # fmt: skip
    "confidence missing or zero": [
        _r(1, 1, "wrong", None, **_q()),
        _r(2, 1, "correct", None, **_q(A, (Q, B))),
        _r(3, 1, "correct", 0, **_q(B, (Q, A))),
    ],  # fmt: skip
    "phrasing: the variant asked last is not asked again": [
        _r(1, 1, "unknown", 1, revealed=True, **_q()),
        _r(2, 1, "wrong", 2, revealed=True, **_q(A, (Q, B), reference="R2")),
    ],  # fmt: skip
    "phrasing: no reference, no variants, no evidence": [
        _r(1, 3, "wrong", 2, **_q(variants=(), reference=None, evidence=())),
    ],  # fmt: skip
    "phrasing: the latest miss after a pass": [
        _r(1, 1, "partial", 2, **_q()),
        _r(2, 1, "correct", 3, **_q(A, (Q, B))),
        _r(5, 1, "unknown", 1, revealed=True, **_q("A new one?", ("Another?",), reference="R3")),
        _r(6, 1, "correct", 2, **_q("Another?", ("A new one?",), reference="R3")),
    ],  # fmt: skip
    "phrasing: the dig's reference stands for none": [
        _r(1, 5, "wrong", 2, **_q(reference="(graded against the code)", variants=())),
    ],  # fmt: skip
}

NOWS = ["2026-11-01T00:00:00+00:00", "2026-11-02T10:00:00+00:00", "2026-11-20T00:00:00+00:00",
        "2027-06-01T00:00:00+00:00"]  # fmt: skip


def _state(s: review.State | None) -> dict | None:
    return s.as_dict() if s is not None else None


def _stamped(records: list[dict]) -> list[dict | None]:
    """Each record's `review` when the records are written one sitting (one timestamp) at a
    time, in file order, as quiz.record writes them."""
    written: list[dict] = []
    out: list[dict | None] = []
    i = 0
    while i < len(records):
        j = i
        while j < len(records) and records[j]["ts"] == records[i]["ts"]:
            j += 1
        new = copy.deepcopy(records[i:j])
        review.stamp(written, new)
        out.extend(r.get("review") for r in new)
        written.extend(new)
        i = j
    return out


def _items(items: list[review.Item]) -> list[dict]:
    return [{"unit": it.unit, "level": it.level, "state": it.state.as_dict(),
             "question": it.question} for it in items]  # fmt: skip


def _due(records: list[dict], now: str, unit_ids: list[str] | None = None,
         everything: bool = False) -> dict:  # fmt: skip
    when: datetime = parse_ts(now)
    got = review.due(records, when, set(unit_ids) if unit_ids else None, everything=everything)
    return {"now": now, "unit_ids": unit_ids, "everything": everything,
            "items": [[it.unit, it.level] for it in got]}  # fmt: skip


def cases() -> list[dict]:
    out = []
    for name, records in CASES.items():
        folded = review.fold(records)
        dues = [_due(records, now) for now in NOWS]
        dues += [_due(records, NOWS[2], ["u"]), _due(records, NOWS[0], everything=True)]
        out.append({
            "name": name,
            "records": records,
            "fold": [{"unit": k[0], "level": k[1], "state": _state(v)}
                     for k, v in sorted(folded.items())],
            "stamped": _stamped(records),
            "items": _items(review.items(records)),
            "due": dues,
        })  # fmt: skip
    return out


def quality_cases() -> list[dict]:
    recs = [{"verdict": v, **({} if c is None else {"confidence": c})}
            for v in ("correct", "partial", "wrong", "unknown")
            for c in (None, 0, 1, 2, 3)]  # fmt: skip
    return [{"record": r, "quality": review.quality(r)} for r in recs]


def parity() -> dict:
    return {
        "comment": "Generated by tests/test_hosted_review_parity.py from udebt/review.py; "
        "do not edit by hand.",
        "constants": {
            "ease": review.EASE,
            "ease_min": review.EASE_MIN,
            "first": review.FIRST,
            "second": review.SECOND,
        },  # fmt: skip
        "quality": quality_cases(),
        "cases": cases(),
    }


def _dump(data: dict) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def test_review_parity_fixture_is_current():
    want = _dump(parity())
    if os.environ.get("UDEBT_WRITE_PARITY") == "1" or not FIXTURE.exists():
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(want, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == want, (
        "web/test/fixtures/review-parity.json is stale: UDEBT_WRITE_PARITY=1 pytest "
        "tests/test_hosted_review_parity.py"
    )


def test_stamps_agree_with_the_fold():
    """The state stamped on the last record at a key is the fold's state there: a hosted row's
    data.review read back as a CLI record's review needs no refolding."""
    for name, records in CASES.items():
        folded = review.fold(records)
        last: dict[tuple[str, int], dict] = {}
        for r, st in zip(records, _stamped(records), strict=True):
            if st is not None:
                last[review.key(r)] = st
        assert {k: v.as_dict() for k, v in folded.items()} == last, name


def test_the_cases_cover_every_rule():
    """Every quality, a lapse, the ease floor, intervals past the second and every phrasing
    branch appear in the fixture."""
    seen_q = {review.quality(r) for recs in CASES.values() for r in recs if r.get("verdict")}
    assert seen_q == {0, 1, 2, 3, 4, 5}
    states = [s for recs in CASES.values() for s in _stamped(recs) if s]
    assert any(s["ease"] == review.EASE_MIN for s in states)
    assert any(s["reps"] >= 3 and s["interval"] not in (1.0, 3.0) for s in states)
    assert any(s["reps"] == 0 and s["ease"] < review.EASE for s in states)
    picks = {it["question"]["question"] for c in cases() for it in c["items"]}
    assert {Q, A}.issubset(picks)
