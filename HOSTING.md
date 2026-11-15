# HOSTING

The brief for the hosted udebt app. It covers how it fits the HintWise platform, what it
offers, the three ready-made sample repositories, who does what, and the owner's decisions
(made 2026-11-15, at the end). Written 2026-11-15 from the owner's request and the HintWise platform docs
in `pisanuw/claude-built-apps` (public): `docs/new-app-guide.md` (the standard
instructions), `docs/platform.md`, `BUILD_ROUTINE.md`, `platform/brand/README.md` and
`platform/templates/app/`. When this brief and one of those disagree, those win.

It replaces parts of ROADMAP Phase 4: sign-in is HintWise's Google and email sign-in through
Supabase, not GitHub OAuth, and model calls go through the HintWise LLM gateway or the user's
own key, not an organisation key stored in Supabase.

**Checked with the Plabs session, 2026-11-15** (claude-built-apps aeed6d5, information only):
the guide is current. Its answers are folded in below, marked "(Plabs)".

**Status 2026-11-15:** built by the UDebt session (ROADMAP Phase 4b) and tested locally with fakes;
not deployed. The deployment steps, in order, are in `docs/DEPLOY.md`. Use without signing in
(decision 10) is built too, as described in "Use without signing in, as built" below; its advance
quizzes are not written yet.

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
  - (Plabs) Each request is one model call, with at most 200 messages and 64 tools.
    `max_tokens` is the per-app column `platform.llm_apps.max_tokens`, default 4096, with
    no platform ceiling beyond the model's own limit. photo2pgn runs at 12000, the owner's
    number. Edge Function wall-clock time for one long call is not measured.
  - (Plabs) A server, such as the Render worker, may call the gateway with the signed-in
    person's session token. The gateway checks the token and needs no Origin header. The
    token expires (about an hour by default, not checked for this project), and every call
    counts against that person's caps.
- **Footer, logo, favicon (§6):**
  - udebt is its own repository (option B), so the page embeds the exact output of
    `footerHtml('<slug>')` from `scripts/site-footer.mjs` in `index.html`, just before
    `</body>` and outside the app root.
  - A sha256 test pins it. (Plabs) For the slug `udebt` the markup is 2231 bytes, sha256
    `dc713ce9950e2cb7cabc6a20f439d96e2c1e39666b483c3e9acb65f6cd4685c8`; another slug
    changes the hash. It must be in the HTML the server sends, never inserted by React or
    `innerHTML`.
  - A strict `style-src` lists `FOOTER_STYLE_CSP`, today
    `'sha256-4VDViIffTKl8oDDJg58VHCVBOHSQm7GPM7eZ1fAKuzc='`.
  - udebt has no icon, so it uses the HintWise favicon tag from
    `platform/brand/favicon-link.html`.
- **Option B steps (§1, §3, §10):**
  - The repository name is the slug, so rename before the first deploy.
  - The owner imports the repo into Netlify, with Deploy Previews and branch deploys off.
  - The Plabs session attaches `<slug>.hintwise.org`, applies migrations (copied byte for
    byte into `claude-built-apps/<slug>/supabase/migrations/`), adds the label to
    `EXTERNAL_APP_LABELS`, and redeploys `sign-in-link` and `delete-account`. Each
    production step needs the owner's yes. (Plabs) `EXTERNAL_APP_LABELS` in
    `platform/src/signin/validate.ts` is `['stepwise', 'photo2pgn']` today. Until `udebt`
    is added and both functions are redeployed, email links and account deletion are
    refused for the app.
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
- **The loop resends everything.** (Plabs) Each turn is a new gateway call that resends the
  whole conversation, so an open-ended tool loop costs more every turn and runs into the
  1 MB body limit. The worker should do the structural work offline, as udebt already does
  (clusters, history, weights), and give the model a curated set of files in one or a few
  calls rather than letting it browse.
- **4096 output tokens.** A map of 12 units and their sub-units may need more. The Plabs
  session can raise an app's `max_tokens`; the owner picks the number.
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
| Analysis worker | Render, its own Python service, at `udebt-api.hintwise.org` | clone a public repository within size limits; run the offline core (structure, history mining, weights, replay); run the model steps through the gateway with the person's token, or with their own key; write results with the person's token; delete the clone when the job ends. (Plabs) `backend-hub` is the documented first choice, but its free 512 MB Node instance very likely cannot hold Python plus clones of hundreds of MB (not measured). A new Render service needs the owner's yes because it adds a bill. The "Set CNAME / Render domain" workflow can point `udebt-api.hintwise.org` at it, and the gateway's CORS accepts any one-label `https://<label>.hintwise.org`. |
| Data | Supabase, schema `app_<slug_>` | repositories and pinned shas; analyses (the map, specs, orientations, tour) per repository and sha; each person's repositories; quiz records per person (own-row policies); quiz sessions. |
| Files | Supabase Storage, private bucket `<slug>` | large analysis artefacts if a row is too big. |

Smaller decisions this proposal makes, open to change:

- **The person is a learner, not an author.** v1 does not map commit emails to accounts, so
  there is no own-commit prior. Debt starts at 100 and quizzes earn it down. Claiming a
  commit identity, which needs verified emails, comes later.
- **Public GitHub repositories only in v1,** within size limits (commits, clone size) that
  have to be chosen. Private repositories need GitHub access and the metadata-only storage
  ROADMAP Phase 4 describes.
- **Grading against the code without storing code.** (Plabs) Cloned code is not kept beyond
  the job. The analysis stores references instead: path, line range and the pinned sha for
  what each unit's questions rely on. For a public repository, the excerpts are fetched from
  GitHub at that sha when a quiz is written or graded, so no clone and no stored code are
  needed. Private repositories would need the worker.
- **Shared analyses without the service-role key.** A sample's analysis is readable by every
  signed-in person but written by no app code. (Plabs) `check-schema` fails any table a
  signed-in stranger can read unless it is listed in `SHARED_TABLES` in
  `platform/supabase-admin.mjs` (today only `platform.llm_apps`). An entry such as
  `app_udebt.samples` is a Plabs commit. The table needs row level security, a
  `select to authenticated using (true)` policy, no insert, update or delete grants, and
  nothing for `anon`. The rows are loaded with the Supabase admin workflow after the
  owner's yes: `migrate udebt`, with the migrations copied into
  `claude-built-apps/udebt/supabase/migrations/`, or `sql <file>` for a one-off. The `sql`
  logs are public, so private data stays out. A person's own analysis is written with their
  token and readable by them. Whether a person's analysis of a public repository should be
  shared is an owner decision, and needs the same `SHARED_TABLES` step.
- **Safety in the worker.** (Plabs) Accept public `https://github.com/` URLs only, cap clone
  size and depth, guard against SSRF and against symlinks or paths escaping the clone, and
  never put an Anthropic key on Render. The README says what is stored (Supabase, US East).

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
| Attach `<slug>.hintwise.org`; Render domain (`udebt-api.hintwise.org`); apply migrations; add `udebt` to `EXTERNAL_APP_LABELS` and redeploy `sign-in-link` and `delete-account`; add the shared tables to `SHARED_TABLES`; load the sample analyses; set gateway caps and `max_tokens` | the Plabs session, each with the owner's yes |

## Owner decisions (2026-11-15)

1. **Who builds:** the UDebt session builds the app, the worker and the migrations. The
   "UDebt: Nov 12" session prepares the three samples now and checks the result.
2. **Slug and domain:** `udebt.hintwise.org`, and the worker at `udebt-api.hintwise.org`.
   **The repository is not renamed;** it stays `pisanuw/understanding-debt`. This is an
   exception to the guide's "the repository name is the slug" for option B apps. The Netlify
   import and the Plabs session's "Attach custom domain" run should use the slug `udebt`
   explicitly, not derive it from the repository name.
3. **Model use:**
   - samples are analysed in advance on the owner's subscription;
   - quizzes go through the HintWise LLM gateway;
   - analysing a new repository needs the person's own key.

   No organisation key is stored anywhere. This replaces ROADMAP decision 1.
4. **Worker:** a Render free instance for now. Plabs expects 512 MB to be tight for Python
   plus large clones, so the worker's limits (`--max-commits`, `--max-mb`) have to fit it.
   The gateway's `max_tokens` and the per-person caps are decided after measuring the
   samples' calls (see "Measured on the samples").
5. **Scope:** public GitHub repositories only.
6. **Sharing:** yes. An analysis of a public repository is shared with everyone who adds
   it. Shared tables need `SHARED_TABLES` entries (a Plabs commit).
7. **Samples:** huggingface_hub, sentry-javascript and mlflow. Their specs are generated now
   on the owner's subscription, with `udebt worker run ... --llm --walkthroughs 12
   --no-excerpts`: references, no stored code.
8. **After the measurements (2026-11-15):**
   - yes to the `udebt/llm.py` isolation fix (f294414);
   - yes to the cheaper `default_model` with the default caps;
   - yes to the Plabs session loading `samples/*.sql` into production. The `app_udebt`
     migrations must be applied first.
9. **A fourth sample (owner, 2026-11-15):** "I want https://github.com/pisanuw/understanding-debt
   to be one of the sample repositories". These choices were made under the 15-minute rule, and
   the owner can reverse any of them:
   - it is added, not swapped for another sample;
   - its committed `.udebt/map.json` is refreshed first. The map dated 12 Nov covers 15% of
     today's source and none of `web/`, `udebt/hosted/` or `supabase/`;
   - no licence for now.
10. **No login (owner, 2026-11-15):** "The web site should be usable without any login, any
    progress on quizzes would just be saved on browser". Under the platform rules, anonymous
    visitors get no grants in app schemas, and the gateway serves signed-in users only. These
    choices were made under the 15-minute rule, and the owner can reverse any of them:
    - anonymous visitors use the samples, served as static JSON with the site;
    - their quizzes on the samples are written in advance on the owner's subscription and
      checked by the learner against the reference answer, with no model call. Visitors who
      give their own key also get quizzes the model writes and grades;
    - their progress stays in the browser;
    - signed-in users keep server records, and the app offers to copy browser progress into
      the account at sign-in;
    - adding a repository still needs sign-in.

## Use without signing in, as built (2026-11-15)

Decision 10 as the UDebt session built it; the choices below were made under the 15-minute rule,
and the owner can reverse any of them (BRIEFING, "Phase 4b, use without signing in").

- **The samples as static files.** `web/public/samples/index.json` lists the registered samples
  (`udebt/hosted/samples.py`) with their pins; `<name>.json` holds each sample's rows, exactly what
  `udebt worker sql --dialect json --public` writes, derived from the committed `samples/<name>.sql`
  by `python -m udebt.hosted.static_samples` (no worker output needed). A registered sample with no
  script, or no quiz file, is listed with `null`.
- **Code without a token, for the samples only.** `/api/excerpt` and `/api/tree` answer a request
  without a token when it names a registered sample at its pinned commit (the index, bundled with
  the functions), and refuse anything else with 401; with a token they check it as before. This
  was chosen over reading raw.githubusercontent.com from the browser: no CSP change, one code
  path with the same limits, the preview's fakes cover it, and nothing new is open beyond what the
  site already serves. The functions never touch Supabase for such a request.
- **Advance quizzes.** `udebt worker quizzes` writes, per sample, three questions per unit and
  level 1-3 with the hosted quiz's own prompt, each with its reference answer and, at level 1, the
  orientation (`<name>.quizzes.json`, format version 1, contract kind `quizzes`). The coordinating
  session runs it on the owner's subscription (51 calls for the four samples at the defaults;
  docs/DEPLOY.md). Levels 4-5 are never written in advance.
- **Self-checked answers.** A visitor answers, reads the reference and marks the answer (correct,
  partly right, wrong, didn't know; confidence fixed before the reference shows). The record's
  `type` is `self-checked`, which the scoring counts like any other answer and the page labels
  wherever the debt rests on it. The HintWise key needs an account.
- **Advance quizzes for everybody (owner, 2026-11-15: "everybody gets advance quizzes", the
  model-written and model-graded quiz one choice away).** The questions written in advance are
  offered to everyone on a sample, signed in or not, with or without a model in use. Whoever has a
  model in use (a visitor with their own key, a signed-in person with the HintWise key or their
  own) chooses on the Quiz tab between "Written in advance, self-checked (no model call)" and
  "Written and graded by the model (one call each way)"; the advance quiz is the default (one
  constant, `DEFAULT_QUIZ_KIND`, for the owner to flip) and the choice is kept per browser, never
  on the server. A signed-in person's self-checked answers go to their server rows, labelled as
  for visitors. Both kinds are one history (ladder, debt, review); Review checks an item the way
  its latest miss was checked: self-checked if the person marked it, by the model in use otherwise
  (BRIEFING, "Phase 4b, advance quizzes for everybody").
- **Progress in the browser.** One localStorage entry per sample holds the visitor's records in
  the same shape as the server's, read through the same store interface, so debt, review and the
  ladder behave the same; with storage blocked the visit works in memory.
- **The copy offer.** After sign-in, answers the browser keeps that were never offered are offered
  once: copy into the account (the person's own token, every field kept), keep separate, or
  discard. Copying, keeping or discarding marks them decided, so they are not offered again.
- **What an anonymous session can write:** nothing on the server. Only the copy (after sign-in,
  with the person's token) and account deletion write there; the gateway client refuses to send
  without a token, and the tests watch every request the page makes while not signed in.

## Measured on the samples (2026-11-15)

The samples were made with the `claude` CLI. Its default model is the cheaper of the gateway's
two. The load SQL and how it was made are in `samples/` (README there); the findings from making
it are in TESTBEDS.md, "Sample prep".

| | huggingface_hub | sentry-javascript | mlflow |
|---|--:|--:|--:|
| commits | 2,920 | 17,695 | 14,477 |
| units (sub-units) | 12 (6) | 12 (3) | 12 (18) |
| map coverage of non-test source | 85% | 27% (77% of `packages/`) | 77% |
| CLI calls / model turns | 15 / 129 | 15 / 151 | 15 / 172 |
| largest output (tokens) | 5,602 | 4,565 | 6,943 |
| calls with more than 4,096 output tokens | 4 | 2 | 4 |
| wall time | 808 s (with export) | 528 s | 707 s |
| default model, cached / uncached | $4.25 / $15.81 | $2.74 / $5.60 | $2.77 / $6.47 |
| cheaper model, cached / uncached | $2.44 / $7.91 | $1.45 / $2.80 | $1.49 / $3.24 |

**How these are priced:**
- From the token counts the CLI reported, at the gateway's prices per million tokens: the default
  model at $4 in, $20 out, $0.20 cache read and $5 cache write; the cheaper model at $2, $10,
  $0.20 and $2.50.
- "Uncached" bills every input token at the full price. The hosted tool loop does that today,
  because it sends no `cache_control`.
- The huggingface_hub run offered every default tool (before the wrapper fix), and its larger
  system prompt makes it the most expensive.

**What the numbers mean for the limits:**
1. **Gateway `max_tokens`.** Under decision 3 the gateway carries only quiz writing and grading.
   Those answers run from a few hundred to 1,500 tokens (the UDebt session's estimate), so the
   default of 4096 holds. Analysis calls reach 6,943 tokens, but they run on the person's own key,
   where the worker allows 16,000 (`UDEBT_BYOK_MAX_TOKENS`). If analyses ever go through the
   gateway, its `max_tokens` must be at least 8,192.
2. **Quiz input on the samples.** A question call carries up to four references per unit.
   - Median code per unit: 6,450 characters (huggingface_hub), 4,747 (sentry-javascript) and
     4,334 (mlflow).
   - A two-unit call is therefore 9,000 to 13,000 characters (about 2,200 to 3,200 tokens), and a
     five-unit call 22,000 to 32,000, all under the 60,000 cap.
   - That is the scale of the digitalme fixture behind the UDebt session's estimate ($0.07 for a
     two-question sitting, $0.16 for five, at the default model's price), so the estimate carries
     over to the samples.
3. **Per-person caps.** $0.25 a day is about three two-question sittings on the default model and
   about seven on the cheaper one.
   - **Decided by the owner (2026-11-15):** udebt's `default_model` is the cheaper model, with
     $0.25 per person and $2 for the app per day and `max_tokens` at 4096, until the gateway's
     `usage` shows real sittings. The Plabs session runs `supabase/ops/udebt-llm-caps.sql`; the
     web app's `DEFAULT_MODEL` names the same model.
   - The cheaper model made these samples, as every logged call shows. The synthetic experiments
     in TESTBEDS.md also ran on the CLI's default with no model set, though their reports do not
     record the model.
4. **A new analysis on the person's own key**, as the hosted worker would run it (a map,
   orientations, the tour and three walkthroughs):
   - cheaper model: $0.63 to $1.04 cached, $1.10 to $2.91 uncached;
   - default model: $1.21 to $1.87 cached, $2.20 to $5.82 uncached.

   Adding `cache_control` to the tool loop would bring the uncached figures down to about the
   cached ones.
5. **Units with no code for the question writer:** 1 of 18 (huggingface_hub), 1 of 15
   (sentry-javascript) and 12 of 30 (mlflow). These sub-units list only directories and have no
   walkthrough or orientation, so `refsForUnit` finds no file, and a quiz on them gets no code.
   - **Fixed (owner's yes, 2026-11-15)** without regenerating the samples: such a unit is quizzed
     from the top tracked files under its directories. The worker now records them (`files`);
     for the loaded samples the page lists the directories from GitHub's tree at the pin
     (`/api/tree`). On a clone of mlflow at the pin, each of the 12 units gets four source files.
