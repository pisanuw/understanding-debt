"""The synthetic-developer experiment, offline (UDEBT_LLM=fake)."""

import json
import subprocess

import pytest
from test_provenance import mk

from udebt import cli
from udebt import llm as llm_mod
from udebt.synthetic import (
    change_lines,
    classify_change,
    permutation_p,
    ranks,
    reading,
    spearman,
)


def test_rank_statistics():
    assert ranks([10, 20, 20, 5]) == [2.0, 3.5, 3.5, 1.0]
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None  # no variation
    assert spearman([1, 2], [1, 2]) is None  # too few
    xs = list(range(10))
    assert permutation_p(xs, xs, n=500) < 0.01
    assert permutation_p(xs, xs[::-1], n=500) > 0.99
    assert reading(0.0, 0.49).startswith("no correlation") and "chance" in reading(0.0, 0.49)
    assert reading(0.79, 0.0001).endswith("consistent with decay")
    assert reading(-0.27, 0.81).endswith("against the decay model")


def test_new_versus_edited_lines():
    then = {"app/a.py", "app/b.py"}
    since = [
        mk(sha="1" * 40, files=["app/a.py"], file_lines={"app/a.py": 10}),
        mk(
            sha="2" * 40,
            files=["app/new.py", "app/b.py"],
            file_lines={"app/new.py": 30, "app/b.py": 5},
        ),
        mk(sha="3" * 40, files=["other/x.py"], file_lines={"other/x.py": 99}),  # not the unit
        mk(sha="4" * 40, files=["app/a.py"], file_lines={"app/a.py": 50}, klass="bot"),
        mk(sha="5" * 40, files=["app/yarn.lock"], file_lines={"app/yarn.lock": 400}),  # mechanical
    ]
    assert change_lines(since, ["app"], then) == (15.0, 30.0)
    assert change_lines(since, ["app"], then, split=lambda f: 0.5) == (7.5, 15.0)
    assert classify_change(1.2, 15, 30) == ("mostly-new", 0.333)
    assert classify_change(1.2, 30, 15) == ("mostly-edited", 0.667)
    assert classify_change(0.0, 0, 0) == ("unchanged", None)


class Spy(llm_mod.FakeLLM):
    """FakeLLM that records what each call saw; `fail_answer` makes that answer call fail."""

    log: list = []
    fail_answer: int | None = None

    def structured(self, prompt, schema, *, cwd, read_tools=True, max_turns=40):
        task = prompt.split("\n", 1)[0]
        entry = {"task": task, "cwd": cwd, "max_turns": max_turns}
        if "TASK: answer" in prompt:
            entry["store"] = (cwd / "udebt" / "store.py").read_text()
            entry["git"] = (cwd / ".git").exists()
            entry["questions"] = [ln for ln in prompt.splitlines() if ln.startswith("QUESTION:")]
            n_answers = sum(1 for e in Spy.log if "TASK: answer" in e["task"])
            if Spy.fail_answer is not None and n_answers == Spy.fail_answer:
                Spy.log.append({**entry, "failed": True})
                raise llm_mod.LLMError("claude exited 1: max turns")
        Spy.log.append(entry)
        return super().structured(
            prompt, schema, cwd=cwd, read_tools=read_tools, max_turns=max_turns
        )


@pytest.fixture
def spy(monkeypatch):
    monkeypatch.setenv("UDEBT_LLM", "fake")
    Spy.log, Spy.fail_answer = [], None
    monkeypatch.setattr(llm_mod, "FakeLLM", Spy)
    return Spy


def _run(root, capsys, *a):
    rc = cli.main(["--repo", str(root), *a])
    return rc, capsys.readouterr().out


def _sha(root, rev):
    return subprocess.run(
        ["git", "rev-parse", rev], cwd=root, capture_output=True, text=True
    ).stdout.strip()


def test_experiment_one_snapshot(fixture_repo, spy, capsys):
    root = fixture_repo
    _run(root, capsys, "init")
    _run(root, capsys, "map")
    spy.log.clear()
    rc, out = _run(root, capsys, "experiment", "synthetic", "--at", "HEAD~2")
    assert rc == 0 and "nothing recorded" in out and "mass since vs 1 - score" in out
    tasks = [e["task"] for e in spy.log]
    assert tasks == ["TASK: stable-questions", "TASK: answer", "TASK: grade"]
    (ans,) = [e for e in spy.log if e["task"] == "TASK: answer"]
    # the answerer saw the snapshot (store.py as of "store change 0"), with no way back
    assert ans["store"] == "STATE = {'v': 0}\n" and ans["git"] is False
    assert {e["cwd"] for e in spy.log if e is not ans} == {root}  # questions and grades: HEAD
    assert not ans["cwd"].exists()  # the snapshot is gone
    assert all(e["max_turns"] >= 80 for e in spy.log)
    assert len(ans["questions"]) == 4  # two units, two questions each (the default)
    wt = subprocess.run(["git", "worktree", "list"], cwd=root, capture_output=True, text=True)
    assert len(wt.stdout.splitlines()) == 1
    assert not list((root / ".udebt" / "records").glob("*.jsonl"))

    at = _sha(root, "HEAD~2")
    (path,) = (root / ".udebt" / "experiments").glob("synthetic-*.json")
    assert path.name.startswith(f"synthetic-{at[:8]}-")
    rep = json.loads(path.read_text())
    assert rep["version"] == 2 and rep["llm_calls"] == 3 and rep["per_unit"] == 2
    (snap,) = rep["snapshots"]
    assert snap["at_sha"] == at and snap["commits_since"] == 2 and snap["graded"]
    storage, scoring_ = snap["units"]["storage"], snap["units"]["scoring"]
    assert storage["mass_since"] > 0 and storage["change"] == "mostly-edited"
    assert storage["edited_share"] == 1.0 and storage["lines_since_new"] == 0
    assert scoring_["mass_since"] == 0 and scoring_["change"] == "unchanged"
    # the share model's input and prediction, next to the mass model's
    assert rep["decay"]["model"] == "share"
    assert storage["share_changed"] > 0 and storage["size_lines"] >= 1
    assert storage["predicted"] < 1 and storage["predicted_mass"] < 1
    assert scoring_["share_changed"] == 0 and scoring_["predicted"] == 1.0
    assert snap["mean_share_changed"] == round(storage["share_changed"] / 2, 3)
    # the fake answers every other question: one correct, one unknown per unit
    assert storage["verdict"] == "correct/unknown" and storage["score"] == 0.5
    assert storage["unknown"] == 1 and storage["confident_wrong"] == 0
    sm = snap["summary"]
    assert sm["questions"] == 4 and sm["verdicts"] == {"correct": 2, "unknown": 2}
    assert sm["by_change"]["unchanged"]["units"] == 1
    assert sm["by_change"]["mostly-edited"]["units"] == 1
    assert "reading" in sm["mass_since_vs_wrongness"]
    assert sm["mean_observed"] == 0.5 and 0.5 < sm["mean_predicted"] < 1
    assert "share changed vs 1 - score" in out and "pred" in out


def test_experiment_several_snapshots_share_one_question_set(fixture_repo, spy, capsys):
    root = fixture_repo
    _run(root, capsys, "init")
    _run(root, capsys, "map")
    spy.log.clear()
    rc, out = _run(root, capsys, "experiment", "synthetic", "--snapshots", "HEAD~3,HEAD~1,HEAD~1")
    assert rc == 0 and "Pooled over snapshots" in out and "Same unit, two snapshots" in out
    tasks = [e["task"] for e in spy.log]
    assert tasks == ["TASK: stable-questions"] + ["TASK: answer", "TASK: grade"] * 2
    answers = [e for e in spy.log if e["task"] == "TASK: answer"]
    assert answers[0]["questions"] == answers[1]["questions"]  # one question set
    # nearest snapshot first: HEAD~1 knows 'v': 1, HEAD~3 the initial store
    assert [a["store"] for a in answers] == ["STATE = {'v': 1}\n", "STATE = {}\n"]
    (path,) = (root / ".udebt" / "experiments").glob("synthetic-*.json")
    rep = json.loads(path.read_text())
    assert [s["at_sha"] for s in rep["snapshots"]] == [_sha(root, "HEAD~1"), _sha(root, "HEAD~3")]
    assert rep["llm_calls"] == 5
    near, far = (s["units"]["storage"]["mass_since"] for s in rep["snapshots"])
    assert 0 < near < far
    assert rep["pooled"]["n"] == 4
    # storage moved more since the older snapshot but scored the same; scoring never moved
    assert rep["paired"] == {"concordant": 0, "discordant": 0, "tied": 1, "tau": None}


def test_experiment_resumes_without_paying_twice(fixture_repo, spy, capsys):
    root = fixture_repo
    _run(root, capsys, "init")
    _run(root, capsys, "map")
    spy.log.clear()
    spy.fail_answer = 1  # the second snapshot's answer call fails
    with pytest.raises(SystemExit, match="max turns"):
        _run(root, capsys, "experiment", "synthetic", "--snapshots", "HEAD~1,HEAD~3")
    (path,) = (root / ".udebt" / "experiments").glob("synthetic-*.json")
    part = json.loads(path.read_text())
    assert part["llm_calls"] == 4  # questions, answers, grades, and the failed answers
    assert [s["graded"] for s in part["snapshots"]] == [True, False]
    spy.log.clear()
    spy.fail_answer = None
    rc, out = _run(
        root,
        capsys,
        "experiment",
        "synthetic",
        "--snapshots",
        "HEAD~1,HEAD~3",
        "--resume",
        str(path),
    )
    assert rc == 0 and "reused from the report being resumed" in out
    assert [e["task"] for e in spy.log] == ["TASK: answer", "TASK: grade"]
    rep = json.loads(path.read_text())
    assert rep["llm_calls"] == 6 and all(s["graded"] for s in rep["snapshots"])


def test_experiment_suggest_needs_no_model(fixture_repo, spy, capsys):
    root = fixture_repo
    _run(root, capsys, "init")
    _run(root, capsys, "map")
    spy.log.clear()
    rc, out = _run(root, capsys, "experiment", "synthetic", "--suggest", "0.1,100")
    assert rc == 0 and spy.log == []
    lines = [ln for ln in out.splitlines() if ln.strip().startswith(("0.1", "100.0"))]
    assert len(lines) == 2 and "1/2" in lines[0]  # scoring has not moved since HEAD~1
    # by share: the first commit whose units' mean share changed since reaches the target
    rc, out = _run(root, capsys, "experiment", "synthetic", "--suggest", "share:0.01")
    assert rc == 0 and spy.log == []
    (row,) = [ln for ln in out.splitlines() if ln.strip().startswith("0.01")]
    assert row.split()[1] == _sha(root, "HEAD~1")[:8] and "1/2" in row


def test_experiment_rejects_head(fixture_repo, monkeypatch, capsys):
    monkeypatch.setenv("UDEBT_LLM", "fake")
    cli.main(["--repo", str(fixture_repo), "init"])
    cli.main(["--repo", str(fixture_repo), "map"])
    with pytest.raises(SystemExit, match="is HEAD"):
        cli.main(["--repo", str(fixture_repo), "experiment", "synthetic", "--at", "HEAD"])
