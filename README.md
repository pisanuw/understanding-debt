# udebt: measure and reduce understanding debt

When an AI writes the code and a developer guides it, the developer's mental model of the
system is abstract and may not match how the code actually works. That gap is
**understanding debt**. A developer can pay it down by digging in, or let it grow by
continuing to build on top.

`udebt` is a standalone CLI that runs over an existing git repository, one with a mix of
human commits, AI-assisted commits and AI-authored commits, and uses the `claude` CLI
(subscription-billed, no API key) to:

1. **classify** every commit as `human`, `assisted` (a human committed with an AI co-author),
   `ai` (an agent authored it) or `bot` (CI, release, dependency or pipeline automation),
   from signatures in commit trailers and author identities;
2. **mine** the history for decision-bearing commits: founding commits, dependency and
   infrastructure changes, new top-level directories, renames, big restructurings, scored
   from the commit weights, discounted by how much of each still exists at HEAD and spread
   over the history's periods so the AI-built era is not crowded out;
3. **map** the architectural units of the codebase in two levels, starting from clusters it
   finds in the import and co-change graphs: the decisions, boundaries and flows a developer
   must understand, each linked to the commits that shaped it and who made them;
4. **assign** each developer a responsibility set, the units they are expected to understand,
   from CODEOWNERS or their contribution footprint, for a lead to curate;
5. **quiz** a developer with decision-centric questions (why X rather than Y, what breaks if
   you revert it, what happens when it fails) graded against the code;
6. **score** debt per unit and as a 0-100 index over a developer's set, and for the team
   against a bus-factor target, with a weak prior from the developer's own commits so the
   view is useful before the first quiz;
7. **flag** changes that land weight in units the author does not understand, most strongly
   outside their responsibility set;
8. **dig** into the worst unit with a short guided walkthrough that ends in a check question.

## Install

```bash
uv venv && . .venv/bin/activate
uv pip install -e ".[dev]"
```

Requires Python 3.11+, git, and the [`claude` CLI](https://docs.anthropic.com/en/docs/claude-code)
on your PATH and logged in.

## Use

```bash
cd your-repo
udebt init                    # creates .udebt/
udebt alias other@me.org       # other addresses you have committed under (optional)
udebt directed add <session>   # agent sessions (or a whole agent) whose commits you directed
udebt contributors             # everyone who committed: AI, joint human-AI, human, bot, "most likely"
udebt weigh [--refine]         # how much debt each commit can create; --refine asks claude about the heaviest
udebt clusters                 # candidate units from imports and co-change, no LLM
udebt map                      # clusters + history -> claude names units and sub-units -> .udebt/map.json
udebt map --rescore            # redo churn/importance/provenance with current weights, no LLM
udebt units                    # the hierarchy, with scope and importance
udebt responsibility seed      # propose each developer's set from CODEOWNERS, else footprint
udebt responsibility [dev]     # every developer's set, or one (dev: email, @handle, me)
udebt responsibility <dev> add|remove <unit>...   # a lead curates
udebt status                   # your debt per top-level unit, index over your set
udebt status --deep            # the same with sub-units; `udebt status <unit>` for one unit
udebt status --team            # holders per unit against the bus target; units below it
udebt history                  # the decisions that shaped the repo, oldest first, with who made them
udebt history --candidates     # the commits `map` would show the model as decision candidates (no LLM)
udebt quiz -n 5 [--deep]       # five decision questions from your set (sub-units with --deep)
udebt dig [unit]               # walkthrough of the highest-debt unit, then a check question
udebt risk [range|--staged]    # weight a change lands in each unit vs your understanding; --deep per sub-unit
udebt hook install             # warn-only pre-push hook that runs `udebt risk`
udebt scope <unit> blackbox    # "I choose not to understand this", for you (or --for <dev>)
udebt scope <unit> blackbox --team   # for everyone: stored in the map
udebt experiment synthetic --snapshots <rev>,... [--per-unit 2]   # validation: synthetic developers
udebt cache [status|rebuild]   # the parsed-commit cache (gitignored)
```

Commit everything under `.udebt/` except the parsed-commit cache and the line counts
(`sizes.json`), which `.udebt/.gitignore` keeps out. The map and `responsibility.json` are shared, each `records/<developer>.jsonl`
file holds one developer's quiz history, so the team view and bus factor work for everyone,
and `commits.json` holds the commit weights with their LLM refinements, paid for once.

**The parsed-commit cache.** Every command reads the whole history, and `weigh` reads it
four more ways (whitespace-ignored lines, added files, renames, version bumps). The first
command parses it once into `.udebt/commits-parsed.json` (`commits-parsed.json.gz` above
4 MB), keyed by sha: each commit's author, date, subject, body, parents and per-file line
counts, plus the weighing inputs once `weigh` has needed them. After that a command runs
`git rev-list HEAD`, parses only the commits the cache has not seen (`git log --no-walk`),
and drops cached commits no longer reachable from HEAD, so a rebase or an amend never leaves
stale history behind. Classification is recomputed from the cached fields on every load, so
a new signature in `config.json` applies at once; a `.mailmap` change rebuilds the cache.
On dsa-instructor (2510 commits) `contributors` goes from 4.8 s to 0.26 s and `weigh` from
5.2 s (14.6 s when the weights need recomputing) to 0.30 s once the cache is warm; the cold
run costs what a run cost before. The cache is derived and large on long histories (3.6 MB
for 2510 commits, about a quarter of that gzipped), so it is gitignored; a teammate's first
command rebuilds it. `udebt cache` shows what it holds against HEAD; `udebt cache rebuild`
starts it afresh.

## The model

**Commit weight.** Not every commit can create debt. Each commit gets a weight in [0, 1]
from cheap git signals: whitespace-only changes, pure renames, lockfiles, generated files
and docs-only commits weigh about 0.05; test-only changes are discounted; size (whitespace-
ignored lines, log scale), dependency and infrastructure changes, new code files, new
top-level directories and changes spanning several directories push the weight up; merges
weigh 0. `udebt weigh --refine` asks the model to classify the heaviest commits as
architectural, behavioural, refactor or cosmetic and blends that in. Weights are cached in
`.udebt/commits.json` (version 3; an older cache is recomputed, keeping refinements).

**Version bumps are not dependency changes.** A manifest (`package.json`, `pyproject.toml`,
`Cargo.toml`, `setup.py`, `pom.xml`, `go.mod` and the rest) or a version file (`VERSION`,
`_version.py`, `version.ts`, `lerna.json`, `Chart.yaml`, ...) whose diff in a commit changes
only version strings is a *version bump* in that commit, not a manifest change. A changed
line counts when it sets a version (`"version": "1.2.3"`, `version = "1.2.3"`,
`__version__ = ...`, `<version>...</version>`, a bare version) or when its old and new forms
are the same line apart from the version (`"@sentry/core": "8.1.0"` to `"8.2.0"`, which a
monorepo release rewrites in every package, or `playwright==1.56.0` to `==1.61.0`); a version
string needs at least `major.minor`, so `"port": 3000` is not one. A version bump adds nothing
for dependencies, does not count towards the directories a commit spans, and its lines are
left out of line share like lockfile lines. A commit whose changes are all version bumps,
apart from docs (a changelog), lockfiles and generated files, weighs 0.03, like a lockfile
bump, so a monorepo release no longer decays every unit. Adding, removing or swapping a
dependency is still a dependency change (+0.3). One `git log -p -U0` pass, restricted to
manifest and version files, reads the diffs; the parsed-commit cache keeps the result.

**A major upgrade is a dependency change.** A pin whose version moves to a new major
version is not a version bump: `"left-pad": "1.5.0"` to `"2.0.0"`, `"^17.0.2"` to
`"^18.2.0"`, `pydantic>=1.10` to `>=2.0`, `gem "rails", "~> 6.1"` to `"~> 7.0"`, or an upper
bound that lets one in (`<2.0.0` to `<3.0.0`) counts as "dependencies changed" (+0.3) and
stays in line share, because an upgrade that breaks things is a decision someone must
understand even when no code changed with it. The major is the first number, or the first
two below 1.0 (`0.23` to `0.30` is major, since 0.x minors break). A go.mod module path that
moves from `/v2` to `/v3` was already a different line and still counts. Patch and minor
upgrades (`1.4.2` to `1.5.0`) stay version bumps, and so do the project's own version lines
and pins moved to the version the same diff gives the project (a monorepo's major release
pinning its own packages). Calendar versions (a year first, `certifi==2023.7.22` to
`2024.2.2`) and versions that are not numbers (`latest`, a git sha, `1.x`) keep the
behaviour above. A dependency with a TOML table of its own (`[dependencies.serde]` then
`version = "1.0"`, in Cargo also `[dev-dependencies.x]`, `[target.'cfg(unix)'.dependencies.x]`
and `[workspace.dependencies.x]`, in Poetry `[tool.poetry.dependencies.x]`) is a pin like any
other: the diff pass asks git to name the enclosing table in each hunk header (a throwaway
attributes file giving Cargo.toml and pyproject.toml a diff driver whose function lines are
TOML tables), and a `version =` line inside a dependency's table is read as that
dependency's version, so `1.0` to `2.0` there is a major upgrade. A repository whose own
.gitattributes gives those files a diff driver overrides this, and the table form is missed.

**A merged branch is weighed as one commit.** A repository that squashes every pull request
lands it as one commit; one that merges keeps the branch's commits, and weighed one by one
ten 20-line commits would carry about 3.9 against 0.61 for the same 200 lines squashed. So
the commits a merge brings in from its branch (the first-parent chain of its second parent,
back to history already merged) form a group: the group is weighed once as if squashed
(combined files and effective lines, the pull request title as the subject), and that weight
is shared among its commits in proportion to their effective lines outside lockfiles and
generated files. Each commit then lands its part in units by its own line share as usual. A
pull request merged into a long-lived branch is its own group; "merge main into my branch"
brings in nothing; commits on the main line (first parents from HEAD) are weighed alone, and
so are branches longer than `group_max_commits` (default 50), which are lines of work rather
than pull requests. `udebt risk` on an unmerged branch still weighs its commits one by one. When a commit touches several units, each unit receives the commit's
weight times the square root of the share of changed lines that fell in that unit. The
square root is deliberate: a unit that got a tenth of a large architectural commit carries
about a third of its debt, because a new dependency or a changed model spills past the lines
it touched there. A commit confined to one unit lands its full weight. Lockfile and generated
lines do not count towards the share: a 2000-line `package-lock.json` in a backend refactor
says nothing about where the thinking went. A file that several units list (a `main.py`
every flow passes through) is split evenly among them: in four units, it gives each a
quarter of its lines. The split is first among the top-level units that list the file, then
a parent's part among those of its sub-units that list it, and every mass below uses it.

Everything below that used to be a commit count is now a sum of weights ("mass"). **Bot
commits land no mass at all**: automation neither erodes anyone's understanding nor builds
anyone's footprint, so it is left out of churn, decay, the prior, risk, the co-change graph
and the denominator of every share (`ai%` and `you%` in `status` are shares of the non-bot
mass).

For each architectural unit `u` and developer `d`:

| quantity | definition |
|---|---|
| evidence of one answer | correct 1.0, partial 0.5, wrong 0.0, unknown 0.0, **confidently wrong -0.5** |
| verified understanding | mean evidence over the last 5 answers on `u`, clamped to [0, 1] |
| understanding | verified x exp(-1.386 x share of `u` changed since the last answer): it halves for every half of the unit that changes (config `share_decay_rate`, ln 4) |
| share of `u` changed since t | weighted changed lines landed under `u` after t / size of `u` in lines; can pass 1 when code is rewritten more than once |
| weighted changed lines of a commit in `u` | its changed lines under `u`, counted as for line share (lockfile, generated and version-bump lines left out, a shared file split), times its line weight: the commit's weight, or for a commit a merged branch brought in, the whole branch's weight (at most 1); 0 for a bot |
| size of `u` | lines of the tracked files under `u` at the cut-off (HEAD for `status`), without lockfiles, generated, media or binary files, a shared file split as above; counted once per blob, cached in `.udebt/sizes.json` (gitignored) |
| understanding, mass model | verified x 0.9^(mass landed on `u` since the last answer): config `decay_model: "mass"` (`decay_per_commit`), the Phase 1 rule, kept to compare |
| line share of a commit in `u` | changed lines under `u` / all changed lines, lockfile, generated and version-bump lines left out; a file listed by k top-level units counts 1/k of its lines in each, and a parent's part is split again among the sub-units listing it |
| attribution of a commit to `u` | weight x sqrt(line share); 0 for a bot commit |
| prior (no quiz yet) | 0.5 x your weighted share of `u` x exp(-1.386 x share of `u` others changed since your last commit) (mass model: 0.9^(mass others landed since)) |
| importance | 0.6 x centrality/5 + 0.4 x mass/max mass, centrality rated 1-5 by the mapper |
| co-change edge (a, b) | sum over commits touching both, with at most 30 files, of weight / (files - 1) |
| import edge (a, b) | 1 per direction in which a imports b; scaled so imports and co-change carry equal total weight |
| candidate units | Louvain communities of the merged graph (seeded), sub-clusters for those of 8+ files |
| sub-unit importance | the parent's importance split among its sub-units in proportion to the same centrality/mass blend among siblings, so they sum to the parent's |
| sub-unit understanding | its own quiz evidence; without it, max(own-commit prior, 0.5 x the parent's quiz understanding) |
| parent understanding | max(its own, importance-weighted mean of its sub-units' own understanding), the mean only once a sub-unit has quiz evidence |
| debt(u) | importance x (1 - understanding), architectural units only |
| debt index | 100 x sum(debt) / sum(importance), over top-level units (or over sub-units where they exist, with `--deep`) |
| responsibility set | CODEOWNERS: units where an owner owns >= 30% of the files; for a developer CODEOWNERS does not name: units where their share of the human-led mass is >= 10% (and >= 0.1), AI-authored commits left out; then curated |
| your debt index | the debt index over your responsibility set plus the units you are touching now (uncommitted or unpushed changes); over every unit when you have no set |
| bus target | every architectural unit should have >= `bus_target` (default 2) developers at understanding >= 0.6; the team view lists those below |
| risk of a change | per unit touched: mass landed vs your understanding; **OUTSIDE** if the unit is outside your set, mass >= 0.1 and understanding < 0.6; HIGH if mass >= 0.3 and understanding < 0.3; CAUTION if mass >= 0.1 and understanding < 0.6; the change's verdict is its worst unit |

Your share of a unit's commits counts AI-assisted commits at half weight, and so do agent
commits you directed (config `directed`, see Contributors): such a commit stays `ai` for
display and AI share, but it is your work for your share, your prior and your footprint, and
it does not erode your prior. Four deliberate choices:

- **Your own commits give you a weak prior, never more than 0.5.** Having typed the code is
  evidence of understanding, but half-credit at best, and it erodes as others (human or AI)
  commit on top. Any quiz answer, including "I don't know", replaces the prior.
- **Unmeasured units with no prior count as zero understanding.** A developer new to the
  repo starts at 100 and earns it down.
- **Saying "I don't know" is free; being confidently wrong costs.** A known unknown is cheap to
  fix. A misconception causes bugs.
- **Understanding decays when the code moves without you, by how much of the unit moved.**
  Every changed line landed on a unit after you last verified it counts, at its commit's
  weight, against the unit's size: once a tenth of the unit has changed you keep 87% of what
  you knew, a half 50%, all of it 25%. So debt grows as the AI keeps building, and the same
  change costs more in a small unit than in a large one. The Phase 1 rule (10 percent off
  per unit of weighted mass, whatever the unit's size) decayed a large repository's units
  far too fast and a small one's too slowly in the synthetic runs (ROADMAP Phase 5); it is
  still there as `decay_model: "mass"`.

Team view takes the maximum understanding across developers per unit and counts how many
developers clear the known threshold (default 0.6). That count is the unit's bus factor, and
the team view lists every architectural unit whose bus factor is below `bus_target` along
with who is responsible for it. Holders need a quiz score: the own-commit prior stops at 0.5,
below the threshold, so until someone takes a quiz the team view says "no quiz evidence yet"
and labels the holder column `quizzed` rather than reading as "nobody knows anything".

## Units and sub-units

`map.json` (version 3) holds up to 12 top-level units; a unit big enough to need it has
`children`, up to 6 sub-units with their own paths and ids of the form `parent/child`. The
default views (`status`, `quiz`, `risk`, `dig`) work at the top level; `--deep` or a unit
argument goes down a level, and a sub-unit's short id works where it is unambiguous. A quiz
on a parent is evidence about the parent's decision, so its sub-units only inherit half of
it; quizzes on sub-units roll up to the parent. In `risk --deep`, files under a parent that
no sub-unit covers appear as `<parent> (rest)`. Version 1 and 2 maps (flat) keep working,
and `map --rescore` leaves their version alone.

## Responsibility sets

A developer's set is what they are expected to understand. `udebt responsibility seed`
proposes one per human contributor (agents and bots get none):

- **CODEOWNERS** (`.github/CODEOWNERS`, `CODEOWNERS` or `docs/CODEOWNERS`, last matching
  rule wins as on GitHub): an owner of at least 30% of a unit's files is responsible for it.
  Emails map directly; `@handles` map through `handles` in `.udebt/config.json`, or through
  a GitHub noreply address (`123+handle@users.noreply.github.com`) in history. A handle with
  no known email is kept as pending, and seeding adds it to `handles` with a null value for
  the owner to fill in.
- **Footprint**, for developers CODEOWNERS does not name: units where their share of the
  human-led work is at least `footprint_threshold` (default 0.1). AI-authored commits count
  for nobody, so a unit only agents built is in nobody's set until a lead assigns it.

Where a developer does not qualify for a whole unit, seeding tries its sub-units. A lead
curates with `add` and `remove`; that marks the set as curated, so a later `seed` leaves it
alone (`seed --force` re-seeds it and keeps the units added by hand).

The same file holds **per-developer scope**. `udebt scope <unit> blackbox` marks a unit out
of scope for you only; `--for <dev>` for someone else; `--team` for everyone (the map's
`scope`). A developer's own choice beats the team's, in both directions, and a team-wide
blackbox parent makes its sub-units blackbox. Status notes say which one applies.

`udebt risk` reads the sets: landing real weight in a unit outside your set that you do not
understand is **OUTSIDE**, the strongest signal udebt gives, because nobody expected you to
know that unit and nothing shows you do. The message names who has it in their set.

## Structural discovery

Before the model sees the repository, `udebt` proposes candidate units from the code itself
(`udebt clusters` shows them without calling the model):

1. **Import graph.** Python files are parsed with `ast`; JS and TS files by a regex over
   `import`, `export ... from`, `require` and dynamic `import()`. An import that resolves to a
   tracked file is an edge; anything else is an external package, kept to describe the
   cluster. Each language is one function registered with `@language(...)` in
   `udebt/imports.py`, so C++, Rust and Java are one function each.
2. **Co-change graph**, for every language: two files are linked by each commit that changed
   both, by the commit's weight divided by (files in the commit - 1). Commits touching more
   than `cochange_max_files` (default 30) files are reformats or bulk moves and are ignored.
3. **Communities.** The two graphs are merged with equal total weight and partitioned by
   Louvain (implemented in `udebt/graph.py`, stdlib only, seeded so the result is
   deterministic). Clusters of eight or more files are partitioned again on their own
   subgraph to give sub-clusters.
4. Only source files are clustered. Docs, config and data files would glue unrelated code
   together, so each is attached afterwards to the cluster it changes with most; files that
   change with nearly everything (journals, changelogs) are listed as hubs and left out.

The map prompt lists each cluster with a name candidate (its main directory and frequent
file-name words), its heaviest files, the packages it imports and the clusters it imports
from. The model names the clusters, merges and splits them, and adds the cross-cutting
decisions no cluster captures (auth model, persistence, error policy). On a 1900-file,
2500-commit repository the graph work takes about four seconds, most of it parsing Python.

## History mining

The mapper links each unit to the commits that shaped it, choosing from at most 40 decision
candidates that `udebt` picks from history without the model (`udebt history --candidates`
lists them, `--json` for the full records):

1. **Score.** A commit's score starts from its commit weight (above: size, dependency and
   infrastructure changes, new code files, spread; the LLM class where `weigh --refine`
   set one). A commit a merged branch brought in is ranked by its weight on its own, before
   the branch's weight is shared out, since a decision is ranked by what the commit did.
   Then the bonuses that make a commit a decision rather than just a big change: +0.3 for a
   new top-level directory, +0.2 for renames or moves, +0.1 per decision keyword in the
   subject (`migrate`, `replace`, `split`, `async`, `schema`, ... at most two). A commit
   whose weight plus bonuses is under 0.5 is not a candidate; bots and merges never are.
2. **Survival.** That sum is multiplied by the share of the commit's files that still
   exist at HEAD, directly or under the name a later rename gave them (rename pairs are
   kept in the parsed-commit cache). The mapper can only link a commit to code that is
   still there: a subproject deleted years ago, however busy it was, says nothing about the
   units of today. A commit none of whose files survive is dropped, except a founding
   commit (the first three), which adds +0.5 after the multiplication.
3. **Spread.** History is cut into `history_periods` (default 4) periods of equal weighted
   mass, oldest first, and each is guaranteed `history_quota` (default 10) of the slots,
   filled by its best scores; slots a period cannot fill, and any a smaller quota leaves
   open, go to the best scores anywhere. Equal mass rather than equal time: a busy recent
   year gets more periods than a quiet early one, and a long pre-AI past cannot crowd out
   the era the agents built. With 4 x 10 of 40 slots, slots are proportional to mass: the
   larger, fully surviving commits of the AI era cannot crowd out the middle periods either
   (with 5 per period they took 22 of 40 slots on huggingface_hub).
4. **Ties** go to the more recent commit.

Each candidate carries its sha, date, class, agent, author, subject, file count, score,
reasons, up to eight sample files (those still alive first), its `survival`, its `weight`
and its `period`. On huggingface_hub, where the old count-based miner gave 34 of 40 slots
to 2021-2022 and 22 to commits with no surviving file, the periods now get 10 each, the last
12 months 16 slots (25 with 5 per period) and no candidate is gone from HEAD (TESTBEDS.md).

## Contributors

`udebt contributors` merges identities (through `.mailmap` and `udebt alias`) and labels
each one. **AI**, **Bot** and **Joint human-AI** come from the commit classes: an identity
whose commits are `ai` (agent author identities), `bot` (bot signatures, below) or carry
agent trailers on a material share of commits. A contributor labelled Bot is exactly one
whose commits are left out of every mass. **Most likely AI** and **Most likely human** come
from commit metadata only: burst cadence, long bullet-list bodies, uniformly conventional
subjects. Those two are advisory and never feed the scores.

**Directed agent work.** Cloud sessions often commit as the agent itself, so a developer who
directed every line has no footprint and no prior. `udebt directed add <session>` (a
`session_...` id or a `https://claude.ai/code/session_...` link, matched against
`Claude-Session:` trailers) or `udebt directed add <agent>` (every commit by that agent that
no session claims; `--for <dev>` for someone else) records it in `.udebt/config.json`:

```json
{"directed": {"dev@example.com": {"sessions": ["session_01Ab..."], "agents": ["claude-code"]}}}
```

A matched commit stays `ai` in every count and AI share, and counts as that developer's
assisted work, at half weight, in their share, prior and responsibility footprint.

## Recognised AI agents

Every pattern matches case-insensitively. Built in (`udebt/provenance.py`,
`BUILTIN_SIGNATURES`):

| agent | authored by (class `ai`) | trailers and footers (class `assisted`) |
|---|---|---|
| `claude-code` | `noreply@anthropic.com`, `Claude`, `Claude Code`, the `claude[bot]` GitHub app | `Co-Authored-By: Claude ...`, `Claude-Session:`, `Generated with Claude Code` |
| `github-copilot` | `Copilot`, `copilot-swe-agent[bot]`, `*copilot*@users.noreply.github.com`; a body saying `🤖 Authored with GitHub Copilot on behalf of @user` | `Co-Authored-By: ... Copilot` |
| `cursor` | `@cursor.sh`, `@cursor.com`, `Cursor Agent`, `cursor[bot]` | `Co-Authored-By: ... Cursor` |
| `aider` | `... (aider)` | `Co-Authored-By: ... aider`, `aider:` |
| `openai-codex` | `codex*@openai.com`, `Codex`, `OpenAI Codex`, `chatgpt-codex-connector[bot]` | `Co-Authored-By: ... Codex` |
| `gemini` | `Gemini`, `Gemini CLI`, `Gemini Code Assist` | `Co-Authored-By: ... Gemini` |
| `devin` | `devin-ai-integration[bot]`, `Devin`, `Devin AI` | `Co-Authored-By: Devin ...` |
| `opencode` | `noreply@opencode.ai`, `opencode`, `opencode-agent[bot]` | `Co-Authored-By: ... opencode` |
| `jules` | `google-labs-jules[bot]` | `Co-Authored-By: ... google-labs-jules` |
| `amazon-q` | `amazon-q-developer[bot]` | `Co-Authored-By: ... Amazon Q` |

**Attribution trailers.** `Assisted-by:` (the Linux kernel, Fedora and LLVM convention),
`Generated-by:` and `Made-with:` count when their value names an AI: `Assisted-by: Claude
Code`, `Assisted-by: Claude:<model>` (kernel), `Assisted-by: Copilot`,
`Generated-by: Claude Opus ...`, `Made-with: Cursor`, and the
[agentic-commits](https://github.com/grushetsky/agentic-commits) form
`Assisted-by: <agent> <version> (<provider>/<model>)`. Each signature's `names` recognise
its agent; the agent named first wins (`Assisted-by: OpenCode with Claude Opus` is OpenCode),
and the `(provider/model)` part is the model, not the agent. A value that names no known
agent but does name an AI (a model family, a vendor, "LLM") maps to a generic agent named
after the trailer (`assisted-by`); a value naming a person (`Assisted-by: Jane Doe
<jane@example.org>`) or a script does not count.

**Bots** (`BUILTIN_BOTS`): dependabot, renovate, github-actions, semantic-release, any other
`*[bot]` GitHub app that no agent signature claims, and identities named like automation
(`bot`, `pipeline`, `automation`, `actions` as a whole word in the name or address). A bot
commit that carries an agent trailer is `ai`: automation committed an agent's work and no
human was in the loop.

Order: an agent author identity, then an "authored by an agent" body, then a bot identity,
then agent trailers, else `human`. Add your own in `.udebt/config.json`:

```json
{
  "ai_signatures": [
    {"name": "mybot", "author_emails": ["^bot@corp\\.com$"],
     "trailer_patterns": ["(?m)^Co-Authored-By: MyBot"], "names": ["\\bmybot\\b"]}
  ],
  "bot_signatures": [
    {"name": "deploy", "author_emails": ["^deploy@corp\\.com$"]}
  ]
}
```

A commit matching `author_emails` or `author_names` (or `authored_patterns` in the body) is
`ai`; a human-authored commit matching `trailer_patterns`, or with an attribution trailer
one of the `names` recognises, is `assisted`. An identity that a broad bot rule catches but
that an agent really drives can be claimed back with an `ai_signatures` entry, since agent
signatures are checked first.

## Experiments

`udebt experiment synthetic` tests the model without human subjects. A synthetic developer
is the `claude` CLI taking the quiz with its knowledge frozen at an older snapshot:

```bash
udebt experiment synthetic --suggest 10,25,50          # snapshots that much mass before HEAD (no LLM)
udebt experiment synthetic --suggest mshare:0.3,0.7    # ... where the units' mean mass share since is that
udebt experiment synthetic --suggest share:0.3,0.7     # ... where their mean share changed is that
udebt experiment synthetic --snapshots <rev>,<rev>,<rev> [--per-unit 2] [--units N]
udebt experiment synthetic --at <rev>                  # one snapshot
udebt experiment synthetic --snapshots ... --resume .udebt/experiments/<report>.json
```

1. one question set is written from HEAD for every snapshot (one call), `--per-unit`
   questions per unit (default 2): the first on the unit's decision (why X rather than Y,
   what would have to change), the second on its current behaviour (what happens when X
   fails, which component decides Z). The prompt shows no commit history and forbids
   questions about what changed, so a unit that has not moved since a snapshot can be
   answered correctly from it;
2. per snapshot, the answers come from a temporary `git worktree` at `<rev>` with its `.git`
   link removed, the answerer's only working directory, and the prompt says the code it sees
   is the only code it knows (one call);
3. per snapshot, the answers are graded against HEAD, as the quiz grades them (one call).

So a run costs 1 + 2 x snapshots calls, and the snapshot is the only variable. Its stale
knowledge should go wrong where the code moved since. The report, in
`.udebt/experiments/synthetic-<sha>[_<sha>...]-<date>.json` (nothing goes to
`.udebt/records/`), gives per snapshot and per unit:

- the weighted mass landed since the snapshot (the same attribution as everywhere else),
  its share of the unit's mass, and the files at the snapshot and now;
- the share of the unit changed since (weighted changed lines over its size at HEAD, the
  decay model's input) and the score the model predicts for a developer who knew the unit
  at the snapshot (`predicted`, with the mass model's `predicted_mass` beside it); the
  summary compares their means and errors with the observed score;
- where the lines since went: the share in files that existed at the snapshot (`edited`)
  against files it lacked (`new`), which classes the unit `unchanged`, `mostly-edited` or
  `mostly-new`; edits to code a developer saw are where misconceptions come from, new code
  is where known gaps come from;
- each question, answer, confidence, verdict and feedback; the mean score (correct 1,
  partial 0.5, wrong and unknown 0), and the confidently wrong answers (confidence 3);
- the Spearman correlation between mass since and wrongness (1 - mean score), with a
  one-sided permutation p-value, also against udebt's own evidence (confidently wrong -0.5)
  and against the share since; and the same split by kind of change.

Across snapshots it adds a pooled correlation (not independent: the same questions) and a
paired count: for the same unit at two snapshots, how often the one with more mass since
scored worse. A positive, significant correlation is what decay predicts. `--units N` takes
the N most important top-level units (default all). The report is written after every call
and counts each call before making it, so a run that stops part way (a model error, say) can
be finished with `--resume <report>` without paying again for what it already has.

## Development

```bash
ruff check . && ruff format --check .
pytest -q
UDEBT_LLM=fake udebt --repo /path/to/repo map   # offline, canned answers
```
