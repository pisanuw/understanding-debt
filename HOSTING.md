# HOSTING

The brief for the hosted udebt app. It covers how it fits the HintWise platform, what it
offers, the three ready-made sample repositories, who does what, and the decisions the owner
still has to make. Written 2026-11-15 from the owner's request and the HintWise platform docs
in `pisanuw/claude-built-apps` (public): `docs/new-app-guide.md` (the standard
instructions), `docs/platform.md`, `BUILD_ROUTINE.md`, `platform/brand/README.md` and
`platform/templates/app/`. When this brief and one of those disagree, those win.

It replaces parts of ROADMAP Phase 4: sign-in is HintWise's Google and email sign-in through
Supabase, not GitHub OAuth, and model calls go through the HintWise LLM gateway or the user's
own key, not an organisation key stored in Supabase.

## What the owner asked for (2026-11-15)

- A deployed app on Netlify, Render and Supabase where a signed-in user points udebt at any
  repository and works on their understanding of it. They read its specs (units, decisions,
  tour, walkthroughs) and climb the quiz ladder.
- Three sample repositories whose analysis and specs are already cached, so a new user can
  start straight away.
- Google sign-in through Supabase, and the standard HintWise footer.

## Platform rules that shape the design

From `docs/new-app-guide.md`; the section numbers are that guide's.

- **Stack (§2):** a Vite + TypeScript app from `platform/templates/app/`, static on Netlify
  at `https://<slug>.hintwise.org`; Netlify Functions for light server code; Render for
  long-running work; Supabase Postgres in the app's own schema `app_<slug_>` with row
  level security; Supabase Storage (one private bucket); Cloudflare DNS. No other provider
  without the owner's yes.
- **Sign-in (§4):** Supabase Auth at `https://auth.hintwise.org`.
  - Google uses `signInWithOAuth`, with no app-specific Google client or redirect URI.
  - Email uses the platform's `sign-in-link` function, never `signInWithOtp`.
  - Copy `supabase.ts`, `auth.ts`, `account.ts`, `llm.ts`, `repository.ts`,
    `signin-styles.ts` and `google-g-light.svg` from the template, with their tests.
  - "Delete my account" is required.
  - Sign-in returns only to `https://*.hintwise.org/**`; local development and tests use
    fakes.
- **Data (§5):**
  - Every table with a person's rows has `user_id ... default auth.uid() references
    auth.users on delete cascade`, own-row policies, and grants to `authenticated`, none to
    `anon`.
  - Migrations are idempotent and never edited once applied.
  - Never touch `public` or another app's schema.
  - **Apps never get the service-role key;** server code acts with the user's token.
- **Model calls (§7):**
  - The owner's Anthropic key is reachable only through the LLM gateway, for signed-in
    users, behind caps. Never put an Anthropic key in an app or in Netlify.
  - Bring-your-own-key is always offered.
  - Gateway limits: a body of at most 1,000,000 bytes; only `model`, `max_tokens`, `system`,
    `messages` and `tools` pass through; no streaming; `max_tokens` clamped to 4096 by
    default.
  - Default caps: $2 per day per app, and $0.25 per person per day across HintWise.
  - The owner sets numbers after measuring; the Plabs session applies them.
- **Footer, logo, favicon (§6):**
  - udebt is its own repository (option B), so the page embeds the exact output of
    `footerHtml('<slug>')` from `scripts/site-footer.mjs` in `index.html`, just before
    `</body>` and outside the app root.
  - A sha256 test pins it.
  - A strict `style-src` lists `FOOTER_STYLE_CSP`.
  - udebt has no icon, so it uses the HintWise favicon tag from
    `platform/brand/favicon-link.html`.
- **Option B steps (§1, §3, §10):**
  - The repository name is the slug, so rename before the first deploy.
  - The owner imports the repo into Netlify, with Deploy Previews and branch deploys off.
  - The Plabs session attaches `<slug>.hintwise.org`, applies migrations (copied byte for
    byte into `claude-built-apps/<slug>/supabase/migrations/`), adds the label to
    `EXTERNAL_APP_LABELS`, and redeploys `sign-in-link` and `delete-account`. Each
    production step needs the owner's yes.
- **Quality (§9):**
  - Before each push: `npm run lint && npm run typecheck && npm run coverage && npm run
    build`, with coverage at least 85%, and a Playwright walkthrough with fakes.
  - The README says what is stored, how to delete the account, and one honest limitation.

## The mismatch to solve first: udebt's model use

udebt's analysis is agentic. `udebt map`, question writing, grading, orientation and the
tour call the `claude` CLI with read tools over a checked-out repository, often with
prompts of 100,000 to 200,000 characters. The sentry-javascript map prompt was 201,087
characters. The gateway is a plain Messages API proxy:

- **No file tools of its own.** `tools` pass through, so the worker has to run the tool
  loop itself: the model asks to read a file, the worker reads it from its clone and sends
  the result back, one gateway call per turn.
- **4096 output tokens.** A map of 12 units and their sub-units may need more. The Plabs
  session can raise an app's `max_tokens`.
- **Caps.** One map on the owner's key will likely exceed the $0.25 per person per day
  default, and possibly the app's $2 per day. Nothing has been measured on the gateway yet,
  and the platform asks for measurement before choosing numbers.

Proposal:
- **Sample repositories cost users nothing to analyse.** Their analysis and specs are made
  locally with the `claude` CLI (subscription, no API cost) and loaded into Supabase.
- **Quizzing on any repository** uses small calls (writing a quiz, grading answers) through
  the gateway under per-person call caps, plus bring-your-own-key.
- **Analysing a new repository** needs the person's own key at first, or credits later.
  Opening it to the owner's key waits for a measured cost per analysis and caps the owner
  names.

## Proposed architecture

| part | where | what |
|---|---|---|
| Web app | Netlify, `https://<slug>.hintwise.org` | sign in; repository list (the three samples plus the person's own); specs view (units and sub-units, each unit's decision, alternatives and why, the decisions that shaped it, the tour, orientations); the quiz ladder; dig and walkthroughs; the person's debt per unit. The local `udebt serve` teaching layer (Phase 3.3) is the starting point. |
| Analysis worker | Render, its own Python service (not `backend-hub`, which is a shared Node instance with 512 MB) | clone a public repository within size limits; run the offline core (structure, history mining, weights, replay); run the model steps through the gateway with the person's token, or with their own key; write results with the person's token. |
| Data | Supabase, schema `app_<slug_>` | repositories and pinned shas; analyses (the map, specs, orientations, tour) per repository and sha; each person's repositories; quiz records per person (own-row policies); quiz sessions. |
| Files | Supabase Storage, private bucket `<slug>` | large analysis artefacts if a row is too big. |

Smaller decisions this proposal makes, open to change:

- **The person is a learner, not an author.** v1 does not map commit emails to accounts, so
  there is no own-commit prior. Debt starts at 100 and quizzes earn it down. Claiming a
  commit identity, which needs verified emails, comes later.
- **Public GitHub repositories only in v1,** within size limits (commits, clone size) that
  have to be chosen. Private repositories need GitHub access and the metadata-only storage
  ROADMAP Phase 4 describes.
- **Grading against the code** needs the code. For public repositories the analysis stores
  the excerpts each unit's questions rely on, so quizzes need no clone. Private repositories
  would need the worker.
- **Shared analyses without the service-role key.** A sample's analysis is readable by every
  signed-in person but written by no app code. The Plabs session loads it with the Supabase
  admin workflow after the owner's yes. A person's own analysis is written with their token
  and readable by them. Whether a person's analysis of a public repository should be shared
  is an owner decision.

## The three sample repositories

All three are already mapped at their `testbeds.json` shas, with the phase 1.3 history fix,
and are permissively licensed. Attribution and the licence name go on each sample's page.

| repository | licence | language | commits | why |
|---|---|---|--:|---|
| [huggingface/huggingface_hub](https://github.com/huggingface/huggingface_hub) | Apache-2.0 | Python | 2,920 | small (287 source files), a clear architecture of 12 units, a fast first experience |
| [getsentry/sentry-javascript](https://github.com/getsentry/sentry-javascript) | MIT | TypeScript | 17,695 | a large monorepo; AI share rose from 1% to 61% in a year, which shows the reason udebt exists |
| [mlflow/mlflow](https://github.com/mlflow/mlflow) | Apache-2.0 | Python + TS | 14,478 | a platform with a server, stores, SDKs and a web UI; 61% AI, both agent-authored and assisted |

The specs for each come from the current code: `map`, orientations, `tour`, and
walkthroughs for the main units. Each sample gets a short level-1 quiz path, so the first
session takes minutes. All of this is made with the `claude` CLI on the owner's
subscription. Each sample's analysis is a few calls (a map takes 1 call and 1–4 minutes);
the walkthroughs add one per unit.

## Who does what

| step | who |
|---|---|
| Decisions below | owner |
| The app, the worker, the migrations, tests | the UDebt session (the code owner), after the owner says go |
| Sample analyses and specs, made locally with the `claude` CLI and checked | the "UDebt: Nov 12" session (testbeds), with the owner's yes for the calls |
| Rename the repo to the slug (before the first deploy) | owner |
| Import the repo into Netlify with previews off; create the Render service | owner |
| Attach `<slug>.hintwise.org`; Render domain; apply migrations; `EXTERNAL_APP_LABELS` and redeploy `sign-in-link` and `delete-account`; load the sample analyses; set gateway caps and `max_tokens` | the Plabs session, each with the owner's yes |

## Decisions for the owner

Numbered for answers; each has a recommendation.

1. **Who builds the app.** Recommended: the UDebt session builds it; this session prepares
   the samples and checks the result.
2. **Slug and domain.** The repo name must equal the slug. Recommended: rename
   `pisanuw/understanding-debt` to `udebt` before the first deploy, giving
   `udebt.hintwise.org`. Keeping the name gives `understanding-debt.hintwise.org`.
3. **Model use.** Recommended:
   - samples are precomputed on the subscription;
   - quizzes go through the gateway with a per-person call cap, plus bring-your-own-key;
   - analysing a new repository is bring-your-own-key only until a measured cost per
     analysis lets the owner set caps.

   This replaces ROADMAP decision 1 (an organisation key stored in Supabase), which the
   platform's "never put an Anthropic key in an app" rule rules out.
4. **The Render worker.** It needs its own Python service with disk, which is a cost.
   Recommended: one small service that sleeps when idle, with repositories limited to about
   20,000 commits and a 500 MB clone in v1.
5. **Repository scope.** Recommended: public GitHub repositories only in v1.
6. **Sharing analyses.** Should a person's analysis of a public repository be visible to
   others who add the same repository? Recommended: yes for public repositories, which saves
   the cost of repeat analyses.
7. **The samples.** Recommended: the three above. Swapping one for a smaller TypeScript
   repository would give a gentler second start.
