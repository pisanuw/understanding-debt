"""An idempotent SQL script that loads one worker output (`udebt worker run --out DIR`) into the
app_udebt schema (supabase/migrations/): the repository row and its analysis at that commit.

For the platform's admin workflow, which loads the sample repositories (HOSTING.md: written by
no app code, readable by every signed-in person), and for tests against SQLite. Run it twice
and nothing changes: the repository is upserted by URL and the analysis for (repository, sha,
owner) is replaced inside one transaction. `user_id` None loads a shared analysis (a sample);
a uuid loads one person's own analysis (as that person's token would write it).

Strings are dollar-quoted in Postgres (with a tag that cannot occur in the text) and quoted
with doubled single quotes in SQLite; JSON goes in as `jsonb` in Postgres, text in SQLite.
`analysis_rows` gives the same values as rows (the `json` dialect of `udebt worker sql`): the
web app's preview and tests seed its in-memory store with them.

`public` (`--public`) is the hosted app's load of a public GitHub repository (Phase 4b): the URL
must be `https://github.com/<owner>/<name>` (`url` overrides the manifest's, which is whatever
the worker cloned), the export is left out (it is per-developer series keyed by commit email,
which the learner app does not use), and commit emails are dropped from the units' history.
"""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any

GITHUB = re.compile(r"^https://github\.com/[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")


class LoadError(ValueError):
    pass


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


def _no_emails(data: Any) -> Any:
    """`data` with the `email` of every history entry left out (a copy)."""
    if isinstance(data, dict):
        return {
            k: _no_emails(v)
            for k, v in data.items()
            if not (k == "email" and "sha" in data and "subject" in data)
        }
    if isinstance(data, list):
        return [_no_emails(v) for v in data]
    return data


def analysis_rows(
    out_dir: Path,
    *,
    user_id: str | None = None,
    license: str | None = None,
    sample: bool | None = None,
    with_export: bool = True,
    storage_path: str | None = None,
    public: bool = False,
    url: str | None = None,
) -> dict[str, Any]:
    """The repository row and the analysis row for the worker output in `out_dir`, as the
    migrations take them (supabase/migrations/)."""
    out = Path(out_dir)
    manifest = json.loads((out / "analysis.json").read_text(encoding="utf-8"))

    def part(name: str) -> Any:
        p = out / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    url = (url or manifest["url"]).rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    if public and not GITHUB.match(url):
        raise LoadError(f"a public load needs a https://github.com/<owner>/<name> URL, not {url}")
    manifest = {**manifest, "url": url}
    specs, map_ = part("specs.json"), part("map.json")
    export = part("export.json") if with_export and not public else None
    if public:
        specs, map_, manifest = _no_emails(specs), _no_emails(map_), {**manifest, "public": True}
    sample = (user_id is None) if sample is None else sample
    return {
        "repository": {
            "url": url,
            "name": manifest["repo"],
            "license": license,
            "is_sample": bool(sample),
        },  # fmt: skip
        "analysis": {
            "repo_url": url,
            "sha": manifest["sha"],
            "user_id": user_id,
            "contract": manifest["contract"],
            "manifest": manifest,
            "export": export,
            "map": map_,
            "specs": specs,
            "storage_path": storage_path,
        },
    }


def analysis_json(out_dir: Path, **kw: Any) -> str:
    """The rows as one fixture document: {"repositories": [...], "analyses": [...]}."""
    rows = analysis_rows(out_dir, **kw)
    doc = {"repositories": [rows["repository"]], "analyses": [rows["analysis"]]}
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n"


def analysis_sql(
    out_dir: Path,
    *,
    dialect: str = "postgres",
    user_id: str | None = None,
    license: str | None = None,
    sample: bool | None = None,
    with_export: bool = True,
    storage_path: str | None = None,
    public: bool = False,
    url: str | None = None,
) -> str:
    """The script for the worker output in `out_dir`."""
    if dialect not in ("postgres", "sqlite"):
        raise ValueError("dialect is postgres or sqlite")
    rows = analysis_rows(out_dir, user_id=user_id, license=license, sample=sample,
                         with_export=with_export, storage_path=storage_path, public=public,
                         url=url)  # fmt: skip
    repo, an = rows["repository"], rows["analysis"]
    manifest = an["manifest"]
    L = lambda v, j=False: _lit(v, dialect, j)  # noqa: E731
    url = repo["url"]
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
        f"({L(url)}, {L(repo['name'])}, {L(repo['license'])}, {L(repo['is_sample'])})",
        f"    on conflict (url) do update set name = excluded.name, license = {excluded_license}, "
        "is_sample = excluded.is_sample;",
        f"delete from app_udebt.analyses where repo_id = {repo_id} and sha = "
        f"{L(manifest['sha'])} and {owner};",
        "insert into app_udebt.analyses (repo_id, sha, user_id, contract, manifest, export, map, "
        "specs, storage_path) values (",
        f"    {repo_id}, {L(manifest['sha'])}, {L(user_id)}, {L(manifest['contract'])},",
        f"    {L(manifest, True)},",
        f"    {L(an['export'], True)},",
        f"    {L(an['map'], True)},",
        f"    {L(an['specs'], True)},",
        f"    {L(storage_path)});",
        "commit;",
    ]
    return "\n".join(lines) + "\n"
