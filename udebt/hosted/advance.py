"""Advance quizzes: questions written ahead of time for the hosted site's visitors who are not
signed in (HOSTING.md decision 10). Such a visitor has no gateway (signed-in people only) and
often no key of their own, so the site serves, next to each sample's static JSON, a file of
questions per unit and ladder level 1-3, each with its reference answer (and, at level 1, the
orientation shown before it). The visitor answers, reads the reference and marks their own
answer (correct, partial, wrong, unknown): a `self-checked` record, with no model call.

    udebt worker quizzes <sample | worker output | load script | rows file> --out <file>
        [--levels 1-3] [--per 3] [--batch 5] [--clone <dir>] [--plan] [--model <name>]

The questions are written with the hosted quiz's own prompt (web/src/core/prompts.ts
`questionsPrompt`, whose rules are udebt/prompts.py's, exported in prompt-parts.json): this
module is a port of it and of what the page and the `/api/questions` function put into it (the
request's caps, `refsForUnit`, the tree fallback, excerpts at the pinned commit within 60,000
characters). `tests/test_advance.py` writes cases to web/test/fixtures/advance-prompt-parity.json,
which vitest checks the TypeScript against, so the two prompts stay the same text. The model is
udebt's `LLM` (the `claude` CLI on the subscription, `UDEBT_LLM=fake` in tests) with no tools:
the code reaches it as excerpts, as on the gateway. One call per level and batch of units.

Levels 4-5 are never written in advance: they need code reading and real grading.

The file (format `udebt-advance-quizzes`, `version` 1, contract kind `quizzes`):

    {"format": "udebt-advance-quizzes", "version": 1, "repo": <url>, "name": ..., "sha": <pin>,
     "generated_at": ..., "levels": [1, 2, 3], "per": 3, "calls": N,
     "questions": [{"unit_id", "level", "question", "variants": [2], "reference_answer",
                    "evidence": [...], "orientation": {...} (level 1 only)}, ...]}
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .. import contract, ladder
from ..prompts import (
    NO_IDENTIFIERS,
    OBJECTIVE_RULE,
    ONE_FACT,
    ORIENTATION_RULE,
    QUIZ_QUESTIONS_SCHEMA,
    REFERENCE_RULE,
    REVEALED_RULE,
    ladder_block,
)
from ..quiz import one_fact
from ..topfiles import _safe as safe_path
from ..topfiles import dir_paths, files_under, tracked_sizes

FORMAT = "udebt-advance-quizzes"
VERSION = 1
LEVELS = (1, 2, 3)  # never 4-5: those need code reading and real grading
PER = 3
BATCH = 5
MAX_TURNS = 4  # no tools: the answer is the CLI's structured output
# web/src/core/excerpts.ts and assemble.ts
TOP_LINES = 60
DEFAULT_LINES = 80
MAX_LINES = 200
MAX_FILE_BYTES = 2_000_000
EXCERPT_BUDGET = 60_000
REFS_PER_UNIT = 4
REFS_PER_REQUEST_UNIT = 6
MAX_UNITS = 12
MAX_PATHS = 3
NO_REFERENCE = "(graded against the code)"
FILE_SHAPED = re.compile(r"\.[A-Za-z0-9]{1,6}$")


class AdvanceError(Exception):
    pass


# -- JavaScript string semantics (the TypeScript counts and slices UTF-16 code units) ---------


def _u16len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _u16slice(s: str, n: int) -> str:
    if _u16len(s) <= n:
        return s
    out, used = [], 0
    for ch in s:
        w = 2 if ord(ch) > 0xFFFF else 1
        if used + w > n:
            break
        out.append(ch)
        used += w
    return "".join(out)


def _str(v: Any, cap: int) -> str:
    return _u16slice(v, cap) if isinstance(v, str) else ""


def _strs(v: Any, n: int, cap: int) -> list[str]:
    return [_u16slice(x, cap) for x in v if isinstance(x, str)][:n] if isinstance(v, list) else []


def _js(v: Any) -> str:
    """`${v ?? ''}` for the values a prompt line shows."""
    return "" if v is None else str(v)


# -- excerpts (web/src/core/excerpts.ts) ------------------------------------------------------


def _num(v: Any) -> float:
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, int | float):
        return float(v)
    try:
        return float(str(v).strip()) if str(v).strip() else 0.0
    except (TypeError, ValueError):
        return math.nan


def clamp_range(start: Any, end: Any) -> tuple[int, int]:
    s_ = _num(start)
    e_ = _num(end)
    s = math.floor(s_) if math.isfinite(s_) and s_ >= 1 else 1
    e = math.floor(e_) if math.isfinite(e_) and e_ >= s else s + DEFAULT_LINES - 1
    return s, min(e, s + MAX_LINES - 1)


def _unit_paths(u: dict[str, Any]) -> list[Any]:
    where = [w.get("path") for w in ((u.get("orientation") or {}).get("where") or [])]
    return [*where, *(u.get("paths") or [])]


def top_refs(files: Iterable[Any], limit: int = REFS_PER_UNIT) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in files:
        p = safe_path(f)
        if p and len(out) < limit and not any(x["path"] == p for x in out):
            out.append({"path": p, "start": 1, "end": TOP_LINES})
    return out


def refs_for_unit(
    u: dict[str, Any], walk: dict[str, Any] | None, limit: int = REFS_PER_UNIT
) -> list[dict[str, Any]]:
    """refsForUnit: the walkthrough's steps, else the top of the files its orientation and paths
    name, else the top of its recorded `files`."""
    out: list[dict[str, Any]] = []

    def add(r: dict[str, Any] | None) -> None:
        if (
            r
            and len(out) < limit
            and not any(x["path"] == r["path"] and x["start"] == r["start"] for x in out)
        ):
            out.append(r)

    for st in (walk or {}).get("steps") or []:
        p = safe_path(st.get("file"))
        if p:
            s, e = clamp_range(st.get("start_line"), st.get("end_line"))
            add({"path": p, "start": s, "end": e})
    if not out:
        for p in _unit_paths(u):
            path = safe_path(p)
            if path and not path.endswith("/") and FILE_SHAPED.search(path):
                add({"path": path, "start": 1, "end": TOP_LINES})
    if not out:
        for r in top_refs(u.get("files") if isinstance(u.get("files"), list) else [], limit):
            add(r)
    return out


def slice_lines(text: str, ref: dict[str, Any]) -> dict[str, Any]:
    lines_all = re.sub(r"\r\n?", "\n", text).split("\n")
    if lines_all and lines_all[-1] == "":
        lines_all.pop()
    start, end = clamp_range(ref["start"], ref["end"])
    want = max(0, min(end, len(lines_all)) - start + 1)
    lines = lines_all[start - 1 : start - 1 + want]
    asked = max(0, min(ref["end"], len(lines_all)) - start + 1)
    return {"path": ref["path"], "start": start, "end": start + len(lines) - 1, "lines": lines,
            "cut": max(0, asked - len(lines))}  # fmt: skip


def format_excerpts(excerpts: list[dict[str, Any]]) -> str:
    out = []
    for x in excerpts:
        head = f"--- {x['path']} lines {x['start']}-{x['end']}"
        body = "\n".join(f"{x['start'] + i:>5} | {ln}" for i, ln in enumerate(x["lines"]))
        out.append(
            f"{head}\n{body}\n      ... {x['cut']} more line(s)" if x["cut"] else f"{head}\n{body}"
        )
    return "\n\n".join(out)


def within_budget(excerpts: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    out, used = [], 0
    for x in excerpts:
        size = _u16len(format_excerpts([x])) + 2
        if used + size > max_chars:
            break
        out.append(x)
        used += size
    return out


# -- orientation (web/src/core/specs.ts orientationOf, orientationText) -----------------------


def orientation_of(u: dict[str, Any]) -> dict[str, Any]:
    o = u.get("orientation")
    if isinstance(o, dict) and o.get("purpose"):
        return o
    parts = [_js(u.get("decision")).strip()]
    if u.get("alternatives"):
        parts.append(f"Not chosen: {str(u['alternatives']).strip()}")
    if u.get("why"):
        parts.append(f"Why: {str(u['why']).strip()}")
    authors: list[str] = []
    for h in u.get("history") or []:
        klass = "?" if h.get("klass") is None else h["klass"]
        who = f"{h.get('author') or '?'} [{klass}]"
        if who not in authors:
            authors.append(who)
    return {
        "purpose": _js(u.get("summary")).strip() or u.get("name") or u.get("id"),
        "decision": " ".join(p for p in parts if p),
        "where": [{"path": p, "what": ""} for p in (u.get("paths") or [])[:MAX_PATHS]],
        "shaped_by": f"From its history: {', '.join(authors[:4])}."
        if authors
        else "The history list does not say.",
        "fallback": True,
    }


def shown(o: dict[str, Any]) -> dict[str, Any]:
    """The orientation as the page shows it before a level 1 question: what it is for, the
    decision, where it lives, who shaped it (no cache keys)."""
    out = {
        "purpose": str(o.get("purpose") or ""),
        "decision": str(o.get("decision") or ""),
        "where": [{"path": str(w.get("path")), "what": str(w.get("what") or "")}
                  for w in o.get("where") or [] if isinstance(w, dict) and w.get("path")],
        "shaped_by": str(o.get("shaped_by") or ""),
    }  # fmt: skip
    return {**out, "fallback": True} if o.get("fallback") else out


def orientation_text(o: dict[str, Any]) -> str:
    where = "; ".join(
        f"{w['path']} ({w['what']})" if w.get("what") else str(w["path"])
        for w in o.get("where") or []
    )
    return (
        f"What it is for: {o['purpose']} The decision: {o['decision']} Where it lives: {where}. "
        f"Who shaped it: {o['shaped_by']}"
    )


# -- the prompt (web/src/core/prompts.ts questionsPrompt, assemble.ts readQuestionsRequest) ---


def normalize_unit(u: dict[str, Any]) -> dict[str, Any]:
    """A question unit as `/api/questions` reads it: every field checked and capped."""
    uid = _str(u.get("id"), 200)
    if not uid:
        raise AdvanceError("Each unit needs an id.")
    hist = u.get("history") if isinstance(u.get("history"), list) else []
    return {
        "id": uid,
        "name": _str(u.get("name"), 300) or uid,
        "parent_name": _str(u.get("parent_name"), 300) or None,
        "level": ladder.level_of({"level": u.get("level")}),
        "summary": _str(u.get("summary"), 2000),
        "decision": _str(u.get("decision"), 3000),
        "alternatives": _str(u.get("alternatives"), 2000),
        "why": _str(u.get("why"), 2000),
        "paths": _strs(u.get("paths"), 40, 400),
        "history": [
            {
                "sha": _str(o.get("sha"), 40),
                "date": _str(o.get("date"), 30),
                "klass": _str(o.get("klass"), 20),
                "subject": _str(o.get("subject"), 200),
            }
            for o in (h if isinstance(h, dict) else {} for h in hist[:5])
        ],  # fmt: skip
        "shown_orientation": _str(u.get("shown_orientation"), 3000) or None,
        "revealed": _strs(u.get("revealed"), 3, 1000),
    }


def _history_line(h: dict[str, Any]) -> str:
    klass = "?" if h.get("klass") is None else h["klass"]
    return f"{h['sha']} {_js(h.get('date'))} [{klass}] {_u16slice(_js(h.get('subject')), 70)}"


def unit_lines(u: dict[str, Any]) -> str:
    parent = f" (a part of {u['parent_name']})" if u.get("parent_name") else ""
    n = ladder.level_of({"level": u.get("level")})
    lines = [
        f"- unit_id: {u['id']}",
        f"  name: {u['name']}{parent}",
        f"  level: {n} ({ladder.LEVELS[n].name})",
        f"  summary: {_js(u.get('summary'))}",
        f"  decision: {_js(u.get('decision'))}",
        f"  alternatives: {_js(u.get('alternatives'))}",
        f"  why: {_js(u.get('why'))}",
        f"  paths: {', '.join(u.get('paths') or [])}",
    ]
    shaped = "; ".join(_history_line(h) for h in (u.get("history") or [])[:5])
    lines.append(f"  shaped_by: {shaped or '(unknown)'}")
    if u.get("shown_orientation"):
        lines.append(f"  orientation the developer has just read: {u['shown_orientation']}")
    for q in u.get("revealed") or []:
        lines.append(f"  revealed after a miss (ask the same fact in other words): {q}")
    return "\n".join(lines)


def _code_block(repo: str, sha: str, excerpts: list[dict[str, Any]]) -> str:
    if not excerpts:
        return f"No code excerpts could be fetched at {sha[:12]}; rely on the unit descriptions."
    return (
        f"Code excerpts from {repo} at {sha[:12]} (line numbers on the left):\n\n"
        f"{format_excerpts(excerpts)}"
    )


def questions_prompt(
    units: list[dict[str, Any]], per_unit: int, repo: str, sha: str, excerpts: list[dict[str, Any]]
) -> str:
    """The hosted question-writing prompt, character for character (prompts.ts)."""
    orientation_rule = (
        f"\n{ORIENTATION_RULE}\n" if any(u.get("shown_orientation") for u in units) else ""
    )
    revealed_rule = f"\n{REVEALED_RULE}\n" if any(u.get("revealed") for u in units) else ""
    return f"""TASK: questions

You are testing whether a developer understands the ARCHITECTURE of the repository {repo}
at commit {sha[:12]}: what each unit is for, the decisions that shaped it, and what
those decisions mean for the rest of the system. Questions climb a ladder of five levels, and a
developer is asked a level only once they have shown the levels above it. Each unit below
names the level to ask it at. Write {per_unit} question(s) for each unit, every one at exactly
that unit's level, and return that level with each question.

The ladder:
{ladder_block()}

{ONE_FACT}

{NO_IDENTIFIERS}
{orientation_rule}{revealed_rule}
{OBJECTIVE_RULE} You cannot open files: the code reaches you as the excerpts at the end,
taken from the repository at that commit, with the unit descriptions and the commit subjects
given; verify every reference answer against them. Cite the files you relied on in
`evidence`, each as `path` or `path:start-end`. {REFERENCE_RULE}

Answer by calling the tool `submit` once, with every question.

Units:
{chr(10).join(unit_lines(u) for u in units)}

{_code_block(repo, sha, excerpts)}
"""  # noqa: E501


# -- where the code comes from ----------------------------------------------------------------


class CodeSource(Protocol):
    def read(self, sha: str, path: str) -> str | None: ...

    def entries(self, sha: str) -> list[tuple[str, int]] | None: ...


class CloneSource:
    """A local clone that holds the pinned commit: `git show` and `git ls-tree`, as the
    preview's fake GitHub reads it."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def read(self, sha: str, path: str) -> str | None:
        p = subprocess.run(["git", "-C", str(self.root), "show", f"{sha}:{path}"],
                           capture_output=True)  # fmt: skip
        if p.returncode != 0 or len(p.stdout) > MAX_FILE_BYTES:
            return None
        return p.stdout.decode("utf-8", "replace")

    def entries(self, sha: str) -> list[tuple[str, int]] | None:
        try:
            return tracked_sizes(self.root, sha)
        except (subprocess.CalledProcessError, OSError):
            return None


Opener = Callable[..., Any]


class GitHubSource:
    """GitHub at the pin, as the hosted functions read it: files from raw.githubusercontent.com
    (at most 2 MB), the tree from one unauthenticated call to the git trees API (60 an hour per
    address); None when GitHub does not answer."""

    def __init__(self, owner: str, repo: str, opener: Opener = urllib.request.urlopen):
        self.owner, self.repo, self.opener = owner, repo, opener

    def _get(self, url: str, accept: str, cap: int) -> bytes | None:
        req = urllib.request.Request(url, headers={"accept": accept, "user-agent": "udebt"})
        try:
            with self.opener(req, timeout=30) as res:  # noqa: S310 - fixed GitHub hosts
                if getattr(res, "status", 200) != 200:
                    return None
                data = res.read(cap + 1)
        except (urllib.error.URLError, OSError, ValueError):
            return None
        return None if len(data) > cap else data

    def read(self, sha: str, path: str) -> str | None:
        quoted = "/".join(urllib.parse.quote(s, safe="") for s in path.split("/"))
        raw = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{sha}/{quoted}"
        data = self._get(raw, "text/plain", MAX_FILE_BYTES)
        return None if data is None else data.decode("utf-8", "replace")

    def entries(self, sha: str) -> list[tuple[str, int]] | None:
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees/{sha}?recursive=1"
        data = self._get(url, "application/vnd.github+json", 50_000_000)
        if data is None:
            return None
        try:
            tree = json.loads(data.decode("utf-8")).get("tree") or []
        except (ValueError, AttributeError):
            return None
        return [(t["path"], int(t["size"])) for t in tree
                if t.get("type") == "blob" and t.get("mode") != "120000"
                and isinstance(t.get("path"), str) and isinstance(t.get("size"), int)]  # fmt: skip


# -- writing the file -------------------------------------------------------------------------


def _architectural(specs: dict[str, Any]) -> list[dict[str, Any]]:
    return [u for u in specs.get("units") or []
            if (u.get("scope") or "architectural") == "architectural"]  # fmt: skip


def unit_refs(
    specs: dict[str, Any], source: CodeSource, sha: str
) -> dict[str, list[dict[str, Any]]]:
    """Each architectural unit's excerpt references, with the tree fallback for a unit that names
    only directories (the top files under them, as the page's TreeCache picks them)."""
    walks = specs.get("walkthroughs") or {}
    units = _architectural(specs)
    refs = {u["id"]: refs_for_unit(u, walks.get(u["id"])) for u in units}
    need = [u for u in units if not refs[u["id"]] and dir_paths(u)]
    entries = source.entries(sha) if need else None
    for u in need:
        if entries:
            refs[u["id"]] = top_refs(files_under(dir_paths(u), entries))
    return refs


def question_unit(u: dict[str, Any], level: int, units: list[dict[str, Any]]) -> dict[str, Any]:
    """What the page sends for a unit (quiz.ts prepare): at level 1 with the orientation the
    learner reads first."""
    parent = (
        next((p for p in units if p["id"] == u.get("parent")), None) if u.get("parent") else None
    )
    return {
        "id": u["id"], "name": u.get("name"), "parent_name": parent.get("name") if parent else None,
        "level": level, "summary": u.get("summary"), "decision": u.get("decision"),
        "alternatives": u.get("alternatives"), "why": u.get("why"), "paths": u.get("paths"),
        "history": (u.get("history") or [])[:5],
        "shown_orientation": orientation_text(orientation_of(u)) if level == 1 else None,
        "revealed": [],
    }  # fmt: skip


def fetch_excerpts(
    source: CodeSource, sha: str, refs: list[dict[str, Any]], cache: dict[str, str | None]
) -> list[dict[str, Any]]:
    """assemble.ts fetchAll: each reference once, unreadable and binary files left out, within
    EXCERPT_BUDGET characters."""
    seen: set[str] = set()
    got = []
    for r in refs:
        key = f"{r['path']}:{r['start']}-{r['end']}"
        if key in seen:
            continue
        seen.add(key)
        if r["path"] not in cache:
            cache[r["path"]] = source.read(sha, r["path"])
        text = cache[r["path"]]
        if text is None or "\0" in text:
            continue
        x = slice_lines(text, r)
        if x["lines"]:
            got.append(x)
    return within_budget(got, EXCERPT_BUDGET)


def has_reference(q: dict[str, Any]) -> bool:
    ref = " ".join(str(q.get("reference_answer") or "").split())
    return bool(ref) and ref != NO_REFERENCE


def read_answer(res: Any, asked: dict[str, int]) -> list[dict[str, Any]]:
    """assemble.ts writeQuestions: questions for the units asked, each at its unit's level and
    asking one fact."""
    raw = res.get("questions") if isinstance(res, dict) else None
    out = []
    for q in raw if isinstance(raw, list) else []:
        if not isinstance(q, dict):
            continue
        uid, text = q.get("unit_id"), q.get("question")
        if not isinstance(uid, str) or uid not in asked or not isinstance(text, str) or not text:
            continue
        lv = asked[uid]
        one = one_fact({"unit_id": uid, "level": lv, "question": text,
                        "variants": _strs(q.get("variants"), 2, 2000),
                        "reference_answer": _str(q.get("reference_answer"), 3000),
                        "evidence": _strs(q.get("evidence"), 6, 400)}, lv)  # fmt: skip
        out.append({k: one[k] for k in ("unit_id", "level", "question", "variants",
                                        "reference_answer", "evidence")})  # fmt: skip
    return out


def batches(units: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [units[i : i + size] for i in range(0, len(units), size)]


def plan(specs: dict[str, Any], levels: Iterable[int], batch: int) -> list[tuple[int, list[str]]]:
    """The calls a run makes: (level, unit ids), in order."""
    units = _architectural(specs)
    return [(lv, [u["id"] for u in chunk]) for lv in levels for chunk in batches(units, batch)]


def write(
    analysis: dict[str, Any],
    llm: Any,
    source: CodeSource,
    *,
    levels: Iterable[int] = LEVELS,
    per: int = PER,
    batch: int = BATCH,
    log: Callable[[str], None] = print,
    cwd: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Every advance question for one analysis: one call per level and batch of units."""
    levels = check_levels(levels)
    if not 1 <= per <= 3:
        raise AdvanceError("--per is 1 to 3 (the hosted request's limit)")
    if not 1 <= batch <= MAX_UNITS:
        raise AdvanceError(f"--batch is 1 to {MAX_UNITS} units a call (the hosted request's limit)")
    url, sha, specs = analysis["url"], analysis["sha"], analysis["specs"]
    units = _architectural(specs)
    if not units:
        raise AdvanceError("the analysis has no architectural unit")
    everyone = specs.get("units") or []
    refs = unit_refs(specs, source, sha)
    cache: dict[str, str | None] = {}
    found: dict[tuple[str, int], list[dict[str, Any]]] = {}
    calls = 0
    with tempfile.TemporaryDirectory(prefix="udebt-quizzes-") as tmp:
        for lv in levels:
            for chunk in batches(units, batch):
                qunits = [normalize_unit(question_unit(u, lv, everyone)) for u in chunk]
                wanted = [r for u in chunk for r in refs[u["id"]][:REFS_PER_REQUEST_UNIT]]
                excerpts = fetch_excerpts(source, sha, wanted, cache)
                prompt = questions_prompt(qunits, per, url, sha, excerpts)
                log(f"level {lv}: {', '.join(u['id'] for u in chunk)} ({len(prompt):,} characters, "
                    f"{len(excerpts)} excerpt(s))")  # fmt: skip
                res = llm.structured(prompt, QUIZ_QUESTIONS_SCHEMA, cwd=cwd or Path(tmp),
                                     read_tools=False, max_turns=MAX_TURNS)  # fmt: skip
                calls += 1
                for q in read_answer(res, {u["id"]: lv for u in chunk}):
                    kept = found.setdefault((q["unit_id"], lv), [])
                    if (
                        len(kept) < per
                        and has_reference(q)
                        and all(k["question"] != q["question"] for k in kept)
                    ):
                        kept.append(q)
    questions = []
    short = []
    for u in units:
        for lv in levels:
            kept = found.get((u["id"], lv), [])
            if len(kept) < per:
                short.append(f"{u['id']} level {lv}: {len(kept)}")
            for q in kept:
                questions.append({**q, "orientation": shown(orientation_of(u))} if lv == 1 else q)
    if short:
        log(f"Fewer than {per} question(s) with a reference answer for: {'; '.join(short)}")
    doc = {
        "format": FORMAT,
        "version": VERSION,
        "repo": url,
        "name": analysis.get("name") or url.rsplit("/", 1)[-1],
        "sha": sha,
        "generated_at": (now or datetime.now(UTC)).replace(microsecond=0).isoformat(),
        "levels": list(levels),
        "per": per,
        "calls": calls,
        "questions": questions,
    }
    return contract.check("quizzes", doc)


def check_levels(levels: Iterable[int]) -> list[int]:
    out = sorted({int(n) for n in levels})
    if not out or any(n not in LEVELS for n in out):
        raise AdvanceError("advance quizzes are levels 1-3 only (levels 4-5 need real grading)")
    return out


def parse_levels(text: str) -> list[int]:
    """'1-3', '1,2' or '2' as levels."""
    out: list[int] = []
    for part in str(text).split(","):
        a, _, b = part.strip().partition("-")
        try:
            lo, hi = int(a), int(b or a)
        except ValueError as e:
            raise AdvanceError(f"levels: {text!r} is not like 1-3") from e
        out.extend(range(lo, hi + 1))
    return check_levels(out)


# -- what to write questions for ---------------------------------------------------------------


def load_source(source: str, root: Path | None = None) -> dict[str, Any]:
    """{url, name, sha, specs} from a worker output directory, a load script (.sql), a rows file
    (.json, as `udebt worker sql --dialect json` writes) or a registered sample's name (its
    samples/<name>.sql, else web/public/samples/<name>.json, under `root`)."""
    from ..loadsql import analysis_rows, rows_from_sql
    from .samples import SAMPLES

    root = Path(root or Path.cwd())
    p = Path(source)
    if p.is_dir():
        rows = analysis_rows(p)
        manifest = rows["analysis"]["manifest"]
        sample = SAMPLES.get(str(manifest.get("repo")))
        url = sample["url"] if sample else rows["repository"]["url"]
        return {"url": url, "name": manifest.get("repo"), "sha": rows["analysis"]["sha"],
                "specs": rows["analysis"]["specs"]}  # fmt: skip
    if not p.exists() and source in SAMPLES:
        for cand in (root / "samples" / f"{source}.sql",
                     root / "web" / "public" / "samples" / f"{source}.json"):  # fmt: skip
            if cand.exists():
                p = cand
                break
        else:
            raise AdvanceError(
                f"{source}: no samples/{source}.sql or web/public/samples/{source}.json"
            )
    if not p.exists():
        raise AdvanceError(f"{source}: not a worker output, a .sql or .json file, or a sample name")
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".sql":
        rows = rows_from_sql(text)
        repo, an = rows["repository"], rows["analysis"]
    else:
        doc = json.loads(text)
        an = (doc.get("analyses") or [None])[0]
        if not an:
            raise AdvanceError(f"{p}: no analysis in it")
        repos = [r for r in doc.get("repositories") or [] if r.get("url") == an.get("repo_url")]
        repo = repos[0] if repos else {"url": an.get("repo_url"), "name": None}
    return {"url": repo["url"], "name": repo.get("name"), "sha": an["sha"], "specs": an["specs"]}


def source_for(url: str, clone: str | None) -> CodeSource:
    if clone:
        return CloneSource(Path(clone))
    from .github import parse

    g = parse(url)
    if not g:
        raise AdvanceError(f"{url}: not a public GitHub URL; pass --clone")
    return GitHubSource(g.owner, g.name)


def run(args: argparse.Namespace, llm_factory: Callable[[str | None], Any] | None = None) -> int:
    """`udebt worker quizzes`."""
    from .samples import SAMPLES

    try:
        levels = parse_levels(args.levels)
        analysis = load_source(args.source)
        source = source_for(analysis["url"], args.clone)
        sample = SAMPLES.get(str(analysis.get("name")))
        if sample and sample["sha"] != analysis["sha"]:
            print(f"Note: {analysis['name']} is pinned at {str(sample['sha'])[:12]}, this analysis "
                  f"is at {analysis['sha'][:12]}; the site lists a file only at the pin.",
                  file=sys.stderr)  # fmt: skip
        if args.plan:
            refs = unit_refs(analysis["specs"], source, analysis["sha"])
            calls = plan(analysis["specs"], levels, args.batch)
            print(f"{analysis['name']} at {analysis['sha'][:12]}: {len(calls)} call(s), "
                  f"{args.per} question(s) per unit and level, levels {levels}")  # fmt: skip
            for lv, ids in calls:
                print(f"  level {lv}: {', '.join(ids)}")
            none = [uid for uid, r in refs.items() if not r]
            if none:
                print(f"  no code found for: {', '.join(none)} (their questions are written from "
                      "the unit descriptions)")  # fmt: skip
            return 0
        if not args.out:
            raise AdvanceError("--out is needed (or --plan)")
        from ..llm import get_llm

        llm = (llm_factory or (lambda m: get_llm(model=m)))(args.model)
        doc = write(analysis, llm, source, levels=levels, per=args.per, batch=args.batch,
                    log=lambda s: print(s, file=sys.stderr))  # fmt: skip
    except (AdvanceError, contract.ContractError) as e:
        raise SystemExit(f"udebt worker quizzes: {e}") from e
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"Wrote {args.out}: {len(doc['questions'])} question(s) on "
          f"{len({q['unit_id'] for q in doc['questions']})} unit(s), levels {doc['levels']}, "
          f"{doc['calls']} model call(s).")  # fmt: skip
    return 0
