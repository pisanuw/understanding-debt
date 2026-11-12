"""Parsed-commit cache: what `git log` says about each commit, parsed once.

Every command used to re-parse `git log --numstat` over the whole history, and `weigh` ran
three more full passes of its own (effective lines, added files, renames), which on a
59,000-commit history took minutes each. The cache keeps, per sha:

    the record provenance.parse_log produces: parents, author date, name and email,
    subject, body and per-file changed lines (what load_commits builds a Commit from);
    the weighing inputs, each filled the first time the weights need it: effective lines
    (`eff`), files added (`add`), whether it renames a file (`ren`) and the files whose
    diff is a version bump (`ver`).

On load, `git rev-list HEAD` lists the reachable shas in log order. Only shas the cache
lacks are parsed (`git log --no-walk --stdin`, so nothing else is walked) and appended;
cached shas no longer reachable from HEAD (a rebase, an amend, a reset) are dropped and never
used. Classification (ai, assisted, human, bot) and directed work are not cached: they are
recomputed from the cached fields on every load, so a signature or config change applies
without a re-parse. The author name and email honour .mailmap, so a .mailmap change, or a
change to this file's format, rebuilds the cache.

The file is `.udebt/commits-parsed.json`, gzipped to `commits-parsed.json.gz` when the plain
file would pass GZIP_OVER bytes. It is derived data, rebuilt by any command, and on a large
history it runs to tens of megabytes, so it is gitignored (`.udebt/.gitignore`);
`commits.json`, which holds the weights and the paid-for LLM refinements, stays committed.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .history import rename_shas
from .provenance import Commit, Signature, classify_all, commit_from, parse_log
from .weights import WeighInputs, _effective, added_files, version_bumps

FORMAT = 1  # bump when the stored record changes; an older file is rebuilt
NAME = "commits-parsed.json"
GZIP_OVER = 4_000_000  # bytes of plain JSON above which the file is written gzipped
INPUT_KINDS = ("eff", "add", "ren", "ver")
GITIGNORE = "# derived by udebt, rebuilt on demand (`udebt cache status`)\n{name}\n{name}.gz\n"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8"
    )


def reachable(root: Path) -> list[str]:
    """Shas reachable from HEAD, newest first, in `git log` order. Empty without commits."""
    p = _git(root, "rev-list", "HEAD")
    return p.stdout.split() if p.returncode == 0 else []


def mailmap_key(root: Path) -> str:
    """A digest of what decides the author names and emails git reports: the repository's
    .mailmap and any mailmap.file / mailmap.blob setting (and the file it names)."""
    h = hashlib.sha1()
    p = root / ".mailmap"
    if p.is_file():
        h.update(p.read_bytes())
    cfg = _git(root, "config", "--get-regexp", r"^mailmap\.").stdout
    h.update(cfg.encode())
    for line in cfg.splitlines():
        key, _, value = line.partition(" ")
        f = Path(value).expanduser()
        if key == "mailmap.file" and not f.is_absolute():
            f = root / f
        if key == "mailmap.file" and f.is_file():
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


@dataclass
class Stats:
    """What the last load did, for `udebt cache status` and the tests."""

    rebuilt: str | None = None  # why the cache was started afresh, if it was
    parsed: int = 0  # shas parsed from git this load
    dropped: int = 0  # cached shas no longer reachable from HEAD
    inputs: dict[str, int] = field(default_factory=dict)  # weighing inputs computed, by kind
    written: bool = False


class ParsedCache:
    """The cache for one repository. `commits()` syncs it with HEAD and returns Commits,
    oldest first, exactly as provenance.load_commits would; `versions()`, `renames()` and
    `inputs()` return the weighing inputs for those commits, computing only what is
    missing. Each writes the file when it changed something."""

    def __init__(self, root: Path, udebt_dir: Path):
        self.root = root
        self.dir = udebt_dir
        self.plain = udebt_dir / NAME
        self.gz = udebt_dir / (NAME + ".gz")
        self.data: dict[str, Any] | None = None
        self.order: list[str] = []  # reachable shas, oldest first, as of the last sync
        self.stats = Stats()
        self._stamp: tuple[int, int] | None = None  # (mtime_ns, size) we last read or wrote

    # -- file ----------------------------------------------------------------------------

    @property
    def path(self) -> Path | None:
        """The file on disk, gzipped or not (the newer, should both exist)."""
        have = [p for p in (self.plain, self.gz) if p.exists()]
        return max(have, key=lambda p: p.stat().st_mtime_ns) if have else None

    def _read(self) -> dict[str, Any] | None:
        p = self.path
        if p is None:
            return None
        st = p.stat()
        if self.data is not None and self._stamp == (st.st_mtime_ns, st.st_size):
            return self.data  # this process wrote or read it last: no need to parse again
        try:
            raw = p.read_bytes()
            if p.suffix == ".gz":
                raw = gzip.decompress(raw)
            data = json.loads(raw)
        except (OSError, EOFError, ValueError):
            return None  # unreadable or half-written: start afresh
        self._stamp = (st.st_mtime_ns, st.st_size)
        return data if isinstance(data, dict) else None

    def _write(self) -> None:
        assert self.data is not None
        self.dir.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(self.data, separators=(",", ":"), ensure_ascii=False).encode()
        target, other = (self.gz, self.plain) if len(blob) > GZIP_OVER else (self.plain, self.gz)
        if target is self.gz:
            blob = gzip.compress(blob, compresslevel=6, mtime=0)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".commits-parsed-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
            os.chmod(tmp, 0o666 & ~_umask())  # mkstemp makes it 0600; match the other files
            os.replace(tmp, target)  # atomic: a reader never sees half a file
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        if other.exists():
            other.unlink()
        st = target.stat()
        self._stamp = (st.st_mtime_ns, st.st_size)
        self.stats.written = True
        ensure_gitignore(self.dir)

    def clear(self) -> None:
        for p in (self.plain, self.gz):
            p.unlink(missing_ok=True)
        self.data, self._stamp, self.order = None, None, []

    # -- commits ---------------------------------------------------------------------------

    def sync(self) -> list[str]:
        """Bring the cache up to date with HEAD: parse new shas, drop unreachable ones.
        Returns the reachable shas, oldest first."""
        self.stats = Stats()
        key = mailmap_key(self.root)
        data = self._read()
        if data is None:
            self.stats.rebuilt = "no cache" if self.path is None else "unreadable"
        elif data.get("format") != FORMAT:
            self.stats.rebuilt, data = f"format {data.get('format')} -> {FORMAT}", None
        elif data.get("mailmap") != key:
            self.stats.rebuilt, data = ".mailmap changed", None
        if data is None:
            data = {"format": FORMAT, "mailmap": key, "commits": {}}
        cached: dict[str, dict[str, Any]] = data["commits"]
        newest_first = reachable(self.root)
        live = set(newest_first)
        stale = [s for s in cached if s not in live]
        for s in stale:
            del cached[s]
        new = [s for s in newest_first if s not in cached]
        for raw in parse_log(self.root, new) if new else []:
            sha = raw.pop("sha")
            if sha in live:
                cached[sha] = raw
        self.stats.parsed, self.stats.dropped = len(new), len(stale)
        self.data = data
        self.order = newest_first[::-1]
        if new or stale or self.stats.rebuilt:
            data["head"] = newest_first[0] if newest_first else None
            self._write()
        return self.order

    def commits(
        self, sigs: list[Signature] | None = None, bots: list[Signature] | None = None
    ) -> list[Commit]:
        """Every commit reachable from HEAD, oldest first, classified afresh."""
        order = self.sync()
        cached = self.data["commits"] if self.data else {}
        found = [commit_from({"sha": s, **cached[s]}) for s in order if s in cached]
        return classify_all(found, sigs, bots)

    # -- weighing inputs ---------------------------------------------------------------

    def _ensure(self, kinds: Iterable[str], commits: list[Commit]) -> None:
        """Compute the weighing inputs of `kinds` that `commits` lack, one restricted git
        pass per kind, and write the file if anything was added."""
        if self.data is None:
            self.sync()
        cached = self.data["commits"]
        shas = [c.sha for c in commits if c.sha in cached]
        changed = False
        for kind in kinds:
            missing = [s for s in shas if kind not in cached[s]]
            if not missing:
                continue
            if kind == "eff":
                got = _effective(self.root, missing)
                for s in missing:
                    cached[s]["eff"] = list(got.get(s, (0, 0)))
            elif kind == "add":
                got = added_files(self.root, missing)
                for s in missing:
                    cached[s]["add"] = sorted(got.get(s, ()))
            elif kind == "ren":
                got = rename_shas(self.root, missing)
                for s in missing:
                    cached[s]["ren"] = int(s in got)
            elif kind == "ver":
                want = set(missing)
                got = version_bumps(self.root, missing, [c for c in commits if c.sha in want])
                for s in missing:
                    cached[s]["ver"] = sorted(got.get(s, ()))
            self.stats.inputs[kind] = self.stats.inputs.get(kind, 0) + len(missing)
            changed = True
        if changed:
            self._write()

    def versions(self, commits: list[Commit]) -> dict[str, set[str]]:
        self._ensure(["ver"], commits)
        cached = self.data["commits"]
        return {c.sha: set(v) for c in commits if (v := cached.get(c.sha, {}).get("ver"))}

    def renames(self, commits: list[Commit]) -> set[str]:
        self._ensure(["ren"], commits)
        cached = self.data["commits"]
        return {c.sha for c in commits if cached.get(c.sha, {}).get("ren")}

    def inputs(self, commits: list[Commit]) -> WeighInputs:
        self._ensure(INPUT_KINDS, commits)
        cached = self.data["commits"]
        eff, added, renames, versions = {}, {}, set(), {}
        for c in commits:
            e = cached.get(c.sha)
            if e is None:
                continue
            eff[c.sha] = (e["eff"][0], e["eff"][1])
            if e["add"]:
                added[c.sha] = set(e["add"])
            if e["ren"]:
                renames.add(c.sha)
            if e["ver"]:
                versions[c.sha] = set(e["ver"])
        return WeighInputs(eff, added, renames, versions)

    # -- status ----------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """What is on disk against HEAD, without changing anything."""
        p = self.path
        data = self._read() or {}
        cached = data.get("commits", {})
        live = reachable(self.root)
        live_set = set(live)
        return {
            "path": p,
            "bytes": p.stat().st_size if p else 0,
            "gzip": bool(p and p.suffix == ".gz"),
            "format": data.get("format"),
            "format_ok": data.get("format") == FORMAT,
            "mailmap_ok": data.get("mailmap") == mailmap_key(self.root),
            "cached": len(cached),
            "reachable": len(live),
            "new": sum(1 for s in live if s not in cached),
            "stale": sum(1 for s in cached if s not in live_set),
            "inputs": {k: sum(1 for e in cached.values() if k in e) for k in INPUT_KINDS},
            "head": data.get("head"),
        }


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


_OPEN: dict[Path, ParsedCache] = {}


def open_cache(root: Path, udebt_dir: Path) -> ParsedCache:
    """The cache for this repository, shared within one process so that a command which
    loads the commits and then weighs them reads the file once."""
    key = udebt_dir.resolve()
    pc = _OPEN.get(key)
    if pc is None or pc.root != root:
        pc = _OPEN[key] = ParsedCache(root, udebt_dir)
    return pc


def ensure_gitignore(udebt_dir: Path) -> None:
    """Keep the cache out of git: `.udebt/.gitignore` names it (created, or appended to)."""
    p = udebt_dir / ".gitignore"
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    if NAME in text.split():
        return
    sep = "\n" if text and not text.endswith("\n") else ""
    p.write_text(text + sep + GITIGNORE.format(name=NAME), encoding="utf-8")
