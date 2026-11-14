"""The tutor (Phase 3.3, `udebt serve`): a conversation about one unit, run turn by turn
through the `claude` CLI, which reads the code for the developer and explains it, asking back
rather than lecturing. After a few turns, or when the developer says "check me", it proposes
a check question at the developer's ladder level; the answer is graded and recorded like any
quiz answer (`source: tutor`).

Each turn is one CLI call: the first names the session (`--session-id <uuid>`) and appends
the Socratic system prompt, the later ones `--resume <uuid>`; tools are Read, Glob and Grep
only. The session's transcript lives in the CLI's own store, not in the repository; udebt
keeps the turns in memory for the page and records only the graded check answer. A session
reports its elapsed time and suggests stopping at ten minutes, the owner's session length.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from . import ladder
from . import orientation as orient
from .markdown import to_html

CHECK_AFTER = 4  # developer turns before the tutor is asked for the check question
SESSION_SECONDS = 600
CHECK_ME = re.compile(r"\bcheck me\b", re.I)
_CHECK = re.compile(r"^\s*CHECK QUESTION:\s*(.+?)\s*$", re.M)
_REF = re.compile(r"^\s*REFERENCE:\s*(.+?)\s*$", re.M)
ASK_NOW = (
    "[udebt: propose the check question now, in the two-line format, after at most two sentences.]"
)


def system_prompt(unit: dict[str, Any], level: int, dev: str) -> str:
    lv = ladder.LEVELS[level]
    o = orient.cached(unit)
    hist = "; ".join(
        f"{h.get('sha')} {h.get('date')} [{h.get('klass')}] {h.get('author') or '?'}: "
        f"{h.get('subject', '')[:70]}"
        for h in (unit.get("history") or [])[:6]
    )
    return f"""You are udebt's tutor. You are helping {dev}, a developer on the repository in the
current directory, understand ONE architectural unit well enough to change it safely. Teach
it Socratically: find out what they already think, read the code for them with your read-only
tools and explain what it does in plain language, then ask one question back that makes them
reason about it. Show at most a few lines of code at a time, with the file and line numbers;
never paste a whole file. Keep each reply under 150 words. Stay on this unit and its place in
the system; if they ask about something else, say how it connects and come back.

The unit (from udebt's map of the architecture):
- id: {unit["id"]}; name: {unit.get("name", "")}; kind: {unit.get("kind", "")}
- summary: {unit.get("summary", "")}
- decision: {unit.get("decision", "")}
- alternatives not chosen: {unit.get("alternatives", "")}
- why: {unit.get("why", "")}
- paths: {", ".join(unit.get("paths", []))}
- history (who shaped it): {hist or "not known"}
- orientation the developer may have read: {orient.text(o) if o else "none yet"}

The developer is at level {level} of udebt's question ladder for this unit ({lv.name}: {lv.asks}).
When a message asks you to propose the check question, or the developer writes "check me", end
your reply with exactly these two lines and nothing after them:
CHECK QUESTION: <one question with one part, at level {level}: {lv.asks}; it may name {lv.names}>
REFERENCE: <two plain sentences a correct answer must contain>
Never write those two lines otherwise, and never answer the check question yourself."""


def split_check(reply: str) -> tuple[str, dict[str, str] | None]:
    """(the reply as the developer sees it, the check question and reference, if any): the
    REFERENCE line is never shown."""
    q = _CHECK.search(reply)
    if not q:
        return reply.strip(), None
    ref = _REF.search(reply)
    shown = (reply[: q.start()]).rstrip()
    check = {
        "question": q.group(1).strip(),
        "reference_answer": ref.group(1).strip() if ref else "",
    }
    if ladder.compound(check["question"]):
        check["question"] = ladder.first_clause(check["question"])
    return shown, check


@dataclass
class Session:
    unit_id: str
    level: int
    dev: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started: float = field(default_factory=time.monotonic)
    turns: list[dict[str, str]] = field(default_factory=list)
    check: dict[str, str] | None = None
    asked: int = 0  # developer turns so far

    @property
    def elapsed(self) -> int:
        return int(time.monotonic() - self.started)

    def view(self) -> dict[str, Any]:
        return {
            "session": self.id,
            "unit": self.unit_id,
            "level": self.level,
            "label": ladder.label(self.level),
            "turns": [
                {**t, "html": to_html(t["text"])} if t["role"] == "tutor" else t for t in self.turns
            ],
            "check": {"question": self.check["question"]} if self.check else None,
            "elapsed_s": self.elapsed,
            "suggest_stop": self.elapsed >= SESSION_SECONDS,
        }


def turn(llm, root, unit: dict[str, Any], s: Session, message: str) -> dict[str, Any]:
    """One developer message and the tutor's reply."""
    s.asked += 1
    text = message.strip()
    sent = text
    if CHECK_ME.search(text) or s.asked > CHECK_AFTER:
        sent = f"{text}\n\n{ASK_NOW}"
    if s.asked == 1:
        reply = llm.chat(
            sent, cwd=root, session_id=s.id, system=system_prompt(unit, s.level, s.dev)
        )
    else:
        reply = llm.chat(sent, cwd=root, session_id=s.id, resume=True)
    shown, check = split_check(reply)
    s.turns.append({"role": "you", "text": text})
    s.turns.append({"role": "tutor", "text": shown})
    if check:
        s.check = check
    return s.view()


def check_question(unit: dict[str, Any], s: Session) -> dict[str, Any] | None:
    if not s.check:
        return None
    return {
        "unit_id": unit["id"],
        "level": s.level,
        "question": s.check["question"],
        "reference_answer": s.check.get("reference_answer") or "(graded against the code)",
        "evidence": list(unit.get("paths", [])),
    }
