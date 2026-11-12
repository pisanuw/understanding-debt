import subprocess
from pathlib import Path

import pytest


def git(cwd: Path, *args: str, env: dict | None = None) -> str:
    import os

    e = {**os.environ, **(env or {})}
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=e
    ).stdout


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """A tiny repo with two 'units' and a few commits of churn, all by dev1."""
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


AI_ENV = {"GIT_AUTHOR_NAME": "Claude", "GIT_AUTHOR_EMAIL": "noreply@anthropic.com"}


@pytest.fixture
def mixed_repo(fixture_repo: Path) -> Path:
    """fixture_repo plus: an AI-authored commit on scoring, an AI-assisted commit by dev1 on
    store (with a manifest), and a human commit by dev2 on scoring."""
    root = fixture_repo
    (root / "udebt" / "scoring.py").write_text("def debt(i, u):\n    return max(0, i * (1 - u))\n")
    git(
        root,
        "commit",
        "-q",
        "-am",
        "clamp debt\n\nCo-Authored-By: Claude Opus 5.5 <x@y>",
        env=AI_ENV,
    )
    (root / "pyproject.toml").write_text("[project]\nname='proj'\n")
    (root / "udebt" / "store.py").write_text("import json\nSTATE = {}\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "persist to json\n\nCo-Authored-By: Claude Opus 5.5 <x@y>")
    (root / "udebt" / "scoring.py").write_text(
        "def debt(i, u):\n    return max(0.0, i * (1 - u))\n"
    )
    git(
        root,
        "commit",
        "-q",
        "-am",
        "float literal",
        env={"GIT_AUTHOR_NAME": "Dev Two", "GIT_AUTHOR_EMAIL": "dev2@example.com"},
    )
    return root
