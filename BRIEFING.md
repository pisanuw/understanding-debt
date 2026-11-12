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

- **Phase 3, time series and the local dashboard (2026-11-12)**:
  - **Snapshots committed, one file**: `.udebt/snapshots.json` (not a `snapshots/`
    directory), compact JSON, one point and one risk event per line, so a commit of it is
    an append. `udebt snapshot` appends only points newer than the last (idempotent); a
    fingerprint (map `generated_at`, a hash of unit ids, paths and centrality, decay model
    and rates, evidence window, interval) or a last point off the first-parent line makes
    the stored series stale, kept with a note until `--rebuild`.
  - **Units replayed as defined now** (owner): the current map's units against the past;
    a unit no commit had touched yet is absent at a point; importance from the churn up to
    the point (the same `set_importance` as `map --rescore`), centrality from today; sets
    and scope as today. Weights are today's too.
  - **The repository at a point** is the first-parent line up to a commit: a branch lands
    with its merge, a point's time is the first-parent commit's author time, kept
    monotone; evidence counts up to that time. Weekly points fall on Mondays 00:00 UTC
    (idle weeks skipped); `day` and `mass:N` are the alternatives; a last point at the
    latest commit or evidence.
  - **Same scoring code**: `compute_scores` takes a `Cutoff`; `status` is the case without
    one. A per-map `Ledger` holds each commit's attribution and lines per unit, so a replay
    costs a filter per score, not a pass over history.
  - **Developers in the series**: human-led footprint of at least 0.1 mass (directed agent
    work at half), or evidence, or a responsibility set; agents and bots are not
    developers.
  - **Risk events from history**: each commit a developer authored or directed, weight
    >= 0.1, assessed by `risk.assess` against their understanding at the step before it
    landed, evidence before its author time; one event per unit at OUTSIDE, HIGH or
    CAUTION. Listings show OUTSIDE and HIGH by default.
  - **Export** (`udebt-export` v1, JSON or CSV tables, gitignored `.udebt/export/`) carries
    no answer text unless `--with-answers` (answer, feedback, reference dropped; question,
    verdict, confidence kept), matching Phase 4's metadata-only storage.
  - **Dashboard**: one self-contained HTML file from the package template
    `udebt/dashboard.html`, data as one JSON blob; the reference categorical palette from
    the dataviz guidance, validated in both modes (three light slots under 3:1 contrast,
    hence direct end labels and the series table). This repository's dashboard is
    committed as the dogfood example, with `snapshot_every: "mass:2"`.
  - Dig records carry `source: "dig"` (older ones are recognised by their reference).

- **Phase 3.1, map paths, decay caps and refit, risk and dashboard polish (2026-11-12)**:
  - **Map paths are resolved, not trusted**: every unit and sub-unit path the model gives is
    kept if it is a tracked file or a directory holding tracked files (after stripping `./`
    and slashes); else replaced by the one tracked file or directory whose path ends in it
    (`hf_api.py` -> `src/huggingface_hub/hf_api.py`); else dropped into the unit's
    `unresolved`. Exact wins over suffix, so a package-relative `utils` resolves to a
    top-level `utils/` when one exists; an ambiguous tail (`__init__.py`, `lfs.py` in two
    places) is dropped rather than guessed. A parent's paths are resolved before its
    sub-units' are merged in. `map.json` records the counts (`paths`).
  - **Retry once, then flag**: under 50% coverage of the clustered non-test source files, or
    a unit left with no path, `udebt map` warns and asks once more with the unresolved paths
    and two real root-relative examples appended (`--no-retry` skips it); the better answer
    (coverage, then fewer empty units, then fewer drops) is kept. A map still short is
    written with `warnings`, which `status` prints before anything else. The prompt now says
    paths are relative to the root and shows two examples taken from the files (the deepest
    directory holding half the source files, a file in it and its largest subdirectory).
  - **A commit changes at most all of a unit**: its changed lines in a unit are capped at
    the unit's size just after it (at least one line). A rewrite counts the unit once, not
    old plus new lines; deleting most of a unit counts what is left. A whole-file deletion
    counts its size once (numstat's deletions). "Just after" rather than "before" because
    the artifact was a deletion: capped at the size before, the 74,700 raw JSON lines
    `eb5a9a3d` removed from dsa-instructor's `chroma-integration` would still count against
    the 1,731 lines left (share 43.9); capped after, the share is 1.32. The size after each
    commit is the running sum of insertions minus deletions under the unit in history order
    (no tree reads); at HEAD it matches the blob count to within 54 lines on every unit of
    the four repositories checked. On a branch it also counts what the main line added
    meanwhile, which the cap tolerates. The parsed-commit cache keeps per-file deletions
    (format 2, one rebuild).
  - **k refitted, pooled: 1.0** (config `decay_k`, which replaces `share_decay_rate`; a
    non-default old value carries over, the old default ln 4 does not). Fitted on all 108
    recorded unit-snapshots (digitalme's two runs, dsa-instructor's two) plus
    huggingface_hub's four summarised runs as aggregate points (mean of 12 units, weight 12,
    shares from today's map at the same snapshot): pooled 1.02 (1.06 without huggingface_hub),
    rounded to 1.0; per repository 2.72, 0.42 and 0.93. The cap moves dsa-instructor's fit
    (RMSE 0.191 to 0.165) and nothing else. A floor, floor + (1 - floor) x exp(-k s), fits
    pooled at floor 0.35, k 2.39, RMSE 0.241 against 0.251: not adopted, because per
    repository it runs to a step (k at the grid edge, 6) onto a plateau whose level is within
    question-set variance (0.75 and 0.58 on one snapshot), the answerer is an LLM that reads
    its snapshot in full, and a floor of a third would keep a unit rewritten many times a
    third understood. It is there as config `decay_floor` (default 0). The mass share still
    fits better (pooled RMSE 0.193 at k 1.75; rho with wrongness 0.79 against 0.66).
  - **CAUTION on the prior alone is muted**: a CAUTION whose understanding is only the
    own-commit prior is reported OK and `muted`; the replay leaves it out of the risk events
    and `udebt risk` names it in one line ("Not flagged: ... only the own-commit prior").
    HIGH and OUTSIDE still fire on a prior; a unit with no understanding at all can still be
    CAUTION. Events: digitalme 312 to 12, dsa-instructor 240 to 144. The replay fingerprint
    carries a rules version (2), so series made before go stale.
  - **Dashboard lines capped at eight**: the developers with the highest debt index at the
    latest point (ties to more commits), plus one dashed line for the mean of the rest; the
    Developer selector groups "On the chart" and "Swap onto the chart", and picking one of the
    rest swaps them into the eighth slot (later picks reuse that slot). The page opens on the
    developer with most commits, swapped in if needed. The series table follows the lines.
    Unit colours in the breakdown reuse the palette from the start when eight developer
    lines leave fewer than three free slots.

- **Phase 3.2, the question ladder (2026-11-12)**: the first real quiz on digitalme (10
  answers, none correct) asked only implementation questions; the owner wanted the
  architecture first. The structure changed, not the wording:
  - **Five levels** (owner-approved): 1 purpose and placement, 2 decision and alternatives,
    3 consequence across units, 4 behaviour under failure, 5 implementation (the old
    questions). Levels 1-3 name no function, variable, file or code block of the repository;
    they may name an external technology the decision is about (a database, a host, a model
    provider), since a decision is often a choice between products. Level 4 may name files or
    components. The grader is told the level: at 1-3 a plain-language answer is not marked
    down for lacking identifiers, and partial means the role or decision is right but a
    dependency or consequence is missing.
  - **Progression is derived from the records**, each storing `level` (`forced: true` under
    `quiz --level N`): next = depth + 1, depth being the highest n with levels 1..n passed by
    their latest sitting (one run's answers at that level; all must be correct). Partial,
    wrong and unknown repeat a level; "wrong at n >= 2 may drop to n - 1" is read as: a
    *confidently* wrong answer at n >= 2 asks n - 1 first (a misconception about a decision
    puts the unit's role in doubt), a plain wrong repeats n. Depth-based progression makes
    records from before the ladder (level 5) and forced answers harmless: neither can skip a
    level not shown, and a forced level 1 pass is still a pass.
  - **Score**: v_n = mean evidence of level n's latest sitting; verified = max(0, (v1 + v2 +
    v3) / 3 + 0.1 x (v4 + v5)), up to 1.2; understanding = min(1, verified x fade). The cap is
    after the fade on purpose: capped before it, depth could never count on the ladder
    (level 4 needs 1-3 passed, already 1.0), so "lift above the threshold, never required"
    is read as headroom: a unit known to level 5 stays at 1.0 until 18% of it changed, and a
    faded understanding can be lifted back over 0.6. Confidently wrong is -0.5 at its level.
    The decay machinery and the single fade from the last answer are as before.
  - **The evidence window** no longer applies on the ladder (each level's latest sitting
    counts; a window over the unit's last 5 answers would drop levels 1-3 after five level 5
    answers). It stays for config `evidence_model: "flat"`, the old mean, kept to compare.
  - **Pre-ladder records** are level 5: depth evidence only (0.1 at most), so a unit answered
    only before the ladder reads near zero until levels 1-3 are answered. No testbed here has
    any; the owner's 10 local answers on digitalme would read that way.
  - **Orientation**: before a unit's level 1 question, for a developer with no level 1-3
    answer on it (pre-ladder answers do not count, they were about code): purpose (without
    the dependencies the question asks for), decision with alternatives and why, two or three
    paths (checked against the tracked files), who shaped it (written by the model from the
    unit's `history`). Cached on the unit in map.json with the `basis` it was written from
    (a hash of the unit's fields), so a re-map that changes the unit rewrites it and one that
    does not keeps it; one call per quiz run for all missing ones, before the questions call,
    and the question writer sees it. `udebt dig` opens with it and asks its check question at
    the developer's ladder level.
  - `udebt quiz --preview` prints orientations, questions and reference answers as Markdown,
    records nothing (orientations are still cached), and runs `ladder.identifiers_in`, a
    heuristic for code-like tokens, over the level 1-3 questions. It made the digitalme
    sample, `.udebt/samples/digitalme-ladder-quiz.md` (2 calls, 38 s).
  - The synthetic experiment asks at one explicit level (`--level`, default 3), recorded in
    its report (version 3). The replay fingerprint adds the evidence model; rules version 3.

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
- (Partly answered in Phase 3.1: lines kept, each commit capped at its unit's size, k refit
  to 1.0. See Decisions. The rest of this question stands.) The line share the owner
  specified (weighted changed lines over size in lines) fits the
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
- (Answered in Phase 3.1: CAUTION from the prior alone is muted, HIGH and OUTSIDE kept.)
  Replayed risk events flood with CAUTION for a sole developer: the own-commit prior stops
  at 0.5, under the 0.6 threshold, so every commit of theirs with mass >= 0.1 is CAUTION
  (308 of 312 events on digitalme). Should the replay (and `risk`) leave out CAUTION
  where the understanding is only the prior, or should the threshold apply to quizzed
  understanding only?
- Phase 3's exit check needs a quiz on digitalme to show "a quiz as a visible drop"; there
  are no quiz records on any testbed yet. The June refactor shows as the largest weekly
  step after the first (51.8 to 56.3), small because the sole author's own assisted
  commit only halves his share.
- (Answered in Phase 3.1 for the chart: eight lines by current debt plus "others", with a
  swap selector.) Replay keeps every developer with 0.1 of footprint; a large external team
  (prefect, go-git) would give hundreds of lines. The replay itself still scores all of them:
  huggingface_hub has 378 such developers, 1.7 s per point.
- The dogfood `.udebt/map.json` here was mapped from the old miner, which found 3
  candidates in this repository; the new one finds 18, those 3 among them, so a
  re-map would link more history. Not re-run, to save the call.
- The cap leaves one large share: at dsa-instructor's `f13a9cf`, `chroma-integration` reads
  9.7, because while the unit held 77,000 lines of raw JSON several re-ingests changed tens
  of thousands of them, each under the cap, and all count against the 1,731 lines left.
  Summing each commit's lines over the unit's size at that commit (a per-commit fraction,
  min 1) instead of over the size at the cut-off would read 1.6 there and fits a little
  better pooled (RMSE 0.275 at k 0.79 against 0.282); it changes the share's definition, so
  it is left for the owner.
- On a large team, the eight developers with the most debt are the least involved ones
  (huggingface_hub: those who touched little, at index ~100). Rank the chart by footprint,
  or by debt within each developer's set, instead?
- k differs by repository more than between forms (2.72 digitalme, 0.42 dsa-instructor,
  0.93 huggingface_hub): calibrate it per repository from its own quizzes once there are
  some, with 1.0 as the default?
- Ladder (Phase 3.2): should decay move a developer back down? Today the ladder moves only on
  answers, so a unit rewritten since levels 1-3 were passed still asks level 4 next, though its
  understanding has faded. Re-ask the highest passed level once its faded value is under the
  threshold?
- Ladder: only a confidently wrong answer drops a level; should two plain wrong answers in a
  row at a level n >= 2 drop one too?
- Ladder: passing levels 1 and 2 is 0.67, over the 0.6 known threshold, so a holder need not
  have shown level 3. Raise the threshold (0.7) so a holder has shown all three?
- Orientation leak: on a flow unit the orientation's decision lists the steps of the flow, and
  the digitalme sample's chat-flow level 1 question (what it relies on before and after
  generation) is half answered by it. Keep orientations to the choice and its alternatives,
  or accept that a first level 1 answer partly measures reading?
- Pre-ladder answers count as level 5 (as the brief said), so the owner's 10 local digitalme
  answers give those units almost nothing. Ignore them, or count them as they were (flat) until
  the unit has a level 1-3 answer?
- Level 1-3 questions may name external technologies (PostgreSQL, Supabase, the model
  provider). Confirm, or forbid those too?
- A parent's depth is its own answers only; sub-unit quizzes roll up understanding but not
  depth. Show the sub-units' minimum depth on the parent?
