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


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


ALICE = {"GIT_AUTHOR_NAME": "Alice", "GIT_AUTHOR_EMAIL": "alice@example.com"}
BOB = {"GIT_AUTHOR_NAME": "Bob", "GIT_AUTHOR_EMAIL": "123+bob@users.noreply.github.com"}


@pytest.fixture
def layered_repo(tmp_path: Path) -> Path:
    """A small app with real structure: a Python package (api imports db), a JS frontend,
    tests, a CODEOWNERS file, three human authors, an AI commit and a cross-unit commit.

    shop/api/  routes -> auth -> tokens; routes -> shop.db.models   (lead, then the AI)
    shop/db/   models -> session                                     (alice)
    web/src/   App, Cart -> client                                   (bob, a GitHub handle)
    CODEOWNERS: * @lead, /shop/db/ alice@example.com, /web/ @bob @carol
    """
    root = tmp_path / "shop"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Lead")
    git(root, "config", "user.email", "lead@example.com")
    _write(
        root,
        {
            "shop/__init__.py": "",
            "shop/api/__init__.py": "",
            "shop/api/routes.py": (
                "import fastapi\nfrom shop.api import auth\nfrom shop.db.models import Order\n"
            ),
            "shop/api/auth.py": "from .tokens import sign\n\ndef check(t):\n    return sign(t)\n",
            "shop/api/tokens.py": "import hmac\n\ndef sign(x):\n    return x\n",
            "shop/db/__init__.py": "",
            "shop/db/models.py": "import sqlalchemy\nfrom .session import engine\n\nclass Order:\n"
            "    pass\n",
            "shop/db/session.py": "engine = None\n",
            "web/src/App.jsx": "import React from 'react'\nimport { get } from './client'\n",
            "web/src/Cart.jsx": "import { get } from './client.js'\n",
            "web/src/client.js": "export const get = () => fetch('/api')\n",
            "tests/test_models.py": "from shop.db.models import Order\n",
            "README.md": "# shop\n",
            ".github/CODEOWNERS": "# owners\n*            @lead\n/shop/db/    alice@example.com\n"
            "/web/        @bob @carol\n",
        },
    )
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial shop")
    for i in range(3):
        _write(
            root,
            {
                "shop/db/models.py": "import sqlalchemy\nfrom .session import engine\n\n"
                f"class Order:\n    version = {i}\n",
                "shop/db/session.py": f"engine = {i}\n",
            },
        )
        git(root, "commit", "-q", "-am", f"db: model change {i}", env=ALICE)
    for i in range(2):
        _write(
            root,
            {
                "web/src/App.jsx": f"import React from 'react'\nimport {{ get }} from './client'\n"
                f"// v{i}\n",
                "web/src/Cart.jsx": f"import {{ get }} from './client.js'\n// v{i}\n",
                "web/src/client.js": f"export const get = () => fetch('/api/v{i}')\n",
            },
        )
        git(root, "commit", "-q", "-am", f"web: client v{i}", env=BOB)
    _write(
        root,
        {
            "shop/api/routes.py": "import fastapi\nfrom shop.api import auth\n"
            "from shop.db.models import Order\n\nROUTES = ['/orders']\n",
            "shop/api/auth.py": "from .tokens import sign\n\ndef check(t):\n"
            "    return sign(t) is not None\n",
            "shop/api/tokens.py": "import hmac\n\ndef sign(x):\n    return hmac.new(b'k', x)\n",
        },
    )
    git(root, "commit", "-q", "-am", "api: sign tokens with hmac", env=AI_ENV)
    _write(
        root,
        {
            "shop/db/models.py": "import sqlalchemy\nfrom .session import engine\n\n"
            "class Order:\n    version = 9\n    total = 0\n",
            "web/src/client.js": "\n".join(
                f"export const get{i} = () => fetch('/api/{i}')" for i in range(40)
            )
            + "\n",
        },
    )
    git(
        root,
        "commit",
        "-q",
        "-am",
        "orders: add totals end to end\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
        env=ALICE,
    )
    return root
