"""Phase 4a: the analysis worker. A transient clone within limits, contract files out, the
clone always deleted."""

import json
import subprocess

import pytest

from udebt import cli, contract, worker
from udebt.store import Store


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setenv("UDEBT_LLM", "fake")


def _map_file(root, capsys):
    cli.main(["--repo", str(root), "init"])
    cli.main(["--repo", str(root), "map"])
    capsys.readouterr()
    return Store(root).map_path


def test_a_given_map_gives_the_export_and_the_specs(mixed_repo, capsys, tmp_path):
    root = mixed_repo
    mp = _map_file(root, capsys)
    work, out = tmp_path / "work", tmp_path / "out"
    work.mkdir()
    m = worker.run(str(root), out, "HEAD~1", map_file=mp, work_dir=work, log=lambda *_: None)
    head1 = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=root, capture_output=True,
                           text=True).stdout.strip()  # fmt: skip
    assert m["sha"] == head1 and m["map_source"] == "given" and m["llm_calls"] == 0
    assert list(work.iterdir()) == [], "the clone is deleted"
    names = {p.name for p in out.iterdir()}
    assert names == {"analysis.json", "weights.json", "structure.json", "history.json",
                     "contributors.json", "export.json", "map.json", "specs.json"}  # fmt: skip
    data = json.loads((out / "export.json").read_text())
    assert contract.validate("import", data) == [] and data["head"] == head1
    specs = json.loads((out / "specs.json").read_text())
    assert {u["id"] for u in specs["units"]} >= {"storage", "scoring"}
    assert json.loads((out / "analysis.json").read_text()) == m
    assert m["files"]["export.json"] == (out / "export.json").stat().st_size


def test_walkthrough_excerpts_and_a_committed_map(fixture_repo, capsys, tmp_path):
    root = fixture_repo
    _map_file(root, capsys)
    monkey_in = iter([""])
    import builtins

    real = builtins.input
    builtins.input = lambda _p="": next(monkey_in, "")
    try:
        cli.main(["--repo", str(root), "dig", "storage", "--no-check"])
        cli.main(["--repo", str(root), "tour"])
    finally:
        builtins.input = real
    capsys.readouterr()
    subprocess.run(["git", "add", "-f", ".udebt/map.json", ".udebt/config.json"], cwd=root,
                   check=True)  # fmt: skip
    subprocess.run(["git", "commit", "-qm", "commit the map"], cwd=root, check=True)
    out = tmp_path / "out"
    m = worker.run(str(root), out, log=lambda *_: None)
    assert m["map_source"] == "repository" and m["code_excerpts"] and m["counts"]["tour"]
    specs = json.loads((out / "specs.json").read_text())
    (step, *_) = specs["walkthroughs"]["storage"]["steps"]
    assert step["file"] == "udebt/store.py" and step["lines"] == ["STATE = {'v': 2}"]
    assert specs["tour"]["markdown"].startswith("# Architecture tour")
    m = worker.run(str(root), tmp_path / "bare", excerpts=False, log=lambda *_: None)
    specs = json.loads((tmp_path / "bare" / "specs.json").read_text())
    assert "lines" not in specs["walkthroughs"]["storage"]["steps"][0] and not m["code_excerpts"]


def test_no_map_writes_the_offline_core_and_says_so(fixture_repo, tmp_path):
    out = tmp_path / "out"
    url = f"file://{fixture_repo}"
    m = worker.run(url, out, log=lambda *_: None)
    assert m["map_source"] == "none" and "no map" in m["note"]
    assert "export.json" not in m["files"] and "weights.json" in m["files"]
    assert m["url"] == url and m["repo"] == "proj"
    assert worker.public_url("https://x-access-token:s3cret@github.com/a/b.git") == (
        "https://github.com/a/b.git"
    )


def test_over_a_limit_nothing_is_written_and_the_clone_goes(fixture_repo, tmp_path):
    work, out = tmp_path / "work", tmp_path / "out"
    work.mkdir()
    with pytest.raises(worker.WorkerError, match="over the 2 limit"):
        worker.run(str(fixture_repo), out, limits=worker.Limits(max_commits=2), work_dir=work,
                   log=lambda *_: None)  # fmt: skip
    assert not out.exists() and list(work.iterdir()) == []
    with pytest.raises(worker.WorkerError, match="MB limit"):
        worker.run(str(fixture_repo), out, limits=worker.Limits(max_mb=0), work_dir=work,
                   log=lambda *_: None)  # fmt: skip
    with pytest.raises(worker.WorkerError):
        worker.run(str(tmp_path / "nope"), out, work_dir=work, log=lambda *_: None)
    assert list(work.iterdir()) == []


def test_the_command_writes_a_map_with_the_model_when_asked(fixture_repo, capsys, tmp_path):
    out = tmp_path / "out"
    rc = cli.main(["worker", "run", str(fixture_repo), "--out", str(out), "--llm",
                   "--walkthroughs", "1"])  # fmt: skip
    text = capsys.readouterr().out
    m = json.loads((out / "analysis.json").read_text())
    assert rc == 0 and m["map_source"] == "llm" and m["llm_calls"] >= 3
    assert m["counts"]["tour"] and m["counts"]["walkthroughs"] == 1
    assert "The clone is deleted." in text
    with pytest.raises(SystemExit, match="nothing written; the clone is deleted"):
        cli.main(["worker", "run", str(tmp_path / "nope"), "--out", str(tmp_path / "x")])


def test_a_unit_over_directories_gets_its_top_files(mixed_repo, tmp_path):
    """Phase 4b, the tree fallback: a unit whose paths are directories gets `files`, the top
    tracked files under them by topfiles.py's rule, so the hosted question writer has code for
    it without a walkthrough; a unit naming only files gets none."""
    root = mixed_repo
    (root / "udebt" / "tests").mkdir()
    (root / "udebt" / "tests" / "test_store.py").write_text("def test():\n    pass\n" * 50)
    (root / "udebt" / "uv.lock").write_text("x\n" * 500)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "tests"], cwd=root, check=True)
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps({"version": 3, "units": [
        {"id": "core", "name": "Core", "paths": ["udebt/"], "children": [
            {"id": "core/store", "name": "Store", "paths": ["udebt/store.py"]}]},
        {"id": "docs", "name": "Docs", "paths": ["README.md", "nowhere"]},
    ]}))  # fmt: skip
    out = tmp_path / "out"
    worker.run(str(root), out, map_file=mp, export=False, log=lambda *_: None)
    units = {u["id"]: u for u in json.loads((out / "specs.json").read_text())["units"]}
    assert units["core"]["files"] == ["udebt/scoring.py", "udebt/store.py",
                                      "udebt/tests/test_store.py"]  # fmt: skip
    assert "files" not in units["core/store"] and "files" not in units["docs"]
    from udebt.topfiles import needs_tree

    assert not any(needs_tree(u, None) for u in units.values())
