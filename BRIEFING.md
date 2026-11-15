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
  that changed, below; since Phase 3.5 at a rate per ladder level.)
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

- **Owner answers 2026-11-15 (hosting, HOSTING.md)**: these supersede the org API key above.
  - The UDebt session builds the hosted app on the HintWise platform at
    `udebt.hintwise.org`, without renaming the repository.
  - Google and email sign-in go through HintWise Supabase Auth.
  - Samples are precomputed on the subscription; quizzes go through the HintWise LLM
    gateway; a new repository needs the person's own key.
  - The worker runs on a Render free instance, with gateway limits set after measuring the
    samples.
  - Only public repositories are accepted, and analyses of them are shared.
  - The samples are huggingface_hub, sentry-javascript and mlflow, with no stored code.

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
  `records/`; the report is `experiments/synthetic-<sha8>-<Lisbon date>-<HHMMSS>.json` (the
  time since Phase 3.4) and names
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

- **Phase 3.3, the teaching layer (2026-11-14)**: the owner took the level 1 quiz on
  digitalme and answered none of five: the questions were compound inventories, an "I don't
  know" taught nothing and the orientation did not show (fixed first, `099a7fc`). "The system
  is currently good at quizzing the user, but leaves the programmer frustrated." Owner
  decisions: level 1 is free text; the teaching surface is a local server in the browser;
  decision records may be written into the repository, opt-in and flagged as possibly
  sensitive; a learning session is ten minutes; an interactive tutor through the `claude` CLI
  is acceptable; a revealed miss is ordinary unknown or wrong evidence (a correct variant at
  the next sitting passes the level); the server's generation endpoints may be POSTs that only
  fill the caches in map.json.
  - **One question, one fact**: a question with more than one question mark, a second
    question joined by "and what/which/how/why/..." or "; ", a second sentence after the
    question mark, or (levels 1-3) an inventory ("list", "enumerate", "which other parts",
    "name the ...") is compound. The quiz schema asks for two `variants` with each question;
    the first phrasing that asks one fact is kept, else the first clause, so validation costs
    no call. The synthetic experiment's stable questions are left as they were, so its runs
    stay comparable with Phase 1-3 runs. The orientation rule that asked for "which other parts
    rely on it" is replaced by one consequence (what a user would notice, why it runs where it
    does).
  - **Reveal at levels 1-2 only**: those are the purpose and the decision, which a developer
    cannot work out alone; at level 3 and up a miss gets feedback, not the answer, so the
    consequence and failure questions still measure reasoning. Partial answers are not revealed
    (their feedback names what was missing). A reveal changes no score.
  - **Basis is derived, not stored**: `measured` for quiz and children sources, `estimated` for
    prior and parent, `none`; the export derives it from each point's existing `quizzed`, so
    `snapshots.json` keeps its format and fingerprint.
  - **Run timestamps are strictly increasing per developer**: two runs in the same second
    (a quiz then an immediate review) would otherwise share a timestamp and read as one
    sitting; `quiz.record` moves a run one second past the developer's latest record.
  - **The unit graph splits shared files**: an import from a file k units list counts 1/k for
    each (and 1/k for the imported side), and only edges of at least 0.5 order anything. On
    digitalme `backend/main.py` is listed by five units; counted whole, every unit used every
    other.
  - **Learning order is prerequisites first, by priority**: units taken by importance x debt
    (the tour, being shared, uses importance), each placed after the units it uses,
    recursively; a cycle is broken where the walk meets it. Kahn's algorithm, as the handoff
    planned, put digitalme's two least important isolated units first (nothing held them
    back) and, in its cycles, the chat flow third, though it uses four units.
  - **The tour's order and connections are computed, not written by the model**; the model
    writes the overview and one paragraph per unit. Cached in map.json with a basis of each
    unit's orientation basis, the order and the uses edges.
  - **Walkthrough cache by file contents**: valid while the unit's map entry and a hash of its
    tracked files' contents (working tree) are unchanged. The handoff's `git diff --name-only
    <head>` would discard a walkthrough written over uncommitted changes as soon as they were
    committed. Excerpts read tracked files only (no `..`, no absolute path, nothing ignored),
    at most 25 lines; a step naming an untracked file is dropped.
  - **Review is SM-2 with the handoff's 1 and 3 days**, starting only on a miss; quality from
    the verdict and the confidence. It asks a stored phrasing not asked last time, with the
    stored reference: no generation call. Review, dig, teach-back and tutor answers are all
    ordinary evidence (`source` says which), so a correct review passes its level.
  - **Teach-back is level 2**, whatever the developer's place on the ladder: a level 2 pass
    without level 1 adds to the score but not to depth, as a forced answer does.
  - **Decision records**: no model call, from the map; `udebt decisions` alone writes nothing;
    `write` remembers `decisions_dir` in config and from then on a level 2 pass rewrites only
    the Last confirmed by line, so hand edits survive.
  - **`udebt serve`** (Part 2): stdlib `ThreadingHTTPServer`, hand-written page as package
    data. The token travels as `?t=` for pages and an `X-Udebt-Token` header for the API (a
    header, so a cross-origin page cannot forge a call); a Host check stops DNS rebinding.
    The dashboard tab is the Phase 3 page in an iframe, fetching `/api/dashboard` when its
    blob is `null`; a stale stored series is replayed in memory rather than shown stale or
    rewritten. The browser grades one answer per call (immediate feedback), so each answer is
    its own sitting; the CLI still grades a run in one call.
  - **Quiz questions are pooled** in map.json per unit and level (at most six, with the
    unit's basis), by the CLI and the server; the server asks a revealed question's stored
    variant first, then a pooled question this developer has not answered, then the model.
    The CLI quiz still asks the model every run and tells it what was revealed.
  - **The tutor** is one `claude` CLI call per turn with the handoff's flags; after four
    developer turns or "check me" the server adds an instruction to propose the check
    question, so the timing does not depend on the model. Its two-line format (`CHECK
    QUESTION:`, `REFERENCE:`) is parsed and the reference never reaches the page. udebt keeps
    the turns in memory only (lost on restart; the CLI's session store keeps the transcript);
    only the graded check answer is recorded. The check question is at the developer's next
    ladder level for the unit, fixed when the session starts.
  - **Cached generations live in the committed map.json** (orientations already did): the
    tour, walkthroughs with their check questions and reference answers, pooled questions. A teammate can read
    an answer before being asked; the quiz measures understanding, and reading the answer is
    learning, so this is accepted rather than hidden.

- **Phase 3.4, owner-accepted polish and change digests (2026-11-15)**: the owner accepted
  the seven Phase 3.3 recommendations; digests are new.
  - **A partial answer at levels 1-2 is revealed** like an unknown or a wrong one, after
    grading (`quiz.REVEALED_VERDICTS`); the CLI quiz, review, dig, teach-back and the browser.
  - **Teach-back is graded at the developer's next ladder level** for the unit (level 1 with
    no evidence), not always 2: the explanation asked for grows with the level, the grader is
    given per-level meanings of correct and partial, and a pass moves the ladder (before, a
    level 2 pass without level 1 added to the score but not to depth).
  - **Dig rotates its check**: a stored variant of a missed question, else the walkthrough's
    own check, else an unasked pooled question at that level, else the least recently asked,
    which triggers one call for a new one (`dig.fresh_check`, told what was asked). A
    walkthrough's checks join the unit's pool when written, so the quiz can use them too.
    Writing a new question when the pool is exhausted goes slightly past "draws from the
    pool": with one walkthrough check per level the pool alone would repeat on the second dig.
  - **Tutor sessions persist** in `.udebt/tutor/<dev-slug>/<unit>.json` (gitignored), one per
    developer and unit, saved after every turn and check answer; a restart reattaches by id;
    a start on a unit with a saved conversation gets a 409 with it, and the page offers
    resume or start over (start over replaces the file; the CLI store keeps the old
    transcript). The timer counts epoch seconds and restarts after 30 minutes idle.
  - **Batch grading in the browser**: `/api/answers` grades a sitting's answers in one call
    and records them with one timestamp per (source, forced, kind), so one quiz run is one
    sitting, as in the CLI. `/api/dontknow` holds a blank answer and returns the reference at
    levels 1-2 with no call; the question cannot then be answered with text. Unanswered
    questions at "Finish now" are dropped unrecorded. `/api/answer` (one at a time) stays for
    the tutor's check.
  - **Reference answers stay in the committed map.json**, documented in README: variants and
    grading against the code make memorising a reference worth little, and reading it is
    learning.
  - **Hub files connect nothing**: a file more than two top-level units list is left out of
    both the import and the co-change connections (`unitgraph.MAX_OWNERS`), never out of mass.
    digitalme: `backend/main.py` and `frontend/netlify.toml`; uses edges >= 0.5 30 -> 20, all
    uses edges 47 -> 30, co-change pairs 64 -> 59; the learning order now starts with auth and
    cost control and puts the chat flow fourth (was eighth). Its tour went stale and was
    rewritten (one call).
  - **Change digests** (`udebt digest`, Digest tab): per unit below the threshold, the range
    starts at the latest evidence head on the unit, else `--since` (a revision, else a date:
    the last first-parent commit at or before it), else 30 days before HEAD's commit date
    (`--days`): "since the first commit" would be the whole unit, which the orientation and
    the tour cover. The commit list is local; one `TASK: digest` call writes all missing
    briefings with the range's diff stat and first 160 diff lines (lockfiles and generated
    files out) and the heaviest 15 commits per unit; `held` / `extended` / `changed`. Cached
    per (unit, start, end) in `.udebt/digests/` (gitignored), invalid when the unit's map entry
    changes (its basis). `risk` prints "Read the digest: `udebt digest --unit X`" for up to
    three flagged units. digitalme, `--since 2026-07-01`: 11 units, 66 commits, one call of
    32 s (94K characters), 6 held, 5 extended.
  - **Synthetic reports never overwrite** (owner): `-<HHMMSS>` in the name, a counter after
    that, the first write exclusive. **No leakage** (owner): the experiment's question,
    answer and grading calls each read a temporary worktree without `.udebt/` (HEAD for
    questions and grades), recorded as `clean_worktree: true`; every other read-tools call is
    denied `.udebt/` (`--disallowedTools "Read(./.udebt/**)"`) and told so in the prompt, not
    moved to a worktree, because the quiz asks about the working tree as it is, uncommitted
    changes included. The deny rule's form is Claude Code's documented one; no call was spent
    to see the CLI accept it (the tests check the command line).

- **Phase 4a, the platform-agnostic hosted core (2026-11-15)**: the hosting target moved to the
  HintWise platform mid-phase (HOSTING.md on `claude/jolly-hamilton-pvos83`: Supabase Auth
  with Google and email sign-in, Netlify Functions, a Python worker on Render, the HintWise
  LLM gateway or the user's own key, schema `app_udebt` with own-row RLS). 4a built only what
  any host needs; the hosted API server, the SPA's login and data-layer refactor, Render and
  Netlify configs and the local end-to-end through a hosted API were dropped.
  - **Contract version 1** (`contract.py`): a stdlib validator over a JSON-Schema subset; open
    objects everywhere, so fields are added without a version and only removing or retyping
    one bumps it; a newer `contract` in a payload is refused. The export is the import format
    (plus an optional sanitized map) and carries the series fingerprint, so a host can tell a
    rebuild from an append. The sanitized map keeps descriptions, paths, history,
    orientations and the tour; it drops walkthroughs, pooled questions (reference answers),
    provenance and churn.
  - **The worker** writes contract files to a directory, not to a database: the same output
    serves an admin load (`udebt worker sql`), a platform function, or a person. Limits are
    the brief's recommendation (20,000 commits, 500 MB), checked after the clone (git cannot
    tell a remote's size first), with a clone timeout. A map comes from the repository, a file
    or, with `--llm`, the configured model; the model steps on the gateway are 4b.
    Walkthrough excerpts (code lines) go into `specs.json` by default because v1 is public
    repositories only and grading needs them; `--no-excerpts` for anything else.
  - **Schema**: shared `repositories`/`analyses` (a sample's analysis has `user_id` null:
    readable by all, written by no app code, which matches "apps never get the service-role
    key"), a person's rows own-row only, `unique nulls not distinct (repo_id, sha, user_id)`;
    roles designed in comments only. No answer text is stored. The SQL loader replaces an
    analysis by delete and insert in one transaction rather than an upsert, so the same script
    is idempotent in SQLite, whose unique constraints treat nulls as distinct.
  - **Tests on Postgres use psql**, not a driver, so the core keeps no runtime or test
    dependency beyond pytest; a stub `auth` schema (`auth.users`, `auth.uid()` reading the JWT
    claims as Supabase does) stands in for Supabase's.
  - **Push and pull**: a two-endpoint protocol (`POST <url>/import`, `GET <url>/pull`) with a
    bearer token and an `X-Udebt-Contract` header; the token lives in `UDEBT_TOKEN` or a
    0600 credentials file keyed by URL, read from stdin so it never sits in shell history, and
    the URL in gitignored `.udebt/remote.json`. Evidence is keyed by developer, time, unit,
    level and question (level added because one sitting's records may share time, unit and
    question text in synthetic data).
  - **Check runs** stay advisory unless the repository opts into soft blocking; an
    acknowledgement applies to the head commit the last check ran on (`*` when none ran yet),
    so a new push asks again. The webhook router is pure: the caller says who is a lead.
  - **The first hosted-API draft** (Starlette, GitHub OAuth, orgs, the four roles in RLS) is
    kept, untested, outside the package in `drafts/hosted-api/`, as the coordinator asked for
    already-built skipped work.

- **Phase 3.5, decay per ladder level (2026-11-15)**: the huggingface_hub level 3 and level 5
  runs at one snapshot showed the architecture fading far more slowly than the code (0.88
  against 0.69 where k = 1.0 predicted 0.62 for both; the gap tracks the share changed, rho
  0.80), and the ladder score weights levels 1-3 most.
  - **Each level fades at its own rate before the levels are combined**: understanding =
    min(1, max(0, (v_1 f_1 + v_2 f_2 + v_3 f_3) / 3 + 0.1 (v_4 f_4 + v_5 f_5))), f_n =
    exp(-k_n x share since the unit's last answer). With equal rates this is exactly the
    Phase 3.2 rule (verified x fade). A negative level value (confidently wrong) fades
    towards 0 like a pass; the floor of 0 applies after combining.
  - **Defaults k 0.2 at levels 1-3, 1.0 at levels 4-5** (config `decay_k_by_level`), from
    the fits in ROADMAP Phase 5: level 3 0.20 (RMSE 0.212, bootstrap 95% 0.13-0.28; k = 1.0
    gives 0.443), levels 4-5 0.97 (RMSE 0.232, 0.80-1.21) on every pre-ladder unit-snapshot
    (taken as level 5) and the three level 5 sets. Levels 1 and 2 were not measured and take
    level 3's rate; level 4 takes level 5's. With the ceiling free the level 3 slope is not
    distinguishable from 0 on the new points (k 0.04, -0.20 to 0.29); 0.2 is the fit in the
    form the product uses (a pass is worth 1.0 at no change), kept small and positive rather
    than 0 because the level 3 runs at `bd209e71` still rank units by share (rho 0.63).
  - **The own-commit prior and the flat evidence model fade at level 5's rate**: having typed
    code is implementation-shaped knowledge, and the flat model is the rule for pre-ladder
    answers, which count as level 5. The mass model is the same at every level.
  - **The anchor is unchanged**: one share since the unit's last answer at any level, for
    every level (the "single fade from the last answer" of Phase 3.2). Anchoring each level
    at its own latest sitting would be truer but changes what a later answer restores; open
    question.
  - **Config**: a `decay_k_by_level` table (keys "1" to "5") over the defaults, levels left
    out keeping them; without one, a `decay_k` other than 1.0 applies to every level (the
    single-k rule stays reachable), while `decay_k` = 1.0 is read as not chosen because every
    config written from Phase 3.1 to 3.4 carries it. `dump` writes the table only when set,
    so a later refit's defaults reach every repository that did not choose its own (the
    lesson of Phase 1.4's `share_decay_rate`).
  - **Shown everywhere decay is**: status notes ("decayed from 1.00 by share 0.52 at levels
    1-3 (k 0.2)"), the decay line, the browser's unit tile, the export's `settings.decay`
    (`rate`, `by_level`, `prior_rate`), the dashboard header, the synthetic report
    (`predicted` at the run's level; `decay.level_rate`; report version still 3, fields
    added). Replay rules version 4 and the rates in the fingerprint make stored series stale;
    this repository's series and dashboard were rebuilt.
  - **Measurement** (10 calls of 10): `bd209e71` (2024-08, mean share 1.00) and `b60bdd6c`
    (2022-09, 1.50), two level 3 question sets each: 0.69 and 0.63, then 0.88 and 0.90. The
    brief's 3+ cannot be reached (the root reads 1.67). The oldest snapshot scored highest
    because the answerer, finding no code for most units, reasoned from the questions and was
    graded correct, while at 2024 it answered from older code and was graded wrong (TESTBEDS).
  - **digitalme** for pisan@uw.edu is prior-only, so its status is unchanged but for the decay
    line (the prior fades at level 5's rate, still 1.0). Hypothetically, levels 1-3 passed on
    every unit at `f557893` would read 6.5 instead of 27.6.

- **Phase 4b, the HintWise app (2026-11-15)**: built and tested locally with fakes, not deployed
  (docs/DEPLOY.md lists the owner's and the Plabs session's steps).
  - **Layout**: the web app in `web/`, created from claude-built-apps `platform/templates/app/`
    (aeed6d5) with the template's modules and tests unchanged but for the slug; its own code in
    `web/src/core` (pure TypeScript shared by the page and the functions), `data`, `model`, `ui`;
    `netlify.toml` at the repository root with `base = "web"`. The repository keeps its name
    (owner): the slug `udebt` is written out in `footerHtml('udebt')`, the gateway row, the
    sign-in label and the Netlify site.
  - **Function framework**: Netlify Functions v2 (a default export taking a web-standard
    `Request`, `config.path` under `/api`), bundled by Netlify's esbuild, no Netlify package:
    the same handlers run on Netlify, on the Vite dev and preview servers (`web/dev/
    api-plugin.ts`) and in vitest. Three functions: `/api/questions`, `/api/grade` (the gateway
    with the person's token; grading also records the sitting with that token) and
    `/api/excerpt` (a line range of one file at a sha from raw.githubusercontent.com, at most
    200 lines of a file under 2 MB, `no-store`). A token is checked with one PostgREST read of
    `app_udebt.repositories` (row level security refuses a bad token, an unknown repository
    returns nothing), kept five minutes per token and repository.
  - **Worker dependency**: none. Stdlib `http.server` (ThreadingHTTPServer) serves three JSON
    routes behind Render's TLS proxy, one job at a time on a 512 MB instance; a framework would
    add nothing these routes need and a dependency the package has never had.
  - **The model steps of an analysis** run in-process (`worker.run(model=...)`) through
    `MessagesLLM`, a tool loop over a Messages API: udebt's own prompts (which carry the offline
    structure), the read-only tools `Read`, `Glob`, `Grep` over the clone (named as in the CLI's
    Claude Code runs, so the prompts' "read-only tools" still hold), a `submit` tool whose input
    schema is the prompt's schema (the gateway passes `tools` but not `tool_choice`; a JSON text
    answer is accepted too), one call per turn, at most 8 turns (the last offers only `submit`),
    the oldest tool results cut when the body nears 1,000,000 bytes. Grep takes fixed strings,
    not regular expressions (no pattern from a model can stall the worker). New analyses use the
    person's own key (16,000 output tokens); the gateway path exists behind
    `UDEBT_ALLOW_GATEWAY=1` for when the owner opens it.
  - **Worker limits**: `UDEBT_MAX_COMMITS` 6000, `UDEBT_MAX_MB` 200, clone timeout 300 s, three
    walkthroughs per new analysis, the replay and export skipped (`export=False`, `udebt worker
    run --no-export`: the learner app uses neither, and on a large team the replay is the
    slowest part), git with prompts off, https only, symlinks checked out as files and small pack
    buffers. GitHub's API is asked first whether a repository is public and how large (best
    effort, unauthenticated), `git ls-remote` names the commit, and a commit already analysed is
    added to the person's list without a clone. A token with under 15 minutes left is refused,
    so a job's last writes do not outlive it.
  - **Shared analyses without the service-role key**: any signed-in person may write an
    analysis of a public repository with their token (it is theirs: only they change or delete
    it); everyone reads every analysis. When the account goes, the analysis stays unowned (`on
    delete set null`), and the unique key `(repo_id, sha, user_id)` lets several unowned ones
    coexist. The page prefers HintWise's own analysis (a sample), then the person's, then the
    newest shared one, and says which it shows ("prepared by HintWise", "shared by another
    person"): a person could write a misleading analysis with their token (open question).
  - **Decay between analyses**: a record keeps the commit of the analysis it was answered at;
    a newer analysis's `specs.changed_since` (computed by the worker for every earlier analysed
    commit, as `status` computes the share) fades it per level. Within one analysis nothing
    fades. A commit with no figure is not faded and the debt page says so.
  - **Quizzes without read tools**: the question writer gets the unit's map fields, its
    orientation and code excerpts (the walkthrough's steps, else the top of the files its
    orientation names); the grader gets the excerpts of the files each question cited. At most
    60,000 characters of code a call (about 15,000 tokens), so a two-question sitting is about
    10,000 input tokens. The rules are the CLI's (`udebt/prompts.py` constants, exported to
    `web/src/core/prompt-parts.json` by `udebt.hosted.prompt_parts`, a test failing when stale).
    Orientations come from the analysis, or from the unit's own fields (no call).
  - **What a record keeps**: as the CLI's but without the answer text and the feedback; its
    `data` keeps the question's two other phrasings, the reference answer and the cited files, so
    a missed question comes back in other words with no call (`revealedVariant`). The reference
    is shown on a miss anyway; the person can read it in their own rows, as they can in map.json
    locally.
  - **Parity**: the TypeScript ladder and scoring are checked against
    `web/test/fixtures/parity.json`, written by `tests/test_hosted_parity.py` from the Python
    core (11 record sets at four shares, a hierarchy at two shares, compound questions).
  - **Preview mode**: without Supabase env the page runs on fixture rows (`udebt worker sql
    --dialect json --public`), a fake sign-in that drives the real sign-in panel, and the
    functions with fakes (`UDEBT_FAKES=1`, ignored on Netlify; GitHub read with `git show` from
    a local clone). The Playwright walkthrough and the screenshots use it.
  - **The page**: the footer embedded byte for byte and pinned by its sha256; a strict CSP
    (`style-src` with the sign-in block's hash and `FOOTER_STYLE_CSP`, `style-src-attr
    'unsafe-hashes'` with the two style attributes of Google's unmodified G, no
    `'unsafe-inline'`), one source for `netlify.toml` and `vite preview`; the Anthropic browser
    SDK loaded only for an own-key sitting (the main bundle 312 KB instead of 690 KB), so the
    page keeps the mode and key with the same keys and rules as `llm.ts` in `model/prefs.ts`.
  - **Template deviations**: `@playwright/test` pinned to 1.63.0 (this environment's browsers);
    `@types/node` added (the functions and the dev plugin use Node); the lint and coverage
    include `netlify/`; ESLint ignores `_`-prefixed rest siblings.
  - **Migrations**: the 4a file renamed to `0001_app_udebt.sql` before anything was applied
    (the platform runs files in name order and names them `NNNN_`), content unchanged; the 4b
    changes in `0002_hosted.sql`, including the `platform.llm_apps` row. Shared tables need two
    `SHARED_TABLES` entries (supabase/README.md).
  - **Not ported in v1**: the tutor (it needs read tools over a clone per conversation),
    teach-back, digests (they need the commits since an answer, which an analysis does not
    keep), decision records, team views and pull-request checks. The spaced review queue was
    ported the same day (below).
  - **Samples**: `python -m udebt.hosted.samples` checks each output against the pins in
    `testbeds.json` and writes the public SQL; tested on a digitalme output made without a model
    call. The UDebt session generated no sample and made no model call.

- **Phase 4b, the review queue (2026-11-15)**: Phase 3.3's spaced review in the hosted app, v1's
  next feature (Phase 4b answer 5). Tested with fakes only; no model call, nothing deployed.
  - **Where**: `web/src/core/review.ts`, beside the ladder and scoring ports. The brief said
    `web/src/model/`, which holds the clients; the page and `/api/grade` both need the fold, and
    `src/core` is the code they share.
  - **Parity**: the fold, the stamped states, the items with their phrasing and the due lists
    are checked case by case against `udebt/review.py` (`web/test/fixtures/review-parity.json`,
    20 record sets, written by `tests/test_hosted_review_parity.py`). Python's `round` (half to
    even on the exact double) and `timedelta(days=x)` (the fraction to the nearest microsecond,
    then truncated to the second) are reproduced, so due times agree to the second.
  - **No schema change**: the state after each answer is on `records.data.review` (`ease`,
    `interval`, `reps`, `due`, as the CLI's `review`); the column's comment already named it.
    The fold over the records stays the source of truth (the page never reads `data.review`);
    the stamp is there for an export or import.
  - **Stamping**: `/api/grade` reads the person's earlier records on the sitting's units (one
    PostgREST read with their token, by time then id) and folds them; if the read fails it
    writes nothing and the page records the sitting with its own fold, as when the write is
    refused. The page path (fakes, own key) stamps from the records it holds. The live store
    reads records by time then id, so a sitting's answers on one key fold in the order written.
  - **The tab**: items on units the current analysis has (as `udebt serve`), most overdue
    first; a sitting of 5 by default (1 to 10) through the quiz's flow (confidence, "I don't
    know" with no call, the answer shown on a miss at levels 1-2, one grading call at the end),
    `source: review`; no orientation before a review question and no `quiz_sessions` row (as
    serve); serve's "include items not yet due" is not offered. The number due shows on the tab
    and on the repository card, a unit's page lists its items, and each graded answer says when
    it is back in review.

- **Phase 4a answers (2026-11-15, 15-minute rule)**: the hosted app v1 has no own-commit
  prior (learner, not author); a soft-block acknowledgement carries over later pushes on the
  same PR until its touched units change; specs store file and line ranges, never code;
  a digest with no evidence defaults to the last 30 days; digitalme's dense co-change
  graph is left as is since the import graph carries the ordering.

- **Phase 4b, the tree fallback (2026-11-15, owner's yes)**: a unit that names only directories
  (no walkthrough, no file in its orientation or paths) is quizzed from the top tracked files
  under them, lines 1-60 each, as a named file is read. Tested with fakes and on a clone of
  mlflow at its pin; no model call, nothing deployed.
  - **One rule, twice**: `udebt/topfiles.py` and `web/src/core/topfiles.ts`, checked against
    each other on `web/test/fixtures/topfiles-parity.json` (filekinds' lock, generated, media,
    docs and test patterns character for character, `structure.SOURCE_EXTS`). Never lockfiles,
    generated files, media, symlinks, submodules, empty files or files over 2 MB; then source,
    other files (config, data, styles), docs, tests and fixtures (a new `FIXTURES` pattern, not
    added to filekinds, whose kinds weigh commits); larger first, then by path. Each directory
    gives its top four and the unit takes them in turns, at most four, so a unit over two
    directories shows both.
  - **Write side**: the worker records the result as `files` on each unit with a real directory
    among its orientation's and own paths (`git ls-tree -r -l` of the clone). specs.json is not
    a contract kind and the field is added, so no contract change; the loaded samples are valid
    as they are. The web prefers a walkthrough, then named files, then `files`.
  - **Read side, for older analyses** (the three samples in production): `/api/tree?repo&sha&
    path...` with `/api/excerpt`'s checks and `no-store`. One unauthenticated call to GitHub's
    recursive git trees API lists the whole tree at the sha (mlflow: 10,390 files), kept in the
    function's memory per repository and sha (four trees at most); a directory's own subtree is
    asked only when GitHub truncates the whole listing. A 403 or 429 stops calls until GitHub's
    reset (a minute when it names none) and answers 503 with `retry_after`. The whole tree rather
    than one call per directory, because one call then serves every unit of a repository.
  - **The page** keeps each directory's answer per analysis for the session (memory, never
    storage), sends every directory a sitting needs in one request, and after a failure asks
    again only after 10 minutes; meanwhile the question is written without code and the prompt
    says so, as before. Both modes use it (the own-key path already fetched excerpts through the
    functions).
  - **Evidence after grading**: a graded question now shows the files it rests on, linked at the
    sha, as the CLI shows the evidence beside the reference answer; never before the answer. The
    fake gateway cites the excerpts its prompt carried, as a model would, which is what the
    Playwright walkthrough and the screenshots show.
  - **mlflow at `f6ff35427d7e`**: `samples/mlflow.sql` loaded twice into a scratch Postgres; each
    of the 12 units with no file gets four source files (48 in all, each at least 60 lines), and
    the Python rule and the TypeScript handler pick the same files in the same order.

- **Phase 4b, use without signing in (2026-11-15, owner's requirement; design by the coordinating
  session under the 15-minute rule, details by the UDebt session; the owner can reverse any of
  them):** "The web site should be usable without any login, any progress on quizzes would just
  be saved on browser" (HOSTING.md decision 10, and "Use without signing in, as built"). Tested
  with fakes; no model call, nothing deployed.
  - **Samples only, as static JSON.** Anonymous visitors get no grants in app schemas, so the
    page reads the registered samples from the site: `web/public/samples/index.json` and
    `<name>.json`, the rows `udebt worker sql --dialect json --public` writes, derived from the
    committed `samples/*.sql` (`loadsql.rows_from_sql` reads back what `analysis_sql` wrote; a
    round-trip test in both dialects) by `python -m udebt.hosted.static_samples`, whose `--check`
    the Python tests run. A registered sample with no script or quiz file is listed with `null`
    (the fourth sample's script arrived the same day, so all four have JSON). Adding a repository
    still needs an account.
  - **Excerpts: the functions, opened to the samples at their pins** (not the browser reading
    raw.githubusercontent.com): no CSP change, the same limits and code for both, the preview's
    fakes cover it, and a request without a token can name nothing the site does not already
    serve. `/api/excerpt` and `/api/tree` without a token answer only a registered sample at its
    pin (the index bundled with the functions), else 401; with a token, unchanged. The tree's
    unauthenticated GitHub calls (one per repository and sha while warm) now serve visitors too.
  - **Advance quizzes** (`udebt worker quizzes`, `udebt/hosted/advance.py`): levels 1-3, three
    per unit and level, every architectural unit (sub-units too, for "Quiz this unit"), one call
    per level and batch of five units, with the hosted quiz's own prompt: a Python port of
    `questionsPrompt` and of what the page and `/api/questions` put in it (the request's caps,
    `refsForUnit`, the tree fallback, excerpts within 60,000 characters, UTF-16 lengths as
    JavaScript counts them), checked character for character on
    `web/test/fixtures/advance-prompt-parity.json`. The `claude` CLI answers through its
    structured output with no tools; the prompt keeps its sentence about the `submit` tool so it
    stays the hosted text. Questions without a reference answer are dropped (nothing to check
    against). The file is versioned (`udebt-advance-quizzes` v1) and is contract kind `quizzes`;
    adding a kind changes no existing one, so `CONTRACT` stays 1. The FakeLLM answers the hosted
    prompt as the preview's fake gateway does (names, summaries, the excerpts it was given).
  - **Self-checked provenance.** The record's `type` is `self-checked` (the column every row
    has, also the CLI record's `type`), `source` `advance` or `review`. The scoring counts it
    like any other answer; `repoScores` marks the units whose latest sittings hold one (or roll
    it up or inherit it), and the page labels them in the header, the debt table and tiles and
    each result. Every self-checked answer is `revealed` (its reference was shown), so a miss
    comes back in other words, as after a revealed miss in a model quiz. Confidence is fixed
    before the reference shows and no mark is highlighted.
  - **Which question.** A missed one in other words first (`revealedVariant`), else the first at
    that unit and level not asked, else the one asked longest ago; units past level 3 are skipped
    with an explanation.
  - **Who gets advance quizzes:** whoever has no model in use: a visitor without their own key,
    and a signed-in person with model features off (their records go to the server, labelled).
    Review with no model in use self-checks the items that kept a reference; others wait for a
    model. A visitor with their own key gets model-written questions and grading; never the
    gateway without an account (`App.quiz()`, and `GatewayClient` refuses to send without a token).
  - **Progress in the browser:** `LocalStore`, one localStorage entry per sample
    (`udebt.local.v1.<name>`), the same `UdebtStore` interface, every access in try/catch and
    memory when storage is blocked (the mode and key preferences too). Repository ids there are
    the index's order, so signing in or out returns to the list (not on the first sign-in state at
    load). Settings can delete the answers kept in the browser.
  - **The copy offer:** a card at the top of every signed-in screen while the browser holds
    answers never offered: copy (the server store's `addRecords` with the person's token, every
    field kept, under the account's repository of the same URL), keep separate, or discard; the
    choice marks them decided (a high-water mark of local ids per sample), so they are never
    offered twice. A sample the server does not know keeps its answers in the browser and says so;
    a failed copy keeps the offer.
  - **Cost:** the main bundle 343 KB (312 KB before).

## Non-goals (for now)

- Editor or Claude Code hook integration (provenance tracking). Later phase.
- Time-based decay. Only commit-based decay in the slice.
- Blocking merges. Every signal is advisory unless a team opts in (ROADMAP).
- Storing source code in the hosted service. Metadata only (ROADMAP Phase 4).

- **Phase 4b answers (2026-11-15, 15-minute rule; the owner can reverse any of them):**
  (1) a signed-in person's shared analysis stays visible, labelled as theirs, HintWise's own
  preferred (revisit with real users; the alternative is a worker signature or samples only);
  (2) the platform's default $0.25 per person per day stays until measured on the gateway;
  the owner then said yes (same day, "1 yes") to the cheaper model as `default_model`
  (`supabase/ops/udebt-llm-caps.sql`, the web app's `DEFAULT_MODEL`); (3) own-row records stay: the hosted debt is a self-assessment,
  README says so; (4) the free instance keeps 6,000 commits and 200 MB, large repositories are
  precomputed samples or `udebt worker run` locally then `udebt push`; (5) the spaced review
  queue is ported next, the tutor (needs streaming and the `claude` CLI) after.

- **ClaudeCLI isolation (2026-11-15, 15-minute rule):** every `claude -p` call passes
  `--setting-sources user --strict-mcp-config`, and a call that reads code `--tools Read Glob
  Grep` as well as `--allowedTools`, so the analysed repository's `.claude/settings.json`
  hooks, permissions and `.mcp.json` never load (found by the testbeds session: sentry-javascript's
  SessionStart hook installed and built the clone on every call). Reproduced with a one-hook
  repository: the hook ran without the flags, not with them. The hosted worker is unaffected.

- **Owner's yeses of 2026-11-15 (after the Phase 4b report):** (1) the cheaper model as
  the hosted default; (2) a fallback for units whose paths are directories only (12 of mlflow's
  30 units had no file for a quiz): the top tracked files under the directory; (3) the production
  steps in docs/DEPLOY.md, in their order: the session merges to `main`, the owner imports the
  Netlify site and creates the Render blueprint, the Plabs session runs the migrations, labels,
  the three sample loads and the caps file.

- **Use without signing in, the owner's answers (2026-11-15, "2 everybody gets advance
  quizzes 1,3,4,5,6 yes"):** (1) self-checked answers count at full weight, labelled; (2) the
  advance quizzes are offered to everybody, signed in or not, with or without a model in use,
  the model-written and model-graded quiz staying one choice away; (3) a copy leaves the browser's
  answers in place; (4) copied rows keep the review states folded from the browser's answers; (5)
  visitors' excerpt calls stay `no-store` until Netlify usage is known; (6) the advance questions
  are written for every unit at levels 1-3, three per unit and level, about 51 calls for the
  four samples.

## Open questions

- Phase 4b tree fallback: `/api/tree` calls api.github.com unauthenticated, 60 requests an hour per
  IP address, from Netlify's shared outbound addresses. One call serves a repository at a sha
  while the function is warm, and refusals are respected, but live refusals may be frequent.
  If they are, list the tree from the browser instead (GitHub's API allows it; the CSP would need
  `connect-src https://api.github.com`, and each person's own limit would apply), or give the
  function a GitHub token (a secret, the owner's yes)? The conservative choice was the function,
  no token, no CSP change.
- Phase 4b tree fallback: the files a question rests on are shown only after grading. Show the
  files the question was written from beside the question, as a "where to look" (a hint at
  levels 4-5)? Chosen: no, nothing new before the answer.
- Phase 4b tree fallback: regenerate the three samples (15 calls each) so they carry `files` and
  need no GitHub listing, or keep the read-side fallback for them? Chosen: keep it (no calls).
- Phase 4b review: a review sitting writes no `quiz_sessions` row (the table has no kind column,
  and the conservative choice was no schema change). Keep it so, or add a `kind` in a
  `0003_review.sql` so a person's sessions list their reviews too?
- Phase 4b review: PostgREST's row cap (Supabase's default 1000) would cut a person's records on
  one repository, both in the page's read (scores, ladder, review) and in `/api/grade`'s read
  for the review state. Page through them, or accept it for v1 (about 500 two-question
  sittings on one repository)?
- Phase 3.5: k_low (0.2) comes from huggingface_hub alone, a library whose architecture has
  been stable for years; digitalme decayed fastest on pre-ladder questions (k 2.72) and its
  June refactor changed its architecture. Calibrate levels 1-3 per repository from human
  answers once there are some, keeping 0.2 as the default?
- Phase 3.5: the synthetic answerer scores level 3 questions higher with no code (it reasons
  from the question) than with stale code, and two grading calls treated near-identical
  guesses differently. Is the synthetic method usable for levels 1-3 at all, or should the
  level 1-3 rate wait for human answers (or cue-free questions)?
- Phase 3.5: each level fades from the unit's last answer at any level, so a later level 4
  answer refreshes levels 1-3 too. Anchor each level at its own latest sitting instead?
- Phase 3.5: levels 1, 2 and 4 have no measurement; they borrow level 3's and level 5's
  rates. Measure level 1 and level 4 on the same snapshots (10 calls per level: two
  question sets over the two snapshots)?

- Phase 4a: v1 of the hosted app has no own-commit prior (HOSTING.md: the person is a learner,
  not an author). Keep the prior out of the hosted score entirely, or show it as `estimated`
  once a person verifies a commit email?
- Phase 4a: an acknowledgement of a soft block applies to one head commit. Should it carry
  over to later pushes on the same pull request unless they touch another flagged unit?
- Phase 4a: `specs.json` stores walkthrough code lines for public repositories. Confirm, or
  store only file and line ranges and fetch the lines from GitHub at view time?

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
- (Answered 2026-11-15, Phase 3.4: partial answers at levels 1-2 reveal the reference.)
- Should `udebt path` count a unit as learned at depth 3, or keep suggesting level 4 once the
  architecture is known? Today it says "nothing now" at depth 3 while understanding is above
  the threshold.
- (Answered in Phase 3.4: `dig` rotates through the pool, and writes a new check only when
  every pooled one was asked.)
- (Answered in Phase 3.4: teach-back is graded at the developer's next level.)
- (Answered in Phase 3.4: the graph ignores files listed by more than two units.) Co-change
  stays dense on digitalme (59 of 66 pairs): most commits there touch several units' own
  files. Raise `changes_with`'s floor, or show co-change only above a share of the unit's mass?
- (Answered in Phase 3.4: tutor sessions persist under `.udebt/tutor/`, gitignored.)
- (Answered in Phase 3.4: the browser grades a sitting in one call at its end.)
- (Answered in Phase 3.4: kept in map.json, documented in README.) Digests with no evidence
  on a unit cover the last 30 days before HEAD: right default, or since the developer's last
  own commit there (which for a sole developer is usually HEAD, so no digest at all)?
