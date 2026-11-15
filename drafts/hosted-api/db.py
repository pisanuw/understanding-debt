"""One small database layer over SQLite (stdlib, local runs and tests) and Postgres (psycopg,
Supabase in production), so the rest of the hosted code writes one SQL dialect:

- placeholders are `?` (rewritten to `%s` for psycopg);
- JSON columns are written as JSON text and read back as Python values (`JSON_COLUMNS`);
- booleans come back as bool, whatever the engine stores;
- `conn.upsert(table, row, keys)` is `insert ... on conflict (keys) do update`, which both
  engines speak.

In Postgres, `db.tx(user_id)` runs the transaction as the `authenticated` role with the
request's claims set (`request.jwt.claims`, as Supabase does), so row-level security
(migrations/postgres/0002_rls.sql) filters every read: the permission model is enforced in SQL
as well as in code. Writes go through `db.tx()` (the service role) after the code checks.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from importlib import resources
from pathlib import Path
from typing import Any

JSON_COLUMNS = {
    "settings", "data", "mass", "units", "und", "debt", "depth", "quizzed", "payload", "result",
}  # fmt: skip
BOOL_COLUMNS = {"members_see_individuals", "soft_block", "forced"}


class Conn:
    """A transaction's connection: q (rows as dicts), one, exec, upsert."""

    def __init__(self, raw: Any, kind: str):
        self.raw = raw
        self.kind = kind

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.kind == "postgres" else sql

    @staticmethod
    def _param(v: Any) -> Any:
        if isinstance(v, dict | list):
            return json.dumps(v, ensure_ascii=False, sort_keys=True)
        return v

    @staticmethod
    def _row(cols: list[str], values: tuple) -> dict[str, Any]:
        out = {}
        for c, v in zip(cols, values, strict=True):
            if c in JSON_COLUMNS and isinstance(v, str):
                with suppress(ValueError):
                    v = json.loads(v)
            elif c in BOOL_COLUMNS and v is not None:
                v = bool(v)
            elif hasattr(v, "isoformat"):
                v = v.isoformat()
            elif v is not None and type(v).__name__ == "UUID":
                v = str(v)
            out[c] = v
        return out

    def exec(self, sql: str, params: tuple | list = ()) -> int:
        cur = self.raw.execute(self._sql(sql), [self._param(p) for p in params])
        return cur.rowcount

    def q(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        cur = self.raw.execute(self._sql(sql), [self._param(p) for p in params])
        if cur.description is None:
            return []
        cols = [d[0] for d in cur.description]
        return [self._row(cols, tuple(r)) for r in cur.fetchall()]

    def one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def upsert(self, table: str, row: dict[str, Any], keys: tuple[str, ...]) -> int:
        cols = list(row)
        sets = [c for c in cols if c not in keys]
        sql = (
            f"insert into {table} ({', '.join(cols)}) values ({', '.join('?' for _ in cols)}) "
            f"on conflict ({', '.join(keys)}) do "
            + (f"update set {', '.join(f'{c} = excluded.{c}' for c in sets)}" if sets
               else "nothing")
        )  # fmt: skip
        return self.exec(sql, [row[c] for c in cols])

    def insert_new(self, table: str, row: dict[str, Any], keys: tuple[str, ...]) -> bool:
        """Insert unless a row with these keys exists; True when inserted."""
        cols = list(row)
        sql = (
            f"insert into {table} ({', '.join(cols)}) values ({', '.join('?' for _ in cols)}) "
            f"on conflict ({', '.join(keys)}) do nothing"
        )
        return self.exec(sql, [row[c] for c in cols]) > 0


class Database:
    """`sqlite:///path` (or `sqlite://:memory:`) or `postgresql://...`."""

    def __init__(self, url: str, *, rls: bool = True):
        self.url = url
        self.kind = "postgres" if url.startswith(("postgres://", "postgresql://")) else "sqlite"
        self.rls = rls and self.kind == "postgres"
        self._lock = threading.RLock()
        self._sqlite: sqlite3.Connection | None = None
        if self.kind == "sqlite":
            path = url.split("sqlite://", 1)[1] if url.startswith("sqlite://") else url
            path = path[1:] if path.startswith("/") and not path.startswith("//") else path
            path = path or ":memory:"
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._sqlite = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self._sqlite.execute("pragma foreign_keys = on")
            self._sqlite.execute("pragma journal_mode = wal")

    # -- connections -----------------------------------------------------------------
    @contextmanager
    def tx(self, user_id: str | None = None) -> Iterator[Conn]:
        """One transaction. With `user_id` in Postgres, as that user under row-level
        security; without, as the service (the code has checked the permission)."""
        if self.kind == "sqlite":
            with self._lock:
                raw = self._sqlite
                raw.execute("begin")
                try:
                    yield Conn(raw, "sqlite")
                    raw.execute("commit")
                except BaseException:
                    raw.execute("rollback")
                    raise
            return
        import psycopg

        with psycopg.connect(self.url, autocommit=False) as raw:
            if user_id is not None and self.rls:
                raw.execute("set local role authenticated")
                claims = json.dumps({"sub": str(user_id), "role": "authenticated"})
                raw.execute("select set_config('request.jwt.claims', %s, true)", [claims])
            try:
                yield Conn(raw, "postgres")
                raw.commit()
            except BaseException:
                raw.rollback()
                raise

    # -- migrations ------------------------------------------------------------------
    def migrations(self) -> list[tuple[str, str]]:
        d = resources.files("udebt.hosted").joinpath("migrations", self.kind)
        names = sorted(p.name for p in d.iterdir() if p.name.endswith(".sql"))
        return [(n, d.joinpath(n).read_text(encoding="utf-8")) for n in names]

    def migrate(self) -> list[str]:
        """Apply the migrations not applied yet (recorded in `udebt_migrations`); returns
        their names."""
        done: list[str] = []
        if self.kind == "sqlite":
            with self._lock:
                c = self._sqlite
                c.execute("create table if not exists udebt_migrations (name text primary key)")
                have = {r[0] for r in c.execute("select name from udebt_migrations")}
                for name, sql in self.migrations():
                    if name in have:
                        continue
                    c.executescript("begin;" + sql + ";commit;")
                    c.execute("insert into udebt_migrations (name) values (?)", [name])
                    done.append(name)
            return done
        import psycopg

        with psycopg.connect(self.url, autocommit=True) as c:
            c.execute("create table if not exists udebt_migrations (name text primary key)")
            have = {r[0] for r in c.execute("select name from udebt_migrations").fetchall()}
            for name, sql in self.migrations():
                if name in have:
                    continue
                with c.transaction():
                    c.execute(sql)
                    c.execute("insert into udebt_migrations (name) values (%s)", [name])
                done.append(name)
        return done
