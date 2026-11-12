from udebt import scoring
from udebt.history import decision_candidates, rename_shas
from udebt.provenance import (
    Commit,
    classify,
    load_commits,
    load_signatures,
    summarize,
    unit_provenance,
)


def mk(**kw) -> Commit:
    base = dict(
        sha="a" * 40,
        date="2026-01-01T00:00:00+00:00",
        author_name="Dev",
        author_email="dev@example.com",
        subject="s",
        body="",
    )
    return Commit(**{**base, **kw})


def test_classify_builtin_signatures():
    sigs = load_signatures()
    assert classify(mk(author_name="Claude", author_email="noreply@anthropic.com"), sigs) == (
        "ai",
        "claude-code",
    )
    assert classify(mk(body="Co-Authored-By: Claude Opus 5.5 <n@a.com>"), sigs) == (
        "assisted",
        "claude-code",
    )
    assert classify(mk(body="Claude-Session: https://claude.ai/code/session_x"), sigs) == (
        "assisted",
        "claude-code",
    )
    assert classify(mk(body="Co-Authored-By: Copilot <c@github.com>"), sigs) == (
        "assisted",
        "github-copilot",
    )
    assert classify(mk(), sigs) == ("human", None)


def test_extra_signature_from_config():
    sigs = load_signatures([{"name": "mybot", "author_emails": [r"^bot@corp\.com$"]}])
    assert classify(mk(author_email="bot@corp.com"), sigs) == ("ai", "mybot")


def test_load_commits_and_summary(mixed_repo):
    cs = load_commits(mixed_repo)
    assert len(cs) == 7 and cs[0].subject == "initial"  # oldest first
    sm = summarize(cs, "dev1@example.com")
    assert (sm["ai"], sm["assisted"], sm["human"], sm["dev"]) == (1, 1, 5, 5)
    assert sm["agents"] == {"claude-code": 2}
    assert cs[-1].files == ["udebt/scoring.py"] and cs[-1].klass == "human"


def test_unit_provenance_and_prior(mixed_repo):
    cs = load_commits(mixed_repo)
    # scoring.py: initial (dev1), clamp (ai), float literal (dev2)
    p = unit_provenance(cs, ["udebt/scoring.py"], "dev1@example.com")
    assert (p.total, p.ai, p.human, p.dev_human, p.others_since_dev) == (3, 1, 2, 1, 2)
    # unweighted (every commit weighs 1) but split by line share: the initial commit put
    # only part of its lines into scoring.py, the two later ones all of theirs
    assert p.others_since_dev_w == 2 and 2 < p.total_w < 3
    assert abs(p.dev_share - p.dev_w / p.total_w) < 1e-9 and 0 < p.dev_share < 1 / 3
    prior = scoring.prior_understanding(p.dev_share, p.others_since_dev_w, 0.9)
    assert abs(prior - 0.5 * p.dev_share * 0.81) < 1e-9
    # store.py: initial + 3 changes (dev1 human) + persist (dev1 assisted) -> no one else since
    p2 = unit_provenance(cs, ["udebt/store.py"], "dev1@example.com")
    assert (p2.total, p2.dev_human, p2.dev_assisted, p2.others_since_dev) == (5, 4, 1, 0)
    assert 0.85 < p2.dev_share < 0.95  # the assisted commit counts half
    # a stranger has no prior at all
    p3 = unit_provenance(cs, ["udebt/store.py"], "nobody@example.com")
    assert p3.dev_share == 0.0 and p3.others_since_dev == 5


def test_decision_candidates(mixed_repo):
    cs = load_commits(mixed_repo)
    cands = decision_candidates(cs, rename_shas(mixed_repo), max_n=10)
    by_subject = {c["subject"]: c for c in cands}
    assert "initial" in by_subject and "founding" in by_subject["initial"]["reasons"]
    assert "persist to json" in by_subject
    assert "dependencies" in by_subject["persist to json"]["reasons"]
    assert by_subject["persist to json"]["klass"] == "assisted"
    assert "float literal" not in by_subject  # nothing decision-like about it
    assert [c["date"] for c in cands] == sorted(c["date"] for c in cands)


def test_score_unit_prior_vs_quiz():
    u = {"id": "a", "importance": 1.0}
    s = scoring.score_unit(u, [], 0, prior=0.4)
    assert s.source == "prior" and s.understanding == 0.4 and not s.measured
    s = scoring.score_unit(u, [{"verdict": "unknown", "confidence": 1, "ts": "t"}], 0, prior=0.4)
    assert s.source == "quiz" and s.understanding == 0.0, "quiz evidence overrides the prior"
    s = scoring.score_unit(u, [], 0)
    assert s.source == "none" and s.debt == 1.0


def test_identity_aliases(mixed_repo):
    cs = load_commits(mixed_repo)
    # dev2's one commit on scoring.py becomes dev1's once aliased
    p = unit_provenance(cs, ["udebt/scoring.py"], {"dev1@example.com", "dev2@example.com"})
    assert (p.dev_human, p.others_since_dev) == (2, 0)
    assert summarize(cs, {"dev1@example.com", "dev2@example.com"})["dev"] == 6


def test_share_splits_a_commit_across_units(mixed_repo):
    cs = load_commits(mixed_repo)
    persist = next(c for c in cs if c.subject == "persist to json")  # touches store.py + toml
    assert persist.file_lines["pyproject.toml"] == 2
    assert 0 < persist.share(["udebt/store.py"]) < 1
    assert abs(persist.share(["udebt/store.py"]) + persist.share(["pyproject.toml"]) - 1) < 1e-9
    assert persist.share(["nowhere"]) == 0.0
    only = next(c for c in cs if c.subject == "float literal")
    assert only.share(["udebt/scoring.py"]) == 1.0 == only.attribution(["udebt/scoring.py"])
    # attribution is concave: a small line share still carries a visible part of the weight
    assert persist.attribution(["pyproject.toml"]) > persist.share(["pyproject.toml"])
    assert (
        abs(persist.attribution(["pyproject.toml"]) ** 2 - persist.share(["pyproject.toml"])) < 1e-9
    )
