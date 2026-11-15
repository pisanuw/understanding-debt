"""Phase 4a: udebt push and pull over the contract, against a tiny stdlib stub of a host that
keys rows the way the protocol says (remote.py)."""

import json
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import understood

from udebt import cli, contract
from udebt.store import Store

TOKEN = "udbt_test-token"


class Host:
    """What a host keeps: points by time, evidence by (dev, ts, unit, level, question)."""

    def __init__(self):
        self.points: dict[str, dict] = {}
        self.evidence: dict[tuple, dict] = {}
        self.pushes = 0
        self.seen_headers: list[dict] = []


def stub(host: Host) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            host.seen_headers.append(dict(self.headers))
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self._send(401, {"error": "bad token"})
                return False
            return True

        def do_POST(self):  # noqa: N802
            if not self._authed():
                return
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            errors = contract.validate("import", data)
            if self.path != "/udebt/import" or errors:
                return self._send(422, {"error": "; ".join(errors[:3]) or "no such path"})
            host.pushes += 1
            new_points = sum(p["t"] not in host.points for p in data["snapshots"])
            host.points.update({p["t"]: p for p in data["snapshots"]})
            new_ev = 0
            for e in data["evidence"]:
                k = (e["dev"], e["ts"], e["unit"], e.get("level"), e.get("question"))
                new_ev += k not in host.evidence
                host.evidence.setdefault(k, e)
            self._send(200, {"imported": {"points": new_points, "evidence": new_ev}})

        def do_GET(self):  # noqa: N802
            if not self._authed():
                return
            self._send(200, {"contract": 1, "evidence": list(host.evidence.values()),
                             "snapshots": list(host.points.values())})  # fmt: skip

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def remote_repo(fixture_repo, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UDEBT_LLM", "fake")
    monkeypatch.setenv("UDEBT_CREDENTIALS", str(tmp_path / "creds.json"))
    monkeypatch.delenv("UDEBT_TOKEN", raising=False)
    root = fixture_repo
    cli.main(["--repo", str(root), "init"])
    cli.main(["--repo", str(root), "map"])
    store = Store(root)
    for r in understood("storage", ts="2026-11-14T09:00:00+00:00"):
        store.append_record("dev1@example.com", {**r, "dev": "dev1@example.com", "question": "q"})
    host = Host()
    srv = stub(host)
    capsys.readouterr()
    yield root, store, host, f"http://127.0.0.1:{srv.server_address[1]}/udebt"
    srv.shutdown()
    srv.server_close()


def run(root, capsys, *args, stdin=None, monkeypatch=None):
    rc = cli.main(["--repo", str(root), *args])
    return rc, capsys.readouterr().out


def test_push_is_idempotent_and_needs_a_token(remote_repo, capsys, monkeypatch):
    root, store, host, url = remote_repo
    with pytest.raises(SystemExit, match="No remote yet"):
        cli.main(["--repo", str(root), "push"])
    rc, out = run(root, capsys, "remote", "set", url)
    assert "remote.json" in (store.dir / ".gitignore").read_text()
    with pytest.raises(SystemExit, match="No token"):
        cli.main(["--repo", str(root), "push"])
    rc, out = run(root, capsys, "push", "--dry-run")
    assert rc == 0 and "dry run" in out and host.pushes == 0
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(TOKEN + "\n"))
    rc, out = run(root, capsys, "remote", "token")
    from udebt import remote

    path = remote.credentials_path()
    assert json.loads(path.read_text()) == {url: TOKEN}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and TOKEN not in out
    rc, out = run(root, capsys, "push")
    assert rc == 0 and host.pushes == 1 and '"evidence": 3' in out
    assert host.seen_headers[-1]["X-Udebt-Contract"] == "1"
    rc, out = run(root, capsys, "push")
    assert '"evidence": 0' in out and '"points": 0' in out, "the second push adds nothing"
    monkeypatch.setenv("UDEBT_TOKEN", "wrong")
    with pytest.raises(SystemExit, match="HTTP 401"):
        cli.main(["--repo", str(root), "push"])


def test_pull_brings_host_evidence_back_once(remote_repo, capsys, monkeypatch):
    root, store, host, url = remote_repo
    monkeypatch.setenv("UDEBT_TOKEN", TOKEN)
    run(root, capsys, "remote", "set", url)
    run(root, capsys, "push")
    # a quiz answered on the host, by a teammate, and one row that breaks the contract
    host.evidence[("dev2@example.com", "2026-11-15T08:00:00+00:00", "scoring", 1, "Why?")] = {
        "ts": "2026-11-15T08:00:00+00:00", "dev": "dev2@example.com", "unit": "scoring",
        "level": 1, "verdict": "correct", "confidence": 2, "type": "quiz", "question": "Why?",
    }  # fmt: skip
    host.evidence[("x", "t", "u", None, None)] = {"ts": "t", "dev": "x", "unit": "u",
                                                  "verdict": "??"}  # fmt: skip
    rc, out = run(root, capsys, "pull")
    assert rc == 0 and "1 new evidence record(s) (dev2@example.com 1)" in out
    assert "1 row(s) broke the contract" in out
    (rec,) = store.load_records("dev2@example.com")
    assert rec["origin"] == "remote" and rec["level"] == 1 and rec["unit"] == "scoring"
    assert contract.validate("record", rec) == []
    assert (store.dir / "remote" / "snapshots.json").exists()
    rc, out = run(root, capsys, "pull")
    assert "0 new evidence record(s)" in out, "idempotent"
    rc, out = run(root, capsys, "push")
    assert '"evidence": 0' in out, "pulled evidence pushed back is not a duplicate"
    rc, out = run(root, capsys, "remote")
    assert f"remote {url}; token set" in out
