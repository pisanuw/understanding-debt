"""GitHub: webhook verification and routing, and the App client that posts check runs.

Events handled (`handle`), each answered at once, the work queued as a job:

- `push` to the default branch: an `analyze` job (fetch, replay, import);
- `pull_request` opened, synchronize, reopened, ready_for_review: a `check` job (the
  change's risk for its author, posted as a check run); `labeled` with the acknowledgement
  label by a lead or owner: an acknowledgement, and the check posted again;
- `check_suite` requested or rerequested, `check_run` rerequested: a `check` job per pull
  request in it;
- `issue_comment` created on a pull request, starting `/udebt ack`, by a lead or owner: an
  acknowledgement (for the head the last check ran on), and the check posted again;
- `installation`: the installation id kept on the organisation whose slug is the account;
- `ping`: pong. Anything else is ignored with a reason.

The App client signs an RS256 JWT with the App's private key, exchanges it for an installation
token and posts the check run; its HTTP goes through an injectable function, so tests (and
the recorded fixtures under tests/fixtures/github/) never touch the network.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from typing import Any

from .db import Conn

PR_ACTIONS = ("opened", "synchronize", "reopened", "ready_for_review")


def verify(secret: str, body: bytes, signature: str | None) -> bool:
    """`X-Hub-Signature-256` is `sha256=<hex HMAC of the body>`, compared in constant time."""
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, signature.split("=", 1)[1])


def repo_for(c: Conn, full_name: str) -> dict[str, Any] | None:
    return c.one("select * from repos where lower(github) = lower(?)", [full_name])


def role_of_login(c: Conn, org_id: str, login: str) -> str | None:
    row = c.one(
        "select m.role from memberships m join users u on u.id = m.user_id "
        "where m.org_id = ? and lower(u.login) = lower(?)",
        [org_id, login],
    )
    return row["role"] if row else None


def acknowledge(c: Conn, repo: dict[str, Any], pr: int, head: str, login: str, via: str) -> None:
    user = c.one("select id from users where lower(login) = lower(?)", [login])
    c.upsert("acks", {"repo_id": repo["id"], "pr": pr, "head_sha": head,
             "user_id": user["id"] if user else None, "login": login, "via": via},
             ("repo_id", "pr", "head_sha"))  # fmt: skip


def ack_for(c: Conn, repo_id: str, pr: int, head: str) -> dict[str, Any] | None:
    """The acknowledgement for this head (or one made before any check ran, `*`)."""
    return c.one(
        "select * from acks where repo_id = ? and pr = ? and head_sha in (?, '*') "
        "order by created_at desc",
        [repo_id, pr, head],
    )


def handle(
    c: Conn, event: str, p: dict[str, Any], enqueue: Callable[..., str], ack_label: str
) -> tuple[int, dict[str, Any]]:
    """Route one verified webhook delivery; returns (status, body)."""
    if event == "ping":
        return 200, {"pong": True}
    if event == "installation":
        acct = ((p.get("installation") or {}).get("account") or {}).get("login", "")
        n = c.exec("update orgs set github_installation = ? where lower(slug) = lower(?)",
                   [(p.get("installation") or {}).get("id"), acct])  # fmt: skip
        return 200, {"installation": bool(n)}
    full = ((p.get("repository") or {}).get("full_name")) or ""
    repo = repo_for(c, full)
    if repo is None:
        return 200, {"ignored": f"{full or 'this repository'} is not registered with udebt"}
    if event == "push":
        if p.get("ref") != f"refs/heads/{repo['default_branch']}" or p.get("deleted"):
            return 200, {"ignored": f"not a push to {repo['default_branch']}"}
        return 202, {"queued": enqueue(c, repo["id"], "analyze", {"sha": p.get("after")})}
    if event == "pull_request":
        pr = p.get("pull_request") or {}
        n, head = int(p.get("number") or pr.get("number")), pr["head"]["sha"]
        if p.get("action") == "labeled" and (p.get("label") or {}).get("name") == ack_label:
            who = (p.get("sender") or {}).get("login", "")
            if role_of_login(c, repo["org_id"], who) not in ("owner", "lead"):
                return 200, {"ignored": f"@{who} is not a lead or owner here"}
            acknowledge(c, repo, n, head, who, "label")
        elif p.get("action") not in PR_ACTIONS:
            return 200, {"ignored": f"pull_request {p.get('action')}"}
        job = {"pr": n, "head_sha": head, "base_sha": pr["base"]["sha"],
               "author": (pr.get("user") or {}).get("login")}  # fmt: skip
        return 202, {"queued": enqueue(c, repo["id"], "check", job)}
    if event in ("check_suite", "check_run"):
        obj = p.get(event) or {}
        if p.get("action") not in ("requested", "rerequested"):
            return 200, {"ignored": f"{event} {p.get('action')}"}
        ids = []
        for pr in obj.get("pull_requests") or []:
            job = {"pr": pr["number"], "head_sha": obj["head_sha"], "base_sha": pr["base"]["sha"],
                   "author": None}  # fmt: skip
            ids.append(enqueue(c, repo["id"], "check", job))
        return (202, {"queued": ids}) if ids else (200, {"ignored": "no pull request in it"})
    if event == "issue_comment":
        issue, comment = p.get("issue") or {}, p.get("comment") or {}
        body = str(comment.get("body", "")).strip()
        if p.get("action") != "created" or not issue.get("pull_request"):
            return 200, {"ignored": "not a new comment on a pull request"}
        if not body.startswith("/udebt ack"):
            return 200, {"ignored": "not a udebt command"}
        who = (comment.get("user") or p.get("sender") or {}).get("login", "")
        if role_of_login(c, repo["org_id"], who) not in ("owner", "lead"):
            return 200, {"ignored": f"@{who} is not a lead or owner here"}
        n = int(issue["number"])
        last = c.one("select head_sha, payload from checks where repo_id = ? and pr = ? "
                     "order by created_at desc", [repo["id"], n])  # fmt: skip
        head = last["head_sha"] if last else "*"
        acknowledge(c, repo, n, head, who, "comment")
        if last is None:
            return 200, {"acknowledged": n, "note": "no check yet; it will pass when it runs"}
        base = (last.get("payload") or {}).get("udebt", {}).get("base_sha")
        job = {"pr": n, "head_sha": head, "base_sha": base, "author": None}
        return 202, {"acknowledged": n, "queued": enqueue(c, repo["id"], "check", job)}
    return 200, {"ignored": f"event {event}"}


# -- the App client ----------------------------------------------------------------------

Http = Callable[[str, str, dict[str, str], bytes | None], Any]


class GitHubApp:
    """Posts check runs as the GitHub App, and mints clone tokens. Untested against GitHub
    itself (no App could be created here); tests drive it with a recorded-response `http`."""

    API = "https://api.github.com"

    def __init__(self, app_id: str, private_key: str, http: Http):
        self.app_id = app_id
        self.private_key = private_key
        self.http = http

    def _jwt(self) -> str:
        import jwt

        now = int(time.time())
        claims = {"iat": now - 60, "exp": now + 540, "iss": self.app_id}
        return jwt.encode(claims, self.private_key, algorithm="RS256")

    def installation_token(self, installation: int) -> str:
        h = {"Authorization": f"Bearer {self._jwt()}", "Accept": "application/vnd.github+json",
             "User-Agent": "udebt"}  # fmt: skip
        res = self.http("POST", f"{self.API}/app/installations/{installation}/access_tokens",
                        h, b"")  # fmt: skip
        return res["token"]

    def post_check(self, full_name: str, installation: int, body: dict[str, Any]) -> Any:
        token = self.installation_token(installation)
        h = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json",
             "User-Agent": "udebt", "Content-Type": "application/json"}  # fmt: skip
        public = {k: v for k, v in body.items() if k != "udebt"}
        return self.http("POST", f"{self.API}/repos/{full_name}/check-runs", h,
                         json.dumps(public).encode())  # fmt: skip
