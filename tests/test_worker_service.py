"""Phase 4b: the Render worker service and its tool loop, against a stub of Supabase (Auth and
PostgREST, stdlib) and a fake Messages API that answers like udebt's FakeLLM after one read.
Nothing reaches the network: the GitHub URL is mapped to a local fixture repository."""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from udebt.hosted import github, messages_llm, supabase_rest
from udebt.hosted.messages_llm import (
    AnthropicTransport,
    GatewayTransport,
    MessagesLLM,
    RepoTools,
    gateway_body,
)
from udebt.hosted.worker_service import Config, Service, harden_git, make_handler
from udebt.llm import FakeLLM, LLMError


def jwt(exp: float) -> str:
    def enc(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'HS256'})}.{enc({'sub': 'u-1', 'exp': exp})}.sig"


GOOD = jwt(time.time() + 3600)
KEY = "sk-ant-test-key-never-logged"


class StubSupabase:
    """Auth and PostgREST in memory; every request's headers are kept to check the token."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {
            "repositories": [],
            "analyses": [],
            "my_repositories": [],
            "jobs": [],
        }
        self.requests: list[tuple[str, str, dict]] = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, status, data=None):
                raw = json.dumps(data).encode() if data is not None else b""
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _auth(self):
                stub.requests.append((self.command, self.path, dict(self.headers)))
                tok = self.headers.get("authorization", "")
                if self.headers.get("apikey") != "anon" or tok not in (f"Bearer {GOOD}",):
                    self._reply(401, {"message": "invalid JWT"})
                    return False
                return True

            def _query(self):
                table, _, qs = self.path.removeprefix("/rest/v1/").partition("?")
                q = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
                return table, {k: urllib.request.unquote(v) for k, v in q.items()}

            def do_GET(self):
                if not self._auth():
                    return
                if self.path == "/auth/v1/user":
                    return self._reply(200, {"id": "u-1", "email": "a@example.com"})
                table, q = self._query()
                rows = stub.tables[table]
                for k, v in q.items():
                    if v.startswith("eq."):
                        rows = [r for r in rows if str(r.get(k)) == v[3:]]
                self._reply(200, rows)

            def do_POST(self):
                if not self._auth():
                    return
                table, _ = self._query()
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                rows = body if isinstance(body, list) else [body]
                out = []
                for r in rows:
                    if table == "repositories" and any(
                        x["url"] == r["url"] for x in stub.tables[table]
                    ):
                        return self._reply(409, {"message": "duplicate key"})
                    if table == "my_repositories" and any(
                        x["repo_id"] == r["repo_id"] for x in stub.tables[table]
                    ):
                        continue
                    row = {"id": len(stub.tables[table]) + 1, "user_id": "u-1", **r}
                    stub.tables[table].append(row)
                    out.append(row)
                self._reply(201, out)

            def do_PATCH(self):
                if not self._auth():
                    return
                table, q = self._query()
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                rows = [r for r in stub.tables[table] if f"eq.{r['id']}" == q.get("id")]
                for r in rows:
                    r.update(body)
                self._reply(200, rows)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def stub():
    s = StubSupabase()
    yield s
    s.close()


def fake_transport(seen: list[dict]):
    """A Messages API that reads one file on the first turn of each task, then submits what
    FakeLLM would answer for the prompt."""
    fake = FakeLLM()

    def call(body: dict) -> dict:
        seen.append(json.loads(json.dumps(body)))
        msgs = body["messages"]
        prompt = msgs[0]["content"]
        names = {t["name"] for t in body["tools"]}
        if len(msgs) == 1 and "Glob" in names:
            return {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Glob",
                        "input": {"pattern": "**/*.py"},
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10},
            }
        schema = next(t for t in body["tools"] if t["name"] == "submit")["input_schema"]
        answer = fake.structured(prompt, schema, cwd=Path("."))
        return {
            "content": [{"type": "tool_use", "id": "t2", "name": "submit", "input": answer}],
            "stop_reason": "tool_use",
            "usage": {"output_tokens": 5},
        }

    return call


def service(stub, tmp_path, fixture_repo, seen, logs, **cfg) -> Service:
    config = Config(
        supabase_url=stub.url, anon_key="anon", work_dir=tmp_path / "work", walkthroughs=1, **cfg
    )
    config.work_dir.mkdir(exist_ok=True)
    return Service(
        config,
        github_info=lambda r: github.Info(
            private=False, size_kb=10, license="MIT", default_branch="main"
        ),
        clone_url=lambda u: str(fixture_repo),
        transport_for=lambda key, token: (fake_transport(seen), 4096),
        log=logs.append,
    )


def wait(svc: Service) -> None:
    for t in svc.threads:
        t.join(timeout=60)


def test_a_job_analyses_writes_with_the_person_token_and_deletes_the_clone(
    stub, tmp_path, fixture_repo, monkeypatch
):
    monkeypatch.setenv("UDEBT_LLM", "fake")
    seen, logs = [], []
    svc = service(stub, tmp_path, fixture_repo, seen, logs)
    out = svc.submit(GOOD, KEY, {"url": "https://github.com/acme/proj", "model": "claude-test-1"})
    assert out["status"] == "running" and out["job"] == 1
    wait(svc)
    job = stub.tables["jobs"][0]
    assert job["status"] == "done", job
    assert job["calls"] >= 4 and "units" in job["message"]
    (a,) = stub.tables["analyses"]
    assert a["repo_id"] == 1 and len(a["sha"]) == 40 and a["contract"] == 1
    assert {u["id"] for u in a["specs"]["units"]} >= {"storage", "scoring"}
    assert a["manifest"]["code_excerpts"] is False and a["manifest"]["public"] is True
    assert a["manifest"]["url"] == "https://github.com/acme/proj"
    assert "export" not in a, "a public analysis leaves out the per-developer export"
    assert all(
        s.get("lines") is None for w in a["specs"]["walkthroughs"].values() for s in w["steps"]
    )
    assert stub.tables["repositories"][0]["license"] == "MIT"
    assert stub.tables["my_repositories"] == [{"id": 1, "user_id": "u-1", "repo_id": 1}]
    assert all(
        h.get("Authorization", h.get("authorization")) == f"Bearer {GOOD}"
        for _, _, h in stub.requests
    )
    writes = [(m, p, h) for m, p, h in stub.requests if m in ("POST", "PATCH")]
    assert all(h.get("Content-Profile") == "app_udebt" for _, _, h in writes)
    assert list((tmp_path / "work").iterdir()) == [], "the clone and the output are gone"
    assert KEY not in json.dumps(stub.tables) and KEY not in "\n".join(logs)
    assert any(
        b["messages"][-1]["role"] == "user" and isinstance(b["messages"][-1]["content"], list)
        for b in seen
    ), "the Glob result went back"
    # the same commit again: no clone, the repository just joins the list
    seen.clear()
    svc.submit(
        GOOD,
        KEY,
        {"url": "https://github.com/acme/proj", "model": "claude-test-1", "rev": a["sha"]},
    )
    wait(svc)
    assert stub.tables["jobs"][1]["message"] == "Already analysed: added to your list."
    assert seen == [] and len(stub.tables["analyses"]) == 1


def test_requests_are_checked_before_anything_runs(stub, tmp_path, fixture_repo):
    seen, logs = [], []
    svc = service(stub, tmp_path, fixture_repo, seen, logs)
    url = "https://github.com/acme/proj"

    def code(token, key, body):
        try:
            svc.submit(token, key, body)
        except Exception as e:  # noqa: BLE001
            return getattr(e, "status", None), str(e)
        return 202, ""

    assert code(GOOD, KEY, {"url": "https://gitlab.com/a/b", "model": "claude-x"})[0] == 400
    assert code(GOOD, KEY, {"url": "file:///etc", "model": "claude-x"})[0] == 400
    assert code(GOOD, KEY, {"url": url, "model": "claude-x", "rev": "a b"})[0] == 400
    assert code(GOOD, KEY, {"url": url})[0] == 400, "a key needs a model"
    assert code(GOOD, None, {"url": url})[1].startswith("Analysing a new repository needs")
    assert code(None, KEY, {"url": url, "model": "claude-x"})[0] == 401
    assert code("not-a-real-token", KEY, {"url": url, "model": "claude-x"})[0] == 401
    soon = jwt(time.time() + 60)
    assert "expires soon" in code(soon, KEY, {"url": url, "model": "claude-x"})[1]
    assert stub.tables["jobs"] == []
    svc.current = type("J", (), {"status": "running"})()
    assert code(GOOD, KEY, {"url": url, "model": "claude-x"})[0] == 429


def test_private_and_oversized_repositories_fail_the_job(stub, tmp_path, fixture_repo):
    seen, logs = [], []
    svc = service(stub, tmp_path, fixture_repo, seen, logs)
    svc.github_info = lambda r: github.Info(
        private=True, size_kb=1, license=None, default_branch=None
    )
    svc.submit(GOOD, KEY, {"url": "https://github.com/acme/proj", "model": "claude-x"})
    wait(svc)
    assert stub.tables["jobs"][0]["status"] == "failed"
    assert "public" in stub.tables["jobs"][0]["message"]
    svc.github_info = lambda r: github.Info(
        private=False, size_kb=900_000, license=None, default_branch=None
    )
    svc.submit(GOOD, KEY, {"url": "https://github.com/acme/proj", "model": "claude-x"})
    wait(svc)
    assert "over the 200 MB limit" in stub.tables["jobs"][1]["message"]
    assert seen == [] and not svc.busy()


def test_a_model_failure_fails_the_job_and_frees_the_service(stub, tmp_path, fixture_repo):
    logs = []
    svc = service(stub, tmp_path, fixture_repo, [], logs)

    def broken(body):
        raise LLMError("the HintWise allowance (user-day) is used up until tomorrow")

    svc.transport_for = lambda key, token: (broken, 4096)
    svc.submit(GOOD, KEY, {"url": "https://github.com/acme/proj", "model": "claude-x"})
    wait(svc)
    job = stub.tables["jobs"][0]
    assert job["status"] == "failed" and "allowance" in job["message"]
    assert not svc.busy() and list((tmp_path / "work").iterdir()) == []
    assert svc.status(GOOD, job["id"])["status"] == "failed"
    with pytest.raises(Exception, match="No such job"):
        svc.status(GOOD, 99)


def test_the_http_routes_cors_and_health(stub, tmp_path, fixture_repo):
    svc = service(stub, tmp_path, fixture_repo, [], [])
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(svc))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def req(method, path, body=None, headers=None):
        r = urllib.request.Request(
            base + path,
            method=method,
            headers=headers or {},
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            with urllib.request.urlopen(r) as res:
                return res.status, dict(res.headers), json.loads(res.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), json.loads(e.read() or b"null")

    try:
        status, headers, data = req(
            "GET", "/healthz", headers={"Origin": "https://udebt.hintwise.org"}
        )
        assert status == 200 and data == {"ok": True, "busy": False, "version": 1}
        assert headers["access-control-allow-origin"] == "https://udebt.hintwise.org"
        status, headers, _ = req("OPTIONS", "/analyses", headers={"Origin": "https://evil.example"})
        assert status == 403 and "access-control-allow-origin" not in headers
        status, headers, _ = req(
            "OPTIONS", "/analyses", headers={"Origin": "https://udebt.hintwise.org"}
        )
        assert status == 204 and "x-anthropic-key" in headers["access-control-allow-headers"]
        assert req("POST", "/analyses", {"url": "x"})[0] == 400
        assert (
            req(
                "POST",
                "/analyses",
                {"url": "https://github.com/a/b", "model": "claude-x"},
                {"X-Anthropic-Key": KEY},
            )[0]
            == 401
        )
        assert req("POST", "/nope", {})[0] == 404
        assert req("GET", "/nope")[0] == 404
        assert req("GET", "/analyses/1")[0] == 401
        big = req("POST", "/analyses", {"url": "x" * 20_000})
        assert big[0] == 413
    finally:
        server.shutdown()


def test_config_from_env_and_git_hardening():
    c = Config.from_env(
        {
            "SUPABASE_ANON_KEY": "k",
            "UDEBT_MAX_MB": "150",
            "UDEBT_MAX_COMMITS": "x",
            "UDEBT_ALLOWED_ORIGINS": "https://a.hintwise.org, https://b",
            "UDEBT_ALLOW_GATEWAY": "1",
            "UDEBT_WORK_DIR": "/tmp/w",
        }
    )
    assert (c.max_mb, c.max_commits, c.allow_gateway) == (150, 6000, True)
    assert c.allowed_origins == ("https://a.hintwise.org", "https://b")
    assert c.supabase_url == "https://auth.hintwise.org" and c.work_dir == Path("/tmp/w")
    env = harden_git({})
    assert env["GIT_ALLOW_PROTOCOL"] == "https" and env["GIT_TERMINAL_PROMPT"] == "0"
    keys = {
        env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
        for i in range(int(env["GIT_CONFIG_COUNT"]))
    }
    assert keys["core.symlinks"] == "false" and keys["pack.threads"] == "1"


# -- the tool loop ---------------------------------------------------------------------------


def test_the_tools_stay_inside_the_checkout(fixture_repo, tmp_path):
    (fixture_repo / "outside").symlink_to(tmp_path)
    (tmp_path / "secret.txt").write_text("no")
    t = RepoTools(fixture_repo)
    assert "STATE" in t.read("udebt/store.py")
    assert t.read(str(fixture_repo / "udebt" / "store.py")).startswith("     1\t")
    for bad in (
        "../x",
        "/etc/passwd",
        ".git/config",
        ".udebt/map.json",
        "outside/secret.txt",
        "nope.py",
        "",
    ):
        assert t.run("Read", {"path": bad}).startswith("Error:"), bad
    assert t.glob("**/*.py").splitlines() == ["udebt/scoring.py", "udebt/store.py"]
    assert t.glob("*.md") == "README.md"
    assert t.glob("") == "Give a pattern." and t.glob("x/*.go") == "No tracked path matches."
    assert t.grep("def DEBT") == "udebt/scoring.py:1: def debt(i, u):"
    assert t.grep("STATE", glob="*.md") == "No match." and t.grep("") == "Give a string to find."
    assert t.run("Bash", {}) == "Error: unknown tool Bash"
    many = fixture_repo / "big.py"
    many.write_text("\n".join(f"x = {i}" for i in range(1000)))
    out = t.read("big.py", 10)
    assert out.splitlines()[0] == "    10\tx = 9" and "more lines" in out
    (fixture_repo / "bin.dat").write_bytes(b"\0\1\2")
    assert t.read("bin.dat") == "bin.dat is a binary file."


def _msg(*blocks, stop="tool_use"):
    return {"content": list(blocks), "stop_reason": stop}


def test_the_loop_reads_then_submits_and_resends_the_conversation(fixture_repo):
    seen = []
    replies = iter(
        [
            _msg(
                {"type": "tool_use", "id": "a", "name": "Read", "input": {"path": "udebt/store.py"}}
            ),
            _msg({"type": "tool_use", "id": "b", "name": "submit", "input": {"units": []}}),
        ]
    )

    def t(body):
        seen.append(json.loads(json.dumps(body)))
        return next(replies)

    llm = MessagesLLM(t, model="claude-test", max_tokens=1000)
    assert llm.structured("TASK: map ...", {"type": "object"}, cwd=fixture_repo) == {"units": []}
    assert llm.calls == 2 and seen[0]["model"] == "claude-test" and seen[0]["max_tokens"] == 1000
    assert [x["name"] for x in seen[0]["tools"]] == ["Read", "Glob", "Grep", "submit"]
    result = seen[1]["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "a"
    assert "STATE" in result["content"]
    assert "Read" in seen[1]["system"]


def test_the_loop_accepts_json_text_nudges_and_gives_up(fixture_repo):
    llm = MessagesLLM(lambda b: _msg({"type": "text", "text": 'Here: {"ok": 1}'}, stop="end_turn"))
    assert llm.structured("p", {}, cwd=fixture_repo, read_tools=False) == {"ok": 1}
    llm = MessagesLLM(
        lambda b: _msg({"type": "text", "text": "thinking"}, stop="end_turn"), max_turns=3
    )
    with pytest.raises(LLMError, match="no answer after 3 turns"):
        llm.structured("p", {}, cwd=fixture_repo)
    last = []

    def reads_forever(body):
        last.append([t["name"] for t in body["tools"]])
        return _msg({"type": "tool_use", "id": "x", "name": "Glob", "input": {"pattern": "*"}})

    with pytest.raises(LLMError):
        MessagesLLM(reads_forever, max_turns=3).structured("p", {}, cwd=fixture_repo)
    assert last[-1] == ["submit"], "the last turn offers only submit"
    cut = MessagesLLM(
        lambda b: _msg(
            {"type": "tool_use", "id": "s", "name": "submit", "input": {"u": 1}}, stop="max_tokens"
        )
    )
    with pytest.raises(LLMError, match="max_tokens"):
        cut.structured("p", {}, cwd=fixture_repo)
    with pytest.raises(LLMError, match="tutor"):
        MessagesLLM(lambda b: {}).chat("hi")
    assert (
        MessagesLLM(lambda b: _msg({"type": "text", "text": "hi"})).text("p", cwd=fixture_repo)
        == "hi"
    )


def test_old_tool_results_are_cut_to_fit_the_body(fixture_repo):
    big = "x" * 60_000
    (fixture_repo / "big.txt").write_text(
        "\n".join(big[i : i + 100] for i in range(0, 60_000, 100))
    )
    n = iter(range(100))

    def t(body):
        if next(n) < 4:
            return _msg(
                {"type": "tool_use", "id": f"r{n}", "name": "Read", "input": {"path": "big.txt"}}
            )
        return _msg({"type": "tool_use", "id": "s", "name": "submit", "input": {"ok": True}})

    llm = MessagesLLM(t, max_body=90_000, max_turns=8)
    assert llm.structured("p", {}, cwd=fixture_repo) == {"ok": True}
    with pytest.raises(LLMError, match="over the"):
        MessagesLLM(t, max_body=10).structured("p" * 100, {}, cwd=fixture_repo)


def test_transports_send_only_what_each_service_takes(monkeypatch):
    sent = []

    class Res:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self.data).encode()

    def urlopen(req, timeout=None):
        sent.append((req.full_url, dict(req.headers), json.loads(req.data)))
        return Res({"content": []})

    monkeypatch.setattr(messages_llm.urllib.request, "urlopen", urlopen)
    body = {
        "model": "m",
        "max_tokens": 50_000,
        "system": "s",
        "messages": [],
        "tools": [],
        "tool_choice": {"type": "any"},
        "stream": True,
    }
    GatewayTransport("https://auth.example/functions/v1/llm-gateway", "tok")(body)
    url, headers, data = sent[-1]
    assert url.endswith("/llm-gateway") and headers["Authorization"] == "Bearer tok"
    assert data == {
        "app": "udebt",
        "model": "m",
        "max_tokens": 4096,
        "system": "s",
        "messages": [],
        "tools": [],
    }
    AnthropicTransport("sk-1")(body)
    url, headers, data = sent[-1]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["X-api-key"] == "sk-1" and headers["Anthropic-version"] == "2023-06-01"
    assert "stream" not in data and "tool_choice" not in data and data["max_tokens"] == 50_000
    assert "sk-1" not in repr(AnthropicTransport("sk-1")) and "tok" not in repr(
        GatewayTransport("u", "tok")
    )
    assert gateway_body({"messages": []})["max_tokens"] == 4096

    def refuse(req, timeout=None):
        body = json.dumps({"error": "cap", "scope": "app-day", "resets_at": "tomorrow"}).encode()
        raise urllib.error.HTTPError(req.full_url, 429, "cap", {}, __import__("io").BytesIO(body))

    monkeypatch.setattr(messages_llm.urllib.request, "urlopen", refuse)
    with pytest.raises(LLMError, match="app-day"):
        GatewayTransport("https://g", "t")({"messages": []})

    def down(req, timeout=None):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(messages_llm.urllib.request, "urlopen", down)
    with pytest.raises(LLMError, match="could not be reached"):
        AnthropicTransport("k")({"messages": []})


def test_supabase_helpers_and_github_checks(stub, fixture_repo):
    sb = supabase_rest.Supabase(stub.url, "anon", GOOD)
    assert sb.user()["id"] == "u-1" and "Supabase(" in repr(sb) and GOOD not in repr(sb)
    rid = sb.ensure_repository("https://github.com/a/b", "b", None)
    assert sb.ensure_repository("https://github.com/a/b", "b", None) == rid
    sb.add_mine(rid)
    sb.add_mine(rid)
    assert len(stub.tables["my_repositories"]) == 1
    job = sb.start_job("https://github.com/a/b", None)
    sb.finish_job(job, "done", "ok")
    sb.finish_job(None, "done", "ignored")
    assert stub.tables["jobs"][0]["status"] == "done"
    with pytest.raises(supabase_rest.SupabaseError) as e:
        supabase_rest.Supabase(stub.url, "anon", "bad").user()
    assert e.value.status == 401
    assert supabase_rest.token_seconds_left(jwt(1000), now=400) == 600
    assert supabase_rest.token_seconds_left("garbage") is None
    assert github.parse("https://github.com/a/b.git").url == "https://github.com/a/b"
    for bad in ("https://github.com/a", "https://github.com/a/..", "git@github.com:a/b"):
        assert github.parse(bad) is None
    sha = "a" * 40
    assert github.resolve("unused", sha) == sha
    head = github.resolve(str(fixture_repo), None)
    assert head and len(head) == 40
    assert github.resolve(str(fixture_repo), "main") == head
    assert github.resolve(str(fixture_repo / "nope"), None) is None
