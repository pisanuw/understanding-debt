# BRIEFING

## Scope

A standalone CLI (`udebt`) that measures and helps reduce *understanding debt*: the gap
between a developer's mental model and the code an AI wrote under their direction. The
primary use is pointing it at an **existing repository with mixed human and AI history**
and making sure the developer understands the decisions that shaped it. Target user is a
**team** sharing one repository. Product, not a research instrument. Numeric scores are
acceptable.

## Decisions

- **Measure by prediction, not provenance.** Provenance (who typed it) says the developer
  understands nothing in an AI-driven workflow, which is false at the architectural level.
  Prediction questions graded against the code test the actual mental model.
- **Architectural units, not files.** The LLM extracts decisions, modules, boundaries and
  flows. Package internals are out of scope by design. A developer can mark a unit
  `blackbox` to declare deliberate ignorance; only accidental ignorance counts as debt.
- **Misconceptions cost more than gaps.** Confident wrong answers score negative evidence.
  Blank answers are recorded as known unknowns with no penalty.
- **Decay per commit.** Understanding verified at commit X decays by 0.9 per later commit
  touching the unit's paths. This is what makes debt grow while the AI builds.
- **Decisions come from history as much as from code.** Cheap git heuristics pick
  decision-bearing commits (founding, dependencies, infra, new dirs, renames, big
  restructurings); the LLM reads code plus that list and links each unit to the commits that
  shaped it (`history` on each unit).
- **Three commit classes**: `ai` (agent-authored), `assisted` (human author, AI co-author
  trailer), `human`. Signatures are data in config; Claude Code is the only one that matters
  today, others are stubs for later.
- **Weak prior from own commits**, capped at 0.5, AI-assisted commits at half weight,
  decayed by every commit someone else landed since. Replaced by any quiz evidence.
- **Identity aliases** in config (`identities`) so a developer's several emails count as one.
- **Testbeds**: `pisanuw/digitalme` (190 commits, 126 human / 62 assisted / 2 ai) is the
  primary; `pisanuw/dsa-instructor` (2510 commits, mostly ai) is the stress test; the small
  all-human repos are controls.
- **LLM via the `claude` CLI**, subscription-billed, read-only tools (Read, Glob, Grep),
  structured output via `--json-schema`. No API key. `UDEBT_LLM=fake` for offline tests.
- **Storage is JSON under `.udebt/`**, all of it committed to the repo by default:
  `map.json` shared, `records/<email-slug>.jsonl` per developer, append-only. One file
  per developer keeps merges conflict-free.
- **Five questions per quiz session** by default. Short enough to run often.
- **Python, stdlib only** at runtime. Dev deps: pytest, ruff.

## Non-goals (for now)

- Editor or Claude Code hook integration (provenance tracking). Later phase.
- Time-based decay. Only commit-based decay in the slice.
- Validating that the debt index predicts outcomes (bug-fix time). Would be a research
  project; not needed for the product slice.
- Language-specific static analysis for importance. Churn plus LLM centrality is the proxy.

## Open questions

- Threshold for "architectural decision worth an acknowledgement" when we add
  capture-at-generation-time.
