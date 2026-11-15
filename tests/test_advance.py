"""Advance quizzes (`udebt worker quizzes`, udebt/hosted/advance.py): questions written ahead of
time for the hosted site's visitors who are not signed in, self-checked against their reference
answers. The model here is the FakeLLM; the code comes from a small git repository.

The prompt is the hosted quiz's own (web/src/core/prompts.ts): this test writes cases with the
Python answers to web/test/fixtures/advance-prompt-parity.json, which vitest checks the
TypeScript against, and fails when the committed file is stale (regenerate with
UDEBT_WRITE_PARITY=1 pytest tests/test_advance.py)."""

from __future__ import annotations

import io
import json
import os
import urllib.error
from pathlib import Path

import pytest
from conftest import git

from udebt import contract
from udebt.cli import main
from udebt.hosted import advance
from udebt.llm import FakeLLM
from udebt.loadsql import analysis_json, analysis_sql
from udebt.prompts import ORIENTATION_RULE, REVEALED_RULE

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "web" / "test" / "fixtures" / "advance-prompt-parity.json"
DM = ROOT / "tests" / "fixtures" / "hosted" / "digitalme"
URL = "https://github.com/acme/shop"


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "shop"
    files = {
        "app/server.py": "".join(f"line {i} of the server\n" for i in range(1, 121)),
        "app/store.py": "STATE = {}\n\ndef put(k, v):\n    STATE[k] = v\n",
        "web/src/main.ts": "export const main = () => 'hello';\n" * 3,
        "web/src/view.ts": "export function view() {\n  return 1;\n}\n",
        "web/README.md": "# web\n",
        "web/logo.png": "\0PNG",
    }
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Dev One")
    git(root, "config", "user.email", "dev1@example.com")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    return root, git(root, "rev-parse", "HEAD").strip()


def specs() -> dict:
    hist = [{"sha": "a" * 40, "date": "2026-01-02", "klass": "ai", "subject": "Add the server",
             "author": "Dev One"}]  # fmt: skip
    return {
        "units": [
            {"id": "server", "name": "HTTP server", "summary": "Serves the shop.",
             "decision": "One process.", "alternatives": "Workers.", "why": "Simple.",
             "paths": ["app/server.py"], "history": hist, "parent": None},
            {"id": "store", "name": "Store", "summary": "Keeps state.", "paths": ["app/store.py"],
             "parent": None, "orientation": {"purpose": "Keeps what the server knows.",
             "decision": "A dict, not a database.", "where": [{"path": "app/store.py",
             "what": "the dict"}], "shaped_by": "Dev One.", "basis": "x", "head": "y"}},
            {"id": "web", "name": "Web front end", "summary": "The page.", "paths": ["web"],
             "parent": None},
            {"id": "web/view", "name": "View", "summary": "Draws.", "paths": ["web/src/view.ts"],
             "parent": "web"},
            {"id": "box", "name": "Vendored", "paths": ["vendor"], "scope": "blackbox",
             "parent": None},
            {"id": "bare", "name": "Bare", "summary": None, "decision": None,
             "paths": ["nowhere/"], "history": [{"sha": "b" * 40, "klass": None}], "parent": None},
        ],
        "walkthroughs": {"server": {"steps": [
            {"file": "app/server.py", "start_line": 10, "end_line": 30, "narration": "x"},
            {"file": "app/server.py", "start_line": 40, "end_line": 50, "narration": "y"}]}},
    }  # fmt: skip


def analysis(sha: str) -> dict:
    return {"url": URL, "name": "shop", "sha": sha, "specs": specs()}


def test_one_call_per_level_and_batch_three_questions_each_with_its_reference(tmp_path):
    root, sha = make_repo(tmp_path)
    llm = FakeLLM()
    logs: list[str] = []
    doc = advance.write(analysis(sha), llm, advance.CloneSource(root), batch=2, log=logs.append)
    assert contract.validate("quizzes", doc) == []
    assert (doc["format"], doc["version"], doc["repo"], doc["sha"]) == (
        "udebt-advance-quizzes", 1, URL, sha)  # fmt: skip
    # 5 architectural units (the blackbox one is left out) in batches of 2, three levels
    assert doc["calls"] == len(llm.calls) == 9 and doc["levels"] == [1, 2, 3]
    units = {q["unit_id"] for q in doc["questions"]}
    assert units == {"server", "store", "web", "web/view", "bare"}
    for uid in units:
        for lv in (1, 2, 3):
            got = [q for q in doc["questions"] if q["unit_id"] == uid and q["level"] == lv]
            assert len(got) == 3, (uid, lv)
            assert all(q["reference_answer"] and len(q["variants"]) == 2 for q in got)
            assert all(("orientation" in q) == (lv == 1) for q in got)
    store1 = next(q for q in doc["questions"] if q["unit_id"] == "store" and q["level"] == 1)
    assert store1["orientation"] == {"purpose": "Keeps what the server knows.",
        "decision": "A dict, not a database.", "where": [{"path": "app/store.py",
        "what": "the dict"}], "shaped_by": "Dev One."}  # fmt: skip
    bare1 = next(q for q in doc["questions"] if q["unit_id"] == "bare" and q["level"] == 1)
    assert bare1["orientation"]["fallback"] is True
    first, level2 = llm.calls[0], llm.calls[3]
    # level 1 shows the orientation the learner reads first; level 2 does not
    assert ORIENTATION_RULE in first and "orientation the developer has just read" in first
    assert ORIENTATION_RULE not in level2 and REVEALED_RULE not in level2
    assert "--- app/server.py lines 10-30" in first and "--- app/server.py lines 40-50" in first
    assert "Write 3 question(s) for each unit" in first
    # the tree fallback: `web` names only a directory, so the top files under it are read
    web = next(c for c in llm.calls if "- unit_id: web\n" in c)
    assert "--- web/src/main.ts lines 1-3" in web and "--- web/src/view.ts lines 1-3" in web
    assert "logo.png" not in web
    bare = next(c for c in llm.calls if "- unit_id: bare\n" in c and "unit_id: web" not in c)
    assert "No code excerpts could be fetched" in bare
    assert any("level 1: server, store" in s for s in logs)


def test_a_short_answer_is_reported_and_questions_without_a_reference_are_dropped(tmp_path):
    root, sha = make_repo(tmp_path)

    class Thin:
        def __init__(self):
            self.calls = 0

        def structured(self, prompt, schema, *, cwd, read_tools=True, max_turns=40):
            assert read_tools is False and max_turns == advance.MAX_TURNS
            self.calls += 1
            ids = [ln.split(":", 1)[1].strip() for ln in prompt.splitlines()
                   if ln.startswith("- unit_id:")]  # fmt: skip
            return {"questions": [
                {"unit_id": ids[0], "question": "What is it for?", "variants": ["Why?", "How?"],
                 "reference_answer": "It serves.", "evidence": ["app/server.py"], "level": 5},
                {"unit_id": ids[0], "question": "What is it for?", "reference_answer": "Again."},
                {"unit_id": ids[0], "question": "Who wrote it?", "reference_answer": ""},
                {"unit_id": ids[0], "question": "Graded?",
                 "reference_answer": "(graded against the code)"},
                {"unit_id": "elsewhere", "question": "x?", "reference_answer": "y"},
                {"unit_id": ids[0], "question": ""}, "not a question"]}  # fmt: skip

    logs: list[str] = []
    llm = Thin()
    doc = advance.write(analysis(sha), llm, advance.CloneSource(root), levels=[2], per=2,
                        batch=12, log=logs.append)  # fmt: skip
    assert llm.calls == 1
    assert [(q["unit_id"], q["level"], q["question"]) for q in doc["questions"]] == [
        ("server", 2, "What is it for?")]  # fmt: skip
    assert "Fewer than 2 question(s)" in logs[-1] and "store level 2: 0" in logs[-1]


def test_levels_4_and_5_and_out_of_range_numbers_are_refused(tmp_path):
    root, sha = make_repo(tmp_path)
    src = advance.CloneSource(root)
    for kw in ({"levels": [4]}, {"levels": []}, {"per": 4}, {"batch": 13}):
        with pytest.raises(advance.AdvanceError):
            advance.write(analysis(sha), FakeLLM(), src, **kw)
    with pytest.raises(advance.AdvanceError):
        advance.write({**analysis(sha), "specs": {"units": []}}, FakeLLM(), src)
    assert advance.parse_levels("1-3") == [1, 2, 3] and advance.parse_levels("2,1") == [1, 2]
    for bad in ("1-5", "x", "0"):
        with pytest.raises(advance.AdvanceError):
            advance.parse_levels(bad)


def test_the_command_writes_a_valid_file_and_the_plan_makes_no_call(tmp_path, monkeypatch, capsys):
    root, sha = make_repo(tmp_path)
    repo = {"url": URL, "name": "shop", "license": None, "is_sample": True}
    an = {"repo_url": URL, "sha": sha, "user_id": None, "specs": specs()}
    rows = {"repositories": [repo], "analyses": [an]}
    src = tmp_path / "shop.json"
    src.write_text(json.dumps(rows))
    monkeypatch.setenv("UDEBT_LLM", "fake")
    out = tmp_path / "out" / "shop.quizzes.json"
    assert main(["worker", "quizzes", str(src), "--clone", str(root), "--plan"]) == 0
    said = capsys.readouterr().out
    assert "shop at" in said and "3 call(s)" in said and "level 1: server, store" in said
    assert not out.exists()
    assert main(["worker", "quizzes", str(src), "--clone", str(root), "--out", str(out),
                 "--levels", "1-2", "--per", "2"]) == 0  # fmt: skip
    doc = json.loads(out.read_text())
    assert doc["levels"] == [1, 2] and doc["per"] == 2 and doc["calls"] == 2
    assert len(doc["questions"]) == 5 * 2 * 2
    assert "Wrote" in capsys.readouterr().out
    assert main(["validate", str(out)]) == 0
    with pytest.raises(SystemExit, match="--out is needed"):
        main(["worker", "quizzes", str(src), "--clone", str(root)])
    with pytest.raises(SystemExit, match="levels 1-3 only"):
        main(["worker", "quizzes", str(src), "--levels", "4", "--out", str(out)])
    with pytest.raises(SystemExit, match="not a worker output"):
        main(["worker", "quizzes", str(tmp_path / "nope.json"), "--out", str(out)])


def test_sources_a_worker_output_a_script_a_rows_file_and_a_sample_name(tmp_path, capsys):
    url = "https://github.com/pisanuw/digitalme"
    a = advance.load_source(str(DM))  # the URL the worker cloned (here a local path)
    assert a["name"] == "digitalme" and len(a["specs"]["units"]) == 15
    sql = tmp_path / "dm.sql"
    sql.write_text(analysis_sql(DM, public=True, url=url, license="MIT"))
    b = advance.load_source(str(sql))  # a public load: no commit emails
    assert (b["url"], b["name"], b["sha"]) == (url, a["name"], a["sha"])
    assert [u["id"] for u in b["specs"]["units"]] == [u["id"] for u in a["specs"]["units"]]
    rows = tmp_path / "dm.json"
    rows.write_text(analysis_json(DM, public=True, url=url))
    assert advance.load_source(str(rows)) == b
    hf = advance.load_source("huggingface_hub", root=ROOT)
    assert hf["url"] == "https://github.com/huggingface/huggingface_hub" and hf["specs"]["units"]
    (tmp_path / "empty.json").write_text('{"analyses": []}')
    for bad in (str(tmp_path / "empty.json"), "mlflow"):
        with pytest.raises(advance.AdvanceError):
            advance.load_source(bad, root=tmp_path)
    assert isinstance(advance.source_for(url, None), advance.GitHubSource)
    with pytest.raises(advance.AdvanceError):
        advance.source_for("https://gitlab.com/a/b", None)


def test_a_sample_not_at_its_pin_is_noted(tmp_path, monkeypatch, capsys):
    pin = "2c61ae8c11eedb1b79e262dd9eb5e68962cac063"
    out = tmp_path / "hf.json"
    rows = {"repositories": [{"url": "https://github.com/huggingface/huggingface_hub",
                              "name": "huggingface_hub"}],
            "analyses": [{"repo_url": "https://github.com/huggingface/huggingface_hub",
                          "sha": "0" * 40, "specs": {"units": [{"id": "a", "name": "A",
                                                                "paths": ["x/"]}]}}]}  # fmt: skip
    (tmp_path / "hf.json").write_text(json.dumps(rows))
    monkeypatch.setenv("UDEBT_LLM", "fake")
    monkeypatch.setattr(advance.GitHubSource, "entries", lambda self, sha: None)
    assert main(["worker", "quizzes", str(out), "--plan"]) == 0
    err = capsys.readouterr()
    assert f"pinned at {pin[:12]}" in err.err and "no code found for: a" in err.out


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, status: int = 200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_github_source_reads_raw_files_and_the_tree_without_a_network():
    seen: list[str] = []
    tree = {"tree": [{"path": "a/b.py", "type": "blob", "mode": "100644", "size": 10},
                     {"path": "a/link", "type": "blob", "mode": "120000", "size": 3},
                     {"path": "a", "type": "tree"}]}  # fmt: skip

    def opener(req, timeout):
        seen.append(req.full_url)
        if "api.github.com" in req.full_url:
            return FakeResponse(json.dumps(tree).encode())
        if "missing" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
        if "big" in req.full_url:
            return FakeResponse(b"x" * (advance.MAX_FILE_BYTES + 1))
        if "moved" in req.full_url:
            return FakeResponse(b"", status=301)
        return FakeResponse("héllo\n".encode())

    g = advance.GitHubSource("acme", "shop", opener=opener)
    assert g.read("f" * 40, "dir/a file.py") == "héllo\n"
    assert seen[-1] == f"https://raw.githubusercontent.com/acme/shop/{'f' * 40}/dir/a%20file.py"
    assert g.read("f" * 40, "missing.py") is None and g.read("f" * 40, "big.py") is None
    assert g.read("f" * 40, "moved.py") is None
    assert g.entries("f" * 40) == [("a/b.py", 10)]
    assert seen[-1].startswith("https://api.github.com/repos/acme/shop/git/trees/")

    def broken(req, timeout):
        return FakeResponse(b"not json")

    assert advance.GitHubSource("a", "b", opener=broken).entries("f" * 40) is None
    assert advance.CloneSource(Path("/nonexistent")).entries("f" * 40) is None


# -- the prompt, character for character with web/src/core/prompts.ts -------------------------

SHA = "0123456789abcdef0123456789abcdef01234567"


def parity() -> dict:
    hist = [
        {"sha": "a" * 40, "date": None, "klass": None, "subject": "Fix " + "é" * 30 + "😀" * 30},
        {"sha": "b" * 40, "date": "2026-01-02", "klass": "assisted", "subject": None},
        *[{"sha": c * 40, "date": "2026-01-03", "klass": "human", "subject": "more"}
          for c in "cdef"],
    ]  # fmt: skip
    server = {"id": "server", "name": "HTTP server", "parent": None, "summary": "Serves.",
              "decision": "One process.", "alternatives": None, "why": "Simple.",
              "paths": ["app/server.py", "app/"], "history": hist}  # fmt: skip
    child = {"id": "server/auth", "name": "Auth", "parent": "server", "summary": None,
             "decision": None, "paths": [], "history": []}  # fmt: skip
    oriented = {**child, "orientation": {"purpose": "Checks tokens.", "decision": "JWT.",
                "where": [{"path": "app/auth.py", "what": "the check"}, {"path": "app/"}],
                "shaped_by": "Dev One."}}  # fmt: skip
    text = "".join(f"line {i}\r\n" for i in range(1, 300)) + "tail"
    excerpts = [
        advance.slice_lines(text, {"path": "app/server.py", "start": 5, "end": 12}),
        advance.slice_lines(text, {"path": "app/big.py", "start": 1, "end": 500}),
        advance.slice_lines("one\ntwo\n", {"path": "app/short.py", "start": 1, "end": 60}),
    ]
    units_all = [server, child, oriented]
    cases = []
    for name, units, level, per, ex, revealed in [
        ("level 1 with the orientation, a sub-unit and excerpts", [server, child], 1, 3,
         excerpts, []),
        ("level 2, a revealed miss, no excerpts", [server], 2, 1, [], ["What is it for?"]),
        ("level 3, a sub-unit with an orientation", [oriented], 3, 2, excerpts[:1], []),
    ]:  # fmt: skip
        raw = []
        for u in units:
            q = advance.question_unit(u, level, units_all)
            q["revealed"] = revealed
            q["refs"] = advance.refs_for_unit(u, None)
            raw.append(q)
        norm = [advance.normalize_unit(q) for q in raw]
        cases.append({"name": name, "repo": URL, "sha": SHA, "per_unit": per, "units": raw,
                      "excerpts": ex,
                      "prompt": advance.questions_prompt(norm, per, URL, SHA, ex)})  # fmt: skip
    slices = [
        {"text": text, "ref": {"path": "p", "start": s, "end": e},
         "excerpt": advance.slice_lines(text, {"path": "p", "start": s, "end": e})}
        for s, e in [(1, 3), (298, 310), (0, 2), (5, 4), (10, 400), (400, 410)]
    ]  # fmt: skip
    walks = {"steps": [{"file": "./a/b.py", "start_line": 3, "end_line": 9},
                       {"file": "../x.py", "start_line": 1, "end_line": 2},
                       {"file": "a/c.py", "start_line": "x", "end_line": None}]}  # fmt: skip
    refs = [
        {"unit": u, "walk": w, "refs": advance.refs_for_unit(u, w)}
        for u, w in [
            (server, None), (server, walks), (child, None), (oriented, None),
            ({"id": "f", "name": "F", "paths": ["d/"], "files": ["d/a.py", "../c"]}, None),
        ]
    ]  # fmt: skip
    many = [advance.slice_lines("x" * 990 + "\n", {"path": f"f{i}.py", "start": 1, "end": 1})
            for i in range(70)]  # fmt: skip
    return {
        "comment": "Written by tests/test_advance.py from udebt/hosted/advance.py; "
        "UDEBT_WRITE_PARITY=1 pytest tests/test_advance.py regenerates it.",
        "cases": cases,
        "slices": slices,
        "refs": refs,
        "orientations": [
            {
                "unit": u,
                "orientation": advance.orientation_of(u),
                "text": advance.orientation_text(advance.orientation_of(u)),
            }
            for u in units_all
        ],  # fmt: skip
        "budget": {
            "excerpts": many,
            "max": advance.EXCERPT_BUDGET,
            "kept": len(advance.within_budget(many, advance.EXCERPT_BUDGET)),
        },  # fmt: skip
    }


def _dump(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, indent=1) + "\n"


def test_advance_prompt_parity_fixture_is_current():
    want = _dump(parity())
    if os.environ.get("UDEBT_WRITE_PARITY") == "1" or not FIXTURE.exists():
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(want, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == want, (
        "web/test/fixtures/advance-prompt-parity.json is stale: UDEBT_WRITE_PARITY=1 pytest "
        "tests/test_advance.py"
    )


def test_the_parity_cases_cover_what_they_say():
    p = parity()
    first = p["cases"][0]["prompt"]
    assert ORIENTATION_RULE in first and "(a part of HTTP server)" in first
    assert "shaped_by: " + "a" * 40 + "  [] Fix" in first  # the request's caps: '' for nulls
    assert REVEALED_RULE in p["cases"][1]["prompt"]
    assert "... 100 more line(s)" in first  # 500 asked, 200 kept
    assert p["budget"]["kept"] < 70
