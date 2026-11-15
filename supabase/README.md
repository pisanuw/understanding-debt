# udebt on Supabase: what the Plabs session applies

udebt (https://udebt.hintwise.org) is an option B HintWise app: it lives in
`pisanuw/understanding-debt` (not renamed: owner decision of 2026-11-15, the slug is `udebt`
anyway). Its data sits in the shared HintWise project, schema `app_udebt`. Every step below is a
production change: each needs the owner's yes. The full order, with the owner's own steps, is in
`docs/DEPLOY.md`.

## 1. Migrations: copy byte for byte into claude-built-apps

Copy these files, unchanged, into `claude-built-apps/udebt/supabase/migrations/`, check their
sha256 there, then run the Supabase admin workflow: `migrate udebt`, `expose-schema app_udebt`,
`check-schema app_udebt`. Never edit an applied file; later changes come as new numbered files.

| file | sha256 | what |
|---|---|---|
| `0001_app_udebt.sql` | `5e8c4e3796c1ae637d1c2c8cbb0bd96d0d96e557dd0e84f9eb847f2eb346b00e` | Phase 4a: schema `app_udebt`; `repositories` and `analyses` (shared), `my_repositories`, `records`, `quiz_sessions` (own rows: `user_id ... default auth.uid() references auth.users on delete cascade`, policies on `auth.uid()`); grants to `authenticated` only, nothing for `anon` |
| `0002_hosted.sql` | `162ad5f92e7575e310b147361fee2689c2b511772ec8b80aeb40af0b0b213135` | Phase 4b: analyses readable by everyone signed in (public repositories, shared by the owner's decision), still written and changed by their owner only, kept unowned (`on delete set null`) when the account goes; repositories limited to `https://github.com/<owner>/<name>`; `jobs` (own rows: the worker's progress); `insert into platform.llm_apps (slug) values ('udebt') on conflict do nothing` |

Both are idempotent (a re-run changes nothing) and were applied twice in a row to Postgres 16
with a stub of Supabase's `auth` schema and a `platform.llm_apps` table
(`tests/test_schema.py`, `tests/test_samples.py` with `UDEBT_TEST_PG`). Not tried on Supabase
itself.

## 2. SHARED_TABLES entries (a Plabs commit)

`check-schema` fails any table a signed-in stranger can read unless it is listed in
`SHARED_TABLES` in `claude-built-apps/platform/supabase-admin.mjs`. Add these two entries:

```js
  'app_udebt.repositories': 'public GitHub repositories udebt has analysed: URL, name, licence, whether a sample, who added it (uuid); no other personal data (policy repositories_read, udebt 0001)',
  'app_udebt.analyses': 'analyses of public GitHub repositories at a pinned commit: units, decisions, file and line references, the tour; no code and no commit emails; shared with every signed-in person by the owner\'s decision of 15 Nov 2026 (policy analyses_read, udebt 0002)',
```

The other tables (`my_repositories`, `records`, `quiz_sessions`, `jobs`) are own-row only and
need no entry.

## 3. EXTERNAL_APP_LABELS (a Plabs commit, then two redeploys)

In `claude-built-apps/platform/src/signin/validate.ts`, add the label `udebt`:

```ts
export const EXTERNAL_APP_LABELS = ['stepwise', 'photo2pgn', 'udebt'];
```

then `deploy-function sign-in-link` and `deploy-function delete-account`. Until both are
redeployed, email sign-in links and "Delete my account" are refused for udebt (Google sign-in
works without it).

## 4. The sample analyses (after the migrations)

The "UDebt: Nov 12" session delivers three worker outputs (huggingface_hub, sentry-javascript,
mlflow, each from `udebt worker run <url> --rev <pin> --llm --walkthroughs 12 --no-excerpts`).
In a checkout of this repository, with Python 3.11+:

```bash
pip install .
python -m udebt.hosted.samples <hf dir> <sentry dir> <mlflow dir> --out sample-sql
```

It checks each output (the pinned sha of `testbeds.json`, a map, no code lines) and writes
`sample-sql/<name>.sql` with its size and sha256: `udebt worker sql --public` with the
repository's GitHub URL and licence (huggingface_hub Apache-2.0, sentry-javascript MIT, mlflow
Apache-2.0), no export, no commit emails, idempotent. Copy the three files into
claude-built-apps (for example `udebt/supabase/ops/`) and run the admin workflow's
`sql <file>` for each. The workflow's logs are public; so is everything in these files.
Then `check-schema app_udebt` again: the stranger check now sees the samples, by design (the
`SHARED_TABLES` entries).

The same command was tested on a digitalme output made without a model call
(`tests/fixtures/hosted/digitalme`, `tests/test_samples.py`): the script loads twice into
SQLite and Postgres 16 and every signed-in person reads the sample.

## 5. Gateway settings (the owner names the numbers)

`0002_hosted.sql` registers the app (`platform.llm_apps`, default caps); `ops/udebt-llm-caps.sql` then sets the cheaper model and the caps the owner chose. A quiz sitting makes
two gateway calls (writing the questions, grading the answers), each at most 4096 output tokens;
the owner picks the per-person and per-app caps after measuring them (docs/DEPLOY.md, "Not
tested"), and the Plabs session sets them with an `ops/udebt-llm-caps.sql` like photo2pgn's.
Analysing a new repository uses the person's own key, so the gateway needs no larger
`max_tokens` for now.

## Files here

- `migrations/`: the two files above, in the platform's `NNNN_<name>.sql` naming (the 4a file
  was `20261115000000_app_udebt.sql`, renamed before anything was applied, content unchanged).
- `sqlite/app_udebt.sql`: the same tables for SQLite, for tests and local runs (no row level
  security; the Postgres tests check that).
