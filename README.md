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
   infrastructure changes, new top-level directories, renames, big restructurings;
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
udebt quiz -n 5 [--deep]       # five decision questions from your set (sub-units with --deep)
udebt dig [unit]               # walkthrough of the highest-debt unit, then a check question
udebt risk [range|--staged]    # weight a change lands in each unit vs your understanding; --deep per sub-unit
udebt hook install             # warn-only pre-push hook that runs `udebt risk`
udebt scope <unit> blackbox    # "I choose not to understand this", for you (or --for <dev>)
udebt scope <unit> blackbox --team   # for everyone: stored in the map
```

Commit everything under `.udebt/`. The map and `responsibility.json` are shared, and each
`records/<developer>.jsonl` file holds one developer's quiz history, so the team view and
bus factor work for everyone.

## The model

**Commit weight.** Not every commit can create debt. Each commit gets a weight in [0, 1]
from cheap git signals: whitespace-only changes, pure renames, lockfiles, generated files
and docs-only commits weigh about 0.05; test-only changes are discounted; size (whitespace-
ignored lines, log scale), dependency and infrastructure changes, new code files, new
top-level directories and changes spanning several directories push the weight up; merges
weigh 0. `udebt weigh --refine` asks the model to classify the heaviest commits as
architectural, behavioural, refactor or cosmetic and blends that in. Weights are cached in
`.udebt/commits.json`. When a commit touches several units, each unit receives the commit's
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
| understanding | verified x 0.9^(mass landed on `u` since the last answer) |
| line share of a commit in `u` | changed lines under `u` / all changed lines, lockfile and generated lines left out; a file listed by k top-level units counts 1/k of its lines in each, and a parent's part is split again among the sub-units listing it |
| attribution of a commit to `u` | weight x sqrt(line share); 0 for a bot commit |
| prior (no quiz yet) | 0.5 x your weighted share of `u` x 0.9^(mass others landed since your last commit) |
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
- **Understanding decays when the code moves without you.** Each commit that touches a unit
  after you last verified it knocks 10 percent off, so debt grows as the AI keeps building.

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
Code`, `Assisted-by: Claude:claude-opus-4` (kernel), `Assisted-by: Copilot`,
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

## Development

```bash
ruff check . && ruff format --check .
pytest -q
UDEBT_LLM=fake udebt --repo /path/to/repo map   # offline, canned answers
```
