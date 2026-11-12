# CHANGES

2026-11-11 [scope] Defined understanding debt (gap between developer mental model and AI-written code). Team target, standalone CLI over git, product, numeric scores OK.
2026-11-11 [decision] Measure by prediction questions graded against code, not by provenance. Unmeasured = 0 understanding. Confident-wrong scores negative. Blackbox scope excludes a unit.
2026-11-11 [decision] Understanding decays 0.9 per commit touching the unit since last verified. Importance = 0.6*centrality + 0.4*churn.
2026-11-11 [code] Vertical slice: udebt init/map/units/scope/quiz/status/dig. claude CLI backend with --json-schema and read-only tools; FakeLLM for offline tests. 12 tests.
2026-11-11 [note] Real run on this repo: map 9 units in 23s, questions verified against code, grading caught a partial answer, dig found silent no-record when CHECK QUESTION missing. Fixed; mapper cap now enforced.
2026-11-12 [decision] Commit .udebt/records/ by default (one file per developer). Keep 5 questions per quiz session.
2026-11-12 [scope] Primary use is an existing repo with mixed human/AI history: make sure the developer understands the decisions that shaped it. Testbed digitalme, stress test dsa-instructor.
2026-11-12 [code] Provenance classifier (ai/assisted/human, data-driven signatures), history mining for decision commits, history-aware map, own-commit prior (cap 0.5), identity aliases, `udebt history`, ai%/you% in status.
2026-11-12 [plan] ROADMAP.md: 5 phases (contributors+commit weight, units+responsibility, time series+local dashboard, hosted Netlify/Render/Supabase, validity). 8 open questions. Research: Substrate Collapse, DOK/DOA, Osmani, Storey, Assisted-by trailers.
2026-11-12 [decision] Owner answered the 8 roadmap questions: org API key hosted, owner-assigned visibility roles, advisory gating with opt-in soft block, seeded+curated responsibility, refine top 40, metadata-only AI heuristics, Py/JS-TS/C++/Rust/Java, live dashboard+sync+export.
2026-11-12 [code] Phase 1: commit weights (heuristic + LLM refine, cached), weighted churn/decay/provenance/prior with per-unit line share, `contributors` with AI/joint/human/bot/most-likely labels, `risk` (min over units, advisory), `hook install`, `map --rescore`.
2026-11-12 [code] Structural discovery: import graph (Python ast, JS/TS regex; one function per language), co-change graph (<=30-file commits), stdlib Louvain, sub-clusters; docs attach to clusters.
2026-11-12 [decision] Line share for unit attribution ignores lockfile and generated lines: the June digitalme refactor no longer lands most of its weight in the frontend through package-lock.json.
2026-11-12 [code] Map v3: clusters feed the prompt; units get up to 6 sub-units (`parent/child`). Sub-units split the parent's importance, inherit half its quiz, roll quizzes up. --deep views.
2026-11-12 [code] Responsibility sets in .udebt/responsibility.json: seed from CODEOWNERS (handles via config or noreply, else pending) or human footprint; lead curates; index over set + touching.
2026-11-12 [decision] Scope is per developer by default (`--for <dev>`; `--team` writes the map). Risk adds OUTSIDE (outside your set, mass >= 0.1, und < 0.6) as its strongest level.
2026-11-12 [decision] Footprint ignores AI-authored commits and counts assisted ones in full: responsibility is human. A unit only agents touched is in nobody's set until a lead assigns it.
2026-11-12 [decision] Team view checks a bus target per unit (config bus_target, default 2) and lists units below it with who is responsible. Parents roll up sub-unit quizzes only, not their priors.
2026-11-12 [note] Real runs: digitalme 12 units/3 sub-units in 37s; dsa-instructor 12/9 in 62s (graph 4s). risk on dsa b194175 flags teachback OUTSIDE. Agent-only units are in nobody's set.
2026-11-12 [decision] Testbeds: count every agent, not only Claude. Starter set huggingface_hub, sentry-javascript, mlflow + servo control; no team contacts, so validate via reverts; Py/TS first.
2026-11-12 [doc] TESTBEDS.md + testbeds.json: 34 public repos screened from metadata, pinned shas; built-in signatures miss Assisted-by, Devin, claude[bot], OpenCode, Generated-by.
2026-11-12 [decision] Owner: merge testbeds branch; Phase 1.1 (missing agent signatures, case-insensitive emails, bot class out of human share/decay, PR grouping for squash bias); synthetic developers + reverts as validation, first experiment after 1.1.
2026-11-12 [code] Phase 1.1 classifier: Assisted-by/Generated-by/Made-with naming an AI, Devin, claude[bot], OpenCode, Jules, Codex connector, Amazon Q, vscode Copilot footer; case-insensitive.
2026-11-12 [decision] Fourth class `bot` (bot signatures + config bot_signatures) lands no mass: out of churn, decay, prior, risk, co-change and share denominators. Bot + agent trailer = ai.
2026-11-12 [decision] Directed agent work (config `directed`, `udebt directed add`): a matched ai commit stays ai but counts as the developer's assisted work (half) in share, prior, footprint.
2026-11-12 [decision] A file listed by k units gives each 1/k of its lines (top level, then among the parent's sub-units); one Membership index per map feeds every attribution.
2026-11-12 [decision] Prior cap stays 0.5. Team view without any quiz says 'no quiz evidence yet; holders need a quiz score >= threshold' and labels the holder column `quizzed`.
2026-11-12 [decision] Squash bias: a merged branch (second-parent chain, <= group_max_commits 50) is weighed once as if squashed, shared by effective lines. commits.json v2.
2026-11-12 [note] Grouping cuts total mass: digitalme 83.9 -> 69.7, dsa-instructor 991.9 -> 541.2, go-git 1388 -> 898; squash-merging repos (prefect) unchanged.
2026-11-12 [code] `udebt experiment synthetic --at <rev>`: claude quizzed from an old worktree snapshot (3 calls), graded on HEAD; report in .udebt/experiments/, nothing in records/.
2026-11-12 [note] First synthetic run, digitalme at 44f520b, 12 units: 0 correct, Spearman(mass since, wrongness) -0.27 (p 0.81). No support for decay; floor effect and confidence term.
2026-11-12 [note] Dogfood .udebt regenerated: commits.json v2, map rescored, both sessions directed by pisan@uw.edu, whose seeded set is now all 12 units (index 74.9).
2026-11-12 [note] Starter-set offline run on ba564e2 (TESTBEDS.md): servo 0 AI in 59k commits; human-cut version bumps weigh >=0.7 (244 in sentry-js); servo weigh 18 min.
2026-11-12 [decision] Version-only manifest/version-file edits (own version or pins) are a `version` kind: no +0.3, out of line share; a commit of only bumps weighs 0.03. commits.json v3.
2026-11-12 [decision] Parsed-commit cache .udebt/commits-parsed.json(.gz): new shas parsed via rev-list, unreachable dropped, classes recomputed; gitignored, commits.json committed.
2026-11-12 [note] Cache on dsa-instructor: contributors 4.8 -> 0.26 s, weigh 5.2 (14.6 recompute) -> 0.30 s warm; prefect 4.1 -> 0.28, 4.2 (9.4) -> 0.29 s. Cold costs as before.
2026-11-12 [code] risk --staged reads staged version bumps, and its line share now uses the staged per-file line counts it already computed (it fell back to file counts).
2026-11-12 [code] experiment synthetic: --snapshots (one shared HEAD question set), --per-unit 2, decision/behaviour questions, edited-vs-new split, paired counts, --suggest, --resume.
2026-11-12 [note] Synthetic rerun, digitalme, snapshots at mass 2.3/25.7/50.4: score 1.00/0.46/0.21, 30 of 31 pairs worse; within-snapshot rho 0.39, 0.00. Decay over time, not units.
2026-11-12 [decision] No model change from one synthetic run: decay looks faster than 0.9/mass and spills past unit paths; wait for human quizzes (BRIEFING open questions).
2026-11-12 [note] First map on huggingface_hub (12 units, 45 s): decision_candidates misses the AI era; 22 of 40 touch no file alive at HEAD; ties go to oldest.
2026-11-12 [code] Phase 1.3 history mining: score = commit weight + new dir/renames/keyword bonuses, x share of files alive at HEAD (renames followed); none alive drops it unless founding.
2026-11-12 [decision] Candidates: 4 equal-mass periods get 5 of 40 slots each (config history_periods, history_quota); ties go to the newer commit. `udebt history --candidates` lists them, no LLM.
2026-11-12 [note] huggingface_hub 2c61ae8: last-12-month candidates 1 -> 25, none alive 22 -> 0, AI-involved 2 -> 18; map history links 9 (7 to one release) -> 31 on 30 commits, 22 recent.
2026-11-12 [decision] Owner: a pin moved to a new major (0.x: the minor) is a dependency change (+0.3), not a version bump; own version, patch, minor, calver stay bumps. commits.json v4.
2026-11-12 [note] Major-upgrade rule changes 0 commits on digitalme and dsa-instructor, 4 on huggingface_hub. Dogfood map not re-run: the old miner saw 3 candidates here, the new one 18.
2026-11-12 [note] Synthetic rerun, dsa-instructor, snapshots at mass 4.5/25.2/50.6: score 0.98/0.94/0.96, paired 4 worse 4 better; ceiling. Mean share since 0.10 at mass 50 vs 0.70 on digitalme.
2026-11-12 [decision] No model change from it; across both repos the score tracks a unit's share changed, not absolute mass. Decay by share is an open question (BRIEFING, ROADMAP Phase 5).
