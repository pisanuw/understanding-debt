# Deploying udebt

Deployment follows **HOSTING.md** (branch `claude/jolly-hamilton-pvos83`), the brief for the
hosted udebt app on the HintWise platform: a Vite + TypeScript app on Netlify from the platform
template, Supabase Auth (Google and email sign-in through HintWise), Netlify Functions for light
server code, a Python worker on Render, model calls through the HintWise LLM gateway or the
user's own key, and the Supabase schema `app_udebt` with own-row security on `auth.uid()`.
That work is Phase 4b and waits on the owner's decisions listed at the end of HOSTING.md
(who builds it, the slug, model use, the worker service, repository scope, shared analyses,
the samples). Nothing here deploys anything.

## What Phase 4a produced for it

| piece | where | what 4b does with it |
|---|---|---|
| the contract | `udebt/contract.py`, `udebt validate` | the shapes the app, its functions and the worker exchange (export, import payload, records, map, specs); version 1 |
| the analysis worker | `udebt/worker.py`, `udebt worker run <url> --rev <sha> --out <dir>` | the Render worker's core: a transient clone within limits, the offline analysis, contract files out, the clone deleted; the model steps move to the gateway (a tool loop over the clone) in 4b |
| sample analyses | `udebt worker run ... --map <map.json>` or `--llm`, then `udebt worker sql <dir>` | the three samples (huggingface_hub, sentry-javascript, mlflow) made locally on the subscription and loaded by the Plabs session's admin workflow, with the owner's yes |
| the schema | `supabase/migrations/20261115000000_app_udebt.sql` | copied byte for byte into `claude-built-apps/udebt/supabase/migrations/` and applied by the Plabs session; never edited once applied |
| a SQLite twin | `supabase/sqlite/app_udebt.sql` | local runs and tests without Postgres |
| push and pull | `udebt/remote.py`, `udebt push`, `udebt pull` | the CLI's sync with the app: a function serving `POST <url>/import` and `GET <url>/pull` over the contract, acting with the person's token |
| the pull-request check | `udebt/checks.py`, `udebt/webhooks.py`, `udebt risk --check-run` | a GitHub App's check runs and webhook routing, if the owner wants PR checks (HOSTING.md leaves them out of v1) |

## What was tested here, and what was not

- Tested: the contract against every file the CLI writes and the exports of digitalme,
  dsa-instructor and huggingface_hub; the worker on fixture repositories, digitalme and
  dsa-instructor; the migration against Postgres 16 with a stub of Supabase's `auth` schema
  (own rows, shared samples, no cross-user writes, nothing for `anon`, account deletion
  cascades), applied twice; push and pull against a stub host; check runs and webhook routing
  from trimmed payload fixtures.
- Not tested: Supabase itself (its `auth.users`, roles and API), Netlify, Render, the HintWise
  gateway, a GitHub App and real webhook deliveries; no cloud account was available.

## Checks before 4b starts

```bash
ruff check . && ruff format --check . && pytest -q
UDEBT_TESTBEDS=1 pytest -q tests/test_contract.py              # the local testbeds' exports
UDEBT_TEST_PG=postgresql://postgres@127.0.0.1:5432/postgres pytest -q tests/test_schema.py
```

The parked first draft of a full hosted API (Starlette, GitHub OAuth, organisations and roles)
is under `drafts/hosted-api/`, untested; its README says what in it may help 4b.
