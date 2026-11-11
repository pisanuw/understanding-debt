# CHANGES

2026-11-11 [scope] Defined understanding debt (gap between developer mental model and AI-written code). Team target, standalone CLI over git, product, numeric scores OK.
2026-11-11 [decision] Measure by prediction questions graded against code, not by provenance. Unmeasured = 0 understanding. Confident-wrong scores negative. Blackbox scope excludes a unit.
2026-11-11 [decision] Understanding decays 0.9 per commit touching the unit since last verified. Importance = 0.6*centrality + 0.4*churn.
2026-11-11 [code] Vertical slice: udebt init/map/units/scope/quiz/status/dig. claude CLI backend with --json-schema and read-only tools; FakeLLM for offline tests. 12 tests.
2026-11-11 [note] Real run on this repo: map 9 units in 23s, questions verified against code, grading caught a partial answer, dig found silent no-record when CHECK QUESTION missing. Fixed; mapper cap now enforced.
