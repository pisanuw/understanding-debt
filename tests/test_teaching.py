"""Phase 3.3, the teaching layer: one fact per question, reveal on miss, estimated vs measured,
spaced review."""

from datetime import UTC, datetime, timedelta

import pytest

from udebt import cli, ladder, review
from udebt.llm import FakeLLM
from udebt.quiz import generate_questions, one_fact
from udebt.store import Store

# the five level 1 questions of the digitalme sample the owner could answer none of
OWNER_SAMPLE = [
    "Where does the part that streams a persona reply to the browser sit in the life of one "
    "chat message? Name the kinds of checks and lookups it relies on before generation, and "
    "the work it triggers or hands off to after the reply finishes.",
    "Beyond the main chat reply, which other parts of the system generate Claude spend that "
    "the usage metering has to capture, and who consumes the resulting numbers? What would go "
    "unbounded if the metering were missing?",
    "The admin feature that turns reviewer feedback into proposed persona changes sits between "
    "several other parts of the system. What does it depend on to run and be authorized, and "
    "which part of the system ultimately feels its output once a human approves it?",
    "Which other parts of the system rely on the sign-in and role system to decide what a "
    "caller may do, and what does the role system itself depend on for identity and for role "
    "data?",
    "Which parts of the system consume the assembled persona prompt, and what does the "
    "prompt-building part itself depend on to produce it? Name the main upstream inputs and "
    "downstream callers.",
]


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setenv("UDEBT_LLM", "fake")


def run(root, capsys, *args):
    rc = cli.main(["--repo", str(root), *args])
    return rc, capsys.readouterr().out


def _mapped(root, capsys):
    run(root, capsys, "init")
    run(root, capsys, "map")
    return Store(root)


def test_every_owner_sample_question_is_compound_and_one_part_ones_pass():
    for q in OWNER_SAMPLE:
        assert ladder.compound(q, 1), q
    for q in (
        "What job does the part that streams replies do for the rest of the system?",
        "Google sign-in was chosen over cookie sessions. What constraint made that the better "
        "choice here?",
        "If the role store moved out of SQLite, what is the first other part that would change?",
    ):
        assert ladder.compound(q, 2) is None, q
    assert ladder.compound("List the endpoints it guards.", 1)
    assert ladder.compound("List the endpoints it guards.", 5) is None, "only levels 1-3"
    assert ladder.first_clause(OWNER_SAMPLE[3]).endswith("decide what a caller may do?")
    assert ladder.first_clause("What is it for; who calls it?") == "What is it for?"


class OneCall(FakeLLM):
    """Returns one level 1 question whose phrasings are given."""

    def __init__(self, phrasings):
        super().__init__()
        self.phrasings = phrasings

    def structured(self, prompt, schema, **kw):
        self.calls.append(prompt)
        assert "variants" in schema["properties"]["questions"]["items"]["required"]
        return {
            "questions": [
                {"unit_id": "store", "level": 1, "question": self.phrasings[0],
                 "variants": self.phrasings[1:], "reference_answer": "It keeps state.",
                 "evidence": ["udebt/store.py"]}
            ]
        }  # fmt: skip


def test_a_compound_question_is_replaced_by_a_variant_from_the_same_call(tmp_path):
    unit = {"id": "store", "name": "Store", "level": 1, "paths": []}
    llm = OneCall(["What is it for, and which parts use it?", "What job does it do?", "Why?"])
    (q,) = generate_questions(tmp_path, llm, [unit])
    assert q["question"] == "What job does it do?" and len(llm.calls) == 1
    assert q["rejected"][0]["why"].startswith("two questions joined")
    assert "What is it for, and which parts use it?" not in q["variants"]
    # every phrasing compound: the first clause of the first, no further call
    llm = OneCall(["What is it for, and which parts use it?", "List its users.", "A; b?"])
    (q,) = generate_questions(tmp_path, llm, [unit])
    assert q["question"] == "What is it for?" and q["trimmed"] and len(llm.calls) == 1
    assert one_fact({"question": "What is it for?"}, 1) == {
        "question": "What is it for?",
        "variants": [],
    }


def test_a_blank_level_1_answer_reveals_the_reference_and_the_next_sitting_varies_it(
    fixture_repo, monkeypatch, capsys
):
    root = fixture_repo
    store = _mapped(root, capsys)
    monkeypatch.setattr("builtins.input", lambda _p="": "")
    rc, out = run(root, capsys, "quiz", "--unit", "storage")
    assert rc == 0
    q_at = out.index("What is the storage unit for?")
    assert q_at < out.index("The answer: storage returns a default.") < out.index("Grading")
    (rec,) = store.load_records("dev1@example.com")
    assert rec["revealed"] is True and rec["verdict"] == "unknown" and rec["level"] == 1
    assert len(rec["variants"]) == 2 and rec["review"]["reps"] == 0
    assert "the next quiz asks the same fact in other words" in out
    # the next sitting at level 1 tells the question writer what was revealed
    from udebt import llm as llm_mod

    seen = []
    real = llm_mod.FakeLLM.structured

    def spy(self, prompt, schema, **kw):
        seen.append(prompt)
        return real(self, prompt, schema, **kw)

    monkeypatch.setattr(llm_mod.FakeLLM, "structured", spy)
    run(root, capsys, "quiz", "--unit", "storage")
    qp = next(p for p in seen if p.startswith("TASK: questions"))
    assert "revealed after a miss (ask the same fact in other words): What is the storage" in qp


def test_a_wrong_answer_is_revealed_after_grading_but_not_above_level_2(
    fixture_repo, monkeypatch, capsys
):
    root = fixture_repo
    store = _mapped(root, capsys)
    answers = iter(["it sends email", "", "2"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    rc, out = run(root, capsys, "quiz", "--unit", "storage")
    assert "[WRONG   ] storage (level 1)" in out
    assert out.index("[WRONG") < out.index("The answer: storage returns a default.")
    assert store.load_records("dev1@example.com")[-1]["revealed"] is True
    monkeypatch.setattr("builtins.input", lambda _p="": "")
    rc, out = run(root, capsys, "quiz", "--unit", "storage", "--level", "3")
    assert "The answer:" not in out
    assert "revealed" not in store.load_records("dev1@example.com")[-1]


def test_status_shows_the_basis_and_the_quiz_explains_the_index_change(
    fixture_repo, monkeypatch, capsys
):
    root = fixture_repo
    _mapped(root, capsys)
    rc, out = run(root, capsys, "status")
    assert "basis" in out.splitlines()[3]
    assert "Basis: 0 unit(s) measured by your answers, 2 estimated" in out
    row = next(ln for ln in out.splitlines() if ln.startswith("storage"))
    assert " estimated " in row
    answers = iter(["returns a default", "", "2", ""])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    rc, out = run(root, capsys, "quiz", "-n", "2")
    assert "`udebt tour`" in out.split("Quizzing")[0], "no measured unit yet: the tour first"
    assert "Debt index 50.0 -> " in out and "were estimated from your own commits" in out
    assert "Basis: 2 unit(s) measured by your answers, 0 estimated" in out
    rc, out = run(root, capsys, "timeline")
    assert "now: 2 unit(s) measured by quiz answers (scoring, storage)" in out


def t(hours):
    return (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=hours)).isoformat()


def rec(verdict, hours, confidence=2, level=1, **kw):
    return {"unit": "u", "level": level, "verdict": verdict, "confidence": confidence,
            "ts": t(hours), "question": "Q?", "reference": "R.", **kw}  # fmt: skip


def test_review_intervals_are_sm2_from_the_first_miss():
    assert review.fold([rec("correct", 0)]) == {}, "a correct answer starts nothing"
    s = review.fold([rec("unknown", 0)])[("u", 1)]
    assert (s.reps, s.interval, s.ease) == (0, 1.0, 2.3) and s.due == t(24)
    recs = [rec("unknown", 0), rec("correct", 30)]
    s = review.fold(recs)[("u", 1)]
    assert (s.reps, s.interval) == (1, 1.0)
    recs.append(rec("correct", 60))
    s = review.fold(recs)[("u", 1)]
    assert (s.reps, s.interval) == (2, 3.0)
    recs.append(rec("correct", 200, confidence=3))
    s = review.fold(recs)[("u", 1)]
    assert s.reps == 3 and s.interval == round(3.0 * 2.3, 1) and s.ease == 2.4
    recs.append(rec("wrong", 400, confidence=3))
    s = review.fold(recs)[("u", 1)]
    assert (s.reps, s.interval) == (0, 1.0) and s.ease == 2.2
    floor = review.fold([rec("unknown", h) for h in range(10)])[("u", 1)]
    assert floor.ease == review.EASE_MIN


def test_review_asks_a_stored_variant_not_the_question_asked_last():
    recs = [rec("unknown", 0, variants=["Q2?", "Q3?"], revealed=True)]
    (item,) = review.due(recs, now=datetime(2026, 1, 3, tzinfo=UTC))
    assert item.question["question"] == "Q2?" and item.question["reference_answer"] == "R."
    assert review.due(recs, now=datetime(2026, 1, 1, 12, tzinfo=UTC)) == [], "not due yet"
    assert review.due(recs, now=datetime(2026, 1, 1, 12, tzinfo=UTC), everything=True)
