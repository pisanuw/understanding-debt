"""The versioned JSON contract (Phase 4a): the shapes the CLI writes and the hosted service
reads, frozen as schemas with a small validator (stdlib only, a subset of JSON Schema: type,
properties, required, items, enum, const, minimum, maximum, additionalProperties, anyOf).

    kind             file or payload                          producer
    map              .udebt/map.json (versions 1-3)            udebt map
    record           one line of .udebt/records/<dev>.jsonl    quiz, dig, review, tutor, ...
    snapshots        .udebt/snapshots.json (version 1)         udebt snapshot
    commits          .udebt/commits.json                       udebt weigh
    responsibility   .udebt/responsibility.json                udebt responsibility seed
    export           `udebt export --format json` (udebt-export v1), the import format
    import           what `udebt push` sends: an export with an optional sanitized `map`
    quizzes          advance quizzes (udebt-advance-quizzes v1)   udebt worker quizzes

`CONTRACT` is the contract's version; an export carries it as `contract`. A field may be added
without a version change (consumers ignore what they do not know: `additionalProperties` is
open everywhere); removing or retyping one bumps it. Validation is strict about what a
consumer relies on and silent about the rest.

`quizzes` (added 2026-11-15, no version change: adding a kind changes no existing one) is the
file the hosted site serves next to a sample's static JSON: questions written in advance per
unit and ladder level 1-3, each with its reference answer (and, at level 1, the orientation
shown before it), which a visitor who is not signed in answers and checks against the reference
without a model call. Its own `version` is the file format's, bumped when a field is removed or
retyped, as `contract` is for the rest.

`sanitize_map` is the map as the hosted service stores it: the descriptions, paths, history
and orientations, the tour; never the walkthroughs (narration tied to line ranges of the
code) or the pooled questions with their reference answers. Metadata only.
"""

from __future__ import annotations

import copy
from typing import Any

CONTRACT = 1
VERDICTS = ["correct", "partial", "wrong", "unknown"]
LEVELS = ["OUTSIDE", "HIGH", "CAUTION", "OK"]


class ContractError(ValueError):
    def __init__(self, kind: str, errors: list[str]):
        super().__init__(f"{kind}: " + "; ".join(errors[:5]) + (" ..." if len(errors) > 5 else ""))
        self.kind = kind
        self.errors = errors


def _t(*types: str, **kw: Any) -> dict[str, Any]:
    return {"type": list(types) if len(types) > 1 else types[0], **kw}


STR, NUM, INT, BOOL = _t("string"), _t("number"), _t("integer"), _t("boolean")
OPT_STR = _t("string", "null")
STRS = _t("array", items=STR)
NUM_MAP = _t("object", additionalProperties=_t("number", "null"))

HISTORY = _t("object", required=["sha"], properties={"sha": STR, "date": OPT_STR,
             "klass": OPT_STR, "subject": OPT_STR, "author": OPT_STR})  # fmt: skip

_UNIT_PROPS: dict[str, Any] = {
    "id": STR,
    "name": STR,
    "kind": OPT_STR,
    "summary": OPT_STR,
    "decision": OPT_STR,
    "alternatives": OPT_STR,
    "why": OPT_STR,
    "paths": STRS,
    "centrality": _t("integer", "null", minimum=1, maximum=5),
    "scope": _t("string", enum=["architectural", "blackbox"]),
    "importance": _t("number", "null"),
    "history": _t("array", items=HISTORY),
    "questions": _t("object"),
    "orientation": _t("object", "null"),
    "walkthrough": _t("object", "null"),
}
CHILD = _t("object", required=["id", "name", "paths"], properties=_UNIT_PROPS)
UNIT = _t("object", required=["id", "name", "paths"],
          properties={**_UNIT_PROPS, "children": _t("array", items=CHILD)})  # fmt: skip
MAP = _t(
    "object",
    required=["units"],
    properties={
        "version": _t("integer", minimum=1, maximum=3),
        "generated_at": OPT_STR,
        "head": OPT_STR,
        "units": _t("array", items=UNIT),
        "tour": _t("object", "null"),
    },
)

RECORD = _t(
    "object",
    required=["ts", "unit", "verdict"],
    properties={
        "ts": STR,
        "unit": STR,
        "verdict": _t("string", enum=VERDICTS),
        "level": _t("integer", "null", minimum=1, maximum=5),
        "confidence": _t("integer", minimum=1, maximum=3),
        "head": OPT_STR,
        "question": OPT_STR,
        "type": STR,
        "source": STR,
        "dev": STR,
        "forced": BOOL,
        "revealed": BOOL,
        "variants": STRS,
        "review": _t("object"),
        "origin": STR,
    },
)

DEV_ROW = _t(
    "object",
    required=["und", "debt"],
    properties={
        "index": _t("number", "null"),
        "und": NUM_MAP,
        "debt": NUM_MAP,
        "quizzed": STRS,
        "depth": _t("object", additionalProperties=_t("integer", "null")),
    },
)
POINT = _t(
    "object",
    required=["t", "sha", "commits", "mass", "units", "devs"],
    properties={
        "t": STR,
        "sha": STR,
        "commits": INT,
        "mass": NUM_MAP,
        "units": _t("object"),
        "devs": _t("object", additionalProperties=DEV_ROW),
    },
)
EVENT = _t(
    "object",
    required=["sha", "dev", "unit", "level", "mass", "understanding"],
    properties={
        "sha": STR,
        "dev": STR,
        "unit": STR,
        "level": _t("string", enum=LEVELS),
        "mass": NUM,
        "understanding": NUM,
        "weight": NUM,
        "date": OPT_STR,
        "landed": OPT_STR,
        "klass": OPT_STR,
        "subject": OPT_STR,
        "source": OPT_STR,
        "in_set": _t("boolean", "null"),
    },
)
SNAPSHOTS = _t(
    "object",
    required=["version", "fingerprint", "points", "risk"],
    properties={
        "version": _t("integer", const=1),
        "fingerprint": _t("object"),
        "points": _t("array", items=POINT),
        "risk": _t("object", required=["events"],
                   properties={"through": OPT_STR, "events": _t("array", items=EVENT)}),
    },
)  # fmt: skip
COMMITS = _t(
    "object",
    required=["version", "commits"],
    properties={
        "version": INT,
        "commits": _t("object", additionalProperties=_t(
            "object", required=["weight"],
            properties={"sha": STR, "weight": _t("number", minimum=0, maximum=1)})),
    },
)  # fmt: skip
RESPONSIBILITY = _t(
    "object",
    required=["developers"],
    properties={
        "version": INT,
        "developers": _t("object", additionalProperties=_t(
            "object", properties={"units": _t("object", additionalProperties=STR)})),
    },
)  # fmt: skip
EVIDENCE = _t(
    "object",
    required=["ts", "dev", "unit", "verdict"],
    properties={
        "ts": STR,
        "dev": STR,
        "unit": STR,
        "type": STR,
        "level": _t("integer", "null", minimum=1, maximum=5),
        "forced": BOOL,
        "verdict": _t("string", enum=VERDICTS),
        "confidence": _t("integer", "null", minimum=1, maximum=3),
        "evidence": _t("number", "null"),
        "head": OPT_STR,
        "question": OPT_STR,
    },
)
EXPORT_UNIT = _t(
    "object",
    required=["id", "name"],
    properties={
        "id": STR,
        "name": STR,
        "parent": OPT_STR,
        "kind": OPT_STR,
        "scope": STR,
        "importance": _t("number", "null"),
        "ai_share": _t("number", "null"),
        "you": NUM_MAP,
        "understanding": NUM_MAP,
        "depth": _t("object"),
        "basis": _t("object", additionalProperties=STR),
        "holders": STRS,
        "responsible": STRS,
    },
)
EXPORT = _t(
    "object",
    required=[
        "format", "version", "repo", "head", "settings", "devs", "units", "snapshots",
        "evidence", "risk_events", "contributors",
    ],
    properties={
        "format": _t("string", const="udebt-export"),
        "version": _t("integer", const=1),
        "contract": _t("integer", minimum=1),
        "repo": STR,
        "head": STR,
        "generated_at": OPT_STR,
        "settings": _t("object", required=["known_threshold"],
                       properties={"known_threshold": NUM, "bus_target": INT}),
        "devs": STRS,
        "units": _t("array", items=EXPORT_UNIT),
        "responsibility": _t("object", additionalProperties=STRS),
        "contributors": _t("array", items=_t("object", properties={"key": OPT_STR})),
        "snapshots": _t("array", items=POINT),
        "evidence": _t("array", items=EVIDENCE),
        "risk_events": _t("array", items=EVENT),
        "heavy_commits": _t("array", items=_t("object", required=["sha"])),
        "fingerprint": _t("object"),
        "map": MAP,
    },
)  # fmt: skip

ADVANCE_QUESTION = _t(
    "object",
    required=["unit_id", "level", "question", "reference_answer"],
    properties={
        "unit_id": STR,
        "level": _t("integer", minimum=1, maximum=3),
        "question": STR,
        "variants": STRS,
        "reference_answer": STR,
        "evidence": STRS,
        "orientation": _t("object", required=["purpose", "decision", "where", "shaped_by"],
                          properties={"purpose": STR, "decision": STR, "shaped_by": STR,
                                      "where": _t("array", items=_t("object", required=["path"],
                                                                    properties={"path": STR}))}),
    },
)  # fmt: skip
QUIZZES = _t(
    "object",
    required=["format", "version", "repo", "sha", "questions"],
    properties={
        "format": _t("string", const="udebt-advance-quizzes"),
        "version": _t("integer", const=1),
        "repo": STR,
        "name": STR,
        "sha": STR,
        "generated_at": OPT_STR,
        "levels": _t("array", items=_t("integer", minimum=1, maximum=3)),
        "per": INT,
        "calls": INT,
        "questions": _t("array", items=ADVANCE_QUESTION),
    },
)

SCHEMAS: dict[str, dict[str, Any]] = {
    "map": MAP,
    "record": RECORD,
    "snapshots": SNAPSHOTS,
    "commits": COMMITS,
    "responsibility": RESPONSIBILITY,
    "export": EXPORT,
    "import": EXPORT,
    "evidence": EVIDENCE,
    "quizzes": QUIZZES,
}

_PY = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _is(value: Any, t: str) -> bool:
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    return isinstance(value, _PY[t])


def _check(value: Any, schema: dict[str, Any], path: str, out: list[str]) -> None:
    if "anyOf" in schema:
        if not any(not _errors(value, s) for s in schema["anyOf"]):
            out.append(f"{path}: matches none of the allowed shapes")
        return
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_is(value, x) for x in types):
            out.append(f"{path}: expected {' or '.join(types)}, got {type(value).__name__}")
            return
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: expected {schema['const']!r}, got {value!r}")
    if "enum" in schema and value is not None and value not in schema["enum"]:
        out.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: {value} < {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: {value} > {schema['maximum']}")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                out.append(f"{path}: missing {k!r}")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                _check(v, props[k], f"{path}.{k}", out)
            elif extra is False:
                out.append(f"{path}: unexpected {k!r}")
            elif isinstance(extra, dict):
                _check(v, extra, f"{path}.{k}", out)
    elif isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            _check(v, schema["items"], f"{path}[{i}]", out)


def _errors(value: Any, schema: dict[str, Any]) -> list[str]:
    out: list[str] = []
    _check(value, schema, "$", out)
    return out


def validate(kind: str, data: Any) -> list[str]:
    """The ways `data` breaks the contract for `kind` (empty when it conforms)."""
    if kind not in SCHEMAS:
        raise KeyError(f"no contract for {kind!r}; kinds: {', '.join(SCHEMAS)}")
    errors = _errors(data, SCHEMAS[kind])
    if kind in ("export", "import") and isinstance(data, dict) and not errors:
        errors += _export_consistency(data)
    return errors


def check(kind: str, data: Any) -> Any:
    """`data`, or ContractError listing what is wrong with it."""
    errors = validate(kind, data)
    if errors:
        raise ContractError(kind, errors)
    return data


def _export_consistency(data: dict[str, Any]) -> list[str]:
    """Cross-field rules a schema cannot say: every developer row, evidence row and risk
    event names a developer in `devs` or a unit in `units`."""
    out = []
    contract = data.get("contract", CONTRACT)
    if contract > CONTRACT:
        out.append(f"$.contract: {contract} is newer than this udebt understands ({CONTRACT})")
    devs = set(data.get("devs", []))
    units = {u["id"] for u in data.get("units", [])}
    for i, p in enumerate(data.get("snapshots", [])):
        for d in p.get("devs", {}):
            if d not in devs:
                out.append(f"$.snapshots[{i}].devs: {d!r} is not in $.devs")
                break
    for key in ("evidence", "risk_events"):
        for i, e in enumerate(data.get(key, [])):
            if e.get("unit") not in units and key == "risk_events":
                base = str(e.get("unit", "")).split(" (rest)")[0]
                if base not in units:
                    out.append(f"$.{key}[{i}].unit: {e.get('unit')!r} is not in $.units")
    return out[:20]


MAP_KEEP = ("version", "generated_at", "head", "rescored_at", "tour")
UNIT_DROP = ("walkthrough", "questions", "provenance", "churn")


def sanitize_map(map_data: dict[str, Any]) -> dict[str, Any]:
    """The map as the hosted service may store it (metadata only): units with their
    descriptions, paths, centrality, scope, importance, history and orientation, sub-units the
    same; the tour. Walkthroughs (narration of line ranges), pooled questions (with reference
    answers), provenance and churn are dropped."""
    out = {k: copy.deepcopy(map_data[k]) for k in MAP_KEEP if k in map_data}

    def unit(u: dict[str, Any]) -> dict[str, Any]:
        v = {k: copy.deepcopy(x) for k, x in u.items() if k not in UNIT_DROP and k != "children"}
        if u.get("children"):
            v["children"] = [unit(c) for c in u["children"]]
        return v

    out["units"] = [unit(u) for u in map_data.get("units", [])]
    return out
