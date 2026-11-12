# udebt: measure and reduce understanding debt

When an AI writes the code and a developer guides it, the developer's mental model of the
system is abstract and may not match how the code actually works. That gap is
**understanding debt**. A developer can pay it down by digging in, or let it grow by
continuing to build on top.

`udebt` is a standalone CLI that runs over a git repository and uses the `claude` CLI
(subscription-billed, no API key) to:

1. **map** the architectural units of the codebase: the decisions, boundaries and flows a
   developer must understand, as opposed to package internals they can treat as a black box;
2. **quiz** a developer with prediction questions whose answers the code determines, and grade
   the answers against the code;
3. **score** debt per unit and as a 0-100 index, for one developer and for the team;
4. **dig** into the worst unit with a short guided walkthrough that ends in a check question.

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
udebt map                     # claude reads the code, writes .udebt/map.json
udebt status                   # everything is unmeasured: debt index 100
udebt quiz -n 5                # answer five prediction questions, get graded
udebt status                   # debt index drops where you were right
udebt dig                      # walkthrough of the highest-debt unit, then a check question
udebt scope <unit> blackbox    # "I choose not to understand this"; excluded from the index
udebt status --team            # who holds which unit; bus factor per unit
```

Commit everything under `.udebt/`. The map is shared, and each `records/<developer>.jsonl`
file holds one developer's quiz history, so the team view and bus factor work for everyone.

## The model

For each architectural unit `u` and developer `d`:

| quantity | definition |
|---|---|
| evidence of one answer | correct 1.0, partial 0.5, wrong 0.0, unknown 0.0, **confidently wrong -0.5** |
| verified understanding | mean evidence over the last 5 answers on `u`, clamped to [0, 1] |
| understanding | verified x 0.9^(commits touching `u` since the last answer) |
| importance | 0.6 x centrality/5 + 0.4 x churn/max churn, centrality rated 1-5 by the mapper |
| debt(u) | importance x (1 - understanding), architectural units only |
| debt index | 100 x sum(debt) / sum(importance) |

Three deliberate choices:

- **Unmeasured counts as zero understanding.** The index starts at 100 and you earn it down.
- **Saying "I don't know" is free; being confidently wrong costs.** A known unknown is cheap to
  fix. A misconception causes bugs.
- **Understanding decays when the code moves without you.** Each commit that touches a unit
  after you last verified it knocks 10 percent off, so debt grows as the AI keeps building.

Team view takes the maximum understanding across developers per unit and counts how many
developers clear the known threshold (default 0.6). That count is the unit's bus factor.

## Development

```bash
ruff check . && ruff format --check .
pytest -q
UDEBT_LLM=fake udebt --repo /path/to/repo map   # offline, canned answers
```
