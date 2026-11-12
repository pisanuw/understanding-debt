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
- **Four commit classes**: `ai` (agent-authored), `assisted` (human author, AI co-author
  or attribution trailer), `human`, and `bot` (CI, release, dependency and pipeline
  automation). Signatures are data in config (`ai_signatures`, `bot_signatures`) and match
  case-insensitively. **Every agent counts** (owner, 2026-11-12): Claude was the starting
  point, but Copilot's coding agent, Cursor, Codex, Devin, OpenCode and `Assisted-by:`
  trailers carry large shares in real repos (TESTBEDS.md).
- **Weak prior from own commits**, capped at 0.5 (owner, second round: keep the cap), AI-
  assisted commits at half weight, decayed by every commit someone else landed since.
  Replaced by any quiz evidence.
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
  per developer keeps merges conflict-free. The one exception is the parsed-commit cache.
- **Parsed-commit cache** (Phase 1.2): `.udebt/commits-parsed.json` (gzipped as
  `.json.gz` above 4 MB) holds per sha what `git log --numstat` gives (author, date,
  subject, body, parents, per-file lines) and the weighing inputs (effective lines, added
  files, renames, version-bump files), each filled the first time the weights need it.
  `git rev-list HEAD` decides what is used: new shas are parsed with `git log --no-walk
  --stdin`, unreachable ones are dropped. Classification and directed work are recomputed
  each load. A `.mailmap` change or a format change rebuilds it. **Gitignored**, through
  `.udebt/.gitignore` written by `init` and by the cache itself: it is derived, any command
  rebuilds it, and it grows with history (3.6 MB plain for 2510 commits; roughly 85 MB
  plain, 20-25 MB gzipped, extrapolated for servo's 59k). `commits.json` stays committed
  because it holds the LLM refinements nobody wants to pay for twice. The cache sits next
  to the committed files rather than in `.git/` so that `udebt` never writes inside git's
  own directory and the cache is easy to find and delete.
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

- **Phase 1.1 classifier (2026-11-12)**: attribution trailers (`Assisted-by:`,
  `Generated-by:`, `Made-with:`) count when the value names an AI, positively: a known agent
  name, a vendor or model family, or the agentic-commits `(provider/model)` form. The agent
  named first wins; an AI no signature knows maps to the generic trailer key
  (`assisted-by`). A value naming a person does not count, so `Assisted-by: pi` (an obscure
  agent) is missed on purpose rather than risk counting human helpers.
- **Bots land no mass** (owner): a `bot` commit weighs 0 wherever mass is summed (one helper,
  `provenance.landed`), so it is out of churn, decay, the prior, risk, co-change, footprint
  and every share's denominator; its commits are still counted for display. Bot identities
  are one source of truth: `contributors` labels Bot exactly the identities whose commits are
  classed `bot`. A bot-authored commit carrying an agent trailer is `ai`. The name heuristics
  are the old contributor ones (`bot`, `pipeline`, `automation`, `actions` as whole words),
  so digitalme's `YPai pipeline <bot@...>` commit is now `bot` (see open questions); an
  `ai_signatures` entry claims such an identity back because agent signatures win.
- **Directed agent work** (owner): config `directed` maps a developer to `Claude-Session:`
  session ids and, wholesale, agent names (`udebt directed add`). A matched `ai` commit stays
  `ai` for display and AI share; in `unit_provenance` and footprint seeding it is that
  developer's assisted work at half weight, and it resets their prior's decay like an own
  commit. Session claims win over agent claims; among agent claims the first wins. A
  developer who only directed agents is a human for seeding. This repo claims its two
  sessions, so its dogfood set is no longer empty.
- **Shared files are split** (owner): a file k top-level units list gives each 1/k of its
  lines, and a parent's part is split again among its sub-units that list it, so a sub-unit
  never gets more of a file than its parent. One `units.Membership` per map feeds share,
  attribution, churn, provenance, decay, risk and footprint. Blackbox units still count as
  listing a file.
- **Squash bias fixed by grouping** (owner): a merge's branch commits (the first-parent
  chain of its other parents, back to merged history) are weighed once as a squashed commit
  and share that weight by effective lines outside lockfiles. Innermost merge wins, so a pull
  request merged into a long-lived branch is its own group. Branches over
  `group_max_commits` (50) stay ungrouped: a 67-commit review branch on dsa-instructor is a
  line of work, not one change. Merging main into a branch adds nothing. Total mass drops
  17% on digitalme, 45% on dsa-instructor. A recompute re-blends LLM refinements on the new
  heuristic. Foxtrot merges (`git pull` merges on the main line) would group a teammate's
  pushed commits as one; neither local testbed has a pull of main on its main line.
- **Synthetic-developer experiment** (`udebt experiment synthetic`): three calls (questions
  on HEAD, answers on the snapshot, grades on HEAD), all top-level architectural units by
  importance unless `--units N`, one question each. The snapshot worktree loses its `.git`
  file so the answerer cannot follow it back to HEAD, and the prompt does not hint that the
  code moved on. Mass since is over `rev..HEAD` (ancestry, not dates). Nothing goes to
  `records/`; the report is `experiments/synthetic-<sha8>-<Lisbon date>.json` and names
  the resolved sha, since `<rev>` may contain `~`. First result on digitalme: rho -0.27, no
  support for decay; recorded as is (ROADMAP Phase 5).
- **Synthetic experiment, second design** (Phase 1.2): several snapshots in one run share
  one question set written from HEAD, `--per-unit` (default 2) questions per unit, the
  first on the decision and the second on current behaviour, from a prompt
  (`TASK: stable-questions`) that shows no history and forbids "what changed" questions.
  Score is correct 1, partial 0.5, wrong and unknown 0 (udebt's -0.5 for confident wrong is
  reported beside it). A unit's change since a snapshot is `unchanged` (mass < 0.005),
  `mostly-edited` (>= half its changed lines in files the snapshot had) or `mostly-new`.
  The nearest snapshot should leave at least a third of the units unchanged, as a control;
  `--suggest` lists candidates. Reports are saved after every call and `--resume` finishes
  one, because a run has a fixed call budget.
- **Version bumps** (Phase 1.2): a manifest or version file whose diff changes only version
  strings is a `version` kind for that commit: no +0.3 for dependencies, no directory spread,
  out of line share; a commit of only version bumps (plus docs, lockfiles, generated files)
  weighs 0.03. A changed line counts when it sets a version or is a pin whose old and new
  forms differ only in version strings, so a dependency *upgrade* (`playwright==1.56.0` to
  `1.61.0`) is a version bump too; adding, removing or swapping a dependency is not. This
  goes past the four own-version patterns the owner named, because a monorepo release
  rewrites every package's internal pins along with its version (see open questions). A
  version needs `major.minor`. `__version__` in a package's `__init__.py` is not read (only
  named version files are), to keep the diff pass to manifests.
- **Team view without quizzes** (owner): when no developer has a quiz record, it says
  "no quiz evidence yet; holders need a quiz score >= threshold" and labels the holder
  column `quizzed`.

## Non-goals (for now)

- Editor or Claude Code hook integration (provenance tracking). Later phase.
- Time-based decay. Only commit-based decay in the slice.
- Blocking merges. Every signal is advisory unless a team opts in (ROADMAP).
- Storing source code in the hosted service. Metadata only (ROADMAP Phase 4).

## Open questions

- Threshold for "architectural decision worth an acknowledgement" when we add
  capture-at-generation-time.
- (Answered 2026-11-12, second round: directed agent work, keep the prior cap, split shared
  files. See Decisions.)
- digitalme's one `YPai pipeline <bot@ypai.pisan.me>` commit (18aa4c2, the content pipeline
  itself, merged as PR #1) is now `bot` and lands no mass. If an agent session wrote it under
  that identity, claim it with an `ai_signatures` entry or `udebt directed`; should the
  `pipeline` name heuristic stay?
- vscode's `on behalf of @user` footer names the directing developer; it could fill
  `directed` automatically from `handles`.
- Dependency upgrades (same package, new version) now weigh like version bumps. Should a
  major-version upgrade (`pydantic>=1` to `>=2`) with no code change keep some weight? On
  prefect most such commits are dependabot's (already bots); on go-git several are human.
