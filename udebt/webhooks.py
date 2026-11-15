"""GitHub webhooks for the pull-request check (Phase 4a): signature verification and routing,
as pure functions with no web framework and no database. Whatever receives the delivery (a
platform function, the worker) verifies it, asks `route` what to do and does it: queue an
analysis, queue a check, record an acknowledgement.

    event          action                                   result
    ping           -                                        pong
    push           to the default branch                    analyze {sha}
    pull_request   opened, synchronize, reopened,           check {pr, head_sha, base_sha,
                   ready_for_review                         author}
                   labeled with the acknowledgement label   ack (when the sender is a lead or
                   (`udebt-acknowledged`)                   owner), then check again
    check_suite    requested, rerequested                   check per pull request in it
    check_run      rerequested                              check per pull request in it
    issue_comment  created on a pull request, `/udebt ack`  ack for the head the last check
                   by a lead or owner                       ran on, then check again
    installation   -                                        installation {account, id}

Everything else is `ignore` with a reason, as is a repository nobody registered, a push to
another branch, and an acknowledgement by someone who is not a lead or owner. Who is a lead is
the caller's to say (`role_of`): roles live wherever the host keeps them.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

PR_ACTIONS = ("opened", "synchronize", "reopened", "ready_for_review")
ACK_COMMAND = "/udebt ack"
ACK_LABEL = "udebt-acknowledged"


def verify(secret: str, body: bytes, signature: str | None) -> bool:
    """`X-Hub-Signature-256` is `sha256=<hex HMAC-SHA256 of the raw body>`; constant time."""
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, signature.split("=", 1)[1])


def sign(secret: str, body: bytes) -> str:
    """The header GitHub would send (for tests and local replays)."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@dataclass
class Action:
    kind: str  # pong | analyze | check | ack | installation | ignore
    repo: str | None = None  # owner/name
    jobs: list[dict[str, Any]] = field(default_factory=list)  # checks or analyses to queue
    ack: dict[str, Any] | None = None  # {pr, head_sha, login, via}
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [])}


def route(
    event: str,
    p: dict[str, Any],
    *,
    registered: Callable[[str], dict[str, Any] | None],
    role_of: Callable[[str, str], str | None],
    last_head: Callable[[str, int], str | None] = lambda repo, pr: None,
    ack_label: str = ACK_LABEL,
) -> Action:
    """What one verified delivery asks for. `registered(full_name)` gives the repository's
    settings (`default_branch`) or None; `role_of(full_name, login)` the login's role there;
    `last_head(full_name, pr)` the head sha the latest check ran on, if any."""
    if event == "ping":
        return Action("pong")
    if event == "installation":
        inst = p.get("installation") or {}
        return Action("installation", jobs=[{"account": (inst.get("account") or {})
                      .get("login"), "id": inst.get("id"), "action": p.get("action")}])  # fmt: skip
    full = (p.get("repository") or {}).get("full_name") or ""
    repo = registered(full) if full else None
    if repo is None:
        return Action("ignore", full or None, reason="the repository is not registered")
    if event == "push":
        branch = repo.get("default_branch") or "main"
        if p.get("ref") != f"refs/heads/{branch}" or p.get("deleted"):
            return Action("ignore", full, reason=f"not a push to {branch}")
        return Action("analyze", full, jobs=[{"kind": "analyze", "sha": p.get("after")}])
    if event == "pull_request":
        pr = p.get("pull_request") or {}
        n = int(p.get("number") or pr.get("number"))
        author = (pr.get("user") or {}).get("login")
        job = {"kind": "check", "pr": n, "head_sha": pr["head"]["sha"],
               "base_sha": pr["base"]["sha"], "author": author}  # fmt: skip
        if p.get("action") == "labeled":
            if (p.get("label") or {}).get("name") != ack_label:
                return Action("ignore", full, reason="another label")
            who = (p.get("sender") or {}).get("login", "")
            if role_of(full, who) not in ("owner", "lead"):
                return Action("ignore", full, reason=f"@{who} is not a lead or owner here")
            ack = {"pr": n, "head_sha": job["head_sha"], "login": who, "via": "label"}
            return Action("ack", full, jobs=[job], ack=ack)
        if p.get("action") not in PR_ACTIONS:
            return Action("ignore", full, reason=f"pull_request {p.get('action')}")
        return Action("check", full, jobs=[job])
    if event in ("check_suite", "check_run"):
        obj = p.get(event) or {}
        wanted = ("requested", "rerequested") if event == "check_suite" else ("rerequested",)
        if p.get("action") not in wanted:
            return Action("ignore", full, reason=f"{event} {p.get('action')}")
        jobs = [{"kind": "check", "pr": pr["number"], "head_sha": obj["head_sha"],
                 "base_sha": pr["base"]["sha"], "author": None}
                for pr in obj.get("pull_requests") or []]  # fmt: skip
        if not jobs:
            return Action("ignore", full, reason="no pull request in it")
        return Action("check", full, jobs=jobs)
    if event == "issue_comment":
        issue, comment = p.get("issue") or {}, p.get("comment") or {}
        if p.get("action") != "created" or not issue.get("pull_request"):
            return Action("ignore", full, reason="not a new comment on a pull request")
        if not str(comment.get("body", "")).strip().startswith(ACK_COMMAND):
            return Action("ignore", full, reason="not a udebt command")
        who = (comment.get("user") or p.get("sender") or {}).get("login", "")
        if role_of(full, who) not in ("owner", "lead"):
            return Action("ignore", full, reason=f"@{who} is not a lead or owner here")
        n = int(issue["number"])
        head = last_head(full, n)
        ack = {"pr": n, "head_sha": head or "*", "login": who, "via": "comment"}
        jobs = [{"kind": "check", "pr": n, "head_sha": head, "base_sha": None, "author": None}]
        return Action("ack", full, jobs=jobs if head else [], ack=ack,
                      reason=None if head else "no check yet: it passes when it runs")  # fmt: skip
    return Action("ignore", full, reason=f"event {event}")
