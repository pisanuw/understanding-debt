"""The `claude` CLI is started so that the analysed repository adds nothing to the process:
only the user's settings (no project hooks, permissions or environment), no MCP server, and
exactly the tools udebt names (TESTBEDS.md, "Sample prep": a clone's SessionStart hook ran
`pnpm install` and a build on every call before these flags)."""

import json
import subprocess
from pathlib import Path

import pytest

from udebt import llm


@pytest.fixture
def calls(monkeypatch):
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"result": "ok", "structured_output": {"a": 1}}), stderr=""
        )

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    monkeypatch.setattr(llm.shutil, "which", lambda name: "/usr/bin/claude")
    return seen


def _flag_values(cmd: list[str], flag: str) -> list[str]:
    """The values after `flag` up to the next flag."""
    i = cmd.index(flag)
    out = []
    for tok in cmd[i + 1 :]:
        if tok.startswith("--"):
            break
        out.append(tok)
    return out


def _isolated(cmd: list[str]) -> None:
    assert _flag_values(cmd, "--setting-sources") == ["user"]
    assert "--strict-mcp-config" in cmd


def test_a_read_call_offers_and_allows_only_the_three_read_tools(calls, tmp_path):
    cli = llm.ClaudeCLI()
    assert cli.structured("TASK: x", {"type": "object"}, cwd=tmp_path) == {"a": 1}
    (cmd,) = calls
    _isolated(cmd)
    assert _flag_values(cmd, "--tools") == llm.ClaudeCLI.READ_TOOLS
    assert _flag_values(cmd, "--allowedTools") == llm.ClaudeCLI.READ_TOOLS
    assert _flag_values(cmd, "--disallowedTools") == llm.UDEBT_DENY
    assert "--no-session-persistence" in cmd


def test_a_call_without_tools_offers_none_and_is_isolated(calls, tmp_path):
    cli = llm.ClaudeCLI()
    assert cli.text("TASK: y", cwd=tmp_path, read_tools=False) == "ok"
    (cmd,) = calls
    _isolated(cmd)
    assert _flag_values(cmd, "--tools") == [""]
    assert "--allowedTools" not in cmd


def test_a_chat_turn_is_isolated_too(calls, tmp_path):
    cli = llm.ClaudeCLI()
    cli.chat("hello", cwd=tmp_path, session_id="s" * 36, system="be brief")
    cli.chat("more", cwd=tmp_path, session_id="s" * 36, resume=True)
    for cmd in calls:
        _isolated(cmd)
        assert _flag_values(cmd, "--tools") == llm.ClaudeCLI.READ_TOOLS
        assert _flag_values(cmd, "--allowedTools") == llm.ClaudeCLI.READ_TOOLS
        assert _flag_values(cmd, "--disallowedTools") == llm.UDEBT_DENY
    assert "--append-system-prompt" in calls[0] and "--resume" in calls[1]


def test_the_cwd_is_the_repository_not_udebt(calls, tmp_path):
    """The call runs in the repository (the tools read its code); the isolation flags, not
    the directory, keep the repository's settings out."""
    llm.ClaudeCLI().text("TASK: z", cwd=Path(tmp_path))
    assert calls  # ran
