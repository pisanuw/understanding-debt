"""Code excerpts for walkthroughs (`udebt dig` and `udebt serve`): lines of a tracked file
from the working tree, numbered, at most MAX_LINES. Only files `git ls-files` lists are read:
no absolute paths, no `..`, nothing untracked or ignored (an `.env`, say)."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path

from . import repo

MAX_LINES = 25
MAX_BYTES = 2_000_000


class ExcerptError(Exception):
    pass


@dataclass
class Excerpt:
    file: str
    start: int
    end: int
    total: int  # lines in the file
    lines: list[str]
    cut: int = 0  # lines of the asked range left out by the cap

    def numbered(self, indent: str = "    ") -> str:
        w = len(str(self.end))
        body = [f"{indent}{self.start + i:>{w}} | {ln}" for i, ln in enumerate(self.lines)]
        if self.cut:
            body.append(f"{indent}{'':>{w}} | ... ({self.cut} more line(s) in the range)")
        return "\n".join(body)

    def as_dict(self) -> dict:
        return {
            "file": self.file,
            "start": self.start,
            "end": self.end,
            "total": self.total,
            "lines": self.lines,
            "cut": self.cut,
        }


def clean(path: str) -> str:
    p = str(path).strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    norm = posixpath.normpath(p) if p else ""
    if not norm or norm.startswith(("/", "../")) or norm == ".." or "\0" in norm:
        raise ExcerptError(f"not a repository path: {path!r}")
    return norm


def read(
    root: Path,
    file: str,
    start: int,
    end: int,
    *,
    tracked: set[str] | None = None,
    cap: int = MAX_LINES,
) -> Excerpt:
    """Lines `start`..`end` (1-based, inclusive) of a tracked `file`, clamped to the file and
    capped at `cap` lines."""
    f = clean(file)
    tracked = tracked if tracked is not None else set(repo.tracked_files(root))
    if f not in tracked:
        raise ExcerptError(f"{f} is not a tracked file")
    p = root / f
    try:
        if p.stat().st_size > MAX_BYTES:
            raise ExcerptError(f"{f} is too large to excerpt")
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise ExcerptError(f"{f} cannot be read: {e}") from e
    lines = text.splitlines()
    total = len(lines)
    s = max(1, min(int(start), max(1, total)))
    e = max(s, min(int(end), total))
    cut = max(0, e - s + 1 - cap)
    e_shown = e - cut
    return Excerpt(f, s, e_shown, total, lines[s - 1 : e_shown], cut)
