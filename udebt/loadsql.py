"""An idempotent SQL script that loads one worker output (`udebt worker run --out DIR`) into the
app_udebt schema (supabase/migrations/): the repository row and its analysis at that commit.

For the platform's admin workflow, which loads the sample repositories (HOSTING.md: written by
no app code, readable by every signed-in person), and for tests against SQLite. Run it twice
and nothing changes: the repository is upserted by URL and the analysis for (repository, sha,
owner) is replaced inside one transaction. `user_id` None loads a shared analysis (a sample);
a uuid loads one person's own analysis (as that person's token would write it).

Strings are dollar-quoted in Postgres (with a tag that cannot occur in the text) and quoted
with doubled single quotes in SQLite; JSON goes in as `jsonb` in Postgres, text in SQLite.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any


def _quote(text: str, dialect: str) -> str:
    if dialect == "sqlite":
        return "'" + text.replace("'", "''") + "'"
    tag = "u" + secrets.token_hex(4)
    while f"${tag}$" in text:
        tag = "u" + secrets.token_hex(4)
    return f"${tag}${text}${tag}$"


def _lit(v: Any, dialect: str, as_json: bool = False) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return ("true" if v else "false") if dialect == "postgres" else str(int(v))
    if isinstance(v, int):
        return str(v)
    if as_json:
        q = _quote(json.dumps(v, ensure_ascii=False, separators=(",", ":")), dialect)
        return f"{q}::jsonb" if dialect == "postgres" else q
    return _quote(str(v), dialect)


def analysis_sql(
    out_dir: Path,
    *,
    dialect: str = "postgres",
    user_id: str | None = None,
    license: str | None = None,
    sample: bool | None = None,
    with_export: bool = True,
    storage_path: str | None = None,
) -> str:
    """The script for the worker output in `out_dir`."""
    if dialect not in ("postgres", "sqlite"):
        raise ValueError("dialect is postgres or sqlite")
    out = Path(out_dir)
    manifest = json.loads((out / "analysis.json").read_text(encoding="utf-8"))

    def part(name: str) -> Any:
        p = out / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    sample = (user_id is None) if sample is None else sample
    L = lambda v, j=False: _lit(v, dialect, j)  # noqa: E731
    url = manifest["url"]
    repo_id = f"(select id from app_udebt.repositories where url = {L(url)})"
    excluded_license = (
        "coalesce(excluded.license, app_udebt.repositories.license)"
        if dialect == "postgres"
        else "coalesce(excluded.license, license)"
    )
    owner = "user_id is null" if user_id is None else f"user_id = {L(user_id)}"
    lines = [
        f"-- udebt worker sql: {manifest['repo']} at {manifest['sha'][:12]} "
        f"({'shared sample' if user_id is None else 'own analysis'}), contract "
        f"{manifest['contract']}; idempotent",
        "begin;",
        "insert into app_udebt.repositories (url, name, license, is_sample) values "
        f"({L(url)}, {L(manifest['repo'])}, {L(license)}, {L(bool(sample))})",
        f"    on conflict (url) do update set name = excluded.name, license = {excluded_license}, "
        "is_sample = excluded.is_sample;",
        f"delete from app_udebt.analyses where repo_id = {repo_id} and sha = "
        f"{L(manifest['sha'])} and {owner};",
        "insert into app_udebt.analyses (repo_id, sha, user_id, contract, manifest, export, map, "
        "specs, storage_path) values (",
        f"    {repo_id}, {L(manifest['sha'])}, {L(user_id)}, {L(manifest['contract'])},",
        f"    {L(manifest, True)},",
        f"    {L(part('export.json') if with_export else None, True)},",
        f"    {L(part('map.json'), True)},",
        f"    {L(part('specs.json'), True)},",
        f"    {L(storage_path)});",
        "commit;",
    ]
    return "\n".join(lines) + "\n"
