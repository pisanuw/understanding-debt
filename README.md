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
5. **quiz** a developer up a ladder of questions, graded against the code: what each unit is
   for first, after a short orientation, then the decision that shaped it, what reversing it
   would change across units, and only then failure modes and implementation detail;
6. **score** debt per unit and as a 0-100 index over a developer's set, and for the team
   against a bus-factor target, with a weak prior from the developer's own commits so the
   view is useful before the first quiz;
7. **flag** changes that land weight in units the author does not understand, most strongly
   outside their responsibility set, and **brief** a developer on what landed in those units
   since they last showed what they know there (`udebt digest`);
8. **teach** the architecture, not only test it: a ten-minute tour of the whole system, a
   learning path in dependency order, walkthroughs that trace one real request through a
   unit's code, the answer shown after a miss, spaced review of what was missed, teach-back
   (explain a unit, hear what was right and what was missing), opt-in decision records, and
   all of it in the browser (`udebt serve`);
9. **replay** all of it over the history: debt per developer and unit at every week (or N
   units of mass), the risk events of the past, an export for the hosted version and a
   static dashboard;
10. **prepare the hosted twin** (Phase 4a): a versioned JSON contract, an analysis worker
    over a transient clone, a Supabase schema with own-row security, `udebt push` and
    `udebt pull`, and the pull-request check run with its webhook router.

## Install

```bash
uv venv && . .venv/bin/activate
uv pip install -e ".[dev]"
```

Requires Python 3.11+, git, and the [`claude` CLI](https://docs.anthropic.com/en/docs/claude-code)
on your PATH and logged in.

Every `claude` call udebt makes runs inside the analysed repository with only your own
settings (`--setting-sources user`), no MCP server (`--strict-mcp-config`) and exactly the
tools udebt names (`--tools Read Glob Grep` on a call that reads code, none otherwise), so the
repository's `.claude/settings.json` hooks and `.mcp.json` never run. Before this, a clone's
SessionStart hook installed and built the repository on every call (TESTBEDS.md).

## Use

```bash
cd your-repo
udebt init                    # creates .udebt/
udebt alias other@me.org       # other addresses you have committed under (optional)
udebt directed add <session>   # agent sessions (or a whole agent) whose commits you directed
udebt contributors             # everyone who committed: AI, joint human-AI, human, bot, "most likely"
udebt weigh [--refine]         # how much debt each commit can create; --refine asks claude about the heaviest
udebt clusters                 # candidate units from imports and co-change, no LLM
udebt map [--no-retry]         # clusters + history -> claude names units and sub-units -> .udebt/map.json
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
udebt quiz -n 5 [--deep]       # five questions from your set, each at your level on the ladder
udebt quiz --level N           # force ladder level N (1-5) for every question, for experiments
udebt quiz --preview           # print the orientations and questions as Markdown; record nothing
udebt tour [--refresh]         # a ten-minute architecture overview, one call, cached for the team
udebt path [--all]             # your learning path: dependency order, minutes, next action per unit
udebt dig [unit] [--refresh]   # one real request traced through the unit's code, then a check
udebt review [-n 5] [--all]    # spaced review of the questions you missed, in other words
udebt teachback <unit>         # explain a unit; right, missing, misconception; graded at your level
udebt digest [--since R|DATE] [--unit U]   # what landed in the units you know least, as a briefing
udebt decisions [write] [--dir docs/decisions]   # one decision record per top-level unit (opt-in)
udebt serve [--port N] [--open]   # all of it in the browser on 127.0.0.1, with a tutor
udebt risk [range|--staged]    # weight a change lands in each unit vs your understanding; --deep per sub-unit
udebt hook install             # warn-only pre-push hook that runs `udebt risk`
udebt scope <unit> blackbox    # "I choose not to understand this", for you (or --for <dev>)
udebt scope <unit> blackbox --team   # for everyone: stored in the map
udebt experiment synthetic --snapshots <rev>,... [--per-unit 2] [--level 3]   # validation: synthetic developers
udebt cache [status|rebuild]   # the parsed-commit cache (gitignored)
udebt snapshot [--every week|day|mass:N] [--rebuild]   # replay debt at past points -> .udebt/snapshots.json
udebt timeline [--dev X] [--unit U] [--format table|csv|json]   # the series and its risk events
udebt risk --history [--since DATE] [--all]   # every past change a developer landed, against their understanding then
udebt export [--format csv|json] [--out PATH] [--with-answers]   # full history for the hosted import
udebt dashboard [--out .udebt/dashboard/index.html] [--open]    # one static, self-contained HTML page
udebt validate <file> [--kind K]   # check a JSON file against the versioned contract
udebt worker run <url> [--rev SHA] --out DIR [--map F|--llm]   # analyse a transient clone
udebt worker sql DIR [--dialect postgres|sqlite] [--user-id U]   # an idempotent load of it
udebt remote set <url>; udebt remote token   # the hosted store; the token is read from stdin
udebt push [--dry-run]; udebt pull           # the export out, hosted evidence back
udebt risk <range> --check-run [--soft-block] [--ack LOGIN]   # the pull-request check body
```

Commit everything under `.udebt/` except the parsed-commit cache, the line counts
(`sizes.json`), the export and dashboard screenshots, which `.udebt/.gitignore` keeps out;
`snapshots.json` (the replayed series) is committed like the map. The map and `responsibility.json` are shared, each `records/<developer>.jsonl`
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
| level of an answer | the question ladder's level it was asked at, 1 to 5 (below); an answer recorded before the ladder counts as level 5, the implementation questions the old prompt asked |
| level value v_n | mean evidence of the latest sitting at level n on `u` (one quiz run's answers at that level, usually one), 0 with none: the most recent verdict per level counts |
| verified understanding | max(0, (v_1 + v_2 + v_3) / 3 + 0.1 x (v_4 + v_5)): levels 1-3 (the architecture) a third each, levels 4-5 (optional depth) 0.1 each, so at most 1.2 |
| level fade f_n | exp(-k_n x share of `u` changed since the last answer on `u`), k_n by ladder level (config `decay_k_by_level`, Phase 3.5): **k 0.2 at levels 1-3**, the architecture (a level 1-3 pass keeps 82% after a unit's worth of change, falls to 0.6 at 2.55 units' worth); **k 1.0 at levels 4-5**, implementation (37% left after a unit's worth, half at 69%). Fitted on the synthetic runs, levels 1-3 on huggingface_hub's level 3 question sets, 4-5 on every pre-ladder and level 5 run (ROADMAP Phase 5) |
| understanding | min(1, max(0, (v_1 f_1 + v_2 f_2 + v_3 f_3) / 3 + 0.1 x (v_4 f_4 + v_5 f_5))): each level's value fades at its own rate, then the levels combine as in verified, so a level-3 pass outlasts a level-5 pass on the same change. Passing levels 1-3 is full understanding; depth above 1 is headroom against the fade (a unit known to level 5 reads 1.0 until 59% of it has changed) and can lift a faded understanding back over the known threshold, but is never needed to reach it. With one k for every level this is min(1, verified x exp(-k x share)), the rule before Phase 3.5 |
| one k for every level | `decay_k` (default 1.0) applies to every level when the config has no `decay_k_by_level` and sets `decay_k` to something other than 1.0 (the single rate every config written from Phase 3.1 to 3.4 carries, read as not chosen); `decay_k_by_level` with all five levels at one k does the same for any k. Levels a `decay_k_by_level` leaves out keep the defaults |
| depth | the highest level n with every level 1..n passed (its latest sitting all correct); 0 when level 1 is not |
| basis | `measured` when the understanding comes from quiz answers on `u` (or on its sub-units), `estimated` when from the own-commit prior or half the parent's quiz, `none` without either; it changes no number |
| next level on `u` | depth + 1, at most 5; one lower when the latest sitting was confidently wrong at level 2 or more |
| verified, flat model | mean evidence over the last 5 answers on `u`, clamped to [0, 1], levels ignored, faded at level 5's k: config `evidence_model: "flat"` (`evidence_window`), the rule before the ladder |
| share of `u` changed since t | weighted changed lines landed under `u` after t / size of `u` in lines; can pass 1 when code is rewritten more than once, though no single commit adds more than the unit's size |
| weighted changed lines of a commit in `u` | its changed lines under `u` (insertions plus deletions; a deleted file counts its size once), counted as for line share (lockfile, generated and version-bump lines left out, a shared file split), **at most the size of `u` just after the commit**, times its line weight: the commit's weight, or for a commit a merged branch brought in, the whole branch's weight (at most 1); 0 for a bot |
| size of `u` just after a commit | the running sum of every earlier commit's insertions minus deletions under `u` (the same files and split as the size), in history order: no tree is read; at HEAD it matches the blob count to a few lines on the testbeds |
| size of `u` | lines of the tracked files under `u` at the cut-off (HEAD for `status`), without lockfiles, generated, media or binary files, a shared file split as above; counted once per blob, cached in `.udebt/sizes.json` (gitignored) |
| understanding, mass model | verified x 0.9^(mass landed on `u` since the last answer), the same at every level: config `decay_model: "mass"` (`decay_per_commit`), the Phase 1 rule, kept to compare |
| line share of a commit in `u` | changed lines under `u` / all changed lines, lockfile, generated and version-bump lines left out; a file listed by k top-level units counts 1/k of its lines in each, and a parent's part is split again among the sub-units listing it |
| attribution of a commit to `u` | weight x sqrt(line share); 0 for a bot commit |
| prior (no quiz yet) | 0.5 x your weighted share of `u` x exp(-k_5 x share of `u` others changed since your last commit), at level 5's k: having typed the code is implementation-shaped knowledge (mass model: 0.9^(mass others landed since)) |
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
| risk of a change | per unit touched: mass landed vs your understanding; **OUTSIDE** if the unit is outside your set, mass >= 0.1 and understanding < 0.6; HIGH if mass >= 0.3 and understanding < 0.3; CAUTION if mass >= 0.1 and understanding < 0.6, unless that understanding is only the own-commit prior (which stops at 0.5): then the unit is named in one line, not flagged; the change's verdict is its worst unit |

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
- **Understanding decays when the code moves without you, by how much of the unit moved,
  and the code-level knowledge faster than the architecture.** Every changed line landed on a
  unit after you last verified it counts, at its commit's weight, against the unit's size. An
  answer about the code (levels 4-5, and the own-commit prior) fades fast: once a tenth of the
  unit has changed you keep 90% of it, a half 61%, all of it 37%. An answer about the
  architecture (levels 1-3: what the unit is for, the decision, what else depends on it)
  fades at a fifth of that rate: 98%, 90% and 82%, because a unit's role and its decisions
  outlast most rewrites of its code. In synthetic runs on huggingface_hub, level 3 answers
  from a snapshot nine months old scored 0.88 where level 5 answers scored 0.69, and the gap
  grew with the share changed (ROADMAP Phase 5). `udebt status` names the levels and rates
  behind each faded score ("quiz, decayed from 1.00 by share 0.52 at levels 1-3 (k 0.2)"), and
  so does the unit page in `udebt serve`. So debt grows as the AI keeps building, and the same
  change costs more in a small unit than in a large one. One commit counts at most the
  unit's size just after it: rewriting a unit counts it once (not its old plus its new
  lines), and deleting most of one (data files moved out of git) counts what is left, not
  the deleted lines against a size that no longer has them. `decay_floor` (default 0) keeps
  a part of the understanding however much changes, floor + (1 - floor) x exp(-k x share);
  the refit tried it and kept the plain curve (ROADMAP Phase 5). The Phase 1 rule (10 percent off
  per unit of weighted mass, whatever the unit's size) decayed a large repository's units
  far too fast and a small one's too slowly in the synthetic runs (ROADMAP Phase 5); it is
  still there as `decay_model: "mass"`.

Team view takes the maximum understanding across developers per unit and counts how many
developers clear the known threshold (default 0.6). That count is the unit's bus factor, and
the team view lists every architectural unit whose bus factor is below `bus_target` along
with who is responsible for it. Holders need a quiz score: the own-commit prior stops at 0.5,
below the threshold, so until someone takes a quiz the team view says "no quiz evidence yet"
and labels the holder column `quizzed` rather than reading as "nobody knows anything".

## The question ladder

The first real quiz on digitalme asked only implementation questions ("if you deleted the
`except (asyncio.CancelledError, GeneratorExit)` block in `event_stream()` ...") and the
developer got none right: "I would need to understand the overall architecture better before I
could understand how the API key is used." Asked for consequences, the model finds the most
specific consequence it can. So every question now sits on a ladder, and a developer climbs it
unit by unit:

| level | asks | the question may name |
|---|---|---|
| 1 purpose and placement | what the unit is for, or one consequence of its place in the system, answerable in a sentence | nothing from the code: components by role, in plain language |
| 2 decision and alternatives | what was chosen, what was rejected, what constraint forced it | nothing from the code |
| 3 consequence across units | what else changes if the decision were reversed or replaced | nothing from the code |
| 4 behaviour under failure | module-level edge cases and failure modes | file or component names, no code |
| 5 implementation | code paths, specific functions (the questions before the ladder) | anything |

- **Everyone starts each unit at level 1.** A correct answer at level n unlocks n + 1 on that
  unit for the next quiz; partial, wrong or unknown asks level n again (another question); a
  confidently wrong answer at level 2 or more asks n - 1 first, since a misconception about a
  decision says the picture of the unit's role may be wrong too. With `--per-unit N`, a level
  passes when all N answers of the run are correct.
- **The level is derived from the records**, never kept apart: every answer is recorded with
  its `level` (and `forced` when `--level` chose it). Because each level needs the ones before
  it, an answer from before the ladder (level 5) or at a forced level never moves anyone past
  a level they have not shown; both still count in the score at their level.
- **Levels 1-3 are the understanding** (the table above): a third each, the latest answer
  per level counting, each faded at its level's rate (k 0.2 at levels 1-3, 1.0 at 4-5, since
  Phase 3.5). Levels 4 and 5 are depth: 0.1 each on top, so a developer who knows a unit down
  to its code keeps 1.0 through the first changes, and depth can lift a faded understanding
  back over the threshold, but nobody needs it to get there. A
  confidently wrong answer at any level is still negative evidence (-0.5 for that level).
- **Level 1-3 questions name no function, variable, file or code block.** The question prompt
  says so for each level, and lets them name an external technology the decision is about (a
  database, a hosting platform, a model provider). The grader is told the level too: at levels
  1-3 a correct plain-language answer is correct without any identifier, and partial means the
  role or decision is right but a dependency or a consequence is missing.
  `udebt quiz --preview` checks the questions it prints with a heuristic for code-like tokens.
- **One question, one fact** (Phase 3.3). Every question has one part: one question mark,
  nothing joined on with "and what", "and which" or "; ", no second sentence after it, and at
  levels 1-3 no inventory ("list", "which other parts ...", "name the ..."). The owner's first
  level 1 quiz on digitalme asked five compound inventory questions and he could answer none.
  The question writer returns two more phrasings of the same fact with each question
  (`variants`); a compound first phrasing is replaced by the first variant that asks one fact,
  else trimmed to its first clause, in the same call (`ladder.compound`, `first_clause`).
  Reference answers at levels 1-3 are at most two plain sentences, because the developer
  reads them.
- **A miss teaches.** At levels 1 and 2 a blank answer prints the reference answer at once and
  a wrong or partial one prints it after grading (partial since Phase 3.4: its feedback names
  what was missing, the reference shows the whole answer); the record says `revealed: true` and keeps the
  question's variants. It counts as the miss it was (unknown or wrong evidence, no penalty for
  the reveal), the next sitting at that level asks the same fact in other words (the question
  writer is given the revealed questions), and `udebt review` brings it back the next day.
- **An orientation comes first.** Before every level 1 question on a unit, until the developer
  passes level 1 there, the quiz prints a short orientation: what the unit is for, the decision
  with its alternatives and why, the two or three paths to open first, and who shaped it (from
  the unit's `history`). The model writes it once per unit and it is cached on the unit in
  `map.json` (`orientation`, shared by the team, rewritten when `udebt map` changes the unit);
  a quiz run asks for every missing one in one call before the questions call, and the
  question writer sees it, so the level 1 question asks what follows from it rather than what
  it says. `udebt dig` opens with the same orientation.
- **`udebt status` shows depth** next to understanding (`1/5`, `-` with no answer), and so do
  `udebt timeline --dev` (under the table) and `--unit` (a column per developer), the
  dashboard's unit table and breakdown, and the export (`depth` per unit and point; each
  answer's `level`, null for one recorded before the ladder).
- **`udebt quiz --level N`** asks every question at level N, the old flat behaviour at 5, for
  experiments; the synthetic experiment always passes one (`--level`, default 3), so its runs
  stay comparable. `evidence_model: "flat"` in config scores the old way, the mean of the last 5 answers.

A quiz run costs one call for the questions, one for the grades and, on units met for the
first time, one for their orientations.

## Teaching

The quiz measures; these commands teach. Each shares the quiz's machinery (one-fact
questions, the answer shown after a miss at levels 1-2, records written the same way) and
none costs more than one call; what the model writes for the team is cached in `map.json`.

- **`udebt tour`**: the architecture as one ten-minute read, before any question. The units
  come in learning order (each after the units it uses) with a map of their connections: a
  unit *uses* another when its files import the other's, and they *change together* by the
  commits that touch both (`udebt/unitgraph.py`, no model call; a file two units list is split
  between them, and a file more than two units list connects nothing, Phase 3.4: a hub file
  every flow passes through says only that they share an entry point. It still counts, split,
  wherever mass is attributed. On digitalme, `backend/main.py` (five units) and the frontend's
  `netlify.toml` are left out: import edges strong enough to order the path 30 to 20, all
  import edges 47 to 30, co-change pairs 64 to 59 of 66). One
  `TASK: tour` call writes an overview and a paragraph per unit: purpose, placement among its
  connections, the decision, the alternative not chosen, who shaped it and when. Cached in
  `map.json` (`tour`) with a basis hash of the units and their order; `--refresh` rewrites it.
  `udebt quiz` and `udebt path` point to it while nothing of yours is measured. digitalme's:
  12 units, 2,255 words, about 11 minutes, one call of 31 s.
- **`udebt path`**: what to learn next. Units are taken by importance x debt and each one's
  prerequisites (the units it uses) are placed before it, so the foundations of what matters
  come first and a unit nothing uses comes when its priority says; a cycle is broken where
  the walk meets it. Per unit: understanding, basis, depth, minutes (1 for the orientation and
  4 for the walkthrough on first contact, 2 per ladder level 1-3 still to pass, 1 per review
  due, 3 for a re-check after it faded) and the next action, plus the commands that fit a
  ten-minute session (`--minutes N`). `--format json` for scripts.
- **`udebt dig <unit>`** shows the code. One `TASK: walkthrough` call traces one real request
  through the unit in three to six steps, each a file and a line range with narration; `dig`
  prints the orientation, then each step's narration beside the actual lines from the working
  tree, numbered, at most 25 per step (tracked files only: an untracked or ignored file is
  never read), then the misconception most likely, then a check question at your ladder level.
  The check rotates (Phase 3.4): a variant of a question you missed and saw the answer to,
  else the walkthrough's own check for that level if you have not had it, else a question
  from the unit's pool at that level you have not had (the quiz's questions and every
  walkthrough's checks, which join the pool when written); only when you have had them all
  does one call write a new one, told what you were asked so it says it in other words. Cached on the unit
  (`walkthrough`) with the head, the unit's basis and a hash of its files' contents; any change
  to a file of the unit, committed or not, rewrites it on the next `dig`.
- **`udebt review`**: about five minutes of the questions you missed, SM-2 style, one item per
  unit and level, folded from the records (`udebt/review.py`; the state after each answer is
  also written on the record as `review`). It asks a stored phrasing you were not asked last
  time, with its stored reference, so a review costs one grading call; a review answer is
  ordinary evidence, so a correct one passes the level.

  | | |
  |---|---|
  | an item starts | on a miss (partial, wrong, unknown, revealed or not) at a unit and level |
  | quality | correct 5, 4, 3 by confidence 3, 2, 1; partial 2; wrong or unknown 1; confidently wrong 0 |
  | quality < 3 | repetitions 0, next in 1 day, ease - 0.2 (at least 1.3) |
  | quality >= 3 | next in 1 day, then 3 days, then the last interval x ease; ease + 0.1 - (5 - q)(0.08 + 0.02(5 - q)), from 2.5 |

- **`udebt teachback <unit>`**: you explain the unit; one call says what was right, what was
  missing and one misconception if there is one, and grades it at your current ladder level on
  the unit (Phase 3.4; level 1 with no evidence there): the explanation asked for grows with
  the level (what it is for and where it sits; then the decision and what was not chosen; then
  what reversing it would change; then failure behaviour; then how the code does it), and a
  pass moves the ladder like a quiz answer. Recorded with `type: teachback`; a miss at levels
  1-2 (partial included) shows the reference answer.
- **`udebt decisions write [--dir docs/decisions]`**: opt-in, nothing writes them otherwise.
  One short decision record per top-level unit (context, decision, alternatives not chosen,
  the commits that shaped it and who made them, where it lives) and an index, from the map
  with no model call; `udebt decisions` alone shows what would be written. Each has a **Last
  confirmed by** line: the developer who most recently passed level 2 on the unit; once the
  records exist (`decisions_dir` in config), every level 2 pass refreshes that line and leaves
  the rest of the file alone. **These files describe internal architecture, the alternatives
  rejected and who made each call: they may not belong in a public repository.**
- **Estimated or measured.** `udebt status` has a `basis` column (`measured`: your quiz
  answers, or your sub-units'; `estimated`: the own-commit prior, at most 0.5, or half the
  parent's quiz; `none`) and counts under the index; `udebt timeline` prints the basis at the
  latest point (and per point in `--dev` CSV and JSON); the export adds `basis` per developer
  to each unit and a `basis` column to the snapshots table, derived from each point's
  `quizzed` units (the stored series is unchanged); the dashboard marks an estimated
  understanding `est.`. When a quiz replaces estimates with measurements, it says so in one
  line ("Debt index X -> Y: <units> were estimated from your own commits (a prior of at most
  0.5) and now rest on your answers"), since an honest "I don't know" can raise the index the
  prior was holding down.

### In the browser: `udebt serve`

`udebt serve` starts a local server (127.0.0.1, a free port unless `--port`; `--open` opens
it) and prints a URL with a token that is new each run. Stdlib only (`http.server`), the
page hand-written under `udebt/static/`, no CDN, light and dark, laid out for 390 px phones.
Tabs:

- **Dashboard**: the Phase 3 dashboard page, which fetches `/api/dashboard` when its data
  blob is empty (a stale stored series is replayed in memory; nothing is written).
- **Tour** and **Path**, as above; the tour is written by a button when the map has none.
- **Unit**: understanding with its basis (measured or estimated), depth and next level; the
  orientation and connections; the walkthrough, each step beside its real lines (fetched
  from `/api/excerpt`, numbered); the decision record; your answers and review items.
- **Quiz** and **Review**: free text, graded with the CLI's code and recorded exactly as the
  CLI records it. A sitting is graded in one call at its end, or at "Finish now" (Phase 3.4;
  before, every answer was its own call and its own sitting): "I don't know" needs no call
  and at levels 1-2 shows the answer at once (that question then stays blank), a typed
  answer's verdict and, at levels 1-2 on a miss, its answer come with the batch. A quiz
  asks, per unit at your level, a stored variant of a question you just missed, else a
  question already generated for that unit and level (pooled in `map.json`, `questions`,
  by the CLI's quizzes too), and only then the model (one call for all new ones).
- **Tutor**: a conversation about one unit. Each turn is one `claude` CLI call: the first
  `--session-id <uuid> --append-system-prompt <socratic>`, later ones `--resume <uuid>`, both
  with `--tools Read Glob Grep --allowedTools Read Glob Grep` and session persistence on (the
  CLI keeps the transcript in its own store, not the repository). The tutor reads the code for
  you and explains it, asks back, and after four turns or "check me" proposes a check
  question at your level, whose reference is stripped from the page; your answer is graded
  and recorded (`source: tutor`). It shows the elapsed time and suggests stopping at ten
  minutes. Conversations persist (Phase 3.4): each turn saves the transcript, the `claude`
  session id, the level and any pending check question to `.udebt/tutor/<developer>/<unit>.json`
  (gitignored), so a reload or a server restart reattaches (`#tutor/<session>`), and opening
  the tutor on a unit with a saved conversation offers **Resume it** (the CLI session is
  resumed with `--resume`) or **Start over** (a new session replaces it). Back after more than
  half an hour, the ten-minute timer starts again.
- **Digest**: the change digest below, for your units under the threshold or one unit, from
  your last answer or a date or commit you type; a button writes the missing briefings (one
  call), every GET only reads the cache.
- **Teach-back**, as above, at your level on the unit.

**Reference answers are in the committed `map.json`**, with the pooled questions and the
walkthroughs' checks, so a teammate can read the answer to a question before being asked it.
That is accepted on purpose: the questions come in several phrasings (`variants`, a new
phrasing after a reveal, a rotating pool) and grading is against the code, not against the
reference's words, so what earns a pass is understanding the fact, not remembering a
sentence; and reading the answer to an architectural question is learning the architecture,
which is the point. A team that wants them out of git can gitignore `map.json`'s generated
caches by keeping the map elsewhere, at the cost of every developer paying for their own.

Generation (tour, walkthrough, questions) is a POST that fills the caches in `map.json`;
every GET only reads. Security: it binds 127.0.0.1 and refuses any other Host header (no
DNS rebinding); pages need `?t=<token>` and API calls an `X-Udebt-Token` header, compared in
constant time (another origin cannot set it without a CORS preflight, which is never
answered); POST bodies are JSON, at most 1 MB; `/api/excerpt` reads tracked files only;
every response has a same-origin Content-Security-Policy, `Referrer-Policy: no-referrer`
(the token is in the URL) and `no-store`; the request log never prints the token.

### Change digests: `udebt digest`

`udebt digest` is a briefing, not a warning: for each unit you understand below the threshold
(in your set, or every top-level unit with `--all`; one unit with `--unit`, whatever your
understanding), what landed there since you last showed what you know there:

- the range starts at the commit of your latest answer on the unit, or at `--since <rev|date>`
  (a date takes the last first-parent commit at or before it), or, with no evidence on the
  unit, `--days` (30) days before HEAD's commit date; it ends at HEAD;
- the commits of the range that touch the unit, with who made them (the agent, and who
  directed it, for an AI commit), their class, weight and the mass each landed in the unit;
  bots, which land none, are left out;
- one call for every unit of the run (`TASK: digest`, the heaviest 15 commits per unit, the
  range's diff stat and the first 160 diff lines under the unit's paths, the code readable with
  the read-only tools) writes per unit a headline, one to four changes in terms of the unit's
  decision with the commits that made them, whether the decision itself moved (`held`: the
  changes work within it; `extended`: it stands and now covers more; `changed`: it was
  replaced, reversed or relaxed) with one sentence on how, and where to look;
- it ends with the next step, `udebt dig <unit>` or `udebt quiz --unit <unit>`.

Briefings are cached per (unit, start sha, end sha) in `.udebt/digests/` (gitignored), so two
developers with the same range share one, and a unit where nothing landed costs no call.
`udebt risk` names the digest for every unit it flags ("Read the digest: `udebt digest --unit
X`"). On digitalme, `--since 2026-07-01` briefed 11 units (66 commits) in one call of 32 s:
six decisions held, five extended (`.udebt/samples/digitalme-digest.md`).

## Units and sub-units

`map.json` (version 3) holds up to 12 top-level units; a unit big enough to need it has
`children`, up to 6 sub-units with their own paths and ids of the form `parent/child`. The
default views (`status`, `quiz`, `risk`, `dig`) work at the top level; `--deep` or a unit
argument goes down a level, and a sub-unit's short id works where it is unambiguous. A quiz
on a parent is evidence about the parent's decision, so its sub-units only inherit half of
it; quizzes on sub-units roll up to the parent. In `risk --deep`, files under a parent that
no sub-unit covers appear as `<parent> (rest)`. Version 1 and 2 maps (flat) keep working,
and `map --rescore` leaves their version alone.

**Paths are checked before anything is scored.** The prompt says every path is relative to
the repository root and shows two taken from the files (a source file and a directory, with
their leading `src/` or package directory). Each path the model returns, for a unit or a
sub-unit, is kept when it names a tracked file or a directory that holds tracked files;
otherwise it is replaced by the one tracked file or directory whose path ends in it (a
package-relative `hf_api.py` becomes `src/huggingface_hub/hf_api.py`); otherwise, when
nothing or several things end in it (`__init__.py`), it is dropped and listed under the
unit's `unresolved`. `map.json` records how many resolved each way (`paths`). If the units
then cover under half of the clustered non-test source files, or a unit has no path left,
`udebt map` says so and asks once more, with the paths that did not resolve and the two
examples appended (`--no-retry` skips this); a map still short of that is written with
`warnings`, and `udebt status` prints them before anything else. The fix for a bad map is to
re-run `udebt map`, or to edit the paths in `map.json` and run `udebt map --rescore`.

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

## Time series and the dashboard

`udebt snapshot` replays the history. At each point along the first-parent line (weekly by
default: Mondays 00:00 UTC from the first commit's week, skipping a week in which neither a
commit nor evidence arrived; `--every day`, or `--every mass:N` for a point each time N
more units of weighted mass have landed; config `snapshot_every`) it scores every developer
with a footprint (0.1 of human-led mass), a responsibility set or evidence, on every
top-level unit, with the same code `status` uses, at an explicit cut-off:

- only the commits on the first-parent line by then; a branch lands when its merge does,
  and a point's time is its first-parent commit's author time (never going back);
- only the evidence recorded by then;
- unit sizes from that point's tree, for the share decay;
- **the units of the current map, replayed against the past.** Units are defined now and
  history is replayed against them: a unit no commit had touched yet does not exist at a
  point and stays out of the indexes; importance comes from the churn up to the point,
  centrality from today's map; responsibility sets and scope are today's.

Each point records the commits and mass so far by class (ai, assisted, human, and bot
commits' weight beside them), each existing unit's importance and size, and per developer
the understanding of each unit, its part of the debt index (the parts sum to the index)
and the index over their set (over every existing unit for a developer without one). The
series is kept in `.udebt/snapshots.json`, committed, one point per line; a run appends only
the points newer than the last, so it is idempotent and its diff is an append. A series made
with another map, decay model or rates, evidence window, evidence model, interval or scoring
rules (the ladder made them version 3, per-level decay version 4), or whose last point a
rewrite took off the first-parent line, is kept as it is with a note until `udebt snapshot
--rebuild`. The export's `settings.decay` names the single k (`rate`), the k of each level as
applied (`by_level`) and the prior's (`prior_rate`); the dashboard's header shows them.

**Risk events** are the "high-debt member landed a heavy change" moments: every commit a
developer authored (or directed, for an agent commit) weighing at least 0.1 is assessed by
`udebt risk`'s rule against their understanding at the step before it landed, with the
evidence recorded before its author time, and each unit at OUTSIDE, HIGH or CAUTION is an
event, except a CAUTION whose understanding is only the own-commit prior (a sole developer
would otherwise get one for nearly every commit: 308 of digitalme's 312 events). They are
stored with the series and extended the same way. `udebt risk --history`
lists OUTSIDE and HIGH, strongest first (`--all` adds CAUTION, `--since` cuts the start);
`udebt timeline` and the dashboard list them too.

`udebt timeline` prints the series: every developer's index per point; with `--dev`, that
developer's debt per unit; with `--unit`, every developer's understanding of one unit;
`--format csv` or `json` for scripts. `udebt export` writes the whole history in one JSON
file (`udebt-export` version 1) or a directory of CSV tables, by default under the
gitignored `.udebt/export/`: settings, units (importance, ai share, each developer's share
and understanding, holders, who is responsible), responsibility sets, contributors with
their labels, the snapshots, the evidence, the risk events and the commits weighing 0.5 or
more. Evidence goes without answer text (the answer, the grader's feedback and the
reference answer) unless `--with-answers`: the question text, verdict and confidence are
metadata, the answers are not. This is the file the hosted version imports (ROADMAP Phase 4).

`udebt dashboard` writes `.udebt/dashboard/index.html`: one file with its CSS and its
hand-written JavaScript inline, charts drawn as inline SVG, no CDN, no web font and no
request of any kind, so it opens offline and from `file://`. The data is the export (no
answers) in one `<script type="application/json" id="udebt-data">` tag; regenerate the file
or swap the blob. It shows:

- a line per developer of the debt index over time, at most eight (the eight with the most
  debt now, ties to more commits) plus one dashed line for the mean of the others; the
  Developer selector swaps any other developer onto the chart, in the eighth slot, and the
  page opens on the developer with most commits; a crosshair tooltip; click a point
  (or use the arrow keys) to see that developer's debt per unit at that point, as a
  segmented bar of the five units with most debt and the rest, and as a ranked list;
- markers for quiz and dig evidence on the developer's line, and two rugs under the time
  axis: the developer's risk events, and commits weighing 0.5 or more by anyone else;
- summary tiles, a contributors table with the labels from `udebt contributors`, a units
  table with ai%, you%, understanding, holders against the bus target and who is
  responsible, the risk events, and the whole series as a table;
- light and dark from `prefers-color-scheme`, one hue per contributor from a validated
  categorical palette in fixed order, status colours only for risk levels and the bus
  target and always with a label; it keeps to phone width (tables scroll in their cards).

On digitalme (190 commits) the weekly series has 12 points and 12 risk events (4 HIGH),
rebuilt cold in 0.6 s; the dashboard is 89 KB. On dsa-instructor (2,510 commits) 19 points
and 144 events (143 HIGH), 6.0 s cold, 276 KB. On huggingface_hub (2,920 commits, 378
developers with a footprint) every 25 units of mass gives 53 points and 1,383 events in
94 s; the series is 8.3 MB and the dashboard 9 MB, and the page renders without errors in
Chromium. This repository's own dashboard, `.udebt/dashboard/index.html`, is committed as
the example (every 2 units of mass, since its history spans two days).

## Hosted app: udebt.hintwise.org (Phase 4b)

udebt as a web app on the HintWise platform, for a developer who has to understand a codebase
they did not write (or that an AI wrote for them): sign in with Google or an email link (one
HintWise account), pick a repository, read its architecture (the units and sub-units, each
unit's decision, the alternatives and why, the commits that shaped it, an orientation, the
tour, walkthroughs that trace one request through the code), and climb the quiz ladder until
your understanding debt falls. Four public repositories are ready to start with
(huggingface_hub, sentry-javascript, mlflow and udebt itself, analysed in advance), and they need
no account (below); any other public GitHub repository can be added once signed in, at once if
someone already analysed it, otherwise analysed with your own Anthropic key. Quizzes run on the
HintWise key within a daily allowance, or on your own key.
Not deployed yet: `docs/DEPLOY.md` has the steps, `web/README.md` the code.

The scoring is the CLI's, ported to TypeScript and checked against it case by case: the
ladder's levels, a third of the understanding for each of levels 1-3, decay per level as a unit
changes between analysed commits (k 0.2 at levels 1-3, 1.0 at 4-5). You are a learner, not an
author: there is no own-commit prior, so every repository starts at a debt index of 100 and
your answers earn it down.

**Review.** A question you miss (wrong, partial or "I don't know", whether the answer was shown
or not) comes back in the Review tab the next day, and at growing intervals once you answer it
right (1 day, 3 days, then the last interval times an ease factor that a confident answer raises
and a miss lowers): the SM-2 queue of `udebt review`, one item per unit and level, ported to
TypeScript and checked against `udebt/review.py` case by case. An item is asked in one of its
stored phrasings that you were not asked last time, with the reference answer kept from the
miss, so a review sitting costs one grading call and none to write questions; a review answer
counts on the ladder like any other. The tab and the repository card show how many are due.

**Without signing in.** The samples work with no account (owner, 2026-11-15). The site serves
them as static JSON (`web/public/samples/`, derived from the load scripts in `samples/`), reads
their code from GitHub at each sample's pinned commit, and keeps your answers in this browser
only, one entry per sample in its local storage: debt, the review queue and the ladder work from
them as they do from the server. Quizzes come from questions written in advance for each unit at
levels 1-3 (below): you answer, read the reference answer and mark your own answer (correct,
partly right, wrong, or "I didn't know"); your confidence is fixed before the reference shows,
and your answer text is never sent or stored. Those answers are recorded as **self-checked**, and
the debt says so wherever it rests on them (the label beside the debt index, on the units in
the debt table and on each result); they count as you marked them. Levels 4-5 are not written in
advance, because they need code reading and a real grader. With your own Anthropic key (Settings)
the model writes and grades questions as for a signed-in person; the HintWise key needs an
account. A missed answer comes back in Review the next day and, with no model in use, is
self-checked there too. When you sign in, udebt asks once what to do with the answers this
browser keeps: copy them into your account (each keeps its provenance, so a self-checked answer
stays self-checked), keep them separate (they stay in the browser for when you are signed out),
or discard them; nothing happens without your choice, and the same answers are never offered
again. Settings has "Delete the answers kept in this browser". Signed in with model features
off, you get the same questions written in advance, self-checked.

**Questions written in advance: `udebt worker quizzes`.** The questions for visitors without an
account are written once per sample with the hosted quiz's own prompt (`udebt/hosted/advance.py`
ports `web/src/core/prompts.ts`, and a parity test keeps the two identical), from code excerpts at
the pinned commit, through the configured model (the `claude` CLI on a subscription, no tools):
one call per level and batch of five units, three questions per unit and level, each with two
other phrasings, its reference answer, the files it rests on and, at level 1, the orientation
shown before it. From the repository root, with a clone that holds the pinned commit:

```bash
udebt worker quizzes mlflow --clone <clone> --plan        # the calls it would make; none made
udebt worker quizzes mlflow --clone <clone> --out web/public/samples/mlflow.quizzes.json
python -m udebt.hosted.static_samples                      # lists the file in index.json
```

The source is a registered sample's name (its `samples/<name>.sql`), a worker output, a load
script or a rows file; `--levels` (within 1-3, default `1-3`), `--per` (1-3, default 3), `--batch`
(units per call, default 5) and `--model` change the defaults. Without `--clone` the files are
read from raw.githubusercontent.com and the tree from GitHub's API (unauthenticated). The file
(format `udebt-advance-quizzes`, version 1) is contract kind `quizzes`: `udebt validate <file>`
checks it, and the site lists a file only at the sample's pin. At the defaults the four samples
take 12, 9, 18 and 12 calls (huggingface_hub, sentry-javascript, mlflow, udebt) for 162, 135, 270
and 171 questions. `python -m udebt.hosted.static_samples --check` fails while
`web/public/samples/` is stale. Make the clone an ordinary or depth-1 one
(`git fetch --depth 1 origin <sha>`), not a treeless partial clone (`--filter=tree:0`): the
command lists the tree with `git ls-tree -r -l`, which on a treeless clone fetches objects one by
one and takes hours (the testbeds session, 2026-11-15).

**What is stored, and where.** Without an account: only in your browser, as above. Signed in: in
HintWise's database (Supabase, US East), schema `app_udebt`:

- for each answer: the unit, the ladder level, the question, the verdict, your confidence,
  whether the answer was shown, the question's other phrasings, reference answer and cited
  files (so a missed question can come back in other words), the review state after it
  (ease, interval, repetitions and when it is next due), and `self-checked` as its type when you
  marked it yourself. Never the text of your answer, and not the grader's feedback;
- the repositories you added, your quiz sessions (the questions as asked) and your analysis
  jobs;
- shared with every signed-in person: the repositories in udebt and their analyses (units,
  decisions, file and line references, the tour; no code, no commit emails), with the id of
  whoever added or analysed one.

**Code for every question.** Questions are written from code excerpts at the analysis's commit:
a unit's walkthrough steps, else the top of the files its orientation and paths name. A unit that
names only directories (12 of mlflow's 30 units, one each on the other two samples) is written
from the top tracked files under them: source before config, docs and tests, larger first, never
lockfiles, generated files or media, at most four, the first 60 lines of each. An analysis made
since 2026-11-15 records those files (`files` on the unit); for an older one, the three samples
among them, the page lists the directories from GitHub's tree at that commit through `/api/tree`
(once per directory per visit, kept in the page's memory only). If GitHub refuses the listing,
the question is written without code, as before. After grading, each question shows the files
it rests on, linked at the commit.

No code is stored: a walkthrough's lines and the excerpts a grader reads are fetched from GitHub
at the analysis's pinned commit for that one request. The HintWise gateway records the cost of
each model call on your account and when you first and last used the app. Your own Anthropic
key stays in your browser and goes only to api.anthropic.com, except that an analysis you start
sends it once to udebt's analysis service, which uses it for that job and keeps nothing.

**Deleting your account.** Settings, Account, "Delete my account" (sign in again first if your
sign-in is older than 15 minutes, then type DELETE). It deletes your records, sessions,
repository list and jobs here and your data in every other HintWise app. Analyses you started
stay, without your name, because other people use them.

**One honest limitation.** Your records are written with your own sign-in (or kept in your
browser), and self-checked answers are graded by you, so nothing stops you from editing or
flattering them: the debt number is a self-assessment for learning, not a certificate of what
you know.

## Hosted: the platform-agnostic core (Phase 4a)

The hosted app (Phase 4b, above) lives on the HintWise platform (HOSTING.md; deployment in
`docs/DEPLOY.md`). Phase 4a built what any host needs, stdlib only, with no web framework:

- **The contract** (`udebt/contract.py`, version 1): schemas for `map.json` (versions 1-3), a
  record, `snapshots.json`, `commits.json`, `responsibility.json`, the export and the import
  payload (the export plus a sanitized map), and since 2026-11-15 the hosted site's advance
  quizzes (`quizzes`, a kind added without a new version), checked by a small validator that names the
  failing path (`$.units[3]: missing 'name'`) and by cross-field rules (every developer and
  unit a row names exists). Fields may be added without a new version; removing or retyping
  one bumps it. The export carries `contract` and the series' `fingerprint`, and `udebt
  export` refuses to write one that breaks the contract; `udebt validate <file>` checks any of
  these files. The sanitized map keeps the specs (descriptions, decisions, paths, history,
  orientations, the tour) and drops walkthroughs and pooled questions with their reference
  answers. The exports of digitalme, dsa-instructor and huggingface_hub validate
  (`UDEBT_TESTBEDS=1 pytest tests/test_contract.py`).
- **The worker** (`udebt/worker.py`, `udebt worker run <url> --rev <sha> --out <dir>`): clones
  into a temporary directory within limits (a clone timeout, 500 MB of objects, 20,000
  commits by default), takes the repository's committed map, else `--map` (with its config
  and responsibility sets), else with `--llm` writes one (plus orientations, the tour and
  `--walkthroughs N`) with the configured model; writes the offline core (`weights.json`,
  `structure.json`, `history.json`, `contributors.json`) and, with a map, `export.json`
  (contract `import`), `map.json` (sanitized) and `specs.json` (units, orientations,
  connections, the tour as Markdown, walkthroughs with the lines of each step, which let a
  public repository's quizzes be graded without a clone; `--no-excerpts` leaves them out), and
  the manifest `analysis.json`; then deletes the clone, always. Over a limit it writes nothing.
  digitalme takes 2 s, dsa-instructor (2,510 commits) 38 s and 1.3 MB.
- **The schema** (`supabase/migrations/0001_app_udebt.sql`, schema `app_udebt`,
  idempotent): shared `repositories` and `analyses` keyed by (repository, sha, owner), where a
  sample's analysis has no owner, is readable by every signed-in person and written by no app
  code; a person's `my_repositories`, `records` and `quiz_sessions`, each with `user_id ...
  default auth.uid() references auth.users on delete cascade` and own-row policies; grants to
  `authenticated` only. The team roles (owner, lead, member, viewer) are designed in its
  comments, not built. `supabase/sqlite/app_udebt.sql` is the same for SQLite, and `udebt worker
  sql <dir>` turns a worker output into an idempotent load for either (the platform's admin
  workflow loads the samples with it). `UDEBT_TEST_PG=<url> pytest tests/test_schema.py`
  checks the policies against a real Postgres with Supabase's `auth.uid()`.
- **Push and pull** (`udebt/remote.py`): `udebt remote set <url>`, then `udebt push` POSTs the
  import payload to `<url>/import` and `udebt pull` GETs `<url>/pull`, adding the evidence the
  host recorded (a quiz taken in the browser) to the records once (`origin: remote`); both
  directions are idempotent because every row has a natural key (an evidence record's
  developer, time, unit, level and question). The URL is in `.udebt/remote.json`, the token
  in `UDEBT_TOKEN` or `~/.config/udebt/credentials.json` (mode 0600, read from stdin by `udebt
  remote token`), both outside git.
- **The pull-request check** (`udebt/checks.py`, `udebt/webhooks.py`): `udebt risk` as a
  GitHub check-run body, advisory by default (it always passes, with the verdict, a table of
  units and how to raise each); a repository that opts into soft blocking fails a HIGH or
  OUTSIDE change until a lead acknowledges it (the label `udebt-acknowledged` or the comment
  `/udebt ack`), and the check then passes naming them. `udebt risk <range> --check-run` prints
  it. The webhook router verifies the signature and says what a delivery asks for (analyse a
  push to the default branch, check a pull request, record an acknowledgement); whoever
  receives it does the work.

A first draft of a full hosted API (Starlette, GitHub OAuth, organisations and roles in row
level security) was written before the hosting target changed; it is parked, untested, under
`drafts/hosted-api/`.

## Experiments

`udebt experiment synthetic` tests the model without human subjects. A synthetic developer
is the `claude` CLI taking the quiz with its knowledge frozen at an older snapshot:

```bash
udebt experiment synthetic --suggest 10,25,50          # snapshots that much mass before HEAD (no LLM)
udebt experiment synthetic --suggest mshare:0.3,0.7    # ... where the units' mean mass share since is that
udebt experiment synthetic --suggest share:0.3,0.7     # ... where their mean share changed is that
udebt experiment synthetic --snapshots <rev>,<rev>,<rev> [--per-unit 2] [--level 3] [--units N]
udebt experiment synthetic --at <rev>                  # one snapshot
udebt experiment synthetic --snapshots ... --resume .udebt/experiments/<report>.json
```

1. one question set is written from HEAD for every snapshot (one call), `--per-unit`
   questions per unit (default 2), every one at one level of the question ladder (`--level`,
   default 3, consequence across units: what else would change if the decision were
   reversed), so runs at the same level compare and the question set's mix of levels no
   longer varies between runs. The prompt shows no commit history and forbids questions
   about what changed, so a unit that has not moved since a snapshot can be answered
   correctly from it. Reports before version 3 asked one decision and one behaviour question
   per unit, roughly levels 2-3 and 4;
2. per snapshot, the answers come from a temporary `git worktree` at `<rev>` with its `.git`
   link removed, the answerer's only working directory, and the prompt says the code it sees
   is the only code it knows (one call);
3. per snapshot, the answers are graded against HEAD, as the quiz grades them (one call).

So a run costs 1 + 2 x snapshots calls, and the snapshot is the only variable. Every call
reads a temporary worktree with no `.udebt/` (HEAD's for the questions and grades, the
snapshot's for the answers), so no call can read an earlier report's answers, the samples or
the map's pooled questions (`clean_worktree: true` in the report); every other `claude` call
that reads files is denied `.udebt/` by a permission rule and told so in its prompt. Its stale
knowledge should go wrong where the code moved since. The report, in
`.udebt/experiments/synthetic-<sha>[_<sha>...]-<date>-<HHMMSS>.json` (never overwritten; nothing
goes to `.udebt/records/`), records the `level` and gives per snapshot and per unit:

- the weighted mass landed since the snapshot (the same attribution as everywhere else),
  its share of the unit's mass, and the files at the snapshot and now;
- the share of the unit changed since (weighted changed lines over its size at HEAD, the
  decay model's input) and the score the model predicts for a developer who knew the unit
  at the snapshot, at the run's level and that level's k (`predicted`, with the mass model's
  `predicted_mass` beside it; `decay.level_rate` in the report names the k, and `--suggest`
  predicts at `--level` too); the summary compares their means and errors with the observed
  score;
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
