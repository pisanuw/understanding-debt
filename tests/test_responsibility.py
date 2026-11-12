import json

from udebt import responsibility as r
from udebt.mapping import commits_for, mappable_files, weights_for
from udebt.provenance import Commit
from udebt.store import Config, Store
from udebt.weights import CommitWeight


def test_codeowners_patterns_follow_github():
    rules = r.parse_codeowners(
        "# comment\n"
        "*                 @all\n"
        "*.js              @js   # trailing comment\n"
        "/build/logs/      @logs\n"
        "docs/*            @docs\n"
        "apps/             @apps\n"
        "/scripts/run.sh   ops@example.com\n"
        "**/migrations     @db\n"
    )
    assert r.owners_of("README.md", rules) == ["@all"]
    assert r.owners_of("src/a/b.js", rules) == ["@js"]
    assert r.owners_of("build/logs/x/y.txt", rules) == ["@logs"]
    assert r.owners_of("deep/build/logs/x.txt", rules) == ["@all"]  # anchored
    assert r.owners_of("docs/a.md", rules) == ["@docs"]
    assert r.owners_of("docs/sub/b.md", rules) == ["@all"]  # docs/* is one level only
    assert r.owners_of("x/apps/y/z.py", rules) == ["@apps"]  # unanchored dir, any depth
    assert r.owners_of("scripts/run.sh", rules) == ["ops@example.com"]
    assert r.owners_of("svc/migrations/001.sql", rules) == ["@db"]
    assert r.owners_of("x.py", r.parse_codeowners("")) == []


def test_owner_emails_mapping_and_pending():
    cfg = Config(
        identities={"bob@example.com": ["123+bob@users.noreply.github.com"]},
        handles={"@lead": "Lead@Example.com", "@team/web": ["a@x.com", "b@x.com"], "@carol": None},
    )
    known = {"123+bob@users.noreply.github.com", "dana@users.noreply.github.com"}
    assert r.owner_emails("someone@corp.com", cfg, known) == ["someone@corp.com"]
    assert r.owner_emails("@lead", cfg, known) == ["lead@example.com"]
    assert r.owner_emails("@team/web", cfg, known) == ["a@x.com", "b@x.com"]
    assert r.owner_emails("@bob", cfg, known) == ["bob@example.com"]  # noreply + alias
    assert r.owner_emails("@dana", cfg, known) == ["dana@users.noreply.github.com"]
    assert r.owner_emails("@carol", cfg, known) is None  # placeholder still empty
    assert r.owner_emails("@org/other", cfg, known) is None


def test_footprint_ignores_ai_and_counts_assisted_in_full():
    def c(sha, email, klass):
        return Commit(
            sha=sha,
            date="",
            author_name="",
            author_email=email,
            subject="",
            body="",
            files=["a/x.py"],
            file_lines={"a/x.py": 10},
            klass=klass,
        )

    commits = [
        c("1", "h@x", "human"),
        c("2", "noreply@anthropic.com", "ai"),
        c("3", "k@x", "assisted"),
    ]
    w = {x.sha: CommitWeight(x.sha, 0.5) for x in commits}
    share, mass = r.footprint(commits, w, {"paths": ["a"]}, Config())
    assert share == {"h@x": 0.5, "k@x": 0.5} and mass["h@x"] == 0.5
    assert r.footprint(commits[1:2], w, {"paths": ["a"]}, Config()) == ({}, {})


def test_effective_scope_precedence():
    team = {"api": "blackbox", "db": "architectural", "api/auth": "architectural"}
    assert r.effective_scope("db", None, team, {}) == ("architectural", "team")
    assert r.effective_scope("api/auth", "api", team, {}) == ("blackbox", "team")  # parent
    assert r.effective_scope("api/auth", "api", team, {"api": "architectural"}) == (
        "architectural",
        "you",
    )
    assert r.effective_scope("db", None, team, {"db": "blackbox"}) == ("blackbox", "you")


UNITS = [
    {"id": "db", "name": "DB", "paths": ["shop/db", "tests/test_models.py"]},
    {
        "id": "web",
        "name": "Web",
        "paths": ["web/src"],
        "children": [
            {"id": "web/client", "name": "client", "paths": ["web/src/client.js"]},
            {"id": "web/views", "name": "views", "paths": ["web/src/App.jsx", "web/src/Cart.jsx"]},
        ],
    },
    {"id": "api", "name": "API", "paths": ["shop/api"]},
]


def test_seed_from_codeowners_then_footprint(layered_repo):
    root = layered_repo
    store = Store(root)
    store.init()
    cfg = store.config
    cfg.identities = {"bob@example.com": ["123+bob@users.noreply.github.com"]}
    store.save_config(cfg)
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    files = mappable_files(root)
    rep = r.seed(root, store, UNITS, commits, weights, files)
    resp = r.load(store)
    assert rep.codeowners == ".github/CODEOWNERS" and rep.source == "codeowners+footprint"
    assert resp.units("alice@example.com") == {"db": "codeowners"}
    assert resp.units("bob@example.com") == {"web": "codeowners"}  # @bob via noreply alias
    # @lead and @carol have no email: pending, with an empty slot in config for the owner
    # (@lead owns one of db's four files through `*`, under the 30% bar)
    assert rep.pending == {"@carol": ["web"], "@lead": ["api"]}
    assert json.loads(store.config_path.read_text())["handles"] == {"@lead": None, "@carol": None}
    # the lead is not named by email in CODEOWNERS, so their set comes from footprint
    assert set(resp.units("lead@example.com")) <= {"db", "web", "api"}
    assert all(v == "footprint" for v in resp.units("lead@example.com").values())
    assert "noreply@anthropic.com" not in resp.developers  # agents get no set

    # the owner fills in @lead; a plain re-seed now gives the lead their CODEOWNERS units
    cfg = store.config
    cfg.handles["@lead"] = "lead@example.com"
    store.save_config(cfg)
    resp.add("alice@example.com", ["web/client"])  # a lead's curation
    resp.save(store.responsibility_path)
    rep = r.seed(root, store, UNITS, commits, weights, files)
    resp = r.load(store)
    assert resp.units("lead@example.com") == {"api": "codeowners"}
    assert rep.kept == ["alice@example.com"] and "web/client" in resp.units("alice@example.com")
    assert rep.pending == {"@carol": ["web"]}
    # --force re-seeds the curated set but keeps what the lead added by hand
    resp.remove("alice@example.com", ["db"])
    resp.save(store.responsibility_path)
    r.seed(root, store, UNITS, commits, weights, files, force=True)
    resp = r.load(store)
    assert resp.units("alice@example.com") == {"db": "codeowners", "web/client": "manual"}
    assert resp.unassigned(UNITS) == []
    assert resp.holders_of("web/views", UNITS) == ["bob@example.com"]


def test_responsibility_file_roundtrip(tmp_path):
    p = tmp_path / "responsibility.json"
    resp = r.Responsibility()
    assert resp.add("Dev@X.com", ["a", "b"]) == ["a", "b"] and resp.remove("dev@x.com", ["b"]) == [
        "b"
    ]
    resp.set_scope("dev@x.com", "c", "blackbox")
    resp.save(p)
    back = r.Responsibility.load(p)
    assert back.units("dev@x.com") == {"a": "manual"} and back.curated("dev@x.com")
    assert back.scope("dev@x.com") == {"c": "blackbox"}
    back.set_scope("dev@x.com", "c", None)
    assert back.scope("dev@x.com") == {}
    assert back.stale([{"id": "z"}]) == {"dev@x.com": ["a"]}
