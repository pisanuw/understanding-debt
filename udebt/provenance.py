"""Who wrote each commit: an AI agent, a human with an AI co-author, or a human alone.

Signatures are data, not code, so new agents can be added in .udebt/config.json under
"ai_signatures" without touching this file. Each signature has:

    name             short agent id
    author_emails    regexes matched against the commit author email  -> class "ai"
    author_names     regexes matched against the commit author name   -> class "ai"
    trailer_patterns regexes matched against the commit body          -> class "assisted"

A commit authored by the agent itself is "ai". A human-authored commit carrying the agent's
trailer or footer is "assisted". Anything else is "human".
"""

from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from .filekinds import mechanical

BUILTIN_SIGNATURES: list[dict[str, Any]] = [
    {
        "name": "claude-code",
        "author_emails": [r"^noreply@anthropic\.com$"],
        "author_names": [r"^Claude$", r"^Claude Code$"],
        "trailer_patterns": [
            r"(?im)^Co-Authored-By:\s*Claude\b",
            r"(?im)^Claude-Session:",
            r"(?i)Generated with \[?Claude Code",
        ],
    },
    {
        "name": "github-copilot",
        "author_emails": [r"copilot.*@users\.noreply\.github\.com$"],
        "author_names": [r"^(GitHub )?Copilot$", r"^copilot-swe-agent"],
        "trailer_patterns": [r"(?im)^Co-Authored-By:.*Copilot\b"],
    },
    {
        "name": "cursor",
        "author_emails": [r"@cursor\.(sh|com)$"],
        "author_names": [r"^Cursor( Agent)?$"],
        "trailer_patterns": [r"(?im)^Co-Authored-By:.*Cursor\b"],
    },
    {
        "name": "aider",
        "author_emails": [r"\(aider\)"],
        "author_names": [r"\(aider\)$"],
        "trailer_patterns": [r"(?im)^Co-Authored-By:.*aider\b", r"(?im)^aider:"],
    },
    {
        "name": "openai-codex",
        "author_emails": [r"codex.*@openai\.com$"],
        "author_names": [r"^Codex$", r"^OpenAI Codex$"],
        "trailer_patterns": [r"(?im)^Co-Authored-By:.*Codex\b"],
    },
    {
        "name": "gemini",
        "author_names": [r"^Gemini( CLI| Code Assist)?$"],
        "trailer_patterns": [r"(?im)^Co-Authored-By:.*Gemini\b"],
    },
]


@dataclass
class Signature:
    name: str
    author_emails: list[re.Pattern]
    author_names: list[re.Pattern]
    trailer_patterns: list[re.Pattern]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Signature:
        return cls(
            name=d["name"],
            author_emails=[re.compile(p) for p in d.get("author_emails", [])],
            author_names=[re.compile(p) for p in d.get("author_names", [])],
            trailer_patterns=[re.compile(p) for p in d.get("trailer_patterns", [])],
        )


def load_signatures(extra: list[dict[str, Any]] | None = None) -> list[Signature]:
    return [Signature.from_dict(d) for d in [*BUILTIN_SIGNATURES, *(extra or [])]]


@dataclass
class Commit:
    sha: str
    date: str  # ISO 8601 author date
    author_name: str
    author_email: str
    subject: str
    body: str
    files: list[str] = field(default_factory=list)
    insertions: int = 0
    deletions: int = 0
    klass: str = "human"  # ai | assisted | human
    agent: str | None = None
    file_lines: dict[str, int] = field(default_factory=dict)  # path -> insertions + deletions

    @property
    def short(self) -> str:
        return self.sha[:7]

    def touches(self, paths: list[str]) -> bool:
        return any(_under(f, paths) for f in self.files)

    @cached_property
    def counted_lines(self) -> dict[str, int]:
        """Changed lines per file, without lockfiles and generated files: a 2000-line
        package-lock.json says nothing about where a refactor's thinking went."""
        return {f: n for f, n in self.file_lines.items() if not mechanical(f)}

    def share(self, paths: list[str], exclude: list[str] | None = None) -> float:
        """Fraction of this commit's changed lines that fall under `paths` (0..1), and not
        under `exclude`, not counting lockfiles and generated files. Falls back to the
        fraction of files when line counts are unavailable (binary or purely mechanical)."""
        if not self.files:
            return 0.0

        def inside(f: str) -> bool:
            return _under(f, paths) and not (exclude and _under(f, exclude))

        lines = self.counted_lines
        total = sum(lines.values())
        if total > 0:
            return min(1.0, sum(n for f, n in lines.items() if inside(f)) / total)
        return sum(1 for f in self.files if inside(f)) / len(self.files)

    def attribution(self, paths: list[str], exclude: list[str] | None = None) -> float:
        """How much of this commit's weight lands on `paths`: the square root of the line
        share. Concave on purpose: a unit that received a tenth of a large architectural
        commit carries about a third of its debt, because the commit's nature (a new
        dependency, a changed model) spills past the lines it touched there. A commit
        confined to one unit lands its full weight."""
        return math.sqrt(self.share(paths, exclude))


def _under(f: str, paths: list[str]) -> bool:
    for p in paths:
        p = p.rstrip("/")
        if f == p or f.startswith(p + "/"):
            return True
    return False


def classify(c: Commit, sigs: list[Signature]) -> tuple[str, str | None]:
    for s in sigs:
        if any(r.search(c.author_email) for r in s.author_emails) or any(
            r.search(c.author_name) for r in s.author_names
        ):
            return "ai", s.name
    for s in sigs:
        if any(r.search(c.body) for r in s.trailer_patterns):
            return "assisted", s.name
    return "human", None


_NUMSTAT = re.compile(r"^(\d+|-)\t(\d+|-)\t(.+)$")


def load_commits(
    root: Path, sigs: list[Signature] | None = None, limit: int | None = None
) -> list[Commit]:
    """All commits reachable from HEAD, oldest first, with changed files and classification."""
    sigs = sigs or load_signatures()
    fmt = "%x1e%H%x1f%aI%x1f%aN%x1f%aE%x1f%s%x1f%b"  # %aN/%aE honour .mailmap
    cmd = ["git", "log", f"--format={fmt}", "--numstat", "--no-renames"]
    if limit:
        cmd.append(f"-n{limit}")
    out = subprocess.run(
        cmd,
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    commits: list[Commit] = []
    for rec in out.split("\x1e"):
        if not rec.strip():
            continue
        parts = rec.split("\x1f", 5)
        if len(parts) < 6:
            continue
        sha, date, an, ae, subject, rest = parts
        body_lines, files, ins, dels, per_file = [], [], 0, 0, {}
        for line in rest.splitlines():
            m = _NUMSTAT.match(line)
            if m:
                files.append(m.group(3))
                a = int(m.group(1)) if m.group(1).isdigit() else 0
                d = int(m.group(2)) if m.group(2).isdigit() else 0
                ins += a
                dels += d
                per_file[m.group(3)] = a + d
            else:
                body_lines.append(line)
        c = Commit(
            sha=sha.strip(),
            date=date,
            author_name=an,
            author_email=ae,
            subject=subject,
            body="\n".join(body_lines).strip(),
            files=files,
            insertions=ins,
            deletions=dels,
            file_lines=per_file,
        )
        c.klass, c.agent = classify(c, sigs)
        commits.append(c)
    commits.reverse()
    return commits


def _emails(dev: str | set[str] | None) -> set[str]:
    if not dev:
        return set()
    return {dev.lower()} if isinstance(dev, str) else {e.lower() for e in dev}


def summarize(commits: list[Commit], dev: str | set[str] | None = None) -> dict[str, Any]:
    mine = _emails(dev)
    out = {"total": len(commits), "ai": 0, "assisted": 0, "human": 0, "agents": {}, "dev": 0}
    for c in commits:
        out[c.klass] += 1
        if c.agent:
            out["agents"][c.agent] = out["agents"].get(c.agent, 0) + 1
        if c.author_email.lower() in mine:
            out["dev"] += 1
    return out


@dataclass
class UnitProvenance:
    """Counts and weighted mass of the commits touching a unit. The `*_w` fields sum commit
    weights (see weights.py); the plain fields count commits."""

    total: int = 0
    ai: int = 0
    assisted: int = 0
    human: int = 0
    dev_human: int = 0
    dev_assisted: int = 0
    others_since_dev: int = 0  # commits by anyone else after the developer's last own commit
    total_w: float = 0.0
    ai_w: float = 0.0
    assisted_w: float = 0.0
    dev_w: float = 0.0  # own human commits at full weight, own assisted at half
    others_since_dev_w: float = 0.0
    first_date: str | None = None
    last_date: str | None = None

    @property
    def ai_share(self) -> float:
        return (self.ai_w + 0.5 * self.assisted_w) / self.total_w if self.total_w else 0.0

    @property
    def dev_share(self) -> float:
        """Weighted share of the unit the developer wrote; AI-assisted commits count half."""
        return self.dev_w / self.total_w if self.total_w else 0.0


def unit_provenance(
    commits: list[Commit],
    paths: list[str],
    dev: str | set[str] | None,
    weights: dict[str, Any] | None = None,
) -> UnitProvenance:
    """Provenance of the commits touching `paths`. `dev` is the developer's email, or the set
    of all addresses they have committed under. `weights` maps sha -> CommitWeight; without
    it every commit weighs 1."""
    p = UnitProvenance()
    mine = _emails(dev)
    since, since_w = 0, 0.0
    for c in commits:  # oldest first
        if not c.touches(paths):
            continue
        w = weights[c.sha].weight if weights and c.sha in weights else 1.0
        w *= c.attribution(paths)  # the part of the commit that landed in this unit
        p.total += 1
        p.total_w += w
        setattr(p, c.klass, getattr(p, c.klass) + 1)
        if c.klass == "ai":
            p.ai_w += w
        elif c.klass == "assisted":
            p.assisted_w += w
        p.first_date = p.first_date or c.date
        p.last_date = c.date
        if c.author_email.lower() in mine and c.klass != "ai":
            if c.klass == "human":
                p.dev_human += 1
                p.dev_w += w
            else:
                p.dev_assisted += 1
                p.dev_w += 0.5 * w
            since, since_w = 0, 0.0
        else:
            since += 1
            since_w += w
    has_own = bool(p.dev_human or p.dev_assisted)
    p.others_since_dev = since if has_own else p.total
    p.others_since_dev_w = round(since_w if has_own else p.total_w, 3)
    p.total_w, p.ai_w, p.assisted_w, p.dev_w = (
        round(p.total_w, 3),
        round(p.ai_w, 3),
        round(p.assisted_w, 3),
        round(p.dev_w, 3),
    )
    return p
