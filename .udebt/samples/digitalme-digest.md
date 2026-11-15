# digitalme change digest (Phase 3.4 validation)

Run on 2026-11-15 as `udebt --as-user pisan@uw.edu digest --since 2026-07-01` on digitalme at f93102b: one `claude` CLI call (94K characters of prompt, 32 s) for 11 units below 0.6 in his set, 66 commits; decisions 6 held, 5 extended, 0 changed. Cached in the testbed's gitignored `.udebt/digests/`; a second run made no call.

```
Writing 11 briefing(s) (one call, cached in .udebt/digests/)...

Change digest for pisan@uw.edu: units in your set you understand below 0.6

== Chat request lifecycle (SSE) (`chat-flow`): understanding 0.41 (estimated)
   since 2026-07-01 (6f9cafc0): 6f9cafc0..f93102b1
   11 commit(s), mass 1.93 in the unit:
     2026-07-31 975dc5cd Yusuf Pisan            human              w 0.95 m 0.49  Hardening: message cap, concurrent post-stream calls, discon
     2026-07-30 cad28b12 Yusuf Pisan            human              w 1.00 m 0.40  Chat UX round: tap-to-send chips, visitor ratings, typing do
     2026-07-21 b5f6bc28 Yusuf Pisan            assisted/claude-co w 0.80 m 0.21  feat(backend): add ElevenLabs voice replies for cached FAQs
     2026-07-21 5c4bdb35 Yusuf Pisan            assisted/claude-co w 0.79 m 0.18  feat(backend): regenerate FAQ voice audio for a single entry
     2026-07-31 9994fbd8 Yusuf Pisan            human              w 0.89 m 0.15  Nightly offsite backup of analytics.db to a private GitHub r
     2026-09-02 74ad9444 Yusuf Pisan            human              w 0.79 m 0.15  feat(frontend): headshot avatars, site icons, and Add to Hom
     2026-07-22 836879a9 Yusuf Pisan            assisted/claude-co w 0.95 m 0.12  feat(backend): add monthly FAQ + gap report from conversatio
     2026-11-08 f93102b1 claude-code            ai/claude-code     w 0.17 m 0.08  fix(backend): add ypai.hintwise.org to CORS allow_origins
     ... and 3 lighter commit(s)
   In brief: The chat endpoint kept its single async SSE shape but got hardened (message
     cap, concurrent post-calls, disconnect-safe history) and grew a lot of visitor-facing
     features around it.
   - The endpoint now rejects messages over a hard server-side size cap, runs the guardrail
     and follow-up post-calls concurrently, and records history safely when the client
     disconnects, with a Stop button and stream watchdog on the client. (975dc5c)
   - The chat flow now returns the assistant message id so visitors can rate replies, and
     supports resuming a transcript on reload, tap-to-send chips, retry and smarter
     scrolling in the UI. (cad28b1)
   - Replies pick up a new top 'high_plus' warmth tier with no sentence cap, and warmth
     state is exposed to visitors, shown as a footer meter in the app. (0c7ea90, 77b4e07,
     977702f)
   - Cached FAQ answers can now be played as ElevenLabs voice clips, and the frontend gained
     headshot avatars and Add to Home Screen support; the new hintwise origin was added to
     CORS. (b5f6bc2, 74ad944, f93102b)
   Decision extended: the decision stands and now covers more. The single async SSE
     endpoint, pre-checks, hard sentence cap and post-calls still stand; the sentence cap
     now has one deliberate exception (high_plus) and the flow covers voice, ratings and
     resume.
   Look at: backend/main.py, frontend/src/App.jsx, backend/sentences.py
   Then: `udebt dig chat-flow` to see the code, or `udebt quiz --unit chat-flow` to show what you know.

== Cost controls, rate limits and usage metering (`cost-control`): understanding 0.40 (estimated)
   since 2026-07-01 (6f9cafc0): 6f9cafc0..f93102b1
   9 commit(s), mass 1.51 in the unit:
     2026-07-31 975dc5cd Yusuf Pisan            human              w 0.95 m 0.44  Hardening: message cap, concurrent post-stream calls, discon
     2026-07-30 cad28b12 Yusuf Pisan            human              w 1.00 m 0.28  Chat UX round: tap-to-send chips, visitor ratings, typing do
     2026-07-21 5c4bdb35 Yusuf Pisan            assisted/claude-co w 0.79 m 0.18  feat(backend): regenerate FAQ voice audio for a single entry
     2026-07-22 836879a9 Yusuf Pisan            assisted/claude-co w 0.95 m 0.17  feat(backend): add monthly FAQ + gap report from conversatio
     2026-07-31 9994fbd8 Yusuf Pisan            human              w 0.89 m 0.15  Nightly offsite backup of analytics.db to a private GitHub r
     2026-07-21 b5f6bc28 Yusuf Pisan            assisted/claude-co w 0.80 m 0.14  feat(backend): add ElevenLabs voice replies for cached FAQs
     2026-11-08 f93102b1 claude-code            ai/claude-code     w 0.17 m 0.08  fix(backend): add ypai.hintwise.org to CORS allow_origins
     2026-09-02 77b4e075 Yusuf Pisan            human              w 0.24 m 0.05  feat(backend): expose warmth meter state to visitors
     ... and 1 lighter commit(s)
   In brief: Spend controls gained a hard per-message size cap and cheaper concurrent post-
     calls, while new background jobs (monthly report, TTS) were added around the existing
     limits.
   - Messages over 2000 characters are now refused server-side, since the client limit was
     bypassable and long inputs could ride the cascade into expensive tiers. (975dc5c)
   - A monthly FAQ and gap report runs on the cheap utility model, once per calendar month
     via a settings marker, and emails owners. (836879a)
   - ElevenLabs audio generation was added as admin-triggered work (whole bank or a single
     entry) that only synthesizes missing clips unless forced, and does no Anthropic calls.
     (b5f6bc2, 5c4bdb3)
   - The new high_plus warmth tier has a much higher token ceiling (1500) and is reachable
     only when admin verbosity is high. (0c7ea90)
   Decision held: the decision stands; the changes work within it. The layered rate limits,
     caps and metering are untouched; the new message cap and run-once-per-month or on-click
     jobs work in the same spirit of bounding spend.
   Look at: backend/main.py, backend/notifier.py
   Then: `udebt dig cost-control` to see the code, or `udebt quiz --unit cost-control` to show what you know.

== Admin dashboard and Persona Fixes loop (`admin-persona-fix-loop`): understanding 0.38 (estimated)
   since 2026-07-01 (6f9cafc0): 6f9cafc0..f93102b1
   9 commit(s), mass 2.25 in the unit:
     2026-07-22 836879a9 Yusuf Pisan            assisted/claude-co w 0.95 m 0.82  feat(backend): add monthly FAQ + gap report from conversatio
     2026-07-31 975dc5cd Yusuf Pisan            human              w 0.95 m 0.44  Hardening: message cap, concurrent post-stream calls, discon
     2026-07-21 5c4bdb35 Yusuf Pisan            assisted/claude-co w 0.79 m 0.33  feat(backend): regenerate FAQ voice audio for a single entry
     2026-07-21 b5f6bc28 Yusuf Pisan            assisted/claude-co w 0.80 m 0.20  feat(backend): add ElevenLabs voice replies for cached FAQs
     2026-07-30 cad28b12 Yusuf Pisan            human              w 1.00 m 0.15  Chat UX round: tap-to-send chips, visitor ratings, typing do
     2026-07-31 9994fbd8 Yusuf Pisan            human              w 0.89 m 0.15  Nightly offsite backup of analytics.db to a private GitHub r
     2026-11-08 f93102b1 claude-code            ai/claude-code     w 0.17 m 0.08  fix(backend): add ypai.hintwise.org to CORS allow_origins
     2026-09-02 77b4e075 Yusuf Pisan            human              w 0.24 m 0.05  feat(backend): expose warmth meter state to visitors
     ... and 1 lighter commit(s)
   In brief: The admin area gained a monthly FAQ and gap report, FAQ audio controls, and
     visitor ratings that feed the same Persona Fixes pipeline.
   - A new report module reads conversation logs to propose new FAQs and surface hedges,
     thumbs-downs and guardrail trips, with the old gap-analysis script now reduced to a
     thin wrapper around it. (836879a)
   - Visitor thumbs-up and down ratings land in the same review table, tagged by source, so
     they flow into the Persona Fixes list alongside admin reviews. (cad28b1)
   - The Persona tab in the admin page gained buttons to run the gap report and to generate
     or regenerate FAQ audio, whole bank or single entry. (b5f6bc2, 5c4bdb3, 836879a)
   - The admin UI picked up the new high_plus verbosity option. (0c7ea90)
   Decision extended: the decision stands and now covers more. The PR-gated persona edit
     loop is unchanged, but the feedback inputs now include visitor ratings and a gap
     report, with FAQ creation still left as a human step.
   Look at: backend/faq_gap_report.py, frontend/src/AdminPage.jsx, backend/main.py
   Then: `udebt dig admin-persona-fix-loop` to see the code, or `udebt quiz --unit admin-persona-fix-loop` to show what you know.

== Auth and role-based access (`auth-rbac`): understanding 0.39 (estimated)
   since 2026-07-01 (6f9cafc0): 6f9cafc0..f93102b1
   10 commit(s), mass 1.48 in the unit:
     2026-07-31 975dc5cd Yusuf Pisan            human              w 0.95 m 0.45  Hardening: message cap, concurrent post-stream calls, discon
     2026-07-21 5c4bdb35 Yusuf Pisan            assisted/claude-co w 0.79 m 0.18  feat(backend): regenerate FAQ voice audio for a single entry
     2026-09-02 74ad9444 Yusuf Pisan            human              w 0.79 m 0.17  feat(frontend): headshot avatars, site icons, and Add to Hom
     2026-07-31 9994fbd8 Yusuf Pisan            human              w 0.89 m 0.15  Nightly offsite backup of analytics.db to a private GitHub r
     2026-07-21 b5f6bc28 Yusuf Pisan            assisted/claude-co w 0.80 m 0.14  feat(backend): add ElevenLabs voice replies for cached FAQs
     2026-07-30 cad28b12 Yusuf Pisan            human              w 1.00 m 0.14  Chat UX round: tap-to-send chips, visitor ratings, typing do
     2026-07-22 836879a9 Yusuf Pisan            assisted/claude-co w 0.95 m 0.12  feat(backend): add monthly FAQ + gap report from conversatio
     2026-11-08 f93102b1 claude-code            ai/claude-code     w 0.17 m 0.08  fix(backend): add ypai.hintwise.org to CORS allow_origins
     ... and 2 lighter commit(s)
   In brief: Auth itself was left alone, but the browser-side defenses around the
     localStorage JWT were tightened and new admin-gated endpoints were added.
   - Netlify now sends a strict Content-Security-Policy and other security headers, which is
     the main XSS mitigation for a token kept in localStorage, with allowances only for the
     API origin and Google Sign-In. (975dc5c)
   - New admin endpoints for FAQ audio, the gap report and others sit behind the existing
     admin dependency. (b5f6bc2, 5c4bdb3, 836879a)
   - The new hintwise frontend origin was added to the CORS allow-list. (f93102b)
   - The Add to Home Screen manifest uses minimal-ui so Google sign-in popups still work on
     iOS. (74ad944)
   Decision held: the decision stands; the changes work within it. The Google-to-JWT,
     header-based, no-cookie design is unchanged; the CSP and the CORS entry reinforce it.
   Look at: frontend/netlify.toml, backend/main.py
   Then: `udebt dig auth-rbac` to see the code, or `udebt quiz --unit auth-rbac` to show what you know.

== Persona prompt and retrieval modes (`persona-retrieval`): understanding 0.45 (estimated)
   since 2026-07-01 (6f9cafc0): 6f9cafc0..f93102b1
   1 commit(s), mass 0.07 in the unit:
     2026-09-02 c2f6d82f Yusuf Pisan            human              w 0.08 m 0.07  feat(persona): point build/model questions at the public vir
   In brief: Persona content got one small addition: questions about how YPai is built now
     point visitors to the public repo.
   - The persona files now tell YPai not to explain its internals or model, but to point
     people to the open-source virtualme repo and invite them to fork it. (c2f6d82)
   Decision held: the decision stands; the changes work within it. Persona knowledge is
     still plain markdown in the repo and no retrieval code changed; this is only a content
     edit.
   Look at: persona/persona-system.md, persona/persona-distilled.md, persona/projects.md
   Then: `udebt dig persona-retrieval` to see the code, or `udebt quiz --unit persona-retrieval` to show what you know.

== SQLite persistence (analytics.db) and backup (`analytics-persistence`): understanding 0.40 (estimated)
   since 2026-07-01 (6f9cafc0): 6f9cafc0..f93102b1
   2 commit(s), mass 0.92 in the unit:
     2026-07-31 9994fbd8 Yusuf Pisan            human              w 0.89 m 0.67  Nightly offsite backup of analytics.db to a private GitHub r
     2026-07-30 cad28b12 Yusuf Pisan            human              w 1.00 m 0.25  Chat UX round: tap-to-send chips, visitor ratings, typing do
   In brief: The SQLite store gained a rating-source column and display helpers, and a
     nightly offsite backup to a private GitHub repo now exists.
   - A new backup module pushes a gzipped snapshot of the database to a private repo through
     the Contents API once per UTC day, plus a latest pointer, and stays inert unless
     configured. (9994fbd)
   - The reviews table gained a source column via an in-code migration, and new helpers save
     visitor ratings, check which session owns a message, and rebuild a transcript for
     resume. (cad28b1)
   - Recording an assistant message now returns its row id so the chat flow can hand it to
     the frontend. (cad28b1)
   Decision held: the decision stands; the changes work within it. Single-file SQLite with
     in-code migrations remains the system of record, and the backup it called for is now
     actually implemented.
   Look at: backend/backup.py, backend/analytics.py
   Then: `udebt dig analytics-persistence` to see the code, or `udebt quiz --unit analytics-persistence` to show what you know.

== React/Vite frontend (`frontend-spa`): understanding 0.43 (estimated)
   since 2026-07-01 (6f9cafc0): 6f9cafc0..f93102b1
   9 commit(s), mass 3.06 in the unit:
     2026-09-02 74ad9444 Yusuf Pisan            human              w 0.79 m 0.74  feat(frontend): headshot avatars, site icons, and Add to Hom
     2026-07-30 cad28b12 Yusuf Pisan            human              w 1.00 m 0.71  Chat UX round: tap-to-send chips, visitor ratings, typing do
     2026-09-02 977702ff Yusuf Pisan            human              w 0.39 m 0.33  feat(frontend): footer warmth meter with stage label and inf
     2026-09-26 0d2d82e7 Yusuf Pisan            human              w 0.81 m 0.33  Added encrypted .env file
     2026-07-21 5c4bdb35 Yusuf Pisan            assisted/claude-co w 0.79 m 0.28  feat(backend): regenerate FAQ voice audio for a single entry
     2026-07-21 b5f6bc28 Yusuf Pisan            assisted/claude-co w 0.80 m 0.25  feat(backend): add ElevenLabs voice replies for cached FAQs
     2026-07-31 975dc5cd Yusuf Pisan            human              w 0.95 m 0.24  Hardening: message cap, concurrent post-stream calls, discon
     2026-07-22 836879a9 Yusuf Pisan            assisted/claude-co w 0.95 m 0.17  feat(backend): add monthly FAQ + gap report from conversatio
     ... and 1 lighter commit(s)
   In brief: The frontend became noticeably richer: visitor rating, link handling, warmth
     meter, installable icons, and a strict CSP.
   - The chat UI gained tap-to-send chips, visitor ratings, typing dots, smart scroll,
     retry, link detection, a Stop button and stream watchdog. (cad28b1, 975dc5c)
   - A footer warmth meter shows the stage label with an info popover. (977702f)
   - The app is installable with a web manifest, headshot avatars and icons, and uses
     minimal-ui so Google sign-in works on iOS. (74ad944)
   - Netlify headers add a strict CSP, and the admin page gained FAQ audio and gap report
     controls; an encrypted env file was also committed. (975dc5c, b5f6bc2, 5c4bdb3,
     836879a, 0d2d82e)
   Decision held: the decision stands; the changes work within it. It is still a plain React
     SPA calling the Render API directly with localStorage state; the new CSP allow-lists
     that API origin rather than adding a proxy.
   Look at: frontend/src/App.jsx, frontend/src/WarmthMeter.jsx, frontend/netlify.toml
   Then: `udebt dig frontend-spa` to see the code, or `udebt quiz --unit frontend-spa` to show what you know.

== Safety: hostile pre-check, guardrails, bans (`safety-guardrails`): understanding 0.40 (estimated)
   since 2026-07-01 (6f9cafc0): 6f9cafc0..f93102b1
   4 commit(s), mass 0.88 in the unit:
     2026-07-22 c5c1d75b Yusuf Pisan            assisted/claude-co w 0.65 m 0.47  fix(backend): make guardrail rule 6 (no em dashes) determini
     2026-09-02 977702ff Yusuf Pisan            human              w 0.39 m 0.19  feat(frontend): footer warmth meter with stage label and inf
     2026-09-02 77b4e075 Yusuf Pisan            human              w 0.24 m 0.12  feat(backend): expose warmth meter state to visitors
     2026-09-02 0c7ea903 Yusuf Pisan            human              w 0.11 m 0.10  feat(backend): add high_plus warmth tier, unlocked by verbos
   In brief: The em-dash rule is now checked by exact character match instead of the LLM
     monitor, and the warmth ramp gained a top tier.
   - Rule 6 (no em dashes) is enforced deterministically in code and holds even during a
     monitor outage, because the model flagged invented em dashes at about a 65% false-
     positive rate. (c5c1d75)
   - The warmth ramp gained a high_plus tier with its own thresholds, reachable only when
     admin verbosity is high. (0c7ea90)
   - Warmth state is exposed to visitors, and a footer meter component renders it. (77b4e07,
     977702f)
   Decision extended: the decision stands and now covers more. The fail-closed LLM post-
     check stands, but one rule now has a deterministic code check layered on top of it.
   Look at: backend/guardrails.py, backend/warmth.py, frontend/src/WarmthMeter.jsx
   Then: `udebt dig safety-guardrails` to see the code, or `udebt quiz --unit safety-guardrails` to show what you know.

== Content pipeline and CI/deploy (`content-pipeline`): understanding 0.43 (estimated)
   since 2026-07-01 (6f9cafc0): 6f9cafc0..f93102b1
   3 commit(s), mass 0.90 in the unit:
     2026-07-31 e3f58b10 Yusuf Pisan            human              w 0.68 m 0.65  CI: auto-deploy Render on backend merges, gated on ruff + un
     2026-09-02 74ad9444 Yusuf Pisan            human              w 0.79 m 0.17  feat(frontend): headshot avatars, site icons, and Add to Hom
     2026-07-31 975dc5cd Yusuf Pisan            human              w 0.95 m 0.08  Hardening: message cap, concurrent post-stream calls, discon
   In brief: Deploys became more automatic: backend merges now ship to Render after lint and
     tests pass, and Netlify gained security and manifest headers.
   - The publish-on-merge workflow now also triggers on backend and Dockerfile changes
     (previously a manual deploy), and fires the Render hook only after ruff and unit tests
     pass. (e3f58b1)
   - Netlify now serves a strict CSP and security headers, plus manifest and icon cache
     headers. (975dc5c, 74ad944)
   Decision extended: the decision stands and now covers more. Merge-to-deploy gated by CI
     still holds, but it now covers backend code and not only persona edits.
   Look at: .github/workflows/publish-on-merge.yml, frontend/netlify.toml
   Then: `udebt dig content-pipeline` to see the code, or `udebt quiz --unit content-pipeline` to show what you know.

== FAQ cache and voice replies (`faq-cache-tts`): understanding 0.34 (estimated)
   since 2026-07-01 (6f9cafc0): 6f9cafc0..f93102b1
   7 commit(s), mass 1.58 in the unit:
     2026-07-21 b5f6bc28 Yusuf Pisan            assisted/claude-co w 0.80 m 0.53  feat(backend): add ElevenLabs voice replies for cached FAQs
     2026-07-21 5c4bdb35 Yusuf Pisan            assisted/claude-co w 0.79 m 0.48  feat(backend): regenerate FAQ voice audio for a single entry
     2026-07-21 71515728 Yusuf Pisan            assisted/claude-co w 0.64 m 0.33  fix(backend): stop FAQ matcher dropping all-stopword questio
     2026-07-21 ebaab0ce Yusuf Pisan            assisted/claude-co w 0.20 m 0.19  fix(backend): stop caching truncated FAQ answers + add repai
     2026-09-02 c2f6d82f Yusuf Pisan            human              w 0.08 m 0.04  feat(persona): point build/model questions at the public vir
     2026-07-21 8bc06ed9 Yusuf Pisan            assisted/claude-co w 0.01 m 0.01  feat(backend): show full FAQ answers in admin, not truncated
     2026-07-21 4becc06c Yusuf Pisan            assisted/claude-co w 0.00 m 0.00  fix(scripts): drop unused import flagged by full ruff CI
   In brief: The FAQ cache gained voice clips and several correctness fixes, so answers are
     no longer truncated or silently unmatched.
   - A new TTS module generates ElevenLabs audio for cached answers, stored by content hash,
     with a bank-wide generator and a single-entry regenerate. (b5f6bc2, 5c4bdb3)
   - FAQ generation token caps were raised and truncation is logged, with a CLI repair tool
     that rebuilds the whole cache at no API cost. (ebaab0c)
   - The matcher no longer strips English stopwords, so questions like 'Who are you?' can
     finally match instead of falling through to the LLM. (7151572)
   - Two new cached questions about how YPai is built and what model it uses were added, and
     the admin view shows full answers. (c2f6d82, 8bc06ed)
   Decision extended: the decision stands and now covers more. Pre-generated answers served
     without a full call still stand, and audio clips keyed by content hash fit that same
     approach.
   Look at: backend/tts.py, backend/faq_cache.py, backend/scripts/faq_repair_cli.py
   Then: `udebt dig faq-cache-tts` to see the code, or `udebt quiz --unit faq-cache-tts` to show what you know.

== Email correspondence and notifications (`email-notifications`): understanding 0.42 (estimated)
   since 2026-07-01 (6f9cafc0): 6f9cafc0..f93102b1
   1 commit(s), mass 0.13 in the unit:
     2026-07-22 836879a9 Yusuf Pisan            assisted/claude-co w 0.95 m 0.13  feat(backend): add monthly FAQ + gap report from conversatio
   In brief: The notifier gained one new email: the monthly FAQ and gap report sent to
     owners.
   - A new notification sends the report to owner addresses as escaped preformatted text,
     and is skipped when email isn't configured. (836879a)
   Decision held: the decision stands; the changes work within it. It is still an env-gated,
     owner-only notification that does nothing without configuration.
   Look at: backend/notifier.py
   Then: `udebt dig email-notifications` to see the code, or `udebt quiz --unit email-notifications` to show what you know.

Nothing landed since your start point in: contacts-db.
```
