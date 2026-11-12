import json

from conftest import AI_ENV, git

from udebt import cli
from udebt.history import rename_shas
from udebt.provenance import load_commits
from udebt.weights import compute_weights, ensure_weights


def test_heuristic_weights(mixed_repo):
    root = mixed_repo
    # whitespace-only reformat of scoring.py
    src = (root / "udebt" / "scoring.py").read_text()
    (root / "udebt" / "scoring.py").write_text(src.replace("    ", "        "))
    git(root, "commit", "-q", "-am", "style: reformat")
    # docs only
    (root / "README.md").write_text("# proj\n\nMore words.\n")
    git(root, "commit", "-q", "-am", "docs: expand readme")
    # a big new module in a new top-level dir with a dependency change
    (root / "svc").mkdir()
    (root / "svc" / "server.py").write_text("\n".join(f"x{i} = {i}" for i in range(300)) + "\n")
    (root / "pyproject.toml").write_text("[project]\nname='proj'\ndependencies=['fastapi']\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "feat: add http service", env=AI_ENV)

    cs = load_commits(root)
    w = compute_weights(root, cs, rename_shas(root))
    by_subject = {c.subject: w[c.sha] for c in cs}
    assert by_subject["style: reformat"].weight == 0.05
    assert "whitespace-only" in by_subject["style: reformat"].reasons[0]
    assert by_subject["docs: expand readme"].weight == 0.05
    big = by_subject["feat: add http service"]
    assert big.weight >= 0.9
    assert any("dependencies" in r for r in big.reasons)
    assert any("new top-level dir" in r for r in big.reasons)
    assert 0.1 <= by_subject["float literal"].weight <= 0.3  # one-line change


def test_cache_keeps_refinements(mixed_repo):
    root = mixed_repo
    cs = load_commits(root)
    path = root / ".udebt" / "commits.json"
    w = ensure_weights(root, path, cs, set())
    w[cs[0].sha].weight, w[cs[0].sha].refined = 0.99, "architectural"
    from udebt.weights import save_cache

    save_cache(path, w)
    again = ensure_weights(root, path, cs, set())
    assert again[cs[0].sha].weight == 0.99 and again[cs[0].sha].refined == "architectural"
    data = json.loads(path.read_text())
    assert data["version"] == 1 and len(data["commits"]) == len(cs)


def test_weigh_refine_and_risk_cli(mixed_repo, monkeypatch, capsys):
    monkeypatch.setenv("UDEBT_LLM", "fake")
    root = mixed_repo

    def run(*a):
        rc = cli.main(["--repo", str(root), *a])
        return rc, capsys.readouterr().out

    run("init")
    run("map")
    rc, out = run("weigh", "--refine", "2")
    assert rc == 0 and "Refined 2 commits" in out and "*" in out
    # risk of the last commit (dev2's one-liner on scoring) for dev1
    rc, out = run("risk", "HEAD~1..HEAD")
    # small mass, and dev1's prior on scoring is under the known threshold -> CAUTION
    assert rc == 0 and "scoring" in out and "->  CAUTION" in out
    # a stranger with zero understanding touching storage heavily
    (root / "udebt" / "store.py").write_text("\n".join(f"y{i} = {i}" for i in range(400)) + "\n")
    (root / "requirements.txt").write_text("sqlalchemy\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "switch storage to sqlalchemy")
    rc, out = run("--as-user", "new@example.com", "risk", "HEAD~1..HEAD")
    assert rc == 0 and "HIGH" in out and "storage" in out and "udebt dig storage" in out
    rc, out = run("--as-user", "new@example.com", "risk", "HEAD~1..HEAD", "--strict")
    assert rc == 1
    rc, out = run("hook", "install")
    assert rc == 0 and (root / ".git" / "hooks" / "pre-push").exists()
