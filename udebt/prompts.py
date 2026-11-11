"""Prompt templates. Each starts with a TASK marker the FakeLLM keys on."""

from __future__ import annotations

import json
from typing import Any

MAP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "short kebab-case identifier"},
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": ["decision", "module", "boundary", "flow"]},
                    "summary": {"type": "string"},
                    "decision": {"type": "string"},
                    "alternatives": {"type": "string"},
                    "why": {"type": "string"},
                    "paths": {"type": "array", "items": {"type": "string"}},
                    "centrality": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": ["id", "name", "kind", "summary", "decision", "paths", "centrality"],
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


def map_prompt(file_list: list[str], max_units: int) -> str:
    files = "\n".join(file_list[:400])
    more = "" if len(file_list) <= 400 else f"\n... and {len(file_list) - 400} more files"
    return f"""TASK: map

You are mapping the ARCHITECTURE of the repository in the current directory so a developer who
did not write the code can decide what they must understand. You have read-only tools; use them
to look at the code before answering. Do not guess from file names alone.

Identify at most {max_units} architectural units. A unit is one of:
- decision: a design choice embedded in the code (persistence strategy, auth model, error handling
  policy, concurrency model, configuration approach, dependency on an external service)
- module: a cohesive area of code with a clear responsibility that other code depends on
- boundary: an interface between parts (API surface, CLI contract, plugin boundary, data schema)
- flow: an end-to-end path that crosses several modules (request lifecycle, build pipeline)

Skip things a developer can safely treat as a black box: third-party package internals, generated
code, boilerplate, formatting config, one-off scripts.

For each unit give: a short kebab-case id; a name; the kind; a one-sentence summary; the decision
it embodies (what was chosen); plausible alternatives that were NOT chosen; why this choice was
likely made (say "unclear" if the code does not reveal it); the repository paths (files or
directories, relative to the root) where it lives; and centrality 1-5, where 5 means most other
code depends on it or breaks if it changes.

Tracked files:
{files}{more}
"""


def questions_prompt(units: list[dict[str, Any]], per_unit: int) -> str:
    unit_lines = []
    for u in units:
        unit_lines.append(
            f"- unit_id: {u['id']}\n  name: {u['name']}\n  summary: {u.get('summary', '')}\n"
            f"  paths: {', '.join(u.get('paths', []))}"
        )
    return f"""TASK: questions

You are testing whether a developer understands the architecture of the repository in the current
directory. Write {per_unit} PREDICTION question(s) for each unit below. A prediction question has
an answer that the code determines, so it can be graded objectively. Good shapes:
- "What happens when X fails / is empty / is called twice?"
- "If you change/rename/remove X, what else breaks?"
- "Which component owns Y? Where does Z get decided?"
- "What is the order of events when ...?"

Avoid trivia (exact variable names, line counts, package versions). Target the decisions and
boundaries a developer must know to change the code safely. Use the read-only tools to verify
every reference answer against the actual code and cite the files you relied on in `evidence`.
Keep each question to two sentences and each reference answer to three.

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
