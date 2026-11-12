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
- **Decay per commit.** Understanding verified at commit X decays with the later commits
  touching the unit's paths. This is what makes debt grow while the AI builds. (First 0.9
  per commit, then per unit of weighted mass; since Phase 1.4 by the share of the unit
  that changed, below.)
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
  Second result on digitalme (snapshots at mass 2.3, 25.7, 50.4; the nearest moved in from
  10, which leaves only 1 of 12 units unchanged): mean score 1.00, 0.46, 0.21; the same
  unit scored worse with more mass in 30 of 31 pairs; within a snapshot rho 0.39 (p 0.10)
  and 0.00. Decay across snapshots, unit attribution not shown; 2 of 72 unknown, 2 of 72
  confidently wrong. No model change made on it (ROADMAP Phase 5).
- **Synthetic experiment, third result** (Phase 1.3, dsa-instructor, same design, 7
  calls, snapshots picked from `--suggest` before any answer: mass 4.5 with 8 of 12 units
  unchanged, 25.2, 50.6): mean score 0.98, 0.94, 0.96; 67 of 72 answers correct, none
  unknown or confidently wrong; within a snapshot rho 0.26, 0.18, 0.58 (p 0.03, on two
  partial answers); same unit with more mass since worse in 4 pairs, better in 4. It does
  not reproduce digitalme's decay with snapshot age; the snapshots are 4 to 11 days old and
  a unit's share since is 0.3%, 3% and 10%. Nothing tuned, no model change (ROADMAP Phase 5).
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
- **History mining from weights, survival and periods** (Phase 1.3): a decision
  candidate's score is its commit weight (a branch commit's standalone weight, LLM class
  blended in) plus +0.3 new top-level dir, +0.2 renames, +0.1 per keyword (max 2), times
  the share of its files alive at HEAD (renamed successors followed; rename pairs cached as
  `mv`), +0.5 for a founding commit after the multiplication. No surviving file drops a
  commit unless it is founding; weight plus bonuses under 0.5 is not a candidate.
  Dependencies and infrastructure count only through the weight, not as a parallel count.
  Four periods of equal weighted mass (`history_periods`) each get 5 of the 40 slots
  (`history_quota`), the rest go to the best scores, ties to the more recent commit. Equal
  mass rather than calendar years, so busy eras get more periods; half the slots spread,
  half competed for. (Phase 1.4 raised the quota to 10: slots proportional to mass.) The output keeps its fields and adds `survival`, `weight`, `period`;
  `udebt history --candidates` prints it without a map or a model call. On
  huggingface_hub the last 12 months go from 1 slot to 25 and the map's history links from
  9 (7 to one release) to 31 on 30 commits, 22 of them recent (TESTBEDS.md).
- **Major upgrades are dependency changes** (owner, Phase 1.3): a pin whose version moves
  to a new major (first number; first two below 1.0; an upper bound that admits a new
  major) weighs +0.3 and stays in line share. Patch and minor upgrades, the project's own
  version lines and pins moved to the version the same diff gives the project stay version
  bumps; calendar versions (first number >= 1000) and non-numeric versions keep the Phase
  1.2 behaviour. `commits.json` v4; the parsed cache versions its `ver` input, so only
  that input is recomputed. Changes 0 commits on digitalme and dsa-instructor, 4 on
  huggingface_hub (ruff and mypy 0.x upgrades).
- **Phase 1.4 (owner-approved model fixes, 2026-11-12)**:
  - **History quota 10 per period** (owner): with 4 periods all 40 slots are guaranteed by
    period, so slots are proportional to mass; `history_periods` and `history_quota` stay
    settings. huggingface_hub: periods 8/5/5/22 -> 10/10/10/10, last 12 months 25 -> 16.
  - **Decay by share changed** (owner): understanding and the prior's erosion fade by
    exp(-k x share), share = weighted changed lines landed under the unit since the evidence
    over the unit's size in lines at the cut-off. "Weighted" is each commit's changed lines
    in the unit (line share's rules: no lockfile, generated or version-bump lines, shared
    files split) times its weight; a commit a merged branch brought in carries the whole
    branch's weight per line (capped at 1), since with its own small part a ten-commit branch
    would count a tenth of the lines its squashed twin counts. Size = lines of the tracked
    files under the unit's paths, the same kinds (no binaries), split the same way; counted
    per blob (`git cat-file --batch`), cached in gitignored `.udebt/sizes.json`, so it is
    "cached per map head" and per any other tree. A unit with no lines left divides by one.
  - **Form and rate fitted** on the 72 unit-snapshots of the two Phase 5 runs, the share
    recomputed under this definition: exp(-k s) k 1.37 (RMSE 0.229), (1 - s)^k k 0.87
    (0.226), 0.9^mass 0.265, best per-mass rate 0.84 (0.244). The two forms fit alike; the
    exponential is kept because it composes (fading over two stretches is fading over their
    sum, which replay needs) and never reaches zero. k = ln 4 = 1.386: understanding halves
    for every half of a unit that changes. Fitted per repository, k is 2.39 on digitalme and
    0.14 on dsa-instructor: the line share does not unify the two as the mass share (mass
    since over the unit's mass) would (RMSE 0.165 at k 2.15); see open questions.
  - The per-mass rule stays as `decay_model: "mass"` (`decay_per_commit` 0.9);
    `share_decay_rate` sets k. Status notes say "decayed from X by share Y (mass Z)".
  - **Synthetic run at larger shares** (owner; dsa-instructor, 5 calls): snapshots at mean
    mass share 0.30 and 0.70 (`--suggest mshare:`, the measure digitalme's range was
    stated in; by line share 0.3 is the 10-31 snapshot already run). Observed 0.85 and
    0.40, the share model predicted 0.40 and 0.11, 0.9^mass 0.57 and 0.33; 12 of 12 units
    worse at the older snapshot. Recorded as is, nothing retuned (ROADMAP Phase 5).
  - **TOML dependency tables** (owner): `[dependencies.serde]` then `version = "2.0"` is a
    pin (Cargo dev/build/target/workspace tables, Poetry's `[tool.poetry.dependencies.x]`),
    read through hunk headers git labels with the enclosing table (an xfuncname in a
    throwaway attributes file). `commits.json` v5, parsed cache `ver` v3. Changes no commit
    on the five testbeds (none uses the table form).

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
- The synthetic rerun suggests mass reaching small units from outside their paths
  (contacts-db wrong at 0.12): attribute some mass beyond a unit's own paths? (The decay
  rate part was answered in Phase 1.4: decay by share.)
- (Answered 2026-11-12, Phase 1.4: decay by the share of a unit changed, and the next
  dsa-instructor run at mass shares 0.3 and 0.7, 5 calls. See Decisions and ROADMAP
  Phase 5.)
- (Answered 2026-11-12, Phase 1.3: a major-version upgrade keeps the dependency weight;
  patch and minor upgrades stay version bumps. See Decisions.)
- (Answered 2026-11-12, Phase 1.4: 10 slots per period, proportional to mass; decay by the
  share of a unit changed; the next dsa-instructor run picks snapshots by share. See
  Decisions.)
- The line share the owner specified (weighted changed lines over size in lines) fits the
  two synthetic runs worse than the mass share does (RMSE 0.229 against 0.165): bulk
  content (curriculum JSON and rendered figures in dsa-instructor's `offline-pipeline`,
  23,800 weighted lines in 9 days, mostly two course additions) and one large commit in a
  small unit (`chroma-integration`, share 0.27 on mass 0.20) read as large shares the
  answers did not show. Keep lines, switch to mass over the unit's mass, or cap a commit's
  lines in a unit at the unit's size? The Phase 1.4 run added a third artifact: deleting
  whole files (raw knowledge-base JSON moved out of git) counts their removed lines against
  a size that no longer has them (`chroma-integration` share 43.9, answers still 0.75).
- One rate does not fit both repositories under either share (per-repository k 2.39 on
  digitalme, 0.14 on dsa-instructor; the Phase 1.4 run under-predicts dsa-instructor's
  scores by 0.3-0.45). Calibrate the rate per repository from its own quizzes once there
  are some, keeping ln 4 as the default?
- The dogfood `.udebt/map.json` here was mapped from the old miner, which found 3
  candidates in this repository; the new one finds 18, those 3 among them, so a
  re-map would link more history. Not re-run, to save the call.
