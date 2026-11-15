"""The hosted API (Starlette): auth, organisations and repositories, the import, the reads
the SPA draws, evidence, acknowledgements, jobs and the GitHub webhook.

Every handler is a plain function run in a worker thread (the database calls are blocking);
`endpoint` parses the bearer token and the JSON body, opens the transaction (as the caller,
under row-level security, for reads in Postgres) and maps errors to status codes: 401 no or bad
token, 403 a role that may not, 404 unknown, 409 a conflict, 413 too large, 422 a payload that
breaks the contract.

Routes (all JSON; `{org}/{repo}` are the organisation's slug and the repository's name):

    GET  /healthz                         GET  /auth/config
    POST /auth/dev            (dev only)  GET  /auth/github/login, /auth/github/callback
    GET  /api/me                          POST /api/tokens
    POST /api/orgs                        POST /api/orgs/{org}/members   (owner)
    POST /api/orgs/{org}/repos (owner)
    GET  /api/r/{org}/{repo}/state | dashboard | unit?id= | units | timeline | contributors
                              | risk | pull | settings | jobs
    POST /api/r/{org}/{repo}/import | answer | ack | jobs
    PATCH /api/r/{org}/{repo}/settings    (owner)
    POST /webhooks/github                 (signature checked)
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .. import contract, ladder
from . import auth, github, jobs, reads
from .config import Settings
from .db import Conn, Database
from .importer import evidence_row, import_export
from .permissions import ROLES, Forbidden, Viewer

MAX_BODY = 64 * 1024 * 1024  # an export of a large team's history (huggingface_hub: ~40 MB)
SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")


class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Ctx:
    db: Database
    settings: Settings
    http: Any
    params: dict[str, str]
    query: dict[str, str]
    body: Any
    auth: str | None
    raw: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    def user(self, c: Conn) -> dict[str, Any]:
        return auth.user_for(c, self.settings.jwt_secret, self.auth)

    def repo(self, c: Conn, user: dict[str, Any]) -> tuple[dict, dict, Viewer]:
        """(org, repo, the caller as a Viewer there); 404 when either is unknown or the caller
        is no member (the same answer, so a stranger learns nothing)."""
        org = c.one("select * from orgs where slug = ?", [self.params["org"]])
        repo = org and c.one("select * from repos where org_id = ? and name = ?",
                             [org["id"], self.params["repo"]])  # fmt: skip
        m = org and c.one("select * from memberships where org_id = ? and user_id = ?",
                          [org["id"], user["id"]])  # fmt: skip
        if not (org and repo and m):
            raise HTTPError(404, "no such repository (or you are not a member)")
        v = Viewer(user["id"], m["role"], m.get("dev_key"),
                   bool(repo["members_see_individuals"]), user.get("login"))  # fmt: skip
        return org, repo, v


def _json(status: int, data: Any) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def endpoint(fn: Callable[[Ctx], Any], *, body: bool = False):
    async def handler(request: Request) -> Response:
        app = request.app.state
        raw = b""
        data: Any = None
        if body:
            n = int(request.headers.get("content-length") or 0)
            if n > MAX_BODY:
                return _json(413, {"error": "body too large"})
            raw = await request.body()
            if len(raw) > MAX_BODY:
                return _json(413, {"error": "body too large"})
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                return _json(400, {"error": "the body is not JSON"})
        ctx = Ctx(app.db, app.settings, app.http, dict(request.path_params),
                  dict(request.query_params), data, request.headers.get("authorization"), raw,
                  {k.lower(): v for k, v in request.headers.items()})  # fmt: skip
        try:
            out = await run_in_threadpool(fn, ctx)
        except auth.Unauthorized as e:
            return _json(401, {"error": str(e)})
        except Forbidden as e:
            return _json(403, {"error": str(e)})
        except contract.ContractError as e:
            return _json(422, {"error": str(e), "errors": e.errors[:20]})
        except HTTPError as e:
            return _json(e.status, {"error": e.message})
        if isinstance(out, Response):
            return out
        if isinstance(out, tuple):
            return _json(out[0], out[1])
        return _json(200, out)

    return handler


# -- auth ------------------------------------------------------------------------------


def health(ctx: Ctx):
    return {"ok": True, "db": ctx.db.kind, "contract": contract.CONTRACT}


def auth_config(ctx: Ctx):
    return {"dev": ctx.settings.dev_auth, "github": bool(ctx.settings.github_client_id)}


def auth_dev(ctx: Ctx):
    if not ctx.settings.dev_auth:
        raise HTTPError(404, "dev login is off (UDEBT_DEV_AUTH)")
    b = ctx.body or {}
    login = str(b.get("login") or "").strip()
    if not SLUG.match(login.lower()):
        raise HTTPError(400, "a login: letters, digits, '.', '_' or '-'")
    with ctx.db.tx() as c:
        user = auth.upsert_user(c, login, str(b.get("email") or "").strip().lower() or None)
    return {"token": auth.issue(ctx.settings.jwt_secret, user, ctx.settings.session_hours),
            "user": {"id": user["id"], "login": user["login"], "email": user["email"]}}  # fmt: skip


def github_login(ctx: Ctx):
    s = ctx.settings
    if not s.github_client_id:
        raise HTTPError(404, "GitHub sign-in is not configured (GITHUB_CLIENT_ID)")
    back = ctx.query.get("return") or s.app_url
    if s.app_origins and not any(back.startswith(o) for o in s.app_origins + [s.app_url]):
        raise HTTPError(400, "return URL is not one of the app's origins")
    url = auth.login_url(s.github_client_id, s.jwt_secret, f"{s.api_url}/auth/github/callback",
                         back)  # fmt: skip
    return RedirectResponse(url, status_code=302)


def github_callback(ctx: Ctx):
    s = ctx.settings
    gh, back = auth.github_user(ctx.query.get("code", ""), ctx.query.get("state", ""),
                                client_id=s.github_client_id, client_secret=s.github_client_secret,
                                secret=s.jwt_secret, http=ctx.http)  # fmt: skip
    with ctx.db.tx() as c:
        user = auth.upsert_user(c, gh["login"], (gh.get("verified_email") or "").lower() or None,
                                gh.get("name"), int(gh["id"]))  # fmt: skip
    token = auth.issue(s.jwt_secret, user, s.session_hours)
    return RedirectResponse(f"{back or s.app_url}#token={token}", status_code=302)


def me(ctx: Ctx):
    with ctx.db.tx() as c:
        user = ctx.user(c)
    with ctx.db.tx(user["id"]) as c:
        orgs = c.q(
            "select o.id, o.slug, o.name, m.role, m.dev_key from memberships m "
            "join orgs o on o.id = m.org_id where m.user_id = ? order by o.slug",
            [user["id"]],
        )
        for o in orgs:
            o["repos"] = c.q(
                "select name, head, imported_at, members_see_individuals, soft_block "
                "from repos where org_id = ? order by name",
                [o.pop("id")],
            )
    return {"user": {k: user.get(k) for k in ("id", "login", "email", "name")}, "orgs": orgs}


def new_token(ctx: Ctx):
    with ctx.db.tx() as c:
        user = ctx.user(c)
        token = auth.new_api_token(c, user["id"], str((ctx.body or {}).get("name") or "cli"))
    return {"token": token, "note": "shown once; udebt stores only its hash"}


# -- organisations and repositories ----------------------------------------------------


def new_org(ctx: Ctx):
    b = ctx.body or {}
    slug = str(b.get("slug") or "").lower()
    if not SLUG.match(slug):
        raise HTTPError(400, "a slug: lowercase letters, digits, '.', '_' or '-'")
    with ctx.db.tx() as c:
        user = ctx.user(c)
        if c.one("select 1 as x from orgs where slug = ?", [slug]):
            raise HTTPError(409, f"{slug} exists")
        oid = uuid.uuid4().hex
        c.exec("insert into orgs (id, slug, name) values (?, ?, ?)", [oid, slug,
               str(b.get("name") or slug)])  # fmt: skip
        c.exec("insert into memberships (org_id, user_id, role, dev_key) values (?, ?, 'owner', ?)",
               [oid, user["id"], (b.get("dev_key") or user.get("email") or None)])  # fmt: skip
    return 201, {"slug": slug, "role": "owner"}


def _org_as_owner(c: Conn, ctx: Ctx, user: dict[str, Any]) -> dict[str, Any]:
    org = c.one("select * from orgs where slug = ?", [ctx.params["org"]])
    m = org and c.one("select role from memberships where org_id = ? and user_id = ?",
                      [org["id"], user["id"]])  # fmt: skip
    if not m:
        raise HTTPError(404, "no such organisation (or you are not a member)")
    if m["role"] != "owner":
        raise Forbidden("only an owner can change the organisation")
    return org


def set_member(ctx: Ctx):
    """Add a member or change their role and developer key (owner). The user is made by
    login when they have not signed in yet, so an owner can assign roles ahead."""
    b = ctx.body or {}
    role = str(b.get("role") or "member")
    if role not in ROLES:
        raise HTTPError(400, f"role is one of {', '.join(ROLES)}")
    login = str(b.get("login") or "").strip()
    if not login:
        raise HTTPError(400, "a login")
    with ctx.db.tx() as c:
        user = ctx.user(c)
        org = _org_as_owner(c, ctx, user)
        target = c.one("select * from users where lower(login) = lower(?)", [login])
        if target is None:
            target = auth.upsert_user(c, login, (b.get("email") or "").lower() or None)
        if target["id"] == user["id"] and role != "owner":
            owners = c.q("select user_id from memberships where org_id = ? and role = 'owner'",
                         [org["id"]])  # fmt: skip
            if len(owners) == 1:
                raise HTTPError(409, "the last owner cannot step down")
        dev = (b.get("dev_key") or "").lower() or None
        c.upsert("memberships", {"org_id": org["id"], "user_id": target["id"], "role": role,
                 "dev_key": dev}, ("org_id", "user_id"))  # fmt: skip
    return {"login": target["login"], "role": role, "dev_key": dev}


def new_repo(ctx: Ctx):
    b = ctx.body or {}
    name = str(b.get("name") or "")
    if not SLUG.match(name.lower()):
        raise HTTPError(400, "a repository name: letters, digits, '.', '_' or '-'")
    with ctx.db.tx() as c:
        user = ctx.user(c)
        org = _org_as_owner(c, ctx, user)
        if c.one("select 1 as x from repos where org_id = ? and name = ?", [org["id"], name]):
            raise HTTPError(409, f"{name} exists")
        c.exec("insert into repos (id, org_id, name, github, clone_url, default_branch) "
               "values (?, ?, ?, ?, ?, ?)", [uuid.uuid4().hex, org["id"], name, b.get("github"),
               b.get("clone_url"), b.get("default_branch") or "main"])  # fmt: skip
    return 201, {"org": org["slug"], "repo": name}


# -- repository reads ------------------------------------------------------------------


def _read(ctx: Ctx, fn: Callable[[Conn, dict, dict, Viewer], Any]):
    with ctx.db.tx() as c:
        user = ctx.user(c)
    with ctx.db.tx(user["id"]) as c:  # as the caller: row-level security in Postgres
        org, repo, v = ctx.repo(c, user)
        return fn(c, org, repo, v)


def r_state(ctx: Ctx):
    return _read(ctx, lambda c, org, repo, v: reads.state(c, repo, org, v))


def r_dashboard(ctx: Ctx):
    return _read(ctx, lambda c, org, repo, v: reads.dashboard(c, repo, v))


def r_unit(ctx: Ctx):
    out = _read(ctx, lambda c, org, repo, v: reads.unit(c, repo, v, ctx.query.get("id", "")))
    if out is None:
        raise HTTPError(404, f"unknown unit: {ctx.query.get('id')}")
    return out


def r_units(ctx: Ctx):
    return _read(ctx, lambda c, org, repo, v: {"units": reads.dashboard(c, repo, v)["units"]})


def r_contributors(ctx: Ctx):
    return _read(ctx, lambda c, o, repo, v: {"contributors":
                 reads.dashboard(c, repo, v)["contributors"]})  # fmt: skip


def r_risk(ctx: Ctx):
    from ..replay import interesting

    def fn(c, org, repo, v):
        d = reads.dashboard(c, repo, v)
        return {"risk_events": interesting(d["risk_events"])}

    return _read(ctx, fn)


def r_timeline(ctx: Ctx):
    dev = (ctx.query.get("dev") or "").lower() or None
    unit = ctx.query.get("unit") or None

    def fn(c, org, repo, v):
        d = reads.dashboard(c, repo, v)
        rows = []
        for p in d["snapshots"]:
            for k, r in p["devs"].items():
                if dev and k != dev:
                    continue
                row = {"t": p["t"], "sha": p["sha"], "dev": k, "index": r.get("index")}
                if unit:
                    row["understanding"] = (r.get("und") or {}).get(unit)
                rows.append(row)
        return {"devs": d["devs"], "rows": rows}

    return _read(ctx, fn)


def r_pull(ctx: Ctx):
    """What `udebt pull` takes back: the visible evidence (the hosted-recorded included) and
    the series, scoped like the dashboard."""

    def fn(c, org, repo, v):
        d = reads.dashboard(c, repo, v)
        return {"head": d["head"], "generated_at": d["generated_at"], "devs": d["devs"],
                "evidence": d["evidence"], "snapshots": d["snapshots"],
                "settings": d["settings"], "role": v.role}  # fmt: skip

    return _read(ctx, fn)


def r_settings(ctx: Ctx):
    def fn(c, org, repo, v):
        return {"members_see_individuals": bool(repo["members_see_individuals"]),
                "soft_block": bool(repo["soft_block"]), "role": v.role,
                "dev_key": v.dev_key, "github": repo.get("github"),
                "default_branch": repo["default_branch"], "head": repo.get("head"),
                "imported_at": repo.get("imported_at")}  # fmt: skip

    return _read(ctx, fn)


def r_jobs(ctx: Ctx):
    def fn(c, org, repo, v):
        v.require("see_jobs")
        return {"jobs": c.q("select id, kind, status, attempts, error, result, created_at, "
                            "finished_at from jobs where repo_id = ? order by created_at desc "
                            "limit 50", [repo["id"]])}  # fmt: skip

    return _read(ctx, fn)


# -- repository writes -----------------------------------------------------------------


def _write(ctx: Ctx, fn: Callable[[Conn, dict, dict, Viewer, dict], Any]):
    with ctx.db.tx() as c:
        user = ctx.user(c)
        org, repo, v = ctx.repo(c, user)
        return fn(c, org, repo, v, user)


def w_settings(ctx: Ctx):
    b = ctx.body or {}

    def fn(c, org, repo, v, user):
        if "members_see_individuals" in b:
            v.require("set_visibility")
            c.exec("update repos set members_see_individuals = ? where id = ?",
                   [bool(b["members_see_individuals"]), repo["id"]])  # fmt: skip
        if "soft_block" in b:
            v.require("set_soft_block")
            c.exec("update repos set soft_block = ? where id = ?", [bool(b["soft_block"]),
                   repo["id"]])  # fmt: skip
        for k in ("github", "clone_url", "default_branch"):
            if k in b:
                v.require("set_visibility")  # owner only
                c.exec(f"update repos set {k} = ? where id = ?", [b[k], repo["id"]])
        row = c.one("select members_see_individuals, soft_block, github, clone_url, "
                    "default_branch from repos where id = ?", [repo["id"]])  # fmt: skip
        return row

    return _write(ctx, fn)


def w_import(ctx: Ctx):
    if not isinstance(ctx.body, dict):
        raise HTTPError(400, "the body is an export (udebt export --format json)")
    return _write(ctx, lambda c, org, repo, v, user: {"imported": import_export(c, repo, ctx.body,
                  v), "role": v.role})  # fmt: skip


def w_answer(ctx: Ctx):
    """Record one graded answer as evidence (metadata only: no answer text). A member records
    for themselves; an owner or lead may name another developer (`dev`)."""
    from ..store import now_iso

    b = dict(ctx.body or {})
    if b.get("verdict") not in contract.VERDICTS:
        raise HTTPError(400, f"verdict is one of {', '.join(contract.VERDICTS)}")
    if not b.get("unit"):
        raise HTTPError(400, "a unit")

    def fn(c, org, repo, v, user):
        v.require("record_evidence")
        dev = (b.get("dev") or v.dev_key or "").lower()
        if not dev:
            raise HTTPError(409, "you have no developer key in this organisation yet")
        if dev != (v.dev_key or "").lower() and not v.can("import_all"):
            raise Forbidden("a member records evidence for themselves only")
        lvl = b.get("level")
        e = {"ts": str(b.get("ts") or now_iso()), "dev": dev, "unit": str(b["unit"]),
             "level": ladder.check(int(lvl)) if lvl is not None else None,
             "verdict": b["verdict"], "confidence": int(b.get("confidence") or 2),
             "type": str(b.get("type") or "quiz"), "question": b.get("question"),
             "head": b.get("head") or repo.get("head"),
             "forced": bool(b.get("forced"))}  # fmt: skip
        contract.check("evidence", e)
        row = evidence_row(repo["id"], e, origin="hosted")
        added = c.insert_new("evidence", row, ("repo_id", "key"))
        return 201 if added else 200, {"recorded": added, "key": row["key"], "evidence": e}

    try:
        return _write(ctx, fn)
    except (SystemExit, ValueError) as e:
        raise HTTPError(400, str(e)) from e


def w_ack(ctx: Ctx):
    b = ctx.body or {}

    def fn(c, org, repo, v, user):
        v.require("acknowledge")
        pr = int(b.get("pr") or 0)
        if pr <= 0:
            raise HTTPError(400, "a pull request number")
        last = c.one("select head_sha from checks where repo_id = ? and pr = ? "
                     "order by created_at desc", [repo["id"], pr])  # fmt: skip
        head = b.get("head_sha") or (last or {}).get("head_sha") or "*"
        github.acknowledge(c, repo, pr, head, user["login"], "api")
        job = jobs.enqueue(c, repo["id"], "check", {"pr": pr, "head_sha": head}) if last else None
        return {"acknowledged": pr, "head_sha": head, "queued": job}

    return _write(ctx, fn)


def w_jobs(ctx: Ctx):
    def fn(c, org, repo, v, user):
        v.require("import_all")
        kind = (ctx.body or {}).get("kind", "analyze")
        if kind != "analyze":
            raise HTTPError(400, "only analyze jobs are made by hand")
        return 202, {"queued": jobs.enqueue(c, repo["id"], "analyze", {})}

    return _write(ctx, fn)


def webhook(ctx: Ctx):
    s = ctx.settings
    if not github.verify(s.github_webhook_secret, ctx.raw, ctx.headers.get("x-hub-signature-256")):
        raise HTTPError(401, "bad or missing webhook signature")
    event = ctx.headers.get("x-github-event", "")
    with ctx.db.tx() as c:
        return github.handle(c, event, ctx.body or {}, jobs.enqueue, s.ack_label)


# -- the app ---------------------------------------------------------------------------


def create_app(settings: Settings | None = None, db: Database | None = None, http=None):
    settings = settings or Settings.from_env()
    settings.check()
    db = db or Database(settings.database_url, rls=settings.pg_rls)
    db.migrate()
    r = "/api/r/{org}/{repo}"
    routes = [
        Route("/healthz", endpoint(health)),
        Route("/auth/config", endpoint(auth_config)),
        Route("/auth/dev", endpoint(auth_dev, body=True), methods=["POST"]),
        Route("/auth/github/login", endpoint(github_login)),
        Route("/auth/github/callback", endpoint(github_callback)),
        Route("/api/me", endpoint(me)),
        Route("/api/tokens", endpoint(new_token, body=True), methods=["POST"]),
        Route("/api/orgs", endpoint(new_org, body=True), methods=["POST"]),
        Route("/api/orgs/{org}/members", endpoint(set_member, body=True), methods=["POST"]),
        Route("/api/orgs/{org}/repos", endpoint(new_repo, body=True), methods=["POST"]),
        Route(f"{r}/state", endpoint(r_state)),
        Route(f"{r}/dashboard", endpoint(r_dashboard)),
        Route(f"{r}/unit", endpoint(r_unit)),
        Route(f"{r}/units", endpoint(r_units)),
        Route(f"{r}/timeline", endpoint(r_timeline)),
        Route(f"{r}/contributors", endpoint(r_contributors)),
        Route(f"{r}/risk", endpoint(r_risk)),
        Route(f"{r}/pull", endpoint(r_pull)),
        Route(f"{r}/settings", endpoint(r_settings)),
        Route(f"{r}/settings", endpoint(w_settings, body=True), methods=["PATCH"]),
        Route(f"{r}/import", endpoint(w_import, body=True), methods=["POST"]),
        Route(f"{r}/answer", endpoint(w_answer, body=True), methods=["POST"]),
        Route(f"{r}/ack", endpoint(w_ack, body=True), methods=["POST"]),
        Route(f"{r}/jobs", endpoint(r_jobs)),
        Route(f"{r}/jobs", endpoint(w_jobs, body=True), methods=["POST"]),
        Route("/webhooks/github", endpoint(webhook, body=True), methods=["POST"]),
    ]
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=settings.app_origins,
            allow_methods=["GET", "POST", "PATCH"],
            allow_headers=["Authorization", "Content-Type"],
            max_age=600,
        )
    ]
    app = Starlette(routes=routes, middleware=middleware)
    app.state.db = db
    app.state.settings = settings
    app.state.http = http or auth.urllib_http
    return app
