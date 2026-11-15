"""The tree fallback's preference rule exists twice: udebt/topfiles.py (the worker records a
unit's `files` with it) and web/src/core/topfiles.ts (the app picks files from GitHub's tree
with it for analyses made before `files`). This test writes a shared list of paths and
listings with the Python answers to web/test/fixtures/topfiles-parity.json, which vitest
checks the TypeScript against, and fails when the committed file no longer matches
(regenerate with UDEBT_WRITE_PARITY=1 pytest tests/test_hosted_topfiles_parity.py). It also
tests the rule itself."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from udebt import filekinds, topfiles
from udebt.structure import SOURCE_EXTS

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "web" / "test" / "fixtures" / "topfiles-parity.json"
KB = 1000

# Every branch of the rule: source in several languages, other files, docs, tests and fixtures,
# the mechanical kinds, the size limits, and a few edges (case, no extension, unicode).
PATHS: list[tuple[str, int]] = [
    ("mlflow/genai/scorers/base.py", 30 * KB),
    ("src/app.tsx", 9 * KB),
    ("pkg/x.go", 4 * KB),
    ("lib/a.rb", 3 * KB),
    ("web/App.vue", 2 * KB),
    ("Main.java", 2 * KB),
    ("scripts/run.sh", 1 * KB),
    ("setup.py", 1 * KB),
    ("a/b.pyi", 1 * KB),
    ("a/B.PY", 1 * KB),
    ("pkg/naïve.py", 1 * KB),
    ("pkg/config.yaml", 5 * KB),
    ("pkg/data.json", 50 * KB),
    ("web/styles.css", 8 * KB),
    ("web/index.html", 2 * KB),
    ("pyproject.toml", 1 * KB),
    ("Dockerfile", 1 * KB),
    ("Makefile", 1 * KB),
    ("pkg/.bashrc", 1 * KB),
    ("pkg/.hidden.py", 1 * KB),
    ("pkg/archive.tar.gz", 90 * KB),
    ("pkg/model.pkl", 90 * KB),
    ("LOGO.PNG", 9 * KB),
    ("docs/index.py", 3 * KB),
    ("pkg/README.md", 6 * KB),
    ("pkg/notes.txt", 1 * KB),
    ("LICENSE", 11 * KB),
    ("CHANGELOG.rst", 20 * KB),
    ("doc/guide.adoc", 2 * KB),
    ("pkg/README", 1 * KB),
    ("tests/test_a.py", 7 * KB),
    ("pkg/test_b.py", 7 * KB),
    ("src/a.test.ts", 7 * KB),
    ("src/a.spec.tsx", 7 * KB),
    ("e2e/flow.ts", 7 * KB),
    ("src/__tests__/x.js", 7 * KB),
    ("spec/models/user_spec.rb", 7 * KB),
    ("tests/README.md", 7 * KB),
    ("pkg/fixtures/data.json", 7 * KB),
    ("pkg/fixture/x.py", 7 * KB),
    ("pkg/__fixtures__/x.py", 7 * KB),
    ("pkg/__mocks__/api.ts", 7 * KB),
    ("pkg/mocks/m.go", 7 * KB),
    ("pkg/testdata/in.txt", 7 * KB),
    ("pkg/test-data/x.csv", 7 * KB),
    ("package-lock.json", 900 * KB),
    ("pkg/yarn.lock", 900 * KB),
    ("Cargo.lock", 90 * KB),
    ("go.sum", 90 * KB),
    ("dist/bundle.js", 900 * KB),
    ("build/x.py", 9 * KB),
    ("node_modules/a/index.js", 9 * KB),
    ("pkg/__pycache__/a.pyc", 9 * KB),
    ("app.min.js", 90 * KB),
    ("a.js.map", 90 * KB),
    ("x.generated.ts", 90 * KB),
    ("__snapshots__/a.snap", 9 * KB),
    ("logo.png", 90 * KB),
    ("icon.svg", 9 * KB),
    ("font.woff2", 90 * KB),
    ("paper.pdf", 90 * KB),
    ("pkg/__init__.py", 0),
    ("pkg/big.py", 2_000_001),
    ("pkg/edge.py", 2_000_000),
]

# A real listing: digitalme's frontend/ at f93102b (`git ls-tree -r -l`), the unit the
# Playwright walkthrough quizzes through the tree fallback.
FRONTEND = [
    ("frontend/.env.asc", 1069),
    ("frontend/.env.example", 296),
    ("frontend/.gitignore", 253),
    ("frontend/README.md", 1027),
    ("frontend/eslint.config.js", 568),
    ("frontend/index.html", 1505),
    ("frontend/jsconfig.json", 720),
    ("frontend/netlify.toml", 1794),
    ("frontend/package-lock.json", 131516),
    ("frontend/package.json", 733),
    ("frontend/public/apple-touch-icon.png", 61462),
    ("frontend/public/icons.svg", 5031),
    ("frontend/public/icons/favicon-32.png", 2686),
    ("frontend/public/icons/ypai-192.png", 68457),
    ("frontend/public/icons/ypai-512.png", 367705),
    ("frontend/public/icons/ypai-avatar.jpg", 10863),
    ("frontend/public/manifest.webmanifest", 632),
    ("frontend/src/AdminPage.jsx", 114432),
    ("frontend/src/App.css", 24353),
    ("frontend/src/App.jsx", 39266),
    ("frontend/src/Linkified.jsx", 603),
    ("frontend/src/SharedConversation.jsx", 3571),
    ("frontend/src/WarmthMeter.jsx", 4503),
    ("frontend/src/assets/hero.png", 13057),
    ("frontend/src/assets/react.svg", 4126),
    ("frontend/src/assets/vite.svg", 8709),
    ("frontend/src/auth.js", 472),
    ("frontend/src/globals.d.ts", 843),
    ("frontend/src/index.css", 31),
    ("frontend/src/linkify.js", 1610),
    ("frontend/src/linkify.test.js", 2043),
    ("frontend/src/main.jsx", 605),
    ("frontend/src/sse.js", 1002),
    ("frontend/src/sse.test.js", 1585),
    ("frontend/vite.config.js", 456),
]

TREE = [
    ("a/one.py", 500),
    ("a/two.py", 900),
    ("a/three.py", 900),
    ("a/sub/four.py", 4000),
    ("a/sub/five.ts", 100),
    ("a/README.md", 9000),
    ("a/tests/test_one.py", 9000),
    ("b/x.js", 10),
    ("b/y.json", 99999),
    ("pkg2/z.py", 5000),
    ("pkgx.py", 5000),
    ("pkg/z.py", 10),
    ("only/test_a.py", 300),
    ("only/README.md", 200),
    ("only/lock/yarn.lock", 5000),
    ("media/logo.png", 4000),
    ("media/doc.pdf", 4000),
]

SELECTIONS = [
    ("one directory: source first, larger first", ["a"], TREE),
    ("two directories in turns", ["a", "b"], TREE),
    ("a trailing slash and overlapping directories", ["a/", "a/sub"], TREE),
    ("ties by path", ["a"], [("a/b.py", 5), ("a/a.py", 5), ("a/c.py", 5)]),
    ("only tests and docs", ["only"], TREE),
    ("nothing to show", ["media"], TREE),
    ("a prefix is not a directory", ["pkg"], TREE),
    ("a directory that holds nothing", ["nowhere"], TREE),
    ("a real frontend", ["frontend"], FRONTEND),
    ("more directories than the limit", ["a", "b", "pkg", "pkg2", "only"], TREE),
]

UNITS: list[tuple[str, dict, dict | None]] = [
    ("two directories (mlflow registry-store)",
     {"paths": ["mlflow/store/model_registry", "mlflow/entities/model_registry"]}, None),
    ("a directory and a file", {"paths": ["src/", "src/a.ts"]}, None),
    ("files recorded by the worker",
     {"paths": ["frontend"], "files": ["frontend/src/App.jsx"]}, None),
    ("unsafe and repeated paths",
     {"paths": ["./pkg", "pkg", "foo.d/", "../x", ".git/hooks", "Dockerfile", 7]}, None),
    ("a walkthrough", {"paths": ["src"]}, {"steps": [{"file": "src/a.py", "start_line": 1,
                                                       "end_line": 9}]}),
    ("an orientation that names a file",
     {"paths": ["src"], "orientation": {"where": [{"path": "src/main.py"}]}}, None),
    ("an orientation that names a directory",
     {"paths": ["src"], "orientation": {"where": [{"path": "lib"}]}}, None),
    ("more directories than the limit", {"paths": ["a", "b", "c", "d", "e"]}, None),
    ("no paths", {"paths": []}, None),
    ("unsafe files only", {"paths": ["web"], "files": ["../etc/passwd"]}, None),
]  # fmt: skip


def parity() -> dict:
    return {
        "limit": topfiles.LIMIT,
        "max_bytes": topfiles.MAX_BYTES,
        "source_exts": sorted(SOURCE_EXTS),
        "patterns": {
            "lock": filekinds.LOCK.pattern,
            "generated": filekinds.GENERATED.pattern,
            "media": filekinds.MEDIA.pattern,
            "docs": filekinds.DOCS.pattern,
            "tests": filekinds.TESTS.pattern,
            "fixtures": topfiles.FIXTURES.pattern,
        },
        "tiers": [[p, s, topfiles.tier(p, s)] for p, s in PATHS],
        "selections": [
            {
                "name": n,
                "dirs": d,
                "entries": [[p, s] for p, s in e],
                "files": topfiles.files_under(d, e),
            }
            for n, d, e in SELECTIONS
        ],  # fmt: skip
        "units": [
            {
                "name": n,
                "unit": {"id": "u", "name": "u", "parent": None, **u},
                "walk": w,
                "dir_paths": topfiles.dir_paths(u),
                "needs_tree": topfiles.needs_tree(u, w),
            }
            for n, u, w in UNITS
        ],  # fmt: skip
    }


def _dump(data: dict) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def test_topfiles_parity_fixture_is_current():
    want = _dump(parity())
    if os.environ.get("UDEBT_WRITE_PARITY") == "1" or not FIXTURE.exists():
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(want, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == want, (
        "web/test/fixtures/topfiles-parity.json is stale: UDEBT_WRITE_PARITY=1 pytest "
        "tests/test_hosted_topfiles_parity.py"
    )


def test_the_cases_cover_every_tier():
    tiers = {topfiles.tier(p, s) for p, s in PATHS}
    assert tiers == {None, 0, 1, 2, 3}
    picked = {n: topfiles.files_under(d, e) for n, d, e in SELECTIONS}
    assert picked["one directory: source first, larger first"] == [
        "a/sub/four.py", "a/three.py", "a/two.py", "a/one.py"]  # fmt: skip
    assert picked["two directories in turns"] == ["a/sub/four.py", "b/x.js", "a/three.py",
                                                  "b/y.json"]  # fmt: skip
    assert picked["only tests and docs"] == ["only/README.md", "only/test_a.py"]
    assert picked["nothing to show"] == [] and picked["a directory that holds nothing"] == []
    assert picked["a prefix is not a directory"] == ["pkg/z.py"]
    assert picked["a real frontend"] == [
        "frontend/src/AdminPage.jsx",
        "frontend/src/App.jsx",
        "frontend/src/WarmthMeter.jsx",
        "frontend/src/SharedConversation.jsx",
    ]
    assert len(picked["more directories than the limit"]) == topfiles.LIMIT


def test_tracked_sizes_and_directories_read_the_tree(tmp_path):
    def git(*a: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "a.py").write_text("x = 1\n")
    (tmp_path / "pkg" / "sub" / "b c.py").write_text("y = 2\n" * 10)
    (tmp_path / "pkg" / "link.py").symlink_to("a.py")
    (tmp_path / "untracked.py").write_text("z\n")
    git("add", "pkg")
    git("-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "x")
    entries = topfiles.tracked_sizes(tmp_path)
    assert sorted(entries) == [("pkg/a.py", 6), ("pkg/sub/b c.py", 60)]
    assert topfiles.directories(entries) == {"pkg", "pkg/sub"}
    unit = {
        "paths": ["pkg/", "pkg/a.py", "missing"],
        "orientation": {"where": [{"path": "pkg/sub"}]},
    }
    assert topfiles.recorded_files(unit, entries, topfiles.directories(entries)) == [
        "pkg/sub/b c.py", "pkg/a.py"]  # fmt: skip
    assert topfiles.recorded_files({"paths": ["pkg/a.py"]}, entries, {"pkg"}) == []
