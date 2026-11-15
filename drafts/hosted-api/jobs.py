"""The job queue and the worker: analysis of a transient clone, metadata kept.

A job is a row in `jobs` (queued, running, done, failed). The webhook enqueues `analyze` on a
push to the default branch and `check` on pull-request events; an owner or lead can enqueue
`analyze` by hand (`POST .../jobs`). `udebt-hosted worker` claims one job at a time
(`for update skip locked` in Postgres) and runs it:

1. clone the repository into a fresh temporary directory (the installation token in the URL
   for a private GitHub repository; a plain URL or a local path otherwise) and check out the
   commit the job names;
2. give it a `.udebt/`: the repository's own committed one when it has a map (udebt commits
   the map, records and responsibility sets by default), else the map and responsibility sets
   stored here; either way the evidence recorded through the hosted API is added to the
   records, so the analysis counts it;
3. `analyze`: replay the history exactly as `udebt export` does (replay.load, replay.ensure,
   replay.export_data) and import the result as the service; `check`: assess the pull
   request's commits for its author as `udebt risk` does, build the check run (checks.py),
   store it and post it through the GitHub App;
4. delete the clone, always (a `finally`): only the imported metadata persists.

No model call: the map comes from the repository or an earlier push (mapping needs the
organisation's API key, Phase 4b).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import traceback
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .db import Conn, Database
from .permissions import Viewer

SERVICE = Viewer("service", "owner")


def enqueue(c: Conn, repo_id: str, kind: str, payload: dict[str, Any]) -> str:
    jid = uuid.uuid4().hex
    c.exec("insert into jobs (id, repo_id, kind, payload) values (?, ?, ?, ?)",
           [jid, repo_id, kind, payload])  # fmt: skip
    return jid


def claim(db: Database) -> dict[str, Any] | None:
    """The oldest queued job, marked running (one worker gets it)."""
    with db.tx() as c:
        lock = " for update skip locked" if c.kind == "postgres" else ""
        job = c.one(f"select * from jobs where status = 'queued' order by created_at, id limit 1"
                    f"{lock}")  # fmt: skip
        if job is None:
            return None
        c.exec("update jobs set status = 'running', attempts = attempts + 1, "
               "started_at = CURRENT_TIMESTAMP where id = ?", [job["id"]])  # fmt: skip
        return job


def _git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}: {p.stderr.strip()[:500]}")
    return p.stdout


def clone(url: str, dest: Path, sha: str | None) -> Path:
    """A full clone (the analysis reads the whole history) checked out at `sha`."""
    _git(dest.parent, "clone", "--quiet", "--no-tags", url, str(dest))
    if sha:
        try:
            _git(dest, "checkout", "--quiet", "--detach", sha)
        except RuntimeError:  # a pull request's head: fetch it by sha, then check it out
            _git(dest, "fetch", "--quiet", "origin", sha)
            _git(dest, "checkout", "--quiet", "--detach", sha)
    return dest


def prepare(c: Conn, root: Path, repo: dict[str, Any]) -> str:
    """Give the clone a `.udebt/`; returns where its map came from."""
    from ..store import Store

    store = Store(root)
    source = "repository"
    if not store.map_path.exists():
        m = c.one("select data from maps where repo_id = ?", [repo["id"]])
        if m is None:
            raise RuntimeError("no map: the repository commits none and nothing was pushed")
        store.init()
        store.save_map(m["data"])
        rows = c.q("select * from responsibility where repo_id = ?", [repo["id"]])
        dev_sets = {r["dev_key"]: r["units"] for r in rows}
        (store.dir / "responsibility.json").write_text(json.dumps({
            "version": 1, "developers": {d: {"units": {u: "hosted" for u in us}}
                                         for d, us in dev_sets.items()}, "pending": {},
        }), encoding="utf-8")  # fmt: skip
        source = "hosted"
    store.init()  # the gitignore for derived files; nothing else is overwritten
    have = {
        (str(r.get("dev", slug)).lower(), r.get("ts"), r.get("unit"), r.get("question"))
        for slug, recs in store.all_records().items()
        for r in recs
    }
    for e in c.q("select * from evidence where repo_id = ? and origin = 'hosted' order by ts",
                 [repo["id"]]):  # fmt: skip
        k = (e["dev_key"], e["ts"], e["unit_id"], e["question"])
        if k in have:
            continue
        rec = {"ts": e["ts"], "type": e["type"], "source": e["type"], "dev": e["dev_key"],
               "unit": e["unit_id"], "level": e["level"], "head": e["head"],
               "question": e["question"], "verdict": e["verdict"],
               "confidence": e["confidence"] or 1, "origin": "hosted"}  # fmt: skip
        if e["forced"]:
            rec["forced"] = True
        store.append_record(e["dev_key"], rec)
    return source


def analyze(root: Path) -> dict[str, Any]:
    """The export `udebt export` would write for this clone, with the sanitized map."""
    from .. import contract, replay
    from ..store import Store

    store = Store(root)
    h = replay.load(root, store)
    res = replay.ensure(h, None, save=False)
    if res.note:  # a stale committed series: replay it afresh, in memory
        res = replay.ensure(h, None, rebuild=True, save=False)
    data = replay.export_data(h, res)
    data["map"] = contract.sanitize_map(store.require_map())
    return data


def pr_risk(root: Path, base: str, head: str, author_email: str) -> list[dict[str, Any]]:
    """`udebt risk base..head` for `author_email`, as rows for the check run."""
    from .. import risk
    from .. import units as hier
    from ..mapping import commits_for, weights_for
    from ..quiz import compute_scores
    from ..responsibility import load as load_responsibility
    from ..store import Store

    store = Store(root)
    m = store.require_map()
    units = m["units"]
    cfg = store.config
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    resp = load_responsibility(store)
    scores = {s.unit_id: s for s in compute_scores(root, store, units, author_email,
              commits=commits, weights=weights, resp=resp)}  # fmt: skip
    changes = risk.commits_in_range(root, f"{base}..{head}", commits)
    mine = resp.ids(cfg.primary(author_email)) & set(hier.index(units))
    rows = risk.assess(units, scores, changes, weights, cfg.known_threshold,
                       responsible=mine or None)  # fmt: skip
    return [{"unit": r.unit_id, "name": r.name, "level": r.level, "mass": r.mass,
             "understanding": round(r.understanding, 3), "in_set": r.in_set,
             "files": r.files} for r in rows]  # fmt: skip


def author_email(c: Conn, root: Path, repo: dict[str, Any], login: str | None, head: str) -> str:
    """The developer key of a pull request's author: their membership's key, else the head
    commit's author email."""
    if login:
        row = c.one(
            "select m.dev_key from memberships m join users u on u.id = m.user_id "
            "where m.org_id = ? and lower(u.login) = lower(?) and m.dev_key is not null",
            [repo["org_id"], login],
        )
        if row:
            return row["dev_key"]
    return _git(root, "log", "-1", "--format=%ae", head).strip().lower()


CloneUrl = Callable[[dict[str, Any], dict[str, Any]], str]
Poster = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], Any]


def plain_url(repo: dict[str, Any], org: dict[str, Any]) -> str:
    return repo.get("clone_url") or f"https://github.com/{repo['github']}.git"


def run(
    db: Database,
    job: dict[str, Any],
    *,
    clone_url: CloneUrl = plain_url,
    post: Poster | None = None,
    work_dir: str | None = None,
    ack_label: str = "udebt-acknowledged",
) -> dict[str, Any]:
    """Run one claimed job; its row ends `done` with a result or `failed` with the error."""
    from . import checks, github
    from .importer import import_export

    tmp = Path(tempfile.mkdtemp(prefix="udebt-job-", dir=work_dir or None))
    try:
        with db.tx() as c:
            repo = c.one("select * from repos where id = ?", [job["repo_id"]])
            org = c.one("select * from orgs where id = ?", [repo["org_id"]])
        p = job["payload"] or {}
        sha = p.get("head_sha") if job["kind"] == "check" else p.get("sha")
        root = clone(clone_url(repo, org), tmp / "repo", sha)
        with db.tx() as c:
            source = prepare(c, root, repo)
        if job["kind"] == "analyze":
            data = analyze(root)
            with db.tx() as c:
                counts = import_export(c, repo, data, SERVICE)
            result = {"map": source, "head": data["head"], **counts}
        else:
            head = p["head_sha"]
            base = p.get("base_sha") or _git(root, "rev-parse", f"{head}~1").strip()
            with db.tx() as c:
                who = author_email(c, root, repo, p.get("author"), head)
                ack = github.ack_for(c, repo["id"], int(p["pr"]), head)
            rows = pr_risk(root, base, head, who)
            body = checks.payload(head, p.get("author") or who, rows,
                                  soft_block=bool(repo["soft_block"]), ack=ack,
                                  ack_label=ack_label)  # fmt: skip
            body["udebt"] = {"base_sha": base, "author": who}
            with db.tx() as c:
                c.upsert("checks", {"repo_id": repo["id"], "pr": int(p["pr"]), "head_sha": head,
                         "conclusion": body["conclusion"], "level": checks.overall(rows),
                         "payload": body}, ("repo_id", "pr", "head_sha"))  # fmt: skip
            posted = post(repo, org, body) if post else None
            result = {"conclusion": body["conclusion"], "level": checks.overall(rows),
                      "posted": posted is not None}  # fmt: skip
        with db.tx() as c:
            c.exec("update jobs set status = 'done', result = ?, finished_at = CURRENT_TIMESTAMP "
                   "where id = ?", [result, job["id"]])  # fmt: skip
        return result
    except Exception as e:
        with db.tx() as c:
            c.exec("update jobs set status = 'failed', error = ?, finished_at = CURRENT_TIMESTAMP "
                   "where id = ?", [f"{type(e).__name__}: {e}"[:2000], job["id"]])  # fmt: skip
        traceback.print_exc()
        return {"error": f"{type(e).__name__}: {e}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # metadata only: the code never stays


def work(db: Database, **kw: Any) -> int:
    """Run queued jobs until none is left; returns how many ran."""
    n = 0
    while (job := claim(db)) is not None:
        run(db, job, **kw)
        n += 1
    return n
