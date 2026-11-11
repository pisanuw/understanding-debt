"""Thin git helpers. Everything here shells out to git and returns plain data."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


class NotARepo(Exception):
    pass


def _git(root: Path | None, *args: str) -> str:
    cmd = ["git", *args]
    try:
        out = subprocess.run(
            cmd, cwd=root, check=True, capture_output=True, text=True, encoding="utf-8"
        )
    except FileNotFoundError as e:
        raise NotARepo("git is not installed") from e
    except subprocess.CalledProcessError as e:
        raise NotARepo(e.stderr.strip() or f"git {' '.join(args)} failed") from e
    return out.stdout


def find_root(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    return Path(_git(start, "rev-parse", "--show-toplevel").strip())


def head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def identity(root: Path) -> tuple[str, str]:
    """Return (name, email) of the current git user."""
    name = _git(root, "config", "user.name").strip() or "unknown"
    email = _git(root, "config", "user.email").strip() or "unknown@localhost"
    return name, email


def slug(email: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", email.lower()).strip("-") or "unknown"


def commit_count(root: Path, paths: list[str], since: str | None = None) -> int:
    """Number of commits touching any of `paths`, optionally only those after `since`."""
    if not paths:
        return 0
    rng = f"{since}..HEAD" if since else "HEAD"
    try:
        out = _git(root, "rev-list", "--count", rng, "--", *paths)
    except NotARepo:
        # `since` may no longer exist after a rebase; fall back to counting everything.
        out = _git(root, "rev-list", "--count", "HEAD", "--", *paths)
    return int(out.strip() or 0)


def tracked_files(root: Path) -> list[str]:
    return [p for p in _git(root, "ls-files").splitlines() if p]


def authors(root: Path) -> list[tuple[str, str]]:
    """Distinct (name, email) pairs in the history, most commits first."""
    out = _git(root, "log", "--format=%an\t%ae")
    counts: dict[tuple[str, str], int] = {}
    for line in out.splitlines():
        if "\t" in line:
            n, e = line.split("\t", 1)
            counts[(n, e)] = counts.get((n, e), 0) + 1
    return [k for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])]
