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


# -- reading a script back ------------------------------------------------------------------
#
# The site serves the samples to visitors who are not signed in as static JSON (web/public/
# samples/, udebt.hosted.static_samples), the same rows `udebt worker sql --dialect json
# --public` writes. They are derived from the committed load scripts (samples/*.sql), so no
# worker output has to be kept: `rows_from_sql` reads back what `analysis_sql` wrote, in either
# dialect, and `analysis_rows(out)` == `rows_from_sql(analysis_sql(out))` for the same options.

_JSON_COLUMNS = {"manifest", "export", "map", "specs"}
_TOKEN = re.compile(r"\s+|--[^\n]*|[A-Za-z_][A-Za-z0-9_.]*|-?\d+|[(),;=]|\$|'|.", re.S)


def _tokens(text: str) -> list[tuple[str, Any]]:
    """(kind, value) for the script: `str` (a literal, value (text, is_json)), `null`,
    `bool`, `int`, `word`, `punct`; comments and whitespace dropped."""
    out: list[tuple[str, Any]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "$":
            m = re.compile(r"\$([A-Za-z0-9_]*)\$").match(text, i)
            if not m:
                raise LoadError(f"unreadable dollar quote at {i}")
            tag = m.group(0)
            end = text.find(tag, m.end())
            if end < 0:
                raise LoadError(f"unterminated dollar quote {tag}")
            value, i = text[m.end() : end], end + len(tag)
            is_json = text.startswith("::jsonb", i)
            i += 7 if is_json else 0
            out.append(("str", (value, is_json)))
            continue
        if c == "'":
            j, buf = i + 1, []
            while True:
                k = text.find("'", j)
                if k < 0:
                    raise LoadError("unterminated quoted string")
                buf.append(text[j:k])
                if text.startswith("''", k):
                    buf.append("'")
                    j = k + 2
                    continue
                i = k + 1
                break
            out.append(("str", ("".join(buf), False)))
            continue
        m = _TOKEN.match(text, i)
        assert m is not None
        tok, i = m.group(0), m.end()
        if tok.isspace() or tok.startswith("--"):
            continue
        low = tok.lower()
        if low == "null":
            out.append(("null", None))
        elif low in ("true", "false"):
            out.append(("bool", low == "true"))
        elif re.fullmatch(r"-?\d+", tok):
            out.append(("int", int(tok)))
        elif tok in "(),;=":
            out.append(("punct", tok))
        else:
            out.append(("word", low))
    return out


def _insert_values(toks: list[tuple[str, Any]], table: str) -> tuple[list[str], list[Any]]:
    """The column names and values of the script's `insert into <table>`; a parenthesised
    subselect is read as the literals it holds."""
    for i in range(len(toks) - 2):
        if toks[i] == ("word", "insert") and toks[i + 2] == ("word", table):
            break
    else:
        raise LoadError(f"no insert into {table}")
    j = i + 3
    if toks[j] != ("punct", "("):
        raise LoadError(f"{table}: no column list")
    cols: list[str] = []
    j += 1
    while toks[j] != ("punct", ")"):
        if toks[j][0] == "word":
            cols.append(toks[j][1])
        j += 1
    j += 1
    if toks[j] != ("word", "values") or toks[j + 1] != ("punct", "("):
        raise LoadError(f"{table}: no values")
    j += 2
    values: list[Any] = []
    while toks[j] != ("punct", ")"):
        kind, v = toks[j]
        if kind == "punct" and v == ",":
            j += 1
            continue
        if kind == "punct" and v == "(":
            depth, inner = 1, []
            j += 1
            while depth:
                k2, v2 = toks[j]
                depth += (k2, v2) == ("punct", "(")
                depth -= (k2, v2) == ("punct", ")")
                if depth and k2 == "str":
                    inner.append(v2[0])
                j += 1
            values.append(("subselect", inner))
            continue
        values.append(v)
        j += 1
    if len(values) != len(cols):
        raise LoadError(f"{table}: {len(cols)} columns, {len(values)} values")
    return cols, values


def rows_from_sql(text: str) -> dict[str, Any]:
    """The repository row and the analysis row a script from `analysis_sql` loads, as
    `analysis_rows` gives them (JSON columns parsed)."""
    toks = _tokens(text)
    rcols, rvals = _insert_values(toks, "app_udebt.repositories")
    acols, avals = _insert_values(toks, "app_udebt.analyses")

    def plain(v: Any, col: str) -> Any:
        if isinstance(v, tuple) and len(v) == 2 and isinstance(v[1], bool):
            text_, is_json = v
            return json.loads(text_) if is_json or col in _JSON_COLUMNS else text_
        return v

    if not {"url", "name"} <= set(rcols) or not {"sha", "manifest", "specs"} <= set(acols):
        raise LoadError("not a script `udebt worker sql` wrote (columns missing)")
    repo = {c: plain(v, c) for c, v in zip(rcols, rvals, strict=True)}
    repo["is_sample"] = bool(repo.get("is_sample"))
    an = {c: plain(v, c) for c, v in zip(acols, avals, strict=True)}
    an.pop("repo_id", None)
    return {
        "repository": {k: repo.get(k) for k in ("url", "name", "license", "is_sample")},
        "analysis": {"repo_url": repo["url"], **{k: an.get(k) for k in (
            "sha", "user_id", "contract", "manifest", "export", "map", "specs", "storage_path")}},
    }  # fmt: skip


def json_from_sql(text: str) -> str:
    """`analysis_json` for the analysis a script loads: one fixture document."""
    rows = rows_from_sql(text)
    doc = {"repositories": [rows["repository"]], "analyses": [rows["analysis"]]}
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n"
