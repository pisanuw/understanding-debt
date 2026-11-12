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
  trailer), `human`. Signatures are data in config. **Every agent counts** (owner,
  2026-11-12): Claude was the starting point, but Copilot's coding agent, Cursor, Codex,
  Devin, OpenCode and `Assisted-by:` trailers carry large shares in real repos (TESTBEDS.md).
- **Weak prior from own commits**, capped at 0.5, AI-assisted commits at half weight,
  decayed by every commit someone else landed since. Replaced by any quiz evidence.
- **Identity aliases** in config (`identities`) so a developer's several emails count as one.
- **Testbeds**: `pisanuw/digitalme` (190 commits, 126 human / 62 assisted / 2 ai) is the
  primary; `pisanuw/dsa-instructor` (2510 commits, mostly ai) is the stress test; the small
  all-human repos are controls. **External testbeds** in TESTBEDS.md and `testbeds.json`
  (pinned shas): start with huggingface_hub, sentry-javascript and mlflow, with servo as an
  AI-banned control. No contacts on those teams, so validation there uses outcome proxies
  (reverts) on the large-team tier.
- **LLM via the `claude` CLI**, subscription-billed, read-only tools (Read, Glob, Grep),
  structured output via `--json-schema`. No API key. `UDEBT_LLM=fake` for offline tests.
- **Storage is JSON under `.udebt/`**, all of it committed to the repo by default:
  `map.json` shared, `records/<email-slug>.jsonl` per developer, append-only. One file
  per developer keeps merges conflict-free.
- **Five questions per quiz session** by default. Short enough to run often.
- **Python, stdlib only** at runtime. Dev deps: pytest, ruff.

- **Roadmap** in ROADMAP.md: contributors and commit weight, conceptual units and
  responsibility sets, time series and a local dashboard, then hosted (Netlify, Render,
  Supabase) with the CLI as the local twin, then validity. Authorship stays a decaying
  prior that may be dropped (Wheeler 2026, "Substrate Collapse").

- **Owner answers 2026-11-12**: hosted uses an org API key; repo owners assign
  visibility roles; gating advisory with opt-in soft block, never hard; responsibility sets
  seeded then lead-curated; refine the heaviest 40 commits; metadata-only AI heuristics;
  languages Python, JS/TS, C++, Rust, Java; live dashboard with sync and export.

- **Commit weight (Phase 1)**: heuristic weight in [0, 1] per commit, LLM-refined for the
  heaviest, cached in `.udebt/commits.json`; split across units by line share. Every former
  commit count (churn, decay, provenance, prior) is now weighted mass.
- **Risk is min over units**, advisory; `udebt risk` and a warn-only pre-push hook.
- **Contributor labels**: explicit (AI, Bot, Joint) feed nothing but display; "most likely"
  labels are metadata-only and advisory.

- **Structural discovery (Phase 2)**: candidate units come from an import graph (Python by
  `ast`, JS/TS by regex; one registered function per language, C++/Rust/Java next) merged
  with a co-change graph (weight / (files - 1) per commit, commits over 30 files ignored),
  partitioned by a seeded stdlib Louvain, sub-clusters for 8+ files. Only source files are
  clustered; docs and config attach afterwards so journals do not glue code together. The
  LLM names, merges and splits clusters and adds cross-cutting decisions. No tree-sitter, no
  networkx: runtime stays stdlib only.
- **Two levels are enough**: map v3 gives top-level units up to six sub-units
  (`parent/child`). Sub-units split the parent's importance; a parent quiz gives sub-units
  half; sub-unit quizzes roll up to the parent (priors do not, they share the same commits).
  Default views are top level; `--deep` or a unit argument goes down. v1/v2 maps still load.
- **Responsibility sets** in `.udebt/responsibility.json` (committed): per developer, from
  CODEOWNERS when it names them (an owner of >= 30% of a unit's files), else from their
  share of the human-led work (>= 10% and >= 0.1 mass). AI-authored commits count for
  nobody: a unit only agents touched is in nobody's set until a lead assigns it. Unknown
  handles stay pending with a null slot in config `handles`. A lead's add/remove marks the
  set curated; a plain re-seed leaves it alone, `--force` re-seeds and keeps manual entries.
- **Your index is over your set** plus the units you are touching now (uncommitted or
  unpushed changes); with no set, over every unit as before.
- **Scope is per developer by default**; `--team` writes the map. A developer's own choice
  beats the team's in both directions.
- **OUTSIDE is the strongest risk level**: real weight (>= 0.1) in a unit outside your set
  that you understand below the known threshold. `--strict` exits 1 on it as on HIGH.
- **Bus target per unit** (config `bus_target`, default 2): the team view lists every
  architectural unit with fewer holders above the known threshold, and who is responsible.
- **Line share ignores lockfile and generated lines**, so a lockfile bump inside a refactor
  does not steer the refactor's weight to the frontend.

- **Owner answers 2026-11-12 (second round)**: merge the testbeds research; Phase 1.1 fixes
  the classifier gaps (Assisted-by, Devin, claude[bot], OpenCode, Jules, Codex connector,
  Amazon Q, Generated-by, Made-with; case-insensitive emails; a bot class excluded from human
  share and decay) and the squash bias (group branch commits by PR, size once); validation by
  synthetic developers (the `claude` CLI quizzed from an older snapshot) and reverts on the
  large-team testbeds, first experiment right after 1.1. Times are reported in Lisbon time.

## Non-goals (for now)

- Editor or Claude Code hook integration (provenance tracking). Later phase.
- Time-based decay. Only commit-based decay in the slice.
- Blocking merges. Every signal is advisory unless a team opts in (ROADMAP).
- Storing source code in the hosted service. Metadata only (ROADMAP Phase 4).

## Open questions

- Threshold for "architectural decision worth an acknowledgement" when we add
  capture-at-generation-time.
- Agent commits a human directed (cloud sessions authored as the agent) count for nobody's
  footprint, so on dsa-instructor two units built only by agent sessions are in nobody's set
  and this repo seeds no set at all. Should config map an agent identity, or a
  `Claude-Session` trailer, to the human who directed it?
- The own-commit prior is capped at 0.5, below the 0.6 known threshold, so no unit has a
  holder until someone takes a quiz and the bus target reads 0 everywhere at first. Intended?
- A file listed under several units (digitalme's `backend/main.py` is in four) lands its full
  line share in each, which inflates the mass of hub files.
