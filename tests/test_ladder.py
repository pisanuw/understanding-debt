"""The question ladder (Phase 3.2): levels, progression, scoring, orientation, the CLI."""

import json

import pytest
from conftest import understood

from udebt import cli, ladder, scoring
from udebt import orientation as orient
from udebt.llm import FakeLLM
from udebt.prompts import grade_prompt, questions_prompt, stable_questions_prompt
from udebt.store import Store


def r(level, verdict, ts="2026-01-01T00:00:00+00:00", confidence=2, **kw):
    return {"level": level, "verdict": verdict, "confidence": confidence, "ts": ts, **kw}


def t(day):
    return f"2026-01-{day:02d}T00:00:00+00:00"


# -- progression -------------------------------------------------------------------------


def test_everyone_starts_at_level_one_and_climbs_on_correct():
    assert ladder.next_level([]) == 1 and ladder.depth([]) is None
    recs = [r(1, "correct", t(1))]
    assert ladder.next_level(recs) == 2 and ladder.depth(recs) == 1
    recs += [r(2, "partial", t(2))]
    assert ladder.next_level(recs) == 2, "partial keeps the level"
    recs += [r(2, "unknown", t(3))]
    assert ladder.next_level(recs) == 2, "unknown repeats it"
    recs += [r(2, "wrong", t(4))]
    assert ladder.next_level(recs) == 2, "a plain wrong repeats it"
    recs += [r(2, "correct", t(5)), r(3, "correct", t(6)), r(4, "correct", t(7))]
    assert ladder.next_level(recs) == 5 and ladder.depth(recs) == 4
    recs += [r(5, "correct", t(8))]
    assert ladder.next_level(recs) == 5 and ladder.depth(recs) == 5, "5 is the top"


def test_a_confident_wrong_above_level_one_drops_a_level():
    recs = [r(1, "correct", t(1)), r(2, "correct", t(2)), r(3, "wrong", t(3), confidence=3)]
    assert ladder.depth(recs) == 2 and ladder.next_level(recs) == 2, "re-check level 2"
    recs += [r(2, "correct", t(4))]
    assert ladder.next_level(recs) == 3, "level 2 holds: back to 3"
    assert ladder.next_level([r(1, "wrong", t(1), confidence=3)]) == 1, "no level 0"


def test_a_sitting_passes_only_when_all_its_answers_are_correct():
    two = [r(1, "correct", t(1)), r(1, "partial", t(1))]  # --per-unit 2, one run
    assert ladder.depth(two) == 0 and ladder.next_level(two) == 1
    assert ladder.next_level(two + [r(1, "correct", t(2)), r(1, "correct", t(2))]) == 2


def test_legacy_and_forced_records_do_not_skip_the_ladder():
    legacy = [{"verdict": "correct", "confidence": 2, "ts": t(1)}]  # before the ladder
    assert ladder.level_of(legacy[0]) == 5
    assert ladder.next_level(legacy) == 1 and ladder.depth(legacy) == 0
    forced = [r(5, "correct", t(1), forced=True)]
    assert ladder.next_level(forced) == 1, "a forced level-5 pass shows nothing of 1-4"
    assert ladder.next_level([r(1, "correct", t(1), forced=True)]) == 2, "but it is a pass"


def test_labels_and_identifier_check():
    assert ladder.label(1) == "level 1 of 5: purpose and placement"
    assert ladder.label(5) == "level 5 of 5: implementation"
    # the owner's first quiz on digitalme: implementation questions, as level 1-3 must not be
    assert ladder.identifiers_in(
        "If you deleted the `except (asyncio.CancelledError, GeneratorExit)` block in "
        "event_stream(), what would happen?"
    )
    assert ladder.identifiers_in("What happens if you remove the usage_tracker.begin() call?")
    assert ladder.identifiers_in("Is _require_cap() right to trust the JWT claims?")
    assert ladder.identifiers_in("What does backend/app/main.py set up?")
    assert not ladder.identifiers_in(
        "What is the part that keeps conversations for, and what would stop working without "
        "it? Why PostgreSQL on a hosted service rather than SQLite, e.g. for read/write load?"
    )


# -- scoring -----------------------------------------------------------------------------


def test_ladder_scoring_a_third_per_architectural_level_and_depth_on_top():
    u = {"id": "u", "importance": 1.0}
    one = scoring.score_unit(u, [r(1, "correct")], 0)
    assert abs(one.understanding - 1 / 3) < 1e-9 and one.depth == 1
    full = scoring.score_unit(u, understood(), 0)
    assert full.understanding == 1.0 and full.depth == 3, "levels 1-3 are enough"
    deep = understood() + [r(4, "correct"), r(5, "correct")]
    s = scoring.score_unit(u, deep, 0, decay=scoring.Decay(), share_since=0.15)
    assert abs(s.verified - 1.2) < 1e-9 and s.depth == 5
    assert s.understanding == 1.0, "depth is headroom: 1.2 x exp(-0.15) is still over 1"
    arch = scoring.score_unit(u, understood(), 0, decay=scoring.Decay(), share_since=0.6)
    lifted = scoring.score_unit(u, deep, 0, decay=scoring.Decay(), share_since=0.6)
    assert arch.understanding < 0.6 <= lifted.understanding, "depth lifts over the threshold"
    # the most recent sitting per level counts: a level re-taken and passed is passed
    again = [r(1, "wrong", t(1)), r(1, "correct", t(2))]
    assert abs(scoring.score_unit(u, again, 0).understanding - 1 / 3) < 1e-9
    # partial at a level is half of it; a confident wrong is negative evidence at any level
    assert abs(scoring.score_unit(u, [r(1, "partial")], 0).understanding - 1 / 6) < 1e-9
    neg = understood() + [r(4, "wrong", confidence=3)]
    assert abs(scoring.score_unit(u, neg, 0).verified - 0.95) < 1e-9
    assert scoring.score_unit(u, [r(1, "wrong", confidence=3)], 0).understanding == 0.0
    # a record from before the ladder is level 5: worth the depth bonus only
    legacy = [{"verdict": "correct", "confidence": 2, "ts": t(1)}]
    assert abs(scoring.score_unit(u, legacy, 0).understanding - 0.1) < 1e-9


def test_flat_evidence_model_is_the_old_rule():
    u = {"id": "u"}
    recs = [r(1, "correct", t(1)), r(2, "wrong", t(2)), r(2, "correct", t(3))]
    flat = scoring.score_unit(u, recs, 0, model="flat")
    assert abs(flat.understanding - 2 / 3) < 1e-9 and flat.depth == 2
    assert abs(scoring.score_unit(u, recs, 0).understanding - 2 / 3) < 1e-9
    with pytest.raises(ValueError):
        scoring.verified(recs, model="mean")


# -- prompts -----------------------------------------------------------------------------


def test_prompts_carry_the_ladder():
    unit = {"id": "store", "name": "Persistence", "level": 1, "paths": ["udebt/store.py"]}
    oriented = {**unit, "shown_orientation": "What it is for: keeping records."}
    p = questions_prompt([oriented], 1)
    assert p.startswith("TASK: questions") and "level: 1 (purpose and placement)" in p
    flat = " ".join(p.split())
    assert "levels must not name any function, method, class, variable, constant, file" in flat
    assert "orientation the developer has just read: What it is for" in p
    assert "must not be answerable by repeating" in flat
    assert "repeating" not in questions_prompt([unit], 1), "no orientation, no rule for it"
    sp = stable_questions_prompt([unit], 2, 3)
    assert "level: 3 (consequence across units)" in sp and "at level 3" in sp
    g = grade_prompt([{**unit, "unit_id": "store", "question": "q", "reference_answer": "a",
                       "answer": "x", "level": 2}])  # fmt: skip
    assert "LEVEL: 2 (decision and alternatives)" in g
    g = " ".join(g.split())
    assert "never marked down for lacking them" in g
    assert "partial means the role or the decision is right" in g


# -- orientation -------------------------------------------------------------------------


def _mapped(root, capsys):
    cli.main(["--repo", str(root), "init"])
    cli.main(["--repo", str(root), "map"])
    capsys.readouterr()
    return Store(root)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("UDEBT_LLM", "fake")

    class Spy(FakeLLM):
        log: list[str] = []

        def structured(self, prompt, schema, **kw):
            Spy.log.append(prompt.split("\n", 1)[0])
            return super().structured(prompt, schema, **kw)

        def text(self, prompt, **kw):
            Spy.log.append(prompt.split("\n", 1)[0])
            return super().text(prompt, **kw)

    from udebt import llm as llm_mod

    Spy.log = []
    monkeypatch.setattr(llm_mod, "FakeLLM", Spy)
    return Spy


def test_orientations_are_written_once_in_one_call_and_cached(fixture_repo, fake, capsys):
    root = fixture_repo
    store = _mapped(root, capsys)
    m = store.require_map()
    got = orient.ensure(root, store, FakeLLM(), m, ["storage", "scoring"])
    assert set(got) == {"storage", "scoring"}
    saved = {u["id"]: u for u in store.require_map()["units"]}
    o = saved["storage"]["orientation"]
    assert o["purpose"] and o["basis"] == orient.basis(saved["storage"])
    assert [w["path"] for w in o["where"]] == ["udebt/store.py"], "invented paths dropped"
    assert "Orientation: Persistence layer" in orient.render(o, saved["storage"])
    llm = FakeLLM()
    orient.ensure(root, store, llm, store.require_map(), ["storage", "scoring"])
    assert llm.calls == [], "cached: no call"
    # a unit whose fields change gets a new one; a re-map that changes nothing keeps it
    m = store.require_map()
    m["units"][0]["decision"] = "something else"
    store.save_map(m)
    llm = FakeLLM()
    orient.ensure(root, store, llm, store.require_map(), ["storage", "scoring"])
    assert len(llm.calls) == 1 and "TASK: orientation" in llm.calls[0]
    assert llm.calls[0].count("- unit_id:") == 1, "only the stale one is asked for"
    cli.main(["--repo", str(root), "map"])
    capsys.readouterr()
    again = {u["id"]: u for u in store.require_map()["units"]}
    assert again["storage"]["orientation"]["basis"] == orient.basis(again["storage"])


def test_quiz_orients_climbs_and_records_levels(fixture_repo, fake, monkeypatch, capsys):
    root = fixture_repo
    store = _mapped(root, capsys)
    fake.log.clear()
    answers = iter(["it returns a default", "", "2"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    assert cli.main(["--repo", str(root), "quiz", "--unit", "storage"]) == 0
    out = capsys.readouterr().out
    assert fake.log == ["TASK: orientation", "TASK: questions", "TASK: grade"]
    assert out.index("Orientation: Persistence layer") < out.index("What is the storage unit")
    assert "unit: storage, level 1 of 5: purpose and placement" in out
    assert "[CORRECT ] storage (level 1)" in out
    (rec,) = store.load_records("dev1@example.com")
    assert rec["level"] == 1 and "forced" not in rec
    # the next quiz asks level 2, with no orientation and no call for one
    fake.log.clear()
    answers = iter(["", ""])
    assert cli.main(["--repo", str(root), "quiz", "--unit", "storage"]) == 0
    out = capsys.readouterr().out
    assert fake.log == ["TASK: questions"], "a blank answer is graded without a call"
    assert "storage (level 2)" in out and "Orientation:" not in out
    assert "level 2 of 5: decision and alternatives" in out
    # forcing a level: the old implementation questions, recorded as forced
    fake.log.clear()
    answers = iter(["it returns a default", "", "3"])
    assert cli.main(["--repo", str(root), "quiz", "--unit", "storage", "--level", "5"]) == 0
    out = capsys.readouterr().out
    assert "forced to level 5" in out and "level 5 of 5: implementation" in out
    last = store.load_records("dev1@example.com")[-1]
    assert last["level"] == 5 and last["forced"] is True
    # depth 1 (level 1 passed, level 2 unknown); the forced pass adds the depth bonus
    assert cli.main(["--repo", str(root), "status", "--deep"]) == 0
    out = capsys.readouterr().out
    row = next(ln for ln in out.splitlines() if ln.startswith("storage"))
    assert " 0.43   1/5 " in row and "depth = highest level passed" in out


def test_quiz_preview_records_nothing(fixture_repo, fake, capsys):
    root = fixture_repo
    store = _mapped(root, capsys)
    fake.log.clear()
    assert cli.main(["--repo", str(root), "quiz", "-n", "2", "--preview"]) == 0
    cap = capsys.readouterr()
    assert fake.log == ["TASK: orientation", "TASK: questions"]
    assert cap.out.startswith("# Ladder quiz preview") and "Quizzing" in cap.err
    assert cap.out.count("**Orientation**") == 2 and "level 1 of 5" in cap.out
    assert "Reference answer (for the grader)" in cap.out
    assert "found none" in cap.out, "the fake's level 1 questions name no identifier"
    assert store.load_records("dev1@example.com") == []
    units = json.loads(store.map_path.read_text())["units"]
    assert all("orientation" in u for u in units), "the orientations are cached for the team"


def test_dig_opens_with_the_orientation_and_checks_at_the_ladder_level(
    fixture_repo, fake, monkeypatch, capsys
):
    root = fixture_repo
    store = _mapped(root, capsys)
    for rec in understood("scoring", "2026-01-01T00:00:00+00:00")[:1]:  # level 1 passed
        store.append_record("dev1@example.com", rec)
    fake.log.clear()
    answers = iter(["the default", "", "2"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    assert cli.main(["--repo", str(root), "dig", "scoring"]) == 0
    out = capsys.readouterr().out
    assert fake.log == ["TASK: orientation", "TASK: dig", "TASK: grade"]
    assert out.index("Orientation: Debt scoring model") < out.index("fake walkthrough")
    assert "level 2 of 5: decision and alternatives" in out
    last = store.load_records("dev1@example.com")[-1]
    assert last["source"] == "dig" and last["level"] == 2


def test_status_deep_shows_depth_per_sub_unit(layered_repo, fake, capsys):
    """alice has passed levels 1-3 on one db sub-unit and level 1 on the other; status
    --deep shows each sub-unit's depth next to its understanding."""
    root = layered_repo
    store = _mapped(root, capsys)
    db = next(u for u in store.require_map()["units"] if "shop/db/models.py" in u["paths"])
    first, second = (c["id"] for c in db["children"][:2])
    dev = "alice@example.com"
    for rec in understood(first, "2099-01-01T00:00:00+00:00"):
        store.append_record(dev, rec)
    store.append_record(dev, r(1, "correct", "2099-01-01T00:00:00+00:00", unit=second))
    store.append_record(dev, r(2, "wrong", "2099-01-02T00:00:00+00:00", unit=second))
    assert cli.main(["--repo", str(root), "--as-user", dev, "status", "--deep"]) == 0
    out = capsys.readouterr().out
    print(out)
    rows = {ln.split()[0]: ln for ln in out.splitlines() if ln.startswith("  " + db["id"])}
    assert " 1.00   3/5 " in rows[first] and " 0.33   1/5 " in rows[second]
    assert any(" -  " in ln or "   - " in ln for ln in out.splitlines()), "no answer: -"
