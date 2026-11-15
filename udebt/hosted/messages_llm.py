"""An `llm.LLM` over a Messages API, for the hosted worker (Phase 4b, HOSTING.md): the HintWise
LLM gateway with the signed-in person's token, or api.anthropic.com with the person's own key.

The gateway is a plain Messages API proxy: no file tools of its own, one model call per request,
`max_tokens` clamped (4096 by default), a body of at most 1,000,000 bytes, and only `model`,
`max_tokens`, `system`, `messages` and `tools` pass. So the worker runs the tool loop itself:

- the prompt is udebt's own (the map, orientations, tour, walkthroughs), which already carries
  the offline structure (clusters, history candidates, weights), so the model needs few reads;
- the model may call three read-only tools over the transient clone, named as in the CLI's
  Claude Code runs: `Read` (a line range of a tracked file), `Glob` (tracked paths matching a
  pattern) and `Grep` (a fixed string in tracked text files); each result is capped;
- it answers by calling `submit`, whose input schema is the prompt's JSON schema (the gateway
  does not pass `tool_choice`, so the system prompt asks for the call, and a JSON object in a
  text answer is accepted too);
- every turn resends the conversation, so turns are capped (`max_turns`, default 8; the last
  offers only `submit`), and when the body nears the limit the oldest tool results are cut.

Paths are checked against the clone: no absolute paths, no `..`, nothing under `.git/` or
`.udebt/`, nothing that resolves outside the clone (the worker also checks out symlinks as
plain files). The key or token is held by the transport only; nothing here logs or stores it.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..llm import LLMError

Transport = Callable[[dict[str, Any]], dict[str, Any]]

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
GATEWAY_APP = "udebt"
GATEWAY_FIELDS = ("model", "max_tokens", "system", "messages", "tools")
MAX_BODY = 1_000_000
GATEWAY_MAX_TOKENS = 4096
READ_LINES = 400
READ_CHARS = 40_000
GLOB_RESULTS = 200
GREP_MATCHES = 100
GREP_FILE_BYTES = 512_000
FILE_BYTES = 1_000_000
DENIED = (".git", ".udebt")

SYSTEM = """You are udebt's analyst, working on a repository checked out at one commit. You can read
it only through the tools Read, Glob and Grep, and only tracked files. Read sparingly: every
turn resends the whole conversation, and you have at most {turns} turns. Answer by calling the
tool `submit` exactly once, with input that matches its schema; do not answer in plain text."""


# -- transports ------------------------------------------------------------------------------


def _post(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"content-type": "application/json", **headers}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 - fixed https URLs
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
        except (ValueError, OSError):
            detail = {}
        if e.code == 429 and detail.get("error") == "cap":
            raise LLMError(
                f"the HintWise allowance ({detail.get('scope')}) is used up until "
                f"{detail.get('resets_at')}"
            ) from None
        msg = detail.get("message") or (detail.get("error") or {})
        if isinstance(msg, dict):
            msg = msg.get("message", "")
        raise LLMError(f"the model service answered {e.code}: {str(msg)[:300]}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"the model service could not be reached: {e}") from None


def gateway_body(body: dict[str, Any], max_tokens: int = GATEWAY_MAX_TOKENS) -> dict[str, Any]:
    """The gateway's request: `app` and only the five fields it passes, max_tokens clamped."""
    out: dict[str, Any] = {"app": GATEWAY_APP}
    for k in GATEWAY_FIELDS:
        if body.get(k) is not None:
            out[k] = body[k]
    out["max_tokens"] = max(1, min(int(out.get("max_tokens") or max_tokens), max_tokens))
    return out


class GatewayTransport:
    """The HintWise gateway with the signed-in person's session token (it counts against their
    caps); the gateway checks the token and needs no Origin header."""

    def __init__(
        self, url: str, token: str, max_tokens: int = GATEWAY_MAX_TOKENS, timeout: float = 300
    ):
        self.url, self._token, self.max_tokens, self.timeout = url, token, max_tokens, timeout

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        return _post(
            self.url,
            gateway_body(body, self.max_tokens),
            {"authorization": f"Bearer {self._token}"},
            self.timeout,
        )

    def __repr__(self) -> str:  # never show the token
        return f"GatewayTransport({self.url})"


class AnthropicTransport:
    """api.anthropic.com with the person's own key, for this job only."""

    def __init__(self, api_key: str, timeout: float = 300, url: str = ANTHROPIC_URL):
        self._key, self.timeout, self.url = api_key, timeout, url

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        sent = {k: body[k] for k in GATEWAY_FIELDS if body.get(k) is not None}
        return _post(
            self.url,
            sent,
            {"x-api-key": self._key, "anthropic-version": ANTHROPIC_VERSION},
            self.timeout,
        )

    def __repr__(self) -> str:  # never show the key
        return "AnthropicTransport()"


# -- the read-only tools ---------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "Read",
        "description": "Read a tracked file of the repository: up to 400 numbered lines from "
        "start_line (default 1).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "relative to the repository root"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
        },
    },
    {
        "name": "Glob",
        "description": "List tracked paths matching a glob pattern such as src/**/*.py (at "
        "most 200).",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "Grep",
        "description": "Find a fixed string (case-insensitive) in tracked text files, "
        "optionally only in paths matching a glob; at most 100 matching lines.",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}, "glob": {"type": "string"}},
            "required": ["pattern"],
        },
    },
]


def glob_regex(pattern: str) -> re.Pattern[str]:
    """A glob as git paths read it: `**/` any directories (or none), `*` within one segment,
    `?` one character; a pattern without a slash matches the name in any directory."""
    pat = pattern if "/" in pattern else f"**/{pattern}"
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pat.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pat[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pat[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pat[i]), i + 1
    return re.compile(out)


class RepoTools:
    """Read, Glob and Grep over a checkout, inside it only."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._files: list[str] | None = None

    def files(self) -> list[str]:
        if self._files is None:
            out = subprocess.run(
                ["git", "ls-files", "-z"], cwd=self.root, capture_output=True, check=False
            ).stdout.decode("utf-8", "replace")
            self._files = [f for f in out.split("\0") if f and f.split("/", 1)[0] not in DENIED]
        return self._files

    def resolve(self, path: str) -> Path:
        """The file inside the checkout, or ValueError."""
        p = str(path or "").strip().replace("\\", "/")
        root = str(self.root)
        if p.startswith(root + "/"):
            p = p[len(root) + 1 :]
        p = p.removeprefix("./")
        parts = p.split("/")
        if not p or p.startswith("/") or any(s in ("", ".", "..") for s in parts):
            raise ValueError(f"not a path inside the repository: {path}")
        if parts[0] in DENIED:
            raise ValueError(f"{parts[0]}/ is not part of the code")
        full = (self.root / p).resolve()
        if self.root != full and self.root not in full.parents:
            raise ValueError(f"{path} leads outside the repository")
        if not full.is_file():
            raise ValueError(f"no such file: {p}")
        return full

    def read(self, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        full = self.resolve(path)
        if full.stat().st_size > FILE_BYTES:
            return f"{path} is larger than {FILE_BYTES // 1000} KB; read a range of a smaller file."
        data = full.read_bytes()
        if b"\0" in data[:8000]:
            return f"{path} is a binary file."
        lines = data.decode("utf-8", "replace").splitlines()
        start = max(1, int(start_line or 1))
        end = min(len(lines), int(end_line or start + READ_LINES - 1), start + READ_LINES - 1)
        out, size = [], 0
        for n in range(start, end + 1):
            row = f"{n:>6}\t{lines[n - 1]}"
            size += len(row) + 1
            if size > READ_CHARS:
                out.append(f"... (cut at {READ_CHARS} characters; read from line {n})")
                break
            out.append(row)
        more = f"\n... ({len(lines) - end} more lines)" if end < len(lines) else ""
        return ("\n".join(out) or "(empty)") + more

    def glob(self, pattern: str) -> str:
        pat = str(pattern or "").strip().removeprefix("./")
        if not pat:
            return "Give a pattern."
        rx = glob_regex(pat)
        got = [f for f in self.files() if rx.fullmatch(f)]
        head = "\n".join(got[:GLOB_RESULTS])
        more = len(got) - GLOB_RESULTS
        return (head or "No tracked path matches.") + (f"\n... ({more} more)" if more > 0 else "")

    def grep(self, pattern: str, glob: str | None = None) -> str:
        needle = str(pattern or "").lower()
        if not needle:
            return "Give a string to find."
        out: list[str] = []
        for f in self.files():
            if glob and not fnmatch.fnmatch(f, glob):
                continue
            p = self.root / f
            try:
                if p.is_symlink() or p.stat().st_size > GREP_FILE_BYTES:
                    continue
                data = p.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:8000]:
                continue
            for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                if needle in line.lower():
                    out.append(f"{f}:{n}: {line.strip()[:200]}")
                    if len(out) >= GREP_MATCHES:
                        return "\n".join(out) + "\n... (more matches not shown)"
        return "\n".join(out) or "No match."

    def run(self, name: str, args: dict[str, Any]) -> str:
        try:
            if name == "Read":
                return self.read(args.get("path", ""), args.get("start_line"), args.get("end_line"))
            if name == "Glob":
                return self.glob(args.get("pattern", ""))
            if name == "Grep":
                return self.grep(args.get("pattern", ""), args.get("glob"))
        except (ValueError, OSError) as e:
            return f"Error: {e}"
        return f"Error: unknown tool {name}"


# -- the loop --------------------------------------------------------------------------------


def _size(body: dict[str, Any]) -> int:
    return len(json.dumps({"app": GATEWAY_APP, **body}).encode("utf-8"))


def _json_in(text: str) -> dict[str, Any] | None:
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        v = json.loads(text[a : b + 1])
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


class MessagesLLM:
    """udebt's LLM protocol (structured, text) over a Messages API transport."""

    def __init__(
        self,
        transport: Transport,
        *,
        model: str | None = None,
        max_tokens: int = GATEWAY_MAX_TOKENS,
        max_turns: int = 8,
        max_body: int = MAX_BODY - 50_000,
        log: Callable[[str], None] | None = None,
    ):
        self.transport = transport
        self.model = model
        self.max_tokens = max_tokens
        self.max_turns = max(1, max_turns)
        self.max_body = max_body
        self.log = log or (lambda _m: None)
        self.calls = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def _call(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.model:
            body = {"model": self.model, **body}
        self.calls += 1
        res = self.transport(body)
        for k in self.usage:
            self.usage[k] += int((res.get("usage") or {}).get(k) or 0)
        return res

    def _fit(self, body: dict[str, Any]) -> None:
        """Cuts the oldest tool results until the body fits; LLMError when nothing is left."""
        for msg in body["messages"]:
            if _size(body) <= self.max_body:
                return
            if msg["role"] != "user" or not isinstance(msg["content"], list):
                continue
            for block in msg["content"]:
                if block.get("type") == "tool_result" and block.get("content") != "[cut]":
                    block["content"] = "[cut]"
        if _size(body) > self.max_body:
            raise LLMError(
                f"the request is {_size(body)} bytes, over the {self.max_body} the gateway takes"
            )

    def structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        cwd: Path,
        read_tools: bool = True,
        max_turns: int = 40,
    ) -> dict[str, Any]:
        turns = min(self.max_turns, max(1, max_turns))
        tools = RepoTools(cwd) if read_tools else None
        submit = {
            "name": "submit",
            "description": "Submit the answer, once.",
            "input_schema": schema,
        }
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        for turn in range(turns):
            last = turn == turns - 1
            offered = ([*TOOLS] if tools and not last else []) + [submit]
            body = {
                "max_tokens": self.max_tokens,
                "system": SYSTEM.format(turns=turns),
                "messages": messages,
                "tools": offered,
            }
            self._fit(body)
            res = self._call(body)
            blocks = res.get("content") or []
            for b in blocks:
                if b.get("type") == "tool_use" and b.get("name") == "submit":
                    if res.get("stop_reason") == "max_tokens":
                        raise LLMError(
                            f"the answer was cut at max_tokens ({self.max_tokens}); "
                            "the app's max_tokens needs raising"
                        )
                    if isinstance(b.get("input"), dict):
                        return b["input"]
            uses = [b for b in blocks if b.get("type") == "tool_use"]
            if not uses:
                text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                got = _json_in(text)
                if got is not None:
                    return got
                messages += [
                    {
                        "role": "assistant",
                        "content": blocks or [{"type": "text", "text": "(no answer)"}],
                    },
                    {"role": "user", "content": "Call the tool `submit` with your answer now."},
                ]
                continue
            results = []
            for b in uses:
                if tools is None or last:
                    out = "No more reads: call `submit` with your answer now."
                else:
                    out = tools.run(str(b.get("name")), b.get("input") or {})
                    self.log(f"  {b.get('name')} {json.dumps(b.get('input'))[:120]}")
                results.append({"type": "tool_result", "tool_use_id": b.get("id"), "content": out})
            if turn == turns - 2:
                results.append(
                    {"type": "text", "text": "Your next turn is the last: call `submit` then."}
                )
            messages += [
                {"role": "assistant", "content": blocks},
                {"role": "user", "content": results},
            ]
        raise LLMError(f"no answer after {turns} turns")

    def text(self, prompt: str, *, cwd: Path, read_tools: bool = True, max_turns: int = 40) -> str:
        res = self._call(
            {"max_tokens": self.max_tokens, "messages": [{"role": "user", "content": prompt}]}
        )
        return "".join(
            b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text"
        )

    def chat(self, message: str, **_: Any) -> str:
        raise LLMError("the tutor is not available on the hosted worker")
