# TESTBEDS

External repositories for testing udebt: public, large teams, and a mix of human, AI-assisted
and AI-authored commits. Screened 2026-11-12 from commit metadata only. The machine-readable
list with pinned commits is `testbeds.json`. The owner's own repos (`pisanuw/digitalme`,
`pisanuw/dsa-instructor`, see BRIEFING) remain the place for real quizzes.

## Decisions (owner, 2026-11-12)

- **Count every agent**, not only Claude. Claude was the starting point; Copilot's coding
  agent, Cursor, Codex, Devin, OpenCode and the `Assisted-by:` convention all carry large
  shares in these repos.
- **Starter set**: the three primaries plus one control below.
- **No contacts on these teams**, so nobody there will take quizzes. Validation (Phase 5)
  relies on outcome proxies such as reverts and hotfixes, run on the stress tier where
  those are plentiful.
- **Python and JS/TS first**, matching ROADMAP decision 7.

## Starter set

| repo | why |
|---|---|
| [huggingface/huggingface_hub](https://github.com/huggingface/huggingface_hub) | Small Python (2,920 commits, 46 MB). Fast map/quiz loop. 38% AI, 14 regulars, 38 humans using AI. |
| [getsentry/sentry-javascript](https://github.com/getsentry/sentry-javascript) | TypeScript, 17,695 commits since 2012: a long human history, then AI rising 1% → 61% over the last year. 31 regulars, 85 humans using AI, Claude + Codex + Cursor. The closest match to "existing repo with mixed history". |
| [mlflow/mlflow](https://github.com/mlflow/mlflow) | Python + TS, 14,477 commits. 61% AI, 52 regulars, 173 humans using AI, Claude plus the Copilot coding agent, so both `ai` and `assisted` classes are large. |
| [servo/servo](https://github.com/servo/servo) (control) | Bans AI contributions. 0 AI-marked commits from 91 regular contributors in a year. |

Next, for team scale: [vllm-project/vllm](https://github.com/vllm-project/vllm) (the widest
team use: 977 humans directed AI work, and the top 5 account for only 19% of it) and
[microsoft/vscode](https://github.com/microsoft/vscode) (AI share 9% → 84% within a year).
[home-assistant/core](https://github.com/home-assistant/core) suits Phase 2 because its
per-integration CODEOWNERS give ready-made responsibility sets.

## Ground truth without team contacts

- **Outcome proxies.** Reverts in the last 12 months: vscode 259, getsentry/sentry 212,
  vllm 82, home-assistant 81, kibana 80, but huggingface_hub 0, sentry-javascript 1 and
  mlflow 13. The starter set tests the mechanics; outcome validation needs the stress tier.
  `testbeds.json` records reverts per repo.
- **Synthetic developers.** Have the `claude` CLI answer the quiz from a snapshot of the
  repo taken N weighted commits ago. Its known-stale knowledge should score worse on exactly
  the units that changed since, which checks decay and unit attribution with no humans
  involved. Implemented as `udebt experiment synthetic`; the first result (digitalme, no
  support yet, floor effect) is in ROADMAP Phase 5.
- **Real people.** The owner's own repos, and students if a course uses one of these
  projects.

## Method

- Metadata-only clones (`git clone --bare --filter=tree:0 --shallow-since=2025-11-12`),
  so no code was downloaded. The figures cover non-merge commits on the default branch from
  the last 12 months.
- Classified with udebt's `classify()` and `BUILTIN_SIGNATURES` (unchanged as of 286cdd4),
  plus extra signatures to find what the built-in list misses: `Assisted-by:` naming an AI,
  Devin, Jules, OpenCode, the `claude[bot]` GitHub app, the Codex connector, Amazon Q, and
  `Generated-by:` / `Made-with:` trailers.
- Bots (dependabot, renovate, and `*[bot]` accounts that are not agents) are removed before
  computing AI share and team size.
- **regulars**: distinct human authors with 5 or more commits in the year.
- **humans using AI**: humans with an AI-assisted commit, or named as co-author on an
  agent-authored commit.
- **top-5 share**: the share of AI-involved commits accounted for by the five
  heaviest AI users. Near 1.0 means one or two people drive the AI use, not the team.
- All-time commit counts come from GitHub commit search (default branch).

Selection criteria for "large team, substantial AI": 20 or more regulars, AI share of 15% or
more, top-5 share of 0.75 or less, and a rising trend.

## What udebt's classifier misses (as of 286cdd4)

"udebt" is the built-in signature list and "full" adds the extra signatures.

| signal | example trailer or author | repo | udebt | full |
|---|---|---|--:|--:|
| `Assisted-by:` (kernel/Fedora convention) | `Assisted-by: Claude ...` | go-git | 5% | 57% |
| | | nodejs/node | 0% | 19% |
| | | harfbuzz | 24% | 61% |
| | | llvm-project | 4% | 10% |
| Devin as author | `devin-ai-integration[bot]` | airbyte | 2% | 86% |
| | | Prefect | 15% | 79% |
| | | cal.com | 3% | 37% |
| Project-specific trailer | `Generated-by: Claude Opus ... following the guidelines` (427 commits) | apache/airflow | 4% | 8% |
| `claude[bot]` GitHub app | author `claude[bot]` | ComfyUI_frontend 262, supabase 85, MCP TS SDK 38 | | |
| OpenCode | `noreply@opencode.ai` | vitest 50, sst/opencode 171 | | |
| `Made-with: Cursor` | trailer only, no co-author | mattermost 160, airflow 32 | | |

**Status (Phase 1.1, 2026-11-12):** every signal in this table is now built in, and so are
the bot class, case-insensitive matching, the vscode footer and grouping of merged branches
(the observations below). Re-measured on clones at the pinned shas (last 12 months, non-merge,
non-bot): go-git 5.0% -> 56.9%, PrefectHQ/prefect 18.3% -> 78.8%.

Other observations:

- Bots are classified `human` for scoring. Phase 1 labels them in `contributors`, but per
  BRIEFING the label is display-only, so bot commits still count as human share and dilute
  decay.
- vscode uses `🤖 Authored with GitHub Copilot on behalf of @user` plus
  `Co-authored-by: Copilot <223556219+Copilot@...>`. The second line is caught; the first
  line, which names the human who directed the work, is not parsed.
- The Copilot `author_emails` regex is lower-case and case-sensitive, so it misses
  `198982749+Copilot@users.noreply.github.com`. The author-name pattern catches these commits
  anyway.
- **Commit granularity.** ClickHouse lands 121k commits a year (34% are merges that bring
  PR branches with them), while vllm, Zed and Next.js squash every PR. Phase 1 gives merges
  weight 0, but branch commits still each carry `0.1 + 0.6 * log-size`. Ten 20-line commits
  weigh about 3.9 in total, while the same work squashed into one 200-line commit weighs
  0.61, a 6× difference. Grouping by PR, or making size additive, would remove the bias.
- `load_commits` runs `git log --numstat` over the full history. That needs blobs, so large
  testbeds need full clones (kibana 18 GB, ClickHouse 15 GB, PostHog 9 GB on GitHub's size
  figure) or a commit cache.

## Starter-set run (offline commands, 2026-11-12)

Full clones at the pinned shas, with `udebt init`, `contributors` and `weigh` and no LLM
calls. huggingface_hub, sentry-javascript and mlflow are measured on ba564e2 (Phase 1.1,
built-in signatures only). servo was measured on 286cdd4 and the extended signatures,
which ba564e2 now builds in.

| repo | commits | ai / assisted / bot (all history) | bot weight if counted | `weigh` | weight 1.0 | AI share, last 12m non-bot: count vs weight | mean weight AI vs human |
|---|--:|--:|--:|--:|--:|---|---|
| huggingface_hub | 2,920 | 1 / 325 / 71 | 2.0% | 7 s | 136 (5%) | 38% vs 44% | 0.59 vs 0.46 |
| sentry-javascript | 17,695 | 39 / 1,658 / 2,032 | 9.3% | 43 s | 1,793 (10%) | 45% vs 46% | 0.65 vs 0.61 |
| mlflow | 14,478 | 1,563 / 1,870 / 713 | 4.0% | 91 s | 672 (5%) | 61% vs 59% | 0.53 vs 0.59 |
| servo (control) | 59,283 | 0 / 0 / 4,129 | 3.2% | 1,112 s | 1,584 (3%) | 0% | – |

- **The control holds over the full history**: servo has 0 AI and 0 assisted commits out of
  59,283.
- **Bots now land no mass.** The bot class keeps 2–9% of the weighted mass out of decay and
  churn, which matters most in sentry-javascript.
- **AI and human commits weigh about the same**, so weighting moves AI share by only a few
  points either way.
- **Version bumps weigh as dependency changes.** A release that edits the `version` field of
  every `package.json` gets "dependencies changed" (+0.3) and touches every package. On
  ba564e2, bot-cut releases land no mass (the bot class), but human-cut ones still do:
  244 release commits in sentry-javascript weigh 0.7 or more (most from 2013–2020, 48 in
  2026), and 18 in mlflow. In a monorepo each one decays every unit. A version-only
  manifest diff should weigh near zero, as ROADMAP Phase 1 intended.
- **Speed on large histories.** On servo each `contributors` run took about 8–9 minutes and
  `weigh` 18.5 minutes, because every command re-parses `git log --numstat` for the whole
  history. The stress tier (vscode 171k commits, llvm 608k) needs a parsed-commit cache.

## First `udebt map` (huggingface_hub, ba564e2, 2026-11-12)

The owner approved the run. It took one `claude` CLI call (subscription, no API key) and 45 s.
Structure: 287 source files, 11 clusters. The map has 12 architectural units and 4 CLI
sub-units covering 81% of non-test source files: `hf-api`, `http-layer`, `download-cache`,
`config-constants`, `auth-model`, `upload-commit`, `cli`, `inference-client`,
`generated-inference-types`, `hf-filesystem-buckets`, `repocards-mixin`, `dev-tooling-ci`.
The units read as a fair architecture of the library.

`status` for the top contributor (515 commits): debt index 92, with priors of 0.02–0.15 per
unit. Per-unit `ai%` is 0–26%, all-time and weighted, against 38% of commits in the last
year.

**History mining misses the AI era.** `udebt history` lists only 2 decision commits, and 7
of the map's 9 history links point at one commit ("V1.0 release (#3464)").
`decision_candidates` gave the mapper 40 commits:

- 34 of the 40 are from 2021–2022, and only 2 from after 2023. 25 touch
  `api-inference-community/`, a subproject removed from the repo in April 2022.
- Only 23% of the files each candidate touched still exist at HEAD, and 22 of the 40 touch
  no surviving file, so the mapper cannot link them to any current unit.
- **Ties go to the oldest commit.** The sort key is `(-score, date)`. 44 commits tie at the
  cut-off score of 6, and all 25 open slots went to 2021–2023. None of the 14 tied commits
  from 2024–2026 got one.
- The last 12 months have 153 scored candidates (max score 7, 64 AI-involved, 88% of their
  files still exist), and one of them got a slot.

Possible fixes: weight candidates by the share of their files that survive at HEAD, break ties
toward recent commits or give each period a quota, and score with the commit weights that
now exist instead of a separate count.

## Caveats

- **Disclosure-only.** Every AI share is a lower bound. Zed (2%), Ruff (2%) and openai/codex
  (3%) almost certainly use more AI than they disclose; Codex does not sign commits by default.
- **Disclosure that vanishes.** Bun 53% → 1% → 0% (2026 Q1 → Q2 → Q3), Deno 40% → 0%,
  cal.com 49% → 3%, cockroach 36% → 0%. The cause, whether a policy change or a tool
  setting, is unknown. These repos are the natural test for ROADMAP's "most likely AI"
  heuristics.
- **Identity.** Humans are counted by email with GitHub noreply addresses folded to the
  login, and no `.mailmap` is applied, so team sizes are somewhat inflated.
- **Mostly untested end to end.** `udebt map` has run on huggingface_hub only, and no quiz
  has been taken on any testbed.
- The critical-packages dataset that seeded the library candidates measured the year ending
  2026-07-29. These numbers are for the year ending 2026-11-12 and are generally higher.

## Tiers

Columns: commits/yr is non-bot, non-merge commits in the last 12 months. AI % is the full
classifier, with the udebt built-in figure in brackets. The quarterly column is AI share
for 2025 Q4, then 2026 Q1 through Q4. A "?" means the metadata lookup was rate-limited.

### Primary (starter set)

| repo | lang | all commits | since | commits/yr | AI % (udebt today) | regulars | humans using AI | top-5 share | AI % by quarter | reverts/yr | main agents |
|---|---|--:|--:|--:|--:|--:|--:|--:|---|--:|---|
| [huggingface/huggingface_hub](https://github.com/huggingface/huggingface_hub) | Python | 2,920 | 2020 | 840 | 38 (35) | 14 | 38 | 0.87 | 0→37→45→38→22 | 0 | claude-code 239, cursor 78, assisted-by 3 |
| [getsentry/sentry-javascript](https://github.com/getsentry/sentry-javascript) | TypeScript | 17,695 | 2012 | 3,625 | 46 (40) | 31 | 85 | 0.66 | 1→13→29→52→61 | 1 | claude-code 1363, openai-codex 245, cursor 55 |
| [mlflow/mlflow](https://github.com/mlflow/mlflow) | Python | 14,477 | 2018 | 4,724 | 61 (57) | 52 | 173 | 0.71 | 33→56→77→61→42 | 13 | claude-code 1673, github-copilot 1177, openai-codex 24 |

### Controls

| repo | lang | all commits | since | commits/yr | AI % (udebt today) | regulars | humans using AI | top-5 share | AI % by quarter | reverts/yr | main agents |
|---|---|--:|--:|--:|--:|--:|--:|--:|---|--:|---|
| [servo/servo](https://github.com/servo/servo) | Rust | ? | ? | 4,736 | 0 (0) | 91 | 0 | – | 0→0→0→0→0 | 12 | – |
| [qemu/qemu](https://github.com/qemu/qemu) | C | ? | ? | 9,471 | 0 (0) | 197 | 1 | 1.00 | 0→0→1→0→0 | 25 | assisted-by 30 |
| [godotengine/godot](https://github.com/godotengine/godot) | C++ | ? | ? | 4,066 | 0 (0) | 104 | 0 | – | 0→0→0→0→0 | 22 | – |
| [qgis/QGIS](https://github.com/qgis/QGIS) | C++ | ? | ? | 5,922 | 0 (0) | 49 | 7 | 0.86 | 0→0→0→0→0 | 33 | assisted-by 11, claude-code 3 |

### Candidates for the next primaries

| repo | lang | all commits | since | commits/yr | AI % (udebt today) | regulars | humans using AI | top-5 share | AI % by quarter | reverts/yr | main agents |
|---|---|--:|--:|--:|--:|--:|--:|--:|---|--:|---|
| [dagster-io/dagster](https://github.com/dagster-io/dagster) | Python | 28,222 | 2018 | 2,361 | 21 (21) | 35 | 40 | 0.76 | 0→8→23→35→75 | 15 | claude-code 503, github-copilot 1 |
| [mattermost/mattermost](https://github.com/mattermost/mattermost) | TypeScript | 23,913 | 2015 | 2,196 | 44 (40) | 48 | 54 | 0.64 | 6→16→36→51→71 | 2 | cursor 594, claude-code 320, generated-by 33 |
| [temporalio/temporal](https://github.com/temporalio/temporal) | Go | 10,205 | 2019 | 2,160 | 14 (14) | 47 | 36 | 0.66 | 0→6→17→20→21 | 18 | claude-code 298, cursor 5, github-copilot 2 |
| [storybookjs/storybook](https://github.com/storybookjs/storybook) | TypeScript | 90,942 | 2016 | 10,472 | 28 (27) | 66 | 61 | 0.72 | 4→8→23→24→65 | 107 | claude-code 2137, cursor 436, github-copilot 311 |

### Large-team stress

| repo | lang | all commits | since | commits/yr | AI % (udebt today) | regulars | humans using AI | top-5 share | AI % by quarter | reverts/yr | main agents |
|---|---|--:|--:|--:|--:|--:|--:|--:|---|--:|---|
| [vllm-project/vllm](https://github.com/vllm-project/vllm) | Python | 25,215 | 2023 | 14,007 | 24 (24) | 463 | 977 | 0.19 | 4→5→12→30→56 | 82 | claude-code 2265, openai-codex 727, cursor 189 |
| [microsoft/vscode](https://github.com/microsoft/vscode) | TypeScript | 171,420 | 2015 | 24,664 | 41 (40) | 135 | 235 | 0.31 | 9→14→32→63→84 | 259 | github-copilot 10046, claude-code 72, cursor 6 |
| [home-assistant/core](https://github.com/home-assistant/core) | Python | 124,202 | 2013 | 22,293 | 20 (20) | 366 | 608 | 0.42 | 8→11→17→19→31 | 81 | claude-code 3333, github-copilot 1088, cursor 32 |
| [getsentry/sentry](https://github.com/getsentry/sentry) | Python | 114,973 | 2010 | 19,080 | 21 (19) | 182 | 145 | 0.33 | 0→17→29→19→16 | 212 | claude-code 3055, openai-codex 466, cursor 395 |
| [n8n-io/n8n](https://github.com/n8n-io/n8n) | TypeScript | 27,249 | 2019 | 10,423 | 37 (35) | 112 | 173 | 0.31 | 2→17→34→48→42 | 3 | claude-code 3457, cursor 392, github-copilot 7 |
| [PostHog/posthog](https://github.com/PostHog/posthog) | Python | 75,189 | 2020 | 40,781 | 20 (18) | 182 | 187 | 0.22 | 6→13→15→17→30 | 63 | claude-code 8020, cursor 168, github-copilot 122 |
| [elastic/kibana](https://github.com/elastic/kibana) | TypeScript | 116,385 | 2013 | 22,717 | 27 (26) | 335 | 326 | 0.24 | 1→5→20→39→52 | 80 | claude-code 3539, cursor 2294, github-copilot 300 |
| [dotnet/runtime](https://github.com/dotnet/runtime) | C# | 147,910 | 2019 | 6,598 | 53 (52) | 129 | 221 | 0.22 | 24→41→58→59→63 | 55 | github-copilot 3482, claude-code 28, cursor 12 |
| [ClickHouse/ClickHouse](https://github.com/ClickHouse/ClickHouse) | C++ | 321,360 | 2016 | 77,671 | 41 (40) | 417 | 243 | 0.72 | 1→30→42→41→66 | 966 | claude-code 31301, cursor 387, github-copilot 135 |
| [llvm/llvm-project](https://github.com/llvm/llvm-project) | LLVM | 608,135 | 2016 | 48,505 | 10 (4) | 1164 | 662 | 0.32 | 0→3→6→14→25 | 1125 | assisted-by 3189, claude-code 1593, cursor 107 |

### Edge cases

| repo | lang | all commits | since | commits/yr | AI % (udebt today) | regulars | humans using AI | top-5 share | AI % by quarter | reverts/yr | main agents |
|---|---|--:|--:|--:|--:|--:|--:|--:|---|--:|---|
| [go-git/go-git](https://github.com/go-git/go-git) | Go | 4,099 | 2019 | 802 | 57 (5) | 13 | 36 | 0.86 | 0→21→62→77→81 | 0 | assisted-by 413, claude-code 26, github-copilot 9 |
| [nodejs/node](https://github.com/nodejs/node) | JavaScript | 49,466 | 2014 | 3,521 | 19 (0) | 93 | 58 | 0.81 | 0→2→11→24→33 | 24 | assisted-by 651, claude-code 14, cursor 2 |
| [harfbuzz/harfbuzz](https://github.com/harfbuzz/harfbuzz) | C++ | 22,551 | 2012 | 2,517 | 61 (24) | 10 | 27 | 0.98 | 0→42→79→58→85 | 34 | assisted-by 920, claude-code 606, openai-codex 11 |
| [apache/airflow](https://github.com/apache/airflow) | Python | ? | ? | 8,396 | 8 (4) | 192 | 151 | 0.59 | 0→4→4→6→20 | 48 | generated-by 313, claude-code 245, github-copilot 43 |
| [airbytehq/airbyte](https://github.com/airbytehq/airbyte) | Python | 56,650 | 2020 | 3,440 | 86 (2) | 45 | 117 | 0.63 | 11→17→19→14→28 | 4 | devin 2672, claude-code 268, cursor 11 |
| [PrefectHQ/prefect](https://github.com/PrefectHQ/prefect) | Python | 22,437 | 2018 | 2,153 | 79 (15) | 22 | 78 | 0.87 | 80→76→50→48→55 | 1 | devin 1316, claude-code 376, openai-codex 3 |
| [calcom/cal.com](https://github.com/calcom/cal.com) | TypeScript | ? | ? | 1,783 | 37 (3) | 43 | 59 | 0.44 | 24→49→11→3→6 | 21 | devin 609, claude-code 53, cursor 3 |
| [dotnet/aspire](https://github.com/dotnet/aspire) | C# | ? | ? | 3,409 | 70 (66) | 25 | 113 | 0.39 | 50→49→69→76→81 | 16 | github-copilot 2357, claude-code 28, gemini 1 |
| [oven-sh/bun](https://github.com/oven-sh/bun) | Rust | 18,653 | 2021 | 4,588 | 10 (10) | 9 | 18 | 0.96 | 39→53→1→0→19 | 24 | claude-code 452, generated-by 1, assisted-by 1 |
| [denoland/deno](https://github.com/denoland/deno) | Rust | ? | ? | 2,623 | 16 (15) | 27 | 35 | 0.90 | 4→40→8→2→0 | 28 | claude-code 403, github-copilot 21, cursor 3 |
| [zed-industries/zed](https://github.com/zed-industries/zed) | Rust | 40,811 | 2021 | 8,036 | 2 (2) | 133 | 88 | 0.22 | 0→2→1→2→4 | 88 | claude-code 133, github-copilot 8, cursor 3 |
| [openai/codex](https://github.com/openai/codex) | Rust | 13,456 | 2025 | 11,457 | 3 (3) | 168 | 51 | 0.68 | 1→6→7→0→0 | 66 | openai-codex 348, claude-code 7, github-copilot 3 |
| [pandas-dev/pandas](https://github.com/pandas-dev/pandas) | Python | 39,821 | 2010 | 2,959 | 54 (51) | 22 | 29 | 0.98 | 2→22→56→67→72 | 5 | claude-code 1586, github-copilot 1 |

## Also screened, not listed in testbeds.json

High AI share but concentrated in one or two people: cypress (64%, top-5 0.93), ruby/rubygems
(53%, 0.99), pyca/cryptography (47%, 0.99), ipython (37%, 1.0), streamlit (41%, 0.90).

Low disclosed AI (under 5%): astral-sh/ruff, langchain, discourse, immich, withastro/astro,
open-webui, ghostty, cline, twenty, modelcontextprotocol/python-sdk.

Mid-range: grafana (14%, 192 regulars), supabase (11%, 106), pnpm (18%, 52, 168 humans using
AI), dotnet/aspnetcore (50%, 43), dotnet/roslyn (24%, 43), typescript-go (26%, Copilot
agent), astral-sh/uv (7%), vercel/ai (9%), pydantic-ai (10%), block/goose (10%), OpenHands
(8%), gemini-cli (8%, Gemini), pytorch (6%, 420 regulars), transformers (9%, 71), quarkus
(6%), cockroach (9%), dd-trace-rb (8%), nx (9%), tldraw (14%), ComfyUI_frontend (13%),
fastmcp (22%), vitest (21%).

## Sources

- Andrew Nesbitt, [A year of AI disclosure in critical packages](https://nesbitt.io/2026/08/06/a-year-of-ai-disclosure-in-critical-packages.html), and its [per-repo CSV](https://github.com/andrew/critical-ai-scan/blob/main/data/critical-github-repositories.csv).
- [CHAOSS disclosure](https://github.com/chaoss/disclosure), the detector behind that dataset.
- [powerset-co/github-coding-agent-tracker](https://github.com/powerset-co/github-coding-agent-tracker), agent signature list.
- [RedMonk, who is writing open source code](https://redmonk.com/sogrady/2026/07/30/writing-open-source/).
- Ban policies: [Godot](https://thenewstack.io/godot-bans-ai-coding-agents/), [QGIS](https://www.remio.ai/post/qgis-bans-ai-assisted-contributions-closing-the-door-after-its-first-policy-fail), [QEMU and others](https://www.technobezz.com/news/open-source-projects-restrict-ai-generated-code-submissions).
