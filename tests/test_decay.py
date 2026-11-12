"""Decay by the share of a unit changed (Phase 1.4), against the per-mass rule it replaces."""

import math

import pytest
from conftest import git

from udebt import scoring
from udebt.cache import ensure_gitignore
from udebt.mapping import commits_for, weights_for
from udebt.provenance import unit_provenance
from udebt.quiz import compute_scores, unit_sizes_at
from udebt.sizes import count_lines, open_counts, parse_batch, share, unit_sizes
from udebt.store import Config, Store
from udebt.weights import CommitWeight, line_weights

OTHER = {"GIT_AUTHOR_NAME": "Other", "GIT_AUTHOR_EMAIL": "other@example.com"}


def test_decay_models():
    d = scoring.Decay()
    assert d.model == "share" and d.rate == scoring.DECAY_K == 1.0
    assert d.factor(5.0, 0.0) == 1.0, "the share model reads the share, not the mass"
    assert abs(d.factor(0.0, math.log(2)) - 0.5) < 1e-9, "k = 1: halves at 69% changed"
    assert abs(d.factor(0.0, 1.0) - math.exp(-1)) < 1e-12, "a unit's worth: 37% left"
    assert abs(d.factor(0, 0.3) * d.factor(0, 0.2) - d.factor(0, 0.5)) < 1e-12, "composes"
    assert d.factor(2.0, None) == 0.9**2, "no share known: the mass rule"
    m = scoring.Decay("mass")
    assert abs(m.factor(2.0, 0.9) - 0.81) < 1e-12, "the mass model ignores the share"
    assert scoring.Decay.of(0.8) == scoring.Decay("mass", per_mass=0.8)
    with pytest.raises(ValueError):
        scoring.Decay("time")
    # evidence and the prior fade by the same rule
    assert abs(scoring.decayed(1.0, 9.0, d, math.log(2)) - 0.5) < 1e-9
    assert abs(scoring.decayed(1.0, 2, 0.9) - 0.81) < 1e-12  # a number is the mass model
    assert scoring.prior_understanding(1.0, 10.0, d, 0.0) == 0.5
    assert abs(scoring.prior_understanding(1.0, 0.0, d, math.log(2)) - 0.25) < 1e-9
    assert abs(scoring.prior_understanding(1.0, 2.0, 0.9) - 0.405) < 1e-12
    s = scoring.score_unit({"id": "u"}, [{"verdict": "correct"}], 3.0, decay=d, share_since=1.0)
    assert s.share_since == 1.0 and abs(s.understanding - math.exp(-1)) < 1e-12


def test_line_counts_and_unit_sizes(tmp_path):
    assert count_lines(b"") == 0 and count_lines(b"a\nb\n") == 2 and count_lines(b"a\nb") == 2
    assert count_lines(b"\x89PNG\0\0") == 0, "binary files count nothing"
    out = b"aaa blob 4\nx\ny\n\nbbb missing\nccc blob 1\nz\n"
    assert parse_batch(out) == {"aaa": 2, "ccc": 1}

    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "a").mkdir()
    (root / "b").mkdir()
    (root / "a" / "x.py").write_text("1\n2\n3\n")
    (root / "a" / "shared.py").write_text("1\n2\n3\n4\n")
    (root / "b" / "y.py").write_text("1\n")
    (root / "b" / "logo.bin").write_bytes(b"\0\1\2")
    (root / "a" / "package-lock.json").write_text("{}\n" * 50)
    git(root, "add", ".")
    git(root, "-c", "user.name=a", "-c", "user.email=a@b", "commit", "-q", "-m", "init")
    lc = open_counts(root, root / ".udebt")
    lines = lc.at("HEAD")
    assert lines == {"a/x.py": 3, "a/shared.py": 4, "b/y.py": 1, "b/logo.bin": 0}
    assert lc.counted == 4 and (root / ".udebt" / "sizes.json").exists()
    units = [
        {"id": "a", "paths": ["a"]},
        {"id": "b", "paths": ["b", "a/shared.py"]},
    ]
    # a file two units list gives each half of its lines
    assert unit_sizes(units, lines) == {"a": 5.0, "b": 3.0}
    assert share(2.5, 5.0) == 0.5 and share(3.0, 0.0) == 3.0  # an emptied unit: any change
    ensure_gitignore(root / ".udebt")
    assert "sizes.json" in (root / ".udebt" / ".gitignore").read_text().split()


def test_branch_lines_carry_the_branch_weight():
    ws = {
        "m": CommitWeight("m", 0.0),
        "b1": CommitWeight("b1", 0.2, group="m"),
        "b2": CommitWeight("b2", 0.3, group="m"),
        "solo": CommitWeight("solo", 0.4),
        "big": CommitWeight("big1", 0.8, group="g"),
        "big2": CommitWeight("big2", 0.7, group="g"),
    }
    lw = line_weights(ws)
    assert lw["b1"] == lw["b2"] == 0.5 and lw["solo"] == 0.4 and lw["m"] == 0.0
    assert lw["big"] == 1.0, "at most a whole commit's weight"


def _two_units(tmp_path):
    """small/ (5 lines) and big/ (500 lines), both by dev1; then `other` changes 4 lines in
    each, one commit per unit, the same mass."""
    root = tmp_path / "two"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Dev One")
    git(root, "config", "user.email", "dev1@example.com")
    (root / "small").mkdir()
    (root / "big").mkdir()
    (root / "small" / "s.py").write_text("".join(f"s{i} = {i}\n" for i in range(5)))
    (root / "big" / "b.py").write_text("".join(f"b{i} = {i}\n" for i in range(500)))
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    first = git(root, "rev-parse", "HEAD").strip()
    for d, f in (("small", "s.py"), ("big", "b.py")):
        p = root / d / f
        text = p.read_text().splitlines()
        text[:2] = ["changed_a = 1", "changed_b = 2"]
        p.write_text("\n".join(text) + "\n")
        git(root, "commit", "-q", "-am", f"{d}: change two lines", env=OTHER)
    store = Store(root)
    store.init()
    store.save_map(
        {
            "version": 3,
            "units": [
                {"id": "small", "name": "small", "paths": ["small"], "importance": 0.5},
                {"id": "big", "name": "big", "paths": ["big"], "importance": 0.5},
            ],
        }
    )
    return root, store, first


def test_the_same_mass_fades_a_small_unit_more(tmp_path):
    root, store, first = _two_units(tmp_path)
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    recs = [
        {"unit": u, "verdict": "correct", "confidence": 2, "ts": "2026-01-01", "head": first}
        for u in ("small", "big")
    ]
    sizes = unit_sizes_at(root, store, store.require_map()["units"])
    assert sizes == {"small": 5.0, "big": 500.0}

    def scores():
        sc = compute_scores(
            root, store, store.require_map()["units"], "dev2@example.com", recs, commits, weights
        )
        return {s.unit_id: s for s in sc}

    by = scores()
    small, big = by["small"], by["big"]
    assert abs(small.commits_since - big.commits_since) < 0.01, "the same mass landed"
    assert math.isclose(small.share_since, 100 * big.share_since, rel_tol=0.2), "sizes 5, 500"
    assert small.understanding < 0.9 < 0.99 < big.understanding
    assert math.isclose(small.understanding, math.exp(-scoring.DECAY_K * small.share_since))

    cfg = store.config
    cfg.decay_model = "mass"
    store.save_config(cfg)
    by = scores()
    assert by["small"].share_since is None
    assert math.isclose(by["small"].understanding, by["big"].understanding, abs_tol=1e-3)
    assert math.isclose(by["small"].understanding, 0.9 ** by["small"].commits_since)


def test_the_prior_erodes_by_share_too(tmp_path):
    root, store, _ = _two_units(tmp_path)
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    units = store.require_map()["units"]
    share_of = {
        u["id"]: unit_provenance(commits, u["paths"], "dev1@example.com", weights).dev_share
        for u in units
    }

    def fade(s):  # what is left of the prior's 0.5 x dev share
        return s.understanding / (scoring.PRIOR_CAP * share_of[s.unit_id])

    by = {s.unit_id: s for s in compute_scores(root, store, units, "dev1@example.com", [])}
    small, big = by["small"], by["big"]
    assert small.source == big.source == "prior"
    assert math.isclose(small.prior_share, 100 * big.prior_share, rel_tol=0.2)
    assert fade(small) < 0.9 < 0.99 < fade(big)
    cfg = store.config
    cfg.decay_model = "mass"
    store.save_config(cfg)
    by = {s.unit_id: s for s in compute_scores(root, store, units, "dev1@example.com", [])}
    assert by["small"].prior_share is None
    # by mass the prior erodes alike: the same mass by others landed on each unit
    assert math.isclose(fade(by["small"]), fade(by["big"]), rel_tol=0.01)


def _capped_repo(tmp_path):
    """unit/ holds code.py (10 lines) and data.json (1000 lines). Then: data.json deleted; a
    rewrite of all of code.py; code.py grown by 50 lines."""
    root = tmp_path / "cap"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Dev One")
    git(root, "config", "user.email", "dev1@example.com")
    (root / "unit").mkdir()
    (root / "unit" / "code.py").write_text("".join(f"a{i} = {i}\n" for i in range(10)))
    (root / "unit" / "data.json").write_text("".join(f'"{i}",\n' for i in range(1000)))
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    git(root, "rm", "-q", "unit/data.json")
    git(root, "commit", "-q", "-m", "data.json leaves git", env=OTHER)
    (root / "unit" / "code.py").write_text("".join(f"b{i} = {i}\n" for i in range(10)))
    git(root, "commit", "-q", "-am", "rewrite code.py", env=OTHER)
    (root / "unit" / "code.py").write_text("".join(f"b{i} = {i}\n" for i in range(60)))
    git(root, "commit", "-q", "-am", "grow code.py", env=OTHER)
    store = Store(root)
    store.init()
    store.save_map({"version": 3, "units": [{"id": "u", "name": "u", "paths": ["unit"]}]})
    return root, store


def test_a_commit_changes_at_most_all_of_a_unit(tmp_path):
    from udebt.provenance import touches_of

    root, store = _capped_repo(tmp_path)
    commits = commits_for(root, store)
    by = {c.subject: c for c in commits}
    rewrite, delete, grow = by["rewrite code.py"], by["data.json leaves git"], by["grow code.py"]
    assert rewrite.file_dels == {"unit/code.py": 10} and rewrite.lines_in(["unit"]) == 20
    assert delete.lines_in(["unit"]) == 1000, "a whole-file deletion counts its size, once"
    assert delete.growth_in(["unit"]) == -1000 and grow.growth_in(["unit"]) == 50
    lines = {t.commit.subject: t.lines for t in touches_of(commits, ["unit"])}
    assert lines == {
        "initial": 1010,  # all new: the unit's size after it
        "data.json leaves git": 10,  # not 1000 lines against a unit that has 10 left
        "rewrite code.py": 10,  # 10 out and 10 in: the unit rewritten once, not twice
        "grow code.py": 50,  # growth is not capped: the unit is 60 lines after it
    }
    # the running size agrees with the blob count at HEAD
    assert unit_sizes_at(root, store, store.require_map()["units"]) == {"u": 60.0}
    first = commits[0].sha
    weights = weights_for(root, store, commits)
    lw = line_weights(weights)
    recs = [{"unit": "u", "verdict": "correct", "ts": "2026-01-01", "head": first}]
    (s,) = compute_scores(root, store, store.require_map()["units"], "x@example.com", recs)
    expected = sum(lw[c.sha] * n for c, n in ((rewrite, 10), (delete, 10), (grow, 50))) / 60
    assert math.isclose(s.share_since, expected, abs_tol=1e-3)


def test_decay_k_is_the_config_name(tmp_path):
    import json

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"share_decay_rate": 1.386}))
    cfg = Config.load(cfg_path)
    assert cfg.decay_k == scoring.DECAY_K and "share_decay_rate" not in cfg.dump(), "old default"
    cfg_path.write_text(json.dumps({"share_decay_rate": 2.0}))
    cfg = Config.load(cfg_path)
    assert cfg.decay_k == 2.0 and cfg.decay.rate == 2.0, "a chosen rate carries over"
    cfg_path.write_text(json.dumps({"decay_k": 0.5, "share_decay_rate": 2.0}))
    assert Config.load(cfg_path).decay_k == 0.5
