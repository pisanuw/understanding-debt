"""Phase 4a: the pull-request check run (checks.py) and the webhook router (webhooks.py),
from fixtures; no network, no web framework."""

import json
from pathlib import Path

import pytest
from conftest import git

from udebt import checks, cli, webhooks

FIX = Path(__file__).parent / "fixtures" / "github"
ROLES = {"alice": "owner", "lena": "lead", "bob": "member"}


def load(name):
    raw = (FIX / f"{name}.json").read_bytes()
    return raw, json.loads(raw)


def route(event, name, last_head=lambda repo, pr: None):
    _, p = load(name)
    return webhooks.route(
        event, p,
        registered=lambda full: {"default_branch": "main"} if full == "acme/shop" else None,
        role_of=lambda full, login: ROLES.get(login),
        last_head=last_head,
    )  # fmt: skip


def test_signatures_are_checked_in_constant_time():
    raw, _ = load("push")
    sig = webhooks.sign("s3cret", raw)
    assert webhooks.verify("s3cret", raw, sig)
    assert not webhooks.verify("s3cret", raw + b" ", sig), "a changed body"
    assert not webhooks.verify("other", raw, sig) and not webhooks.verify("s3cret", raw, None)
    assert not webhooks.verify("", raw, sig), "no secret configured: refuse"
    assert not webhooks.verify("s3cret", raw, sig.replace("sha256=", "sha1="))


def test_each_event_routes_to_its_action():
    assert route("ping", "ping").kind == "pong"
    a = route("push", "push")
    assert a.kind == "analyze" and a.jobs == [{"kind": "analyze", "sha": "2" * 40}]
    assert route("push", "push_branch").reason == "not a push to main"
    a = route("pull_request", "pull_request_opened")
    assert a.kind == "check" and a.jobs[0] == {"kind": "check", "pr": 7, "head_sha": "4" * 40,
                                                "base_sha": "2" * 40, "author": "bob"}  # fmt: skip
    a = route("pull_request", "pull_request_labeled")
    assert a.kind == "ack" and a.ack == {"pr": 7, "head_sha": "4" * 40, "login": "lena",
                                         "via": "label"} and a.jobs  # fmt: skip
    a = route("check_suite", "check_suite_rerequested")
    assert a.kind == "check" and a.jobs[0]["head_sha"] == "4" * 40
    a = route("issue_comment", "issue_comment_ack", last_head=lambda r, n: "4" * 40)
    assert a.kind == "ack" and a.ack["via"] == "comment" and a.ack["head_sha"] == "4" * 40
    a = route("issue_comment", "issue_comment_ack")
    assert a.kind == "ack" and a.ack["head_sha"] == "*" and a.jobs == [], "no check ran yet"
    a = route("installation", "installation")
    assert a.kind == "installation" and a.jobs[0] == {"account": "acme", "id": 555,
                                                      "action": "created"}  # fmt: skip
    assert route("issues", "push").kind == "ignore"


def test_only_a_lead_or_owner_acknowledges_and_only_registered_repos_count():
    _, p = load("pull_request_labeled")
    p["sender"]["login"] = "bob"
    a = webhooks.route(
        "pull_request", p, registered=lambda f: {}, role_of=lambda f, x: ROLES.get(x)
    )
    assert a.kind == "ignore" and "@bob is not a lead or owner" in a.reason
    _, p = load("issue_comment_ack")
    p["comment"]["body"] = "looks good"
    a = webhooks.route("issue_comment", p, registered=lambda f: {}, role_of=lambda f, x: "lead")
    assert a.reason == "not a udebt command"
    a = webhooks.route(
        "push", load("push")[1], registered=lambda f: None, role_of=lambda f, x: None
    )
    assert a.kind == "ignore" and a.reason == "the repository is not registered"
    _, p = load("pull_request_opened")
    p["action"] = "closed"
    a = webhooks.route("pull_request", p, registered=lambda f: {}, role_of=lambda f, x: None)
    assert a.reason == "pull_request closed"


ROWS = [
    {"unit": "storage", "level": "HIGH", "mass": 0.62, "understanding": 0.0, "in_set": True},
    {"unit": "scoring", "level": "OK", "mass": 0.05, "understanding": 0.8, "in_set": None},
]


def test_advisory_by_default_soft_block_until_a_lead_acknowledges():
    a = checks.payload("4" * 40, "bob", ROWS)
    assert a["conclusion"] == "success" and a["output"]["title"] == "Advisory: HIGH in storage"
    assert "| HIGH | `storage` | in | 0.62 | 0.00 |" in a["output"]["summary"]
    assert "udebt digest --unit storage" in a["output"]["summary"]
    assert a["name"] == "udebt understanding" and a["status"] == "completed"
    s = checks.payload("4" * 40, "bob", ROWS, soft_block=True)
    assert s["conclusion"] == "failure" and "acknowledgement clears this" in s["output"]["title"]
    assert (
        "`/udebt ack`" in s["output"]["summary"]
        and "`udebt-acknowledged`" in s["output"]["summary"]
    )
    k = checks.payload(
        "4" * 40, "bob", ROWS, soft_block=True, ack={"login": "lena", "via": "label"}
    )
    assert k["conclusion"] == "success" and "acknowledged by @lena" in k["output"]["title"]
    ok = checks.payload("4" * 40, "bob", ROWS[1:], soft_block=True)
    assert ok["conclusion"] == "success" and "nothing here needs it" in ok["output"]["summary"]
    none = checks.payload("4" * 40, "bob", [])
    assert none["output"]["title"] == "No architectural unit at risk"


def test_risk_prints_the_check_run_for_a_real_change(fixture_repo, capsys, monkeypatch):
    monkeypatch.setenv("UDEBT_LLM", "fake")
    root = fixture_repo
    cli.main(["--repo", str(root), "init"])
    cli.main(["--repo", str(root), "map"])
    (root / "udebt" / "store.py").write_text("".join(f"STATE_{i} = {i}\n" for i in range(60)))
    git(root, "commit", "-q", "-am", "rewrite the store",
        env={"GIT_AUTHOR_NAME": "Dev Two", "GIT_AUTHOR_EMAIL": "dev2@example.com"})  # fmt: skip
    capsys.readouterr()
    args = ["--repo", str(root), "--as-user", "dev2@example.com", "risk", "HEAD~1..HEAD",
            "--check-run"]  # fmt: skip
    assert cli.main(args) == 0
    advisory = json.loads(capsys.readouterr().out)
    assert advisory["conclusion"] == "success" and "storage" in advisory["output"]["summary"]
    cli.main([*args, "--soft-block"])
    blocked = json.loads(capsys.readouterr().out)
    level = blocked["output"]["title"].split()[0]
    if level in ("HIGH", "OUTSIDE"):
        assert blocked["conclusion"] == "failure"
        cli.main([*args, "--soft-block", "--ack", "lena"])
        assert json.loads(capsys.readouterr().out)["conclusion"] == "success"
    else:
        pytest.fail(f"expected a heavy change by a newcomer to be HIGH: {blocked['output']}")
