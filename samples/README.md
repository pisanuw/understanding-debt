# The hosted app's sample analyses

These SQL scripts load the shared samples for udebt.hintwise.org (HOSTING.md, owner decisions 7
and 9) into schema `app_udebt`. The Plabs session runs each script with the Supabase admin
workflow (`sql <file>`, after the owner's yes), then runs `check-schema app_udebt`. Each script is
idempotent: it upserts the repository by URL and replaces that commit's shared analysis in one
transaction. The scripts hold no code lines (the app fetches excerpts from GitHub at the pin), no
export and no commit emails.

| file | repository | pin | licence | commits | units | walkthroughs | sha256 |
|---|---|---|---|--:|--:|--:|---|
| `huggingface_hub.sql` | huggingface/huggingface_hub | `2c61ae8c11ee` | Apache-2.0 | 2,920 | 18 | 12 | `c02548c5…aad4e6` |
| `sentry-javascript.sql` | getsentry/sentry-javascript | `6bee2f2ccccd` | MIT | 17,695 | 15 | 12 | `e0e87f26…4ed8a7` |
| `mlflow.sql` | mlflow/mlflow | `f6ff35427d7e` | Apache-2.0 | 14,477 | 30 | 12 | `57a6b311…aab6d9` |
| `understanding-debt.sql` | pisanuw/understanding-debt | `e9c1c8c5754f` | none | 109 | 19 | 12 | `11af30cb…b0b72e` |

## Loaded (2026-11-15)

`understanding-debt.sql` was loaded later the same day, with the owner's yes: claude-built-apps
bdfeb9c, `sql` run 43715703125, then `check-schema app_udebt` run 43715769274, which passed
with 4 repositories and 4 analyses.

The Plabs session loaded the first three into production with the owner's yes, from
claude-built-apps 6ff0efc (copied byte for byte; the hashes match):
- `migrate udebt` (run 43706780478): 2 migrations applied;
- `expose-schema app_udebt` (43706833269);
- `sql` huggingface_hub (43706890759), sentry-javascript (43706915139), mlflow (43706949963);
- `check-schema app_udebt` (43706985331): the security check passed. 3 repositories and 3
  analyses, readable by everyone by design (`SHARED_TABLES`); the other four tables are empty,
  with RLS on.

## How they were made (2026-11-15, the "UDebt: Nov 12" session)

1. `udebt worker run https://github.com/<repo> --rev <pin> --out <dir> --llm --walkthroughs 12
   --no-excerpts`, run with the `claude` CLI on the owner's subscription. Each sample took 15 calls.
   - sentry-javascript and mlflow ran on commit ac89693 with `--no-export`;
   - huggingface_hub ran on e239f56 and wrote an export, which the loader leaves out.
2. `python -m udebt.hosted.samples <three dirs> --out samples` on ac89693. `udebt/` is unchanged
   at ff0d807, and the checker reports no problems for any of the three.
3. understanding-debt was made the same way, at the pin of its refreshed committed map
   (e9c1c8c), with the code at 6346fe6, which has the `ClaudeCLI` isolation (f294414). The map
   came from the repository, so the run took 14 calls (orientations, tour and 12 walkthroughs)
   and 328 s.

Each `claude` call went through a wrapper that adds these flags:
- `--setting-sources user --strict-mcp-config`;
- `--tools Read Glob Grep` whenever udebt allows read tools.

Without them, `claude -p` runs the analysed repository's own hooks. A first attempt at
sentry-javascript did exactly that: it installed and built the repository inside the clone. That
run was discarded (TESTBEDS.md, "Sample prep"). huggingface_hub has no `.claude/settings.json` and
no `.mcp.json`, so its run was not affected.

## Units that name only directories

These outputs predate the worker's `files` (2026-11-15), so 14 units name only directories with
no file to quiz from: 12 on mlflow, 1 each on the others (the loader's summary of a worker output
counts them as `tree_fallback`). The app lists those directories from GitHub's tree at the pin
(`/api/tree`), so the scripts need no reload. A regenerated sample would carry `files` and need
no listing.

## Regenerating

The worker outputs (`analysis.json`, `map.json`, `specs.json`) are in that session's scratchpad,
which is not durable. If the loader or the schema changes, rerun step 1 (about 10 minutes and 15
calls per sample) and then step 2.
