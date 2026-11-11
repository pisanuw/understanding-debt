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
