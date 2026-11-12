# ROADMAP

How `udebt` grows from a single-developer CLI into a team tool with a hosted dashboard.
Written 2026-11-12 after the history-aware slice landed. Each phase names what exists
today, what the phase adds, the decisions it assumes, and the questions still open.
Dates are deliberately absent; phases are ordered by dependency.

## Where the slice stands

| capability | today | gap |
|---|---|---|
| Who wrote it | each commit is `ai`, `assisted` or `human` from agent signatures; counts shown | no contributor list, no bots, no "most likely" classes, no "you vs others" view |
| How much a commit matters | every commit counts one; decay is 0.9 per commit; churn is a commit count | a 200-file reformat and a new package weigh the same |
| What must be understood | two levels of units, proposed from import and co-change clusters and named by the LLM; responsibility sets per developer; scope per developer or team | sets are seeded from humans only, so agent-only units need a lead to assign them |
| Evidence of understanding | quiz answers graded against code; own-commit prior capped at 0.5 | prior is authorship-based, which AI authorship undermines (see research) |
| Over time | point-in-time `status`; records carry timestamps and commit shas | no replay, no series, no graph |
| Where it runs | local CLI over a clone, `claude` CLI for the model | no server, no shared store, no PR signal |

## What the research says

- **Authorship no longer implies understanding.** Wheeler's
  [The Substrate Collapse](https://arxiv.org/abs/2606.20882) (2026) argues that truck factor,
  Degree-of-Authorship and Degree-of-Knowledge all fail as a class once AI writes the code,
  and that the replacement must rest on evidence of comprehension. That is the case for the
  quiz as primary evidence and for treating the own-commit prior as a temporary crutch.
- **The classic models are still the right shape for everything except the evidence.**
  Fritz and Murphy's
  [Degree-of-Knowledge](https://www.cs.ubc.ca/sites/default/files/tr/2009/TR-2009-13_0.pdf)
  combines authorship with interaction and lets knowledge decay as others change the code.
  Avelino's truck factor uses `DOA = 3.293 + 1.098·FA + 0.164·DL − 0.321·ln(1 + AC)`
  (first authorship, own deliveries, others' acceptances) and a greedy removal of top
  authors ([paper](https://peerj.com/preprints/1233v2.pdf),
  [code](https://github.com/aserg-ufmg/truck-factor)). udebt should keep the structure
  (per unit, per person, decaying) and swap the evidence term.
- **Comprehension debt is now a named field.**
  [Osmani](https://addyosmani.com/blog/comprehension-debt/) (March 2026) frames it as debt
  that "breeds false confidence", argues architecture, data models and security boundaries
  deserve full comprehension while plumbing may not, and notes velocity and DORA metrics
  cannot see it. Storey's
  [Triple Debt](https://techleadjournal.dev/episodes/264/) separates technical debt (the
  system), intent debt (lost rationale: alternatives, constraints) and cognitive debt (the
  team's shared model). udebt's `decision` / `alternatives` / `why` fields are an intent-debt
  record; the quiz measures cognitive debt.
- **Attribution trailers are converging but not settled.** The Linux kernel and Fedora use
  `Assisted-by:`; Claude Code, Codex, Cursor and Copilot still emit `Co-authored-by:` by
  default; the [agentic-commits](https://github.com/grushetsky/agentic-commits) proposal
  specifies `Assisted-by: <agent> <version> (<provider>/<model>)`
  ([overview](https://allthingsopen.org/articles/open-source-ai-contributions-assisted-by-git-trailer-standard)).
  The signature table must accept all of these and stay data-driven.
- **Knowledge maps exist commercially.**
  [CodeScene](https://codescene.io/docs/guides/social/knowledge-distribution.html) computes
  knowledge islands and simulates bus-factor loss from deep file history; pair credit only
  splits when configured. [git-who](https://github.com/sinclairtarget/git-who) extends
  blame to file trees and sees through moves and reformats. Both are authorship-based and
  therefore subject to the critique above; neither has a comprehension term.
- **Commit significance is hard but tractable.** Tangled commits are common
  ([Herzig](https://www.st.cs.uni-saarland.de/publications/files/herzig-msr-2013.pdf));
  refactoring detection is mature only for Java and C++
  ([RefactoringMiner](https://arxiv.org/pdf/2502.17716)); a 2026 line of work uses small
  language models for multi-concern classification
  ([arXiv 2601.21298](https://arxiv.org/html/2601.21298v1)). For udebt a cheap structural
  score with an LLM refinement on the top candidates is the pragmatic path.
- **Architecture recovery is a clustering problem with a semantic layer.** Dependency and
  co-change graphs clustered by community detection, then named and refined by an LLM, is
  the shape of the current literature
  ([SemArc](https://www.researchgate.net/publication/396583765_Software_Architecture_Recovery_Augmented_with_Semantics),
  [LLM refinement, ICSE 2026](https://arxiv.org/pdf/2607.23774)). Co-change coupling from
  git history is language-agnostic and already cheap for udebt to compute.

## Phase 1: contributors and commit weight (local CLI)

**Adds**

- `udebt contributors`: every identity in history (merged through `.mailmap` and
  `identities` in config), with commit counts by class and a label:
  - **AI**: author matches an agent signature on effectively every commit.
  - **Joint human-AI**: human author whose commits carry agent trailers at a material rate.
  - **Human**: no agent signal.
  - **Bot / automation**: CI and pipeline identities (noreply bots, `*-bot`, `pipeline`).
  - **Most likely AI** / **most likely human**: identities with no explicit signature whose
    metadata leans one way: burst cadence (many commits a minute), uniform message shape,
    long bullet-list bodies, trailer use that starts abruptly on a date. Shown with a
    confidence and never used for scoring without a human confirming the label.
  - The current developer is marked **you**; teammates are listed so "mine vs theirs" is a
    single read.
- **Commit weight** `w` in `[0, 1]`, computed per commit from cheap signals and cached in
  `.udebt/commits.json`:
  - near zero: whitespace-only diff (`git diff -w` empty), pure renames, lockfiles and
    generated files, docs-only, version bumps;
  - low: test-only changes, single-line fixes;
  - high: new dependency, new top-level directory, data model or schema change, new
    external service, changes spanning several units, large insert-heavy diffs;
  - optional LLM refinement for the top candidates only (`architectural | behavioural |
    refactor | cosmetic` with weights 1.0 / 0.6 / 0.3 / 0.05), capped per run.
- Weight replaces commit counts everywhere: churn and importance use `Σw`, decay becomes
  `decay^Σw`, provenance shares and the own-commit prior are weighted, and the
  decision-mining score becomes one input to `w` rather than a separate thing.
- `udebt risk <rev-range>`: for a commit, branch or staged diff, the units touched, the
  weight landed in each, the developer's understanding of each, and a verdict. The rule is
  **min over units**: a change touching several units is as risky as the unit the author
  understands least, scaled by the weight landed there.
- A warn-only `pre-push` hook that runs `udebt risk` on the outgoing commits.

**Assumes** weights are heuristic first and LLM-refined second; "most likely" labels are
advisory; the hook never blocks.

**Exit** when on digitalme and dsa-instructor the contributor lists match what the owner
knows, a mass reformat scores under 0.1 and the June 2026 async refactor scores near 1.0.

### Phase 1.1: classifier gaps and squash bias

**Status 2026-11-12: done.** From the gaps TESTBEDS.md found on external history, and three
owner additions.

| item | status |
|---|---|
| `Assisted-by:` naming an AI (kernel, Fedora, LLVM; agentic-commits `(provider/model)` form) | done; also `Generated-by:` and `Made-with:`; first agent named wins, unknown AI -> generic `assisted-by` |
| Devin, `claude[bot]`, OpenCode, Jules, Codex connector, Amazon Q | done, as author identities and trailers |
| vscode `Authored with GitHub Copilot on behalf of @user` | done: `ai`, agent Copilot |
| case-insensitive emails and names | done for every signature pattern |
| `bot` class (dependabot, renovate, github-actions, semantic-release, other `*[bot]`, config `bot_signatures`) | done: lands no mass anywhere; contributors' Bot label reads the class |
| squash bias | done: a merged branch is weighed once as if squashed, shared by effective lines; `commits.json` v2 |
| directed agent work (owner addition) | done: config `directed`, `udebt directed add`; half weight for the directing developer |
| prior cap (owner addition) | kept at 0.5; team view says "no quiz evidence yet" |
| shared files (owner addition) | done: a file in k units gives each 1/k of its lines |
| `udebt risk` on an unmerged branch | not grouped yet: a range of ten small commits still weighs them one by one |

Exit check on real history. AI share of non-merge, non-bot commits in the last 12 months,
before and after: go-git (`Assisted-by:`) 5.0% -> 56.9% (TESTBEDS full classifier 57%),
PrefectHQ/prefect (Devin) 18.3% -> 78.8% (79%); digitalme 44.1% and dsa-instructor 97.5%,
unchanged, since only Claude Code works there. Grouping cuts total mass on digitalme from
83.9 to 69.7, on dsa-instructor from 991.9 to 541.2 and on go-git from 1388 to 898; prefect
squashes every pull request and is unchanged. A test repository with a ten-commit branch and
its squashed twin now weighs within 20%, against 6x before.

### Phase 1.2: version bumps, a parsed-commit cache, the synthetic rerun

**Status 2026-11-12: done.** From the starter-set run in TESTBEDS.md, which found human-cut
releases weighing as dependency changes and every command re-parsing the whole history.

| item | status |
|---|---|
| version-only manifest edits | done: a manifest or version file whose diff changes only version strings (own version lines, or pins that differ only in the version) is a `version` kind: no +0.3, no directory spread, out of line share; a commit of only bumps weighs 0.03; `commits.json` v3 |
| parsed-commit cache | done: `.udebt/commits-parsed.json` (`.json.gz` above 4 MB), new shas parsed via `rev-list`, unreachable ones dropped, classification redone each load, weighing inputs cached; gitignored; `udebt cache status\|rebuild` |
| `risk --staged` | reads staged version bumps, and uses staged line counts for line share |
| synthetic rerun | done: `--snapshots`, `--per-unit 2`, decision and behaviour questions, edited-vs-new split, `--suggest`, `--resume`; on digitalme every answer is right at mass 2.3 and the score falls to 0.46 and 0.21 at 25.7 and 50.4 (30 of 31 unit pairs worse with more mass), but within a snapshot mass ranks units weakly (rho 0.39, 0.00); Phase 5 |

Version bumps, heuristic weights before and after on non-bot commits (no LLM blending):

| repo | non-bot commits | weight changed | now "version bump only" | mass before -> after |
|---|--:|--:|--:|---|
| digitalme | 189 | 0 | 0 | 70.5 -> 70.5 |
| dsa-instructor | 2,510 | 2 | 0 | 541.2 -> 540.9 |
| PrefectHQ/prefect | 2,157 | 69 | 38 (20 weighed >= 0.7) | 1170.7 -> 1133.5 (-3.2%) |
| go-git | 3,954 | 56 | 10 | 818.7 -> 808.3 (-1.3%) |

Neither of the owner's repos has a release commit: digitalme has no version-only edit, and
dsa-instructor's one (`818ea5e`, a Playwright pin moved with a CI change) loses the +0.3 and
shares that with its branch. Prefect and go-git change mostly through dependency pins
(`griffe>=0.49.0,<2.0.0` to `<3.0.0`, `go.mod` bumps), many by dependabot or renovate, which
already land no mass. sentry-javascript, the motivating case, was not cloned here; its
releases rewrite each `package.json`'s version and internal pins, which the pin rule covers.

Wall time, `udebt contributors` and `udebt weigh` (4 cores; cold = no parsed cache):

| repo | command | before | after, cold | after, warm |
|---|---|--:|--:|--:|
| dsa-instructor (2,510 commits) | contributors | 4.8 s | 5.0 s | 0.26 s |
| | weigh, weights current | 5.2 s | | 0.30 s |
| | weigh, weights recomputed | 14.6 s | 15.2 s | 0.62 s |
| prefect (2,671, shallow) | contributors | 4.1 s | 4.2 s | 0.28 s |
| | weigh, weights current | 4.2 s | | 0.29 s |
| | weigh, weights recomputed | 9.4 s | 10.2 s | 0.49 s |

Fifty new commits on dsa-instructor parse in 0.36 s. The cache is 3.6 MB of plain JSON for
dsa-instructor (2.6 MB for prefect), about a quarter of that gzipped; servo was not cloned
here, so its cold run should cost what it did (8-9 and 18 minutes) and a warm one seconds,
with a cache of roughly 85 MB plain, 20-25 MB gzipped (extrapolated). Cached and uncached
results are identical on digitalme, dsa-instructor, prefect, go-git and this repository:
every commit field, the four weighing inputs and every weight.

### Phase 1.3: history mining for the AI era, major upgrades

**Status 2026-11-12: done.** From the first `udebt map` on huggingface_hub (TESTBEDS.md),
where 34 of 40 decision candidates came from 2021-2022 and 22 touched no file alive at
HEAD, and one owner addition.

| item | status |
|---|---|
| survival weighting | done: score x share of the commit's files alive at HEAD, renamed successors followed (rename pairs cached as `mv`); no surviving file drops a commit unless founding |
| score from the commit weights | done: commit weight (a branch commit's standalone weight) + new dir 0.3, renames 0.2, keywords 0.1 each (max 2); founding +0.5 after survival; no parallel dependency or infra count |
| recency | done: ties go to the more recent commit; `history_periods` (4) equal-mass periods each get `history_quota` (5) of the 40 slots |
| output | same fields plus `survival`, `weight`, `period`; `udebt history --candidates [--json]`, no map, no LLM |
| major upgrades (owner addition) | done: a pin moved to a new major is "dependencies changed" (+0.3); patch, minor, own version, calendar and non-numeric versions as before; `commits.json` v4, parsed cache `ver` input v2 |
| synthetic rerun on dsa-instructor | done: 7 calls; a ceiling (0.98, 0.94, 0.96), no decay with snapshot age; Phase 5 |

huggingface_hub at `2c61ae8`, old miner against new (TESTBEDS.md has the rest):

| | before | after |
|---|--:|--:|
| candidates from 2021-2022 | 34 | 6 |
| candidates from the last 12 months | 1 | 25 |
| with no file alive at HEAD | 22 | 0 |
| mean survival | 0.25 | 0.98 |
| AI-involved | 2 | 18 |
| map: history links (distinct commits) | 9 (2; 7 links to the V1.0 release) | 31 (30) |
| map: links to the last 12 months | at most 2 (one candidate was that recent) | 22 |

The list now leans to the recent era (22 of 40 from the last quarter of the mass): AI-era
commits are larger and all their files survive, so they win the open slots; the quota keeps
the two middle periods at 5 each (BRIEFING open questions). On this repository the old
miner found 3 candidates and the new one 18, so the dogfood map, made from the old list,
would link more history if re-run; it was not re-run. On digitalme 17 of the old 29
candidates stay among the new 40; on dsa-instructor 9 of 40.

Major upgrades, heuristic weights before and after on non-bot commits: digitalme 0 of 189
changed, dsa-instructor 0 of 2,510 (its one version-only edit is a minor Playwright bump),
huggingface_hub 4 of 2,849 (ruff and mypy 0.x upgrades, +0.3 each; mass 1314.1 to 1315.2,
no candidate changed). No other testbed is cloned here; prefect and go-git, where Phase 1.2
saw most version-only pins, would move most.

### Phase 1.4: model fixes the owner approved

**Status 2026-11-12: done.** From the Phase 1.3 open questions: the history quota, decay by
share, a dsa-instructor run at larger shares, and Cargo's table form.

| item | status |
|---|---|
| history quota | done: 10 of the 40 slots per period, so with 4 periods slots are proportional to mass; `history_periods` and `history_quota` stay settings |
| decay by share changed | done: understanding x exp(-k x share), share = weighted changed lines landed on the unit since the evidence / the unit's size in lines at the cut-off; k = ln 4 (fitted 1.37); the prior erodes the same way; `decay_model: "mass"` keeps the 0.9-per-mass rule; status notes "decayed from X by share Y (mass Z)" |
| unit sizes | done: lines of the tracked files under the unit (line share's kinds, no binaries, shared files split), `git ls-tree` + `git cat-file --batch`, cached per blob in gitignored `.udebt/sizes.json` |
| synthetic rerun on dsa-instructor at larger shares | done: 5 calls, snapshots at mean mass share 0.30 and 0.70 (picked with `--suggest mshare:`): observed 0.85 and 0.40, the share model predicted 0.40 and 0.11 (too much decay; 0.9^mass 0.57 and 0.33); 12 of 12 pairs worse at the older snapshot; Phase 5, fourth result |
| TOML dependency tables | done: `[dependencies.x]` (and dev, build, target, workspace, Poetry) with `version = "..."` is a pin, through hunk headers git labels with the table; `commits.json` v5, parsed cache `ver` v3; no testbed commit changes (none uses the form) |

**The fit.** The share was recomputed under the new definition for each of the 72
unit-snapshots of the two recorded runs (digitalme `7f463b1`, `f557893`, `7b1895d`;
dsa-instructor `4f56755`, `a2245bd`, `b5436cb`; 12 units each, the score of two answers),
with understanding 1 at the snapshot, and each form fitted by least squares on a grid:

| rule | parameter | RMSE |
|---|---|--:|
| exp(-k x share) | k = 1.37 (used: ln 4 = 1.386) | 0.229 |
| (1 - share)^k | k = 0.87 | 0.226 |
| d^mass | d = 0.838 | 0.244 |
| 0.9^mass (Phase 1) | | 0.265 |
| exp(-k x mass share), mass since / unit mass, for comparison | k = 2.15 | 0.165 |

| run | snapshot | mean share | observed | exp(-1.37 s) | 0.9^mass |
|---|---|--:|--:|--:|--:|
| digitalme | `7f463b1` | 0.003 | 1.00 | 1.00 | 0.98 |
| digitalme | `f557893` | 0.323 | 0.46 | 0.66 | 0.78 |
| digitalme | `7b1895d` | 0.782 | 0.21 | 0.38 | 0.57 |
| dsa-instructor | `4f56755` | 0.001 | 0.98 | 1.00 | 0.98 |
| dsa-instructor | `a2245bd` | 0.090 | 0.94 | 0.90 | 0.90 |
| dsa-instructor | `b5436cb` | 0.281 | 0.96 | 0.71 | 0.81 |

The two forms fit equally well; the exponential is kept because it composes (fading over
two stretches of change is fading over their sum, which a replay over time needs) and
never reaches zero on a rewrite, and k = ln 4 reads plainly: understanding halves for every
half of a unit that changes. The fit is a compromise: per repository k is 2.39 on digitalme
(RMSE 0.191) and 0.14 on dsa-instructor (0.114), so the line share does not make the two
repositories' rates agree. dsa-instructor's units take large line shares the answers did
not show: bulk curriculum JSON and rendered figures in `offline-pipeline` (23,800 weighted
lines in 9 days, mostly two course additions) and one large commit in the small
`chroma-integration` (share 0.27 at mass 0.20). The mass share would fit better (0.165), and
a pooled rank correlation with wrongness is 0.67 for the line share, 0.67 for mass, 0.77 for
the mass share. BRIEFING records the choice as an open question; nothing else was tuned.

huggingface_hub at `2c61ae8`, `udebt history --candidates`, quota 5 against 10 (no LLM):

| | quota 5 | quota 10 |
|---|--:|--:|
| candidates per period (oldest first) | 8 / 5 / 5 / 22 | 10 / 10 / 10 / 10 |
| by year: 2020, 2022, 2023, 2024, 2025, 2026 | 2, 6, 3, 3, 1, 25 | 2, 10, 5, 4, 3, 16 |
| from the last 12 months | 25 | 16 |
| AI-involved | 18 | 11 |
| mean survival | 0.98 | 0.98 |

28 of the 40 stay; 12 recent commits give way to 12 from the middle periods. On digitalme
and dsa-instructor, whose histories fit in one year, the periods go to 10 each as well.

## Phase 2: conceptual units and responsibility

**Adds**

- **Structural unit discovery** feeding the mapper instead of file lists alone:
  1. an import graph where a parser is cheap (Python `ast`, JS/TS and Go by regex or
     tree-sitter), and a **co-change graph** from history for every language;
  2. community detection (Louvain or Leiden) on the merged graph gives candidate clusters;
  3. the LLM names each cluster, merges or splits them, and adds the cross-cutting
     decisions (auth model, persistence, error policy) that no cluster captures;
  4. a two-level hierarchy: units contain sub-units, so a large repo can be understood at
     the top level and dug into where needed.
- **Responsibility sets.** Each developer has the units they are expected to understand:
  seeded from `CODEOWNERS` when present, from their contribution footprint otherwise, and
  editable by a lead (`udebt responsibility <dev> add|remove <unit>`). A developer's debt
  index is computed over their set plus any unit they are about to touch. The team view
  asks instead whether every architectural unit has enough holders (a bus-factor target per
  unit, default two).
- `udebt scope` becomes per developer as well as team-wide: a unit can be out of scope for
  one person and architectural for another.
- `risk` (Phase 1) uses the sets: touching a unit outside your set that you do not
  understand is the strongest signal the tool emits.

**Assumes** CODEOWNERS is the preferred seed, that a lead exists to curate, and that two
hierarchy levels are enough for repos up to a few thousand files.

**Exit** when dsa-instructor maps to a hierarchy a maintainer agrees with and
`udebt risk` on a historical multi-unit commit flags the right unit.

**Status 2026-11-12: done**, except where noted.

| item | status |
|---|---|
| import graph | done for Python (`ast`) and JS/TS (regex) behind a one-function-per-language registry; C++, Rust, Java deferred (owner order); Go not planned; no tree-sitter |
| co-change graph | done, every language; commits over 30 files ignored (`cochange_max_files`) |
| community detection | done: stdlib Louvain, seeded for determinism; Leiden deferred, since none of dsa-instructor's 12 clusters or 51 sub-clusters is disconnected (the failure Leiden fixes). Another seed keeps 84% of files in the same cluster; the LLM pass absorbs the rest |
| LLM names, merges, splits, adds cross-cutting decisions | done; units record the cluster ids they cover |
| two-level hierarchy | done: map v3, `children`, `--deep` on status, quiz and risk; v2 maps still load |
| responsibility sets | done: CODEOWNERS, else human footprint; `responsibility seed / add / remove`; developers declaring their own sets deferred (owner answer 4, "later") |
| per-developer scope | done (`--for`, `--team`) |
| risk uses the sets | done: OUTSIDE level |
| `@org/team` handles | mapped by hand in config `handles`; expanding them through the GitHub API deferred to Phase 4 |

Exit check. digitalme (225 files) maps to 12 units, one with three sub-units, in 37 s;
dsa-instructor (1902 files, 2510 commits) to 12 units, two with nine sub-units between them,
in 62 s, with about 4 s of graph work, and the hierarchy follows the architecture notes in its
own CLAUDE.md. `risk` on dsa-instructor's `b194175` (Teach-Back tables, migration and engine)
flags `teachback` as OUTSIDE and the migration's unit as HIGH; on digitalme's `e45fe82` (the
June refactor) every touched unit is CAUTION for the sole author, heaviest first.

## Phase 3: time series and the local dashboard

**Adds**

- **Replay.** Debt for every developer and unit at any past commit: evidence records up to
  that time, provenance up to that commit, weights as computed. Snapshots per week (or per
  N commits) stored in `.udebt/snapshots/*.json`, committed like the rest.
- `udebt timeline` (CSV or JSON) and `udebt dashboard`: a static HTML page generated into
  `.udebt/dashboard/` with no server, one line per contributor of debt index over time,
  stacked by unit on click, with markers for quizzes, digs and heavy commits by others.
- **Signals.** The dashboard lists "high-debt member landed a heavy change" events from
  `risk`, which is what a lead needs to start the conversation the owner described.

**Assumes** weekly granularity by default and that the static page is enough until Phase 4.

**Exit** when the dashboard for digitalme shows the June 2026 refactor as a visible debt
step and a quiz as a visible drop.

**Status 2026-11-12: done; the exit check is partial** (no quiz has been taken on
digitalme, so there is no drop to show).

| item | status |
|---|---|
| replay | done: every developer (footprint >= 0.1, evidence or a set) on every top-level unit at each point, with `status`'s scoring code at an explicit cut-off (`ledger.Cutoff`): commits on the first-parent line by then (branches with their merge), evidence by then, sizes from that tree, the current map's units replayed against the past (absent until touched, importance from churn up to the point); per point also mass by class and each developer's index over their set |
| snapshots | done: `.udebt/snapshots.json`, one committed file rather than `snapshots/*.json`, one point per line; weekly (Mondays UTC), `day` or `mass:N` (config `snapshot_every`); `udebt snapshot` appends only newer points; a fingerprint marks a stale series, `--rebuild` |
| `udebt timeline` | done: `--dev`, `--unit`, `--format table\|csv\|json`, with the strongest risk events |
| `udebt export` | done: `udebt-export` v1 JSON or eight CSV tables (snapshots, provenance, unit points, evidence, risk events, heavy commits, contributors, units), no answer text unless `--with-answers`, gitignored `.udebt/export/` |
| `udebt dashboard` | done: one self-contained HTML file (inline CSS, hand-written JS, inline SVG, no request), a line per developer, crosshair tooltip, click or arrow keys for the per-unit breakdown (segmented bar and ranked list), quiz and dig markers, rugs for the developer's risk events and others' heavy commits (weight >= 0.5), contributors, units (ai%, you%, holders, bus target), risk events, the series as a table; light and dark; checked with Playwright at 1280 and 390 px |
| signals | done: risk events recomputed from history (each developer commit with weight >= 0.1 against their understanding at the step before it landed), stored with the series; `udebt risk --history [--since] [--all]` |

| | digitalme | dsa-instructor | this repository |
|---|--:|--:|--:|
| commits | 190 | 2,510 | 38 |
| points (interval) | 12 (week) | 19 (week) | 11 (mass:2) |
| risk events: OUTSIDE / HIGH / CAUTION | 0 / 4 / 308 | 0 / 143 / 97 | 0 / 40 / 75 |
| `udebt snapshot --rebuild`, cold | 0.6 s | 5.1 s | 0.2 s |
| `udebt dashboard`, series current | 0.2 s | 1.2 s | 0.1 s |
| `snapshots.json` | 117 KB | 106 KB | 52 KB |
| dashboard file | 184 KB | 306 KB | 101 KB |
| render in Chromium (load event) | ~50 ms | ~65 ms | ~40 ms |

The strongest risk events (all HIGH; the one developer's set covers every unit he touched,
so OUTSIDE never fires): on digitalme `557eb8c` (2026-05-25, faq-cache-tts, understanding
then 0.25, weight 0.80, mass 0.70), `97ef5fe` (2026-05-15, frontend-spa, 0.00, weight 1.00,
the unit's first big change), `4f6903d` (2026-05-30, safety-guardrails, 0.25, weight 0.68);
on dsa-instructor `9e603f9` (2026-10-27, deploy-ops, 0.06, weight 1.00: the off-site backup
catch-up in a unit the agents had rebuilt), `f900b16` (2026-06-11, agent-turn-pipeline,
0.26, weight 1.00: the single supervised turn pipeline), `0a293ad` (2026-10-22,
offline-pipeline, 0.22, weight 0.87). The June refactor on digitalme (`e45fe82`, landed
2026-06-02) is a CAUTION event on eight units (understanding 0.47-0.50) and the largest
weekly step after the first, 51.8 to 56.3: visible but small, because a sole author's own
assisted commit only halves his share. dsa-instructor's index climbs from 72 to 96 between
September and November as agent commits overtake his.

Screenshots at 1280 and 390 px in light and dark showed and fixed: a clipped rug label, a
cramped two-column table layout, unit names breaking mid-word at phone width, tooltip
values wrapping, colliding time labels on a one-day history, and an x-axis with no labels
under two days. No horizontal page scroll at 390 px, no console error, no request.

### Phase 3.1: map paths, decay caps and refit, risk and dashboard polish

**Status 2026-11-12: done.** From the e2485d4 testbed runs (TESTBEDS.md) and the Phase 3
open questions. One `claude` CLI call (the huggingface_hub map; no retry was needed).

| item | status |
|---|---|
| map path resolution | done: each unit and sub-unit path is kept if it names a tracked file or directory, else replaced by the one tracked path it is the tail of, else dropped into `unresolved`; counts in `map.json` `paths` |
| coverage check and retry | done: under 50% of the clustered non-test source files, or a unit with no path left, warns and retries once with the unresolved paths and two root-relative examples (`--no-retry`); a map still short gets `warnings`, printed first by `status` |
| map prompt | done: says paths are relative to the root, with two examples taken from the files |
| huggingface_hub validation | done: 87 of 87 paths exact, coverage 78%, no retry (40 s); offline, the same map with the package prefix stripped resolves 84 by suffix, 6 exact, drops 3 ambiguous, leaves no unit empty |
| per-commit cap | done: a commit's changed lines in a unit are capped at the unit's size just after it (running sum of growth); a deletion counts its size once; `chroma-integration` 43.9 to 1.32 at `42530b1` |
| k refit | done: pooled k = 1.0 (config `decay_k`), on 108 unit-snapshots and 4 huggingface_hub aggregates; a floor was fitted and not adopted (`decay_floor`, default 0); Phase 5 |
| synthetic report | done: predictions use the configured k; the pooled section also correlates share changed and mass share; rows carry `lines_uncapped` |
| risk CAUTION on the prior | done: muted in the replay and in `udebt risk` (one line instead of a row); HIGH and OUTSIDE kept; digitalme 312 to 12 events, dsa-instructor 240 to 144 |
| dashboard line cap | done: eight developers by current debt plus a dashed "others" mean, a selector that swaps a developer in; Playwright at 1280 and 390 px, light and dark |

| replayed risk events | digitalme | dsa-instructor |
|---|--:|--:|
| Phase 3 | 312 (4 HIGH, 308 CAUTION) | 240 (143 HIGH, 97 CAUTION) |
| caps and k = 1, prior CAUTION kept | 312 | 240 |
| Phase 3.1 | 12 (4 HIGH, 8 CAUTION) | 144 (143 HIGH, 1 CAUTION) |

The caps and the new k change no event count on digitalme or dsa-instructor (the events are
the prior's, which neither touches enough to cross a threshold); the muting does it all.

### Phase 3.2: the question ladder

**Status 2026-11-12: done.** From the owner's first real quiz on digitalme (10 answers over
two sittings: 0 correct, 1 partial, 4 wrong, 5 unknown), every question about one exception
handler, one call or one function: "I would need to understand the overall architecture
better before I could understand how the API key is used." Two model calls (the validation
sample).

| item | status |
|---|---|
| ladder | done: 1 purpose and placement, 2 decision and alternatives, 3 consequence across units, 4 behaviour under failure, 5 implementation (`udebt/ladder.py`); levels 1-3 name no identifier |
| progression | done: derived from the records (each stores `level`): next = depth + 1, a confident wrong at n >= 2 asks n - 1; pre-ladder records are level 5 and move nothing |
| scoring | done: verified = (v1 + v2 + v3) / 3 + 0.1 (v4 + v5) from each level's latest sitting, understanding = min(1, verified x fade); `evidence_model: "flat"` keeps the old mean |
| questions prompt | done: one call per run, each unit at its level with its decision, alternatives, why and history, the ladder described, identifiers forbidden at 1-3 explicitly; the grader told the level rules |
| orientation | done: before the first level 1 question on a unit, cached in map.json (`orientation`, with the basis it was written from), all missing ones in one call; `dig` opens with it |
| `quiz --level N`, `--preview` | done: a forced level (the old flat questions at 5) recorded `forced`; a Markdown preview that records nothing |
| depth shown | done: `status` (and `--deep`), `timeline --dev`/`--unit`, the dashboard's units table and breakdown, the export |
| synthetic | done: `--level` (default 3) recorded in the report (version 3); not run |
| validation | done: `.udebt/samples/digitalme-ladder-quiz.md`, five units at level 1 with their orientations; no level 1 question names an identifier, by hand and by heuristic |

**Question-set variance should drop once levels are controlled.** The Phase 3.1 refit found
the same huggingface_hub snapshot scoring 0.75 on one question set and 0.58 on another
(Phase 5): the stable prompt asked "a decision question and a behaviour question", and what
the model wrote for each ranged from purpose to one function's edge case. With every question
of a run at one explicit level, two question sets differ in their wording, not in their
depth, so the next synthetic run should measure a smaller spread between question sets at a
fixed level; it is also the first chance to see how the decay curve differs by level (level 3
should fade with the share changed less steeply than level 5 does).

## Phase 4: hosted (Netlify, Render, Supabase) with the CLI as the local twin

**Adds**

- The Python package becomes the shared core with a versioned JSON contract (`map.json`,
  records, snapshots) that both the CLI and the server produce and consume.
- **Supabase**: Postgres with row-level security for orgs, repos, contributors, units,
  evidence records, snapshots and risk events; GitHub OAuth for sign-in; identities mapped
  to GitHub users.
- **Render**: an analysis worker that clones or fetches a repo on demand, runs the same
  code the CLI runs, and writes results to Supabase. Code is read transiently; only
  metadata (shas, paths, unit descriptions, scores, question text) is stored. The model is
  called through the API with an organisation-supplied key (see questions).
- **Netlify**: the dashboard as a static SPA over the Supabase API, with the quiz and dig
  flows in the browser.
- **GitHub App**: a check run on pull requests that posts the `risk` verdict as advisory,
  and a webhook that refreshes provenance on push.
- `udebt push` / `udebt pull`: the local CLI syncs snapshots and evidence with the hosted
  store, so a developer can quiz locally with their subscription and the team sees it.

**Assumes** metadata-only storage, advisory checks by default, and an org-supplied API key
for the hosted model calls.

**What Phase 3 leaves for it to reuse** (2026-11-12):

- **The import contract**: `udebt export` (`udebt-export` v1) is the payload `udebt push`
  sends; its tables (snapshots per developer and unit, unit points, provenance, evidence
  without answers, risk events, heavy commits, contributors, units) map one to one onto
  Supabase tables, and the CSV form is their bulk load.
- **The worker's scoring**: `compute_scores` with a `Cutoff` and a `Ledger` is the whole
  replay; the worker runs `replay.ensure` on a fetched clone and writes the new points and
  events. Points are append-only and fingerprinted, so a re-run after a push inserts rows
  and a map change is an explicit rebuild.
- **The live dashboard**: `udebt/dashboard.html` reads one JSON object in the export's
  shape; the SPA can start as that page with the blob replaced by an API fetch, scoped by
  the permission model (a member's view drops other developers' rows).
- **The PR check**: a risk event is exactly the advisory verdict a check run posts, and
  `risk --history` is its backfill.
- Still missing: identity mapping to GitHub users (the export keys developers by primary
  email), the per-repository decay rate the Phase 1.4 runs ask for, and a cap on the
  developers a large team's series carries.

**Exit** when one team uses the hosted dashboard for a month and the CLI and server agree
on every number.

## Phase 5: validity and calibration

**Adds**

- Evaluate whether the own-commit prior predicts quiz outcomes on the testbeds; if it does
  not, lower the cap or drop it, as the Substrate Collapse argument predicts.
- Question quality: difficulty estimates per question, a per-unit bank so repeat quizzes
  do not reuse questions, calibration reporting (confidence vs correctness) per developer.
- Outcome validation: do units with high team debt see more reverts, hotfixes or
  incident-linked commits? This is the publishable claim and the product's proof.
- **Synthetic developers** (owner, second round; `udebt experiment synthetic --at <rev>`):
  the `claude` CLI takes the quiz with its knowledge frozen at an older snapshot. Questions
  come from HEAD and are graded against HEAD as in `udebt quiz`; only the answerer reads the
  snapshot, a temporary worktree with its `.git` link removed, told that this is the only
  code it knows. Prediction: wrongness rises with the weighted mass landed on a unit since
  the snapshot (Spearman, one-sided permutation test). Three model calls per run.

**First result, 2026-11-12** (digitalme, snapshot `44f520b`, the commit before the June
refactor; HEAD `f93102b`, 136 commits later; all 12 top-level units, one question each).
Mass since is in Phase 1.1 weights (merged branches grouped, shared files split).

| unit | mass since | share of its mass | files then/now | verdict (confidence) |
|---|--:|--:|--:|---|
| admin-persona-fix-loop | 7.19 | 0.57 | 3/5 | unknown (1) |
| frontend-spa | 6.81 | 0.49 | 21/35 | partial (2) |
| content-pipeline | 6.07 | 0.77 | 19/37 | wrong (2) |
| cost-control | 5.99 | 0.51 | 3/5 | wrong (1) |
| chat-flow | 4.92 | 0.49 | 5/6 | wrong (2) |
| auth-rbac | 4.33 | 0.56 | 3/4 | wrong (1) |
| analytics-persistence | 3.51 | 0.61 | 1/3 | partial (1) |
| persona-retrieval | 3.12 | 0.24 | 23/25 | partial (3) |
| faq-cache-tts | 2.85 | 0.61 | 3/6 | partial (1) |
| safety-guardrails | 1.78 | 0.45 | 3/5 | wrong (3) |
| email-notifications | 1.08 | 0.35 | 2/3 | unknown (1) |
| contacts-db | 0.45 | 0.16 | 5/5 | wrong (3) |

Spearman(mass since, wrongness) = **-0.27**, one-sided p = 0.81: **no support for decay**
in this run, and the sign is the wrong way. Reading it honestly:

- **Floor effect.** No answer was correct. Every unit had changed (mass since >= 0.45), and
  the question writer favours decisions from history, which are the recent changes, so even
  contacts-db, the least changed unit (decay predicts 0.95 of a verified understanding), was
  asked about the one detail that changed (an equality filter became a substring match).
- **The sign comes from the confidence penalty.** Without it (wrong and unknown both 1) the
  correlation is 0.00. Heavily changed units contain files the snapshot lacks, so the
  answerer says it cannot find them: a known unknown, no penalty. Lightly changed units have
  familiar code with one changed detail, so it answers confidently from the old code: a
  misconception, -0.5. That is exactly the debt udebt cares most about, and decay by mass
  rates it as nearly none.
- **Too small to conclude.** Twelve units, one question each, one snapshot, one run.

Next: run snapshots closer to HEAD so some units have no mass since (a real control), ask
two or three questions per unit with some on decisions older than the snapshot, repeat
across snapshots and on dsa-instructor, and only then consider the model change the result
points at (below). The answerer behaved as intended: it named the files it could not find
and answered from the old code where it could.

What it suggests for the model, untested: decay may need to distinguish edits to code a
developer has seen (misconceptions) from new code (known gaps), rather than summing both as
mass; and a small edit to a decision a question can hit may matter more than its line count.

**Second result, 2026-11-12** (Phase 1.2 design: digitalme at HEAD `f93102b`, three
snapshots on the first-parent line sharing one question set written from HEAD, all 12
top-level units, two questions each, the first on the decision and the second on current
behaviour, no history in the question prompt; 7 model calls). The brief asked for snapshots
near 10, 25 and 50 mass with a third of the units unchanged at the nearest, but at mass 10
only 1 of 12 units is unchanged (`--suggest`), so the nearest moved to 2.3, the closest with
4 unchanged. digitalme has no version-only edits, so mass is the same in Phase 1.1 weights.

| snapshot | commits since | mass since | units unchanged | mean score | wrong / partial / correct / unknown | confidently wrong | Spearman(mass since, 1 - score) |
|---|--:|--:|--:|--:|---|--:|---|
| `7f463b1` (2026-09-02) | 8 | 2.3 | 4 of 12 | 1.00 | 0 / 0 / 24 / 0 | 0 | undefined: every answer correct |
| `f557893` (2026-06-04) | 98 | 25.7 | 0 | 0.46 | 9 / 6 / 8 / 1 | 2 | 0.39 (p = 0.10) |
| `7b1895d` (2026-05-24) | 160 | 50.4 | 0 | 0.21 | 14 / 8 / 1 / 1 | 0 | 0.00 (p = 0.49) |

Per unit, mass since / mean score:

| unit | `7f463b1` | `f557893` | `7b1895d` |
|---|--:|--:|--:|
| chat-flow | 0.23 / 1.00 | 3.56 / 0.00 | 7.11 / 0.00 |
| cost-control | 0.08 / 1.00 | 3.89 / 0.50 | 8.33 / 0.00 |
| persona-retrieval | 0.07 / 1.00 | 1.98 / 0.75 | 6.07 / 0.25 |
| admin-persona-fix-loop | 0.08 / 1.00 | 4.14 / 0.50 | 9.63 / 0.25 |
| auth-rbac | 0.26 / 1.00 | 2.90 / 1.00 | 5.61 / 0.25 |
| analytics-persistence | 0 / 1.00 | 2.18 / 0.50 | 4.18 / 0.25 |
| frontend-spa | 1.07 / 1.00 | 4.05 / 0.25 | 9.69 / 0.50 |
| safety-guardrails | 0 / 1.00 | 1.64 / 0.50 | 3.69 / 0.00 |
| content-pipeline | 0.17 / 1.00 | 2.92 / 0.25 | 7.62 / 0.25 |
| faq-cache-tts | 0.04 / 1.00 | 2.25 / 0.00 | 4.38 / 0.00 |
| email-notifications | 0 / 1.00 | 0.31 / 0.75 | 1.98 / 0.25 |
| contacts-db | 0 / 1.00 | 0.12 / 0.50 | 0.45 / 0.50 |

New against edited code (a unit is mostly-new when under half of its changed lines since
were in files the snapshot had):

| snapshot | mostly-edited: units, mean mass, wrongness, confidently wrong | mostly-new: units, mean mass, wrongness, confidently wrong |
|---|---|---|
| `7f463b1` | 8, 0.25, 0.00, 0 (and 4 unchanged, 0.00) | none |
| `f557893` | 10, 2.48, 0.48, 1 | 2, 2.59, 0.88, 1 |
| `7b1895d` | 9, 5.90, 0.75, 0 | 3, 5.23, 0.92, 0 |

Across snapshots, the same unit with more mass since scored worse in 30 pairs, better in 1
(frontend-spa, 0.25 to 0.50) and the same in 5; pooled over all 36 unit-snapshots, rho =
0.79 (p < 0.001, but the points are not independent). Udebt's own evidence (confidently
wrong -0.5) gives 0.30 and 0.00 within the two older snapshots; share since gives 0.33
(p = 0.15) and 0.47 (p = 0.06). Reading it honestly:

- **The floor is gone.** At the nearest snapshot every answer was right, including the 8 on
  the 4 unchanged units and the 16 on units with mass up to 1.07. The questions can be
  answered from old code, so what goes wrong at older snapshots is the code having moved.
- **Decay shows across snapshots, not yet across units.** The 30-to-1 paired count says a
  developer frozen earlier knows less of the same unit, but that is mostly the age of the
  snapshot. Within one snapshot, the rank of mass since barely says which units go wrong:
  0.39 (p = 0.10) at 25.7, 0.00 at 50.4 where nearly everything is wrong. Unit attribution
  is not shown by this run.
- **Understanding falls faster than 0.9 per unit of mass.** 0.9^mass predicts mean scores
  of 0.78 and 0.57 at the two older snapshots, against 0.46 and 0.21 observed; about 0.70
  per unit would fit both. Small units go wrong on little mass of their own: contacts-db scored
  0.50 at 0.12 (the substring privacy filter again), email-notifications 0.75 at 0.31.
  Their answers lean on code outside their paths (`backend/main.py`, shared helpers).
- **Unknowns did not dominate**: 2 of 72 answers. Confidently wrong answers were rare too,
  2 of 72: this answerer gave confidence 1 or 2 to most wrong answers, so the confidence
  penalty that set the first run's sign barely moves anything here.
- **New code was worse than edited code**, at similar or lower mass, but the groups are two
  and three units. The first run's hint (new code gives known unknowns, edits give
  confident misconceptions) is not supported: new code gave low-confidence wrong answers,
  not blanks, and edits rarely gave confident ones.
- Caveats: one repository, one run, an LLM as the developer (it recalls its snapshot
  perfectly, hence the ceiling), graded by the same model family against references written
  from HEAD; across snapshots, mass since and age move together.

What it suggests for the model, untested: keep commit-based decay, but 0.9 per unit of mass
looks slow for a unit that moved a lot; do not retune on one synthetic run, wait for human
quizzes. Attribute some mass beyond a unit's own paths (the files it imports, or a small
repo-wide term), since small units decayed on little mass of their own. Do not discount new
code: it was the worse case. Next: the same design on dsa-instructor (7 calls), a repeat on
digitalme for variance, and a snapshot near mass 10 with a third of the units unchanged on
a repository where one exists.

**Third result, 2026-11-12** (the same design on dsa-instructor: HEAD `a005a03`
(2026-11-11), map v3 of 12 top-level units at that HEAD, two questions per unit, developer
`pisan@uw.edu`, 7 model calls, Phase 1.3 weights). Snapshots on the first-parent line,
picked from `--suggest` before any answer was seen: `4f56755` at mass 4.5, the nearest in
the 2-5 band with a third of the units unchanged (8 of 12), then 25.2 and 50.6. Report:
`dsa-instructor/.udebt/experiments/synthetic-4f567559_a2245bd3_b5436cba-2026-11-12.json`
(not committed there).

| snapshot | commits since | mass since | mean share since | units unchanged | mean score | wrong / partial / correct / unknown | confidently wrong | Spearman(mass since, 1 - score) |
|---|--:|--:|--:|--:|--:|---|--:|---|
| `4f56755` (2026-11-07) | 33 | 4.5 | 0.003 | 8 of 12 | 0.98 | 0 / 1 / 23 / 0 | 0 | 0.26 (p = 0.33) |
| `a2245bd` (2026-11-02) | 155 | 25.2 | 0.031 | 5 of 12 | 0.94 | 1 / 1 / 22 / 0 | 0 | 0.18 (p = 0.29) |
| `b5436cb` (2026-10-31) | 320 | 50.6 | 0.097 | 0 of 12 | 0.96 | 0 / 2 / 22 / 0 | 0 | 0.58 (p = 0.03) |

Per unit, mass since / mean score:

| unit | `4f56755` | `a2245bd` | `b5436cb` |
|---|--:|--:|--:|
| web-layer | 0 / 1.00 | 2.14 / 1.00 | 4.43 / 1.00 |
| agent-turn-pipeline | 0.90 / 1.00 | 1.30 / 1.00 | 2.35 / 1.00 |
| persistence-migrations | 0 / 1.00 | 0 / 1.00 | 1.92 / 1.00 |
| deploy-ops | 0.68 / 1.00 | 3.83 / 0.50 | 6.49 / 0.75 |
| offline-pipeline | 0 / 1.00 | 2.62 / 1.00 | 3.42 / 1.00 |
| config-env-manifest | 0.08 / 0.75 | 0.68 / 1.00 | 0.89 / 1.00 |
| student-data-rights | 0 / 1.00 | 0 / 0.75 | 0.07 / 1.00 |
| spend-and-keys | 0 / 1.00 | 0 / 1.00 | 0.72 / 1.00 |
| chroma-integration | 0 / 1.00 | 0.20 / 1.00 | 0.20 / 1.00 |
| reporting-admin | 0.24 / 1.00 | 2.63 / 1.00 | 3.89 / 0.75 |
| classes-pairing | 0 / 1.00 | 0 / 1.00 | 1.02 / 1.00 |
| teachback | 0 / 1.00 | 0 / 1.00 | 0.99 / 1.00 |

New against edited code, units / mean mass / wrongness (no confidently wrong answers and
no unknowns anywhere): `4f56755` unchanged 8 / 0 / 0.00, mostly-edited 4 / 0.47 / 0.06;
`a2245bd` unchanged 5 / 0 / 0.05, mostly-edited 4 / 1.08 / 0.00, mostly-new 3 / 3.03 /
0.17; `b5436cb` mostly-edited 4 / 0.88 / 0.00, mostly-new 8 / 2.86 / 0.06. Across
snapshots the same unit with more mass since scored worse in 4 pairs, better in 4 and the
same in 22 (tau 0.00); pooled over the 36 unit-snapshots rho = 0.27 (p = 0.06, not
independent). Udebt's own evidence gives the same correlations (no confident wrong
answers); share since gives 0.26, 0.11 and 0.39 (p = 0.14). Reading it honestly:

- **It does not reproduce digitalme's decay with snapshot age.** Scores are flat (0.98,
  0.94, 0.96) where digitalme fell 1.00, 0.46, 0.21, and the paired count is even. 67 of 72
  answers are right: a ceiling, as digitalme had a floor in the first run.
- **Within a snapshot the ranking is weak, as on digitalme.** 0.26 and 0.18, not
  significant; 0.58 (p = 0.03) at the oldest rests on two partial answers out of 24, on
  deploy-ops and reporting-admin, two of the three units with most mass since. The sign
  agrees with decay; the evidence is two answers.
- **Absolute mass does not transfer between repositories.** The same mass is a small part
  of a big repository: at mass 50 a unit had on average 10% of its mass land since (at most
  22%), against 70% on digitalme, and all three snapshots are 4 to 11 days old (dsa-
  instructor lands about 30 commits a day). 0.9^mass predicts 0.81 at the oldest snapshot
  and 0.96 was observed: slower than 0.9 per unit of mass here, faster on digitalme. Over
  the six snapshots of the two repositories, the mean score falls with the mean share since
  (0.003, 0.015, 0.031, 0.097: 0.98, 1.00, 0.94, 0.96; 0.30: 0.46; 0.70: 0.21), which one
  per-mass rate cannot fit.
- **Small things, as before.** The one wrong answer (deploy-ops at `a2245bd`, confidence 1)
  is on the scheduled-workflow dedupe, whose code (`scripts/schedule_slots.py`) did not
  exist at either older snapshot: a low-confidence guess, not a blank. New code was again
  slightly worse than edited code (0.17 and 0.06 against 0.00), on 3 and 8 units. One
  partial was on an unchanged unit (student-data-rights), the grader's noise floor.
- Caveats as before (an LLM as the developer, graded by the same family, one run per
  repository), plus the design's mass targets came from digitalme and were not rescaled.

What it suggests for the model, untested: decay by how much of a unit changed (mass since
over the unit's mass, or over its mass at the last verification) rather than by absolute
mass, since a fixed rate per unit of mass decays a large unit far too fast and a small one
too slowly; this would also make the decay rate comparable between repositories. Do not
retune on two synthetic runs. Next: on dsa-instructor, snapshots chosen by share since
(mean 0.3 and 0.7, roughly mass 150-400, weeks to months back) to meet digitalme's range;
`--suggest` could take shares as well as masses.

**Fourth result, 2026-11-12** (Phase 1.4: dsa-instructor at larger shares, the first run
under the share decay model; HEAD `a005a03`, the same map and units, 12 units, two questions
each, one question set, developer `pisan@uw.edu`, 5 model calls). Snapshots picked before
any answer with `--suggest mshare:0.3,0.7`, on the units' mean mass share since (mass since
over the unit's mass), the measure digitalme's 0.30 and 0.70 were stated in: picking by the
new line share would have taken `a6d279e` (10-31, line share 0.31), next to the `b5436cb`
already run, and the mean line share jumps from 0.31 to 4.2 in one step (below). The model's
predictions were in the suggestion before the run: mean 0.40 and 0.11. Report:
`dsa-instructor/.udebt/experiments/synthetic-42530b11_f13a9cf4-2026-11-12.json` (not committed
there).

| snapshot | commits since | mass since | mean mass share | mean / median line share | predicted (share) | 0.9^mass | observed | wrong / partial / correct / unknown |
|---|--:|--:|--:|---|--:|--:|--:|---|
| `42530b1` (2026-10-28) | 761 | 116.8 | 0.30 | 4.28 / 0.64 | 0.40 | 0.57 | 0.85 | 1 / 5 / 18 / 0 |
| `f13a9cf` (2026-10-06) | 2150 | 339.4 | 0.70 | 5.83 / 1.63 | 0.11 | 0.33 | 0.40 | 8 / 13 / 3 / 0 |

Per unit, mass since / line share / mass share, then predicted / observed score:

| unit | `42530b1` | | `f13a9cf` | |
|---|---|---|---|---|
| web-layer | 16.40 / 0.38 / 0.15 | 0.59 / 1.00 | 61.72 / 1.80 / 0.58 | 0.08 / 0.25 |
| agent-turn-pipeline | 8.64 / 0.27 / 0.11 | 0.69 / 0.75 | 30.27 / 1.69 / 0.40 | 0.10 / 0.50 |
| persistence-migrations | 4.62 / 0.83 / 0.21 | 0.32 / 1.00 | 13.87 / 1.65 / 0.62 | 0.10 / 0.50 |
| deploy-ops | 12.60 / 0.85 / 0.27 | 0.31 / 0.75 | 31.27 / 1.54 / 0.66 | 0.12 / 0.50 |
| offline-pipeline | 7.47 / 0.54 / 0.19 | 0.47 / 1.00 | 13.55 / 0.87 / 0.35 | 0.30 / 0.50 |
| config-env-manifest | 5.14 / 1.69 / 0.40 | 0.10 / 0.50 | 9.92 / 2.34 / 0.78 | 0.04 / 0.25 |
| student-data-rights | 1.62 / 0.20 / 0.22 | 0.75 / 1.00 | 5.39 / 1.40 / 0.73 | 0.14 / 0.25 |
| spend-and-keys | 2.30 / 0.69 / 0.41 | 0.39 / 0.50 | 4.61 / 1.42 / 0.82 | 0.14 / 0.25 |
| chroma-integration | 2.53 / 43.92 / 0.38 | 0.00 / 0.75 | 4.00 / 52.30 / 0.61 | 0.00 / 0.50 |
| reporting-admin | 9.76 / 0.55 / 0.38 | 0.46 / 1.00 | 22.23 / 1.49 / 0.86 | 0.13 / 0.25 |
| classes-pairing | 3.19 / 0.87 / 0.56 | 0.30 / 1.00 | 5.75 / 1.61 / 1.00 | 0.11 / 0.50 |
| teachback | 1.48 / 0.59 / 0.32 | 0.44 / 1.00 | 4.58 / 1.89 / 1.00 | 0.07 / 0.50 |

Same unit at the two snapshots: worse with more mass since in 12 pairs, better in 0. Pooled
over the 24 unit-snapshots, rho with wrongness is 0.35 for mass since (p = 0.04), 0.63 for
the line share and 0.77 for the mass share (not independent). Within a snapshot, line share
0.43 (p = 0.08) and -0.17; mass share 0.34 and 0.22; mass 0.00 and 0.07. No unknowns, no
confidently wrong answers; mostly-new units were again a little worse than mostly-edited
ones (wrongness 0.19 against 0.13, then 0.64 against 0.50). Reading it honestly:

- **Decay with age reproduces on dsa-instructor at these shares.** The Phase 1.3 run's
  ceiling (0.98, 0.94, 0.96) is gone: 0.85 at mass share 0.30 and 0.40 at 0.70, every unit
  worse at the older snapshot. digitalme gave 0.46 and 0.21 at the same mass shares, so this
  repository still decays more slowly by every measure; its maintained architecture notes
  (CLAUDE.md) may carry the answerer across code it has not seen.
- **The share model does not land near the observed scores: it predicts too much decay.**
  Mean predicted 0.40 against 0.85 observed (RMSE 0.50 per unit), and 0.11 against 0.40
  (RMSE 0.32). The old 0.9^mass rule was closer here (0.57 and 0.33; RMSE 0.42 and 0.28),
  as the fit's per-repository rates (2.39 digitalme, 0.14 dsa-instructor) foretold. For the
  record, not as a tuning: exp(-2.15 x mass share), the fit's better-scoring alternative,
  would have predicted 0.54 and 0.24 (RMSE 0.37 and 0.20), also too low.
- **Two artifacts of the line share showed up.** `chroma-integration` reads 43.9 and 52.3:
  its numerator holds the removed lines of raw knowledge-base JSON (8,400 lines for one PDF
  extract) deleted since the snapshot, which its size at HEAD no longer holds; the answers
  (0.75, 0.50) show nothing of the kind. And bulk content and data files dominate the line
  share in units that hold them. Both point at the same fix candidates (cap a commit's lines
  in a unit at the unit's size, or count deletions of whole files once, or use mass over the
  unit's mass); none was applied.
- Caveats as before: an LLM as the developer, graded by the same family, one run, snapshots
  in one repository's busiest two months, and the rate was fitted on runs that included
  this repository's earlier snapshots.

What it suggests, untested: rank by share works better than rank by absolute mass (pooled
0.63 to 0.77 against 0.35), but one rate does not fit both repositories under either share;
the decay rate may need to be per repository (calibrated from its own quizzes) rather than
global. Do not retune on synthetic runs alone (BRIEFING open questions).

**The Phase 3.1 refit, 2026-11-12** (no model call: a re-analysis of the recorded reports).
Every unit-snapshot of the four local reports, scored correct 1, partial 0.5, wrong and
unknown 0: digitalme `44f520b` (12 units, one question each) and `7f463b1`, `f557893`,
`7b1895d` (36); dsa-instructor `4f56755`, `a2245bd`, `b5436cb` (36) and `42530b1`,
`f13a9cf` (24), 108 in all, the share recomputed with and without the per-commit cap (the
uncapped recomputation reproduces each report's own share and the Phase 1.4 fit, k 1.370,
RMSE 0.229 on its 72 points). huggingface_hub's runs exist only as summaries (the other
session's reports are not here), so each is one aggregate observation: the mean observed
score over its 12 units against the mean prediction over the 12 units of today's map at the
same snapshot, weighted 12. The proxy predicts what the runs predicted (ln 4: 0.55, 0.79,
0.41 against the runs' 0.53, 0.78, 0.37). The four: `bb3d3b5` on two question sets (0.75,
0.58), `2447394` (0.71) and `1ebdbf9` (0.56). sentry-javascript's and mlflow's runs (one
snapshot each, share vs wrongness significant) came with no mean observed score and their
checkouts are not here, so they are not in the fit.

| set | n | exp, uncapped: k / RMSE | exp, capped: k / RMSE | floor + (1 - floor) exp, capped: k / floor / RMSE | mass share: k / RMSE |
|---|--:|---|---|---|---|
| digitalme | 48 | 2.72 / 0.232 | 2.72 / 0.232 | 6.0 (grid edge) / 0.18 / 0.222 | 3.01 / 0.220 |
| dsa-instructor | 60 | 0.43 / 0.191 | 0.42 / 0.165 | 0.62 / 0.25 / 0.161 | 1.03 / 0.152 |
| huggingface_hub (4 aggregates) | 4 x 12 | 0.93 / 0.091 | 0.93 / 0.091 | 6.0 (grid edge) / 0.55 / 0.075 | 1.49 / 0.074 |
| pooled, unit points | 108 | 1.10 / 0.300 | 1.06 / 0.294 | 2.46 / 0.33 / 0.283 | 1.87 / 0.224 |
| **pooled, with huggingface_hub** | 156 | 1.04 / 0.255 | **1.02 / 0.251** | 2.39 / 0.35 / 0.241 | 1.75 / 0.193 |

RMSE of the capped share at a fixed k (digitalme, dsa-instructor, huggingface_hub, pooled
with it): ln 4 0.286, 0.312, 0.135, 0.261; **k = 1.0** 0.342, 0.251, 0.093, 0.251; the
floor fit 0.325, 0.245, 0.091, 0.241. The cap's residual: it changes only dsa-instructor,
whose RMSE at its own k goes 0.191 to 0.165 and at k = 1.0 0.262 to 0.251; no digitalme or
huggingface_hub commit reaches its unit's size. Pooled rank correlation with wrongness:
capped share 0.66 (uncapped 0.66), mass share 0.79, mass 0.60.

| snapshot | mean capped share | observed | ln 4 | k = 1.0 | floor fit |
|---|--:|--:|--:|--:|--:|
| digitalme `44f520b` | 0.61 | 0.17 | 0.46 | 0.57 | 0.54 |
| digitalme `7f463b1` | 0.003 | 1.00 | 1.00 | 1.00 | 1.00 |
| digitalme `f557893` | 0.32 | 0.46 | 0.66 | 0.74 | 0.68 |
| digitalme `7b1895d` | 0.78 | 0.21 | 0.37 | 0.48 | 0.49 |
| dsa-instructor `4f56755` | 0.001 | 0.98 | 1.00 | 1.00 | 1.00 |
| dsa-instructor `a2245bd` | 0.09 | 0.94 | 0.90 | 0.92 | 0.90 |
| dsa-instructor `b5436cb` | 0.28 | 0.96 | 0.70 | 0.77 | 0.72 |
| dsa-instructor `42530b1` | 0.72 (4.28 uncapped) | 0.85 | 0.42 | 0.52 | 0.51 |
| dsa-instructor `f13a9cf` | 2.27 (5.83 uncapped) | 0.40 | 0.11 | 0.20 | 0.37 |
| huggingface_hub `2447394` | 0.22 | 0.71 | 0.79 | 0.83 | 0.80 |
| huggingface_hub `bb3d3b5`, two question sets | 0.53 | 0.75, 0.58 | 0.55 | 0.63 | 0.61 |
| huggingface_hub `1ebdbf9` | 0.75 | 0.56 | 0.41 | 0.51 | 0.51 |

Chosen: **exp(-k x capped share), k = 1.0**, config `decay_k`. The pooled fit is 1.02 (1.06
without huggingface_hub, 1.00 with its first summary alone), so 1.0 costs nothing in RMSE
and reads plainly: 37% is left once a unit's worth of change has landed. The floor improves
the pooled RMSE by 0.010 and is not adopted: per repository it runs to the grid edge, a step
down to a plateau, on digitalme and huggingface_hub, so what it fits is the level an LLM
answerer keeps when it reads its snapshot in full, and that level moves with the question
set as much as with the snapshot (below). As a product default a floor of 0.35 would keep a
unit rewritten many times over a third understood. `decay_floor` (default 0) is there for a
team that wants it. Reading it honestly:

- **One k still does not fit all three repositories**: 2.72, 0.42 and 0.93. The pooled k is
  optimistic on digitalme (0.57 predicted against 0.17 observed at `44f520b`) and
  pessimistic on dsa-instructor's older snapshots (0.52 against 0.85, 0.20 against 0.40) and
  huggingface_hub's oldest (0.51 against 0.56). Per-repository calibration from real quizzes
  stays the open question.
- **The cap fixes the artifact it was aimed at, not every large share.** `chroma-integration`
  at `42530b1` goes from 43.9 to 1.32; at `f13a9cf` from 52.3 to 9.7, because while the unit
  held 77,000 lines of raw JSON several re-ingests each changed thousands of them, all
  counted against the 1,731 lines left. A per-commit fraction (each commit's lines over the
  unit's size after it, summed) would read 1.6 there and fits a little better pooled (RMSE
  0.275 at k 0.79 against 0.282 for the capped share, on the first fit's points); it is a
  change of definition, left as an open question.
- **The mass share still fits best** (pooled RMSE 0.193, rho 0.79), as in Phase 1.4.
- **Question-set variance is as large as the decay effect.** The same huggingface_hub
  snapshot `bb3d3b5` scored 0.75 (rho 0.82) on one question set and 0.58 (rho 0.57) on
  another; between huggingface_hub's newest and oldest snapshots the mean score moves 0.71 to
  0.56. The ranking (share against wrongness) held in all six external runs (rho 0.57 to
  0.82, every p < 0.03), but the level of the curve, which is what k and a floor fit, is
  not pinned by one question set: fitting it needs several question sets per snapshot or
  more questions per unit, and that is the next experiment's design, before any further
  refit.
- From the other session's runs (summaries only): mostly-new units were worse in 5 of 6,
  mostly through "unknown" answers; 1 of 144 answers was confidently wrong; 0.9^mass
  predicted 0.02-0.51 against 0.56-0.71 observed. Raw mass correlated with wrongness
  significantly once (mlflow, rho 0.53, p 0.039); the share did in all six.

## Decisions assumed in this roadmap

- Quiz evidence is primary; authorship is a decaying prior and may be removed.
- Weights, labels and risk verdicts are advisory. The tool informs a lead; it does not
  block a merge unless a team opts in.
- Hosted storage holds metadata, never source code.
- Python stays the single implementation language for analysis; the web layer is thin.

## Decisions taken 2026-11-12 (owner answers to the open questions)

1. **Hosted model billing**: yes, an organisation-supplied API key, encrypted in Supabase,
   for the hosted worker. The CLI stays on the `claude` subscription locally.
2. **Visibility**: a permission structure. One or more people are **repo owners**; owners
   assign who sees what (roles below). Default until an owner changes it: owners and leads
   see everyone, members see themselves plus team aggregates.
3. **Gating**: advisory by default; a repo owner can opt in to a soft block where a lead's
   acknowledgement clears it; never a hard block (see "Gating, explained").
4. **Responsibility sets**: seed from CODEOWNERS and footprint, lead curates, with the
   option to let developers declare their own later.
5. **Weight refinement**: LLM-refine only the heaviest commits per run (default 40),
   cached forever; the count is a setting.
6. **"Most likely AI" labels**: metadata only to start (cadence, message shape, trailer
   onset); content stylometry is a later option.
7. **Languages** for the import graph: Python and JS/TS, then C++ and Rust, then Java.
8. **Dashboard**: live (recomputed on push through the webhook), with `udebt sync` to pull
   and check current debt from the CLI, and `udebt export` for full history (CSV, JSON).

### Permission model (Phase 4)

| role | sees | can |
|---|---|---|
| owner | everything | assign roles, set visibility rules, opt the repo into soft blocking, curate responsibility sets |
| lead | every member's debt in their units | curate responsibility sets, acknowledge soft blocks |
| member | own debt, team aggregates, and what owners choose to open up | quiz, dig, sync |
| viewer | aggregates only | nothing |

Owners decide per repo whether members can see each other's individual numbers.

### Gating, explained

"Gating" is what happens when a developer with high debt in a unit lands a non-trivial
change there. Three levels:

- **Advisory.** A pull-request check that always passes and a local pre-push warning. The
  message says which unit, how much weight the change lands there, the author's
  understanding, and the fastest way to raise it (`udebt dig <unit>`, three minutes). This
  builds trust and cannot be gamed because nothing is at stake.
- **Soft block.** The check fails, but a lead can acknowledge it (a label or a comment) and
  the acknowledgement is recorded as the lead taking responsibility for review. This creates
  the conversation the owner described without stopping work.
- **Hard block.** The check fails until the author's understanding clears the threshold.
  Rejected: it turns the quiz into a hoop, invites quiz-gaming, and punishes the junior
  instead of informing the senior.

udebt defaults to advisory and lets a repo owner opt in to soft blocking per repo.

## Open questions (answered above, kept for the record)

1. **Hosted model billing.** The hosted worker cannot use a developer's `claude` CLI
   subscription. Is an organisation-supplied API key (bring your own key, per org, stored
   encrypted in Supabase) acceptable for the hosted version, with the CLI staying on the
   subscription for local use?
2. **Who sees whose debt.** Everyone sees everyone, leads see all and members see
   themselves, or members see themselves plus team aggregates? This shapes the Supabase
   row-level security and the ethics of the dashboard.
3. **Gating.** Advisory PR check by default, with a per-repo opt-in to a required status?
4. **Responsibility sets.** Seed from CODEOWNERS and footprint, curated by a lead, or
   declared by each developer for themselves?
5. **Weight refinement cost.** LLM-refine only the top N heaviest commits per run (N around
   40), or every commit once and cache forever?
6. **"Most likely AI" heuristics.** Metadata only (cadence, message shape, trailers), or also
   content stylometry on diffs, which is heavier and less transparent?
7. **Languages first** for the import graph: Python and JS/TS, then Go and Java?
8. **Dashboard granularity:** weekly snapshots, or per commit with weekly rollups?
