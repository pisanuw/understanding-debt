"""Who wrote each commit: an AI agent, a human with an AI co-author, a human alone, or
automation.

Signatures are data, not code, so new agents can be added in .udebt/config.json under
"ai_signatures", and new automation under "bot_signatures", without touching this file. An
AI signature has:

    name              short agent id
    author_emails     regexes matched against the commit author email  -> class "ai"
    author_names      regexes matched against the commit author name   -> class "ai"
    authored_patterns regexes matched against the body that say the agent authored the
                      commit on someone's behalf                       -> class "ai"
    trailer_patterns  regexes matched against the commit body          -> class "assisted"
    names             regexes that recognise the agent's name in the value of an
                      attribution trailer (Assisted-by:, Generated-by:, Made-with:)

A bot signature has `name`, `author_emails` and `author_names` only. Every regex matches
case-insensitively.

A commit authored by an agent is "ai". A commit authored by CI, release, dependency or
pipeline automation is "bot", unless it carries an agent trailer, in which case no human was
in the loop and it is "ai". A human-authored commit carrying an agent's trailer, footer or an
attribution trailer that names an AI is "assisted". Anything else is "human".

Bots are not people: their commits count for nobody's understanding or footprint, and do not
decay anyone's (see `landed`).
"""

from __future__ import annotations

import math
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from .filekinds import mechanical

_GH = r"@users\.noreply\.github\.com$"  # GitHub noreply addresses: 123+login@users.noreply...

BUILTIN_SIGNATURES: list[dict[str, Any]] = [
    {
        "name": "claude-code",
        "author_emails": [r"^noreply@anthropic\.com$", r"(^|\+)claude\[bot\]" + _GH],
        "author_names": [r"^Claude$", r"^Claude Code$", r"^claude\[bot\]$"],
        "trailer_patterns": [
            r"(?m)^Co-Authored-By:\s*Claude\b",
            r"(?m)^Claude-Session:",
            r"Generated with \[?Claude Code",
        ],
        "names": [r"\bclaude\b", r"\banthropic\b"],
    },
    {
        "name": "github-copilot",
        "author_emails": [r"copilot.*" + _GH],
        "author_names": [r"^(GitHub )?Copilot$", r"^copilot-swe-agent"],
        "authored_patterns": [r"(?m)^\W*Authored with (GitHub )?Copilot\b"],
        "trailer_patterns": [r"(?m)^Co-Authored-By:.*Copilot\b"],
        "names": [r"\bcopilot\b"],
    },
    {
        "name": "cursor",
        "author_emails": [r"@cursor\.(sh|com)$", r"(^|\+)cursor(agent)?\[bot\]" + _GH],
        "author_names": [r"^Cursor( Agent)?$", r"^cursor(agent)?\[bot\]$"],
        "trailer_patterns": [r"(?m)^Co-Authored-By:.*Cursor\b"],
        "names": [r"\bcursor\b"],
    },
    {
        "name": "aider",
        "author_emails": [r"\(aider\)"],
        "author_names": [r"\(aider\)$"],
        "trailer_patterns": [r"(?m)^Co-Authored-By:.*aider\b", r"(?m)^aider:"],
        "names": [r"\baider\b"],
    },
    {
        "name": "openai-codex",
        "author_emails": [r"codex.*@openai\.com$", r"(^|\+)chatgpt-codex-connector\[bot\]" + _GH],
        "author_names": [r"^Codex$", r"^OpenAI Codex$", r"^chatgpt-codex-connector\[bot\]$"],
        "trailer_patterns": [r"(?m)^Co-Authored-By:.*Codex\b"],
        "names": [r"\bcodex\b", r"\bopenai\b", r"\bchatgpt\b", r"\bgpt[- ]?\d"],
    },
    {
        "name": "gemini",
        "author_names": [r"^Gemini( CLI| Code Assist)?$"],
        "trailer_patterns": [r"(?m)^Co-Authored-By:.*Gemini\b"],
        "names": [r"\bgemini\b"],
    },
    {
        "name": "devin",
        "author_emails": [r"(^|\+)devin-ai-integration\[bot\]" + _GH],
        "author_names": [r"^devin-ai-integration\[bot\]$", r"^Devin( AI)?$"],
        "trailer_patterns": [r"(?m)^Co-Authored-By:\s*Devin\b", r"(?m)^Co-Authored-By:.*devin-ai"],
        "names": [r"\bdevin\b"],
    },
    {
        "name": "opencode",
        "author_emails": [r"^noreply@opencode\.ai$", r"(^|\+)opencode-agent\[bot\]" + _GH],
        "author_names": [r"^opencode$", r"^opencode-agent\[bot\]$"],
        "trailer_patterns": [r"(?m)^Co-Authored-By:.*(\bopencode\b|@opencode\.ai)"],
        "names": [r"\bopencode\b"],
    },
    {
        "name": "jules",
        "author_emails": [r"(^|\+)google-labs-jules\[bot\]" + _GH],
        "author_names": [r"^google-labs-jules\[bot\]$"],
        "trailer_patterns": [r"(?m)^Co-Authored-By:.*google-labs-jules"],
        "names": [r"\bjules\b"],
    },
    {
        "name": "amazon-q",
        "author_emails": [r"(^|\+)amazon-q-developer\[bot\]" + _GH],
        "author_names": [r"^amazon-q-developer\[bot\]$"],
        "trailer_patterns": [r"(?m)^Co-Authored-By:.*(amazon-q-developer|\bAmazon Q\b)"],
        "names": [r"\bamazon q\b", r"\bq developer\b"],
    },
]

# Words that make an attribution trailer name an AI when no signature's `names` matches it:
# vendors, model families, and generic terms. The agent is then the generic trailer key.
AI_WORDS = re.compile(
    r"\b(ai|llm|language model|anthropic|openai|claude|opus|sonnet|haiku|gpt[\w.-]*|"
    r"o[1-9](-mini)?|gemini|gemma|llama[\w.-]*|qwen[\w.-]*|deepseek[\w.-]*|mistral|codestral|"
    r"grok|kimi|glm[\w.-]*|windsurf|codeium|cline|roo ?code|kiro|goose|amp)\b",
    re.I,
)
# Attribution trailers: the kernel/Fedora/LLVM `Assisted-by:` and project variants. The
# value names the tool, e.g. `Assisted-by: Claude Code`, `Assisted-by: Claude:<model>`
# (kernel), or the agentic-commits form `Assisted-by: <agent> <version> (<provider>/<model>)`.
ATTRIBUTION = re.compile(r"(?im)^(assisted-by|generated-by|made-with)\s*:\s*(.+)$")
_PROVIDER_MODEL = re.compile(r"\(\s*[\w.-]+/[\w.:@-]+\s*\)")

_WORD = r"(^|[^a-z])({w})([^a-z]|$)"  # a whole word in a name
_LOCAL = r"^([^@]*[^a-z@])?({w})([^a-z@][^@]*)?@"  # a whole word in an email's local part
BUILTIN_BOTS: list[dict[str, Any]] = [
    {"name": "dependabot", "author_names": [r"dependabot"], "author_emails": [r"dependabot"]},
    {"name": "renovate", "author_names": [r"renovate"], "author_emails": [r"renovate"]},
    {
        "name": "github-actions",
        "author_names": [r"^github-actions"],
        "author_emails": [r"github-actions", r"^actions@github\.com$"],
    },
    {
        "name": "semantic-release",
        "author_names": [r"semantic-release"],
        "author_emails": [r"semantic-release"],
    },
    {
        # any other `*[bot]` GitHub app that no AI signature claims, and identities named
        # like automation: `ci-bot`, `bot@example.com`, `Release pipeline`, `Automation`
        "name": "bot",
        "author_names": [r"\[bot\]", _WORD.format(w="bot|pipeline|automation|actions")],
        "author_emails": [r"\[bot\]@", _LOCAL.format(w="bot|pipeline|automation|actions")],
    },
]


@dataclass
class Signature:
    name: str
    author_emails: list[re.Pattern] = field(default_factory=list)
    author_names: list[re.Pattern] = field(default_factory=list)
    trailer_patterns: list[re.Pattern] = field(default_factory=list)
    authored_patterns: list[re.Pattern] = field(default_factory=list)
    names: list[re.Pattern] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Signature:
        def comp(key: str) -> list[re.Pattern]:
            return [re.compile(p, re.I) for p in d.get(key, [])]

        return cls(
            name=d["name"],
            author_emails=comp("author_emails"),
            author_names=comp("author_names"),
            trailer_patterns=comp("trailer_patterns"),
            authored_patterns=comp("authored_patterns"),
            names=comp("names"),
        )

    def authors(self, c: Commit) -> bool:
        return any(r.search(c.author_email) for r in self.author_emails) or any(
            r.search(c.author_name) for r in self.author_names
        )


def load_signatures(extra: list[dict[str, Any]] | None = None) -> list[Signature]:
    return [Signature.from_dict(d) for d in [*BUILTIN_SIGNATURES, *(extra or [])]]


def load_bots(extra: list[dict[str, Any]] | None = None) -> list[Signature]:
    """Bot signatures: the built-in list plus config `bot_signatures`. Configured ones come
    first so they name the bot; any match makes the commit `bot`."""
    return [Signature.from_dict(d) for d in [*(extra or []), *BUILTIN_BOTS]]


@dataclass
class Commit:
    sha: str
    date: str  # ISO 8601 author date
    author_name: str
    author_email: str
    subject: str
    body: str
    files: list[str] = field(default_factory=list)
    insertions: int = 0
    deletions: int = 0
    klass: str = "human"  # ai | assisted | human | bot
    agent: str | None = None  # the AI agent (ai, assisted) or the bot (bot)
    file_lines: dict[str, int] = field(default_factory=dict)  # path -> insertions + deletions
    parents: list[str] = field(default_factory=list)
    directed_by: str | None = None  # developer who directed this agent commit (config)
    # files whose diff here changes only version strings (weights.version_bumps); set with
    # the weights, before any line share is taken
    version_files: set[str] = field(default_factory=set)

    @property
    def short(self) -> str:
        return self.sha[:7]

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1

    def touches(self, paths: list[str]) -> bool:
        return any(_under(f, paths) for f in self.files)

    @cached_property
    def counted_lines(self) -> dict[str, int]:
        """Changed lines per file, without lockfiles, generated files and version bumps: a
        2000-line package-lock.json or a version field in every package.json says nothing
        about where a refactor's thinking went."""
        return {
            f: n
            for f, n in self.file_lines.items()
            if not mechanical(f) and f not in self.version_files
        }

    def share(
        self,
        paths: list[str],
        exclude: list[str] | None = None,
        split: Callable[[str], float] | None = None,
    ) -> float:
        """Fraction of this commit's changed lines that fall under `paths` (0..1), and not
        under `exclude`, not counting lockfiles and generated files. Falls back to the
        fraction of files when line counts are unavailable (binary or purely mechanical).
        `split(path)` is the part of a file's lines this unit gets when several units list
        the file (units.Membership); without it every file counts in full."""
        if not self.files:
            return 0.0

        def inside(f: str) -> bool:
            return _under(f, paths) and not (exclude and _under(f, exclude))

        part = split or _whole
        lines = self.counted_lines
        total = sum(lines.values())
        if total > 0:
            return min(1.0, sum(n * part(f) for f, n in lines.items() if inside(f)) / total)
        return sum(part(f) for f in self.files if inside(f)) / len(self.files)

    def attribution(
        self,
        paths: list[str],
        exclude: list[str] | None = None,
        split: Callable[[str], float] | None = None,
    ) -> float:
        """How much of this commit's weight lands on `paths`: the square root of the line
        share. Concave on purpose: a unit that received a tenth of a large architectural
        commit carries about a third of its debt, because the commit's nature (a new
        dependency, a changed model) spills past the lines it touched there. A commit
        confined to one unit lands its full weight."""
        return math.sqrt(self.share(paths, exclude, split))


def _whole(_path: str) -> float:
    return 1.0


def landed(c: Commit, weights: dict[str, Any] | None, default: float = 1.0) -> float:
    """The weight a commit lands on the code: its commit weight, or `default` when unknown.
    Zero for a bot: automation neither erodes anyone's understanding nor builds anyone's
    footprint, so every mass (churn, decay, prior, risk, co-change) leaves it out."""
    if c.klass == "bot":
        return 0.0
    if weights and c.sha in weights:
        return weights[c.sha].weight
    return default


def _under(f: str, paths: list[str]) -> bool:
    for p in paths:
        p = p.rstrip("/")
        if f == p or f.startswith(p + "/"):
            return True
    return False


def attributed_agent(body: str, sigs: list[Signature]) -> str | None:
    """The AI an attribution trailer names (`Assisted-by:`, `Generated-by:`, `Made-with:`),
    or None when it names none (a person, a script). The agent mentioned first wins, so
    `Assisted-by: OpenCode with Claude Opus` is OpenCode; in the agentic-commits form the
    `(provider/model)` part is the model, not the agent. A value that names no known agent
    but does name an AI (a model family, a vendor, "LLM") maps to the generic trailer key,
    such as `assisted-by`."""
    for m in ATTRIBUTION.finditer(body):
        key, value = m.group(1).lower(), m.group(2)
        head = _PROVIDER_MODEL.sub(" ", value)
        best: tuple[int, str] | None = None
        for s in sigs:
            for r in s.names:
                hit = r.search(head)
                if hit and (best is None or hit.start() < best[0]):
                    best = (hit.start(), s.name)
        if best:
            return best[1]
        if _PROVIDER_MODEL.search(value) or AI_WORDS.search(value):
            return key
    return None


def classify(
    c: Commit, sigs: list[Signature], bots: list[Signature] | None = None
) -> tuple[str, str | None]:
    """(class, agent or bot name). Order: an agent author identity, then a body saying an
    agent authored the commit, then bot identities, then agent trailers."""
    for s in sigs:
        if s.authors(c):
            return "ai", s.name
    for s in sigs:
        if any(r.search(c.body) for r in s.authored_patterns):
            return "ai", s.name
    trailer = next(
        (s.name for s in sigs if any(r.search(c.body) for r in s.trailer_patterns)), None
    ) or attributed_agent(c.body, sigs)
    for b in load_bots() if bots is None else bots:
        if b.authors(c):
            # automation that commits an agent's work: no human was in the loop
            return ("ai", trailer) if trailer else ("bot", b.name)
    if trailer:
        return "assisted", trailer
    return "human", None


_SESSION = re.compile(r"(?im)^Claude-Session:\s*(\S+)")


def session_id(ref: str) -> str:
    """`https://claude.ai/code/session_01Ab...` or `session_01Ab...` -> `session_01Ab...`."""
    return ref.strip().rstrip("/").rsplit("/", 1)[-1]


def sessions_of(c: Commit) -> list[str]:
    return [session_id(m.group(1)) for m in _SESSION.finditer(c.body)]


def assign_directed(
    commits: list[Commit],
    directed: dict[str, dict[str, list[str]]],
    key: Callable[[str], str] = str.lower,
) -> None:
    """Mark the agent-authored commits a developer directed (config `directed`): first by a
    `Claude-Session:` trailer naming one of their sessions, else by an agent they claim
    wholesale (the first claimant wins). The commit stays `ai` for display and AI share;
    `unit_provenance` and footprint count it as that developer's assisted work."""
    by_session: dict[str, str] = {}
    by_agent: dict[str, str] = {}
    for dev, d in directed.items():
        for s in d.get("sessions") or []:
            by_session.setdefault(session_id(s), key(dev))
        for a in d.get("agents") or []:
            by_agent.setdefault(a.lower(), key(dev))
    for c in commits:
        c.directed_by = None
        if c.klass != "ai":
            continue
        dev = next((by_session[s] for s in sessions_of(c) if s in by_session), None)
        if dev is None and c.agent:
            dev = by_agent.get(c.agent.lower())
        c.directed_by = dev


_NUMSTAT = re.compile(r"^(\d+|-)\t(\d+|-)\t(.+)$")
_LOG_FORMAT = "%x1e%H%x1f%P%x1f%aI%x1f%aN%x1f%aE%x1f%s%x1f%b%x1d"  # %aN/%aE honour .mailmap


def git_log(
    root: Path, args: list[str], shas: list[str] | None = None, git_opts: list[str] | None = None
) -> str:
    """`git log <args>` over history from HEAD, or over exactly `shas` (fed on stdin with
    `--no-walk`, so nothing else is walked). `git_opts` go before `log` (`-c key=value`).
    Output is decoded leniently."""
    cmd = ["git", *(git_opts or []), "log", *args]
    if shas is not None:
        at = cmd.index("log") + 1
        cmd[at:at] = ["--no-walk=unsorted", "--stdin"]
    return subprocess.run(
        cmd,
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        input="\n".join(shas) + "\n" if shas is not None else None,
    ).stdout


def parse_log(
    root: Path, shas: list[str] | None = None, limit: int | None = None
) -> list[dict[str, Any]]:
    """What `git log --numstat` says about each commit, newest first (in log order when
    walking from HEAD; in no particular order for `shas`): sha, parents, author date, name and
    email, subject, body, and per-file changed lines. Plain data, so the parsed-commit cache
    (cache.py) can store it; `commit_from` turns one into a Commit."""
    if shas is not None and not shas:
        return []
    args = [f"--format={_LOG_FORMAT}", "--numstat", "--no-renames"]
    if limit:
        args.append(f"-n{limit}")
    out = git_log(root, args, shas)
    raws: list[dict[str, Any]] = []
    for rec in out.split("\x1e"):
        if not rec.strip():
            continue
        head, _, stat = rec.partition("\x1d")
        parts = head.split("\x1f", 6)
        if len(parts) < 7:
            continue
        sha, parents, date, an, ae, subject, body = parts
        files: list[list[Any]] = []
        ins = dels = 0
        for line in stat.splitlines():
            m = _NUMSTAT.match(line)
            if m:
                a = int(m.group(1)) if m.group(1).isdigit() else 0
                d = int(m.group(2)) if m.group(2).isdigit() else 0
                ins += a
                dels += d
                files.append([m.group(3), a + d])
        raws.append(
            {
                "sha": sha.strip(),
                "parents": parents.split(),
                "date": date,
                "author_name": an,
                "author_email": ae,
                "subject": subject,
                "body": body.strip(),
                "files": files,
                "insertions": ins,
                "deletions": dels,
            }
        )
    return raws


def commit_from(raw: dict[str, Any]) -> Commit:
    """A Commit from one `parse_log` record, not yet classified."""
    return Commit(
        sha=raw["sha"],
        date=raw["date"],
        author_name=raw["author_name"],
        author_email=raw["author_email"],
        subject=raw["subject"],
        body=raw["body"],
        files=[f for f, _ in raw["files"]],
        insertions=raw["insertions"],
        deletions=raw["deletions"],
        file_lines={f: n for f, n in raw["files"]},
        parents=list(raw["parents"]),
    )


def classify_all(
    commits: list[Commit], sigs: list[Signature] | None = None, bots: list[Signature] | None = None
) -> list[Commit]:
    sigs = sigs or load_signatures()
    bots = bots if bots is not None else load_bots()
    for c in commits:
        c.klass, c.agent = classify(c, sigs, bots)
    return commits


def load_commits(
    root: Path,
    sigs: list[Signature] | None = None,
    limit: int | None = None,
    bots: list[Signature] | None = None,
) -> list[Commit]:
    """All commits reachable from HEAD, oldest first, with changed files, parents and
    classification. Parses the whole history every time; commands go through the
    parsed-commit cache instead (cache.load_commits)."""
    commits = [commit_from(r) for r in reversed(parse_log(root, limit=limit))]
    return classify_all(commits, sigs, bots)


def _emails(dev: str | set[str] | None) -> set[str]:
    if not dev:
        return set()
    return {dev.lower()} if isinstance(dev, str) else {e.lower() for e in dev}


def summarize(commits: list[Commit], dev: str | set[str] | None = None) -> dict[str, Any]:
    """Commit counts by class, by agent (ai and assisted) and by bot, the developer's own
    count (bots are nobody's) and the agent commits they directed."""
    mine = _emails(dev)
    out: dict[str, Any] = {
        "total": len(commits),
        "ai": 0,
        "assisted": 0,
        "human": 0,
        "bot": 0,
        "agents": {},
        "bots": {},
        "dev": 0,
        "directed": 0,
    }
    for c in commits:
        out[c.klass] += 1
        if c.agent:
            key = "bots" if c.klass == "bot" else "agents"
            out[key][c.agent] = out[key].get(c.agent, 0) + 1
        if c.klass != "bot" and c.author_email.lower() in mine:
            out["dev"] += 1
        elif c.klass == "ai" and c.directed_by in mine:
            out["directed"] += 1
    return out


@dataclass
class UnitProvenance:
    """Counts and weighted mass of the commits touching a unit. The `*_w` fields sum commit
    weights (see weights.py); the plain fields count commits. Bot commits are counted in
    `bot` and `bot_w` only: `total`, `total_w` and every share leave them out."""

    total: int = 0  # ai + assisted + human
    ai: int = 0
    assisted: int = 0
    human: int = 0
    bot: int = 0
    dev_human: int = 0
    dev_assisted: int = 0
    dev_directed: int = 0  # agent commits the developer directed (config `directed`)
    others_since_dev: int = 0  # commits by anyone else after the developer's last own commit
    total_w: float = 0.0
    ai_w: float = 0.0
    assisted_w: float = 0.0
    bot_w: float = 0.0
    dev_w: float = 0.0  # own human commits at full weight, own assisted and directed at half
    others_since_dev_w: float = 0.0
    first_date: str | None = None
    last_date: str | None = None

    @property
    def ai_share(self) -> float:
        return (self.ai_w + 0.5 * self.assisted_w) / self.total_w if self.total_w else 0.0

    @property
    def dev_share(self) -> float:
        """Weighted share of the unit the developer wrote; AI-assisted commits and agent
        commits they directed count half."""
        return self.dev_w / self.total_w if self.total_w else 0.0


def unit_provenance(
    commits: list[Commit],
    paths: list[str],
    dev: str | set[str] | None,
    weights: dict[str, Any] | None = None,
    split: Callable[[str], float] | None = None,
) -> UnitProvenance:
    """Provenance of the commits touching `paths`. `dev` is the developer's email, or the set
    of all addresses they have committed under. `weights` maps sha -> CommitWeight; without
    it every commit weighs 1. `split` shares a file several units list (units.Membership).
    Bot commits are tallied apart and decay nobody's prior. An agent commit the developer
    directed (`Commit.directed_by`) counts as their assisted work: half weight towards their
    share, and it does not erode their prior."""
    p = UnitProvenance()
    mine = _emails(dev)
    since, since_w = 0, 0.0
    for c in commits:  # oldest first
        if not c.touches(paths):
            continue
        if c.klass == "bot":
            p.bot += 1
            p.bot_w += (weights[c.sha].weight if weights and c.sha in weights else 1.0) * (
                c.attribution(paths, split=split)
            )
            continue
        w = landed(c, weights) * c.attribution(paths, split=split)  # the part landing here
        p.total += 1
        p.total_w += w
        setattr(p, c.klass, getattr(p, c.klass) + 1)
        if c.klass == "ai":
            p.ai_w += w
        elif c.klass == "assisted":
            p.assisted_w += w
        p.first_date = p.first_date or c.date
        p.last_date = c.date
        if c.klass == "ai" and c.directed_by is not None and c.directed_by in mine:
            p.dev_directed += 1
            p.dev_w += 0.5 * w
            since, since_w = 0, 0.0
        elif c.author_email.lower() in mine and c.klass != "ai":
            if c.klass == "human":
                p.dev_human += 1
                p.dev_w += w
            else:
                p.dev_assisted += 1
                p.dev_w += 0.5 * w
            since, since_w = 0, 0.0
        else:
            since += 1
            since_w += w
    has_own = bool(p.dev_human or p.dev_assisted or p.dev_directed)
    p.others_since_dev = since if has_own else p.total
    p.others_since_dev_w = round(since_w if has_own else p.total_w, 3)
    p.total_w, p.ai_w, p.assisted_w, p.dev_w, p.bot_w = (
        round(p.total_w, 3),
        round(p.ai_w, 3),
        round(p.assisted_w, 3),
        round(p.dev_w, 3),
        round(p.bot_w, 3),
    )
    return p
