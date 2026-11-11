import subprocess
from pathlib import Path

import pytest


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """A tiny repo with two 'units' and a few commits of churn."""
    root = tmp_path / "proj"
    (root / "udebt").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Dev One")
    git(root, "config", "user.email", "dev1@example.com")
    (root / "udebt" / "store.py").write_text("STATE = {}\n")
    (root / "udebt" / "scoring.py").write_text("def debt(i, u):\n    return i * (1 - u)\n")
    (root / "README.md").write_text("# proj\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    for i in range(3):
        (root / "udebt" / "store.py").write_text(f"STATE = {{'v': {i}}}\n")
        git(root, "commit", "-q", "-am", f"store change {i}")
    return root
