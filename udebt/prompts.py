"""Prompt templates. Each starts with a TASK marker the FakeLLM keys on."""

from __future__ import annotations

import json
from typing import Any

from . import ladder

_UNIT_FIELDS: dict[str, Any] = {
    "id": {"type": "string", "description": "short kebab-case identifier"},
    "name": {"type": "string"},
    "kind": {"type": "string", "enum": ["decision", "module", "boundary", "flow"]},
    "summary": {"type": "string"},
    "decision": {"type": "string"},
    "alternatives": {"type": "string"},
    "why": {"type": "string"},
    "paths": {"type": "array", "items": {"type": "string"}},
    "centrality": {"type": "integer", "minimum": 1, "maximum": 5},
    "shaped_by": {
        "type": "array",
        "items": {"type": "string"},
        "description": "short shas from the history list that made or changed this decision, "
        "oldest first",
    },
    "clusters": {
        "type": "array",
        "items": {"type": "string"},
        "description": "ids of the structural clusters this unit covers (C3, C3.2), if any",
    },
}

_CHILD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": _UNIT_FIELDS,
    "required": ["id", "name", "kind", "summary", "decision", "paths", "centrality"],
}

MAP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    **_UNIT_FIELDS,
                    "children": {
                        "type": "array",
                        "items": _CHILD_SCHEMA,
                        "description": "sub-units, only for units large enough to need them",
                    },
                },
                "required": [
                    "id",
                    "name",
                    "kind",
                    "summary",
                    "decision",
                    "paths",
                    "centrality",
                    "shaped_by",
                ],
            },
        }
    },
    "required": ["units"],
}

QUESTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "unit_id": {"type": "string"},
                    "level": {"type": "integer", "minimum": 1, "maximum": ladder.TOP},
                    "question": {"type": "string"},
                    "reference_answer": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["unit_id", "level", "question", "reference_answer", "evidence"],
            },
        }
    },
    "required": ["questions"],
}

# the quiz's own schema: each question comes with two more phrasings of the same fact, so a
# compound question can be replaced without another call (quiz.generate_questions) and a
# revealed one asked again in other words (review.py)
QUIZ_QUESTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    **QUESTIONS_SCHEMA["properties"]["questions"]["items"]["properties"],
                    "variants": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 2,
                        "description": "two other one-part phrasings asking the same fact",
                    },
                },
                "required": [
                    "unit_id", "level", "question", "variants", "reference_answer", "evidence",
                ],
            },
        }
    },
    "required": ["questions"],
}  # fmt: skip

ORIENTATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "orientations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "unit_id": {"type": "string"},
                    "purpose": {"type": "string"},
                    "decision": {"type": "string"},
                    "where": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}, "what": {"type": "string"}},
                            "required": ["path", "what"],
                        },
                    },
                    "shaped_by": {"type": "string"},
                },
                "required": ["unit_id", "purpose", "decision", "where", "shaped_by"],
            },
        }
    },
    "required": ["orientations"],
}

TOUR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"unit_id": {"type": "string"}, "paragraph": {"type": "string"}},
                "required": ["unit_id", "paragraph"],
            },
        },
    },
    "required": ["overview", "units"],
}

WALKTHROUGH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "flow": {"type": "string", "description": "the one request or run traced"},
        "steps": {
            "type": "array",
            "minItems": 3,
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                    "narration": {"type": "string"},
                },
                "required": ["file", "start_line", "end_line", "narration"],
            },
        },
        "trap": {"type": "string", "description": "the misconception most likely, corrected"},
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "minimum": 1, "maximum": ladder.TOP},
                    "question": {"type": "string"},
                    "reference_answer": {"type": "string"},
                },
                "required": ["level", "question", "reference_answer"],
            },
        },
    },
    "required": ["flow", "steps", "trap", "checks"],
}

TEACHBACK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["correct", "partial", "wrong", "unknown"]},
        "right": {"type": "string"},
        "missing": {"type": "string"},
        "misconception": {"type": "string", "description": "empty when there is none"},
        "reference_answer": {"type": "string"},
    },
    "required": ["verdict", "right", "missing", "misconception", "reference_answer"],
}

DIGEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "digests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "unit_id": {"type": "string"},
                    "headline": {"type": "string"},
                    "changes": {
                        "type": "array",
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "properties": {
                                "what": {"type": "string"},
                                "commits": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["what", "commits"],
                        },
                    },
                    "decision": {"type": "string", "enum": ["held", "extended", "changed"]},
                    "decision_note": {"type": "string"},
                    "look_at": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                },
                "required": [
                    "unit_id", "headline", "changes", "decision", "decision_note", "look_at",
                ],
            },
        }
    },
    "required": ["digests"],
}  # fmt: skip

GRADE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "grades": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {
                        "type": "string",
                        "enum": ["correct", "partial", "wrong", "unknown"],
                    },
                    "feedback": {"type": "string"},
                },
                "required": ["index", "verdict", "feedback"],
            },
        }
    },
    "required": ["grades"],
}


ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "answer": {"type": "string"},
                    "confidence": {"type": "integer", "minimum": 1, "maximum": 3},
                },
                "required": ["index", "answer", "confidence"],
            },
        }
    },
    "required": ["answers"],
}


WEIGH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "commits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sha": {"type": "string"},
                    "klass": {
                        "type": "string",
                        "enum": ["architectural", "behavioural", "refactor", "cosmetic"],
                    },
                    "note": {"type": "string"},
                },
                "required": ["sha", "klass", "note"],
            },
        }
    },
    "required": ["commits"],
}


def weigh_prompt(items: list[dict[str, Any]]) -> str:
    lines = []
    for it in items:
        lines.append(
            f"- sha: {it['sha']}  [{it['klass']}] {it['date']}  {it['subject']}\n"
            f"  files ({it['n_files']}): {', '.join(it['files'][:10])}\n"
            f"  +{it['insertions']} -{it['deletions']}; heuristic weight {it['weight']} "
            f"({'; '.join(it['reasons'])})"
        )
    return f"""TASK: weigh

Classify how much a developer must understand after each commit below lands in the
repository in the current directory. You may use the read-only tools to look at the files a
commit names (the current version; `git show` is not available). Classes:
- architectural: adds or swaps a dependency, data store, schema, protocol, framework,
  concurrency or error model, deployment target, or a module boundary. Someone who does not
  understand this commit cannot change the area safely.
- behavioural: changes what the code does within the existing design (features, fixes).
- refactor: moves or restructures code without changing behaviour.
- cosmetic: formatting, comments, docs, renames of identifiers, lockfiles.

Return the sha exactly as given and a one-sentence note on why.

Commits:
{chr(10).join(lines)}
"""


def map_prompt(
    file_list: list[str],
    max_units: int,
    history: list[dict[str, Any]],
    tree: str | None = None,
    clusters: str | None = None,
    max_children: int = 6,
    cochange_max_files: int = 30,
    examples: list[str] | None = None,
) -> str:
    if tree is not None:
        files_block = f"Directory layout ({len(file_list)} tracked files):\n{tree}"
    else:
        files_block = "Tracked files:\n" + "\n".join(file_list)
    hist_lines = [
        f"- {h['sha']} {h['date']} [{h['klass']}{'/' + h['agent'] if h.get('agent') else ''}] "
        f"{h['subject']}  (files: {h['files']}; e.g. {', '.join(h['sample_files'][:4])})"
        for h in history
    ]
    hist_block = "\n".join(hist_lines) if hist_lines else "(no history available)"
    ex = " and ".join(f"`{e}`" for e in (examples or []))
    paths_rule = (
        "Every path must be relative to the repository ROOT (the current directory), written "
        "exactly as in the file list"
        + (f", for example {ex}" if ex else "")
        + ": keep every leading directory, such as a `src/` prefix or the package directory, "
        "and do not give paths relative to a package or a cluster."
    )
    if clusters:
        cluster_block = f"""
Structural clusters (computed before you, from the code and its history): an import graph
between files merged with a co-change graph (files changed in the same commit, weighted by how
much each commit matters; commits touching more than {cochange_max_files} files are left out),
partitioned by community detection. Each line gives an id, a name candidate in brackets (a
directory plus frequent file-name words), the size, the commit mass that landed there, the
heaviest files, the external packages it imports and the other clusters it imports from.
Indented entries are sub-clusters. Docs and config files are attached to the cluster they
change with most.

A cluster is a candidate unit, not a verdict. Name each one from what the code does (read it),
merge clusters that are one concern, split one that mixes concerns, and skip boilerplate. Then
add the cross-cutting decisions no cluster captures, such as the auth model, persistence,
error policy, configuration or concurrency; their paths may span clusters. Record in
`clusters` which cluster ids each unit covers.

{clusters}
"""
    else:
        cluster_block = ""
    return f"""TASK: map

You are mapping the ARCHITECTURE of the repository in the current directory so a developer who
did not write all of the code can decide what they must understand. The repository was built by a
mix of humans and AI coding agents. You have read-only tools; use them to look at the code before
answering. Do not guess from file names alone.

Identify at most {max_units} top-level architectural units. A unit is one of:
- decision: a design choice embedded in the code (persistence strategy, auth model, error handling
  policy, concurrency model, configuration approach, dependency on an external service, deployment
  target)
- module: a cohesive area of code with a clear responsibility that other code depends on
- boundary: an interface between parts (API surface, CLI contract, plugin boundary, data schema)
- flow: an end-to-end path that crosses several modules (request lifecycle, build pipeline)

Skip things a developer can safely treat as a black box: third-party package internals, generated
code, boilerplate, formatting config, one-off scripts.

For each unit give: a short kebab-case id; a name; the kind; a one-sentence summary; the decision
it embodies (what was chosen); plausible alternatives that were NOT chosen; why this choice was
likely made (say "unclear" if neither code nor history reveals it); the repository paths (files
or directories) where it lives; centrality 1-5, where 5 means most other code depends on it or
breaks if it changes; and `shaped_by`: the short shas from the history list below that introduced
or materially changed this decision, oldest first (empty if none apply).

{paths_rule}

Two levels. A top-level unit may have `children`: 2 to {max_children} sub-units, each a part
a developer could understand and change on its own, with the same fields (no children of
their own) and its own paths. Give children only to units that are big enough to need them (roughly
more than 15 files, or several distinct responsibilities); leave small units without children.
Sub-unit ids are short (`policy`, not `agent-policy`); their centrality is relative to their
siblings. Keep each sub-unit's summary and decision to one sentence.

Commits in the history list are marked [human], [assisted] (a human committed with an AI
co-author) or [ai] (an AI agent authored the commit). Prefer decisions that an [ai] or [assisted]
commit introduced: those are the ones the human developer is least likely to understand.
{cluster_block}
History (commits most likely to carry decisions, oldest first):
{hist_block}

{files_block}
"""


def ladder_block() -> str:
    """The five levels, as the question writer and the grader see them."""
    return "\n".join(
        f'{n}. {lv.name}: asks {lv.asks}. May name {lv.names}.\n   Shape: "{lv.example}"'
        for n, lv in ladder.LEVELS.items()
    )


ONE_FACT = """Every question has exactly one part and asks for exactly one fact: one question
mark, no second question joined on with "and what", "and which" or a semicolon, no second
sentence after it, and never a request to list, enumerate or name several things ("which other
parts ...", "name the ..."). A level 1 question asks what the unit is for, or one consequence of
its place in the system, and a developer who knows the answer can give it in one sentence. If a
unit has several things worth asking, ask the most important one."""

NO_IDENTIFIERS = """Levels 1, 2 and 3 test the architecture, not the code. A question at these
levels must not name any function, method, class, variable, constant, file, directory,
module, endpoint or configuration key of this repository, and must not quote or paraphrase a
line or block of code. Name components by their role, in plain language ("the part that
streams replies to the browser", "the check that a user may open the admin pages"), as a
colleague would at a whiteboard. It may name an external technology, service or library
the decision is about (a database, a hosting platform, a model provider). Ask about the
unit's main decision and its place in the system, not its most specific consequence: a
developer who understands the shape of the system should be able to answer without opening a
file. A level 4 question may name a file or a component, but no code. A level 5 question may
name anything."""


def _history_line(h: dict[str, Any], who: bool = False) -> str:
    klass = h["klass"] + ("/" + h["agent"] if who and h.get("agent") else "")
    author = f" {h.get('author') or '?'}:" if who else ""
    return f"{h['sha']} {h['date']} [{klass}]{author} {h['subject'][:70]}"


def _unit_lines(u: dict[str, Any], *, history: bool = True, level: bool = True) -> str:
    parent = f" (a part of {u['parent_name']})" if u.get("parent_name") else ""
    lines = [f"- unit_id: {u['id']}", f"  name: {u['name']}{parent}"]
    if level:
        n = int(u.get("level", 1))
        lines.append(f"  level: {n} ({ladder.LEVELS[n].name})")
    lines += [
        f"  summary: {u.get('summary', '')}",
        f"  decision: {u.get('decision', '')}",
        f"  alternatives: {u.get('alternatives', '')}",
        f"  why: {u.get('why', '')}",
        f"  paths: {', '.join(u.get('paths', []))}",
    ]
    if history:
        shaped = "; ".join(_history_line(h) for h in (u.get("history") or [])[:5])
        lines.append(f"  shaped_by: {shaped or '(unknown)'}")
    if u.get("shown_orientation"):
        lines.append(f"  orientation the developer has just read: {u['shown_orientation']}")
    for q in u.get("revealed") or []:
        lines.append(f"  revealed after a miss (ask the same fact in other words): {q}")
    return "\n".join(lines)


def questions_prompt(units: list[dict[str, Any]], per_unit: int) -> str:
    """One call for every question of a quiz run. Each unit carries the `level` to ask it at
    (ladder.next_level, or a forced level) and, when the developer was just shown one, the
    orientation text (`shown_orientation`)."""
    oriented = any(u.get("shown_orientation") for u in units)
    orientation_rule = (
        """
Some units carry the orientation the developer has just read: what the unit is for, its
decision and alternatives, where it lives and who shaped it. A level 1 question on such a
unit must not be answerable by copying a sentence of that text: ask one consequence that
follows from it, such as what a user would notice first if the unit stopped working, or why
it has to run before or after one named step of a request.
"""
        if oriented
        else ""
    )
    revealed_rule = (
        """
Some units list questions the developer missed and was then shown the answer to. Ask the
same fact again in other words: a different phrasing or a different angle on the same
point, never the same wording.
"""
        if any(u.get("revealed") for u in units)
        else ""
    )
    return f"""TASK: questions

You are testing whether a developer understands the ARCHITECTURE of the repository in the
current directory: what each unit is for, the decisions that shaped it, and what those
decisions mean for the rest of the system. Questions climb a ladder of five levels, and a
developer is asked a level only once they have shown the levels above it. Each unit below
names the level to ask it at. Write {per_unit} question(s) for each unit, every one at exactly
that unit's level, and return that level with each question.

The ladder:
{ladder_block()}

{ONE_FACT}

{NO_IDENTIFIERS}
{orientation_rule}{revealed_rule}
Every question must have an answer the code (or the cited commits) determines, so it can be
graded objectively. Avoid trivia (exact names, line counts, versions) at every level. Use the
read-only tools to verify every reference answer against the actual code; `git show <sha>` is
not available, so rely on the code and the commit subjects given. Cite the files you relied on
in `evidence`. The reference answer says what a correct answer must contain, and the developer
reads it right after a miss, so it teaches: at levels 1-3 it is at most two sentences of plain
language with no identifiers (the files go in `evidence`); at levels 4-5 at most three
sentences, and it may name files and functions. With each question give `variants`: two more
phrasings of the same question, each one part, asking the same fact, so the reference answer
fits all three.

Units:
{chr(10).join(_unit_lines(u) for u in units)}
"""


def stable_questions_prompt(units: list[dict[str, Any]], per_unit: int, level: int = 3) -> str:
    """Questions for the synthetic-developer experiment (synthetic.py). The same questions go
    to answerers who know different snapshots, so they must be answerable from the code as
    it stands, whatever version a developer learnt: about the unit's decision and current
    behaviour, never about what changed or when. No commit history is shown, so the writer
    is not steered towards the latest changes. Every question is at one explicit ladder
    `level`, so runs at the same level stay comparable."""
    lv = ladder.LEVELS[level]
    return f"""TASK: stable-questions

You are writing a short quiz on how the repository in the current directory works. Write
exactly {per_unit} question(s) for each unit below, each answerable from the code as it is now,
every one at level {level} of the question ladder below ({lv.name}), and return that level with
each question.

The ladder:
{ladder_block()}

{NO_IDENTIFIERS}

Ask about the unit as it stands: its purpose, the decision it embodies and how the code
behaves today. Do not ask what changed, when, or why something was changed, and do not ask
about commit history or about the newest feature in preference to the core of the unit. A
developer who knows this unit's code well should be able to answer from the code alone. When
asked for several questions per unit, ask each about a different part of the unit.

Every question must have an answer the code determines, so it can be graded objectively.
Avoid trivia (exact names, line counts, versions, file names). Use the read-only tools to
verify every reference answer against the actual code, and cite the files you relied on in
`evidence`. Keep each question to two sentences and each reference answer to three.

Units:
{chr(10).join(_unit_lines({**u, "level": level}, history=False) for u in units)}
"""


def orientation_prompt(units: list[dict[str, Any]]) -> str:
    """One call for every unit a quiz run needs an orientation for and the map lacks."""
    blocks = []
    for u in units:
        hist = "\n".join(f"    - {_history_line(h, who=True)}" for h in u.get("history") or [])
        blocks.append(
            _unit_lines(u, history=False, level=False) + f"\n  history:\n{hist or '    (none)'}"
        )
    return f"""TASK: orientation

A developer who did not build the repository in the current directory is about to be quizzed
on the units below, starting with what each one is for. Before the first question on a unit
they read a short orientation, written once and shared by the whole team. Write one for each
unit, in plain language, for a capable developer who has never opened these files:

- purpose: one or two sentences on what the unit is for: the job it does for the rest of the
  system. Name other components by their role. Do not list what depends on it or what it
  depends on: the first question asks that.
- decision: two or three sentences: what was chosen, the alternatives that were not, and why
  (say plainly when neither the code nor the history shows why).
- where: the two or three paths a reader should open first (files or directories relative to
  the repository root, as listed under the unit), each with a few words on what is there.
- shaped_by: one sentence on who shaped the unit and when, from its history below: who
  authored the commits that made the decision, and whether a human wrote them alone ([human]),
  with an AI co-author ([assisted]) or an AI agent authored them ([ai]). Write "The history
  list does not say." when the unit has none.

Keep each orientation under 120 words in all. Use the read-only tools to check the summary
and the paths against the code. Do not quote code and do not invent paths. Return `unit_id`
exactly as given.

Units:
{chr(10).join(blocks)}
"""


def grade_prompt(items: list[dict[str, Any]]) -> str:
    blocks = []
    for i, it in enumerate(items):
        n = ladder.level_of(it)
        blocks.append(
            f"### {i}\nUNIT: {it['unit_id']}\nLEVEL: {n} ({ladder.LEVELS[n].name})\n"
            f"QUESTION: {it['question']}\n"
            f"REFERENCE: {it['reference_answer']}\nEVIDENCE: {', '.join(it.get('evidence', []))}\n"
            f"ANSWER: {it['answer']}\n"
        )
    return f"""TASK: grade

Grade each developer ANSWER against the code in the current directory. The REFERENCE was written
earlier by another model and may itself be wrong; if the answer and reference disagree, check the
EVIDENCE files with the read-only tools and trust the code.

Each item carries its LEVEL on the question ladder: 1 purpose and placement, 2 decision and
alternatives, 3 consequence across units (the architecture); 4 behaviour under failure, 5
implementation (depth). At levels 1-3 the question is about the architecture in plain
language, so grade the developer's grasp of roles, decisions and consequences, not their recall
of names: a correct answer that names no file, function or identifier is correct, and is never
marked down for lacking them. Verdicts:
- correct: the answer captures the behaviour or decision, even if worded differently
- partial: right direction but missing a consequence or wrong on a detail that matters; at
  levels 1-3, partial means the role or the decision is right but a dependency or a
  consequence that matters is missing
- wrong: contradicts how the code works, or asserts something the code does not do
- unknown: the developer said they do not know, or the answer is empty

Grade only what the QUESTION asks: the reference may cover more than the question does, and an
answer is not marked down for leaving out what was not asked.

Give two sentences of feedback per item: what was right, and the one thing to look at in the code.
Return `index` matching the ### number.

{chr(10).join(blocks)}
"""


def answer_prompt(questions: list[dict[str, Any]]) -> str:
    """The synthetic developer (synthetic.py): it answers from the snapshot in the current
    directory and is not told the repository has moved on since."""
    blocks = [
        f"### {i}\nUNIT: {q['unit_id']}\nQUESTION: {q['question']}\n"
        for i, q in enumerate(questions)
    ]
    return f"""TASK: answer

You are a developer on the repository in the current directory, taking a short quiz about it.
The code in the current directory is the only version of this code you know: you have never
seen any other, so answer from what this code does and from nothing else. Use the read-only
tools to look before you answer, and only open files inside the current directory.

Answer each question in at most three sentences, as you would to a colleague. If the question
mentions something you cannot find here, answer from your best understanding of the code you
know and say how sure you are; leave the answer empty only if you have no idea at all.
Confidence: 1 = a guess, 2 = fairly sure, 3 = certain. Return `index` matching the ### number.

{chr(10).join(blocks)}
"""


def walkthrough_prompt(unit: dict[str, Any], orientation: str | None = None) -> str:
    """The walkthrough of one unit (walkthrough.py): one real request traced through its code
    in three to six steps, each a file and a line range the developer sees as an excerpt, and
    one check question per ladder level, so `dig` can ask at whatever level the developer is
    and a cached walkthrough serves the whole team."""
    data = {
        k: v
        for k, v in unit.items()
        if k not in ("orientation", "shown_orientation", "walkthrough", "provenance", "questions")
    }
    seen = (
        f"""
The developer has just read this orientation; do not repeat it, build on it:

{orientation}
"""
        if orientation
        else ""
    )
    return f"""TASK: walkthrough

You are helping a developer who did not write this code understand ONE architectural unit
well enough to change it safely, by tracing one real request (or run, or event) through its
code. Use the read-only tools to read the actual files first.

Unit: {json.dumps(data, indent=2)}
{seen}
- flow: one sentence naming the request you trace, as a user or a caller would start it.
- steps: three to six steps in the order the request reaches them. Each is one file of the
  repository (a path relative to the root, as listed under the unit's paths or below them)
  and a line range of at most 25 lines (`start_line` and `end_line`, 1-based and inclusive,
  checked against the file as it is now), with two or three sentences of narration: what
  happens in those lines and why it matters for the unit's decision. The developer sees the
  lines next to your narration, so explain them rather than repeat them.
- trap: one sentence on the misconception a reader of this code is most likely to form, and
  the fact that corrects it.
- checks: one question for each ladder level, 1 to 5, each with one part and one fact, whose
  answer the code determines, and its reference answer. Levels 1-3 ask about the
  architecture in plain language and name no function, variable or file; their reference is
  at most two plain sentences. Level 4 may name a file or component, level 5 anything.

The ladder:
{ladder_block()}

The unit's `history` lists the commits that shaped it, each marked human, assisted (human
with an AI co-author) or ai (AI-authored); mention in a narration who made the decision and
when if that helps.
"""


# per level, what a teach-back verdict of correct and partial means
_TEACHBACK_VERDICT = {
    1: ("the purpose and the unit's place in the system are right",
        "the purpose is roughly right but its place (what relies on it, or on what it relies) is "
        "missing or vague"),
    2: ("the purpose and the decision are right",
        "the purpose is right but the decision or its main reason is missing or vague"),
    3: ("the decision and at least one real consequence of reversing it for another part of the "
        "system are right",
        "the decision is right but no consequence beyond the unit is named, or the one named is "
        "vague"),
    4: ("the behaviour under at least one real failure (a part failing, a timeout, bad input, "
        "running twice) matches the code",
        "the failure named is real but what the code then does is incomplete"),
    5: ("the main code path and why it is written that way match the code",
        "the main path is right but a step that matters is missing"),
}  # fmt: skip


def teachback_prompt(
    unit: dict[str, Any], explanation: str, orientation: str | None, level: int = 2
) -> str:
    """A teach-back (teachback.py): the developer explains the unit in their own words; the
    grader says what was right, what was missing and one misconception, graded at the
    developer's current ladder `level` for the unit (Phase 3.4)."""
    from .teachback import ASKS

    lv = ladder.LEVELS[level]
    ok, half = _TEACHBACK_VERDICT[level]
    seen = f"\nThe orientation they may have read: {orientation}\n" if orientation else ""
    names = (
        "worded differently and without any file or function name"
        if level in ladder.ARCHITECTURAL
        else "worded differently"
    )
    return f"""TASK: teachback

A developer is explaining, in their own words, one architectural unit of the repository in the
current directory. They were asked to explain {ASKS[level]}: level {level} of the question ladder
({lv.name}: {lv.asks}). Check the explanation against the code with the read-only tools (trust
the code over the unit description if they disagree) and reply in plain language, as a colleague
would:

- right: one or two sentences on what the explanation got right.
- missing: one or two sentences on the most important thing it left out, for level {level}.
- misconception: one sentence on a claim it makes that the code contradicts, and what the
  code does instead; an empty string when there is none.
- verdict, at level {level}: correct when {ok}, even if {names}; partial when {half}; wrong
  when it contradicts how the code works; unknown when it says nothing about the unit.
- reference_answer: two plain sentences a correct explanation at level {level} must contain.

Unit:
{_unit_lines({**unit, "level": level})}
{seen}
THE DEVELOPER'S EXPLANATION:
{explanation}
"""


def tour_prompt(units: list[dict[str, Any]], names: dict[str, str]) -> str:
    """The architecture tour (tour.py), one call for every top-level unit, in learning order,
    each with its connections (computed from imports and co-change, not by the model)."""
    blocks = []
    for u in units:
        c = u.get("connections") or {}
        hist = "\n".join(f"    - {_history_line(h, who=True)}" for h in u.get("history") or [])
        conn = "; ".join(
            f"{label} {', '.join(names.get(x, x) for x in c.get(key, []))}"
            for key, label in (("uses", "uses"), ("used_by", "used by"), ("changes_with",
                                "changes with"))
            if c.get(key)
        )  # fmt: skip
        blocks.append(
            _unit_lines(u, history=False, level=False)
            + f"\n  connections: {conn or 'none found'}"
            + (f"\n  orientation already written: {u['oriented']}" if u.get("oriented") else "")
            + f"\n  history:\n{hist or '    (none)'}"
        )
    return f"""TASK: tour

Write an architecture tour of the repository in the current directory for a capable developer
who has not built it and is about to learn it: the whole system as a map, before any detail.
It is read once, in about ten minutes, and shared by the team.

- overview: three to five sentences on what the system does and how its parts divide the
  work, naming the units below by their names.
- units: one paragraph per unit below, in the order given (each unit comes after the units it
  uses), 90 to 140 words, covering in this order: what the unit is for; where it sits, using
  its connections (the units it uses and that use it, by name); the decision it embodies;
  the alternative that was not chosen and why; who shaped it and when, from its history:
  whether a human wrote it alone ([human]), with an AI co-author ([assisted]) or an AI agent
  authored it ([ai]). Say plainly when the history does not show why.

Plain language throughout: name no function, class, variable or file of the repository
(external technologies such as a database or a hosting service are fine), and do not quote
code. Use the read-only tools to check each unit's summary against the code; do not invent
connections beyond those listed. Keep the whole tour under 1,800 words. Return `unit_id`
exactly as given.

Units:
{chr(10).join(blocks)}
"""


def digest_prompt(blocks: list[dict[str, Any]]) -> str:
    """Change digests (digest.py), one call for every unit of a run: what landed in each unit
    since the developer last showed what they know there, read against the unit's decision."""
    parts = []
    for b in blocks:
        u, r = b["unit"], b["range"]
        commits = "\n".join(
            f"    - {c['sha'][:7]} {c['date']} [{c['klass']}"
            + (f"/{c['agent']}" if c.get("agent") else "")
            + f"] {c['who']}: weight {c['weight']:.2f}, mass in unit {c['mass']:.2f}: "
            f"{c['subject']}" + (f" (files: {', '.join(c['files'])})" if c.get("files") else "")
            for c in b["commits"]
        )
        more = f"\n    ({b['more']} lighter commit(s) not shown)" if b.get("more") else ""
        diff = (
            f"\n  diff of the range under the unit's paths (truncated):\n{b['diff']}"
            if b.get("diff")
            else ""
        )
        start = (r.start or "")[:7] or "the first commit"
        parts.append(
            f"### unit_id: {u['id']}\n"
            + _unit_lines(u, history=False, level=False)
            + f"\n  range: after {start} up to {r.end[:7]} ({r.note})"
            + f"\n  commits in the range that touch the unit, oldest first:\n{commits}{more}"
            + diff
        )
    return f"""TASK: digest

A developer on the repository in the current directory is catching up on parts of the system
that moved since they last showed what they know about them. For each unit below, write a
short briefing of what landed in that unit over the range given: a colleague bringing them up
to date over coffee, never a warning or a judgement of who did what. Read the code as it is now
with the read-only tools where the commit list and the diff leave you unsure (`git` is not
available).

- headline: one sentence on what the range did to the unit, in plain language.
- changes: one to four items, the most important first, each one sentence on what changed in
  terms of the unit's decision (what it now does, allows or relies on that it did not), not a
  list of files, with the short shas of the commits that did it (from the list).
- decision: `held` when the unit's decision (below) still stands and the changes work within
  it; `extended` when it still stands but now covers more (a new case, a new area, a new
  dependency in the same spirit); `changed` when the decision itself was replaced, reversed or
  relaxed.
- decision_note: one sentence: why it held, what it now also covers, or what was chosen
  instead and what that means for someone who knew the old way.
- look_at: one to three paths, relative to the repository root and under the unit's paths,
  where a reader sees the change best.

Name components by their role rather than by function names where you can. Return `unit_id`
exactly as given.

{chr(10).join(parts)}
"""
