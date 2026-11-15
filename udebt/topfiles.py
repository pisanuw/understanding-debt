"""Which files stand for a directory when a unit names only directories (Phase 4b, the tree
fallback): the hosted app's question writer has no read tools, so a unit whose paths are all
directories, with no walkthrough and no file in its orientation, would be quizzed with no code.

The rule, the same here and in `web/src/core/topfiles.ts` (checked against
`web/test/fixtures/topfiles-parity.json`, which tests/test_hosted_topfiles_parity.py writes):

- **Never**: lockfiles, generated files and media (filekinds' mechanical kinds), symlinks and
  submodules, empty files and files over `MAX_BYTES` (the excerpt proxy reads none of these).
- **Tiers**: 0 source code (`structure.SOURCE_EXTS`), 1 anything else (config, data, styles,
  manifests), 2 docs, 3 tests and fixtures.
- **Order**: tier, then larger first, then path. Each directory gives its first `limit`; the
  unit takes them in turns (the first of each directory, then the second, ...), at most
  `limit` in all, so a unit over two directories shows both.

The worker records the result on each unit with a directory path as `files` (specs.json, no
contract change: specs are not a contract kind and fields are added without a version); the
web app uses `files` when present and lists the directory from GitHub at the pinned sha
otherwise (`/api/tree`).
"""

from __future__ import annotations

import posixpath
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

from .filekinds import DOCS, GENERATED, LOCK, MEDIA, TESTS
from .structure import SOURCE_EXTS

LIMIT = 4  # web/src/core/excerpts.ts refsForUnit's limit
MAX_BYTES = 2_000_000  # web/src/core/excerpts.ts MAX_FILE_BYTES
FIXTURES = re.compile(r"(^|/)(fixtures?|__fixtures__|__mocks__|mocks?|testdata|test-data)/")
# refsForUnit reads a path as a file when it has an extension and no trailing slash
FILE_SHAPED = re.compile(r"\.[A-Za-z0-9]{1,6}$")


def tier(path: str, size: int) -> int | None:
    """0 source, 1 other, 2 docs, 3 tests and fixtures; None for a file never shown."""
    if LOCK.search(path) or GENERATED.search(path) or MEDIA.search(path):
        return None
    if size <= 0 or size > MAX_BYTES:
        return None
    if TESTS.search(path) or FIXTURES.search(path):
        return 3
    if DOCS.search(path):
        return 2
    if posixpath.splitext(path)[1].lower() in SOURCE_EXTS:
        return 0
    return 1


def top_under(entries: Iterable[tuple[str, int]], directory: str, limit: int = LIMIT) -> list[str]:
    """The best `limit` files under `directory` (any depth), best first."""
    prefix = directory.strip("/") + "/"
    ranked = []
    for path, size in entries:
        if path.startswith(prefix):
            t = tier(path, size)
            if t is not None:
                ranked.append((t, -size, path))
    return [p for _, _, p in sorted(ranked)[:limit]]


def pick_across(lists: list[list[str]], limit: int = LIMIT) -> list[str]:
    """The first of each list, then the second of each, ..., without repeats, at most `limit`."""
    out: list[str] = []
    for i in range(max((len(x) for x in lists), default=0)):
        for xs in lists:
            if i < len(xs) and xs[i] not in out and len(out) < limit:
                out.append(xs[i])
    return out


def files_under(dirs: list[str], entries: list[tuple[str, int]], limit: int = LIMIT) -> list[str]:
    return pick_across([top_under(entries, d, limit) for d in dirs], limit)


def _safe(path: object) -> str | None:
    """web/src/core/excerpts.ts safePath: a repository path that cannot leave it, or None."""
    if not isinstance(path, str):
        return None
    p = path.strip().removeprefix("./")
    segs = p.split("/")
    if not p or len(p) > 400 or p.startswith("/") or "\\" in p:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in p) or any(s in ("", ".", "..") for s in segs):
        return None
    return None if segs[0] in (".git", ".udebt") else p


def _paths(unit: dict) -> list[object]:
    where = [w.get("path") for w in ((unit.get("orientation") or {}).get("where") or [])]
    return [*where, *(unit.get("paths") or [])]


def dir_paths(unit: dict, limit: int = LIMIT) -> list[str]:
    """The unit's orientation and paths that refsForUnit does not read as files, in order."""
    out: list[str] = []
    for p in _paths(unit):
        d = _safe(p.strip().rstrip("/")) if isinstance(p, str) else None
        if d and not (FILE_SHAPED.search(d) and _safe(p)) and d not in out:
            out.append(d)
    return out[:limit]


def needs_tree(unit: dict, walk: dict | None) -> bool:
    """True when the web app finds no file for the unit and has to list its directories: no
    walkthrough step, no file among its orientation and paths, no recorded `files`."""
    if any(_safe(st.get("file")) for st in ((walk or {}).get("steps") or [])):
        return False
    if any((f := _safe(p)) and FILE_SHAPED.search(f) for p in _paths(unit)):
        return False
    return not any(_safe(f) for f in unit.get("files") or []) and bool(dir_paths(unit))


def directories(entries: Iterable[tuple[str, int]]) -> set[str]:
    """Every directory that holds a tracked file, at any depth."""
    out: set[str] = set()
    for path, _ in entries:
        d = posixpath.dirname(path)
        while d and d not in out:
            out.add(d)
            d = posixpath.dirname(d)
    return out


def recorded_files(unit: dict, entries: list[tuple[str, int]], dirs: set[str]) -> list[str]:
    """What the worker records as the unit's `files`: the top files under those of its
    orientation's and own paths that are directories at the analysed commit (empty when none
    is)."""
    found: list[str] = []
    for p in _paths(unit):
        d = _safe(p.strip().rstrip("/")) if isinstance(p, str) else None
        if d and d in dirs and d not in found:
            found.append(d)
    return files_under(found[:LIMIT], entries) if found else []


def tracked_sizes(root: Path, rev: str = "HEAD") -> list[tuple[str, int]]:
    """(path, size in bytes) of every regular file in `rev`'s tree: `git ls-tree -r -l`, the
    tracked files with their sizes, symlinks and submodules left out."""
    out = subprocess.run(["git", "ls-tree", "-r", "-l", "-z", "--full-tree", rev], cwd=root,
                         capture_output=True, check=True).stdout  # fmt: skip
    entries = []
    for rec in out.split(b"\0"):
        meta, _, path = rec.partition(b"\t")
        parts = meta.split()
        if len(parts) == 4 and parts[1] == b"blob" and parts[0] != b"120000":
            entries.append((path.decode("utf-8", "surrogateescape"), int(parts[3])))
    return entries
