from udebt import risk
from udebt.provenance import Commit
from udebt.scoring import UnitScore
from udebt.weights import CommitWeight

UNITS = [
    {
        "id": "api",
        "name": "API",
        "paths": ["api"],
        "children": [
            {"id": "api/auth", "name": "Auth", "paths": ["api/auth.py"]},
            {"id": "api/routes", "name": "Routes", "paths": ["api/routes.py"]},
        ],
    },
    {"id": "web", "name": "Web", "paths": ["web"]},
]


def score(uid, und, parent=None):
    return UnitScore(uid, 0.5, None, 0, und, "architectural", "prior", parent=parent)


SCORES = {
    "api": score("api", 0.3),
    "api/auth": score("api/auth", 0.2, "api"),
    "api/routes": score("api/routes", 0.7, "api"),
    "web": score("web", 0.2),
}


def change(files: dict[str, int]) -> tuple[list[Commit], dict[str, CommitWeight]]:
    c = Commit(
        sha="c1",
        date="",
        author_name="",
        author_email="",
        subject="",
        body="",
        files=list(files),
        file_lines=files,
    )
    return [c], {"c1": CommitWeight("c1", 1.0)}


def test_verdict_levels():
    assert risk.verdict(0.2, 0.5, 0.6, in_set=False) == "OUTSIDE"
    assert risk.verdict(0.2, 0.5, 0.6, in_set=None) == "CAUTION"  # no set, no OUTSIDE
    assert risk.verdict(0.2, 0.7, 0.6, in_set=False) == "OK"  # understood: fine anywhere
    assert risk.verdict(0.05, 0.0, 0.6, in_set=False) == "OK"  # too little weight
    assert risk.verdict(0.4, 0.1, 0.6, in_set=True) == "HIGH"
    assert risk.overall([]) == "OK"


def test_outside_is_strongest_and_sets_cover_through_parts():
    changes, w = change({"api/auth.py": 50, "web/app.js": 50})
    rs = risk.assess(UNITS, SCORES, changes, w, 0.6, responsible={"api/auth"})
    assert [(r.unit_id, r.level, r.in_set) for r in rs] == [
        ("web", "OUTSIDE", False),
        ("api", "CAUTION", True),  # in the set through the touched sub-unit api/auth
    ]
    assert rs[1].parts == ["auth"] and risk.overall(rs) == "OUTSIDE"
    # touching only a part of api that is not in the set is outside too
    changes, w = change({"api/routes.py": 10, "api/other.py": 10})
    rs = risk.assess(
        UNITS, {**SCORES, "api": score("api", 0.3)}, changes, w, 0.6, responsible={"api/auth"}
    )
    assert rs[0].unit_id == "api" and rs[0].level == "OUTSIDE"
    # no responsibility set: plain levels
    assert risk.assess(UNITS, SCORES, changes, w, 0.6)[0].in_set is None


def test_deep_rows_include_what_only_the_parent_covers():
    changes, w = change({"api/auth.py": 30, "api/models.py": 70})
    rs = {r.unit_id: r for r in risk.assess(UNITS, SCORES, changes, w, 0.6, deep=True)}
    assert set(rs) == {"api/auth", "api (rest)"}
    assert rs["api (rest)"].files == ["api/models.py"]
    assert (
        abs(rs["api (rest)"].mass - 0.7**0.5) < 1e-3 and abs(rs["api/auth"].mass - 0.3**0.5) < 1e-3
    )
    assert risk.touched_units(UNITS, ["web/x.js", "docs/a.md"]) == {"web"}
