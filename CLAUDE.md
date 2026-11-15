# CLAUDE.md

Python 3.11+ CLI, stdlib only at runtime. Package in `udebt/`, tests in `tests/`.

Before committing:

```bash
ruff check . && ruff format --check .
pytest -q
```

Scoring math lives in `udebt/scoring.py` and is documented in README. Change the math and
the README table together. Prompts are in `udebt/prompts.py`; each starts with a `TASK:`
marker the `FakeLLM` in `udebt/llm.py` keys on, so a new prompt needs a canned fake answer.

Read `BRIEFING.md` for scope and decisions. Append to `CHANGES.md` (one line,
`YYYY-MM-DD [type] description`) when scope, decisions or non-obvious code change.

## Working with the owner

When a question for the owner comes up, ask it in the message text with a numbered list and
a recommendation, keep working on everything that does not depend on the answer, and wait
15 minutes. If no answer arrives, proceed with the recommendation and say in the next
message what was chosen and how to undo it. Never block on a dialog. Actions that need an
explicit yes regardless of the timer: writing production data, deploying to production,
spending money (paid API calls, including any Anthropic API key), deleting or overwriting
what cannot be restored, rotating secrets, rewriting shared history, publishing outside
the session. Report times in Lisbon time until told otherwise.
