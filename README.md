# udebt: measure and reduce understanding debt

When an AI writes the code and a developer guides it, the developer's mental model of the
system is abstract and may not match how the code actually works. That gap is
**understanding debt**. A developer can pay it down by digging in, or let it grow by
continuing to build on top.

`udebt` is a standalone CLI that runs over an existing git repository, one with a mix of
human commits, AI-assisted commits and AI-authored commits, and uses the `claude` CLI
(subscription-billed, no API key) to:

1. **classify** every commit as `human`, `assisted` (a human committed with an AI co-author)
   or `ai` (an agent authored it), from signatures in commit trailers and author identities;
2. **mine** the history for decision-bearing commits: founding commits, dependency and
   infrastructure changes, new top-level directories, renames, big restructurings;
3. **map** the architectural units of the codebase: the decisions, boundaries and flows a
   developer must understand, each linked to the commits that shaped it and who made them;
4. **quiz** a developer with decision-centric questions (why X rather than Y, what breaks if
   you revert it, what happens when it fails) graded against the code;
5. **score** debt per unit and as a 0-100 index, for one developer and for the team, with a
   weak prior from the developer's own commits so the view is useful before the first quiz;
6. **dig** into the worst unit with a short guided walkthrough that ends in a check question.

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
udebt contributors             # everyone who committed: AI, joint human-AI, human, bot, "most likely"
udebt weigh [--refine]         # how much debt each commit can create; --refine asks claude about the heaviest
udebt map                     # mines history, claude reads the code, writes .udebt/map.json
udebt map --rescore            # redo churn/importance/provenance with current weights, no LLM
udebt status                   # debt per unit; ai% and you% show who wrote each unit
udebt history                  # the decisions that shaped the repo, oldest first, with who made them
udebt quiz -n 5                # answer five decision questions, get graded
udebt dig                      # walkthrough of the highest-debt unit, then a check question
udebt risk [range|--staged]    # weight a change lands in each unit vs your understanding of it
udebt hook install             # warn-only pre-push hook that runs `udebt risk`
udebt scope <unit> blackbox    # "I choose not to understand this"; excluded from the index
udebt status --team            # who holds which unit; bus factor per unit
```

Commit everything under `.udebt/`. The map is shared, and each `records/<developer>.jsonl`
file holds one developer's quiz history, so the team view and bus factor work for everyone.

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
it touched there. A commit confined to one unit lands its full weight.

Everything below that used to be a commit count is now a sum of weights ("mass").

For each architectural unit `u` and developer `d`:

| quantity | definition |
|---|---|
| evidence of one answer | correct 1.0, partial 0.5, wrong 0.0, unknown 0.0, **confidently wrong -0.5** |
| verified understanding | mean evidence over the last 5 answers on `u`, clamped to [0, 1] |
| understanding | verified x 0.9^(mass landed on `u` since the last answer) |
| prior (no quiz yet) | 0.5 x your weighted share of `u` x 0.9^(mass others landed since your last commit) |
| importance | 0.6 x centrality/5 + 0.4 x mass/max mass, centrality rated 1-5 by the mapper |
| risk of a change | per unit touched: mass landed vs your understanding; HIGH if mass >= 0.3 and understanding < 0.3, CAUTION if mass >= 0.1 and understanding < 0.6; the change's verdict is its worst unit |
| debt(u) | importance x (1 - understanding), architectural units only |
| debt index | 100 x sum(debt) / sum(importance) |

Your share of a unit's commits counts AI-assisted commits at half weight. Four deliberate
choices:

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
developers clear the known threshold (default 0.6). That count is the unit's bus factor.

## Contributors

`udebt contributors` merges identities (through `.mailmap` and `udebt alias`) and labels
each one. **AI**, **Bot** and **Joint human-AI** come from explicit signals (agent author
identities, bot-like names, agent trailers on a material share of commits). **Most likely
AI** and **Most likely human** come from commit metadata only: burst cadence, long
bullet-list bodies, uniformly conventional subjects. Those two are advisory and never
feed the scores.

## Recognised AI agents

Built in: Claude Code (author `noreply@anthropic.com`, `Co-Authored-By: Claude ...`,
`Claude-Session:` trailers), GitHub Copilot, Cursor, aider, OpenAI Codex and Gemini by their
usual author identities and co-author trailers. Add your own in `.udebt/config.json`:

```json
{
  "ai_signatures": [
    {"name": "mybot", "author_emails": ["^bot@corp\\.com$"],
     "trailer_patterns": ["(?im)^Co-Authored-By: MyBot"]}
  ]
}
```

A commit matching `author_emails` or `author_names` is `ai`; a human-authored commit matching
`trailer_patterns` is `assisted`.

## Development

```bash
ruff check . && ruff format --check .
pytest -q
UDEBT_LLM=fake udebt --repo /path/to/repo map   # offline, canned answers
```
