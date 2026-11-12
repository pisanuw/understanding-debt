from udebt.imports import PARSERS, FileIndex, Imports, js_imports, language, python_imports, scan

FILES = [
    "pkg/__init__.py",
    "pkg/api/__init__.py",
    "pkg/api/routes.py",
    "pkg/api/auth.py",
    "pkg/db/models.py",
    "backend/rag.py",
    "backend/main.py",
    "tests/test_rag.py",
    "a/config.py",
    "b/config.py",
    "web/src/App.jsx",
    "web/src/client.js",
    "web/src/lib/index.ts",
    "web/src/Cart.tsx",
]
IDX = FileIndex(FILES)


def test_python_absolute_relative_and_submodule_imports():
    src = (
        "import os, json\nimport requests\nfrom pkg.db.models import Order\n"
        "from pkg.api import auth\nfrom . import auth as a2\nfrom ..db import models\n"
        "from __future__ import annotations\n"
    )
    imp = python_imports("pkg/api/routes.py", src, IDX)
    assert imp.internal == {"pkg/db/models.py", "pkg/api/auth.py"}
    assert imp.external == {"requests"}  # stdlib and __future__ are not packages


def test_python_bare_sibling_imports_and_ambiguity():
    # backend/ is run as the working directory: `import rag` means backend/rag.py
    assert python_imports("backend/main.py", "import rag\n", IDX).internal == {"backend/rag.py"}
    # tests put backend/ on sys.path; a unique match is accepted
    assert python_imports("tests/test_rag.py", "import rag\n", IDX).internal == {"backend/rag.py"}
    # two config.py files and no ancestor relation: no guess
    assert python_imports("tests/test_rag.py", "import config\n", IDX).internal == set()
    assert python_imports("a/x.py", "import config\n", FileIndex([*FILES, "a/x.py"])).internal == {
        "a/config.py"
    }
    assert python_imports("bad.py", "def (:\n", IDX) == Imports()


def test_js_relative_resolution_and_packages():
    src = (
        "import React, { useState } from 'react'\n"
        "import { get } from './client'\n"
        "import lib from './lib'\n"
        'export { x } from "./Cart"\n'
        "const z = require('@scope/pkg/deep')\n"
        "const lazy = import('./client.js')\n"
        "import 'node:fs'\n"
        "import '@/lib'\n"
    )
    imp = js_imports("web/src/App.jsx", src, IDX)
    assert imp.internal == {"web/src/client.js", "web/src/lib/index.ts", "web/src/Cart.tsx"}
    assert imp.external == {"react", "@scope/pkg"}


def test_adding_a_language_is_one_function(tmp_path):
    @language(".fake")
    def fake_imports(path, text, index):
        return Imports(internal={ln.split()[1] for ln in text.splitlines() if ln.startswith("use")})

    try:
        (tmp_path / "x.fake").write_text("use y.fake\n")
        (tmp_path / "y.fake").write_text("")
        out = scan(tmp_path, ["x.fake", "y.fake"])
        assert out["x.fake"].internal == {"y.fake"}
    finally:
        PARSERS.pop(".fake")
