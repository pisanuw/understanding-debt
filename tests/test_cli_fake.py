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
    scoring_u = next(u for u in m["units"] if u["id"] == "scoring")
    # churn is weighted mass, not a count: four small commits weigh well under 4
    assert 0.3 < storage["churn"] < 2.0 and storage["provenance"]["total"] == 4
    assert storage["churn"] > scoring_u["churn"]
    imp_storage, imp_scoring = storage["importance"], scoring_u["importance"]
    assert imp_storage == 0.88  # centrality 4 and max churn: 0.6*0.8 + 0.4*1.0

    # dev1 authored every commit, so the own-commit prior gives 0.5 understanding everywhere
    rc, out = run(root, "status", capsys=capsys)
    assert rc == 0 and "Debt index: 50.0" in out and "prior from your commits" in out
    assert "4 commits: 0 ai, 0 assisted, 4 human" in out

    # a developer with no commits sees the unmeasured view
    rc, out = run(root, "--as-user", "new@example.com", "status", capsys=capsys)
    assert "Debt index: 100.0" in out and "unmeasured" in out

    # quiz order is by importance: storage (0.88) then scoring (lower churn).
    # first answer correct (contains "default"), second blank (known unknown)
    answers = iter(["returns a default", "", "3", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    rc, out = run(root, "quiz", "-n", "2", capsys=capsys)
    assert rc == 0
    assert "[CORRECT ]" in out and "[UNKNOWN ]" in out
    recs = Store(root).load_records("dev1@example.com")
    assert len(recs) == 2 and recs[0]["head"] == repo.head(root)
    # storage is now understood; scoring is a known unknown. The quiz answer replaces the
    # own-commit prior, so admitting ignorance costs the 0.5 prior.
    expected = round(100 * imp_scoring / (imp_storage + imp_scoring), 1)
    assert f"Debt index: {expected}" in out

    # a commit touching store.py decays the understanding we just verified
    (root / "udebt" / "store.py").write_text("STATE = {'v': 'final'}\n")
    import subprocess

    subprocess.run(["git", "commit", "-q", "-am", "tweak"], cwd=root, check=True)
    rc, out = run(root, "status", capsys=capsys)
    assert "decayed from 1.00 by mass" in out

    # blackbox scope removes a unit from the index; by default only for you
    rc, out = run(root, "scope", "scoring", "blackbox", capsys=capsys)
    assert rc == 0 and "blackbox for dev1@example.com (team-wide: architectural" in out
    rc, out = run(root, "status", capsys=capsys)
    assert "blackbox for you (excluded)" in out
    idx = float(out.split("Debt index: ")[1].split(" ")[0])
    assert 0 < idx < 10  # only storage left, decayed a little by one small commit
    resp = json.loads((root / ".udebt" / "responsibility.json").read_text())
    assert resp["developers"]["dev1@example.com"]["scope"] == {"scoring": "blackbox"}
    rc, out = run(root, "--as-user", "dev2@example.com", "status", capsys=capsys)
    assert "blackbox" not in out  # a teammate still counts it

    # team view sees the recorded developer
    rc, out = run(root, "status", "--team", capsys=capsys)
    assert rc == 0 and "dev1@example.com" in out and "Team debt index" in out
    assert "Bus target: 0 of 2 architectural units" in out

    # team-wide scope lives in the map, and re-mapping preserves it
    rc, out = run(root, "scope", "scoring", "blackbox", "--team", capsys=capsys)
    assert "team-wide" in out
    rc, out = run(root, "--as-user", "dev2@example.com", "status", capsys=capsys)
    assert "blackbox team-wide (excluded)" in out
    run(root, "map", capsys=capsys)
    m2 = json.loads((root / ".udebt" / "map.json").read_text())
    assert m2["version"] == 3
    assert next(u for u in m2["units"] if u["id"] == "scoring")["scope"] == "blackbox"
    # a developer can take back a team-wide blackbox for themselves
    rc, out = run(root, "scope", "scoring", "architectural", capsys=capsys)
    rc, out = run(root, "status", capsys=capsys)
    assert "architectural for you (team: blackbox)" in out


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


def test_map_rescore_without_llm(fixture_repo, monkeypatch, capsys):
    root = fixture_repo
    run(root, "init", capsys=capsys)
    run(root, "map", capsys=capsys)
    before = Store(root).load_map()
    monkeypatch.setenv("UDEBT_LLM", "none-should-not-be-called")
    rc, out = run(root, "map", "--rescore", capsys=capsys)
    assert rc == 0 and "no LLM call" in out
    after = Store(root).load_map()
    assert "rescored_at" in after and [u["id"] for u in after["units"]] == [
        u["id"] for u in before["units"]
    ]


def test_hierarchy_responsibility_and_risk(layered_repo, monkeypatch, capsys):
    """Phase 2 end to end: clusters feed the map, the map has sub-units, CODEOWNERS seeds
    responsibility sets, and risk flags the unit outside the author's set."""
    root = layered_repo
    run(root, "init", capsys=capsys)
    cfg_path = root / ".udebt" / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["identities"] = {"bob@example.com": ["123+bob@users.noreply.github.com"]}
    cfg_path.write_text(json.dumps(cfg))

    rc, out = run(root, "clusters", capsys=capsys)
    assert rc == 0 and "Structure: 12 source files" in out and "[shop/api/" in out

    rc, out = run(root, "map", capsys=capsys)
    assert rc == 0 and "sub-units) to .udebt/map.json" in out and "cover 100%" in out
    m = Store(root).load_map()
    assert m["version"] == 3 and m["structure"]["clusters"] and m["structure"]["coverage"] == 1.0
    web = next(u for u in m["units"] if "web/src/client.js" in u["paths"])
    db = next(u for u in m["units"] if "shop/db/models.py" in u["paths"])
    assert db.get("children"), "the fake splits a cluster spanning two directories"
    kid = db["children"][0]
    assert kid["id"].startswith(db["id"] + "/")
    assert abs(sum(c["importance"] for c in db["children"]) - db["importance"]) < 0.01

    rc, out = run(root, "--as-user", "alice@example.com", "status", capsys=capsys)
    assert "You have no responsibility set" in out and "sub-units)" in out

    rc, out = run(root, "responsibility", "seed", capsys=capsys)
    assert rc == 0 and ".github/CODEOWNERS and footprint" in out
    assert "@carol" in out and "@lead" in out
    rc, out = run(root, "responsibility", capsys=capsys)
    assert "alice@example.com" in out and "bob@example.com" in out and "Pending" in out
    rc, out = run(root, "responsibility", "alice@example.com", capsys=capsys)
    assert db["id"] in out and "codeowners" in out and "Debt index over the set" in out
    with pytest.raises(SystemExit, match="not mapped"):
        run(root, "responsibility", "@carol", capsys=capsys)

    rc, out = run(root, "--as-user", "alice@example.com", "status", capsys=capsys)
    assert "(your responsibility set:" in out
    rc, out = run(root, "--as-user", "alice@example.com", "status", "--deep", capsys=capsys)
    assert f"  {kid['id']}" in out and "sub-units where they exist" in out
    rc, out = run(root, "--as-user", "alice@example.com", "status", db["id"], capsys=capsys)
    assert f"  {kid['id']}" in out and web["id"] not in out.split("note")[1].split("Debt")[0]

    # alice's last commit lands most of its weight in the web client, outside her set
    rc, out = run(root, "--as-user", "alice@example.com", "risk", "HEAD~1..HEAD", capsys=capsys)
    assert rc == 0 and "->  OUTSIDE" in out and "outside your responsibility set" in out
    first = out.split("files\n")[1].splitlines()[0].split()
    assert first[:3] == ["OUTSIDE", web["id"], "OUT"]
    assert any(ln.split()[1:3] == [db["id"], "in"] for ln in out.splitlines() if ln[:1] in "CHO")
    assert "bob@example.com" in out  # who to ask
    rc, _ = run(
        root, "--as-user", "alice@example.com", "risk", "HEAD~1..HEAD", "--strict", capsys=capsys
    )
    assert rc == 1
    rc, out = run(
        root, "--as-user", "alice@example.com", "risk", "HEAD~1..HEAD", "--deep", capsys=capsys
    )
    rows = [ln.split() for ln in out.split("files\n")[1].splitlines() if ln.strip()]
    assert any(r[1].startswith(db["id"] + "/") and r[2] == "in" for r in rows[:3])

    # a lead adds the web unit to alice's set: the change is no longer OUTSIDE
    rc, out = run(root, "responsibility", "alice@example.com", "add", web["id"], capsys=capsys)
    assert f"{web['id']} added to alice@example.com" in out
    rc, out = run(root, "--as-user", "alice@example.com", "risk", "HEAD~1..HEAD", capsys=capsys)
    assert "OUTSIDE" not in out
    rc, out = run(root, "responsibility", "alice@example.com", "remove", web["id"], capsys=capsys)
    assert "removed from" in out
    resp = json.loads((root / ".udebt" / "responsibility.json").read_text())
    assert resp["developers"]["alice@example.com"]["curated"] is True

    rc, out = run(root, "status", "--team", capsys=capsys)
    assert "bus target 2" in out and "Below target:" in out and "needs 2 more" in out

    # quiz on sub-units, inside alice's set
    answers = iter(["returns a default", "", "2"] * 4)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    rc, out = run(
        root, "--as-user", "alice@example.com", "quiz", "--deep", "-n", "1", capsys=capsys
    )
    assert rc == 0 and "from your responsibility set" in out
    recs = Store(root).load_records("alice@example.com")
    assert recs[-1]["dev"] == "alice@example.com" and "/" in recs[-1]["unit"]
    assert recs[-1]["unit"].split("/")[0] in {
        db["id"],
        *resp["developers"]["alice@example.com"]["units"],
    }

    # per-developer scope for someone else
    rc, out = run(root, "scope", web["id"], "blackbox", "--for", "bob@example.com", capsys=capsys)
    assert "blackbox for bob@example.com" in out
    rc, out = run(root, "--as-user", "bob@example.com", "status", capsys=capsys)
    assert "blackbox for you (excluded)" in out

    # rescoring a version 3 map keeps the hierarchy
    rc, out = run(root, "map", "--rescore", capsys=capsys)
    after = Store(root).load_map()
    assert rc == 0 and after["version"] == 3
    assert [c["id"] for c in hier_children(after, db["id"])] == [c["id"] for c in db["children"]]


def hier_children(m, uid):
    return next(u for u in m["units"] if u["id"] == uid).get("children", [])
