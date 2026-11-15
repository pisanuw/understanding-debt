"""Phase 3.4: partial answers revealed, teach-back at the current level (test_teaching),
rotating dig checks, persisted tutor sessions, batch grading in the browser, hub files out of
the unit graph, and change digests."""

import json

import pytest
from conftest import AI_ENV, git

from udebt import cli, dig, quiz, serve, tutor, unitgraph
from udebt.llm import FakeLLM
from udebt.provenance import Commit
from udebt.store import Store


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setenv("UDEBT_LLM", "fake")


def run(root, capsys, *args):
    rc = cli.main(["--repo", str(root), *args])
    return rc, capsys.readouterr().out


def _mapped(root, capsys):
    run(root, capsys, "init")
    run(root, capsys, "map")
    return Store(root)


@pytest.fixture
def spy(monkeypatch):
    from udebt import llm as llm_mod

    seen = []
    real = llm_mod.FakeLLM.structured

    def wrapped(self, prompt, schema, **kw):
        seen.append(prompt.split("\n", 1)[0])
        return real(self, prompt, schema, **kw)

    monkeypatch.setattr(llm_mod.FakeLLM, "structured", wrapped)
    return seen


# -- 1. partial answers are revealed at levels 1-2 ----------------------------------------


def test_a_partial_answer_is_revealed_at_levels_1_and_2_only(fixture_repo, monkeypatch, capsys):
    assert quiz.reveals({"level": 1}, "partial") and quiz.reveals({"level": 2}, "partial")
    assert not quiz.reveals({"level": 3}, "partial") and not quiz.reveals({"level": 1}, "correct")
    root = fixture_repo
    store = _mapped(root, capsys)
    answers = iter(["half of it, I think", "", "2"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    rc, out = run(root, capsys, "quiz", "--unit", "storage")
    assert "[PARTIAL ] storage (level 1)" in out
    assert out.index("[PARTIAL") < out.index("The answer: storage returns a default.")
    rec = store.load_records("dev1@example.com")[-1]
    assert rec["revealed"] is True and rec["verdict"] == "partial" and rec["variants"]
    answers = iter(["half of it, I think", "", "2"])
    rc, out = run(root, capsys, "quiz", "--unit", "storage", "--level", "3")
    assert "[PARTIAL ]" in out and "The answer:" not in out


# -- 3. dig rotates its check question ------------------------------------------------------


def test_pick_check_rotates_through_the_pool():
    from udebt import orientation as orient

    unit = {"id": "u", "name": "U", "paths": ["a.py"], "decision": "d"}
    b = orient.basis(unit)
    unit["questions"] = {
        "1": [
            {"question": "Q1?", "variants": ["Q1 again?"], "reference_answer": "r1", "basis": b},
            {"question": "Q2?", "variants": [], "reference_answer": "r2", "basis": b},
            {"question": "Old?", "variants": [], "reference_answer": "r", "basis": "stale"},
        ]
    }
    w = {"checks": {"1": {"question": "Check?", "reference_answer": "rc"}}, "steps": []}

    def rec(q, ts, **kw):
        return {"unit": "u", "level": 1, "question": q, "ts": ts, "verdict": "wrong", **kw}

    assert dig.pick_check(w, unit, 1, [])["question"] == "Check?", "the walkthrough's own first"
    recs = [rec("Check?", "2026-01-01")]
    assert dig.pick_check(w, unit, 1, recs)["question"] == "Q1?"
    recs.append(rec("Q1?", "2026-01-02"))
    assert dig.pick_check(w, unit, 1, recs)["question"] == "Q2?", "a stale pooled one never"
    recs.append(rec("Q2?", "2026-01-03"))
    got = dig.pick_check(w, unit, 1, recs)
    assert got["question"] == "Check?" and got["repeat"], "asked longest ago"
    recs.append(rec("Q2?", "2026-01-04", revealed=True, variants=["Q2 in other words?"]))
    assert dig.pick_check(w, unit, 1, recs)["question"] == "Q2 in other words?"
    assert dig.pick_check(w, unit, 3, recs) is None


def test_dig_asks_a_different_check_each_time(fixture_repo, monkeypatch, capsys):
    root = fixture_repo
    store = _mapped(root, capsys)
    monkeypatch.setattr("builtins.input", lambda _p="": "")
    asked = []
    outs = []
    for _ in range(4):
        outs.append(run(root, capsys, "dig", "storage")[1])
        asked.append(store.load_records("dev1@example.com")[-1]["question"])
    assert len(set(asked[:3])) == 3, asked
    assert asked[0] == "What is the storage unit for?", "the walkthrough's own check first"
    assert "writing a new one" in outs[1], "the pool held one question: the model wrote more"
    assert asked[1].startswith("In other words"), "its first phrasing not yet asked"
    # the fake model only ever writes the same three phrasings: the fourth has to repeat
    assert "writing a new one" in outs[3] and asked[3] == asked[0]
    pool = store.require_map()["units"]
    storage = next(u for u in pool if u["id"] == "storage")
    assert {"1", "2", "3", "4", "5"} <= set(storage["questions"]), "checks joined the pool"


# -- 4. tutor sessions persist ----------------------------------------------------------


def _server(root, token="tok"):
    store = Store(root)
    app = serve.App(root, store, "dev1@example.com", FakeLLM, token=token)
    httpd, _ = serve.start_in_thread(app)
    return app, httpd


def _call(app, method, path, body=None):
    import http.client

    c = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    headers = {"Host": f"127.0.0.1:{app.port}", "X-Udebt-Token": app.token}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=headers)
    r = c.getresponse()
    out = json.loads(r.read() or b"{}")
    c.close()
    return r.status, out


def test_tutor_sessions_survive_a_restart_with_resume_or_start_over(fixture_repo, capsys):
    root = fixture_repo
    store = _mapped(root, capsys)
    FakeLLM.chats = []
    app, httpd = _server(root)
    st, v = _call(app, "POST", "/api/tutor", {"unit": "scoring", "message": "What is it?"})
    sid = v["session"]
    st, v = _call(app, "POST", "/api/tutor", {"session": sid, "message": "Check me."})
    assert v["check"]["question"]
    saved = tutor.path_for(store.dir, "dev1@example.com", "scoring")
    data = json.loads(saved.read_text())
    assert data["session"] == sid and len(data["turns"]) == 4 and data["check"]["reference_answer"]
    assert "tutor/" in (store.dir / ".gitignore").read_text()
    httpd.shutdown()
    httpd.server_close()
    # a new server: the page reattaches by id, and a new start on the unit offers the choice
    app, httpd = _server(root)
    st, v = _call(app, "GET", f"/api/tutor?session={sid}")
    assert st == 200 and len(v["turns"]) == 4 and v["check"]["question"]
    st, r = _call(app, "GET", "/api/tutor?unit=scoring")
    assert r["saved"]["session"] == sid and r["saved"]["asked"] == 2
    assert _call(app, "GET", "/api/tutor?unit=storage")[1] == {"saved": None}
    st, r = _call(app, "POST", "/api/tutor", {"unit": "scoring", "message": "Hello again"})
    assert st == 409 and r["saved"]["session"] == sid
    # resume: the claude CLI session is resumed, and the check answer still works
    st, v = _call(app, "POST", "/api/tutor", {"session": sid, "message": "More please"})
    last = FakeLLM.chats[-1]
    assert st == 200 and last["resume"] is True and last["session_id"] == sid
    st, a = _call(app, "POST", "/api/tutor/answer", {"session": sid, "answer": "a default"})
    assert a["verdict"] == "correct" and json.loads(saved.read_text())["check"] is None
    # start over: a new session replaces the saved one
    st, v = _call(app, "POST", "/api/tutor", {"unit": "scoring", "message": "Hi", "restart": True})
    assert st == 200 and v["session"] != sid and FakeLLM.chats[-1]["resume"] is False
    assert json.loads(saved.read_text())["session"] == v["session"]
    httpd.shutdown()
    httpd.server_close()


def test_a_session_back_after_a_break_starts_a_new_sitting():
    s = tutor.Session("u", 1, "d@x")
    d = s.as_dict()
    d["created"] = d["started"] = d["active"] = 1_000.0
    back = tutor.Session.from_dict(d)
    assert back.elapsed < 5 and back.created == 1_000.0


# -- 5. batch grading in the browser ------------------------------------------------------


def test_a_sitting_is_graded_in_one_call_at_its_end(fixture_repo, capsys, spy):
    root = fixture_repo
    store = _mapped(root, capsys)
    app, httpd = _server(root)
    try:
        st, r = _call(app, "POST", "/api/questions", {"n": 2})
        q1, q2 = r["questions"]
        before = spy.count("TASK: grade")
        st, d = _call(app, "POST", "/api/dontknow", {"id": q1["id"]})
        assert d["revealed"] and d["reference"].endswith("returns a default.")
        assert spy.count("TASK: grade") == before, "I don't know needs no call"
        assert store.load_records("dev1@example.com") == [], "nothing recorded before the end"
        answers = [
            {"id": q1["id"], "answer": "it returns a default"},  # ignored: shown the answer
            {"id": q2["id"], "answer": "half of it", "confidence": 3},
        ]
        st, out = _call(app, "POST", "/api/answers", {"answers": answers})
        assert st == 200 and spy.count("TASK: grade") == before + 1, "one call for the sitting"
        a1, a2 = out["results"]
        assert a1["verdict"] == "unknown" and a1["revealed"]
        assert a2["verdict"] == "partial" and a2["revealed"] and a2["reference"], "after grading"
        recs = store.load_records("dev1@example.com")
        assert len(recs) == 2 and recs[0]["ts"] == recs[1]["ts"], "one sitting"
        assert _call(app, "POST", "/api/answers", {"answers": answers})[0] == 404, "answered"
        # all blank: no call at all
        st, r = _call(app, "POST", "/api/questions", {"n": 1, "units": ["storage"]})
        n = spy.count("TASK: grade")
        st, out = _call(app, "POST", "/api/answers", {"answers": [{"id": r["questions"][0]["id"]}]})
        assert out["results"][0]["verdict"] == "unknown" and spy.count("TASK: grade") == n
        assert _call(app, "POST", "/api/answers", {"answers": []})[0] == 400
    finally:
        httpd.shutdown()
        httpd.server_close()


# -- 7. hub files connect nothing ----------------------------------------------------------


def test_files_listed_by_more_than_two_units_connect_nothing(tmp_path):
    root = tmp_path
    (root / "a.py").write_text("import hub\nimport b\n")
    (root / "b.py").write_text("x = 1\n")
    (root / "c.py").write_text("import hub\n")
    (root / "hub.py").write_text("import a\nimport b\nimport c\n")
    units = [
        {"id": "A", "paths": ["a.py", "hub.py"]},
        {"id": "B", "paths": ["b.py", "hub.py"]},
        {"id": "C", "paths": ["c.py", "hub.py"]},
    ]
    files = ["a.py", "b.py", "c.py", "hub.py"]

    def commit(sha, fs):
        return Commit(sha, "2026-01-01T00:00:00+00:00", "D", "d@x", "s", "", files=fs,
                      file_lines={f: 1 for f in fs})  # fmt: skip

    from types import SimpleNamespace

    commits = [commit("1", ["hub.py", "a.py"]), commit("2", ["a.py", "b.py"])]
    w = {"1": SimpleNamespace(weight=1.0), "2": SimpleNamespace(weight=1.0)}
    g = unitgraph.build(root, units, commits, w, files=files)
    assert g.hubs == ["hub.py"]
    assert g.depends_on("A") == ["B"] and g.depends_on("C") == [], "only a.py -> b.py connects"
    assert set(g.together) == {("A", "B")}, "the hub's commit connects nothing"
    two = unitgraph.build(root, units, commits, w, files=files, max_owners=3)
    assert two.hubs == [] and ("C", "A") in two.uses and ("A", "C") in two.together


# -- 8. change digests ------------------------------------------------------------------


@pytest.fixture
def moved_repo(fixture_repo):
    """fixture_repo plus an AI commit and a human commit on store, after a tag `before`."""
    root = fixture_repo
    git(root, "tag", "before")
    (root / "udebt" / "store.py").write_text("import json\nSTATE = {'v': 9}\n")
    git(root, "commit", "-q", "-am", "store: persist as json", env=AI_ENV)
    (root / "udebt" / "store.py").write_text("import json\nSTATE = {'v': 10}\n")
    git(root, "commit", "-q", "-am", "store: bump state")
    return root


def test_digest_briefs_what_landed_since_a_start_in_one_cached_call(moved_repo, capsys, spy):
    root = moved_repo
    store = _mapped(root, capsys)
    spy.clear()
    rc, out = run(root, capsys, "digest", "--since", "before", "--unit", "storage")
    assert rc == 0 and spy == ["TASK: digest"]
    assert "since before" in out and "2 commit(s)" in out and "ai/claude-code" in out
    assert "store: persist as json" in out and "In brief: The storage unit gained" in out
    assert "Decision extended" in out and "udebt quiz --unit storage" in out
    files = list((store.dir / "digests").glob("storage--*.json"))
    assert len(files) == 1 and "digests/" in (store.dir / ".gitignore").read_text()
    spy.clear()
    rc, out = run(root, capsys, "digest", "--since", "before", "--unit", "storage")
    assert spy == [] and "In brief:" in out, "cached per (unit, start, end)"
    rc, out = run(root, capsys, "digest", "--since", "before", "--format", "json")
    items = json.loads(out)
    by = {it["unit"]: it for it in items}
    assert by["storage"]["digest"]["decision"] == "extended" and by["storage"]["cached"]
    assert by["scoring"]["commits"] == [], "nothing landed on scoring since then"
    assert by["storage"]["commits"][0]["klass"] == "ai"


def test_digest_starts_at_the_last_evidence_or_the_last_days(moved_repo, capsys):
    from udebt import digest
    from udebt.mapping import commits_for

    root = moved_repo
    store = _mapped(root, capsys)
    commits = commits_for(root, store)
    head = commits[-1].sha
    r = digest.range_for(root, [], commits, days=30)
    assert r.how == "days" and r.end == head and r.start is None, "all within 30 days"
    mid = commits[-2].sha
    r = digest.range_for(root, [{"ts": "2026-01-01", "head": mid}], commits)
    assert r.how == "evidence" and r.start == mid
    r = digest.range_for(root, [{"ts": "2026-01-01", "head": "deadbeef"}], commits)
    assert r.how == "days" and "no longer in the history" in r.note
    assert digest.resolve_since(root, "2001-01-01") is None
    with pytest.raises(SystemExit):
        digest.resolve_since(root, "not-a-rev")


def test_digest_tab_reads_then_writes_and_risk_links_to_it(moved_repo, capsys, spy):
    root = moved_repo
    _mapped(root, capsys)
    app, httpd = _server(root)
    try:
        st, d = _call(app, "GET", "/api/digest?since=before")
        assert st == 200 and d["missing"] >= 1 and "TASK: digest" not in spy
        st, d = _call(app, "POST", "/api/digest", {"since": "before"})
        assert st == 200 and d["missing"] == 0 and spy.count("TASK: digest") == 1
        storage = next(it for it in d["items"] if it["unit"] == "storage")
        assert storage["digest"]["headline"] and storage["range"]["how"] == "since"
        assert _call(app, "GET", "/api/digest?unit=nope")[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
    rc, out = run(root, capsys, "risk", "before..HEAD")
    assert "udebt digest --unit storage" in out or "->  OK" in out
