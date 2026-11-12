from udebt.contributors import contributors, grouped
from udebt.provenance import load_commits


def test_labels_on_mixed_repo(mixed_repo):
    cs = load_commits(mixed_repo)
    cons = contributors(cs, {}, {"dev1@example.com"})
    by_name = {c.name: c for c in cons}
    assert by_name["Claude"].label == "AI"
    dev1 = by_name["Dev One"]
    assert dev1.is_you and dev1.label == "Joint human-AI"  # 1 of 5 commits assisted
    assert by_name["Dev Two"].label == "Human" and "too few" in by_name["Dev Two"].reasons[0]
    g = grouped(cons)
    assert [c.name for c in g["AI"]] == ["Claude"]


def test_aliases_merge_identities(mixed_repo):
    cs = load_commits(mixed_repo)
    cons = contributors(cs, {"dev1@example.com": ["dev2@example.com"]}, set())
    assert len(cons) == 2
    dev1 = next(c for c in cons if c.name == "Dev One")
    assert sorted(dev1.emails) == ["dev1@example.com", "dev2@example.com"]
    assert dev1.commits == 6
