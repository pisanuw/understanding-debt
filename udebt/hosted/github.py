"""Public GitHub repositories only (owner decision 2026-11-15): the URL check, and two cheap
looks before a clone, both best effort: the repository's size, visibility and licence from
api.github.com (unauthenticated, 60 an hour per address; None when it does not answer), and
the commit a branch points at (`git ls-remote`, no clone)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

URL = re.compile(
    r"^https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100}?)"
    r"(?:\.git)?/?$"
)
SHA = re.compile(r"^[0-9a-f]{40}$")
REV = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


@dataclass
class Repo:
    owner: str
    name: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


@dataclass
class Info:
    private: bool
    size_kb: int
    license: str | None
    default_branch: str | None


def parse(url: str) -> Repo | None:
    m = URL.match(str(url).strip())
    if not m or m.group(2) in (".", ".."):
        return None
    return Repo(m.group(1), m.group(2))


def info(repo: Repo, timeout: float = 10) -> Info | None:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo.owner}/{repo.name}",
        headers={"accept": "application/vnd.github+json", "user-agent": "udebt-worker"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 - fixed host
            d = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:  # private, or no such repository: both are refused
            return Info(private=True, size_kb=0, license=None, default_branch=None)
        return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    lic = (d.get("license") or {}).get("spdx_id")
    return Info(
        private=bool(d.get("private")),
        size_kb=int(d.get("size") or 0),
        license=None if lic in (None, "NOASSERTION") else lic,
        default_branch=d.get("default_branch"),
    )


def resolve(url: str, rev: str | None, timeout: float = 60) -> str | None:
    """The full sha `rev` names on the remote (a full sha is taken as it is; default: HEAD), or
    None when the remote does not say (a short sha, a commit no ref points at)."""
    if rev and SHA.match(rev):
        return rev
    want = rev or "HEAD"
    try:
        out = subprocess.run(
            ["git", "ls-remote", url, want],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            check=False,
        ).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if SHA.match(sha) and ref in (want, f"refs/heads/{want}", f"refs/tags/{want}"):
            return sha
    return None
