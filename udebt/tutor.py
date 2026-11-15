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

Sessions persist (Phase 3.4, owner): each is saved after every turn under
`.udebt/tutor/<developer>/<unit>.json` (gitignored: a conversation is the developer's own,
not team state), with the `claude` session id, the transcript, the level, the pending check
question and the turn count, so a server restart loses nothing: the page reattaches by its
session id, and opening the tutor on a unit with a saved conversation offers "resume or start
over". One saved conversation per developer and unit; starting over replaces it (the CLI's own
store still has the old transcript). A conversation resumed after more than half an hour idle
starts a new sitting: the ten-minute timer counts from the resume.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ladder, repo
from . import orientation as orient
from .markdown import to_html

CHECK_AFTER = 4  # developer turns before the tutor is asked for the check question
SESSION_SECONDS = 600
SITTING_GAP = 1800  # idle seconds after which a resumed conversation starts a new sitting
DIR = "tutor"
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
never paste a whole file. Never open anything under `.udebt/` (udebt's own records and earlier
answers). Keep each reply under 150 words. Stay on this unit and its place in
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
    started: float = field(default_factory=time.time)  # this sitting's start (epoch seconds)
    turns: list[dict[str, str]] = field(default_factory=list)
    check: dict[str, str] | None = None
    asked: int = 0  # developer turns so far
    created: float = field(default_factory=time.time)  # the conversation's first turn
    active: float = field(default_factory=time.time)  # the latest turn

    @property
    def elapsed(self) -> int:
        return max(0, int(time.time() - self.started))

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
            "created": _iso(self.created),
            "active": _iso(self.active),
            "asked": self.asked,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "session": self.id,
            "unit": self.unit_id,
            "level": self.level,
            "dev": self.dev,
            "turns": self.turns,
            "check": self.check,
            "asked": self.asked,
            "created": self.created,
            "started": self.started,
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Session:
        s = cls(
            str(d["unit"]), ladder.check(int(d["level"])), str(d["dev"]), id=str(d["session"]),
            turns=list(d.get("turns") or []), check=d.get("check"), asked=int(d.get("asked", 0)),
        )  # fmt: skip
        now = time.time()
        s.created = float(d.get("created", now))
        s.active = float(d.get("active", s.created))
        s.started = float(d.get("started", s.created))
        if now - s.active > SITTING_GAP:  # back after a break: a new sitting
            s.started = now
        return s


def _iso(t: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(t, UTC).replace(microsecond=0).isoformat()


def _file_name(unit_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "--", unit_id) + ".json"


def path_for(udebt_dir: Path, dev: str, unit_id: str) -> Path:
    """Where a developer's conversation on a unit is saved."""
    return udebt_dir / DIR / repo.slug(dev) / _file_name(unit_id)


def save(udebt_dir: Path, s: Session) -> Path:
    from .cache import ensure_gitignore

    p = path_for(udebt_dir, s.dev, s.unit_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    ensure_gitignore(udebt_dir)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s.as_dict(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def load(udebt_dir: Path, dev: str, unit_id: str) -> Session | None:
    """The developer's saved conversation on a unit, if any (None when unreadable)."""
    p = path_for(udebt_dir, dev, unit_id)
    try:
        return Session.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError, SystemExit):
        return None


def find(udebt_dir: Path, dev: str, session_id: str) -> Session | None:
    """A saved conversation of the developer's by its session id."""
    d = udebt_dir / DIR / repo.slug(dev)
    if not session_id or not d.is_dir():
        return None
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("session") == session_id:
            try:
                return Session.from_dict(data)
            except (KeyError, TypeError, ValueError, SystemExit):
                return None
    return None


def discard(udebt_dir: Path, dev: str, unit_id: str) -> bool:
    p = path_for(udebt_dir, dev, unit_id)
    if p.exists():
        p.unlink()
        return True
    return False


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
    s.active = time.time()
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
