# The hosted app's three sample analyses

These SQL scripts load the three shared samples for udebt.hintwise.org (HOSTING.md, owner
decision 7) into schema `app_udebt`. The Plabs session runs each script with the Supabase admin
workflow (`sql <file>`, after the owner's yes), then runs `check-schema app_udebt`. Each script is
idempotent: it upserts the repository by URL and replaces that commit's shared analysis in one
transaction. The scripts hold no code lines (the app fetches excerpts from GitHub at the pin), no
export and no commit emails.

| file | repository | pin | licence | commits | units | walkthroughs | sha256 |
|---|---|---|---|--:|--:|--:|---|
| `huggingface_hub.sql` | huggingface/huggingface_hub | `2c61ae8c11ee` | Apache-2.0 | 2,920 | 18 | 12 | `c02548c5…aad4e6` |
| `sentry-javascript.sql` | getsentry/sentry-javascript | `6bee2f2ccccd` | MIT | 17,695 | 15 | 12 | `e0e87f26…4ed8a7` |
| `mlflow.sql` | mlflow/mlflow | `f6ff35427d7e` | Apache-2.0 | 14,477 | 30 | 12 | `57a6b311…aab6d9` |

## How they were made (2026-11-15, the "UDebt: Nov 12" session)

1. `udebt worker run https://github.com/<repo> --rev <pin> --out <dir> --llm --walkthroughs 12
   --no-excerpts`, run with the `claude` CLI on the owner's subscription. Each sample took 15 calls.
   - sentry-javascript and mlflow ran on commit ac89693 with `--no-export`;
   - huggingface_hub ran on e239f56 and wrote an export, which the loader leaves out.
2. `python -m udebt.hosted.samples <three dirs> --out samples` on ac89693. `udebt/` is unchanged
   at ff0d807, and the checker reports no problems for any of the three.

Each `claude` call went through a wrapper that adds these flags:
- `--setting-sources user --strict-mcp-config`;
- `--tools Read Glob Grep` whenever udebt allows read tools.

Without them, `claude -p` runs the analysed repository's own hooks. A first attempt at
sentry-javascript did exactly that: it installed and built the repository inside the clone. That
run was discarded (TESTBEDS.md, "Sample prep"). huggingface_hub has no `.claude/settings.json` and
no `.mcp.json`, so its run was not affected.

## Regenerating

The worker outputs (`analysis.json`, `map.json`, `specs.json`) are in that session's scratchpad,
which is not durable. If the loader or the schema changes, rerun step 1 (about 10 minutes and 15
calls per sample) and then step 2.
