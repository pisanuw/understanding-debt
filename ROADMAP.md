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
| What must be understood | one flat list of LLM-proposed units with paths; a team-wide `blackbox` flag | nothing per developer; every developer's index sums over every unit |
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

## Decisions assumed in this roadmap

- Quiz evidence is primary; authorship is a decaying prior and may be removed.
- Weights, labels and risk verdicts are advisory. The tool informs a lead; it does not
  block a merge unless a team opts in.
- Hosted storage holds metadata, never source code.
- Python stays the single implementation language for analysis; the web layer is thin.

## Open questions

Numbered so they can be answered by number.

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
