"""The analysis service on Render (Phase 4b, HOSTING.md): "analyse this public GitHub repository
at this commit" for a signed-in HintWise person, with their own Anthropic key.

    python -m udebt.hosted.worker_service --port $PORT

Stdlib `http.server` (ThreadingHTTPServer) is enough: three routes, JSON only, one analysis at a
time (a 512 MB free instance), TLS and the public name (udebt-api.hintwise.org) at Render's
proxy. No web framework, so the package keeps no runtime dependency.

    GET  /healthz           {"ok": true, "busy": bool}: Render's health check
    POST /analyses          {"url", "rev"?, "model"?} with `Authorization: Bearer <session
                            token>` and `X-Anthropic-Key: <the person's key>` -> 202 {"job",
                            "status"}; 429 while another analysis runs
    GET  /analyses/<job>    the job's status, for the person who started it

A job, in a thread: GitHub's API is asked whether the repository is public and how large
(best effort); `git ls-remote` names the commit; if the app already has an analysis of that
commit (analyses of public repositories are shared) the repository just joins the person's
list. Otherwise `udebt worker run` analyses it in a temporary clone within the limits
(`UDEBT_MAX_COMMITS`, `UDEBT_MAX_MB`, the clone timeout), the model steps through the tool loop
(messages_llm.MessagesLLM over api.anthropic.com with the person's key, or the HintWise gateway
with their token when `UDEBT_ALLOW_GATEWAY=1`), no code in the specs (`excerpts=False`); the
analysis row, the repository and the person's list entry are written to Supabase with the
person's token, progress to their own app_udebt.jobs row, and the clone and output are deleted.
The key lives in memory for the job only and is never logged or stored.
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import loadsql, worker
from ..llm import LLMError
from . import github
from .messages_llm import AnthropicTransport, GatewayTransport, MessagesLLM
from .supabase_rest import Supabase, SupabaseError, token_seconds_left

VERSION = 1
MODEL = re.compile(r"^claude-[a-z0-9][a-z0-9.-]{0,60}$")
MAX_REQUEST = 16_000
# Git without prompts, over https only, symlinks checked out as plain files, and the pack
# machinery kept small for a 512 MB instance (GIT_CONFIG_COUNT applies them to every git call).
GIT_HARDENING = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ALLOW_PROTOCOL": "https",
    "GIT_CONFIG_NOSYSTEM": "1",
}
GIT_CONFIG = {
    "core.symlinks": "false",
    "pack.threads": "1",
    "pack.windowMemory": "32m",
    "pack.deltaCacheSize": "16m",
    "core.deltaBaseCacheLimit": "16m",
    "core.packedGitLimit": "128m",
    "core.packedGitWindowSize": "16m",
    "credential.helper": "",
}


def harden_git(env: dict[str, str] | None = None) -> dict[str, str]:
    """Sets the git environment for every clone this process makes (and returns it)."""
    env = os.environ if env is None else env
    env.update(GIT_HARDENING)
    env["GIT_CONFIG_COUNT"] = str(len(GIT_CONFIG))
    for i, (k, v) in enumerate(GIT_CONFIG.items()):
        env[f"GIT_CONFIG_KEY_{i}"] = k
        env[f"GIT_CONFIG_VALUE_{i}"] = v
    return env


def _int(env: dict[str, str], key: str, default: int) -> int:
    try:
        return int(env.get(key, default))
    except ValueError:
        return default


@dataclass
class Config:
    supabase_url: str
    anon_key: str
    allowed_origins: tuple[str, ...] = ("https://udebt.hintwise.org",)
    max_commits: int = 6000
    max_mb: int = 200
    clone_timeout: int = 300
    walkthroughs: int = 3
    allow_gateway: bool = False
    byok_max_tokens: int = 16_000
    gateway_max_tokens: int = 4096
    model_turns: int = 8
    min_token_seconds: int = 900
    work_dir: Path | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        e = dict(os.environ if env is None else env)
        origins = tuple(
            o.strip() for o in e.get("UDEBT_ALLOWED_ORIGINS", "").split(",") if o.strip()
        )
        return cls(
            supabase_url=e.get("SUPABASE_URL", "https://auth.hintwise.org"),
            anon_key=e.get("SUPABASE_ANON_KEY", ""),
            allowed_origins=origins or cls.allowed_origins,
            max_commits=_int(e, "UDEBT_MAX_COMMITS", cls.max_commits),
            max_mb=_int(e, "UDEBT_MAX_MB", cls.max_mb),
            clone_timeout=_int(e, "UDEBT_CLONE_TIMEOUT", cls.clone_timeout),
            walkthroughs=_int(e, "UDEBT_WALKTHROUGHS", cls.walkthroughs),
            allow_gateway=e.get("UDEBT_ALLOW_GATEWAY", "0") in ("1", "true", "yes"),
            byok_max_tokens=_int(e, "UDEBT_BYOK_MAX_TOKENS", cls.byok_max_tokens),
            gateway_max_tokens=_int(e, "UDEBT_GATEWAY_MAX_TOKENS", cls.gateway_max_tokens),
            model_turns=_int(e, "UDEBT_MODEL_TURNS", cls.model_turns),
            min_token_seconds=_int(e, "UDEBT_MIN_TOKEN_SECONDS", cls.min_token_seconds),
            work_dir=Path(e["UDEBT_WORK_DIR"]) if e.get("UDEBT_WORK_DIR") else None,
        )


@dataclass
class Job:
    id: int
    user_id: str
    url: str
    rev: str | None
    status: str = "running"
    message: str = ""
    sha: str | None = None
    db_id: int | None = None
    calls: int = 0
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def view(self) -> dict[str, Any]:
        return {
            "job": self.db_id if self.db_id is not None else self.id,
            "status": self.status,
            "message": self.message,
            "url": self.url,
            "sha": self.sha,
            "calls": self.calls,
            "seconds": round((self.finished or time.time()) - self.started, 1),
        }


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Service:
    """The service's state and its three operations; the HTTP handler only routes."""

    def __init__(
        self,
        config: Config,
        *,
        supabase: Callable[[str], Any] | None = None,
        github_info: Callable[[github.Repo], github.Info | None] = github.info,
        resolve: Callable[[str, str | None], str | None] = github.resolve,
        clone_url: Callable[[str], str] = lambda u: u,
        transport_for: Callable[[str | None, str], Any] | None = None,
        log: Callable[[str], None] | None = None,
    ):
        self.config = config
        self.supabase = supabase or (lambda t: Supabase(config.supabase_url, config.anon_key, t))
        self.github_info = github_info
        self.resolve = resolve
        self.clone_url = clone_url
        self.transport_for = transport_for or self._transport
        self.log = log or (lambda m: print(m, file=sys.stderr, flush=True))
        self.lock = threading.Lock()
        self.jobs: dict[int, Job] = {}
        self.current: Job | None = None
        self.threads: list[threading.Thread] = []
        self._ids = itertools.count(1)

    def busy(self) -> bool:
        return self.current is not None and self.current.status == "running"

    def health(self) -> dict[str, Any]:
        return {"ok": True, "busy": self.busy(), "version": VERSION}

    def _transport(self, api_key: str | None, token: str):
        if api_key:
            return AnthropicTransport(api_key), self.config.byok_max_tokens
        url = f"{self.config.supabase_url.rstrip('/')}/functions/v1/llm-gateway"
        return GatewayTransport(url, token, self.config.gateway_max_tokens), (
            self.config.gateway_max_tokens
        )

    def _who(self, token: str | None) -> tuple[Any, dict[str, Any]]:
        if not token:
            raise HttpError(401, "Sign in first.")
        left = token_seconds_left(token)
        if left is not None and left < self.config.min_token_seconds:
            raise HttpError(
                401, "Your sign-in expires soon: sign in again, then start the analysis."
            )
        sb = self.supabase(token)
        try:
            user = sb.user()
        except SupabaseError as e:
            raise HttpError(
                401 if e.status in (401, 403) else 502,
                "Sign in again." if e.status in (401, 403) else str(e),
            ) from None
        if not user or not user.get("id"):
            raise HttpError(401, "Sign in again.")
        return sb, user

    def submit(self, token: str | None, api_key: str | None, body: Any) -> dict[str, Any]:
        b = body if isinstance(body, dict) else {}
        repo = github.parse(str(b.get("url", "")))
        if repo is None:
            raise HttpError(
                400, "Only public GitHub repositories: https://github.com/<owner>/<name>."
            )
        rev = b.get("rev") or None
        if rev is not None and not (isinstance(rev, str) and github.REV.match(rev)):
            raise HttpError(400, "rev is a commit sha, a branch or a tag.")
        model = b.get("model")
        if api_key:
            if not (isinstance(model, str) and MODEL.match(model)):
                raise HttpError(400, "Say which model to use with your key (model).")
        elif not self.config.allow_gateway:
            raise HttpError(400, "Analysing a new repository needs your own Anthropic key.")
        else:
            model = model if isinstance(model, str) and MODEL.match(model) else None
        sb, user = self._who(token)
        with self.lock:
            if self.busy():
                raise HttpError(429, "Another analysis is running; try again in a few minutes.")
            job = Job(next(self._ids), str(user["id"]), repo.url, rev)
            try:
                job.db_id = sb.start_job(repo.url, rev)
            except SupabaseError as e:
                raise HttpError(502, f"The job could not be recorded: {e}") from None
            self.jobs[job.id] = job
            self.current = job
        transport, max_tokens = self.transport_for(api_key, token or "")
        llm = MessagesLLM(
            transport,
            model=model,
            max_tokens=max_tokens,
            max_turns=self.config.model_turns,
            log=self.log,
        )
        t = threading.Thread(target=self._run, args=(job, sb, repo, llm), daemon=True)
        self.threads.append(t)
        t.start()
        return job.view()

    def status(self, token: str | None, job_id: int) -> dict[str, Any]:
        _sb, user = self._who(token)
        job = next((j for j in self.jobs.values() if job_id in (j.id, j.db_id)), None)
        if job is None or job.user_id != str(user["id"]):
            raise HttpError(404, "No such job.")
        return job.view()

    def _finish(self, job: Job, sb: Any, status: str, message: str) -> None:
        job.status, job.message, job.finished = status, message, time.time()
        self.log(
            f"job {job.id} {status}: {job.url} {message} ({job.calls} calls, "
            f"{job.finished - job.started:.0f} s)"
        )
        try:
            sb.finish_job(job.db_id, status, message, sha=job.sha, calls=job.calls)
        except SupabaseError as e:
            self.log(f"job {job.id}: the status could not be written: {e}")

    def _run(self, job: Job, sb: Any, repo: github.Repo, llm: MessagesLLM) -> None:
        out = Path(tempfile.mkdtemp(prefix="udebt-out-", dir=self.config.work_dir))
        try:
            info = self.github_info(repo)
            if info is not None and info.private:
                return self._finish(job, sb, "failed", "Not a public repository (or not found).")
            if info is not None and info.size_kb > self.config.max_mb * 1024:
                return self._finish(
                    job,
                    sb,
                    "failed",
                    f"The repository is {info.size_kb // 1024} "
                    f"MB, over the {self.config.max_mb} MB limit.",
                )
            src = self.clone_url(repo.url)
            job.sha = self.resolve(src, job.rev)
            repo_id = sb.ensure_repository(repo.url, repo.name, info.license if info else None)
            shas = sb.analysed_shas(repo_id)
            if job.sha and job.sha in shas:
                sb.add_mine(repo_id)
                return self._finish(job, sb, "done", "Already analysed: added to your list.")
            limits = worker.Limits(
                self.config.max_commits, self.config.max_mb, self.config.clone_timeout
            )
            manifest = worker.run(
                src,
                out,
                job.sha or job.rev,
                limits=limits,
                model=llm,
                walkthroughs=self.config.walkthroughs,
                excerpts=False,
                since=shas,
                export=False,
                work_dir=self.config.work_dir,
                log=lambda m: self.log(f"job {job.id}: {m}"),
            )
            job.sha, job.calls = manifest["sha"], llm.calls
            if manifest["map_source"] == "none":
                return self._finish(job, sb, "failed", "No map could be written.")
            if job.sha not in sb.analysed_shas(repo_id):
                rows = loadsql.analysis_rows(
                    out, public=True, url=repo.url, license=info.license if info else None
                )
                a = rows["analysis"]
                sb.insert(
                    "analyses",
                    {
                        "repo_id": repo_id,
                        "sha": a["sha"],
                        "contract": a["contract"],
                        "manifest": a["manifest"],
                        "map": a["map"],
                        "specs": a["specs"],
                    },
                )
            sb.add_mine(repo_id)
            units = manifest["counts"].get("units", 0)
            self._finish(job, sb, "done", f"{units} units, {llm.calls} model calls.")
        except (worker.WorkerError, LLMError, SupabaseError, loadsql.LoadError) as e:
            job.calls = llm.calls
            self._finish(job, sb, "failed", str(e))
        except Exception as e:  # noqa: BLE001 - a job never takes the service down
            job.calls = llm.calls
            self.log("".join(traceback.format_exception_only(type(e), e)).strip())
            self._finish(job, sb, "failed", "Unexpected error in the analysis service.")
        finally:
            shutil.rmtree(out, ignore_errors=True)
            llm.transport = None  # type: ignore[assignment] - drop the key with the job
            with self.lock:
                if self.current is job:
                    self.current = None


def make_handler(service: Service) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "udebt-worker"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:  # the path only, never headers
            service.log(
                f"{self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}"
            )

        def _cors(self) -> dict[str, str]:
            origin = self.headers.get("origin")
            if origin and origin in service.config.allowed_origins:
                return {"access-control-allow-origin": origin, "vary": "Origin"}
            return {}

        def _send(self, status: int, data: Any) -> None:
            raw = json.dumps(data).encode("utf-8")
            self.send_response(status)
            for k, v in {
                "content-type": "application/json",
                "cache-control": "no-store",
                "x-content-type-options": "nosniff",
                **self._cors(),
            }.items():
                self.send_header(k, v)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _token(self) -> str | None:
            m = re.match(r"^Bearer\s+(\S{8,4096})$", self.headers.get("authorization", "").strip())
            return m.group(1) if m else None

        def do_OPTIONS(self) -> None:  # noqa: N802
            cors = self._cors()
            self.send_response(204 if cors else 403)
            for k, v in cors.items():
                self.send_header(k, v)
            if cors:
                self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
                self.send_header(
                    "access-control-allow-headers", "authorization, content-type, x-anthropic-key"
                )
                self.send_header("access-control-max-age", "600")
            self.send_header("content-length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            try:
                if path == "/healthz":
                    return self._send(200, service.health())
                m = re.match(r"^/analyses/(\d{1,12})$", path)
                if m:
                    return self._send(200, service.status(self._token(), int(m.group(1))))
                self._send(404, {"error": "Not found."})
            except HttpError as e:
                self._send(e.status, {"error": str(e)})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?")[0] != "/analyses":
                return self._send(404, {"error": "Not found."})
            try:
                n = int(self.headers.get("content-length") or 0)
                if n > MAX_REQUEST:
                    raise HttpError(413, "The request is too large.")
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    raise HttpError(400, "The body is not JSON.") from None
                key = (self.headers.get("x-anthropic-key") or "").strip() or None
                self._send(202, service.submit(self._token(), key, body))
            except HttpError as e:
                self._send(e.status, {"error": str(e)})

    return Handler


def serve(service: Service, host: str = "0.0.0.0", port: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(service))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="udebt-worker-service", description=__doc__.split("\n")[0])
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = p.parse_args(argv)
    config = Config.from_env()
    if not config.anon_key:
        print("SUPABASE_ANON_KEY is not set.", file=sys.stderr)
        return 2
    harden_git()
    server = serve(Service(config), args.host, args.port)
    print(
        f"udebt worker service on {args.host}:{args.port} (limits: {config.max_commits} commits, "
        f"{config.max_mb} MB)",
        file=sys.stderr,
        flush=True,
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
