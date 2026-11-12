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


def test_line_share_ignores_lockfiles_and_generated_files():
    c = mk(
        files=["frontend/package-lock.json", "backend/main.py", "frontend/src/App.jsx"],
        file_lines={
            "frontend/package-lock.json": 2000,
            "backend/main.py": 300,
            "frontend/src/App.jsx": 100,
        },
    )
    assert c.share(["backend"]) == 0.75 and c.share(["frontend"]) == 0.25
    only_lock = mk(files=["yarn.lock"], file_lines={"yarn.lock": 50})
    assert only_lock.share(["yarn.lock"]) == 1.0  # falls back to the file count


def test_classify_more_agents_and_trailers():
    sigs = load_signatures()

    def cls(**kw):
        return classify(mk(**kw), sigs)

    gh = "@users.noreply.github.com"
    # agent author identities, matched case-insensitively
    assert cls(author_name="Copilot", author_email="198982749+Copilot" + gh) == (
        "ai",
        "github-copilot",
    )
    assert cls(author_name="x", author_email="198982749+COPILOT" + gh)[0] == "ai"
    for login, agent in [
        ("devin-ai-integration[bot]", "devin"),
        ("claude[bot]", "claude-code"),
        ("google-labs-jules[bot]", "jules"),
        ("chatgpt-codex-connector[bot]", "openai-codex"),
        ("amazon-q-developer[bot]", "amazon-q"),
    ]:
        assert cls(author_name=login, author_email=f"1+{login}{gh}") == ("ai", agent), login
    assert cls(author_name="opencode", author_email="noreply@opencode.ai") == ("ai", "opencode")
    # vscode: the agent authored it on a developer's behalf
    body = "🤖 Authored with GitHub Copilot on behalf of @dev\n\nCo-authored-by: Copilot <c@x>"
    assert cls(body=body) == ("ai", "github-copilot")
    # Assisted-by (kernel/Fedora/LLVM) names the agent; the first one named wins
    for value, agent in [
        ("Claude Code", "claude-code"),
        ("Claude:claude-opus-4 coccinelle", "claude-code"),
        ("Copilot <c@github.com>", "github-copilot"),
        ("Gemini CLI 0.3", "gemini"),
        ("OpenCode with Claude Opus 4.6", "opencode"),
        ("GitHub Copilot (claude-sonnet-4-6)", "github-copilot"),
        ("aider 0.80 (openai/gpt-4o)", "aider"),  # agentic-commits form
        ("goose 1.2 (anthropic/claude-sonnet-4)", "assisted-by"),  # unknown agent
        ("Qwen3-Coder", "assisted-by"),
    ]:
        assert cls(body=f"Fix it\n\nAssisted-by: {value}") == ("assisted", agent), value
    assert cls(body="Assisted-By: claude code") == ("assisted", "claude-code")
    assert cls(body="Assisted-by: Jane Doe <jane@example.org>") == ("human", None)
    # project trailers
    assert cls(body="Generated-by: Claude Opus following the guidelines") == (
        "assisted",
        "claude-code",
    )
    assert cls(body="Made-with: Cursor") == ("assisted", "cursor")
    assert cls(body="Generated-by: hack/update-codegen.sh") == ("human", None)
    assert cls(body="Co-Authored-By: Devin AI <1+devin-ai-integration[bot]@x>") == (
        "assisted",
        "devin",
    )


def test_bot_class():
    sigs = load_signatures()
    gh = "@users.noreply.github.com"
    for name, email, bot in [
        ("dependabot[bot]", "49699333+dependabot[bot]" + gh, "dependabot"),
        ("renovate[bot]", "29139614+renovate[bot]" + gh, "renovate"),
        ("github-actions[bot]", "41898282+github-actions[bot]" + gh, "github-actions"),
        ("semantic-release-bot", "semantic-release-bot@martynus.net", "semantic-release"),
        ("pre-commit-ci[bot]", "66853113+pre-commit-ci[bot]" + gh, "bot"),
        ("Release pipeline", "ci@example.com", "bot"),
        ("ci", "bot@example.com", "bot"),
    ]:
        assert classify(mk(author_name=name, author_email=email), sigs) == ("bot", bot), name
    # people whose names merely contain the letters are not bots
    assert classify(mk(author_name="Talbot", author_email="robot@example.com"), sigs)[0] == (
        "human"
    )
    # an agent's own [bot] app is an agent, and automation committing an agent's work is ai
    assert classify(mk(author_name="claude[bot]", author_email="x@y"), sigs)[0] == "ai"
    body = "Co-Authored-By: Claude <noreply@anthropic.com>"
    assert classify(mk(author_name="github-actions[bot]", body=body), sigs) == (
        "ai",
        "claude-code",
    )
    # config bot_signatures extend the list and name the bot
    from udebt.provenance import load_bots

    bots = load_bots([{"name": "deploybot", "author_emails": [r"^deploy@corp\.com$"]}])
    assert classify(mk(author_email="DEPLOY@corp.com"), sigs, bots) == ("bot", "deploybot")


def test_bots_count_for_nobody(mixed_repo):
    """A dependabot commit on scoring.py neither builds anyone's share nor decays the prior,
    and is left out of every share's denominator."""
    from conftest import git

    from udebt.quiz import mass_since

    before = load_commits(mixed_repo)
    p0 = unit_provenance(before, ["udebt/scoring.py"], "dev2@example.com")
    (mixed_repo / "udebt" / "scoring.py").write_text("def debt(i, u):\n    return 0.0\n")
    git(
        mixed_repo,
        "commit",
        "-q",
        "-am",
        "Bump the scoring stub",
        env={
            "GIT_AUTHOR_NAME": "dependabot[bot]",
            "GIT_AUTHOR_EMAIL": "49699333+dependabot[bot]@users.noreply.github.com",
        },
    )
    cs = load_commits(mixed_repo)
    assert cs[-1].klass == "bot" and cs[-1].agent == "dependabot"
    sm = summarize(cs, "dev1@example.com")
    assert (sm["bot"], sm["bots"], sm["human"]) == (1, {"dependabot": 1}, 5)
    p = unit_provenance(cs, ["udebt/scoring.py"], "dev2@example.com")
    assert (p.bot, p.total, p.others_since_dev) == (1, p0.total, 0)
    assert p.total_w == p0.total_w and p.dev_share == p0.dev_share and p.bot_w == 1.0
    assert mass_since(cs, {}, ["udebt/scoring.py"], before[-1].sha) == 0.0
    from udebt.contributors import contributors

    bot = next(c for c in contributors(cs, {}, set()) if c.name == "dependabot[bot]")
    assert bot.label == "Bot" and bot.bot == 1 and "dependabot" in bot.reasons[0]


def test_directed_agent_commits_count_as_assisted(mixed_repo):
    """An agent commit from a developer's session counts as their assisted work: half weight
    towards their share, and it does not erode their prior. It stays `ai`."""
    from conftest import AI_ENV, git

    from udebt.provenance import assign_directed
    from udebt.responsibility import footprint
    from udebt.store import Config

    (mixed_repo / "udebt" / "scoring.py").write_text("def debt(i, u):\n    return i - i * u\n")
    git(
        mixed_repo,
        "commit",
        "-q",
        "-am",
        "simplify debt\n\nClaude-Session: https://claude.ai/code/session_01Abc",
        env=AI_ENV,
    )
    cs = load_commits(mixed_repo)
    paths = ["udebt/scoring.py"]
    plain = unit_provenance(cs, paths, "dev2@example.com")
    assert plain.others_since_dev == 1 and plain.dev_directed == 0
    assign_directed(cs, {"Dev2@example.com": {"sessions": ["session_01Abc"]}})
    last = cs[-1]
    assert last.klass == "ai" and last.directed_by == "dev2@example.com"
    p = unit_provenance(cs, paths, "dev2@example.com")
    assert (p.dev_directed, p.others_since_dev, p.others_since_dev_w) == (1, 0, 0)
    assert abs(p.dev_w - (plain.dev_w + 0.5)) < 1e-9 and p.ai == plain.ai  # still ai
    assert summarize(cs, "dev2@example.com")["directed"] == 1
    # the other agent commit (no session) is claimed only by a wholesale agent claim
    assign_directed(cs, {"dev1@example.com": {"agents": ["claude-code"]}})
    assert all(c.directed_by == "dev1@example.com" for c in cs if c.klass == "ai")
    # footprint: the directed commit counts for its developer at half weight
    assign_directed(cs, {"dev2@example.com": {"sessions": ["session_01Abc"]}})
    share, mass = footprint(cs, {}, {"paths": paths}, Config())
    assert mass["dev2@example.com"] == 1.0 + 0.5  # "float literal" in full, plus half
    assert "noreply@anthropic.com" not in mass


def test_shared_file_is_split_between_units(mixed_repo):
    """A file two units list gives each half its lines, in every caller."""
    from udebt.mapping import unit_mass
    from udebt.quiz import mass_since
    from udebt.units import Membership

    units = [
        {"id": "a", "paths": ["udebt/scoring.py", "README.md"]},
        {"id": "b", "paths": ["udebt/scoring.py", "udebt/store.py"]},
        {
            "id": "c",
            "paths": ["udebt/store.py"],
            "children": [
                {"id": "c/x", "paths": ["udebt/store.py"]},
                {"id": "c/y", "paths": ["udebt/store.py"]},
            ],
        },
    ]
    members = Membership(units)
    cs = load_commits(mixed_repo)
    only = next(c for c in cs if c.subject == "float literal")  # scoring.py alone
    assert only.share(["udebt/scoring.py"], split=members.split("a")) == 0.5
    assert abs(only.attribution(["udebt/scoring.py"], split=members.split("b")) - 0.5**0.5) < 1e-9
    assert members.split("c")("udebt/store.py") == 0.5  # store.py: units b and c
    assert members.split("c/x")("udebt/store.py") == 0.25  # c's half, split by x and y
    assert members.split("a")("README.md") == 1.0
    full = unit_mass(cs, ["udebt/scoring.py"], {c.sha: _W(1.0) for c in cs})
    half = unit_mass(cs, ["udebt/scoring.py"], {c.sha: _W(1.0) for c in cs}, members.split("a"))
    assert half < full
    p = unit_provenance(cs, ["udebt/scoring.py"], "dev1@example.com", None, members.split("a"))
    assert p.total_w < full
    first = cs[0].sha
    assert mass_since(cs, {}, ["udebt/scoring.py"], first, members.split("a")) < mass_since(
        cs, {}, ["udebt/scoring.py"], first
    )


class _W:
    def __init__(self, weight: float):
        self.weight = weight
