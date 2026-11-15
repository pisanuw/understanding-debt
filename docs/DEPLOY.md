# Deploying udebt on HintWise

The hosted udebt app (Phase 4b, HOSTING.md): https://udebt.hintwise.org on Netlify, the
analysis service at https://udebt-api.hintwise.org on Render, data in the shared HintWise
Supabase project (schema `app_udebt`), sign-in through `https://auth.hintwise.org`, model
calls through the HintWise LLM gateway or the person's own key. **Status 2026-11-15: built and
tested locally with fakes; nothing is deployed.** Every production step below needs the owner's
explicit yes; the UDebt session made none of them.

## What is where

| part | in this repository | runs on |
|---|---|---|
| the web app (Vite + TypeScript, from the platform template) | `web/` (`web/README.md`) | Netlify, built by `netlify.toml` at the root |
| the functions `/api/questions`, `/api/grade` (also for review sittings), `/api/excerpt` | `web/netlify/functions/` | Netlify Functions in the same site |
| the analysis service | `udebt/hosted/worker_service.py` (`python -m udebt.hosted.worker_service`) | Render, `render.yaml` |
| the migrations and the Plabs notes | `supabase/migrations/0001_app_udebt.sql`, `0002_hosted.sql`, `supabase/README.md` | the Supabase admin workflow in claude-built-apps |
| the sample load | `python -m udebt.hosted.samples` | anywhere with Python 3.11+, then the admin workflow's `sql` |

No secret is stored anywhere: no Anthropic key, no Supabase service-role key. The functions and
the worker act with the signed-in person's own token; the gateway holds the owner's key; a
person's own key stays in their browser (and, for an analysis they start, is sent once to the
worker, used for that job and dropped).

## Already done (the UDebt session, branch `claude/eloquent-lamport-afzr0o`)

- The app, the functions, the worker service, the migrations, the sample loader, and their
  tests: Python `ruff` and `pytest` (the schema and sample tests on Postgres 16 with
  `UDEBT_TEST_PG`), web `npm run lint && npm run typecheck && npm run coverage && npm run
  build` (coverage 98% statements, 88% branches) and a Playwright walkthrough with fakes
  (sign in, open the digitalme sample, read a unit and its walkthrough, take a two-question
  level-1 quiz, see the debt fall, delete the account; miss a question, find it due in Review
  a day later on Playwright's fake clock, review it, see its state move) under the same
  Content-Security-Policy the live site sends.
- The spaced review queue (Review tab): no migration, no new function and no new variable; the
  review state rides in `records.data`, and `/api/grade` reads the person's earlier records on
  a sitting's units with their token to stamp it.
- The footer: the exact `footerHtml('udebt')` (2231 bytes, sha256
  `dc713ce9950e2cb7cabc6a20f439d96e2c1e39666b483c3e9acb65f6cd4685c8`) in `web/index.html`,
  pinned by `web/test/csp.test.ts`; `FOOTER_STYLE_CSP` in the CSP; the HintWise favicon.

## The steps, in order

### Owner

1. **Say yes** to the steps below (each production step is listed with who runs it).
2. **Merge** `claude/eloquent-lamport-afzr0o` into `main` (Netlify and Render build `main`).
3. **Netlify:** import `pisanuw/understanding-debt` (Add new site, Import an existing project),
   site name **`udebt`** (the repository is not renamed, so type the slug), branch `main`. The
   build settings come from `netlify.toml` (base `web`, `npm ci && npm run build`, publish
   `dist`, functions `netlify/functions`). Then in Site configuration:
   - Deploy Previews **off**, branch deploys **off** (every build costs credits).
   - Environment variables for the functions (run time; `netlify.toml` values reach the build
     only): `SUPABASE_URL=https://auth.hintwise.org` and `SUPABASE_ANON_KEY=<the anon key in
     claude-built-apps platform/supabase.json>` (public by design), scope Functions. The Plabs
     "Netlify env" workflow can set them instead.
4. **Render** (a new service adds a bill): New, Blueprint, this repository, `render.yaml`. It
   creates the free web service `udebt-api` (Python 3.12, `pip install .`, start `python -m
   udebt.hosted.worker_service --host 0.0.0.0 --port $PORT`, health check `/healthz`,
   `autoDeploy: false`). Set `SUPABASE_ANON_KEY` (the same public value) when asked, then
   Manual Deploy. Check `curl https://udebt-api.onrender.com/healthz` (the onrender.com name
   Render shows) prints `{"ok": true, ...}`.
5. **Docs-only commits** carry `[skip netlify]` in the message from now on.

### Plabs session (each after the owner's yes; details in `supabase/README.md`)

1. Copy `supabase/migrations/0001_app_udebt.sql` and `0002_hosted.sql` byte for byte into
   `claude-built-apps/udebt/supabase/migrations/` (with a `udebt/README.md` like photo2pgn's),
   check the sha256s, then the Supabase admin workflow: `migrate udebt`, `expose-schema
   app_udebt`.
2. Add `app_udebt.repositories` and `app_udebt.analyses` to `SHARED_TABLES` in
   `platform/supabase-admin.mjs` (the two entries are in `supabase/README.md`), commit, then
   `check-schema app_udebt`.
3. Add `'udebt'` to `EXTERNAL_APP_LABELS` in `platform/src/signin/validate.ts`, commit, then
   `deploy-function sign-in-link` and `deploy-function delete-account`.
4. "Attach custom domain": Netlify site `udebt` -> `udebt.hintwise.org` (give the slug `udebt`
   explicitly; do not derive it from the repository name).
5. "Set CNAME" / "Render domain": `udebt-api.hintwise.org` -> the Render service `udebt-api`.
6. "Netlify env" on site `udebt`: `SUPABASE_URL` and `SUPABASE_ANON_KEY`, if the owner did not.
7. The samples, once the "UDebt: Nov 12" session delivers the three worker outputs:
   `pip install . && python -m udebt.hosted.samples <hf> <sentry> <mlflow> --out sample-sql`,
   copy the three `.sql` files into claude-built-apps (for example `udebt/supabase/ops/`),
   run the admin workflow's `sql <file>` for each, then `check-schema app_udebt` again.
8. Gateway settings, once the owner names the numbers (below): an `ops/udebt-llm-caps.sql`
   like photo2pgn's (caps; optionally `default_model`), run with `sql`.

### Checks after the deploy

```bash
curl -s https://udebt.hintwise.org/ | grep -c data-site-footer            # 1
curl -sI https://udebt.hintwise.org/ | grep -i content-security-policy    # the policy in netlify.toml
curl -s https://udebt-api.hintwise.org/healthz                            # {"ok": true, "busy": false, "version": 1}
curl -s -o /dev/null -w '%{http_code}\n' https://udebt.hintwise.org/api/excerpt   # 401 without a token
```

Then by hand: sign in with Google and with an email link; open each sample; read a unit's
walkthrough (the code comes from GitHub); take a two-question quiz on the HintWise key; see the
debt fall; switch to an own key and take one more; the next day, review a question missed the
day before from the Review tab (one grading call) and see when it comes back; add a small public
repository with an own key and watch the job; delete a test account.

## Environment variables (placeholders only)

| where | name | value |
|---|---|---|
| Netlify build (`netlify.toml`) | `VITE_SUPABASE_URL` | `https://auth.hintwise.org` |
| | `VITE_SUPABASE_ANON_KEY` | the public anon key (platform/supabase.json; in `netlify.toml`) |
| | `VITE_UDEBT_WORKER_URL` | `https://udebt-api.hintwise.org` (the API base URL) |
| Netlify functions (site env) | `SUPABASE_URL` | `https://auth.hintwise.org` |
| | `SUPABASE_ANON_KEY` | `<public anon key>` |
| Render (`render.yaml`) | `PYTHON_VERSION` | `3.12.7` |
| | `SUPABASE_URL` | `https://auth.hintwise.org` |
| | `SUPABASE_ANON_KEY` | `<public anon key>` (set in the dashboard, `sync: false`) |
| | `UDEBT_ALLOWED_ORIGINS` | `https://udebt.hintwise.org` |
| | `UDEBT_MAX_COMMITS`, `UDEBT_MAX_MB`, `UDEBT_CLONE_TIMEOUT` | `6000`, `200`, `300` (seconds) |
| | `UDEBT_WALKTHROUGHS` | `3` (walkthroughs written per new analysis) |
| | `UDEBT_ALLOW_GATEWAY` | `0`: a new analysis needs the person's own key (owner decision 3) |
| | optional: `UDEBT_BYOK_MAX_TOKENS` (16000), `UDEBT_GATEWAY_MAX_TOKENS` (4096), `UDEBT_MODEL_TURNS` (8), `UDEBT_MIN_TOKEN_SECONDS` (900), `UDEBT_WORK_DIR` | |

`UDEBT_FAKES` and `UDEBT_FAKE_REPOS` are for the local preview only; the functions ignore them
on Netlify.

## Numbers for the owner's caps (estimated, not measured on the gateway)

A quiz sitting is two gateway calls: one writes every question, one grades every typed answer
("I don't know" and a question asked again from a stored variant cost none). On the digitalme
fixture the request bodies were about 26 KB and 15 KB for a two-question sitting (about 6,500
and 3,700 input tokens at four characters a token) and 61 KB and 36 KB for five questions;
code excerpts are capped at 60,000 characters a call; the answers are a few hundred to 1,500
output tokens. At the gateway's default model price ($4 in, $20 out per million) that is about
$0.07 for a two-question sitting and $0.16 for five, so the default $0.25 per person per day is
three short sittings. A review sitting is one call, the grading (its questions are stored
phrasings), about the size of a quiz's grading call. Choosing a cheaper `default_model` for
udebt in `platform.llm_apps`, or call caps per person (photo2pgn's pattern), are the owner's
levers. Measure on the live gateway before settling (`usage` in the admin workflow).

A new analysis runs on the person's own key: a map (one call plus up to seven read turns),
orientations, the tour and three walkthroughs, each a short tool loop; roughly 10 to 30 calls.

## Limits of the Render free instance

512 MB and 0.1 CPU. Measured locally over `file://` (the same pack transfer as a network clone),
without a model: digitalme (190 commits, 2 MB) peaked at 34 MB in 2 s; dsa-instructor (2,510
commits, 81 MB pack) at 135 MB in 47 s. Hence `UDEBT_MAX_MB=200` and `UDEBT_MAX_COMMITS=6000`,
one analysis at a time, the replay and export skipped (the learner app uses neither), git with
small pack buffers. huggingface_hub (2,920 commits) fits these limits; sentry-javascript (17,695)
and mlflow (14,478) do not, which is why the samples are analysed elsewhere. On 0.1 CPU a job
may take several times the local time; the instance sleeps when idle (the first request after a
pause waits for it to start).

## Not tested (no cloud account was used)

- Supabase itself: Auth (Google, sign-in links, `verifyOtp`, session refresh), PostgREST with the
  schema exposed, `check-schema`, the delete-account function on `app_udebt`, the size of an
  analysis row in one PostgREST insert (a large repository's specs and map may be a few MB).
- The HintWise gateway: real cap answers, the `submit` tool convention without `tool_choice`
  (the fake follows it; a model may answer in text, which is parsed as a fallback), the
  4096-token limit on a large map through the worker, Edge Function time limits.
- Netlify: the build, the functions' bundling (esbuild, TypeScript importing `web/src/core`),
  the headers, the run-time environment variables.
- PostgREST as `/api/grade` reads earlier records for the review state (`unit_id=in.(...)`,
  `order=ts.asc,id.asc`), and its row cap (Supabase's default 1000): a person with more records
  than that on one repository would get a cut list there and in the page's own read.
- Render: the build, the start command, memory and time on the free instance, the custom domain,
  a token that expires during a long job (the worker refuses tokens with under 15 minutes left).
- GitHub: raw.githubusercontent.com and api.github.com rate limits for many users.
- The three samples: their outputs had not arrived; the loader was tested on digitalme.

## Earlier phases

Phase 4a's platform-agnostic core (the contract, `udebt worker run`, `udebt push` and `udebt
pull`, the pull-request check) is described in the README's "Hosted: the platform-agnostic
core". Its first draft of a full hosted API is parked, untested, in `drafts/hosted-api/`.
