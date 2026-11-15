"""Who is calling: session JWTs, personal API tokens, GitHub OAuth, and a dev login.

- **Session JWT** (HS256, `UDEBT_JWT_SECRET`, `sub` = the user's uuid, 12 hours): what the
  browser holds after signing in, sent as `Authorization: Bearer <jwt>`. In Postgres the same
  `sub` becomes the row-level-security caller (db.py).
- **API token** (`udbt_...`, made by `POST /api/tokens`, stored as a SHA-256 hash): what
  `udebt push` and `udebt pull` send; it never expires until deleted.
- **GitHub OAuth**: `/auth/github/login` redirects to GitHub with a signed `state`;
  `/auth/github/callback` exchanges the code, reads the user and their verified primary
  email, upserts the user and sends the browser back to the app with `#token=<jwt>`. The HTTP
  calls go through `http` (urllib by default), so tests replay recorded responses.
- **Dev login** (`UDEBT_DEV_AUTH=1`, never in production): `POST /auth/dev {login, email}`
  mints a session for anyone, for local runs and the end-to-end test.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any

import jwt

from .db import Conn

ISSUER = "udebt"
TOKEN_PREFIX = "udbt_"


class Unauthorized(Exception):
    pass


def issue(secret: str, user: dict[str, Any], hours: int = 12, **extra: Any) -> str:
    now = int(time.time())
    claims = {"sub": str(user["id"]), "login": user.get("login"), "iss": ISSUER, "iat": now,
              "exp": now + hours * 3600, "role": "authenticated", **extra}  # fmt: skip
    return jwt.encode(claims, secret, algorithm="HS256")


def verify(secret: str, token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"], issuer=ISSUER)
    except jwt.PyJWTError as e:
        raise Unauthorized(f"bad session token: {e}") from e


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_api_token(c: Conn, user_id: str, name: str) -> str:
    token = TOKEN_PREFIX + secrets.token_urlsafe(30)
    c.exec(
        "insert into api_tokens (id, user_id, name, token_hash) values (?, ?, ?, ?)",
        [uuid.uuid4().hex, user_id, name[:80] or "cli", hash_token(token)],
    )
    return token


def user_for(c: Conn, secret: str, header: str | None) -> dict[str, Any]:
    """The user an `Authorization: Bearer ...` header names (a JWT or an API token)."""
    if not header or not header.lower().startswith("bearer "):
        raise Unauthorized("sign in first (Authorization: Bearer <token>)")
    token = header.split(None, 1)[1].strip()
    if token.startswith(TOKEN_PREFIX):
        row = c.one(
            "select u.* from api_tokens t join users u on u.id = t.user_id where t.token_hash = ?",
            [hash_token(token)],
        )
        if row is None:
            raise Unauthorized("unknown API token")
        c.exec("update api_tokens set last_used = CURRENT_TIMESTAMP where token_hash = ?",
               [hash_token(token)])  # fmt: skip
        return row
    claims = verify(secret, token)
    row = c.one("select * from users where id = ?", [claims["sub"]])
    if row is None:
        raise Unauthorized("the session's user no longer exists")
    return row


def upsert_user(
    c: Conn, login: str, email: str | None, name: str | None = None, github_id: int | None = None
) -> dict[str, Any]:
    row = None
    if github_id is not None:
        row = c.one("select * from users where github_id = ?", [github_id])
    if row is None:
        row = c.one("select * from users where login = ?", [login])
    if row is None:
        uid = str(uuid.uuid4())
        c.exec(
            "insert into users (id, github_id, login, email, name) values (?, ?, ?, ?, ?)",
            [uid, github_id, login, email, name],
        )
    else:
        uid = row["id"]
        c.exec(
            "update users set login = ?, email = coalesce(?, email), name = coalesce(?, name), "
            "github_id = coalesce(?, github_id) where id = ?",
            [login, email, name, github_id, uid],
        )
    return c.one("select * from users where id = ?", [uid])


# -- GitHub OAuth ------------------------------------------------------------------------

Http = Callable[[str, str, dict[str, str], bytes | None], dict[str, Any]]


def urllib_http(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Any:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 (fixed GitHub hosts)
        return json.loads(r.read() or b"null")


def login_url(client_id: str, secret: str, callback: str, return_to: str) -> str:
    state = jwt.encode({"r": return_to, "n": secrets.token_urlsafe(8),
                        "exp": int(time.time()) + 600}, secret, algorithm="HS256")  # fmt: skip
    q = urllib.parse.urlencode({"client_id": client_id, "redirect_uri": callback,
                                "scope": "read:user user:email", "state": state})  # fmt: skip
    return f"https://github.com/login/oauth/authorize?{q}"


def github_user(
    code: str, state: str, *, client_id: str, client_secret: str, secret: str, http: Http
) -> tuple[dict[str, Any], str]:
    """(the GitHub user with their verified primary email, where to send the browser)."""
    try:
        st = jwt.decode(state, secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise Unauthorized(f"bad OAuth state: {e}") from e
    body = urllib.parse.urlencode({"client_id": client_id, "client_secret": client_secret,
                                   "code": code}).encode()  # fmt: skip
    tok = http("POST", "https://github.com/login/oauth/access_token",
               {"Accept": "application/json"}, body)  # fmt: skip
    access = (tok or {}).get("access_token")
    if not access:
        raise Unauthorized(f"GitHub refused the code: {(tok or {}).get('error', 'no token')}")
    h = {"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json",
         "User-Agent": "udebt"}  # fmt: skip
    user = http("GET", "https://api.github.com/user", h, None)
    emails = http("GET", "https://api.github.com/user/emails", h, None) or []
    primary = next((e["email"] for e in emails if e.get("primary") and e.get("verified")), None)
    user["verified_email"] = primary or user.get("email")
    return user, str(st.get("r") or "")
