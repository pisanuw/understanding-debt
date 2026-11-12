"""Import edges between tracked files, one parser per language.

Each parser is one function registered for its file extensions with `@language(...)`. It
receives the importing file's path, its text and a `FileIndex` over the repository, and
returns the repository files it imports plus the names of external packages it uses.
Adding a language (C++, Rust and Java are next) is one more decorated function.

Parsers are deliberately cheap: Python uses `ast`, JS/TS a regex over import, export-from,
require and dynamic import. Anything that does not resolve to a tracked file is external.
"""

from __future__ import annotations

import ast
import posixpath
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

MAX_BYTES = 512_000  # skip very large (usually generated) sources


@dataclass
class Imports:
    internal: set[str] = field(default_factory=set)  # tracked files imported
    external: set[str] = field(default_factory=set)  # third-party top-level names


Parser = Callable[[str, str, "FileIndex"], Imports]
PARSERS: dict[str, Parser] = {}


def language(*exts: str) -> Callable[[Parser], Parser]:
    """Register a parser for files ending in any of `exts`."""

    def deco(fn: Parser) -> Parser:
        for e in exts:
            PARSERS[e] = fn
        return fn

    return deco


def parser_for(path: str) -> Parser | None:
    return PARSERS.get(posixpath.splitext(path)[1].lower())


class FileIndex:
    """Lookups over the tracked files that parsers use to resolve an import."""

    def __init__(self, files: list[str]):
        self.files = set(files)
        # dotted module suffix -> [(root dir, file)], e.g. "agent.turn" -> [("ypdsa/", ...)]
        self.modules: dict[str, list[tuple[str, str]]] = {}
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            parts = f[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts or not all(p.isidentifier() for p in parts):
                continue
            for i in range(len(parts)):
                root = "/".join(parts[:i]) + ("/" if i else "")
                self.modules.setdefault(".".join(parts[i:]), []).append((root, f))

    def python_module(self, dotted: str, importer: str) -> str | None:
        """The file a Python absolute import names, as the importer would see it."""
        cands = self.modules.get(dotted)
        if not cands:
            return None
        for root, f in cands:
            if root == "":
                return f
        # importable if the candidate's root is an ancestor of the importer (run from there)
        ancestors = [(root, f) for root, f in cands if importer.startswith(root)]
        if ancestors:
            return max(ancestors, key=lambda rf: len(rf[0]))[1]
        # tests and scripts often put a source dir on sys.path; accept an unambiguous match
        files = {f for _, f in cands}
        if len(files) == 1 and dotted.split(".")[0] not in sys.stdlib_module_names:
            return files.pop()
        return None

    def first(self, *cands: str) -> str | None:
        for c in cands:
            c = posixpath.normpath(c)
            if c in self.files:
                return c
        return None


# -- Python -------------------------------------------------------------------------


@language(".py", ".pyi")
def python_imports(path: str, text: str, index: FileIndex) -> Imports:
    out = Imports()
    try:
        tree = ast.parse(text, filename=path)
    except (SyntaxError, ValueError):
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                _py_absolute(a.name, path, index, out)
        elif isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names if a.name != "*"]
            if node.level:
                base = path.split("/")[:-1]
                up = node.level - 1
                base = base[: len(base) - up] if up <= len(base) else []
                mod = node.module.split(".") if node.module else []
                stem = "/".join(base + mod)
                hit = False
                for n in names:
                    f = index.first(f"{stem}/{n}.py", f"{stem}/{n}/__init__.py")
                    if f:
                        out.internal.add(f)
                        hit = True
                if not hit and stem:
                    f = index.first(f"{stem}.py", f"{stem}/__init__.py")
                    if f:
                        out.internal.add(f)
            elif node.module:
                hit = False
                for n in names:
                    f = index.python_module(f"{node.module}.{n}", path)
                    if f:
                        out.internal.add(f)
                        hit = True
                if not hit:
                    _py_absolute(node.module, path, index, out)
    out.internal.discard(path)
    return out


def _py_absolute(dotted: str, importer: str, index: FileIndex, out: Imports) -> None:
    f = index.python_module(dotted, importer)
    if f:
        out.internal.add(f)
        return
    top = dotted.split(".")[0]
    if top and top not in sys.stdlib_module_names and not top.startswith("_"):
        out.external.add(top)


# -- JavaScript / TypeScript -----------------------------------------------------------

_JS_SPEC = re.compile(
    r"""(?:\bimport\s+(?:[\w*{}\s,$]+?\s+from\s+)?|\bexport\s+[\w*{}\s,$]+?\s+from\s+|"""
    r"""\brequire\s*\(\s*|\bimport\s*\(\s*)['"]([^'"\n]+)['"]"""
)
JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")


@language(*JS_EXTS)
def js_imports(path: str, text: str, index: FileIndex) -> Imports:
    out = Imports()
    here = posixpath.dirname(path)
    for spec in _JS_SPEC.findall(text):
        spec = spec.split("?")[0]
        if spec.startswith("."):
            base = posixpath.normpath(posixpath.join(here, spec))
        elif spec.startswith(("@/", "~/")):
            src = _nearest_src(path)
            if src is None:
                continue
            base = posixpath.normpath(posixpath.join(src, spec[2:]))
        else:
            parts = spec.split("/")
            name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
            if not name.startswith("node:"):
                out.external.add(name)
            continue
        f = index.first(
            base,
            *(base + e for e in JS_EXTS),
            *(f"{base}/index{e}" for e in JS_EXTS),
        )
        if f and f != path:
            out.internal.add(f)
    return out


def _nearest_src(path: str) -> str | None:
    parts = path.split("/")[:-1]
    for i in range(len(parts), 0, -1):
        if parts[i - 1] == "src":
            return "/".join(parts[:i])
    return None


# -- driver ------------------------------------------------------------------------------


def scan(root: Path, files: list[str]) -> dict[str, Imports]:
    """file -> its imports, for every tracked file a registered parser understands."""
    index = FileIndex(files)
    out: dict[str, Imports] = {}
    for f in files:
        parse = parser_for(f)
        if parse is None:
            continue
        p = root / f
        try:
            if p.stat().st_size > MAX_BYTES:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out[f] = parse(f, text, index)
    return out
