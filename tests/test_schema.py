"""Phase 4a: the app_udebt schema. SQLite always (the fallback: constraints, idempotent loads,
cascades); Postgres when UDEBT_TEST_PG names a server we may create databases on, for what
only Postgres has: own-row RLS on auth.uid(), shared samples, nothing for anon. psql only,
no driver: the core stays stdlib only.

    UDEBT_TEST_PG=postgresql://postgres@127.0.0.1:5432/postgres pytest tests/test_schema.py
"""

import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from pathlib import Path

import pytest

from udebt import cli, worker
from udebt.loadsql import analysis_sql

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))
URL = "https://github.com/acme/proj"
SQLITE_SCHEMA = ROOT / "supabase" / "sqlite" / "app_udebt.sql"


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setenv("UDEBT_LLM", "fake")


@pytest.fixture
def analysis(fixture_repo, capsys, tmp_path):
    cli.main(["--repo", str(fixture_repo), "init"])
    cli.main(["--repo", str(fixture_repo), "map"])
    capsys.readouterr()
    out = tmp_path / "analysis"
    worker.run(str(fixture_repo), out, map_file=fixture_repo / ".udebt" / "map.json",
               log=lambda *_: None)  # fmt: skip
    return out


def _sqlite():
    c = sqlite3.connect(":memory:")
    c.execute("pragma foreign_keys = on")
    c.execute("attach database ':memory:' as app_udebt")
    c.execute("pragma app_udebt.foreign_keys = on")
    return c


def test_sqlite_schema_loads_idempotently_and_cascades(analysis):
    c = _sqlite()
    for _ in range(2):
        c.executescript(SQLITE_SCHEMA.read_text())
    sample = analysis_sql(analysis, dialect="sqlite", license="MIT", url=URL)
    own = analysis_sql(analysis, dialect="sqlite", user_id="u-1", url=URL)
    for _ in range(2):
        c.executescript(sample)
        c.executescript(own)
    repos = c.execute("select id, name, license, is_sample from app_udebt.repositories").fetchall()
    assert len(repos) == 1 and repos[0][1:] == ("proj", "MIT", 0), "the own load ran last"
    rows = c.execute("select user_id, manifest, export, specs from app_udebt.analyses "
                     "order by user_id").fetchall()  # fmt: skip
    assert [r[0] for r in rows] == [None, "u-1"], "one shared, one own: no duplicates"
    manifest, export = json.loads(rows[0][1]), json.loads(rows[0][2])
    assert manifest["repo"] == "proj" and export["format"] == "udebt-export"
    assert {u["id"] for u in json.loads(rows[0][3])["units"]} >= {"storage", "scoring"}
    rid = repos[0][0]
    c.execute("insert into app_udebt.records (user_id, repo_id, unit_id, level, verdict, "
              "confidence) values ('u-1', ?, 'storage', 1, 'correct', 2)", [rid])  # fmt: skip
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("insert into app_udebt.records (user_id, repo_id, unit_id, verdict) values "
                  "('u-1', ?, 'storage', 'great')", [rid])  # fmt: skip
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("insert into app_udebt.repositories (url, name) values ('/local/path', 'x')")
    c.execute("insert into app_udebt.jobs (user_id, url) values ('u-1', ?)", [URL])
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("insert into app_udebt.jobs (user_id, url, status) values ('u-1', 'x', 'odd')")
    c.execute("delete from app_udebt.repositories")
    for t in ("analyses", "records"):
        assert c.execute(f"select count(*) from app_udebt.{t}").fetchone()[0] == 0, t


def test_the_sql_command_and_its_quoting(analysis, capsys, tmp_path):
    assert cli.main(["worker", "sql", str(analysis), "--out", str(tmp_path / "a.sql")]) == 0
    sql = (tmp_path / "a.sql").read_text()
    assert sql.startswith("-- udebt worker sql: proj") and "::jsonb" in sql and "$u" in sql
    assert "is_sample) values" in sql and "user_id is null" in sql
    no_export = analysis_sql(analysis, with_export=False, storage_path="udebt/proj/export.json")
    assert "udebt/proj/export.json" in no_export and len(no_export) < len(sql)


# -- Postgres: row-level security ----------------------------------------------------------

PG = os.environ.get("UDEBT_TEST_PG", "")
STUB_AUTH = """
create schema if not exists auth;
create table if not exists auth.users (id uuid primary key, email text);
create or replace function auth.uid() returns uuid language sql stable as $$
    select nullif(coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''),
           (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')), '')::uuid
$$;
do $$ begin
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin; end if;
    if not exists (select 1 from pg_roles where rolname = 'anon') then
        create role anon nologin; end if;
end $$;
grant usage on schema auth to authenticated, anon;
grant execute on function auth.uid() to authenticated, anon;
create schema if not exists platform;
create table if not exists platform.llm_apps (slug text primary key);
"""


def psql(dsn: str, sql: str, sub: str | None = None, role: str | None = None):
    head = ""
    if role or sub:
        head = f"set role {role or 'authenticated'};\n"
        if sub:
            head += (
                f"select set_config('request.jwt.claims', '{json.dumps({'sub': sub})}', false);\n"
            )
    p = subprocess.run(["psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-d", dsn],
                       input=head + sql, capture_output=True, text=True)  # fmt: skip
    return p.returncode, p.stdout.strip().splitlines()[-1:] or [""], p.stderr


@pytest.fixture
def pg():
    if not PG or shutil.which("psql") is None:
        pytest.skip("set UDEBT_TEST_PG to a Postgres URL (with psql on PATH) to test the RLS")
    name = f"udebt_test_{uuid.uuid4().hex[:10]}"
    assert psql(PG, f"create database {name};")[0] == 0
    dsn = PG.rsplit("/", 1)[0] + "/" + name
    yield dsn
    psql(PG, f"drop database if exists {name} with (force);")


def test_postgres_own_rows_shared_samples_and_nothing_for_anon(pg, analysis):
    ok = lambda r: r[0] == 0  # noqa: E731
    assert ok(psql(pg, STUB_AUTH))
    for _ in range(2):  # idempotent
        for m in MIGRATIONS:
            rc, _, err = psql(pg, m.read_text())
            assert rc == 0, (m.name, err)
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    assert ok(psql(pg, f"insert into auth.users (id) values ('{a}'), ('{b}');"))
    sample = analysis_sql(analysis, license="Apache-2.0", url=URL)
    for _ in range(2):
        rc, _, err = psql(pg, sample)  # the admin workflow: no RLS for the table owner
        assert rc == 0, err
    q = "select count(*) from app_udebt.analyses;"
    assert psql(pg, q)[1] == ["1"]
    # A adds the repository to their list, records evidence and saves their own analysis
    rid = "(select id from app_udebt.repositories limit 1)"
    rc, _, err = psql(
        pg,
        f"""
        insert into app_udebt.my_repositories (repo_id) select {rid};
        insert into app_udebt.records (repo_id, unit_id, level, verdict, confidence)
            select {rid}, 'storage', 1, 'correct', 2;
        insert into app_udebt.analyses (repo_id, sha, contract, manifest)
            select {rid}, sha, 1, '{{}}'::jsonb from app_udebt.analyses limit 1;""",
        sub=a,
    )
    assert rc == 0, err
    assert psql(pg, "select count(*) from app_udebt.records;", sub=a)[1] == ["1"]
    assert psql(pg, q, sub=a)[1] == ["2"], "the sample and their own"
    # B sees both analyses (public repositories: shared, 0002), none of A's own rows, and
    # cannot write as A, change A's analysis or add a sample
    assert psql(pg, q, sub=b)[1] == ["2"]
    assert psql(pg, "select count(*) from app_udebt.records;", sub=b)[1] == ["0"]
    assert psql(pg, "select count(*) from app_udebt.my_repositories;", sub=b)[1] == ["0"]
    rc, _, err = psql(pg, f"insert into app_udebt.records (user_id, repo_id, unit_id, verdict) "
                      f"select '{a}', {rid}, 'storage', 'wrong';", sub=b)  # fmt: skip
    assert rc != 0 and "row-level security" in err
    rc, _, err = psql(pg, "insert into app_udebt.repositories (url, name, is_sample) values "
                      "('https://github.com/x/y', 'y', true);", sub=b)  # fmt: skip
    assert rc != 0 and "row-level security" in err
    rc, _, err = psql(pg, "update app_udebt.analyses set specs = '{}'::jsonb returning id;",
                      sub=b)  # fmt: skip
    assert rc == 0, "shared analyses are read-only to others: the update touches no row"
    assert psql(pg, "select count(*) from app_udebt.analyses where specs = '{}'::jsonb;")[1] == [
        "0"
    ]
    # anon: nothing at all
    rc, _, err = psql(pg, "select count(*) from app_udebt.repositories;", role="anon")
    assert rc != 0 and "permission denied" in err
    # jobs are own rows; a repository must be a public GitHub URL
    assert ok(psql(pg, f"insert into app_udebt.jobs (url) values ('{URL}');", sub=a))
    assert psql(pg, "select count(*) from app_udebt.jobs;", sub=b)[1] == ["0"]
    rc, _, err = psql(pg, "insert into app_udebt.repositories (url, name) values "
                      "('https://evil.example/x', 'x');", sub=b)  # fmt: skip
    assert rc != 0 and "repositories_github_url" in err
    assert psql(pg, "select count(*) from platform.llm_apps where slug = 'udebt';")[1] == ["1"]
    # deleting the account deletes their rows; the shared analyses stay, A's now unowned
    assert ok(psql(pg, f"delete from auth.users where id = '{a}';"))
    assert psql(pg, "select count(*) from app_udebt.records;")[1] == ["0"]
    assert psql(pg, "select count(*) from app_udebt.jobs;")[1] == ["0"]
    assert psql(pg, q)[1] == ["2"]
    assert psql(pg, "select count(*) from app_udebt.analyses where user_id is null;")[1] == ["2"], (
        "two unowned analyses of one commit may coexist"
    )
