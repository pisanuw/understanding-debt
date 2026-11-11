# BRIEFING

## Scope

A standalone CLI (`udebt`) that measures and helps reduce *understanding debt*: the gap
between a developer's mental model and the code an AI wrote under their direction. Target
user is a **team** sharing one repository. Product, not a research instrument. Numeric
scores are acceptable.

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
- **LLM via the `claude` CLI**, subscription-billed, read-only tools (Read, Glob, Grep),
  structured output via `--json-schema`. No API key. `UDEBT_LLM=fake` for offline tests.
- **Storage is JSON under `.udebt/`**, committed to the repo: `map.json` shared,
  `records/<email-slug>.jsonl` per developer, append-only.
- **Python, stdlib only** at runtime. Dev deps: pytest, ruff.

## Non-goals (for now)

- Editor or Claude Code hook integration (provenance tracking). Later phase.
- Time-based decay. Only commit-based decay in the slice.
- Validating that the debt index predicts outcomes (bug-fix time). Would be a research
  project; not needed for the product slice.
- Language-specific static analysis for importance. Churn plus LLM centrality is the proxy.

## Open questions

- Should `records/` be committed by default, or opt-in per team?
- Threshold for "architectural decision worth an acknowledgement" when we add
  capture-at-generation-time.
- How many questions per session before it feels like homework. Default is 5.
