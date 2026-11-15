"""Phase 4a: the versioned JSON contract. Every file the CLI writes and every export validate;
a broken payload says where; the export is the import format; testbeds when asked."""

import json
import os
from pathlib import Path

import pytest
from conftest import understood

from udebt import cli, contract, replay
from udebt.store import Store


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setenv("UDEBT_LLM", "fake")


def _mapped(root, capsys, evidence=True):
    cli.main(["--repo", str(root), "init"])
    cli.main(["--repo", str(root), "map"])
    store = Store(root)
    if evidence:
        for r in understood("storage", ts="2026-11-14T10:00:00+00:00"):
            store.append_record("dev1@example.com", {**r, "dev": "dev1@example.com",
                                                     "question": "q", "type": "quiz"})  # fmt: skip
    capsys.readouterr()
    return store


def test_every_file_the_cli_writes_validates(mixed_repo, capsys):
    root = mixed_repo
    store = _mapped(root, capsys)
    cli.main(["--repo", str(root), "snapshot"])
    cli.main(["--repo", str(root), "responsibility", "seed"])
    capsys.readouterr()
    files = {"map": "map.json", "snapshots": "snapshots.json", "commits": "commits.json",
             "responsibility": "responsibility.json"}  # fmt: skip
    for kind, name in files.items():
        data = json.loads((store.dir / name).read_text())
        assert contract.validate(kind, data) == [], name
    for rec in store.load_records("dev1@example.com"):
        assert contract.validate("record", rec) == []


def test_the_export_is_the_import_format(mixed_repo, capsys, tmp_path):
    root = mixed_repo
    store = _mapped(root, capsys)
    out = tmp_path / "e.json"
    assert cli.main(["--repo", str(root), "export", "--format", "json", "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["contract"] == contract.CONTRACT and data["fingerprint"]["rules"]
    assert contract.validate("export", data) == []
    data["map"] = contract.sanitize_map(store.require_map())
    assert contract.validate("import", data) == []
    rc = cli.main(["--repo", str(root), "validate", str(out)])
    assert rc == 0 and "export: conforms to contract 1" in capsys.readouterr().out


def test_a_broken_payload_says_where(mixed_repo, capsys):
    root = mixed_repo
    store = _mapped(root, capsys)
    h = replay.load(root, store)
    good = replay.export_data(h, replay.ensure(h, None, save=False))
    bad = json.loads(json.dumps(good))
    del bad["units"][0]["name"]
    bad["evidence"][0]["verdict"] = "great"
    bad["snapshots"][0]["devs"]["stranger@x"] = {"und": {}, "debt": {}, "index": None}
    errors = contract.validate("export", bad)
    assert any(e.startswith("$.units[0]: missing 'name'") for e in errors)
    assert any("$.evidence[0].verdict: 'great' is not one of" in e for e in errors)
    with pytest.raises(contract.ContractError):
        contract.check("export", bad)
    assert contract.validate("export", {**good, "contract": 99})[0].startswith("$.contract: 99")
    newer = {**good, "units": [{**good["units"][0], "colour": "blue"}] + good["units"][1:]}
    assert contract.validate("export", newer) == [], "fields can be added without a version"
    assert contract.validate("record", {"ts": "t", "unit": "u", "verdict": "x"}) != []
    with pytest.raises(KeyError):
        contract.validate("nope", {})


def test_the_sanitized_map_keeps_specs_and_drops_code_and_answers(fixture_repo, capsys):
    root = fixture_repo
    store = _mapped(root, capsys, evidence=False)
    m = store.require_map()
    u = m["units"][0]
    u["walkthrough"] = {"steps": [{"file": "a.py", "start_line": 1, "end_line": 2}]}
    u["questions"] = {"1": [{"question": "q", "reference_answer": "secret"}]}
    u["orientation"] = {"purpose": "p", "decision": "d"}
    m["tour"] = {"sections": []}
    s = contract.sanitize_map(m)
    assert "walkthrough" not in s["units"][0] and "questions" not in s["units"][0]
    assert s["units"][0]["orientation"]["purpose"] == "p" and s["tour"] == {"sections": []}
    assert "walkthrough" in m["units"][0], "the original is untouched"
    assert contract.validate("map", s) == []


def _testbeds() -> list[str]:
    """UDEBT_TESTBEDS: paths separated by os.pathsep, or 1 for the local testbeds."""
    v = os.environ.get("UDEBT_TESTBEDS", "")
    if v.strip() in ("", "0"):
        return []
    if v.strip() == "1":
        return ["/home/user/digitalme", "/home/user/dsa-instructor",
                "/home/user/testbeds/huggingface_hub"]  # fmt: skip
    return [p for p in v.split(os.pathsep) if p]


@pytest.mark.parametrize("path", _testbeds() or [None])
def test_every_testbed_export_validates(path):
    if path is None:
        pytest.skip("set UDEBT_TESTBEDS=1 (or paths) to validate the testbeds' exports")
    root = Path(path)
    store = Store(root)
    if not store.map_path.exists():
        pytest.skip(f"{path} has no map")
    h = replay.load(root, store)
    res = replay.ensure(h, None, save=False)
    if res.note:
        res = replay.ensure(h, None, rebuild=True, save=False)
    data = replay.export_data(h, res)
    data["map"] = contract.sanitize_map(store.require_map())
    assert contract.validate("import", data) == []
