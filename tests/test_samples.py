"""Phase 4b: loading the samples. The fixture is `udebt worker run` on digitalme with its map,
--no-excerpts and no model call (tests/fixtures/hosted/digitalme); the same checks and SQL
apply to huggingface_hub, sentry-javascript and mlflow when their outputs arrive."""

import json
import os
import shutil
import uuid
from pathlib import Path

import pytest
from test_schema import MIGRATIONS, SQLITE_SCHEMA, STUB_AUTH, _sqlite, psql

from udebt.hosted import samples
from udebt.loadsql import LoadError, analysis_json, analysis_rows, analysis_sql

ROOT = Path(__file__).resolve().parent.parent
DM = ROOT / "tests" / "fixtures" / "hosted" / "digitalme"
WEB = ROOT / "web" / "fixtures" / "digitalme.json"
SAMPLE = {
    "url": "https://github.com/pisanuw/digitalme",
    "license": "MIT",
    "sha": "f93102b121f1da9055c148e8eccde275ccf18236",
}


def test_the_fixture_passes_the_sample_checks_and_others_do_not(tmp_path):
    assert samples.problems(DM, SAMPLE) == []
    assert samples.problems(DM, None)[0].startswith("digitalme: not one of the samples")
    assert "the pin is" in samples.problems(DM, {**SAMPLE, "sha": "0" * 40})[0]
    assert samples.problems(DM, {**SAMPLE, "sha": "0" * 40}, any_sha=True) == []
    bad = tmp_path / "bad"
    shutil.copytree(DM, bad)
    m = json.loads((bad / "analysis.json").read_text())
    (bad / "analysis.json").write_text(
        json.dumps({**m, "code_excerpts": True, "map_source": "none"})
    )
    s = json.loads((bad / "specs.json").read_text())
    next(iter(s["walkthroughs"].values()))["steps"][0]["lines"] = ["x"]
    (bad / "specs.json").write_text(json.dumps(s))
    found = samples.problems(bad, SAMPLE)
    assert {
        "no map",
        "code excerpts in the specs (run with --no-excerpts)",
        "walkthrough steps carry code lines",
    } <= set(found)
    assert samples.problems(tmp_path / "missing", SAMPLE)[0].endswith(")")
    assert samples.summary(DM) == {
        "repo": "digitalme",
        "sha": SAMPLE["sha"],
        "units": 15,
        "orientations": 5,
        "walkthroughs": 1,
        "tour": True,
        "tree_fallback": 1,  # frontend-spa lists only `frontend`: an output from before `files`
        "llm_calls": 0,
    }


def test_the_command_writes_a_script_per_sample_and_refuses_the_rest(tmp_path, capsys):
    hf = tmp_path / "hf"
    shutil.copytree(DM, hf)
    m = json.loads((hf / "analysis.json").read_text())
    pin = samples.SAMPLES["huggingface_hub"]["sha"]
    (hf / "analysis.json").write_text(json.dumps({**m, "repo": "huggingface_hub", "sha": pin}))
    assert (
        samples.main([str(hf), str(DM), str(tmp_path / "nope"), "--out", str(tmp_path / "sql")])
        == 1
    )
    out = capsys.readouterr()
    assert "huggingface_hub.sql" in out.out and "sha256" in out.out
    assert "digitalme: not written" in out.err and "nope: not a worker output" in out.err
    sql = (tmp_path / "sql" / "huggingface_hub.sql").read_text()
    assert "https://github.com/huggingface/huggingface_hub" in sql and "Apache-2.0" in sql
    assert samples.main([str(hf), "--out", str(tmp_path / "sql2")]) == 0


def test_the_public_load_is_idempotent_and_leaves_out_emails_and_the_export():
    c = _sqlite()
    c.executescript(SQLITE_SCHEMA.read_text())
    sql = samples.sql_for(DM, SAMPLE["url"], SAMPLE["license"]).replace("::jsonb", "")
    sqlite = analysis_sql(DM, dialect="sqlite", public=True, url=SAMPLE["url"], license="MIT")
    for _ in range(2):
        c.executescript(sqlite)
    assert c.execute(
        "select url, name, license, is_sample from app_udebt.repositories"
    ).fetchall() == [(SAMPLE["url"], "digitalme", "MIT", 1)]
    rows = c.execute("select user_id, export, specs, manifest from app_udebt.analyses").fetchall()
    assert len(rows) == 1 and rows[0][0] is None and rows[0][1] is None
    assert '"email"' not in rows[0][2] and json.loads(rows[0][3])["public"] is True
    assert "@" not in json.dumps(
        [h for u in json.loads(rows[0][2])["units"] for h in u.get("history", [])]
    )
    assert "$u" in sql, "Postgres strings are dollar-quoted"
    with pytest.raises(LoadError):
        analysis_rows(DM, public=True)  # the manifest's URL is the local clone


def test_the_web_preview_fixture_is_this_output():
    want = analysis_json(DM, public=True, url=SAMPLE["url"], license=SAMPLE["license"])
    assert json.loads(WEB.read_text()) == json.loads(want), (
        "web/fixtures/digitalme.json is stale: udebt worker sql tests/fixtures/hosted/digitalme "
        "--dialect json --public --url https://github.com/pisanuw/digitalme --license MIT "
        "--out web/fixtures/digitalme.json"
    )


PG = os.environ.get("UDEBT_TEST_PG", "")


def test_the_sample_loads_on_postgres_and_every_signed_in_person_reads_it():
    if not PG or shutil.which("psql") is None:
        pytest.skip("set UDEBT_TEST_PG to a Postgres URL (with psql on PATH)")
    name = f"udebt_test_{uuid.uuid4().hex[:10]}"
    assert psql(PG, f"create database {name};")[0] == 0
    dsn = PG.rsplit("/", 1)[0] + "/" + name
    try:
        assert psql(dsn, STUB_AUTH)[0] == 0
        for m in MIGRATIONS:
            assert psql(dsn, m.read_text())[0] == 0
        sql = samples.sql_for(DM, SAMPLE["url"], SAMPLE["license"])
        for _ in range(2):
            rc, _, err = psql(dsn, sql)
            assert rc == 0, err
        stranger = str(uuid.uuid4())
        q = (
            "select count(*) from app_udebt.analyses a join app_udebt.repositories r on "
            "r.id = a.repo_id where r.is_sample and a.specs -> 'units' -> 0 ->> 'id' = 'chat-flow';"
        )
        assert psql(dsn, q, sub=stranger)[1] == ["1"]
        rc, _, err = psql(dsn, "select count(*) from app_udebt.analyses;", role="anon")
        assert rc != 0 and "permission denied" in err
    finally:
        psql(PG, f"drop database if exists {name} with (force);")
