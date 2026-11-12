"""Unit size in lines: what the share-changed decay divides by.

The share of a unit that changed since some evidence is the weighted changed lines landed
under the unit's paths since then, divided by the unit's size at the cut-off (HEAD for
`status`): the lines of the tracked files under its paths, counted as line share counts
them (no lockfiles, generated or media files, no binaries), a file several units list split
among them as everywhere else (units.Membership).

Counting needs each file's content. `git ls-tree -r <rev>` gives every tracked file's blob
sha, and `git cat-file --batch` reads the blobs this repository has not counted before.
Counts are kept per blob in `.udebt/sizes.json`, so a new HEAD, a new map or a replay over
past snapshots only reads the blobs that changed. The file is derived and gitignored
(`.udebt/.gitignore`), like the parsed-commit cache.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import units as hier
from .filekinds import mechanical

NAME = "sizes.json"
VERSION = 1
BINARY_PROBE = 8000  # bytes read for a NUL, as git decides a file is binary


def _git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=root, input=data, check=True, capture_output=True
    ).stdout


def count_lines(blob: bytes) -> int:
    """Lines in a file's content; 0 for a binary file (a NUL near the start)."""
    if not blob or b"\0" in blob[:BINARY_PROBE]:
        return 0
    return blob.count(b"\n") + (0 if blob.endswith(b"\n") else 1)


def parse_batch(out: bytes) -> dict[str, int]:
    """blob sha -> lines, from `git cat-file --batch` output."""
    counts: dict[str, int] = {}
    i = 0
    while i < len(out):
        nl = out.index(b"\n", i)
        header = out[i:nl].split()
        i = nl + 1
        if len(header) < 3 or header[1] == b"missing":
            continue
        size = int(header[2])
        counts[header[0].decode()] = count_lines(out[i : i + size])
        i += size + 1  # the content and its trailing newline
    return counts


class LineCounts:
    """Line counts of the tracked files at any revision, cached per blob."""

    def __init__(self, root: Path, udebt_dir: Path):
        self.root = root
        self.path = udebt_dir / NAME
        self.blobs: dict[str, int] = {}
        self._loaded = False
        self._at: dict[str, dict[str, int]] = {}  # rev -> path -> lines, this process
        self.counted = 0  # blobs read from git this process

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict) and data.get("version") == VERSION:
            self.blobs = {k: int(v) for k, v in data.get("blobs", {}).items()}

    def _save(self) -> None:
        from .cache import ensure_gitignore

        self.path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps({"version": VERSION, "blobs": self.blobs}, separators=(",", ":"))
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".sizes-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(blob)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        ensure_gitignore(self.path.parent)

    def at(self, rev: str) -> dict[str, int]:
        """path -> lines for every tracked file at `rev` that line share counts (lockfiles,
        generated and media files are left out; binaries count 0)."""
        if rev in self._at:
            return self._at[rev]
        self._load()
        out = _git(self.root, "ls-tree", "-r", "-z", "--full-tree", rev)
        files: dict[str, str] = {}
        for entry in out.split(b"\0"):
            if not entry:
                continue
            meta, _, path_b = entry.partition(b"\t")
            parts = meta.split()
            if len(parts) != 3 or parts[1] != b"blob":
                continue  # submodules (commit) and anything else that is not a file
            path = path_b.decode("utf-8", errors="replace")
            if not mechanical(path):
                files[path] = parts[2].decode()
        missing = sorted({s for s in files.values() if s not in self.blobs})
        if missing:
            got = parse_batch(
                _git(self.root, "cat-file", "--batch", data=("\n".join(missing) + "\n").encode())
            )
            for s in missing:
                self.blobs[s] = got.get(s, 0)
            self.counted += len(missing)
            self._save()
        self._at[rev] = {p: self.blobs[s] for p, s in files.items()}
        return self._at[rev]


_OPEN: dict[Path, LineCounts] = {}


def open_counts(root: Path, udebt_dir: Path) -> LineCounts:
    """The line counts for this repository, shared within one process."""
    key = udebt_dir.resolve()
    lc = _OPEN.get(key)
    if lc is None or lc.root != root:
        lc = _OPEN[key] = LineCounts(root, udebt_dir)
    return lc


class _Parts:
    """Which units a file falls under, and the part of its lines each gets, per file and
    memoized: the units are found through the file's path prefixes (a unit path is a file or
    a directory), so a replay over many trees does the work once per distinct file."""

    def __init__(self, units: list[dict[str, Any]]):
        self.units = units  # kept alive, so the id in _PARTS stays this list's
        self.members = hier.Membership(units)
        self.order = [u["id"] for u, _parent in hier.walk(units)]
        self.by_path: dict[str, list[str]] = {}
        for u, _parent in hier.walk(units):
            for path in u.get("paths", []):
                path = path.rstrip("/")
                if path:
                    self.by_path.setdefault(path, []).append(u["id"])
        self.memo: dict[str, list[tuple[str, float]]] = {}

    def of(self, f: str) -> list[tuple[str, float]]:
        got = self.memo.get(f)
        if got is None:
            segs = f.split("/")
            ids: dict[str, None] = {}
            for i in range(1, len(segs) + 1):
                for uid in self.by_path.get("/".join(segs[:i]), ()):
                    ids[uid] = None
            got = self.memo[f] = [(uid, self.members.split(uid)(f)) for uid in ids]
        return got


_PARTS: dict[int, _Parts] = {}


def unit_sizes(units: list[dict[str, Any]], lines: dict[str, int]) -> dict[str, float]:
    """unit id -> its size in lines: the lines of the files under its paths, a file several
    units list split among them (units.Membership), for every unit of the hierarchy."""
    parts = _PARTS.get(id(units))
    if parts is None or parts.units is not units:
        parts = _PARTS[id(units)] = _Parts(units)
    out = dict.fromkeys(parts.order, 0.0)
    for f, n in lines.items():
        for uid, part in parts.of(f):
            out[uid] += n * part
    return {uid: round(v, 1) for uid, v in out.items()}


def share(lines_since: float, size: float) -> float:
    """The share of a unit that changed: weighted changed lines over its size in lines. A
    unit with no lines left counts its changed lines over one line, so any change to it reads
    as a full rewrite. Can pass 1 when the code was rewritten more than once."""
    return round(max(0.0, lines_since) / max(1.0, size), 4)
