from udebt import repo


def test_root_head_identity(fixture_repo):
    assert repo.find_root(fixture_repo / "udebt") == fixture_repo
    assert len(repo.head(fixture_repo)) == 40
    assert repo.identity(fixture_repo) == ("Dev One", "dev1@example.com")
    assert repo.slug("Dev.One+x@Example.com") == "dev-one-x-example-com"


def test_commit_counts(fixture_repo):
    assert repo.commit_count(fixture_repo, ["udebt/store.py"]) == 4
    assert repo.commit_count(fixture_repo, ["udebt/scoring.py"]) == 1
    assert repo.commit_count(fixture_repo, []) == 0
    first = repo._git(fixture_repo, "rev-list", "--max-parents=0", "HEAD").strip()
    assert repo.commit_count(fixture_repo, ["udebt/store.py"], since=first) == 3
    # a vanished `since` sha falls back to the full count instead of crashing
    assert repo.commit_count(fixture_repo, ["udebt/store.py"], since="0" * 40) == 4
