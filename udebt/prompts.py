"""Prompt templates. Each starts with a TASK marker the FakeLLM keys on."""

from __future__ import annotations

import json
from typing import Any

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
                    "question": {"type": "string"},
                    "reference_answer": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["unit_id", "question", "reference_answer", "evidence"],
            },
        }
    },
    "required": ["questions"],
}

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
or directories, relative to the root) where it lives; centrality 1-5, where 5 means most other
code depends on it or breaks if it changes; and `shaped_by`: the short shas from the history list
below that introduced or materially changed this decision, oldest first (empty if none apply).

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


def questions_prompt(units: list[dict[str, Any]], per_unit: int) -> str:
    unit_lines = []
    for u in units:
        hist = u.get("history") or []
        shaped = "; ".join(
            f"{h['sha']} {h['date']} [{h['klass']}] {h['subject'][:70]}" for h in hist[:5]
        )
        parent = f" (a part of {u['parent_name']})" if u.get("parent_name") else ""
        unit_lines.append(
            f"- unit_id: {u['id']}\n  name: {u['name']}{parent}\n"
            f"  summary: {u.get('summary', '')}\n"
            f"  decision: {u.get('decision', '')}\n  alternatives: {u.get('alternatives', '')}\n"
            f"  why: {u.get('why', '')}\n  paths: {', '.join(u.get('paths', []))}\n"
            f"  shaped_by: {shaped or '(unknown)'}"
        )
    return f"""TASK: questions

You are testing whether a developer understands the DECISIONS that shaped the repository in the
current directory. Write {per_unit} question(s) for each unit below. Mix these shapes, favouring
the first two:
- consequence: "If you reverted/replaced X with <alternative>, what else would have to change?"
- rationale: "Why does the code do X rather than Y? What constraint forces it?" Only ask this when
  the code or commit history actually reveals the reason; otherwise ask a consequence question.
- prediction: "What happens when X fails / is empty / is called twice?"
- ownership: "Which component decides Z? Where does it get configured?"

Every question must have an answer the code (or the cited commits) determines, so it can be graded
objectively. Avoid trivia (exact names, line counts, versions). Use the read-only tools to verify
every reference answer against the actual code; `git show <sha>` is not available, so rely on the
code and the commit subjects given. Cite the files you relied on in `evidence`. Keep each
question to two sentences and each reference answer to three.

Units:
{chr(10).join(unit_lines)}
"""


def grade_prompt(items: list[dict[str, Any]]) -> str:
    blocks = []
    for i, it in enumerate(items):
        blocks.append(
            f"### {i}\nUNIT: {it['unit_id']}\nQUESTION: {it['question']}\n"
            f"REFERENCE: {it['reference_answer']}\nEVIDENCE: {', '.join(it.get('evidence', []))}\n"
            f"ANSWER: {it['answer']}\n"
        )
    return f"""TASK: grade

Grade each developer ANSWER against the code in the current directory. The REFERENCE was written
earlier by another model and may itself be wrong; if the answer and reference disagree, check the
EVIDENCE files with the read-only tools and trust the code. Verdicts:
- correct: the answer captures the behaviour or decision, even if worded differently
- partial: right direction but missing a consequence or wrong on a detail that matters
- wrong: contradicts how the code works, or asserts something the code does not do
- unknown: the developer said they do not know, or the answer is empty

Give two sentences of feedback per item: what was right, and the one thing to look at in the code.
Return `index` matching the ### number.

{chr(10).join(blocks)}
"""


def dig_prompt(unit: dict[str, Any]) -> str:
    return f"""You are helping a developer who did not write this code understand ONE architectural
unit well enough to change it safely. Use the read-only tools to look at the actual code first.

Unit: {json.dumps(unit, indent=2)}

The unit's `history` lists the commits that shaped it, each marked human, assisted (human with an
AI co-author) or ai (AI-authored). Mention who made the decision and when if that is known.

Write a walkthrough in Markdown, at most 400 words, with these sections:
1. **The decision** — what was chosen, in one paragraph, and the alternative that was rejected.
2. **Where it lives** — the two to four files or functions that embody it, each with one line on
   what to look for there. Reference them as path:line where you can.
3. **What depends on it** — what breaks or changes if this decision is reversed.
4. **The trap** — the one misconception a reader is most likely to form, and the fact that
   corrects it.

End with exactly one line beginning `CHECK QUESTION:` followed by a prediction question whose
answer the code determines. Do not include the answer.
"""
