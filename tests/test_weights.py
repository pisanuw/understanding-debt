import json

from conftest import AI_ENV, git

from udebt import cli
from udebt.history import rename_shas
from udebt.provenance import load_commits
from udebt.weights import (
    CACHE_VERSION,
    VERSION_BUMP_WEIGHT,
    branch_groups,
    compute_weights,
    ensure_weights,
    parse_version_diff,
    version_bumps,
    version_only,
)


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
    assert data["version"] == CACHE_VERSION and len(data["commits"]) == len(cs)


def test_version_1_cache_is_recomputed_keeping_refinements(mixed_repo):
    root = mixed_repo
    cs = load_commits(root)
    path = root / ".udebt" / "commits.json"
    fresh = compute_weights(root, cs, set())
    first = cs[0].sha
    v1 = {
        sha: {
            "sha": sha,
            "weight": 0.123,  # stale numbers from an older heuristic
            "reasons": ["old"],
            "effective_lines": w.effective_lines,
            "refined": "architectural" if sha == first else None,
        }
        for sha, w in fresh.items()
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "commits": v1}))
    again = ensure_weights(root, path, cs, set())
    assert json.loads(path.read_text())["version"] == CACHE_VERSION
    # recomputed from the current heuristic; the refinement is blended in again, not lost
    assert again[first].refined == "architectural"
    assert again[first].weight == round(0.5 * fresh[first].weight + 0.5 * 1.0, 3)
    assert all(again[c.sha].weight == fresh[c.sha].weight for c in cs[1:])


def _feature_repo(root, *, squash: bool):
    """main with one module, then a feature of ten 20-line commits, merged with a merge commit
    or squashed into one commit."""
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Dev One")
    git(root, "config", "user.email", "dev1@example.com")
    (root / "app").mkdir()
    (root / "app" / "base.py").write_text("BASE = 1\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    lines: list[str] = []
    if not squash:
        git(root, "checkout", "-q", "-b", "feature")
    for i in range(10):
        lines += [f"def f{i}_{j}():\n    return {i * 100 + j}\n" for j in range(10)]
        (root / "app" / "core.py").write_text("".join(lines))
        if not squash:
            git(root, "add", ".")
            git(root, "commit", "-q", "-m", f"core step {i}")
    if squash:
        git(root, "add", ".")
        git(root, "commit", "-q", "-m", "Add core (#1)")
    else:
        git(root, "checkout", "-q", "main")
        (root / "app" / "base.py").write_text("BASE = 2\n")  # main moved on meanwhile
        git(root, "commit", "-q", "-am", "bump base")
        git(root, "merge", "-q", "--no-ff", "feature", "-m", "Merge pull request #1 from x/feature")
    return root


def test_squash_and_merge_weigh_alike(tmp_path):
    merged = _feature_repo(tmp_path / "merged", squash=False)
    squashed = _feature_repo(tmp_path / "squashed", squash=True)

    def feature_mass(root):
        cs = load_commits(root)
        w = compute_weights(root, cs, set())
        feat = [c for c in cs if "app/core.py" in c.files]
        return sum(w[c.sha].weight for c in feat), [w[c.sha] for c in feat]

    m_total, m_weights = feature_mass(merged)
    s_total, (s_weight,) = feature_mass(squashed)
    assert len(m_weights) == 10 and s_weight.group is None
    assert abs(m_total - s_total) <= 0.2 * s_total, (m_total, s_total)
    # before grouping, the ten small commits weighed several times the squashed one
    standalone = sum(w.standalone for w in m_weights)
    assert standalone > 4 * s_total
    assert all(w.group and "weighed as one" in w.reasons[0] for w in m_weights)
    # main-line commits are left alone
    cs = load_commits(merged)
    w = compute_weights(merged, cs, set())
    bump = next(c for c in cs if c.subject == "bump base")
    assert w[bump.sha].group is None and w[bump.sha].standalone is None
    merge = next(c for c in cs if c.is_merge)
    assert w[merge.sha].weight == 0.0


def test_branch_groups_nesting(tmp_path):
    """A pull request merged into a long-lived branch is its own group; the long-lived
    branch's direct commits are the outer merge's group; merging main into a branch brings
    in nothing; main-line commits belong to no group."""
    root = tmp_path / "g"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "D")
    git(root, "config", "user.email", "d@example.com")

    def commit(name):
        (root / f"{name}.py").write_text(f"{name} = 1\n")
        git(root, "add", ".")
        git(root, "commit", "-q", "-m", name)
        return git(root, "rev-parse", "HEAD").strip()

    m0 = commit("m0")
    git(root, "checkout", "-q", "-b", "develop")
    d1 = commit("d1")
    git(root, "checkout", "-q", "-b", "pr")
    p1, p2 = commit("p1"), commit("p2")
    git(root, "checkout", "-q", "main")
    m1 = commit("m1")
    git(root, "checkout", "-q", "pr")
    git(root, "merge", "-q", "--no-ff", "main", "-m", "Merge branch 'main' into pr")
    p3 = commit("p3")
    git(root, "checkout", "-q", "develop")
    git(root, "merge", "-q", "--no-ff", "pr", "-m", "Merge pull request #2 from x/pr")
    pr_merge = git(root, "rev-parse", "HEAD").strip()
    d2 = commit("d2")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "develop", "-m", "Merge branch 'develop'")
    release = git(root, "rev-parse", "HEAD").strip()
    m2 = commit("m2")

    groups = branch_groups(load_commits(root))
    assert groups[pr_merge] == [p1, p2, p3]
    assert groups[release] == [d1, d2]
    grouped = {s for ms in groups.values() for s in ms}
    assert not grouped & {m0, m1, m2}
    assert len(groups) == 2  # "Merge branch 'main' into pr" has no group of its own


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


def test_version_only_lines():
    assert version_only(['  "version": "1.2.3",'], ['  "version": "1.3.0",'])
    assert version_only(['version = "0.4.1"'], ['version = "0.5.0"'])  # pyproject, Cargo
    assert version_only(["__version__ = '2.0.0'"], ["__version__ = '2.0.1'"])
    assert version_only(["    <version>1.0</version>"], ["    <version>1.1-SNAPSHOT</version>"])
    assert version_only(["8.1.0"], ["8.2.0"])  # a VERSION file
    # a monorepo release rewrites the internal pins too: same line apart from the version
    assert version_only(
        ['"version": "8.1.0",', '"@sentry/core": "8.1.0",'],
        ['"version": "8.2.0",', '"@sentry/core": "8.2.0",'],
    )
    assert not version_only([], ['"fastapi": "0.110.0",'])  # a new dependency
    assert not version_only(['"fastapi": "0.110.0",'], ['"starlette": "0.37.0",'])  # a swap
    assert not version_only(['"port": 3000,'], ['"port": 4000,'])  # not a version string
    assert not version_only(['"version": "1.0.0",'], ['"version": "1.0.1",', '"private": true,'])
    assert not version_only(["", "  "], [""])  # nothing but blank lines


def test_major_upgrades_are_dependency_changes():
    def bump(a, b):
        return version_only([a], [b])

    # patch and minor upgrades stay version bumps, in every common format
    assert bump('"left-pad": "1.4.2",', '"left-pad": "1.5.0",')
    assert bump('"react": "^18.2.0",', '"react": "^18.3.1",')
    assert bump('"zod": "~3.22.4",', '"zod": "~3.23.0",')
    assert bump('"pydantic>=2.5.0",', '"pydantic>=2.9.2",')
    assert bump("uvicorn[standard]>=0.23.0", "uvicorn[standard]>=0.23.2")
    assert bump("django~=4.2.0", "django~=4.2.7")
    assert bump(
        'serde = { version = "1.0.188", features = ["derive"] }',
        'serde = { version = "1.0.190", features = ["derive"] }',
    )
    assert bump("\tgithub.com/go-git/go-billy/v5 v5.5.0", "\tgithub.com/go-git/go-billy/v5 v5.6.0")
    assert bump('gem "rails", "~> 7.0.4"', 'gem "rails", "~> 7.1.0"')
    # a new major is a dependency change; for 0.x the minor is the major
    assert not bump('"left-pad": "1.5.0",', '"left-pad": "2.0.0",')
    assert not bump('"react": "^17.0.2",', '"react": "^18.2.0",')
    assert not bump('"pydantic>=1.10.0",', '"pydantic>=2.0.0",')
    assert not bump("uvicorn[standard]>=0.23.0", "uvicorn[standard]>=0.30.0")
    assert not bump('"griffe>=0.49.0,<2.0.0",', '"griffe>=0.49.0,<3.0.0",')  # allows a new major
    assert not bump('tokio = "0.2.22"', 'tokio = "1.0.0"')
    assert not bump('gem "rails", "~> 6.1"', 'gem "rails", "~> 7.0"')
    assert not bump("\tgithub.com/x/y/v2 v2.1.0", "\tgithub.com/x/y/v3 v3.0.0")  # module path
    # the project's own version, and its own packages pinned to it, stay version bumps
    assert bump('"version": "1.9.0",', '"version": "2.0.0",')
    assert version_only(
        ['"version": "7.120.0",', '"@sentry/core": "7.120.0",'],
        ['"version": "8.0.0",', '"@sentry/core": "8.0.0",'],
    )
    # calendar versions and versions that are not numbers keep the old behaviour
    assert bump("certifi==2023.7.22", "certifi==2024.2.2")
    assert not bump('"foo": "latest",', '"foo": "next",')


def _release_repo(tmp_path):
    root = tmp_path / "mono"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Dev One")
    git(root, "config", "user.email", "dev1@example.com")

    def pkg(name, version, deps=None):
        dep = "".join(f',\n  "dependencies": {{"{d}": "{v}"}}' for d, v in (deps or {}).items())
        return f'{{\n  "name": "{name}",\n  "version": "{version}"{dep}\n}}\n'

    for name in ("core", "browser", "node"):
        d = root / "packages" / name
        (d / "src").mkdir(parents=True)
        (d / "src" / "index.ts").write_text(f"export const {name} = 1;\n")
        deps = {} if name == "core" else {"@x/core": "1.0.0"}
        (d / "package.json").write_text(pkg(f"@x/{name}", "1.0.0", deps))
    (root / "CHANGELOG.md").write_text("# Changelog\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    (root / "packages" / "core" / "src" / "index.ts").write_text("export const core = 2;\n")
    git(root, "commit", "-q", "-am", "core: bump the constant")
    return root, pkg


def test_version_bumps_weigh_like_lockfile_bumps(tmp_path):
    root, pkg = _release_repo(tmp_path)
    # a release that bumps one package's version only
    (root / "packages" / "core" / "package.json").write_text(pkg("@x/core", "1.0.1"))
    git(root, "commit", "-q", "-am", "release: core 1.0.1")
    # a monorepo release: three package.json files, versions and internal pins, a changelog
    for name in ("core", "browser", "node"):
        deps = {} if name == "core" else {"@x/core": "1.1.0"}
        (root / "packages" / name / "package.json").write_text(pkg(f"@x/{name}", "1.1.0", deps))
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## 1.1.0\n\n- core bump\n")
    git(root, "commit", "-q", "-am", "release: 1.1.0")
    # a real dependency change
    (root / "packages" / "node" / "package.json").write_text(
        '{\n  "name": "@x/node",\n  "version": "1.1.0",\n  "dependencies": {"@x/core": '
        '"1.1.0"},\n  "devDependencies": {"express": "4.19.2"}\n}\n'
    )
    git(root, "commit", "-q", "-am", "node: add express")

    cs = load_commits(root)
    by = {c.subject: c for c in cs}
    bumps = version_bumps(root, commits=cs)
    assert bumps[by["release: 1.1.0"].sha] == {
        f"packages/{n}/package.json" for n in ("core", "browser", "node")
    }
    assert by["node: add express"].sha not in bumps
    assert version_bumps(root) == bumps  # the full pass and the restricted one agree
    w = compute_weights(root, cs, set())
    one, mono = w[by["release: core 1.0.1"].sha], w[by["release: 1.1.0"].sha]
    assert one.weight == mono.weight == VERSION_BUMP_WEIGHT
    assert "version bump only (3 files)" in mono.reasons
    dep = w[by["node: add express"].sha]
    assert dep.weight >= 0.4 and "dependencies changed" in dep.reasons
    # line share leaves the version lines out, as it does lockfiles: only the changelog counts
    mono_c = by["release: 1.1.0"]
    mono_c.version_files = bumps[mono_c.sha]
    assert set(mono_c.counted_lines) == {"CHANGELOG.md"}


def test_major_upgrade_commits_keep_their_weight(tmp_path):
    root = tmp_path / "deps"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Dev One")
    git(root, "config", "user.email", "dev1@example.com")

    def files(dep: str, mod: str) -> None:
        (root / "package.json").write_text(
            f'{{\n  "name": "app",\n  "version": "1.0.0",\n  "dependencies": '
            f'{{"left-pad": "{dep}"}}\n}}\n'
        )
        (root / "go.mod").write_text(f"module example.com/app\n\nrequire (\n\t{mod}\n)\n")

    files("1.4.2", "github.com/x/y/v2 v2.1.0")
    (root / "main.go").write_text("package main\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    files("1.5.0", "github.com/x/y/v2 v2.1.0")
    git(root, "commit", "-q", "-am", "left-pad 1.5.0")
    files("2.0.0", "github.com/x/y/v2 v2.1.0")
    git(root, "commit", "-q", "-am", "left-pad 2.0.0")
    files("2.0.0", "github.com/x/y/v3 v3.0.0")
    git(root, "commit", "-q", "-am", "y v3")
    cs = load_commits(root)
    by = {c.subject: c for c in cs}
    bumps = version_bumps(root, commits=cs)
    w = compute_weights(root, cs, set())
    minor = w[by["left-pad 1.5.0"].sha]
    assert bumps[by["left-pad 1.5.0"].sha] == {"package.json"}
    assert minor.weight == VERSION_BUMP_WEIGHT
    for subject in ("left-pad 2.0.0", "y v3"):
        assert by[subject].sha not in bumps
        assert "dependencies changed" in w[by[subject].sha].reasons
        assert w[by[subject].sha].weight >= 0.4


def test_toml_dependency_tables():
    """A version line inside a dependency's own table (`[dependencies.serde]`) is a pin."""
    diff = (
        "diff --git a/Cargo.toml b/Cargo.toml\n--- a/Cargo.toml\n+++ b/Cargo.toml\n"
        '@@ -3 +3 @@ [package]\n-version = "0.1.0"\n+version = "0.2.0"\n'
        '@@ -9 +9 @@ [dependencies.serde]\n-version = "{a}"\n+version = "{b}"\n'
    )
    assert parse_version_diff(diff.format(a="1.0.188", b="1.0.190")) == {"Cargo.toml": True}
    assert parse_version_diff(diff.format(a="1.0", b="2.0")) == {"Cargo.toml": False}
    # without the table name (no context) the line reads as the project's own version
    plain = diff.replace(" [dependencies.serde]", "").replace(" [package]", "")
    assert parse_version_diff(plain.format(a="1.0", b="2.0")) == {"Cargo.toml": True}
    # target-specific, dev, workspace and Poetry tables are dependency tables too
    for table in (
        "[target.'cfg(unix)'.dependencies.libc]",
        "[dev-dependencies.tokio]",
        "[workspace.dependencies.rand]",
    ):
        d = diff.replace("[dependencies.serde]", table)
        assert parse_version_diff(d.format(a="0.2", b="0.3")) == {"Cargo.toml": False}
    poetry = diff.replace("Cargo.toml", "pyproject.toml").replace(
        "[dependencies.serde]", "[tool.poetry.dependencies.requests]"
    )
    assert parse_version_diff(poetry.format(a="2.31", b="3.0")) == {"pyproject.toml": False}
    # a version line in any other table is still a version line
    other = diff.replace("[dependencies.serde]", "[workspace.package]")
    assert parse_version_diff(other.format(a="1.0", b="2.0")) == {"Cargo.toml": True}


def test_cargo_table_major_upgrade_weighs(tmp_path):
    root = tmp_path / "crate"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Dev One")
    git(root, "config", "user.email", "dev1@example.com")

    def cargo(own: str, serde: str) -> None:
        (root / "Cargo.toml").write_text(
            f'[package]\nname = "app"\nversion = "{own}"\n\n[dependencies]\nrand = "0.8"\n\n'
            f'[dependencies.serde]\nversion = "{serde}"\nfeatures = ["derive"]\n'
        )

    cargo("0.1.0", "1.0.188")
    (root / "src").mkdir()
    (root / "src" / "main.rs").write_text("fn main() {}\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    cargo("0.1.0", "1.0.190")
    git(root, "commit", "-q", "-am", "serde patch")
    cargo("0.2.0", "1.0.190")
    git(root, "commit", "-q", "-am", "release 0.2.0")
    cargo("0.2.0", "2.0.0")
    git(root, "commit", "-q", "-am", "serde 2")
    cs = load_commits(root)
    by = {c.subject: c for c in cs}
    bumps = version_bumps(root, commits=cs)
    w = compute_weights(root, cs, set())
    for subject in ("serde patch", "release 0.2.0"):
        assert bumps[by[subject].sha] == {"Cargo.toml"}
        assert w[by[subject].sha].weight == VERSION_BUMP_WEIGHT
    assert by["serde 2"].sha not in bumps
    assert "dependencies changed" in w[by["serde 2"].sha].reasons


def test_staged_version_bump(tmp_path):
    from udebt.risk import staged_commit

    root, pkg = _release_repo(tmp_path)
    (root / "packages" / "core" / "package.json").write_text(pkg("@x/core", "2.0.0"))
    git(root, "add", ".")
    c, w = staged_commit(root)
    assert c.version_files == {"packages/core/package.json"}
    assert w.weight == VERSION_BUMP_WEIGHT
