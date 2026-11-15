"""Phase 3.5: each ladder level fades at its own rate (config decay_k_by_level)."""

import json
import math

import pytest
from conftest import git

from udebt import cli, replay, scoring, synthetic
from udebt.quiz import compute_scores, unit_sizes_at
from udebt.store import Config, Store

OTHER = {"GIT_AUTHOR_NAME": "Other", "GIT_AUTHOR_EMAIL": "other@example.com"}
T0 = "2026-01-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setenv("UDEBT_LLM", "fake")


def rec(level, verdict="correct", head=None, unit="engine", **extra):
    out = {"unit": unit, "level": level, "verdict": verdict, "confidence": 2, "ts": T0}
    return {**out, "head": head, **extra}


def test_each_level_fades_at_its_own_rate():
    d = scoring.Decay()
    assert d.rates == (scoring.K_LOW,) * 3 + (scoring.K_HIGH,) * 2 == (0.2, 0.2, 0.2, 1.0, 1.0)
    assert d.rate_at(3) == 0.2 and d.rate_at(5) == d.rate_at() == d.prior_rate == 1.0
    assert d.factor(0, 1.0, 3) == math.exp(-0.2) and d.factor(0, 1.0) == math.exp(-1)
    recs = [rec(n) for n in (1, 2, 3, 4, 5)]
    s = 1.5
    want = (3 * math.exp(-0.2 * s)) / 3 + 0.1 * 2 * math.exp(-s)
    assert math.isclose(scoring.faded(recs, 0, d, s), want), "levels fade, then combine"
    # one rate everywhere is the rule before Phase 3.5: verified x fade
    one = scoring.Decay(by_level=None)
    assert math.isclose(scoring.faded(recs, 0, one, s), 1.2 * math.exp(-s))
    assert scoring.Decay(by_level=(1.0,) * 5).rates == one.rates
    # the mass model fades every level alike
    m = scoring.Decay("mass")
    assert math.isclose(scoring.faded(recs, 2.0, m, s), 1.2 * 0.81)
    # a confident misconception fades towards 0 like a pass; the floor of 0 holds
    neg = [rec(1, "wrong", confidence=3)]
    assert scoring.faded(neg, 0, d, s) == 0.0
    mixed = [rec(1), rec(2), rec(3), rec(4, "wrong", confidence=3)]
    assert math.isclose(scoring.faded(mixed, 0, d, s), math.exp(-0.2 * s) - 0.05 * math.exp(-s))
    # the flat model fades its one value at level 5's rate
    assert math.isclose(scoring.faded(recs[:3], 0, d, s, model="flat"), math.exp(-s))
    with pytest.raises(ValueError):
        scoring.Decay(by_level=(1.0, 1.0))


def test_the_prior_fades_at_level_5s_rate():
    d = scoring.Decay(by_level=(0.1, 0.1, 0.1, 0.5, 2.0))
    assert math.isclose(scoring.prior_understanding(1.0, 0.0, d, 0.5), 0.5 * math.exp(-1.0))
    assert scoring.prior_understanding(1.0, 2.0, scoring.Decay("mass")) == 0.5 * 0.9**2


def test_status_words():
    d = scoring.Decay()
    assert scoring.fade_text(d, [1, 2, 3]) == "levels 1-3 (k 0.2)"
    assert scoring.fade_text(d, [1, 3, 5]) == "levels 1, 3 (k 0.2) and 5 (k 1)"
    assert scoring.fade_text(d, [5]) == "level 5 (k 1)"
    s = scoring.score_unit({"id": "u"}, [rec(1), rec(2), rec(3)], 1.2, decay=d, share_since=0.5)
    assert s.levels == (1, 2, 3)
    note = scoring.decay_note(s, d)
    assert note == "decayed from 1.00 by share 0.50 at levels 1-3 (k 0.2) (mass 1.2)"
    assert scoring.decay_note(s, d, "flat").endswith("at level 5 (k 1) (mass 1.2)")
    line = cli._decay_line(d)
    assert "k 0.2 at levels 1-3, 1 at levels 4-5 and the own-commit prior" in line
    assert "k 1 at every level" in cli._decay_line(scoring.Decay(by_level=None))


def test_config_reads_decay_k_by_level(tmp_path):
    p = tmp_path / "config.json"

    def load(data):
        p.write_text(json.dumps(data))
        return Config.load(p)

    assert Config().decay.rates == scoring.Decay().rates, "unset: the defaults"
    assert "decay_k_by_level" not in Config().dump(), "unset stays unset: a refit applies"
    assert load({"decay_k": 1.0}).decay.rates == (0.2, 0.2, 0.2, 1.0, 1.0), "1.0 was a default"
    chosen = load({"decay_k": 1.7}).decay
    assert chosen.by_level is None and chosen.rates == (1.7,) * 5, "a chosen single k"
    cfg = load({"decay_k": 1.7, "decay_k_by_level": {"3": 0.5, "5": 2}})
    assert cfg.decay.rates == (0.2, 0.2, 0.5, 1.0, 2.0), "levels left out keep the defaults"
    assert cfg.dump()["decay_k_by_level"] == {"3": 0.5, "5": 2}
    assert load({"decay_k_by_level": {str(n): 1.0 for n in range(1, 6)}}).decay.rates == (1.0,) * 5
    for bad in ({"6": 1.0}, {"x": 1.0}, {"2": -1}):
        with pytest.raises(ValueError):
            _ = load({"decay_k_by_level": bad}).decay


def _rewritten(tmp_path):
    """core/engine.py (40 lines) by dev1, then four full rewrites by someone else: about two
    and a third units' worth of change."""
    root = tmp_path / "rw"
    (root / "core").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Dev One")
    git(root, "config", "user.email", "dev1@example.com")
    engine = root / "core" / "engine.py"
    engine.write_text("".join(f"def f{i}(x):\n    return x + {i}\n" for i in range(20)))
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    first = git(root, "rev-parse", "HEAD").strip()
    for r in range(4):
        engine.write_text("".join(f"def g{r}_{i}(x):\n    return x * {i + r}\n" for i in range(20)))
        git(root, "commit", "-q", "-am", f"rewrite the engine, round {r}", env=OTHER)
    store = Store(root)
    store.init()
    units = [{"id": "engine", "name": "engine", "paths": ["core"], "importance": 1.0}]
    store.save_map({"version": 3, "generated_at": T0, "units": units})
    return root, store, first


def test_a_level_3_pass_survives_what_erases_a_level_5_pass(tmp_path, capsys):
    root, store, first = _rewritten(tmp_path)
    units = store.require_map()["units"]
    assert unit_sizes_at(root, store, units) == {"engine": 40.0}
    arch = [rec(n, head=first) for n in (1, 2, 3)]
    deep = arch + [rec(4, head=first), rec(5, head=first)]

    def score(recs):
        (s,) = compute_scores(root, store, units, "a@example.com", recs)
        return s

    a, d, five = score(arch), score(deep), score([rec(5, head=first)])
    share = a.share_since
    assert 2.0 < share < 2.55, "more than two units' worth, but not enough to fade 1-3 to 0.6"
    known = store.config.known_threshold
    assert a.understanding >= known, "levels 1-3 passed: still known"
    assert math.isclose(a.understanding, math.exp(-scoring.K_LOW * share))
    # the same change leaves a level-5 pass under a tenth of what it was
    assert five.understanding < 0.1 * scoring.DEPTH_BONUS
    assert math.isclose(d.understanding - a.understanding, 0.2 * math.exp(-share), rel_tol=1e-6)
    for r in arch:
        store.append_record("a@example.com", {"type": "quiz", "dev": "a@example.com", **r})
    rc = cli.main(["--repo", str(root), "--as-user", "a@example.com", "status"])
    out = capsys.readouterr().out
    assert rc == 0 and f"by share {share:.2f} at levels 1-3 (k 0.2)" in out

    # under one rate for every level (the rule before Phase 3.5) the same pass is erased too
    cfg = store.config
    cfg.decay_k_by_level = {str(n): 1.0 for n in range(1, 6)}
    store.save_config(cfg)
    single = score(arch)
    assert single.understanding < 0.1 < known
    assert math.isclose(single.understanding, math.exp(-share))


def test_the_replay_uses_the_same_rates(tmp_path, capsys):
    root, store, first = _rewritten(tmp_path)
    for r in [rec(n, head=first) for n in (1, 2, 3)]:
        store.append_record("a@example.com", {"type": "quiz", "dev": "a@example.com", **r})
    cli.main(["--repo", str(root), "snapshot"])
    capsys.readouterr()
    data = json.loads((root / ".udebt" / "snapshots.json").read_text())
    assert data["fingerprint"]["rules"] == replay.RULES == 4
    assert data["fingerprint"]["decay"][-1] == [0.2, 0.2, 0.2, 1.0, 1.0]
    (s,) = compute_scores(root, store, store.require_map()["units"], "a@example.com")
    assert data["points"][-1]["devs"]["a@example.com"]["und"]["engine"] == round(s.understanding, 3)
    # a series made under the Phase 3.2 rules is stale, kept with a note until --rebuild
    data["fingerprint"]["rules"] = 3
    (root / ".udebt" / "snapshots.json").write_text(json.dumps(data))
    res = replay.ensure(replay.load(root, store), save=False)
    assert res.note and "scoring rules" in res.note
    export = replay.export_data(replay.load(root, store), replay.ensure(replay.load(root, store)))
    dec = export["settings"]["decay"]
    assert dec["by_level"] == {"1": 0.2, "2": 0.2, "3": 0.2, "4": 1.0, "5": 1.0}
    assert dec["prior_rate"] == 1.0 and dec["rate"] == 1.0


def test_synthetic_prediction_is_at_the_runs_level(tmp_path):
    root, store, first = _rewritten(tmp_path)
    head = git(root, "rev-parse", "HEAD").strip()
    from udebt.mapping import commits_for, weights_for

    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    units = store.require_map()["units"]
    sizes = unit_sizes_at(root, store, units, head)
    rows = {}
    for level in (3, 5):
        _, r = synthetic.unit_rows(
            root, first, head, units, units, commits, weights, sizes, scoring.Decay(), level
        )
        rows[level] = r["engine"]
    s = rows[3]["share_changed"]
    assert rows[3]["predicted"] == round(math.exp(-0.2 * s), 3)
    assert rows[5]["predicted"] == round(math.exp(-s), 3)
