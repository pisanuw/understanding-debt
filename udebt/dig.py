"""Walkthroughs that show the code (Phase 3.3, `udebt dig`).

The model traces one real request through a unit in three to six steps, each a file and a
line range with narration, and writes one check question per ladder level (`TASK:
walkthrough`, one call). `dig` prints the unit's orientation, then each step's narration next
to the actual lines from the working tree (excerpts.py, at most 25 each), then asks the check
question at the developer's level and grades it against its reference. The check rotates
(pick_check): a variant of a missed question, the walkthrough's own check, then the unit's
pooled questions at that level, so a second `dig` does not repeat the first one's question.

The walkthrough is cached on the unit in map.json (`walkthrough`), shared by the team, with
the head it was written at, the unit's orientation basis and a hash of the contents of the
unit's tracked files in the working tree (`files`): it is reused until the unit's map entry
or any of its files changes. (Comparing `git diff --name-only <head>` with the working tree,
as first planned, threw away a walkthrough written over uncommitted changes as soon as those
changes were committed.)"""

from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path
from typing import Any

from . import excerpts, ladder, repo
from . import orientation as orient
from .llm import LLM
from .mapping import PathIndex
from .prompts import WALKTHROUGH_SCHEMA, walkthrough_prompt
from .store import Store, now_iso

MAX_TURNS = 60


def files_hash(root: Path, paths: list[str], tracked: list[str] | None = None) -> str:
    """A hash of the unit's tracked files as they are in the working tree: path and content
    of each, in order."""
    from .provenance import _under

    files = tracked if tracked is not None else repo.tracked_files(root)
    h = hashlib.sha1()
    for f in sorted(f for f in files if _under(f, paths)):
        h.update(f.encode() + b"\0")
        try:
            h.update(hashlib.sha1((root / f).read_bytes()).digest())
        except OSError:
            h.update(b"-")
    return h.hexdigest()[:16]


def cached(root: Path, unit: dict[str, Any]) -> dict[str, Any] | None:
    w = unit.get("walkthrough")
    if not isinstance(w, dict) or not w.get("steps") or w.get("basis") != orient.basis(unit):
        return None
    if w.get("files") != files_hash(root, unit.get("paths", [])):
        return None
    return w


def _steps(root: Path, given: list[dict[str, Any]], tracked: set[str]) -> list[dict[str, Any]]:
    """The model's steps whose file resolves to a tracked file, lines clamped to the file."""
    index = PathIndex(sorted(tracked))
    out = []
    for st in given or []:
        got, _how = index.resolve(str(st.get("file", "")))
        if not got or got not in tracked:
            continue
        try:
            ex = excerpts.read(root, got, st.get("start_line", 1), st.get("end_line", 1),
                               tracked=tracked)  # fmt: skip
        except (excerpts.ExcerptError, TypeError, ValueError):
            continue
        out.append(
            {
                "file": got,
                "start_line": ex.start,
                "end_line": ex.end + ex.cut,
                "narration": " ".join(str(st.get("narration", "")).split()),
            }
        )
    return out


def ensure(
    root: Path,
    store: Store,
    llm: LLM,
    map_data: dict[str, Any],
    unit_id: str,
    *,
    refresh: bool = False,
    orientation: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """(the unit's walkthrough, whether it was written now)."""
    from . import units as hier

    unit = hier.index(map_data["units"])[unit_id]
    if not refresh:
        got = cached(root, unit)
        if got is not None:
            return got, False
    res = llm.structured(
        walkthrough_prompt(unit, orientation), WALKTHROUGH_SCHEMA, cwd=root, read_tools=True,
        max_turns=MAX_TURNS,
    )  # fmt: skip
    tracked = set(repo.tracked_files(root))
    checks: dict[str, dict[str, str]] = {}
    for c in res.get("checks", []):
        try:
            n = ladder.check(int(c.get("level", 0)))
        except (SystemExit, TypeError, ValueError):
            continue
        q = " ".join(str(c.get("question", "")).split())
        if q and str(n) not in checks:
            if ladder.compound(q, n):
                q = ladder.first_clause(q)
            checks[str(n)] = {"question": q, "reference_answer": str(c.get("reference_answer", ""))}
    w = {
        "flow": " ".join(str(res.get("flow", "")).split()),
        "steps": _steps(root, res.get("steps", []), tracked),
        "trap": " ".join(str(res.get("trap", "")).split()),
        "checks": checks,
        "basis": orient.basis(unit),
        "files": files_hash(root, unit.get("paths", []), sorted(tracked)),
        "head": repo.head(root),
        "generated_at": now_iso(),
    }
    if w["steps"]:
        unit["walkthrough"] = w
        store.save_map(map_data)
        # its checks join the unit's question pool, so `dig` and the quiz can rotate through
        # them as well as through the quiz's own questions
        from .quiz import pool_add

        checks = [check_question(w, unit, int(n)) for n in sorted(checks)]
        pool_add(store, map_data, [q for q in checks if q is not None])
    return w, True


def pick_check(
    w: dict[str, Any], unit: dict[str, Any], level: int, records_on_unit: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The check question `dig` asks at `level`, rotating rather than repeating (Phase 3.4):
    a stored variant of a question the developer missed and saw the answer to there, else
    the walkthrough's own check if they have not been asked it, else a pooled question at
    that level they have not been asked (the quiz's, and earlier walkthroughs' checks), else
    the one of these they were asked longest ago, marked `repeat` (`dig` then asks the model
    for a new one, fresh_check). None when there is none at that level."""
    from .quiz import pooled, revealed_variant

    got = revealed_variant(unit, level, records_on_unit)
    if got is not None:
        return got
    last: dict[str, str] = {}
    for r in records_on_unit:
        q = str(r.get("question"))
        last[q] = max(last.get(q, ""), str(r.get("ts", "")))
    own = check_question(w, unit, level)
    if own is not None and own["question"] not in last:
        return own
    pool = pooled(unit, level)
    fresh = next((q for q in pool if q["question"] not in last), None)
    if fresh is not None:
        return fresh
    seen: dict[str, dict[str, Any]] = {}
    for q in ([own] if own else []) + pool:
        seen.setdefault(q["question"], q)
    if not seen:
        return None
    return {**min(seen.values(), key=lambda q: last.get(q["question"], "")), "repeat": True}


def fresh_check(
    root: Path,
    store: Store,
    llm: LLM,
    map_data: dict[str, Any],
    unit: dict[str, Any],
    level: int,
    records_on_unit: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """A new check question at `level` when every pooled one has been asked (one call, the
    quiz's question writer, told what was asked so it says it in other words), pooled for
    the next time; its first phrasing the developer has not been asked. None if the model
    returned only questions already asked."""
    from .quiz import generate_questions, pool_add, revealed_on

    asked = {str(r.get("question")) for r in records_on_unit}
    u = {**unit, "level": level}
    seen = [q for q in revealed_on(records_on_unit, level)] or [
        str(r["question"])
        for r in ladder._ordered(records_on_unit)
        if ladder.level_of(r) == level and r.get("question")
    ][-3:]
    if seen:
        u["revealed"] = seen
    qs = generate_questions(root, llm, [u])
    pool_add(store, map_data, qs)
    for q in qs:
        for text in [q["question"], *(q.get("variants") or [])]:
            if text not in asked:
                rest = [v for v in [q["question"], *(q.get("variants") or [])] if v != text]
                return {**q, "question": text, "variants": rest[:2]}
    return None


def check_question(w: dict[str, Any], unit: dict[str, Any], level: int) -> dict[str, Any] | None:
    """The walkthrough's check question at `level`, as a quiz question."""
    c = (w.get("checks") or {}).get(str(level))
    if not c:
        return None
    return {
        "unit_id": unit["id"],
        "level": level,
        "question": c["question"],
        "reference_answer": c.get("reference_answer") or "(graded against the code)",
        "evidence": sorted({st["file"] for st in w.get("steps", [])}) or unit.get("paths", []),
    }


def render(root: Path, w: dict[str, Any], width: int = 92) -> str:
    """For the terminal: the flow, each step's narration and its lines, the trap."""
    tracked = set(repo.tracked_files(root))
    out = []
    if w.get("flow"):
        out += textwrap.wrap(f"The request traced: {w['flow']}", width)
    for i, st in enumerate(w.get("steps", []), 1):
        out += ["", f"Step {i}: {st['file']}:{st['start_line']}-{st['end_line']}"]
        out += textwrap.wrap(st["narration"], width, initial_indent="  ", subsequent_indent="  ")
        try:
            ex = excerpts.read(root, st["file"], st["start_line"], st["end_line"], tracked=tracked)
            out.append(ex.numbered())
        except excerpts.ExcerptError as e:
            out.append(f"    ({e})")
    if w.get("trap"):
        out += [""] + textwrap.wrap(f"The trap: {w['trap']}", width)
    return "\n".join(out)
