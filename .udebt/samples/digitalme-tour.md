# Architecture tour: digitalme

_Written by `udebt tour` at `f93102b1` on 2026-11-14; 12 units, about 11 minutes to read. Units come in the order to learn them: each after the units it uses._

Digital Me (YPai) is a public chatbot that answers as its owner, Yusuf Pisan, using a persona kept as markdown files. The Chat request lifecycle streams each reply from a FastAPI backend to the React/Vite frontend. Along the way it draws on the Persona prompt and retrieval modes, the Safety checks, the FAQ cache and voice replies, and the Cost controls. SQLite persistence stores conversations, roles, bans and usage, and Auth and role-based access decides who may use the Admin dashboard and Persona Fixes loop. Email correspondence and the Contacts database extend the bot beyond chat. The Content pipeline and CI/deploy keep posts and deployments flowing through git and pull requests.

## The map

*Uses*: its code imports the other unit's (strongest first). *Used by*: how many units import it. *Changes with*: the units commits most often touch with it.

1. **Persona prompt and retrieval modes** (`persona-retrieval`): uses `cost-control`; used by 7; changes with `content-pipeline`, `admin-persona-fix-loop`
2. **Safety: hostile pre-check, guardrails, bans** (`safety-guardrails`): uses `cost-control`, `persona-retrieval`; used by 5; changes with `persona-retrieval`, `chat-flow`
3. **Content pipeline and CI/deploy** (`content-pipeline`): used by 1; changes with `persona-retrieval`, `frontend-spa`
4. **FAQ cache and voice replies** (`faq-cache-tts`): uses `persona-retrieval`, `safety-guardrails`, `content-pipeline`, `cost-control`; used by 4; changes with `frontend-spa`, `chat-flow`
5. **Auth and role-based access** (`auth-rbac`): uses `persona-retrieval`, `safety-guardrails`, `faq-cache-tts`; used by 3; changes with `chat-flow`, `admin-persona-fix-loop`
6. **SQLite persistence (analytics.db) and backup** (`analytics-persistence`): uses `cost-control`; used by 2; changes with `cost-control`, `admin-persona-fix-loop`
7. **Cost controls, rate limits and usage metering** (`cost-control`): uses `persona-retrieval`, `safety-guardrails`, `analytics-persistence`, `auth-rbac`, `faq-cache-tts`; used by 7; changes with `chat-flow`, `admin-persona-fix-loop`
8. **Chat request lifecycle (SSE)** (`chat-flow`): uses `cost-control`, `persona-retrieval`, `safety-guardrails`, `faq-cache-tts`; used by 1; changes with `auth-rbac`, `cost-control`
9. **Admin dashboard and Persona Fixes loop** (`admin-persona-fix-loop`): uses `persona-retrieval`, `cost-control`, `faq-cache-tts`, `safety-guardrails`, `analytics-persistence`; changes with `chat-flow`, `cost-control`
10. **React/Vite frontend** (`frontend-spa`): uses `chat-flow`, `auth-rbac`; changes with `chat-flow`, `admin-persona-fix-loop`
11. **Email correspondence and notifications** (`email-notifications`): uses `persona-retrieval`, `auth-rbac`, `cost-control`; changes with `cost-control`, `chat-flow`
12. **Contacts database with privacy filter** (`contacts-db`): changes with `persona-retrieval`, `cost-control`

## 1. Persona prompt and retrieval modes (`persona-retrieval`)

This unit decides what the bot knows and how it sounds. It builds the system prompt from persona markdown files and tone settings, then picks how much persona context each request gets. It uses Cost controls, rate limits and usage metering, and is used by the FAQ cache, the Admin dashboard, Chat request lifecycle, Email correspondence, Auth, and Safety. The decision: persona knowledge stays as plain markdown in the repo, with six retrieval modes (full, distilled, TF-IDF, Voyage embeddings, Haiku router, cascading) chosen at runtime. Cascading is the default, and a database setting beats an environment variable, which beats the code default. Fine-tuning, one fixed full prompt and a vector database service were rejected, since the full persona is about 40K tokens per call. Yusuf Pisan added the persona files alone [human] in May 2026, then built the modes and the June refactor with an AI co-author [assisted].

Open first: `persona`, `backend/persona.py`, `backend/cascading.py`.

## 2. Safety: hostile pre-check, guardrails, bans (`safety-guardrails`)

This unit protects the bot and its owner from abuse. A Haiku pre-check classifies each incoming message as hostile, manipulative or abusive, and a Haiku post-check reviews each reply and fails closed. Strikes and bans persist in SQLite, so they survive redeploys. It uses Cost controls and the Persona prompt and retrieval modes. It is used by the Chat request lifecycle, the FAQ cache, the Admin dashboard, Auth, and Cost controls. The decision: three strikes or a 24-hour ban, tied to a stable visitor identity, with the sentence limit enforced on the server. Prompt-only rules were evaded, and IP bans break when addresses change. Fingerprinting was explicitly rejected for privacy. Yusuf Pisan introduced it with an AI co-author [assisted] in May 2026, and later session work was by him alone [human].

Open first: `backend/hostile.py`, `backend/guardrails.py`, `backend/visitor.py`.

## 3. Content pipeline and CI/deploy (`content-pipeline`)

This unit turns news headlines into blog posts and gets code into production. Headlines are scored and drafted, a post pull request is opened, and merging ingests the post. Continuous integration then gates deployment to Render, and the frontend ships to Netlify. It is used by the FAQ cache and voice replies, and it changes together with the Persona prompt and retrieval modes, the React/Vite frontend, and the Chat request lifecycle. The decision: git is the approval and ingestion mechanism, using a GitHub Action or a local command-line draft, and checks cover linting, typing, tests and the build. A CMS, direct publishing and manual deploys were not chosen, because pull requests keep a human in control. The daily cron was disabled in June 2026 to stop unmetered API spend. Yusuf Pisan built the pipeline alone [human], then added typing, CI gates and the cron change with an AI co-author [assisted]; he added auto-deploy alone [human] in July.

Open first: `automation`, `posts`, `.github/workflows`.

## 4. FAQ cache and voice replies (`faq-cache-tts`)

This unit answers common questions without a full model call. Pre-generated answers are matched by TF-IDF, and cached answers can carry ElevenLabs audio. It uses the Persona prompt and retrieval modes, Safety, the Content pipeline and CI/deploy, and Cost controls. It is used by the Chat request lifecycle, the Admin dashboard, Auth, and Cost controls. The decision: entries are served stale while they refresh in the background, keyed by a hash of the persona and generated from the distilled persona. Audio files sit on disk, named by a hash of the answer text, so old clips become orphans on their own. Regenerating the whole bank on every deploy was rejected because one redeploy cost about $5. A manifest or database column for audio was also not chosen. Yusuf Pisan began it with an AI co-author [assisted] in May 2026, refined it alone [human] through June, and added voice replies with an AI co-author [assisted] in July.

Open first: `backend/faq_cache.py`, `backend/tts.py`, `backend/data/faq_cache.json`.

## 5. Auth and role-based access (`auth-rbac`)

This unit decides who the visitor is and what they may do. Optional Google sign-in is exchanged for a token, and stackable roles map to capabilities that gate the admin endpoints. It uses the Persona prompt and retrieval modes, Safety, and the FAQ cache. It is used by Cost controls, Email correspondence, and the React/Vite frontend, and it changes with the Chat request lifecycle, the Admin dashboard, and Cost controls. The decision: the browser stores the token locally and sends it in a header, with no cookies, because Netlify and Render are on different origins. Roles live in SQLite and are checked on each request, so changes apply at once. Owners come from an environment variable, and a content security policy is the defense against script injection. Cookie sessions, an external auth provider and a fixed admin list were not chosen. Yusuf Pisan wrote it alone [human] from May to July 2026.

Open first: `backend/roles.py`, `backend/main.py`, `frontend/src/auth.js`.

## 6. SQLite persistence (analytics.db) and backup (`analytics-persistence`)

This unit is the system of record. One SQLite file on Render's persistent disk holds conversations, users and roles, bans, feedback, proposals, usage and settings, and chat context is rebuilt from it by session. It uses Cost controls, and is used by the Admin dashboard and Cost controls; it changes with Cost controls, the Admin dashboard, and Auth. The decision: a single-file database with migrations in code, plus a daily compressed snapshot pushed to a private GitHub repository through its API to guard against disk loss. Postgres, a managed database and browser or cookie session state were not chosen. The stated reason is that SQLite is simple and cheap for a single owner and survives redeploys. Yusuf Pisan created it alone [human] in May 2026 and extended it alone [human] in June and July. The history does not show why the backup uses a GitHub repository.

Open first: `backend/analytics.py`, `backend/backup.py`, `scripts/backup_db.py`.

## 7. Cost controls, rate limits and usage metering (`cost-control`)

This unit keeps the owner's personal Anthropic API key from running up a large bill. It applies per-minute and per-session rate limits, daily exchange caps, token and monthly dollar caps with a login-required threshold, and a bring-your-own-key exemption. Side calls to Haiku are metered through a context-local tracker. It uses the Persona prompt, Safety, SQLite persistence, Auth and the FAQ cache. It is used by the Chat request lifecycle, Safety, the Persona prompt, the Admin dashboard, the FAQ cache, SQLite persistence and Email. It changes with the Chat request lifecycle, the Admin dashboard and Auth. No limits, a paid-only gate and a flat quota were not chosen. The history only says the changes were cost-driven. Yusuf Pisan built it over three commits from May to June 2026, all with an AI co-author [assisted], including disabling an unmetered daily cron.

Open first: `backend/usage_tracker.py`, `backend/main.py`, `backend/analytics.py`.

## 8. Chat request lifecycle (SSE) (`chat-flow`)

This unit handles every chat message. One asynchronous FastAPI endpoint streams Server-Sent Events from the async Anthropic client, so the reply appears as it is generated. It uses Cost controls, the Persona prompt and retrieval modes, Safety, and the FAQ cache, and it is used by the React/Vite frontend. It changes with Auth, Cost controls and the Admin dashboard. The flow is a cheap Haiku hostile pre-check, then retrieval and model choice, then streaming with a hard sentence cap. Guardrail and follow-up calls run concurrently afterward, and history is recorded even if the client disconnects. WebSockets, blocking requests and a worker queue were not chosen, in favor of low latency and a free event loop. Yusuf Pisan wrote the first streaming version alone [human] in May 2026, reworked it with an AI co-author [assisted] in June, and hardened it alone [human] in July.

Open first: `backend/main.py`, `backend/sentences.py`, `frontend/src/sse.js`.

## 9. Admin dashboard and Persona Fixes loop (`admin-persona-fix-loop`)

This unit lets admins review conversations, rate replies, and turn feedback into persona changes. It uses the Persona prompt, Cost controls, the FAQ cache, Safety and SQLite persistence, and it changes with the Chat request lifecycle, Cost controls and Auth. The decision: feedback goes to Sonnet, which returns whole-file rewrites. A background daemon thread does the work while the browser polls, since synchronous generation timed out at the edge proxy. Edits are limited to an allowlist of persona files and open a GitHub pull request, never a push to main, so a human approves. Manual edits, a synchronous request and a job queue were not chosen. Yusuf Pisan introduced it with an AI co-author [assisted] in May 2026, then extended the admin page and sessions alone [human] in late May and June.

Open first: `frontend/src/AdminPage.jsx`, `backend/repo_admin.py`, `backend/faq_gap_report.py`.

## 10. React/Vite frontend (`frontend-spa`)

This unit is what visitors see. The single-page app covers chat, the admin area, a shared view and the warmth meter, and installs as a PWA. It is hosted on Netlify and calls the Render backend directly. It uses the Chat request lifecycle and Auth, and it changes with the Chat request lifecycle, the Admin dashboard and Auth. The decision: plain React in JavaScript with JSDoc type checking, local storage for the token, session resume and visitor id, no proxy, and restricted CORS. Next.js server rendering, a Netlify proxy and cookies were not chosen, because the deploy is cross-origin and a static host is simple and installable. Yusuf Pisan did most of the work alone [human], including July hardening and a September avatar and home-screen change. The June type-checking work was done with an AI co-author [assisted].

Open first: `frontend`.

## 11. Email correspondence and notifications (`email-notifications`)

This unit lets the bot handle email without becoming a spam risk. Resend delivers inbound mail through a webhook into stored threads, and drafts are generated only when an admin clicks and sent only after explicit approval. A notifier emails people who hold the right capability and throttles API-key alerts using a timestamp in the database. It uses the Persona prompt, Auth, and Cost controls, and it changes with Cost controls, the Chat request lifecycle and the Admin dashboard. Auto-reply, the Gmail API and SMTP were not chosen. The stated reasons are avoiding spam and cost and keeping a human gate, and the features stay inert without environment configuration. Yusuf Pisan wrote it alone [human] in late May and June 2026. The history does not show why Resend was picked over the alternatives.

Open first: `backend/email_agent.py`, `backend/notifier.py`, `backend/email_topics.md`.

## 12. Contacts database with privacy filter (`contacts-db`)

This unit lets the bot look up people it knows without carrying them in every prompt. A separate contacts SQLite database is built offline by scripts from email and LinkedIn data, and matches are injected into prompts only for that request. A privacy filter drops personal-email and family contacts, and the file is deployed to Render's disk. It changes with the Persona prompt and retrieval modes, Cost controls and Email correspondence; no unit lists it as a user or provider. Putting contacts in persona files, or having no lookup, was not chosen. With about 18K private entries, the data is kept out of the prompt and out of git. The history shows only an AI-assisted [assisted] commit by Yusuf Pisan in June 2026 recording the privacy rebuild and production deploy. Who first built it, and when, is not visible.

Open first: `backend/contacts.py`, `scripts/populate_contacts_db.py`, `scripts/parse_email_contacts.py`.

---

Next: `udebt path` for the order to learn these in and the time each takes, `udebt dig <unit>` for a walkthrough of one with its code.

