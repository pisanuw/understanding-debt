"""History mining (udebt/history.py): survival, the commit weights, recency and the quota."""

import json
from pathlib import Path

import pytest
from conftest import _write, git

from udebt import cli
from udebt.cache import open_cache
from udebt.history import decision_candidates, periods, survival
from udebt.mapping import commits_for, history_candidates, weights_for
from udebt.provenance import Commit
from udebt.store import Store
from udebt.weights import CommitWeight, decision_weight


def _lines(n: int, tag: str) -> str:
    return "".join(f"{tag}_{i} = {i}\n" for i in range(n))


def _at(date: str) -> dict[str, str]:
    stamp = f"{date}T12:00:00+00:00"
    return {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}


@pytest.fixture
def aging_repo(tmp_path: Path) -> Path:
    """Five years of history: a core package that lives on, an old `legacy/` subproject
    built in 2021 with big commits and deleted in 2022, a helper renamed in 2023, and small
    commits on the core in 2026 (the recent, AI-built era)."""
    root = tmp_path / "aging"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Dev One")
    git(root, "config", "user.email", "dev1@example.com")
    _write(root, {"core/app.py": _lines(30, "app"), "README.md": "# aging\n"})
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial", env=_at("2021-01-04"))
    _write(root, {"core/util.py": _lines(60, "util")})
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "introduce a cache helper", env=_at("2021-01-20"))
    _write(root, {"core/config.py": _lines(40, "cfg")})
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "core: read settings", env=_at("2021-01-25"))
    for k in range(5):  # the subproject: big, with its own dependencies
        _write(
            root,
            {
                f"legacy/mod{k}.py": _lines(250, f"m{k}"),
                "legacy/requirements.txt": "".join(f"pkg{j}\n" for j in range(k + 1)),
            },
        )
        git(root, "add", ".")
        git(root, "commit", "-q", "-m", f"legacy: add stage {k}", env=_at(f"2021-0{k + 2}-10"))
    # one commit that touched the core only a little while building the subproject
    _write(
        root,
        {
            **{f"legacy/wire{k}.py": _lines(80, f"w{k}") for k in range(4)},
            "core/app.py": _lines(31, "app"),
        },
    )
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "legacy: wire the stages into core", env=_at("2021-08-02"))
    git(root, "rm", "-q", "-r", "legacy")
    git(root, "commit", "-q", "-m", "remove the legacy subproject", env=_at("2022-04-12"))
    git(root, "mv", "core/util.py", "core/helpers.py")
    git(root, "commit", "-q", "-m", "rename util to helpers", env=_at("2023-03-01"))
    for k in range(6):  # recent and small
        _write(root, {"core/app.py": _lines(31, "app") + _lines(20 + k, f"r{k}")})
        git(
            root,
            "commit",
            "-q",
            "-am",
            f"switch app step {k} to async\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
            env=_at(f"2026-0{k + 1}-15"),
        )
    Store(root).init()
    return root


def _candidates(root: Path, max_n: int = 40):
    store = Store(root)
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    return history_candidates(root, store, commits, weights, max_n)


def test_deleted_subproject_drops_and_recent_commits_rank_above(aging_repo):
    pool = _candidates(aging_repo, max_n=1000)
    by_subject = {h["subject"]: h for h in pool}
    # nothing the subproject alone touched survives at HEAD, so none of it is a candidate
    assert not [s for s in by_subject if s.startswith("legacy: add")]
    assert "remove the legacy subproject" not in by_subject
    wired = by_subject["legacy: wire the stages into core"]
    assert wired["survival"] == 0.2 and "20% of files alive" in wired["reasons"]
    recent = [h for h in pool if h["date"] >= "2026"]
    assert len(recent) == 6 and all(h["klass"] == "assisted" for h in recent)
    assert all(h["survival"] == 1.0 for h in recent)
    # its weight is higher than any recent commit's, its score lower once survival counts
    assert all(wired["weight"] > h["weight"] for h in recent)
    assert all(wired["score"] < h["score"] for h in recent)
    # a renamed file survives as its successor
    helper = by_subject["introduce a cache helper"]
    assert helper["survival"] == 1.0 and helper["sample_files"] == ["core/util.py"]
    # the first three commits are founding: they stay, with their bonus
    founding = [h["subject"] for h in pool if "founding" in h["reasons"]]
    assert founding == ["initial", "introduce a cache helper", "core: read settings"]
    # the shape the map prompt and FakeLLM read, plus survival and weight
    keys = {"sha", "date", "klass", "agent", "author", "email", "subject", "files", "score"}
    keys |= {"reasons", "sample_files", "survival", "weight"}
    assert keys <= set(pool[0])
    # a tight cut: the founding commits, then only recent ones
    top = _candidates(aging_repo, max_n=6)
    assert [h["subject"] for h in top][:3] == founding
    assert all(h["date"] >= "2026" for h in top[3:]) and len(top) == 6
    assert [h["date"] for h in top] == sorted(h["date"] for h in top)


def test_rename_pairs_are_cached(aging_repo):
    store = Store(aging_repo)
    commits = commits_for(aging_repo, store)
    pc = open_cache(aging_repo, store.dir)
    moves = pc.moves(commits)
    assert list(moves.values()) == [[("core/util.py", "core/helpers.py")]]
    again = open_cache(aging_repo, store.dir)
    again.stats.inputs.clear()
    assert again.moves(commits) == moves and "mv" not in again.stats.inputs


def _commit(i: int, date: str, files: list[str] | None = None, subject: str = "change") -> Commit:
    return Commit(
        sha=f"{i:040x}",
        date=f"{date}T12:00:00+00:00",
        author_name="Dev",
        author_email="dev@example.com",
        subject=subject,
        body="",
        files=files or ["src/a.py"],
    )


def _weights(commits: list[Commit], w: dict[int, float]) -> dict[str, CommitWeight]:
    return {c.sha: CommitWeight(c.sha, w.get(i, 0.6)) for i, c in enumerate(commits)}


def test_ties_go_to_the_more_recent_commit():
    founding = [_commit(i, "2018-01-01") for i in range(3)]  # weight 0: score 0.5 each
    tied = [_commit(3 + i, d) for i, d in enumerate(["2021-01-01", "2026-01-01", "2023-01-01"])]
    commits = founding + tied
    weights = _weights(commits, {0: 0.0, 1: 0.0, 2: 0.0})  # the tied three weigh 0.6
    got = decision_candidates(commits, set(), 1, weights=weights, n_periods=1, quota=0)
    assert [h["date"] for h in got] == ["2026-01-01"]
    got = decision_candidates(commits, set(), 2, weights=weights, n_periods=1, quota=0)
    assert [h["date"] for h in got] == ["2023-01-01", "2026-01-01"]
    assert {h["score"] for h in got} == {0.6}


def test_quota_guarantees_recent_slots():
    """Six heavy old commits and ten moderate recent ones, half the mass each: by score
    alone the old ones take every slot; a quota per period gives the recent half its own."""
    old = [_commit(i, f"2021-0{1 + i}-01") for i in range(6)]
    new = [_commit(6 + i, f"2026-01-{10 + i}") for i in range(10)]
    commits = old + new
    weights = _weights(commits, {i: 1.0 for i in range(6)})
    mass = {sha: w.weight for sha, w in weights.items()}
    split = periods(commits, mass, 2)
    assert [split[c.sha] for c in commits] == [0] * 6 + [1] * 10
    none = decision_candidates(commits, set(), 4, weights=weights, n_periods=2, quota=0)
    assert all(h["date"] < "2026" for h in none)
    some = decision_candidates(commits, set(), 4, weights=weights, n_periods=2, quota=2)
    assert sum(1 for h in some if h["date"] >= "2026") == 2
    # the quota never asks for more than the cut holds
    many = decision_candidates(commits, set(), 4, weights=weights, n_periods=2, quota=10)
    assert len(many) == 4 and sum(1 for h in many if h["date"] >= "2026") == 2


def test_survival_and_decision_weight():
    alive = {"src/b.py", "src/c.py"}
    assert survival(["src/a.py", "src/b.py"], alive, {}) == 0.5
    assert survival(["src/a.py", "src/b.py"], alive, {"src/a.py": "src/c.py"}) == 1.0
    assert survival(["old/x.py"], alive, {"old/x.py": "old/y.py"}) == 0.0
    assert survival(["src/a.py"], None, {}) == 1.0
    # a branch commit ranks by its own weight, not its share of the branch's
    grouped = CommitWeight("s", 0.12, group="m", standalone=0.7)
    assert decision_weight(grouped) == 0.7
    grouped.refined = "architectural"
    assert decision_weight(grouped) == 0.85
    assert decision_weight(CommitWeight("t", 0.4)) == 0.4


def test_history_candidates_cli(aging_repo, capsys):
    rc = cli.main(["--repo", str(aging_repo), "history", "--candidates"])
    out = capsys.readouterr().out
    assert rc == 0 and "decision candidates" in out and "switch app step 5" in out
    assert "legacy: add stage" not in out
    cfg = json.loads((aging_repo / ".udebt" / "config.json").read_text())
    assert cfg["history_quota"] == 5 and cfg["history_periods"] == 4
    rc = cli.main(["--repo", str(aging_repo), "history", "--candidates", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert rc == 0 and {"survival", "weight", "period"} <= set(rows[0])
