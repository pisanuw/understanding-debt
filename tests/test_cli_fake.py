"""End-to-end through the CLI with the FakeLLM (UDEBT_LLM=fake)."""

import json

import pytest

from udebt import cli, repo
from udebt.store import Store


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setenv("UDEBT_LLM", "fake")


def run(root, *args, inputs=None, capsys=None):
    rc = cli.main(["--repo", str(root), *args])
    out = capsys.readouterr().out if capsys else ""
    return rc, out


def test_full_flow(fixture_repo, monkeypatch, capsys):
    root = fixture_repo
    assert run(root, "init", capsys=capsys)[0] == 0
    assert (root / ".udebt" / "config.json").exists()

    rc, out = run(root, "map", capsys=capsys)
    assert rc == 0
    m = Store(root).load_map()
    ids = [u["id"] for u in m["units"]]
    assert ids == ["scoring", "storage"]  # most central first
    storage = next(u for u in m["units"] if u["id"] == "storage")
    assert storage["churn"] == 4 and storage["importance"] == 0.88  # 0.6*0.8 + 0.4*1.0

    # dev1 authored every commit, so the own-commit prior gives 0.5 understanding everywhere
    rc, out = run(root, "status", capsys=capsys)
    assert rc == 0 and "Debt index: 50.0" in out and "prior from your commits" in out
    assert "4 commits: 0 ai, 0 assisted, 4 human" in out

    # a developer with no commits sees the unmeasured view
    rc, out = run(root, "--as-user", "new@example.com", "status", capsys=capsys)
    assert "Debt index: 100.0" in out and "unmeasured" in out

    # quiz order is by importance: storage (0.88) then scoring (0.70, one commit of churn).
    # first answer correct (contains "default"), second blank (known unknown)
    answers = iter(["returns a default", "", "3", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    rc, out = run(root, "quiz", "-n", "2", capsys=capsys)
    assert rc == 0
    assert "[CORRECT ]" in out and "[UNKNOWN ]" in out
    recs = Store(root).load_records("dev1@example.com")
    assert len(recs) == 2 and recs[0]["head"] == repo.head(root)
    # storage (0.88) is now understood; scoring (0.70) is a known unknown. The quiz answer
    # replaces the own-commit prior, so admitting ignorance costs the 0.5 prior.
    assert "Debt index: 44.3" in out  # 0.70 / 1.58

    # a commit touching store.py decays the understanding we just verified
    (root / "udebt" / "store.py").write_text("STATE = {'v': 'final'}\n")
    import subprocess

    subprocess.run(["git", "commit", "-q", "-am", "tweak"], cwd=root, check=True)
    rc, out = run(root, "status", capsys=capsys)
    assert "decayed from 1.00" in out and " 0.90 " in out

    # blackbox scope removes a unit from the index
    rc, out = run(root, "scope", "scoring", "blackbox", capsys=capsys)
    assert rc == 0
    rc, out = run(root, "status", capsys=capsys)
    assert "blackbox (excluded)" in out and "Debt index: 10.0" in out  # only storage at 0.90

    # team view sees the recorded developer
    rc, out = run(root, "status", "--team", capsys=capsys)
    assert rc == 0 and "dev1-example-com" in out and "Team debt index" in out

    # re-mapping preserves the scope decision
    run(root, "map", capsys=capsys)
    m2 = json.loads((root / ".udebt" / "map.json").read_text())
    assert next(u for u in m2["units"] if u["id"] == "scoring")["scope"] == "blackbox"


def test_dig_records_check(fixture_repo, monkeypatch, capsys):
    root = fixture_repo
    run(root, "init", capsys=capsys)
    run(root, "map", capsys=capsys)
    answers = iter(["the default", "", "2"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    rc, out = run(root, "dig", "scoring", capsys=capsys)
    assert rc == 0 and "fake walkthrough" in out and "[CORRECT]" in out
    assert len(Store(root).load_records("dev1@example.com")) == 1


def test_unknown_unit_errors(fixture_repo, capsys):
    run(fixture_repo, "init", capsys=capsys)
    run(fixture_repo, "map", capsys=capsys)
    with pytest.raises(SystemExit, match="Unknown unit"):
        run(fixture_repo, "scope", "nope", "blackbox", capsys=capsys)


def test_map_enforces_max_units(fixture_repo, capsys):
    run(fixture_repo, "init", capsys=capsys)
    rc, out = run(fixture_repo, "map", "--max-units", "1", capsys=capsys)
    assert rc == 0
    units = Store(fixture_repo).load_map()["units"]
    assert [u["id"] for u in units] == ["scoring"]  # centrality 5 beats 4


def test_dig_without_check_line_warns(fixture_repo, monkeypatch, capsys):
    from udebt import llm as llm_mod

    class NoCheck(llm_mod.FakeLLM):
        def text(self, prompt, **kw):
            return "# Walkthrough only\n"

    monkeypatch.setattr(llm_mod, "FakeLLM", NoCheck)
    run(fixture_repo, "init", capsys=capsys)
    run(fixture_repo, "map", capsys=capsys)
    rc, out = run(fixture_repo, "dig", "scoring", capsys=capsys)
    assert rc == 0 and "nothing was recorded" in out
    assert Store(fixture_repo).load_records("dev1@example.com") == []


def test_alias_and_history(mixed_repo, capsys):
    root = mixed_repo
    run(root, "init", capsys=capsys)
    run(root, "map", capsys=capsys)
    rc, out = run(root, "history", capsys=capsys)
    assert rc == 0 and "persist to json" in out and "you+ai" in out
    rc, out = run(root, "alias", "dev2@example.com", "Dev2@Example.com", capsys=capsys)
    assert rc == 0 and "dev2@example.com" in out
    cfg = json.loads((root / ".udebt" / "config.json").read_text())
    assert cfg["identities"] == {"dev1@example.com": ["dev2@example.com"]}
    rc, out = run(root, "status", capsys=capsys)
    assert "yours: 6" in out
