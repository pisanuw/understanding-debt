"""LLM backends. The real one shells out to the `claude` CLI (subscription-billed, no API key).

`FakeLLM` returns canned structured answers so the CLI can be tested end to end offline.
Select with UDEBT_LLM=fake.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
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
            cmd += ["--allowedTools", *self.READ_TOOLS]
        else:
            cmd += ["--tools", ""]
        if self.verbose:
            mode = "read" if read_tools else "none"
            print(f"[udebt] claude call ({len(prompt)} chars, tools={mode})")
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


class FakeLLM:
    """Deterministic stand-in. Looks at a marker in the prompt to decide what to return."""

    def __init__(self, *_: Any, **__: Any):
        self.calls: list[str] = []

    def structured(self, prompt, schema, *, cwd, read_tools=True, max_turns=40):
        self.calls.append(prompt)
        if "TASK: map" in prompt:
            return {
                "units": [
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
                        "shaped_by": _fake_shas(prompt),
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
                        "shaped_by": [],
                    },
                ]
            }
        if "TASK: questions" in prompt:
            ids = [
                line.split(":", 1)[1].strip()
                for line in prompt.splitlines()
                if line.startswith("- unit_id:")
            ]
            return {
                "questions": [
                    {
                        "unit_id": u,
                        "question": f"What happens in {u} when the input is empty?",
                        "reference_answer": f"{u} returns a default.",
                        "evidence": [f"udebt/{u}.py"],
                    }
                    for u in ids
                ]
            }
        if "TASK: grade" in prompt:
            n = prompt.count("ANSWER:")
            verdicts = []
            for i in range(n):
                ans_line = [ln for ln in prompt.splitlines() if ln.startswith("ANSWER:")][i]
                ans = ans_line[len("ANSWER:") :].strip().lower()
                verdict = "correct" if "default" in ans else ("unknown" if not ans else "wrong")
                verdicts.append({"index": i, "verdict": verdict, "feedback": f"fake feedback {i}"})
            return {"grades": verdicts}
        raise LLMError(f"FakeLLM has no canned answer for this prompt: {prompt[:80]}")

    def text(self, prompt, *, cwd, read_tools=True, max_turns=40):
        self.calls.append(prompt)
        return "# Walkthrough\n\nThis is a fake walkthrough.\n\nCHECK QUESTION: What is 1+1?\n"


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
