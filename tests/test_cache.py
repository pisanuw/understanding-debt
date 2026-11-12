"""The parsed-commit cache (udebt/cache.py)."""

import json
import subprocess
from dataclasses import asdict

from conftest import git

from udebt import cache as cache_mod
from udebt import cli
from udebt.history import rename_shas
from udebt.provenance import load_commits, load_signatures
from udebt.weights import WeighInputs, compute_weights


def _with_merge(root):
    """mixed_repo plus a two-commit branch merged with a merge commit and a rename."""
    git(root, "checkout", "-q", "-b", "feature")
    (root / "udebt" / "extra.py").write_text("X = 1\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "extra module")
    git(root, "mv", "README.md", "README.rst")
    git(root, "commit", "-q", "-m", "rename readme")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "feature", "-m", "Merge pull request #1 from x/feature")
    return root


def _rows(commits):
    return [asdict(c) for c in commits]


def test_cold_load_equals_warm_load_and_the_uncached_parse(mixed_repo, tmp_path):
    root = _with_merge(mixed_repo)
    d = tmp_path / "udebt-dir"
    cold = cache_mod.ParsedCache(root, d)
    first = cold.commits()
    assert cold.stats.parsed == len(first) == 10 and cold.stats.rebuilt == "no cache"
    cold_inputs = cold.inputs(first)
    warm = cache_mod.ParsedCache(root, d)  # a fresh process reads the file
    again = warm.commits()
    assert warm.stats.parsed == 0 and not warm.stats.written
    assert _rows(first) == _rows(again) == _rows(load_commits(root))
    assert any(c.is_merge for c in again)
    warm_inputs = warm.inputs(again)
    assert warm.stats.inputs == {}  # nothing left to compute
    full = WeighInputs.from_git(root, again, rename_shas(root))
    assert warm_inputs == cold_inputs
    assert warm_inputs.eff == full.eff and warm_inputs.renames == full.renames
    assert warm_inputs.added == {k: v for k, v in full.added.items() if v}
    assert warm_inputs.versions == full.versions
    w_cached = compute_weights(root, again, warm_inputs.renames, inputs=warm_inputs)
    w_full = compute_weights(root, again, full.renames)
    assert {k: asdict(v) for k, v in w_cached.items()} == {k: asdict(v) for k, v in w_full.items()}


def test_a_new_commit_is_the_only_one_parsed(mixed_repo, tmp_path, monkeypatch):
    root = mixed_repo
    d = tmp_path / "udebt-dir"
    cache_mod.ParsedCache(root, d).inputs(cache_mod.ParsedCache(root, d).commits())
    (root / "udebt" / "store.py").write_text("STATE = {'new': True}\n")
    git(root, "commit", "-q", "-am", "one more")
    new = git(root, "rev-parse", "HEAD").strip()

    seen: dict[str, list] = {"parse": [], "eff": [], "add": [], "ren": [], "ver": []}

    def spy(name, fn):
        def wrapper(root_, shas=None, *rest, **kw):
            seen[name].append(list(shas) if shas is not None else None)
            return fn(root_, shas, *rest, **kw)

        return wrapper

    monkeypatch.setattr(cache_mod, "parse_log", spy("parse", cache_mod.parse_log))
    monkeypatch.setattr(cache_mod, "_effective", spy("eff", cache_mod._effective))
    monkeypatch.setattr(cache_mod, "added_files", spy("add", cache_mod.added_files))
    monkeypatch.setattr(cache_mod, "rename_shas", spy("ren", cache_mod.rename_shas))
    monkeypatch.setattr(cache_mod, "version_bumps", spy("ver", cache_mod.version_bumps))
    pc = cache_mod.ParsedCache(root, d)
    commits = pc.commits()
    assert seen["parse"] == [[new]] and pc.stats.parsed == 1
    assert commits[-1].sha == new and commits[-1].subject == "one more"
    pc.inputs(commits)
    assert seen["eff"] == seen["add"] == seen["ren"] == seen["ver"] == [[new]]
    assert pc.stats.inputs == {"eff": 1, "add": 1, "ren": 1, "ver": 1}
    # and a third load parses nothing at all
    for v in seen.values():
        v.clear()
    pc3 = cache_mod.ParsedCache(root, d)
    pc3.inputs(pc3.commits())
    assert not any(seen.values()) and not pc3.stats.written


def test_rewritten_history_drops_stale_shas(mixed_repo, tmp_path):
    root = mixed_repo
    d = tmp_path / "udebt-dir"
    before = cache_mod.ParsedCache(root, d).commits()
    old_head = before[-1].sha
    git(root, "commit", "-q", "--amend", "-m", "float literal, reworded")
    pc = cache_mod.ParsedCache(root, d)
    after = pc.commits()
    assert pc.stats.dropped == 1 and pc.stats.parsed == 1
    assert old_head not in {c.sha for c in after}
    assert after[-1].subject == "float literal, reworded"
    on_disk = json.loads(pc.path.read_text())["commits"]
    assert old_head not in on_disk and after[-1].sha in on_disk
    assert _rows(after) == _rows(load_commits(root))
    # a reset to an older commit drops the commits above it too
    git(root, "reset", "-q", "--hard", "HEAD~2")
    pc = cache_mod.ParsedCache(root, d)
    assert len(pc.commits()) == len(after) - 2 and pc.stats.dropped == 2


def test_classification_is_recomputed_without_a_reparse(mixed_repo, tmp_path):
    root = mixed_repo
    d = tmp_path / "udebt-dir"
    cache_mod.ParsedCache(root, d).commits()
    sigs = load_signatures([{"name": "dev2bot", "author_emails": [r"^dev2@example\.com$"]}])
    pc = cache_mod.ParsedCache(root, d)
    commits = pc.commits(sigs)
    assert pc.stats.parsed == 0
    assert (commits[-1].klass, commits[-1].agent) == ("ai", "dev2bot")


def test_mailmap_change_rebuilds(mixed_repo, tmp_path):
    root = mixed_repo
    d = tmp_path / "udebt-dir"
    cache_mod.ParsedCache(root, d).commits()
    (root / ".mailmap").write_text("Dev Two Real <dev2@real.example> <dev2@example.com>\n")
    pc = cache_mod.ParsedCache(root, d)
    commits = pc.commits()
    assert pc.stats.rebuilt == ".mailmap changed" and pc.stats.parsed == len(commits)
    assert commits[-1].author_email == "dev2@real.example"


def test_large_cache_is_gzipped(mixed_repo, tmp_path, monkeypatch):
    root = mixed_repo
    d = tmp_path / "udebt-dir"
    first = cache_mod.ParsedCache(root, d).commits()
    assert (d / cache_mod.NAME).exists()
    monkeypatch.setattr(cache_mod, "GZIP_OVER", 100)
    (root / "README.md").write_text("# proj\n\nmore\n")
    git(root, "commit", "-q", "-am", "docs")
    pc = cache_mod.ParsedCache(root, d)
    again = pc.commits()
    assert pc.path.name == cache_mod.NAME + ".gz" and not (d / cache_mod.NAME).exists()
    assert _rows(again[:-1]) == _rows(first)
    assert _rows(cache_mod.ParsedCache(root, d).commits()) == _rows(again)


def test_cache_cli_and_gitignore(mixed_repo, monkeypatch, capsys):
    monkeypatch.setenv("UDEBT_LLM", "fake")
    root = mixed_repo

    def run(*a):
        rc = cli.main(["--repo", str(root), *a])
        return rc, capsys.readouterr().out

    run("init")
    assert "commits-parsed.json" in (root / ".udebt" / ".gitignore").read_text()
    rc, out = run("cache")
    assert rc == 0 and "No parsed-commit cache yet" in out
    rc, out = run("weigh")
    rc, out = run("cache", "status")
    assert rc == 0 and "7 commits cached; 7 reachable from HEAD: 0 new" in out
    assert "gitignored: yes" in out and "version 4" in out
    rc, out = run("cache", "rebuild")
    assert rc == 0 and "Rebuilt: parsed 7 commits" in out and "versions 7" in out
    status = subprocess.run(
        ["git", "status", "--porcelain", "--ignored"], cwd=root, capture_output=True, text=True
    ).stdout
    assert "!! .udebt/commits-parsed.json" in status  # ignored, not untracked


def test_a_changed_input_rule_recomputes_only_that_input(mixed_repo, tmp_path):
    root = mixed_repo
    d = tmp_path / "udebt-dir"
    pc = cache_mod.ParsedCache(root, d)
    commits = pc.commits()
    want = pc.versions(commits)
    pc.inputs(commits)
    data = json.loads(pc.path.read_text())
    data.pop("inputs")  # a cache written before `ver` had a version
    for e in data["commits"].values():
        e["ver"] = ["stale.json"]
    pc.path.write_text(json.dumps(data))
    again = cache_mod.ParsedCache(root, d)
    commits = again.commits()
    assert again.stats.reset == ["ver"] and again.stats.parsed == 0
    assert again.versions(commits) == want
    assert again.stats.inputs == {"ver": len(commits)}  # eff, add and ren were kept
    third = cache_mod.ParsedCache(root, d)
    third.commits()
    assert third.stats.reset == [] and not third.stats.written
