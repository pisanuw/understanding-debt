import json

from conftest import understood

from udebt import scoring
from udebt import units as hier
from udebt.quiz import compute_scores, pick_units
from udebt.store import Store

UNITS = [
    {
        "id": "api",
        "name": "API",
        "paths": ["shop/api"],
        "importance": 0.8,
        "children": [
            {"id": "api/auth", "name": "Auth", "paths": ["shop/api/auth.py"], "importance": 0.6},
            {
                "id": "api/routes",
                "name": "Routes",
                "paths": ["shop/api/routes.py"],
                "importance": 0.2,
            },
        ],
    },
    {"id": "db", "name": "DB", "paths": ["shop/db"], "importance": 0.5},
]


def rec(unit, verdict, ts="2026-01-02T00:00:00+00:00", head=None):
    return {"unit": unit, "verdict": verdict, "confidence": 2, "ts": ts, "head": head}


def test_unit_helpers():
    assert [u["id"] for u in hier.flat(UNITS)] == ["api", "api/auth", "api/routes", "db"]
    assert [u["id"] for u in hier.leaves(UNITS)] == ["api/auth", "api/routes", "db"]
    assert hier.parent_of(UNITS) == {"api/auth": "api", "api/routes": "api"}
    assert hier.resolve(UNITS, "auth")["id"] == "api/auth"  # short id, unambiguous
    assert hier.resolve(UNITS, "nope") is None
    assert hier.at_level({"api", "api/auth", "db"}, UNITS, deep=False) == {"api", "db"}
    assert hier.at_level({"api", "db"}, UNITS, deep=True) == {"api/auth", "api/routes", "db"}
    assert hier.covers({"api"}, "api/routes", UNITS) and not hier.covers({"db"}, "api", UNITS)
    rows = hier.rows(UNITS, deep=False, also={"api/routes"})
    assert [(u["id"], d) for u, d in rows] == [("api", 0), ("api/routes", 1), ("db", 0)]


def test_inherit_rollup_and_split():
    parent = scoring.score_unit({"id": "api", "importance": 0.8}, understood("api"), 0)
    auth = scoring.score_unit({"id": "api/auth", "importance": 0.6}, [], 0, prior=0.2)
    routes = scoring.score_unit({"id": "api/routes", "importance": 0.2}, understood("x"), 0)
    # a sub-unit without its own quiz takes half the parent's quiz when that beats its prior
    inh = scoring.inherit(auth, parent.understanding)
    assert inh.understanding == 0.5 and inh.source == "parent"
    assert scoring.inherit(routes, 1.0) is routes  # its own quiz wins
    assert scoring.inherit(auth, None) is auth
    # a parent is understood at least as well as its sub-units' weighted mean
    weak = scoring.score_unit({"id": "api", "importance": 0.8}, [], 0)
    up = scoring.rollup(weak, [auth, routes])
    assert up.source == "children" and abs(up.understanding - (0.6 * 0.2 + 0.2 * 1.0) / 0.8) < 1e-9
    assert scoring.rollup(parent, [auth, routes]) is parent  # its own quiz is higher
    assert scoring.rollup(weak, [auth]) is weak  # sub-unit priors alone do not roll up
    assert scoring.split_importance(0.8, [1.0, 3.0]) == [0.2, 0.6]
    assert scoring.split_importance(0.6, [0, 0]) == [0.3, 0.3]
    assert scoring.debt_index([auth, routes], only={"api/routes"}) == 0.0


def test_compute_scores_hierarchy_and_v2_maps(layered_repo):
    root = layered_repo
    store = Store(root)
    store.init()
    recs = [*understood("api"), rec("api/routes", "wrong")]
    scores = {s.unit_id: s for s in compute_scores(root, store, UNITS, "new@x.com", records=recs)}
    assert scores["api/auth"].source == "parent" and scores["api/auth"].understanding == 0.5
    assert scores["api/routes"].source == "quiz" and scores["api/routes"].understanding == 0.0
    assert scores["api"].understanding == 1.0 and scores["api/auth"].parent == "api"
    # deep picks come from sub-units, top-level picks from top-level units
    deep = pick_units(UNITS, list(scores.values()), 5, deep=True)
    assert {u["id"] for u in deep} == {"api/auth", "api/routes", "db"}
    assert next(u for u in deep if u["id"] == "api/auth")["parent_name"] == "API"
    assert [u["id"] for u in pick_units(UNITS, list(scores.values()), 1, within={"db"})] == ["db"]
    # a flat version 2 map (no children) scores as before
    flat = [{k: v for k, v in u.items() if k != "children"} for u in UNITS]
    v2 = compute_scores(root, store, flat, "new@x.com", records=recs)
    assert [s.unit_id for s in v2] == ["api", "db"] and all(s.parent is None for s in v2)
    store.save_map({"version": 2, "units": flat})
    from udebt.mapping import rescore_map

    data = rescore_map(root, store)
    assert data["version"] == 2 and data["units"][0]["churn"] > 0
    assert json.loads(store.map_path.read_text())["units"][1]["id"] == "db"
