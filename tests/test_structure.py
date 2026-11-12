from udebt import repo, structure
from udebt.mapping import commits_for, weights_for
from udebt.provenance import Commit
from udebt.store import Store
from udebt.weights import CommitWeight


def mk(sha: str, files: list[str]) -> Commit:
    return Commit(
        sha=sha,
        date="2026-01-01T00:00:00+00:00",
        author_name="d",
        author_email="d@x",
        subject="s",
        body="",
        files=files,
        file_lines={f: 1 for f in files},
    )


def test_cochange_weights_and_reformat_cutoff():
    nodes = {f"f{i}.py" for i in range(40)}
    commits = [
        mk("a", ["f0.py", "f1.py"]),
        mk("b", ["f0.py", "f1.py", "f2.py"]),
        mk("big", sorted(nodes)),  # a 40-file reformat glues nothing
    ]
    w = {c.sha: CommitWeight(c.sha, 1.0) for c in commits}
    g, touches, eligible = structure.cochange_graph(commits, w, nodes, max_files=30)
    assert eligible == 2 and touches["f0.py"] == 2
    assert g.weight("f0.py", "f1.py") == 1.0 + 0.5  # w/(n-1) per commit
    assert g.weight("f1.py", "f2.py") == 0.5 and g.weight("f3.py", "f4.py") == 0.0
    zero = {c.sha: CommitWeight(c.sha, 0.0) for c in commits}
    assert structure.cochange_graph(commits, zero, nodes)[0].n_edges() == 0


def test_build_separates_layers_and_attaches_docs(layered_repo):
    root = layered_repo
    store = Store(root)
    store.init()
    commits = commits_for(root, store)
    s = structure.build(root, repo.tracked_files(root), commits, weights_for(root, store, commits))
    by_file = {f: c.id for c in s.clusters for f in c.files}
    # db, web and api code land in three different clusters
    assert (
        len({by_file["shop/db/models.py"], by_file["web/src/App.jsx"], by_file["shop/api/auth.py"]})
        == 3
    )
    assert by_file["shop/db/session.py"] == by_file["shop/db/models.py"]
    assert by_file["web/src/client.js"] == by_file["web/src/Cart.jsx"]
    # the test file follows the code it imports and changes with
    assert by_file["tests/test_models.py"] == by_file["shop/db/models.py"]
    # docs are attached, never clustered themselves
    assert "README.md" in by_file and not structure.is_source("README.md")
    api = next(c for c in s.clusters if "shop/api/auth.py" in c.files)
    assert "fastapi" in dict(api.external) and api.uses[0][0] == by_file["shop/db/models.py"]
    assert api.label.startswith("shop/api/")
    block = structure.prompt_block(s)
    assert f"- {api.id} [shop/api/" in block and "imports fastapi" in block
    assert s.summary()["import_edges"] == s.import_edges > 0
