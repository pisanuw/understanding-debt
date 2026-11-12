"""The mapper's paths are checked against the tracked files (Phase 3.1): exact, then a unique
suffix, else dropped; a map covering too little is retried once, then written with warnings."""

import copy

import pytest
from conftest import git

from udebt import cli
from udebt import llm as llm_mod
from udebt.mapping import PathIndex, build_map, path_examples, resolve_paths
from udebt.store import Store

FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/api.py": "from pkg import store\nfrom pkg.util import helpers\n\ndef get():\n"
    "    return store.load()\n",
    "src/pkg/store.py": "import json\n\ndef load():\n    return json.loads('{}')\n",
    "src/pkg/util/__init__.py": "",
    "src/pkg/util/helpers.py": "def clamp(x):\n    return max(0, x)\n",
    "tools/api.py": "import sys\n",
    "README.md": "# pkg\n",
}


@pytest.fixture
def src_repo(tmp_path):
    """A package under src/, as huggingface_hub lays it out, plus a second api.py."""
    root = tmp_path / "srcrepo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Dev One")
    git(root, "config", "user.email", "dev1@example.com")
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    (root / "src/pkg/store.py").write_text("import json\n\ndef load():\n    return {}\n")
    git(root, "commit", "-q", "-am", "store: no json")
    Store(root).init()
    return root


def unit(uid, paths, centrality=3, children=None):
    u = {
        "id": uid,
        "name": uid,
        "kind": "module",
        "summary": "s",
        "decision": "d",
        "paths": paths,
        "centrality": centrality,
        "shaped_by": [],
    }
    if children:
        u["children"] = children
    return u


class Scripted:
    """An LLM that answers map prompts from a script, and keeps the prompts."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[str] = []

    def structured(self, prompt, schema, **kw):
        self.calls.append(prompt)
        return {"units": copy.deepcopy(self.answers.pop(0))}


def test_path_index_resolves_exact_then_unique_suffix():
    idx = PathIndex(list(FILES))
    assert idx.resolve("src/pkg/api.py") == ("src/pkg/api.py", "exact")
    assert idx.resolve("./src/pkg/") == ("src/pkg", "exact"), "a directory, normalised"
    assert idx.resolve("pkg/store.py") == ("src/pkg/store.py", "suffix")
    assert idx.resolve("util") == ("src/pkg/util", "suffix"), "a directory's tail"
    assert idx.resolve("api.py") == (None, "none"), "two files end in api.py"
    assert idx.resolve("pkg/missing.py") == (None, "none")
    assert idx.resolve(".") == (None, "none")
    assert path_examples(list(FILES)) == ["src/pkg/api.py", "src/pkg/util"]
    units = [unit("a", ["pkg", "api.py"], children=[unit("x", ["helpers.py"]), unit("y", [])])]
    rep = resolve_paths(units, idx)
    assert units[0]["paths"] == ["src/pkg"] and units[0]["unresolved"] == ["api.py"]
    assert units[0]["children"][0]["paths"] == ["src/pkg/util/helpers.py"]
    assert (rep.exact, rep.suffix, rep.unresolved, rep.given) == (0, 2, ["api.py"], 3)


def test_package_relative_paths_resolve_by_suffix(src_repo):
    store = Store(src_repo)
    llm = Scripted(
        [
            unit(
                "core",
                [],
                5,
                children=[
                    unit("api", ["pkg/api.py"]),
                    unit("storage", ["pkg/store.py", "src/pkg/store.py"]),
                ],
            ),
            unit("helpers", ["util", "README.md"], 2),
        ]
    )
    data = build_map(src_repo, store, llm, out=lambda _m: None)
    assert len(llm.calls) == 1, "covered: no retry"
    assert "relative to the repository ROOT" in llm.calls[0]
    assert "`src/pkg/api.py` and `src/pkg/util`" in llm.calls[0], "real examples"
    by = {u["id"]: u for u in data["units"]}
    kids = {c["id"]: c for c in by["core"]["children"]}
    assert kids["core/api"]["paths"] == ["src/pkg/api.py"]
    assert kids["core/storage"]["paths"] == ["src/pkg/store.py"], "deduplicated"
    assert by["core"]["paths"] == ["src/pkg/api.py", "src/pkg/store.py"]
    assert by["helpers"]["paths"] == ["src/pkg/util", "README.md"]
    assert data["paths"] == {
        "given": 5,
        "exact": 2,
        "suffix": 3,
        "unresolved": 0,
        "empty_units": [],
        "retried": False,
    }
    # 4 of the 6 clustered source files: src/pkg/__init__.py and tools/api.py are not listed
    assert "warnings" not in data and data["structure"]["coverage"] == 0.67
    assert kids["core/storage"]["churn"] > 0, "the resolved paths carry the history"


def test_an_unresolvable_path_is_dropped_and_recorded(src_repo):
    store = Store(src_repo)
    llm = Scripted(
        [unit("core", ["src/pkg", "api.py", "pkg/gone.py"], 5), unit("tools", ["tools"], 1)]
    )
    data = build_map(src_repo, store, llm, out=lambda _m: None)
    core = next(u for u in data["units"] if u["id"] == "core")
    assert core["paths"] == ["src/pkg"]
    assert core["unresolved"] == ["api.py", "pkg/gone.py"], "ambiguous and missing"
    assert data["paths"]["unresolved"] == 2 and len(llm.calls) == 1


def test_low_coverage_warns_retries_once_and_flags_the_map(src_repo, capsys):
    store = Store(src_repo)
    bad = [unit("core", ["pkg/missing.py", "lib/other.py"], 5), unit("tools", ["tools"], 1)]
    good = [unit("core", ["src/pkg"], 5), unit("tools", ["tools"], 1)]
    said: list[str] = []

    llm = Scripted(bad, good)
    data = build_map(src_repo, store, llm, out=said.append)
    assert len(llm.calls) == 2
    retry = llm.calls[1]
    assert "RETRY" in retry and "- pkg/missing.py" in retry and "core" in retry
    assert "`src/pkg/api.py` and `src/pkg/util`" in retry.split("RETRY")[1]
    assert said and "cover 17%" in said[0] and "Asking the model once more" in said[0]
    assert data["paths"]["retried"] and "warnings" not in data
    assert data["structure"]["coverage"] == 1.0

    llm = Scripted(bad, bad)
    said.clear()
    data = build_map(src_repo, store, llm, out=said.append)
    assert len(llm.calls) == 2 and data["warnings"]
    assert "1 unit(s) have no path that exists (core)" in data["warnings"][0]
    assert said[-1].startswith("Warning: the map may be wrong")
    assert data["units"][0]["unresolved"] == ["pkg/missing.py", "lib/other.py"]

    llm = Scripted(bad)
    data = build_map(src_repo, store, llm, retry=False, out=said.append)
    assert len(llm.calls) == 1 and data["warnings"] and not data["paths"]["retried"]

    # status shows the warning before anything else
    capsys.readouterr()
    assert cli.main(["--repo", str(src_repo), "status"]) == 0
    first = capsys.readouterr().out.lstrip().splitlines()[0]
    assert first.startswith("Warning: the map may be wrong")


def test_map_no_retry_flag(src_repo, monkeypatch, capsys):
    calls = []

    class Bad(llm_mod.FakeLLM):
        def structured(self, prompt, schema, **kw):
            calls.append(prompt)
            return {"units": [unit("core", ["nowhere.py"], 5)]}

    monkeypatch.setenv("UDEBT_LLM", "fake")
    monkeypatch.setattr(llm_mod, "FakeLLM", Bad)
    assert cli.main(["--repo", str(src_repo), "map", "--no-retry"]) == 0
    out = capsys.readouterr().out
    assert len(calls) == 1
    assert "Paths: 0 of 1 resolved (0 exact, 0 by a unique suffix), 1 dropped" in out
    assert "Warning: the map may be wrong" in out and "1 path(s) dropped: nowhere.py" in out
    assert cli.main(["--repo", str(src_repo), "map"]) == 0
    assert len(calls) == 3, "without the flag it asks once more"
    assert "after one retry" in capsys.readouterr().out
