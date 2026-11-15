"""`udebt push` and `udebt pull` (Phase 4a): the CLI's twin of a hosted store, over the contract.

The endpoint is configurable and the protocol small, so any host can serve it (the HintWise
platform's functions in Phase 4b, a test stub here):

    POST <url>/import   body: the import payload (contract `import`: the export with the
                        sanitized map); headers Authorization: Bearer <token>,
                        X-Udebt-Contract: <version>. The host keys every row naturally
                        (a point's time; an evidence record's developer, time, unit, level
                        and question), so a second push of the same history adds nothing.
                        reply: {"imported": {...counts}}
    GET  <url>/pull     reply: {"contract": N, "evidence": [contract `evidence` rows],
                        "snapshots": [points]}: what the caller may see, evidence recorded on
                        the host (a quiz taken in the browser) included.

Settings live in `.udebt/remote.json` (gitignored: `{"url": ...}`); the token comes from
`UDEBT_TOKEN`, else from `~/.config/udebt/credentials.json` (mode 0600, one token per URL,
written by `udebt remote token`, which reads it from stdin so it stays out of shell history).
Pulled evidence joins the developer's records with `origin: remote`, once (keyed like the
host keys it); pulled snapshots go to `.udebt/remote/snapshots.json` (gitignored). Stdlib only.
"""

from __future__ import annotations

import json
import os
import stat
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import contract
from .store import Store

NAME = "remote.json"
TIMEOUT = 120


class RemoteError(Exception):
    pass


def credentials_path() -> Path:
    env = os.environ.get("UDEBT_CREDENTIALS")
    return Path(env) if env else Path.home() / ".config" / "udebt" / "credentials.json"


def load_config(store: Store) -> dict[str, Any]:
    p = store.dir / NAME
    if not p.exists():
        raise RemoteError("No remote yet: `udebt remote set <url>`.")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not data.get("url"):
        raise RemoteError(f"{p} names no url.")
    return data


def save_config(store: Store, url: str) -> Path:
    from .cache import ensure_gitignore

    p = store.dir / NAME
    ensure_gitignore(store.dir)
    p.write_text(json.dumps({"url": url.rstrip("/")}, indent=2) + "\n", encoding="utf-8")
    return p


def save_token(url: str, token: str) -> Path:
    p = credentials_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    data[url.rstrip("/")] = token.strip()
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    p.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return p


def token_for(url: str) -> str:
    if os.environ.get("UDEBT_TOKEN"):
        return os.environ["UDEBT_TOKEN"].strip()
    p = credentials_path()
    if p.exists():
        got = json.loads(p.read_text(encoding="utf-8")).get(url.rstrip("/"))
        if got:
            return got
    raise RemoteError("No token: set UDEBT_TOKEN, or run `udebt remote token` and paste it.")


def _call(method: str, url: str, token: str, body: bytes | None = None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
               "X-Udebt-Contract": str(contract.CONTRACT), "User-Agent": "udebt"}  # fmt: skip
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 (the user's URL)
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read() or b"{}").get("error")
        except ValueError:
            msg = None
        raise RemoteError(f"{method} {url}: HTTP {e.code} {msg or e.reason}") from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise RemoteError(f"{method} {url}: {e}") from e


def payload(root: Path, store: Store) -> dict[str, Any]:
    """The import payload: the export (series brought up to date) with the sanitized map."""
    from . import replay

    h = replay.load(root, store)
    res = replay.ensure(h, None)
    if res.note:
        raise RemoteError(f"{res.note}; push the current series")
    data = replay.export_data(h, res)
    data["map"] = contract.sanitize_map(store.require_map())
    return contract.check("import", data)


def push(root: Path, store: Store, *, dry_run: bool = False) -> dict[str, Any]:
    cfg = load_config(store)
    data = payload(root, store)
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    summary = {
        "url": cfg["url"],
        "bytes": len(body),
        "points": len(data["snapshots"]),
        "evidence": len(data["evidence"]),
        "risk_events": len(data["risk_events"]),
    }
    if dry_run:
        return {**summary, "sent": False}
    reply = _call("POST", f"{cfg['url']}/import", token_for(cfg["url"]), body)
    return {**summary, "sent": True, "reply": reply}


def _key(r: dict[str, Any], slug: str = "") -> tuple:
    """An evidence record's natural key: developer, time, unit, level and question."""
    lvl = r.get("level")
    return (str(r.get("dev") or slug).lower(), r.get("ts"), r.get("unit"),
            None if lvl is None else int(lvl), r.get("question"))  # fmt: skip


def pull(store: Store) -> dict[str, Any]:
    from .cache import ensure_gitignore

    cfg = load_config(store)
    data = _call("GET", f"{cfg['url']}/pull", token_for(cfg["url"]))
    if int(data.get("contract", contract.CONTRACT)) > contract.CONTRACT:
        raise RemoteError(f"the host speaks contract {data['contract']}; upgrade udebt")
    have = {_key(r, slug) for slug, recs in store.all_records().items() for r in recs}
    added: dict[str, int] = {}
    bad = 0
    for e in data.get("evidence") or []:
        if contract.validate("evidence", e):
            bad += 1
            continue
        if _key(e) in have:
            continue
        rec = {"ts": e["ts"], "type": e.get("type") or "quiz", "source": e.get("type") or "quiz",
               "dev": e["dev"], "unit": e["unit"], "level": e.get("level"),
               "head": e.get("head"), "question": e.get("question"), "verdict": e["verdict"],
               "confidence": e.get("confidence") or 1, "origin": "remote"}  # fmt: skip
        if e.get("forced"):
            rec["forced"] = True
        store.append_record(e["dev"], rec)
        have.add(_key(e))
        added[e["dev"]] = added.get(e["dev"], 0) + 1
    snaps = data.get("snapshots") or []
    if snaps:
        d = store.dir / "remote"
        d.mkdir(parents=True, exist_ok=True)
        ensure_gitignore(store.dir)
        (d / "snapshots.json").write_text(json.dumps({"points": snaps}) + "\n", encoding="utf-8")
    return {"url": cfg["url"], "evidence_added": added, "invalid": bad, "points": len(snaps)}
