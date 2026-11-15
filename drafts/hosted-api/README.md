# Parked: a first hosted-API draft (superseded)

Written on 2026-11-15 for the original ROADMAP Phase 4 target (a Starlette API on Render,
GitHub OAuth, an organisation API key, orgs and roles in Supabase) and parked the same day,
when the owner moved the hosted app to the HintWise platform (`HOSTING.md` on branch
`claude/jolly-hamilton-pvos83`: Supabase Auth with Google and email sign-in, Netlify Functions,
own-row RLS in schema `app_udebt`, model calls through the HintWise gateway).

**Status: never run end to end and without tests.** Only the migrations were applied, once, to
a local Postgres 16. It is kept, not deleted, because parts may help Phase 4b:

- `permissions.py`: the owner / lead / member / viewer model as code, including how a member's
  view keeps their own rows and gets team means (`scope_export`);
- `migrations/postgres/0002_rls.sql`: that model as row-level security, with a
  security-definer function for team aggregates that names nobody;
- `importer.py`: an idempotent import of the export contract (natural keys per row,
  a fingerprint change replaces the series);
- `reads.py`: the export rebuilt from tables, in the shapes `udebt serve` returns.

It is outside the `udebt` package on purpose: nothing imports it, it is not installed, and its
relative imports (`from .. import contract`) only work from its old place, `udebt/hosted/`.
What Phase 4a kept, platform-agnostic, is in the package: `udebt/contract.py`,
`udebt/worker.py`, `udebt/checks.py`, `udebt/webhooks.py`, `udebt/remote.py` and
`supabase/migrations/` (see `docs/DEPLOY.md`).
