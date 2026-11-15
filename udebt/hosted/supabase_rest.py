"""Supabase calls made with the signed-in person's own token (HOSTING.md: apps never get the
service-role key). Auth (`/auth/v1/user`) says who the token belongs to; PostgREST
(`/rest/v1/<table>`, schema app_udebt) reads and writes under row level security, so a row
the worker writes is the person's own, and a shared analysis is readable by everyone signed in.
Stdlib only (urllib); every call has a timeout; the token is never logged.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SCHEMA = "app_udebt"


class SupabaseError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def token_seconds_left(token: str, now: float | None = None) -> float | None:
    """Seconds until the JWT's `exp` (read, not verified: Supabase does); None if unreadable."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"]) - (time.time() if now is None else now)
    except (IndexError, KeyError, ValueError, TypeError):
        return None


class Supabase:
    def __init__(self, url: str, anon_key: str, token: str, timeout: float = 30):
        self.url = url.rstrip("/")
        self._anon = anon_key
        self._token = token
        self.timeout = timeout

    def __repr__(self) -> str:  # never show the token
        return f"Supabase({self.url})"

    def _request(
        self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
    ) -> Any:
        h = {"apikey": self._anon, "authorization": f"Bearer {self._token}", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["content-type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:  # noqa: S310
                raw = res.read()
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8"))
            except (ValueError, OSError):
                detail = {}
            msg = detail.get("message") or detail.get("msg") or detail.get("error") or e.reason
            raise SupabaseError(e.code, f"{method} {path.split('?')[0]}: {msg}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SupabaseError(0, f"Supabase could not be reached: {e}") from None
        return json.loads(raw) if raw else None

    def user(self) -> dict[str, Any]:
        """The signed-in person ({id, email, ...}); SupabaseError 401 for a bad token."""
        return self._request("GET", "/auth/v1/user")

    # -- PostgREST ---------------------------------------------------------------------------
    def select(self, table: str, query: dict[str, str]) -> list[dict[str, Any]]:
        q = urllib.parse.urlencode(query, safe=",.()*:")
        return self._request("GET", f"/rest/v1/{table}?{q}", headers={"accept-profile": SCHEMA})

    def insert(self, table: str, rows: Any, *, ignore_duplicates: bool = False) -> list[dict]:
        prefer = "return=representation"
        if ignore_duplicates:
            prefer += ",resolution=ignore-duplicates"
        return (
            self._request(
                "POST", f"/rest/v1/{table}", rows, {"content-profile": SCHEMA, "prefer": prefer}
            )
            or []
        )

    def update(self, table: str, query: dict[str, str], values: dict[str, Any]) -> list[dict]:
        q = urllib.parse.urlencode(query, safe=",.()*:")
        return (
            self._request(
                "PATCH",
                f"/rest/v1/{table}?{q}",
                values,
                {"content-profile": SCHEMA, "prefer": "return=representation"},
            )
            or []
        )

    # -- what the worker needs ---------------------------------------------------------------
    def repository(self, url: str) -> dict[str, Any] | None:
        rows = self.select(
            "repositories", {"select": "id,url,name,license,is_sample", "url": f"eq.{url}"}
        )
        return rows[0] if rows else None

    def ensure_repository(self, url: str, name: str, license: str | None) -> int:
        """The repository's id, adding it (added_by = the person) when the app has none."""
        got = self.repository(url)
        if got:
            return int(got["id"])
        try:
            rows = self.insert("repositories", {"url": url, "name": name, "license": license})
        except SupabaseError as e:
            if e.status != 409:
                raise
            rows = []
        got = rows[0] if rows else self.repository(url)
        if not got:
            raise SupabaseError(500, f"the repository {url} could not be added")
        return int(got["id"])

    def analysed_shas(self, repo_id: int) -> list[str]:
        rows = self.select(
            "analyses",
            {
                "select": "sha",
                "repo_id": f"eq.{repo_id}",
                "order": "created_at.desc",
                "limit": "20",
            },
        )
        return [r["sha"] for r in rows]

    def add_mine(self, repo_id: int) -> None:
        self.insert("my_repositories", {"repo_id": repo_id}, ignore_duplicates=True)

    def start_job(self, url: str, sha: str | None) -> int | None:
        rows = self.insert("jobs", {"url": url, "sha": sha, "status": "running"})
        return int(rows[0]["id"]) if rows else None

    def finish_job(self, job_id: int | None, status: str, message: str, **extra: Any) -> None:
        if job_id is None:
            return
        values = {
            "status": status,
            "message": message[:500],
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **extra,
        }
        self.update("jobs", {"id": f"eq.{job_id}"}, values)
