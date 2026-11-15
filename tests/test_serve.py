"""`udebt serve` against an ephemeral port, with the FakeLLM: security, every endpoint, and
evidence recorded exactly as the CLI records it."""

import http.client
import json

import pytest

from udebt import cli, serve
from udebt.llm import FakeLLM
from udebt.store import Store


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setenv("UDEBT_LLM", "fake")


@pytest.fixture
def server(fixture_repo, capsys):
    root = fixture_repo
    cli.main(["--repo", str(root), "init"])
    cli.main(["--repo", str(root), "map"])
    capsys.readouterr()
    store = Store(root)
    llms = []

    def factory():
        llms.append(FakeLLM())
        return llms[-1]

    app = serve.App(root, store, "dev1@example.com", factory, token="tok-123")
    httpd, _ = serve.start_in_thread(app)
    FakeLLM.chats = []
    yield app, store, llms
    httpd.shutdown()
    httpd.server_close()


def call(app, method, path, body=None, *, token="tok-123", host=None, ctype="application/json"):
    c = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{app.port}"}
    if token is not None:
        headers["X-Udebt-Token"] = token
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = ctype
    c.request(method, path, body=data, headers=headers)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw), r
    except ValueError:
        return r.status, raw.decode(), r


def test_localhost_only_with_a_token(server):
    app, _, _ = server
    assert app.port and serve.url(app).startswith(f"http://127.0.0.1:{app.port}/?t=")
    assert call(app, "GET", "/api/state", token=None)[0] == 403
    assert call(app, "GET", "/api/state", token="nope")[0] == 403
    assert call(app, "GET", "/api/state", host="evil.example:80")[0] == 403, "DNS rebinding"
    assert call(app, "GET", "/api/state", host=f"localhost:{app.port}")[0] == 200
    assert call(app, "GET", "/", token=None)[0] == 403, "the page needs ?t="
    st, page, r = call(app, "GET", "/?t=tok-123", token=None)
    assert st == 200 and "/static/app.js" in page and "<script>" not in page
    csp = r.getheader("Content-Security-Policy")
    assert "script-src 'self'" in csp and r.getheader("Referrer-Policy") == "no-referrer"
    st, js, r = call(app, "GET", "/static/app.js", token=None)
    assert st == 200 and "X-Udebt-Token" in js and "http://" not in js and "https://" not in js
    assert call(app, "GET", "/static/../serve.py", token=None)[0] == 404
    assert call(app, "POST", "/api/tour", {"x": 1}, ctype="text/plain")[0] == 415
    assert call(app, "POST", "/api/tour", b"x" * (serve.MAX_BODY + 1))[0] == 413
    assert call(app, "GET", "/api/nope")[0] == 404


def test_dashboard_tab_fetches_its_data(server):
    app, _, _ = server
    st, page, _ = call(app, "GET", "/dashboard?t=tok-123", token=None)
    assert st == 200 and '<script type="application/json" id="udebt-data">null</script>' in page
    assert 'fetch("/api/dashboard"' in page
    st, data, _ = call(app, "GET", "/api/dashboard")
    assert st == 200 and data["format"] == "udebt-export" and data["snapshots"]
    assert not any("answer" in e for e in data["evidence"]), "no answer text"


def test_state_path_unit_and_excerpts(server):
    app, store, _ = server
    st, s, _ = call(app, "GET", "/api/state")
    assert st == 200 and s["repo"] == "proj" and s["basis"]["estimated"] == 2
    assert {u["id"] for u in s["units"]} == {"storage", "scoring"}
    st, p, _ = call(app, "GET", "/api/path")
    assert st == 200 and p["steps"][0]["command"].startswith("udebt dig ")
    st, u, _ = call(app, "GET", "/api/unit?id=storage")
    assert st == 200 and u["score"]["basis"] == "estimated" and u["orientation"]["purpose"]
    assert u["score"]["decay"] == "" and u["score"]["share_since"] is None, "no quiz: no fade"
    assert "<h1>" in u["decision_html"] and u["walkthrough"] is None
    assert call(app, "GET", "/api/unit?id=nope")[0] == 404
    st, x, _ = call(app, "GET", "/api/excerpt?file=udebt/scoring.py&start=1&end=99")
    assert st == 200 and x["lines"][0] == "def debt(i, u):" and x["end"] == 2
    (store.root / "secret.env").write_text("KEY=1\n")
    for bad in ("secret.env", ".udebt/config.json", "../proj/udebt/store.py", "/etc/passwd"):
        st, err, _ = call(app, "GET", f"/api/excerpt?file={bad}&start=1&end=2")
        assert st in (400, 404) and "error" in err, bad


def test_generation_posts_fill_the_map_caches(server):
    app, store, llms = server
    st, t, _ = call(app, "GET", "/api/tour")
    assert t["markdown"] is None
    st, t, _ = call(app, "POST", "/api/tour", {})
    assert st == 200 and t["markdown"].startswith("# Architecture tour") and "<h2>" in t["html"]
    assert store.require_map()["tour"]["sections"]
    assert call(app, "GET", "/api/tour")[1]["markdown"] == t["markdown"]
    st, w, _ = call(app, "POST", "/api/walkthrough", {"unit": "scoring"})
    assert st == 200 and w["written"] and w["walkthrough"]["steps"]
    st, u, _ = call(app, "GET", "/api/unit?id=scoring")
    assert u["walkthrough"]["steps"][0]["file"] == "udebt/scoring.py"
    calls = [p.split("\n", 1)[0] for llm in llms for p in llm.calls]
    assert calls == ["TASK: tour", "TASK: walkthrough"]


def test_quiz_answers_are_recorded_as_the_cli_records_them(server, monkeypatch, capsys):
    app, store, llms = server
    st, r, _ = call(app, "POST", "/api/questions", {"n": 1, "units": ["storage"]})
    (q,) = r["questions"]
    assert st == 200 and q["level"] == 1 and q["orientation"]["purpose"]
    assert "reference_answer" not in q, "the reference stays on the server"
    st, a, _ = call(app, "POST", "/api/answer", {"id": q["id"], "answer": "", "confidence": 2})
    assert st == 200 and a["verdict"] == "unknown" and a["revealed"]
    assert a["reference"] == "storage returns a default."
    assert call(app, "POST", "/api/answer", {"id": q["id"], "answer": "x"})[0] == 404
    served = store.load_records("dev1@example.com")[-1]
    # the same through the CLI
    monkeypatch.setattr("builtins.input", lambda _p="": "")
    cli.main(["--repo", str(store.root), "quiz", "--unit", "scoring"])
    capsys.readouterr()
    by_cli = store.load_records("dev1@example.com")[-1]
    assert set(served) == set(by_cli) and served["revealed"] and by_cli["revealed"]
    assert served["type"] == by_cli["type"] == "quiz" and served["source"] == "quiz"
    # the next quiz on storage asks a stored variant of the missed question: no call
    n_calls = sum(len(llm.calls) for llm in llms)
    st, r, _ = call(app, "POST", "/api/questions", {"n": 1, "units": ["storage"]})
    assert r["questions"][0]["question"].startswith("In other words, what does the storage")
    assert sum(len(llm.calls) for llm in llms) == n_calls
    again = {"id": r["questions"][0]["id"], "answer": "it returns a default"}
    st, a, _ = call(app, "POST", "/api/answer", again)
    assert a["verdict"] == "correct" and a["next_level"] == 2 and not a["revealed"]


def test_review_teachback_and_tutor(server):
    app, store, llms = server
    st, r, _ = call(app, "POST", "/api/questions", {"n": 1, "units": ["storage"]})
    call(app, "POST", "/api/answer", {"id": r["questions"][0]["id"], "answer": ""})
    st, rv, _ = call(app, "GET", "/api/review")
    assert rv["items"] == [] and rv["upcoming"] == 1, "due tomorrow"
    st, rv, _ = call(app, "GET", "/api/review?all=1")
    (item,) = rv["items"]
    st, a, _ = call(app, "POST", "/api/answer", {"id": item["id"], "answer": "a default"})
    rec = store.load_records("dev1@example.com")[-1]
    assert a["verdict"] == "correct" and rec["source"] == "review" and rec["review"]["reps"] == 1
    tb_body = {"unit": "scoring", "explanation": "It sends email.", "confidence": 3}
    st, tb, _ = call(app, "POST", "/api/teachback", tb_body)
    assert tb["verdict"] == "wrong" and tb["misconception"] and tb["revealed"]
    assert "reference_answer" not in tb and tb["label"].startswith("level 1 of 5")
    rec = store.load_records("dev1@example.com")[-1]
    assert rec["type"] == "teachback" and rec["level"] == 1, "no evidence there: level 1"
    assert call(app, "POST", "/api/teachback", {"unit": "scoring", "explanation": " "})[0] == 400
    # the tutor: a session id on the first turn, resume after; "check me" brings the check
    st, v, _ = call(app, "POST", "/api/tutor", {"unit": "scoring", "message": "What is this?"})
    assert st == 200 and v["turns"][-1]["role"] == "tutor" and v["check"] is None
    sid = v["session"]
    st, v, _ = call(app, "POST", "/api/tutor", {"session": sid, "message": "Check me."})
    assert v["check"]["question"] == "What is the unit for?"
    assert "REFERENCE" not in json.dumps(v), "the reference is never shown"
    first, second = FakeLLM.chats
    assert first["session_id"] == second["session_id"] == sid
    assert first["resume"] is False and "Socratic" in first["system"]
    assert second["resume"] is True and second["system"] is None
    assert call(app, "GET", f"/api/tutor?session={sid}")[1]["session"] == sid
    answer = {"session": sid, "answer": "it returns a default", "confidence": 2}
    st, a, _ = call(app, "POST", "/api/tutor/answer", answer)
    rec = store.load_records("dev1@example.com")[-1]
    assert a["verdict"] == "correct" and rec["source"] == "tutor" and rec["level"] == 1
    assert a["session"]["check"] is None
    assert call(app, "POST", "/api/tutor/answer", {"session": sid, "answer": "x"})[0] == 400


def test_tutor_flags_for_the_claude_cli(monkeypatch, tmp_path):
    import subprocess

    from udebt import llm as llm_mod

    seen = []

    class Done:
        returncode = 0
        stdout = json.dumps({"result": "Hello."})
        stderr = ""

    monkeypatch.setattr(llm_mod.shutil, "which", lambda _: "/bin/claude")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: seen.append(cmd) or Done())
    c = llm_mod.ClaudeCLI()
    assert c.chat("hi", cwd=tmp_path, session_id="u-1", system="SYS") == "Hello."
    c.chat("again", cwd=tmp_path, session_id="u-1", resume=True)
    first, later = seen
    tools = ["--tools", "Read", "Glob", "Grep", "--allowedTools", "Read", "Glob", "Grep"]
    assert first[first.index("--session-id") + 1] == "u-1" and "--resume" not in first
    assert first[first.index("--append-system-prompt") + 1] == "SYS"
    assert later[later.index("--resume") + 1] == "u-1" and "--session-id" not in later
    for cmd in seen:
        assert "--no-session-persistence" not in cmd and cmd[-10:-2] == tools
        assert cmd[-2:] == ["--disallowedTools", "Read(./.udebt/**)"], "never udebt's own files"


def test_a_stale_series_is_replayed_in_memory_and_tutor_markdown_is_escaped(server, capsys):
    app, store, _ = server
    cli.main(["--repo", str(store.root), "snapshot"])
    p = store.dir / "snapshots.json"
    data = json.loads(p.read_text())
    data["fingerprint"]["rules"] = 1  # made under older rules
    p.write_text(json.dumps(data))
    before = p.read_text()
    st, d, _ = call(app, "GET", "/api/dashboard")
    assert st == 200 and d["snapshots"] and p.read_text() == before, "replayed, not written"
    assert "replays it in memory" in capsys.readouterr().err
    from udebt import tutor

    s = tutor.Session("storage", 1, "dev1@example.com")
    s.turns = [{"role": "tutor", "text": "**Sign-in** (`main.py:1-2`) <script>x</script>"}]
    (t,) = s.view()["turns"]
    assert "<strong>Sign-in</strong>" in t["html"] and "<script>" not in t["html"]
