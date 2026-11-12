"""The synthetic-developer experiment, offline (UDEBT_LLM=fake)."""

import json
import subprocess

import pytest

from udebt import cli
from udebt import llm as llm_mod
from udebt.synthetic import permutation_p, ranks, spearman


def test_rank_statistics():
    assert ranks([10, 20, 20, 5]) == [2.0, 3.5, 3.5, 1.0]
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None  # no variation
    assert spearman([1, 2], [1, 2]) is None  # too few
    xs = list(range(10))
    assert permutation_p(xs, xs, n=500) < 0.01
    assert permutation_p(xs, xs[::-1], n=500) > 0.99


def test_experiment_synthetic(fixture_repo, monkeypatch, capsys):
    monkeypatch.setenv("UDEBT_LLM", "fake")
    root = fixture_repo
    seen: dict = {}

    class Spy(llm_mod.FakeLLM):
        def structured(self, prompt, schema, *, cwd, read_tools=True, max_turns=40):
            if "TASK: answer" in prompt:  # what the synthetic developer can see
                seen["cwd"] = cwd
                seen["store"] = (cwd / "udebt" / "store.py").read_text()
                seen["git"] = (cwd / ".git").exists()
            else:
                seen.setdefault("other_cwds", set()).add(cwd)
            return super().structured(
                prompt, schema, cwd=cwd, read_tools=read_tools, max_turns=max_turns
            )

    monkeypatch.setattr(llm_mod, "FakeLLM", Spy)

    def run(*a):
        rc = cli.main(["--repo", str(root), *a])
        return rc, capsys.readouterr().out

    run("init")
    run("map")
    rc, out = run("experiment", "synthetic", "--at", "HEAD~2")
    assert rc == 0 and "nothing recorded" in out and "Mass since vs wrongness" in out
    # the answerer saw the snapshot (store.py as of "store change 0"), with no way back
    assert seen["store"] == "STATE = {'v': 0}\n" and seen["git"] is False
    assert seen["other_cwds"] == {root}  # questions and grades read HEAD
    assert not seen["cwd"].exists()  # the snapshot is gone
    wt = subprocess.run(["git", "worktree", "list"], cwd=root, capture_output=True, text=True)
    assert len(wt.stdout.splitlines()) == 1
    assert not list((root / ".udebt" / "records").glob("*.jsonl"))

    (path,) = (root / ".udebt" / "experiments").glob("synthetic-*.json")
    rep = json.loads(path.read_text())
    at = subprocess.run(
        ["git", "rev-parse", "HEAD~2"], cwd=root, capture_output=True, text=True
    ).stdout.strip()
    assert rep["at_sha"] == at and path.name.startswith(f"synthetic-{at[:8]}-")
    assert rep["commits_since"] == 2 and rep["llm_calls"] == 3
    by = {u["unit"]: u for u in rep["units"]}
    assert by["storage"]["mass_since"] > 0 and by["scoring"]["mass_since"] == 0
    assert 0 < by["storage"]["share_since"] < 1
    # the fake answers the first question and leaves the second blank
    verdicts = sorted(u["verdict"] for u in rep["units"])
    assert verdicts == ["correct", "unknown"]
    assert rep["summary"]["units_scored"] == 2 and "reading" in rep["summary"]


def test_experiment_rejects_head(fixture_repo, monkeypatch, capsys):
    monkeypatch.setenv("UDEBT_LLM", "fake")
    cli.main(["--repo", str(fixture_repo), "init"])
    cli.main(["--repo", str(fixture_repo), "map"])
    with pytest.raises(SystemExit, match="is HEAD"):
        cli.main(["--repo", str(fixture_repo), "experiment", "synthetic", "--at", "HEAD"])
