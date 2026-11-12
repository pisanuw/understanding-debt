"""Replay, snapshots, timeline, export and the dashboard (Phase 3)."""

import json
import re
import subprocess

import pytest
from conftest import git

from udebt import cli, replay
from udebt.ledger import Cutoff, Ledger
from udebt.provenance import Commit
from udebt.quiz import compute_scores
from udebt.store import Store

DEV2 = {"GIT_AUTHOR_NAME": "Dev Two", "GIT_AUTHOR_EMAIL": "dev2@example.com"}


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setenv("UDEBT_LLM", "fake")


def run(root, capsys, *args):
    rc = cli.main(["--repo", str(root), *args])
    return rc, capsys.readouterr().out


def _mapped(root, capsys):
    run(root, capsys, "init")
    run(root, capsys, "map")
    return Store(root)


def _c(sha, parents, date="2026-01-01T00:00:00+00:00"):
    return Commit(sha, date, "a", "a@x", sha, "", ["f"], 1, 0, parents=parents)


def test_branch_commits_land_with_their_merge():
    # a - b - m (merge of x <- y) - d ; y, x on a branch off a
    cs = [_c("a", []), _c("y", ["a"]), _c("b", ["a"]), _c("x", ["y"]), _c("m", ["b", "x"])]
    cs.append(_c("d", ["m"]))
    line, arrival = replay.first_parent(cs, "d")
    assert line == ["a", "b", "m", "d"]
    pos = {c.sha: i for i, c in enumerate(cs)}
    assert arrival[pos["y"]] == arrival[pos["x"]] == arrival[pos["m"]] == 2
    assert arrival[pos["b"]] == 1 and arrival[pos["d"]] == 3
    cut = Cutoff(None, "b", 1, arrival)
    assert [c.sha for i, c in enumerate(cs) if cut.has(i)] == ["a", "b"]


def test_parse_every():
    assert replay.parse_every(None) == ("week", 0.0)
    assert replay.parse_every("day") == ("day", 0.0)
    assert replay.parse_every("mass:2.5") == ("mass", 2.5)
    for bad in ("month", "mass:0"):
        with pytest.raises(SystemExit):
            replay.parse_every(bad)


def test_scores_at_head_equal_status_scores(mixed_repo, capsys):
    """The cut-off is the only difference: at HEAD it changes nothing."""
    root = mixed_repo
    store = _mapped(root, capsys)
    h = replay.load(root, store)
    last = len(h.fp) - 1
    for dev in ("dev1@example.com", "dev2@example.com"):
        plain = compute_scores(root, store, h.units, dev, [], h.commits, h.weights)
        cut = compute_scores(
            root, store, h.units, dev, [], h.commits, h.weights,
            ledger=Ledger(h.commits, h.units), cutoff=h.cutoff(last),
        )  # fmt: skip
        assert [(s.unit_id, round(s.understanding, 9)) for s in plain] == [
            (s.unit_id, round(s.understanding, 9)) for s in cut
        ]


def test_replay_against_the_current_map(mixed_repo, capsys):
    root = mixed_repo
    store = _mapped(root, capsys)
    h = replay.load(root, store)
    assert h.devs == ["dev1@example.com", "dev2@example.com"]  # not the agent
    first = replay.snapshot_at(h, h.fp_time[0], 0)
    # at the first commit every unit already has a file, and only dev1 has written anything
    assert set(first["units"]) == {"scoring", "storage"} and first["commits"] == 1
    assert first["mass"]["ai"] == 0 and first["mass"]["human"] > 0
    assert first["devs"]["dev2@example.com"]["index"] == 100.0
    end = replay.snapshot_at(h, h.fp_time[-1], len(h.fp) - 1)
    assert end["commits"] == len(h.commits) and end["mass"]["ai"] > 0
    d1 = end["devs"]["dev1@example.com"]
    assert set(d1["und"]) == {"scoring", "storage"}
    assert round(sum(d1["debt"].values()), 1) == d1["index"], "the stack sums to the index"
    # importance is recomputed from the churn up to the point
    assert end["units"]["storage"][0] != first["units"]["storage"][0]


def test_evidence_counts_only_from_when_it_was_recorded(fixture_repo, capsys):
    root = fixture_repo
    store = _mapped(root, capsys)
    head = git(root, "rev-parse", "HEAD").strip()
    store.append_record(
        "dev1@example.com",
        {"type": "quiz", "dev": "dev1@example.com", "unit": "storage", "head": head,
         "verdict": "correct", "confidence": 2, "ts": "2099-01-01T00:00:00+00:00"},
    )  # fmt: skip
    h = replay.load(root, store)
    ts = replay.parse_ts(h.records["dev1@example.com"][0]["ts"])
    before = replay.snapshot_at(h, h.fp_time[-1], len(h.fp) - 1)
    after = replay.snapshot_at(h, ts, len(h.fp) - 1)
    assert before["devs"]["dev1@example.com"]["und"]["storage"] == 0.5  # the prior
    assert after["devs"]["dev1@example.com"]["und"]["storage"] == 1.0
    assert after["devs"]["dev1@example.com"]["quizzed"] == ["storage"]
    # the plan puts a last point at the evidence, after HEAD's commit
    assert replay.plan(h)[-1][0] == ts


def test_snapshot_is_idempotent_and_appends(mixed_repo, capsys):
    root = mixed_repo
    _mapped(root, capsys)
    rc, out = run(root, capsys, "snapshot", "--every", "day")
    assert rc == 0 and "new" in out
    path = root / ".udebt" / "snapshots.json"
    first = path.read_text()
    data = json.loads(first)
    assert data["fingerprint"]["every"] == "day" and data["points"]
    points = [ln for ln in first.splitlines() if ln.startswith('{"commits"')]
    assert len(points) == len(data["points"]), "one point per line"
    rc, out = run(root, capsys, "snapshot")
    assert "(0 new)" in out and path.read_text() == first, "a second run changes nothing"
    # a new commit a day later appends; earlier points stay exactly as they were
    (root / "udebt" / "store.py").write_text("STATE = {'v': 'later'}\n")
    late = "2099-01-02T00:00:00+00:00"
    git(root, "commit", "-q", "-am", "later", env={**DEV2, "GIT_AUTHOR_DATE": late})
    rc, out = run(root, capsys, "snapshot")
    again = json.loads(path.read_text())
    assert len(again["points"]) > len(data["points"])
    assert again["points"][: len(data["points"])] == data["points"]
    assert again["points"][-1]["sha"] == git(root, "rev-parse", "HEAD").strip()
    # a different interval is another series: kept, with a note, until --rebuild
    rc, out = run(root, capsys, "snapshot", "--every", "week")
    assert "--rebuild" in out and json.loads(path.read_text()) == again
    rc, out = run(root, capsys, "snapshot", "--every", "week", "--rebuild")
    assert json.loads(path.read_text())["fingerprint"]["every"] == "week"


def test_risk_events_use_the_understanding_before_the_commit(mixed_repo, capsys):
    root = mixed_repo
    _mapped(root, capsys)
    # dev2, who has only touched scoring, lands a heavy change on storage
    (root / "udebt" / "store.py").write_text(
        "".join(f"def f{i}(x):\n    return x + {i}\n" for i in range(120))
    )
    git(root, "commit", "-q", "-am", "rewrite the store", env=DEV2)
    rc, out = run(root, capsys, "risk", "--history", "--all")
    assert rc == 0 and "rewrite the store" in out
    data = json.loads((root / ".udebt" / "snapshots.json").read_text())
    ev = [e for e in data["risk"]["events"] if e["subject"] == "rewrite the store"]
    assert ev and ev[0]["dev"] == "dev2@example.com" and ev[0]["unit"] == "storage"
    assert ev[0]["understanding"] == 0.0 and ev[0]["level"] in ("HIGH", "CAUTION")
    # the agent's commit is nobody's risk
    assert all(e["klass"] != "ai" for e in data["risk"]["events"])
    rc, out = run(root, capsys, "risk", "--history", "--since", "2999-01-01")
    assert "Risk events: none" in out


def test_timeline_formats(mixed_repo, capsys):
    root = mixed_repo
    _mapped(root, capsys)
    rc, out = run(root, capsys, "timeline")
    assert rc == 0 and "debt index per developer" in out and "dev1@example.com" in out
    assert "Risk events" in out
    rc, out = run(root, capsys, "timeline", "--format", "csv")
    head = out.splitlines()[0].split(",")
    assert head[:5] == ["t", "sha", "commits", "mass", "ai_share"] and "dev2@example.com" in head
    rc, out = run(root, capsys, "timeline", "--dev", "dev1@example.com", "--format", "json")
    data = json.loads(out)
    assert data["columns"][:3] == ["t", "sha", "index"] and "risk_events" in data
    rc, out = run(root, capsys, "timeline", "--unit", "storage", "--format", "csv")
    assert out.splitlines()[0].startswith("t,sha,importance,size,dev1@example.com")
    with pytest.raises(SystemExit):
        run(root, capsys, "timeline", "--unit", "nope")


def test_export_and_answers(fixture_repo, capsys):
    root = fixture_repo
    store = _mapped(root, capsys)
    store.append_record(
        "dev1@example.com",
        {"type": "quiz", "source": "dig", "dev": "dev1@example.com", "unit": "storage",
         "head": "x", "question": "Q?", "reference": "R", "answer": "SECRET", "feedback": "F",
         "verdict": "partial", "confidence": 2},
    )  # fmt: skip
    rc, out = run(root, capsys, "export", "--out", "-")
    data = json.loads(out[: out.rindex("}") + 1])
    assert data["format"] == "udebt-export" and data["snapshots"] and data["contributors"]
    (ev,) = data["evidence"]
    assert ev["type"] == "dig" and ev["question"] == "Q?" and "answer" not in ev
    assert "SECRET" not in out
    assert data["settings"]["units_replayed_as_defined_now"] is True
    rc, out = run(root, capsys, "export", "--with-answers", "--out", "-")
    assert "SECRET" in out
    rc, out = run(root, capsys, "export", "--format", "csv")
    files = sorted(p.name for p in (root / ".udebt" / "export").glob("*.csv"))
    assert "snapshots.csv" in files and "risk_events.csv" in files and len(files) == 8
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".udebt/export/snapshots.csv"], cwd=root
    ).returncode
    assert ignored == 0, "the export is derived and may hold answers: gitignored"


def test_dashboard_is_one_self_contained_file(mixed_repo, capsys):
    root = mixed_repo
    _mapped(root, capsys)
    rc, out = run(root, capsys, "dashboard")
    assert rc == 0 and "Wrote" in out
    page = (root / ".udebt" / "dashboard" / "index.html").read_text()
    assert page.startswith("<!doctype html>") and "prefers-color-scheme: dark" in page
    # nothing loaded from anywhere: no external src or href, no CDN, no web font
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", page)
    assert "@import" not in page and "url(" not in page
    blob = re.search(
        r'<script type="application/json" id="udebt-data">(.*?)</script>', page, re.S
    ).group(1)
    data = json.loads(blob)
    assert data["repo"] == root.name and data["snapshots"] and data["devs"]
    assert {"risk_events", "heavy_commits", "contributors", "units", "evidence"} <= set(data)
    # a hostile string in the data cannot close the script element
    from udebt.dashboard import blob as make_blob

    assert "</script>" not in make_blob({"subject": "</script><script>alert(1)"})
