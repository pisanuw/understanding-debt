"""LLM backends. The real one shells out to the `claude` CLI (subscription-billed, no API key).

`FakeLLM` returns canned structured answers so the CLI can be tested end to end offline.
Select with UDEBT_LLM=fake.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol


class LLMError(Exception):
    pass


class LLM(Protocol):
    def structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        cwd: Path,
        read_tools: bool = True,
        max_turns: int = 40,
    ) -> dict[str, Any]: ...

    def text(
        self, prompt: str, *, cwd: Path, read_tools: bool = True, max_turns: int = 40
    ) -> str: ...

    def chat(
        self,
        message: str,
        *,
        cwd: Path,
        session_id: str,
        resume: bool = False,
        system: str | None = None,
        max_turns: int = 12,
    ) -> str: ...


# udebt's own directory holds earlier answers, grades, reference answers (pooled questions in
# map.json, experiment reports, samples, digests, tutor transcripts): no call that writes or
# grades questions may read it. The CLI is told so in the prompt and denied it by a permission
# rule (a Read deny rule, which Claude Code also applies to Grep and Glob).
UDEBT_DENY = ["Read(./.udebt/**)"]
UDEBT_NOTE = (
    "\n\n(Never open, list or search anything under `.udebt/`: it holds udebt's own records, "
    "earlier answers and grades, not the code.)"
)


class ClaudeCLI:
    READ_TOOLS = ["Read", "Glob", "Grep"]

    def __init__(self, model: str | None = None, verbose: bool = False):
        self.model = model
        self.verbose = verbose
        if shutil.which("claude") is None:
            raise LLMError(
                "The `claude` CLI is not on PATH. Install Claude Code or set UDEBT_LLM=fake."
            )

    def _run(
        self,
        prompt: str,
        *,
        cwd: Path,
        read_tools: bool,
        max_turns: int,
        schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        cmd = [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--max-turns",
            str(max_turns),
        ]
        if self.model:
            cmd += ["--model", self.model]
        if schema is not None:
            cmd += ["--json-schema", json.dumps(schema)]
        if read_tools:
            cmd += ["--allowedTools", *self.READ_TOOLS, "--disallowedTools", *UDEBT_DENY]
            prompt += UDEBT_NOTE
        else:
            cmd += ["--tools", ""]
        if self.verbose:
            mode = "read" if read_tools else "none"
            # stderr, so a command whose stdout is data (quiz --preview, --format) stays clean
            print(f"[udebt] claude call ({len(prompt)} chars, tools={mode})", file=sys.stderr)
        proc = subprocess.run(
            cmd, input=prompt, cwd=cwd, capture_output=True, text=True, encoding="utf-8"
        )
        if proc.returncode != 0:
            raise LLMError(f"claude exited {proc.returncode}: {proc.stderr.strip()[:2000]}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise LLMError(f"claude returned non-JSON output: {proc.stdout[:500]}") from e
        if data.get("is_error"):
            raise LLMError(f"claude reported an error: {data.get('result', '')[:2000]}")
        return data

    def structured(self, prompt, schema, *, cwd, read_tools=True, max_turns=40):
        data = self._run(prompt, cwd=cwd, read_tools=read_tools, max_turns=max_turns, schema=schema)
        out = data.get("structured_output")
        if out is None:
            # Older CLIs put the JSON string in `result`.
            try:
                out = json.loads(data.get("result", ""))
            except json.JSONDecodeError as e:
                raise LLMError("claude did not return structured output") from e
        return out

    def text(self, prompt, *, cwd, read_tools=True, max_turns=40):
        data = self._run(prompt, cwd=cwd, read_tools=read_tools, max_turns=max_turns, schema=None)
        return str(data.get("result", ""))

    def chat(self, message, *, cwd, session_id, resume=False, system=None, max_turns=12):
        """One turn of a conversation the `claude` CLI keeps (the tutor, udebt serve): the
        first turn names the session (`--session-id`) and appends the system prompt, later
        turns `--resume` it, whose snapshot keeps that prompt. Read-only tools only, given
        both as the tools available and as the tools allowed without asking. The session is
        persisted by the CLI (no `--no-session-persistence`), or it could not be resumed."""
        cmd = ["claude", "-p", "--output-format", "json", "--max-turns", str(max_turns)]
        if self.model:
            cmd += ["--model", self.model]
        if resume:
            cmd += ["--resume", session_id]
        else:
            cmd += ["--session-id", session_id]
            if system:
                cmd += ["--append-system-prompt", system]
        cmd += ["--tools", *self.READ_TOOLS, "--allowedTools", *self.READ_TOOLS]
        cmd += ["--disallowedTools", *UDEBT_DENY]
        if self.verbose:
            how = "resume" if resume else "new"
            print(f"[udebt] claude chat turn ({how} {session_id[:8]})", file=sys.stderr)
        proc = subprocess.run(
            cmd, input=message, cwd=cwd, capture_output=True, text=True, encoding="utf-8"
        )
        if proc.returncode != 0:
            raise LLMError(f"claude exited {proc.returncode}: {proc.stderr.strip()[:2000]}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise LLMError(f"claude returned non-JSON output: {proc.stdout[:500]}") from e
        if data.get("is_error"):
            raise LLMError(f"claude reported an error: {str(data.get('result', ''))[:2000]}")
        return str(data.get("result", ""))


class FakeLLM:
    """Deterministic stand-in. Looks at a marker in the prompt to decide what to return."""

    def __init__(self, *_: Any, **__: Any):
        self.calls: list[str] = []

    def structured(self, prompt, schema, *, cwd, read_tools=True, max_turns=40):
        self.calls.append(prompt)
        if "TASK: map" in prompt:
            canned = [u for u in _CANNED_UNITS if any(p in prompt for p in u["paths"])]
            canned = [
                {**u, "shaped_by": _fake_shas(prompt) if u["id"] == "storage" else []}
                for u in canned
            ]
            covered = {p for u in canned for p in u["paths"]}
            return {"units": canned + _units_from_clusters(prompt, covered)}
        if "TASK: questions" in prompt or "TASK: stable-questions" in prompt:
            units = _unit_blocks(prompt)
            m = re.search(r"Write\s+(?:exactly\s+)?(\d+)\s+question", prompt)
            n = int(m.group(1)) if m else 1
            return {
                "questions": [
                    {
                        "unit_id": u["id"],
                        "level": u["level"],
                        "question": _FAKE_QUESTIONS[u["level"]][k % 2].format(u=u["id"])
                        + (f" ({k + 1})" if k > 1 else ""),
                        "variants": [
                            f"In other words, what does the {u['id']} unit do (v{k + 1}a)?",
                            f"Why does the tool need the {u['id']} unit (v{k + 1}b)?",
                        ],
                        "reference_answer": f"{u['id']} returns a default.",
                        "evidence": [f"udebt/{u['id']}.py"],
                    }
                    for u in units
                    for k in range(n)
                ]
            }
        if "TASK: orientation" in prompt:
            return {
                "orientations": [
                    {
                        "unit_id": u["id"],
                        "purpose": f"The {u['id']} unit keeps what the rest of the tool reads.",
                        "decision": "Plain files rather than a database, for no dependencies.",
                        "where": [{"path": p, "what": "where it lives"} for p in u["paths"]]
                        + [{"path": "no/such/file.py", "what": "an invented path"}],
                        "shaped_by": "Dev One wrote it by hand.",
                    }
                    for u in _unit_blocks(prompt)
                ]
            }
        if "TASK: tour" in prompt:
            ids = [u["id"] for u in _unit_blocks(prompt)]
            return {
                "overview": "A fake tool of " + str(len(ids)) + " units that keep and score state.",
                "units": [
                    {"unit_id": uid, "paragraph": f"The {uid} unit is described here (fake)."}
                    for uid in ids
                ],
            }
        if "TASK: walkthrough" in prompt:
            unit = json.JSONDecoder().raw_decode(prompt[prompt.index("Unit: ") + 6 :])[0]
            files = [p for p in unit.get("paths", []) if "." in p.rsplit("/", 1)[-1]]
            steps = [
                {"file": f, "start_line": 1, "end_line": 40 if i == 0 else 2,
                 "narration": f"This is a fake walkthrough step {i + 1} in {f}."}
                for i, f in enumerate((files or ["no/such/file.py"]) * 3)
            ][:3]  # fmt: skip
            steps.append({"file": "no/such/file.py", "start_line": 1, "end_line": 2,
                          "narration": "An invented file, dropped."})  # fmt: skip
            uid = unit["id"]
            return {
                "flow": f"One call into the {uid} unit.",
                "steps": steps,
                "trap": "It looks stateful; it is not.",
                "checks": [
                    {
                        "level": n,
                        "question": _FAKE_QUESTIONS[n][0].format(u=uid),
                        "reference_answer": f"{uid} returns a default.",
                    }
                    for n in range(1, 6)
                ],  # fmt: skip
            }
        if "TASK: teachback" in prompt:
            said = prompt.split("THE DEVELOPER'S EXPLANATION:", 1)[1].strip().lower()
            verdict = (
                "unknown" if not said else "correct" if "default" in said
                else "partial" if "half" in said else "wrong"
            )  # fmt: skip
            return {
                "verdict": verdict,
                "right": "The purpose is right." if verdict != "wrong" else "",
                "missing": "" if verdict == "correct" else "The decision behind it.",
                "misconception": "It does not send email." if verdict == "wrong" else "",
                "reference_answer": "It returns a default, by design.",
            }
        if "TASK: digest" in prompt:
            out = []
            for block in prompt.split("### unit_id: ")[1:]:
                uid = block.split("\n", 1)[0].strip()
                shas = re.findall(r"^    - ([0-9a-f]{7}) ", block, re.M)
                out.append(
                    {
                        "unit_id": uid,
                        "headline": f"The {uid} unit gained a fake feature in this range.",
                        "changes": [
                            {"what": f"It now does one more thing ({uid}).", "commits": shas[:2]}
                        ],
                        "decision": "extended" if len(shas) > 1 else "held",
                        "decision_note": "The original choice stands and now covers more.",
                        "look_at": [f"udebt/{uid}.py"],
                    }
                )
            return {"digests": out}
        if "TASK: weigh" in prompt:
            shas = [ln.split()[2] for ln in prompt.splitlines() if ln.startswith("- sha:")]
            return {
                "commits": [
                    {"sha": sh, "klass": "architectural" if i == 0 else "cosmetic", "note": "fake"}
                    for i, sh in enumerate(shas)
                ]
            }
        if "TASK: answer" in prompt:
            # the synthetic developer: knows every other question, is blank on the rest
            n = sum(1 for ln in prompt.splitlines() if ln.startswith("### "))
            return {
                "answers": [
                    {
                        "index": i,
                        "answer": "It returns a default." if i % 2 == 0 else "",
                        "confidence": 2,
                    }
                    for i in range(n)
                ]
            }
        if "TASK: grade" in prompt:
            n = prompt.count("ANSWER:")
            verdicts = []
            for i in range(n):
                ans_line = [ln for ln in prompt.splitlines() if ln.startswith("ANSWER:")][i]
                ans = ans_line[len("ANSWER:") :].strip().lower()
                verdict = (
                    "correct" if "default" in ans else "unknown" if not ans
                    else "partial" if "half" in ans else "wrong"
                )  # fmt: skip
                verdicts.append({"index": i, "verdict": verdict, "feedback": f"fake feedback {i}"})
            return {"grades": verdicts}
        raise LLMError(f"FakeLLM has no canned answer for this prompt: {prompt[:80]}")

    def text(self, prompt, *, cwd, read_tools=True, max_turns=40):
        self.calls.append(prompt)
        raise LLMError(f"FakeLLM has no canned text for this prompt: {prompt[:80]}")

    chats: list[dict[str, Any]] = []  # every chat turn, across instances (the server makes many)

    def chat(self, message, *, cwd, session_id, resume=False, system=None, max_turns=12):
        FakeLLM.chats.append(
            {"session_id": session_id, "resume": resume, "system": system, "message": message}
        )
        reply = "Let us look at it together. What do you think it is for?"
        if "propose the check question now" in message:
            reply += (
                "\n\nCHECK QUESTION: What is the unit for?\n"
                "REFERENCE: It returns a default, by design."
            )
        return reply


# per level, a unit's first question (the one a single-question quiz gets) and its second;
# levels 1-3 name no identifier, as the real prompt requires
_FAKE_QUESTIONS = {
    1: ["What is the {u} unit for?",
        "What would a user notice first if the {u} unit stopped working?"],
    2: ["What was chosen for the {u} unit over its alternative?",
        "What constraint forced the choice behind the {u} unit?"],
    3: ["If the decision behind the {u} unit were reversed, what would have to change first?",
        "If the {u} unit were replaced, which unit would notice first?"],
    4: ["What happens in {u} when the input is empty?",
        "What happens in {u} when it is called twice?"],
    5: ["What happens in {u} when the input is empty?",
        "Why does {u} keep its state that way?"],
}  # fmt: skip


def _unit_blocks(prompt: str) -> list[dict[str, Any]]:
    """The units listed in a questions or orientation prompt: id, level (1 when none is
    given) and paths."""
    out: list[dict[str, Any]] = []
    for ln in prompt.splitlines():
        if ln.startswith("- unit_id:"):
            out.append({"id": ln.split(":", 1)[1].strip(), "level": 1, "paths": []})
        elif out and ln.startswith("  level:"):
            out[-1]["level"] = int(ln.split(":", 1)[1].split()[0])
        elif out and ln.startswith("  paths:"):
            out[-1]["paths"] = [p.strip() for p in ln.split(":", 1)[1].split(",") if p.strip()]
    return out


_CANNED_UNITS: list[dict[str, Any]] = [
    {
        "id": "storage",
        "name": "Persistence layer",
        "kind": "decision",
        "summary": "All state lives in JSON files under a dot-directory.",
        "decision": "Plain JSON files instead of a database.",
        "alternatives": "SQLite; a hosted database.",
        "why": "Zero dependencies and diff-friendly for git.",
        "paths": ["udebt/store.py"],
        "centrality": 4,
    },
    {
        "id": "scoring",
        "name": "Debt scoring model",
        "kind": "module",
        "summary": "Pure functions compute understanding and debt.",
        "decision": "Importance times (1 - understanding), with commit decay.",
        "alternatives": "Provenance-only scoring.",
        "why": "Provenance says nothing about architectural understanding.",
        "paths": ["udebt/scoring.py"],
        "centrality": 5,
    },
]
_CODE = (".py", ".js", ".jsx", ".ts", ".tsx")


def _cluster_entries(prompt: str) -> list[dict[str, Any]]:
    """Parse the structural cluster block of a map prompt: top clusters with sub-clusters."""
    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for ln in prompt.splitlines():
        m = re.match(r"^(\s*)- (C\d+(?:\.\d+)?) \[([^\]]*)\]", ln)
        if m:
            cur = {"id": m.group(2), "label": m.group(3), "files": [], "subs": []}
            if m.group(1):
                if out:
                    out[-1]["subs"].append(cur)
            else:
                out.append(cur)
            continue
        f = re.match(r"^\s+files: (.*)$", ln)
        if f and cur is not None:
            cur["files"] = [
                x.strip() for x in re.sub(r" \(\+\d+ more\)$", "", f.group(1)).split(",")
            ]
    return out


def _slug(label: str, fallback: str) -> str:
    head = label.split()[0].rstrip("/") if label.split() else ""
    name = head.split("/")[-1] if head and head != "." else ""
    return re.sub(r"[^a-z0-9]+", "-", (name or fallback).lower()).strip("-") or fallback.lower()


def _units_from_clusters(prompt: str, covered: set[str]) -> list[dict[str, Any]]:
    """One unit per cluster whose code no canned unit covers; children from sub-clusters, or
    from the cluster's directories when it has no sub-clusters but spans several."""
    units = []
    for c in _cluster_entries(prompt):
        code = [f for f in c["files"] if f.endswith(_CODE)]
        if not code or all(f in covered for f in code):
            continue
        groups = [(_slug(s["label"], s["id"]), s["files"]) for s in c["subs"]]
        if len(groups) < 2:
            by_dir: dict[str, list[str]] = {}
            for f in code:
                by_dir.setdefault(f.rsplit("/", 1)[0] if "/" in f else ".", []).append(f)
            groups = [(d.split("/")[-1], fs) for d, fs in sorted(by_dir.items())]
        uid = _slug(c["label"], c["id"])
        unit = {
            "id": uid,
            "name": f"Cluster {c['label']}",
            "kind": "module",
            "summary": f"Files clustered as {c['id']}.",
            "decision": "fake",
            "paths": code,
            "centrality": 3,
            "shaped_by": [],
            "clusters": [c["id"]],
        }
        if len(groups) >= 2:
            unit["children"] = [
                {
                    "id": name,
                    "name": f"{uid} {name}",
                    "kind": "module",
                    "summary": "fake sub-unit",
                    "decision": "fake",
                    "paths": [f for f in fs if f.endswith(_CODE)] or fs,
                    "centrality": 3,
                }
                for name, fs in groups
            ]
        units.append(unit)
    return units


def _fake_shas(prompt: str) -> list[str]:
    """Short shas from the history block of a map prompt, in order."""
    return [
        ln.split()[1]
        for ln in prompt.splitlines()
        if ln.startswith("- ") and len(ln.split()) > 2 and len(ln.split()[1]) == 7
    ]


def get_llm(model: str | None = None, verbose: bool = False) -> LLM:
    if os.environ.get("UDEBT_LLM", "").lower() == "fake":
        return FakeLLM()
    return ClaudeCLI(model=model, verbose=verbose)
