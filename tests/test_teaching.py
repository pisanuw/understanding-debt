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


# -- tour, path --------------------------------------------------------------------------


@pytest.fixture
def spy(monkeypatch):
    from udebt import llm as llm_mod

    log = []
    real = llm_mod.FakeLLM.structured

    def structured(self, prompt, schema, **kw):
        log.append(prompt.split("\n", 1)[0])
        return real(self, prompt, schema, **kw)

    monkeypatch.setattr(llm_mod.FakeLLM, "structured", structured)
    return log


def test_tour_is_one_cached_call_in_learning_order(layered_repo, spy, capsys):
    root = layered_repo
    store = _mapped(root, capsys)
    spy.clear()
    rc, out = run(root, capsys, "tour")
    assert rc == 0 and spy == ["TASK: tour"]
    assert out.startswith("# Architecture tour: shop") and "## The map" in out
    m = store.require_map()
    api = next(u["id"] for u in m["units"] if "shop/api/routes.py" in u["paths"])
    db = next(u["id"] for u in m["units"] if "shop/db/models.py" in u["paths"])
    api_line = next(ln for ln in out.split("## The map")[1].splitlines() if f"(`{api}`)" in ln)
    assert f"uses `{db}`" in api_line, "the api imports the db"
    sections = [ln for ln in out.splitlines() if ln.startswith("## ") and "(`" in ln]
    order = [ln.split("(`")[1].rstrip("`)") for ln in sections]
    assert order.index(db) < order.index(api), "what others use comes first"
    assert "about 1 minutes" in out or "minutes to read" in out
    assert m["tour"]["basis"] and len(m["tour"]["sections"]) == len(m["units"])
    spy.clear()
    rc, again = run(root, capsys, "tour")
    assert spy == [] and again == out, "cached: no call"
    m["units"][0]["decision"] = "changed"
    store.save_map(m)
    run(root, capsys, "tour")
    assert spy == ["TASK: tour"], "a changed unit makes the tour stale"


def test_path_orders_by_dependency_with_minutes_and_next_actions(layered_repo, capsys):
    root = layered_repo
    store = _mapped(root, capsys)
    rc, out = run(root, capsys, "--as-user", "lead@example.com", "path", "--format", "json")
    import json

    steps = json.loads(out)
    m = store.require_map()
    api = next(u["id"] for u in m["units"] if "shop/api/routes.py" in u["paths"])
    db = next(u["id"] for u in m["units"] if "shop/db/models.py" in u["paths"])
    ids = [s["unit"] for s in steps]
    assert ids.index(db) < ids.index(api) and db in steps[ids.index(api)]["after"]
    first = steps[0]
    assert first["minutes"] == 1 + 4 + 2 * 3 and first["command"].startswith("udebt dig ")
    rc, out = run(root, capsys, "--as-user", "lead@example.com", "path")
    assert "A 10-minute session: `udebt dig" in out and "uses " in out


# -- walkthrough -------------------------------------------------------------------------


def test_dig_shows_real_excerpts_caches_and_regenerates_on_change(
    fixture_repo, spy, monkeypatch, capsys
):
    from conftest import git

    from udebt import excerpts

    root = fixture_repo
    store = _mapped(root, capsys)
    spy.clear()
    rc, out = run(root, capsys, "dig", "scoring", "--no-check")
    assert spy == ["TASK: orientation", "TASK: walkthrough"]
    assert "Step 1: udebt/scoring.py:1-2" in out, "lines clamped to the file"
    assert "1 | def debt(i, u):" in out and "This is a fake walkthrough step 1" in out
    assert "no/such/file.py" not in out, "an untracked file is dropped"
    w = next(u for u in store.require_map()["units"] if u["id"] == "scoring")["walkthrough"]
    assert set(w["checks"]) == {"1", "2", "3", "4", "5"} and w["head"]
    spy.clear()
    rc, out = run(root, capsys, "dig", "scoring", "--no-check")
    assert spy == [] and "have not changed since" in out
    (root / "udebt" / "scoring.py").write_text("def debt(i, u):\n    return 0\n")
    run(root, capsys, "dig", "scoring", "--no-check")
    assert spy == ["TASK: walkthrough"], "an uncommitted change in the unit invalidates it"
    git(root, "commit", "-q", "-am", "debt is zero")
    spy.clear()
    run(root, capsys, "dig", "scoring", "--no-check")
    assert spy == [], "rewritten at the new state, which is now the head"
    # the check question comes from the walkthrough, at the ladder level, with its reference
    monkeypatch.setattr("builtins.input", lambda _p="": "")
    rc, out = run(root, capsys, "dig", "scoring")
    assert "What is the scoring unit for?" in out and "The answer: scoring returns a" in out
    rec = store.load_records("dev1@example.com")[-1]
    assert rec["source"] == "dig" and rec["revealed"] and rec["reference"].startswith("scoring")
    # excerpts: tracked files only, capped
    long = "\n".join(f"x{i} = {i}" for i in range(60)) + "\n"
    (root / "udebt" / "long.py").write_text(long)
    with pytest.raises(excerpts.ExcerptError, match="not a tracked file"):
        excerpts.read(root, "udebt/long.py", 1, 5)
    git(root, "add", "udebt/long.py")
    ex = excerpts.read(root, "udebt/long.py", 10, 50)
    assert (ex.start, ex.end, ex.cut, len(ex.lines)) == (10, 34, 16, 25)
    for bad in ("../proj/udebt/store.py", "/etc/passwd", ".git/config", "udebt/../../x"):
        with pytest.raises(excerpts.ExcerptError):
            excerpts.read(root, bad, 1, 2)


# -- review, teach-back, decisions -----------------------------------------------------


def test_review_brings_a_missed_question_back_in_other_words(fixture_repo, monkeypatch, capsys):
    root = fixture_repo
    store = _mapped(root, capsys)
    rc, out = run(root, capsys, "review")
    assert "Nothing to review" in out
    monkeypatch.setattr("builtins.input", lambda _p="": "")
    run(root, capsys, "quiz", "--unit", "storage")
    rc, out = run(root, capsys, "review")
    assert "Nothing due" in out and "level 1 on" in out, "due tomorrow"
    answers = iter(["it returns a default", "", "2"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    rc, out = run(root, capsys, "review", "--all")
    assert "In other words, what does the storage unit do (v1a)?" in out, "a stored variant"
    assert "[CORRECT ] storage (level 1)" in out and "Next review:" in out
    last = store.load_records("dev1@example.com")[-1]
    assert last["source"] == "review" and last["review"]["reps"] == 1
    # the correct review answer is a level 1 pass: the ladder moves on
    from udebt import ladder as lad

    assert lad.next_level(store.load_records("dev1@example.com")) == 2


def test_teachback_is_graded_at_the_current_level_with_right_missing_and_misconception(
    fixture_repo, monkeypatch, capsys
):
    from conftest import understood

    root = fixture_repo
    store = _mapped(root, capsys)
    answers = iter(["It keeps state; half of it is files.", "", "2"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    rc, out = run(root, capsys, "teachback", "storage")
    assert rc == 0 and "graded at your level there, level 1 of 5" in out, "no evidence: level 1"
    assert "[PARTIAL] level 1" in out and "where it sits in the system" in out
    assert "What was right: The purpose is right." in out and "Misconception: none found" in out
    assert "The answer: It returns a default, by design." in out, "partial at 1-2: revealed"
    (rec,) = store.load_records("dev1@example.com")
    assert rec["type"] == "teachback" and rec["source"] == "teachback" and rec["level"] == 1
    assert rec["answer"].startswith("It keeps state") and rec["missing"] and rec["revealed"]
    # level 1 passed: the next teach-back is graded at level 2
    one = understood("storage", ts="2030-01-01T00:00:00+00:00")[0]
    store.append_record("dev1@example.com", {**one, "dev": "dev1@example.com"})
    answers = iter(["It sends email.", "", "3"])
    rc, out = run(root, capsys, "teachback", "storage")
    assert "[WRONG] level 2" in out and "Misconception: It does not send email." in out
    assert "The answer: It returns a default, by design." in out, "level 2: revealed"
    assert store.load_records("dev1@example.com")[-1]["level"] == 2
    # levels 1-3 passed: level 4, where a miss gets feedback but not the answer
    for r in understood("storage", ts="2031-01-01T00:00:00+00:00"):
        store.append_record("dev1@example.com", {**r, "dev": "dev1@example.com"})
    answers = iter(["It sends email.", "", "3"])
    rc, out = run(root, capsys, "teachback", "storage")
    assert "[WRONG] level 4" in out and "The answer:" not in out
    answers = iter([""])
    rc, out = run(root, capsys, "teachback", "storage")
    assert "nothing recorded" in out


def test_teachback_prompt_names_the_level():
    from udebt.prompts import teachback_prompt

    u = {"id": "u", "name": "U", "paths": ["a.py"]}
    one = teachback_prompt(u, "It does x.", None, 1)
    three = teachback_prompt(u, "It does x.", None, 3)
    assert "level 1 of the question ladder" in one and "level: 1 (purpose" in one
    assert "if it were reversed" in three and "consequence of reversing it" in three


def test_decision_records_are_opt_in_and_record_who_confirmed(fixture_repo, monkeypatch, capsys):
    from conftest import understood

    root = fixture_repo
    store = _mapped(root, capsys)
    rc, out = run(root, capsys, "decisions")
    assert "nothing written" in out and not (root / "docs").exists()
    assert "public repository" in out
    rc, out = run(root, capsys, "decisions", "write")
    files = sorted(p.name for p in (root / "docs" / "decisions").iterdir())
    assert files == ["0001-scoring.md", "0002-storage.md", "README.md"]
    text = (root / "docs" / "decisions" / "0002-storage.md").read_text()
    assert "## Alternatives not chosen" in text and "SQLite; a hosted database." in text
    assert "**Last confirmed by:** nobody yet" in text
    assert store.config.decisions_dir == "docs/decisions"
    # a level 2 pass (levels 1 and 2 passed) fills the line, leaving hand edits alone
    p = root / "docs" / "decisions" / "0002-storage.md"
    p.write_text(p.read_text() + "\nA note by hand.\n")
    store.append_record("dev1@example.com", {**understood("storage")[0], "dev": "dev1@example.com"})
    answers = iter(["it returns a default", "", "2"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    run(root, capsys, "quiz", "--unit", "storage")
    text = p.read_text()
    assert "**Last confirmed by:** dev1@example.com on " in text and "A note by hand." in text
