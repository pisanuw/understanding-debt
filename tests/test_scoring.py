from udebt import scoring


def rec(verdict, confidence=2, ts="2026-01-01T00:00:00+00:00", head="h1"):
    return {"verdict": verdict, "confidence": confidence, "ts": ts, "head": head}


def test_evidence_values():
    assert scoring.evidence(rec("correct")) == 1.0
    assert scoring.evidence(rec("partial")) == 0.5
    assert scoring.evidence(rec("wrong")) == 0.0
    assert scoring.evidence(rec("unknown", 1)) == 0.0
    assert scoring.evidence(rec("wrong", 3)) == -0.5, "confident misconception is penalised"


def test_verified_understanding_window_and_clamp():
    assert scoring.verified_understanding([]) is None
    recs = [rec("wrong", 3, ts=f"2026-01-0{i}") for i in range(1, 6)]
    assert scoring.verified_understanding(recs) == 0.0
    recs.append(rec("correct", ts="2026-01-09"))
    # window of 5 drops the oldest wrong answer: (4 * -0.5 + 1) / 5 < 0 -> clamped to 0
    assert scoring.verified_understanding(recs, window=5) == 0.0
    assert scoring.verified_understanding(recs, window=1) == 1.0


def test_decay():
    assert scoring.decayed(None, 10) == 0.0
    assert scoring.decayed(1.0, 0) == 1.0
    assert abs(scoring.decayed(1.0, 2, 0.9) - 0.81) < 1e-9


def test_importance_blend():
    assert scoring.importance(5, 10, 10) == 1.0
    assert scoring.importance(1, 0, 10) == 0.12
    assert scoring.importance(3, 0, 0) == 0.36  # no churn information at all


def test_debt_and_index():
    u1 = {"id": "a", "importance": 1.0, "scope": "architectural"}
    u2 = {"id": "b", "importance": 0.5, "scope": "architectural"}
    u3 = {"id": "c", "importance": 0.9, "scope": "blackbox"}
    s1 = scoring.score_unit(u1, [rec("correct")], 0)
    s2 = scoring.score_unit(u2, [], 0)
    s3 = scoring.score_unit(u3, [], 0)
    assert s1.debt == 0.0
    assert s2.debt == 0.5 and not s2.measured
    assert s3.debt == 0.0, "blackbox units never count"
    assert scoring.debt_index([s1, s2, s3]) == 33.3


def test_quiz_priority_prefers_important_unknowns():
    units = [
        {"id": "low", "importance": 0.2},
        {"id": "high", "importance": 0.9},
        {"id": "known", "importance": 1.0},
    ]
    scores = [
        scoring.score_unit(units[0], [], 0),
        scoring.score_unit(units[1], [], 0),
        scoring.score_unit(units[2], [rec("correct")], 0),
    ]
    assert [s.unit_id for s in scoring.quiz_priority(scores)] == ["high", "low", "known"]


def test_team_view():
    u = {"id": "a", "importance": 1.0}
    dev1 = [scoring.score_unit(u, [rec("correct")], 0)]
    dev2 = [scoring.score_unit(u, [rec("wrong")], 0)]
    t = scoring.team_view({"d1": dev1, "d2": dev2}, known_threshold=0.6)
    assert t["a"]["understanding"] == 1.0
    assert t["a"]["bus_factor"] == 1 and t["a"]["holders"] == ["d1"]
