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
