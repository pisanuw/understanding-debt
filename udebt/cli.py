"""udebt command line.

udebt init                      create .udebt/ in this repo
udebt clusters                  structural clusters from imports and co-change (no LLM)
udebt map                       two-level architectural units with the claude CLI
udebt units                     list units and sub-units with scope and importance
udebt responsibility [dev]      who is expected to understand which units (seed, add, remove)
udebt scope <id> <scope>        mark a unit architectural | blackbox, for you or --team
udebt quiz [-n N] [--deep]      answer prediction questions, get graded, record evidence
udebt status [unit] [--deep]    debt per unit and a 0-100 debt index (--team: bus target)
udebt risk [range]              a change's weight per unit against your understanding
udebt dig [<id>]                guided walkthrough of the worst unit (or a chosen one)
udebt directed add <session>    count agent commits you directed as your assisted work
udebt experiment synthetic --snapshots <rev>,...   synthetic developers frozen at old snapshots
udebt cache [status|rebuild]    the parsed-commit cache under .udebt/ (gitignored)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__, repo, scoring, structure
from . import responsibility as resp_mod
from . import risk as risk_mod
from . import units as hier
from .contributors import as_dict, contributors, grouped
from .dig import walkthrough
from .llm import LLMError, get_llm
from .mapping import (
    build_map,
    commits_for,
    discover,
    mappable_files,
    rescore_map,
    weights_for,
)
from .prompts import WEIGH_SCHEMA, weigh_prompt
from .provenance import (
    BUILTIN_SIGNATURES,
    session_id,
    sessions_of,
    summarize,
    unit_provenance,
)
from .quiz import ask_interactively, compute_scores, generate_questions, grade, pick_units
from .quiz import record as record_results
from .store import Store
from .weights import apply_refinement, save_cache


def _ctx(args) -> tuple[Path, Store]:
    try:
        root = repo.find_root(Path(args.repo) if args.repo else None)
    except repo.NotARepo as e:
        raise SystemExit(f"Not inside a git repository: {e}") from e
    return root, Store(root)


def _llm(store: Store, args):
    try:
        return get_llm(model=args.model or store.config.model, verbose=args.verbose)
    except LLMError as e:
        raise SystemExit(str(e)) from e


def _email(args, root: Path) -> str:
    return args.as_user or repo.identity(root)[1]


def _bar(x: float, width: int = 10) -> str:
    n = round(max(0.0, min(1.0, x)) * width)
    return "#" * n + "." * (width - n)


def _known_emails(store: Store, commits) -> set[str]:
    cfg = store.config
    known = {c.author_email.lower() for c in commits}
    for p, aliases in cfg.identities.items():
        known |= {p.lower(), *(a.lower() for a in aliases)}
    return known


def _dev_key(args, root: Path, store: Store, ref: str | None, commits=None) -> str:
    """A developer reference (email, alias, CODEOWNERS handle or `me`) -> developer key."""
    cfg = store.config
    if ref in (None, "me"):
        return cfg.primary(_email(args, root))
    if ref.startswith("@"):
        known = _known_emails(store, commits if commits is not None else [])
        emails = resp_mod.owner_emails(ref, cfg, known)
        if not emails:
            raise SystemExit(
                f"{ref} is not mapped to an email yet. Add it under `handles` in "
                f".udebt/config.json."
            )
        return emails[0]
    return cfg.primary(ref)


def _my_set(resp, key: str, units) -> set[str]:
    """The developer's responsibility set, limited to units the map still has."""
    return resp.ids(key) & set(hier.index(units))


def _width(units_shown, floor: int = 22) -> int:
    return min(44, max([floor, *(len(u["id"]) + 2 for u, _ in units_shown)]))


# -- commands ------------------------------------------------------------------


def cmd_init(args) -> int:
    root, store = _ctx(args)
    store.init()
    print(f"Initialised {store.dir}")
    print("Next: `udebt map` to extract the architectural units.")
    return 0


def cmd_map(args) -> int:
    root, store = _ctx(args)
    store.require()
    if args.rescore:
        data = rescore_map(root, store)
        print(f"Rescored {len(data['units'])} units with current commit weights (no LLM call).")
        if data.get("version", 1) < 3:
            print("This map is flat (version 2 or older): re-run `udebt map` for sub-units.")
        _print_units(data["units"])
        return 0
    llm = _llm(store, args)
    print("Mapping architecture with claude (this reads the code; expect a minute or two)...")
    try:
        data = build_map(root, store, llm, max_units=args.max_units, max_children=args.max_children)
    except LLMError as e:
        raise SystemExit(str(e)) from e
    st = data.get("structure", {})
    n_kids = sum(len(hier.children_of(u)) for u in data["units"])
    print(_structure_line(st))
    print(
        f"Wrote {len(data['units'])} units ({n_kids} sub-units) to "
        f"{store.map_path.relative_to(root)}; they cover {int(100 * st.get('coverage', 0))}% "
        f"of the clustered non-test source files"
    )
    _print_units(data["units"])
    return 0


def _structure_line(st: dict[str, Any]) -> str:
    secs = sum(st.get("seconds", {}).values())
    clusters = st.get("clusters", [])
    subs = sum(len(c.get("children", [])) for c in clusters)
    return (
        f"Structure: {st.get('nodes', 0)} source files, {st.get('edges', 0)} edges "
        f"({st.get('import_edges', 0)} import, {st.get('cochange_edges', 0)} co-change), "
        f"{len(clusters)} clusters ({subs} sub-clusters) in {secs:.1f}s"
    )


def cmd_clusters(args) -> int:
    root, store = _ctx(args)
    store.require()
    files = mappable_files(root)
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    s = discover(root, store, files, commits, weights)
    if args.json:
        import json as _json

        print(_json.dumps(s.summary(), indent=2))
        return 0
    print(_structure_line(s.summary()))
    print(
        f"Languages parsed for imports: "
        f"{', '.join(f'{k} {v}' for k, v in s.languages.items()) or 'none'}; "
        f"timings {s.seconds}\n"
    )
    print(structure.prompt_block(s, max_clusters=args.top))
    print("\nThese are what `udebt map` hands the model to name, merge or split.")
    return 0


def cmd_units(args) -> int:
    root, store = _ctx(args)
    store.require()
    _print_units(store.require_map()["units"])
    return 0


def _print_units(units: list[dict[str, Any]]) -> None:
    shown = hier.rows(units, deep=True)
    w = _width(shown)
    print()
    print(f"{'id':<{w}} {'kind':<9} {'scope':<13} {'imp':>5}  name")
    for u, depth in shown:
        uid = ("  " * depth) + u["id"]
        print(
            f"{uid:<{w}} {u.get('kind', ''):<9} {u.get('scope', 'architectural'):<13} "
            f"{u.get('importance', 0):>5.2f}  {u['name']}"
        )


def cmd_scope(args) -> int:
    root, store = _ctx(args)
    store.require()
    m = store.require_map()
    unit = hier.resolve(m["units"], args.unit)
    if unit is None:
        raise SystemExit(f"Unknown unit id: {args.unit}")
    uid = unit["id"]
    if args.team:
        unit["scope"] = args.scope
        store.save_map(m)
        print(f"{uid}: scope set to {args.scope} team-wide")
        return 0
    key = _dev_key(args, root, store, args.for_dev)
    team_scope = {u["id"]: u.get("scope", "architectural") for u in hier.flat(m["units"])}
    parent = hier.parent_of(m["units"]).get(uid)
    team, _ = resp_mod.effective_scope(uid, parent, team_scope, {})
    resp = resp_mod.load(store)
    # an override equal to the team's choice is noise; drop it
    resp.set_scope(key, uid, None if args.scope == team else args.scope)
    resp.save(store.responsibility_path)
    print(f"{uid}: {args.scope} for {key} (team-wide: {team}; `--team` changes that)")
    return 0


def cmd_quiz(args) -> int:
    root, store = _ctx(args)
    store.require()
    m = store.require_map()
    units = m["units"]
    email = _email(args, root)
    llm = _llm(store, args)
    resp = resp_mod.load(store)
    scores = compute_scores(root, store, units, email, resp=resp)
    mine = _my_set(resp, store.config.primary(email), units)
    within = None
    if mine and not args.unit:
        touching = risk_mod.touched_units(
            hier.level(units, args.deep), risk_mod.working_files(root)
        )
        within = hier.at_level(mine, units, args.deep) | touching
    picked = pick_units(units, scores, args.num, args.unit or None, deep=args.deep, within=within)
    if not picked:
        raise SystemExit("Nothing to quiz: every unit in view is blackbox or out of your set.")
    where = " (from your responsibility set)" if within else ""
    print(f"Quizzing {email}{where} on: {', '.join(u['id'] for u in picked)}")
    print("Generating questions from the code...")
    try:
        questions = generate_questions(root, llm, picked, per_unit=args.per_unit)
        answered = ask_interactively(questions)
        print("\nGrading against the code...")
        results = grade(root, llm, answered)
    except LLMError as e:
        raise SystemExit(str(e)) from e
    record_results(store, root, email, results)
    print()
    for r in results:
        print(f"[{r['verdict'].upper():<8}] {r['unit_id']}: {r['question']}")
        print(f"           {r['feedback']}")
    print()
    args.unit_ref = None
    return cmd_status(args)


def _prov_line(commits, mine: set[str] | None = None) -> str:
    sm = summarize(commits, mine)
    agents = ", ".join(f"{k} {v}" for k, v in _top(sm["agents"])) or "none detected"
    bots = f"; bots: {', '.join(f'{k} {v}' for k, v in _top(sm['bots']))}" if sm["bots"] else ""
    directed = f" (+{sm['directed']} directed)" if sm["directed"] else ""
    mine_ = f"; yours: {sm['dev']}{directed}" if mine else ""
    return (
        f"{sm['total']} commits: {sm['ai']} ai, {sm['assisted']} assisted, "
        f"{sm['human']} human, {sm['bot']} bot (agents: {agents}{bots}){mine_}"
    )


def _top(counts: dict[str, int], n: int = 6) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda kv: -kv[1])[:n]


def _note(s_, u, prov, by_score) -> str:
    """The status note for one row: why the understanding is what it is."""
    if s_.scope != "architectural":
        who = "for you" if s_.scope_origin == "you" else "team-wide"
        return f"blackbox {who} (excluded)"
    pre = ""
    if s_.scope_origin == "you" and u.get("scope", "architectural") != "architectural":
        pre = "architectural for you (team: blackbox); "
    if s_.source == "none":
        return pre + "unmeasured"
    if s_.source == "children":
        return pre + "from its sub-units' quizzes"
    if s_.source == "parent":
        p = by_score.get(s_.parent)
        pv = f" ({p.verified:.2f})" if p and p.verified is not None else ""
        return pre + f"half of the parent's quiz{pv}"
    if s_.source == "prior" and s_.understanding < 0.005:
        return pre + (
            f"prior from your commits eroded by {prov.others_since_dev} commits "
            f"(mass {prov.others_since_dev_w}) by others"
        )
    if s_.source == "prior":
        note = "prior from your commits"
        if prov.others_since_dev:
            note += (
                f", {prov.others_since_dev} commits (mass {prov.others_since_dev_w}) "
                "by others since"
            )
        return pre + note
    if s_.commits_since:
        return pre + f"quiz, decayed from {s_.verified:.2f} by mass {s_.commits_since}"
    return pre + "quiz"


def cmd_status(args) -> int:
    root, store = _ctx(args)
    store.require()
    m = store.require_map()
    units = m["units"]
    cfg = store.config
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    resp = resp_mod.load(store)
    focus = None
    ref = getattr(args, "unit_ref", None)
    if ref:
        focus = hier.resolve(units, ref)
        if focus is None:
            raise SystemExit(f"Unknown unit id: {ref}")
    deep = bool(getattr(args, "deep", False) or focus is not None)

    if getattr(args, "team", False):
        return _team_status(root, store, units, commits, weights, resp, deep)

    email = _email(args, root)
    key = cfg.primary(email)
    mine = cfg.emails_for(email)
    scores = compute_scores(root, store, units, email, commits=commits, weights=weights, resp=resp)
    by_score = {s_.unit_id: s_ for s_ in scores}
    my_set = _my_set(resp, key, units)
    touching = risk_mod.touched_units(hier.level(units, deep), risk_mod.working_files(root))
    parents = hier.parent_of(units)
    members = hier.Membership(units)
    shown = hier.rows(units, deep, also=my_set)
    if focus is not None:
        fid = focus["id"]
        shown = [(u, d) for u, d in shown if fid in (u["id"], parents.get(u["id"]))]
    w = _width(shown)
    print(f"\nUnderstanding debt for {email} at {repo.head(root)[:8]}")
    print(_prov_line(commits, mine))
    print(
        f"{'unit':<{w}} {'set':>3} {'imp':>5} {'ai%':>4} {'you%':>4} {'und':>5}  {'debt':>5}  "
        f"{'':<10} note"
    )
    # sort the top level by debt; sub-units follow their parent, also by debt
    tops = sorted([u for u, d in shown if d == 0], key=lambda u: -by_score[u["id"]].debt)
    kids = {u["id"]: [] for u in tops}
    for u, d in shown:
        if d == 1:
            kids.setdefault(parents[u["id"]], []).append(u)
    ordered = []
    for u in tops:
        ordered.append((u, 0))
        ordered += [
            (c, 1) for c in sorted(kids.get(u["id"], []), key=lambda c: -by_score[c["id"]].debt)
        ]
    if focus is not None and not ordered:
        ordered = shown
    for u, depth in ordered:
        s_ = by_score[u["id"]]
        prov = unit_provenance(commits, u.get("paths", []), mine, weights, members.split(u["id"]))
        mark = ""
        if my_set:
            if hier.covers(my_set, u["id"], units):
                mark = "*"
            elif any(c["id"] in my_set for c in hier.children_of(u)):
                mark = "~"
        if u["id"] in touching:
            mark += "t"
        n_kids = len(hier.children_of(u))
        extra = f" ({n_kids} sub-units)" if n_kids and not deep else ""
        label = ("  " * depth) + u["id"]
        print(
            f"{label:<{w}} {mark:>3} {s_.importance:>5.2f} {int(100 * prov.ai_share):>4} "
            f"{int(100 * prov.dev_share):>4} {s_.understanding:>5.2f}  {s_.debt:>5.2f}  "
            f"{_bar(1 - s_.understanding):<10} {_note(s_, u, prov, by_score)}  "
            f"[{u['name']}]{extra}"
        )
    level_ids = {u["id"] for u in hier.level(units, deep)}
    all_index = scoring.debt_index(scores, only=level_ids)
    finer = deep and hier.has_children(units)
    view = "units, sub-units where they exist" if finer else "architectural units"
    if my_set:
        ids = hier.at_level(my_set, units, deep) | touching
        n_t = len(touching - hier.at_level(my_set, units, deep))
        t_txt = f" + {n_t} you are touching" if n_t else ""
        print(
            f"\nDebt index: {scoring.debt_index(scores, only=ids)} / 100   (your responsibility "
            f"set: {len(ids) - n_t} units{t_txt}; over all {view}: {all_index})"
        )
        print("set: * in your responsibility set, ~ part of it is, t you are touching it now.")
    else:
        what = "sub-units where they exist" if finer else "every architectural unit"
        print(
            f"\nDebt index: {all_index} / 100   (0 = you understand {what}; unmeasured counts as 0)"
        )
        print(
            "You have no responsibility set, so every unit counts. "
            "`udebt responsibility seed` proposes one."
        )
    print(
        "ai% = weighted share of the unit's mass by an AI agent (assisted counts half). "
        "you% = share you authored (assisted counts half). Bot commits are left out of both."
    )
    if hier.has_children(units) and not deep:
        print("Sub-units: `udebt status --deep`, or `udebt status <unit>` for one unit.")
    pool = [
        s_
        for s_ in scores
        if s_.unit_id in (hier.at_level(my_set, units, deep) if my_set else level_ids)
    ]
    worst = scoring.quiz_priority(pool)
    if worst:
        print(f"Next: `udebt dig {worst[0].unit_id}` or `udebt quiz`")
    return 0


def _team_members(store: Store, resp, commits) -> dict[str, list[dict[str, Any]]]:
    """developer key -> their quiz records, for everyone with records or a set."""
    cfg = store.config
    by_slug = {repo.slug(e): e for e in _known_emails(store, commits) | set(resp.developers)}
    out: dict[str, list[dict[str, Any]]] = {}
    for slug, recs in store.all_records().items():
        email = next((r["dev"] for r in recs if r.get("dev")), None) or by_slug.get(slug, slug)
        out.setdefault(cfg.primary(email), []).extend(recs)
    for dev in resp.developers:
        if resp.has_set(dev):
            out.setdefault(dev, [])
    return dict(sorted(out.items()))


def _team_status(root, store, units, commits, weights, resp, deep) -> int:
    cfg = store.config
    members = _team_members(store, resp, commits)
    if not members:
        print(
            "No quiz records or responsibility sets yet. "
            "Run `udebt quiz` or `udebt responsibility seed`."
        )
        return 0
    per_dev = {
        dev: compute_scores(
            root, store, units, dev, records=recs, commits=commits, weights=weights, resp=resp
        )
        for dev, recs in members.items()
    }
    team = scoring.team_view(per_dev, cfg.known_threshold)
    team_scope = {u["id"]: u.get("scope", "architectural") for u in hier.flat(units)}
    parents = hier.parent_of(units)
    for uid, t in team.items():
        t["scope"] = resp_mod.effective_scope(uid, parents.get(uid), team_scope, {})[0]
    shown = hier.rows(units, deep)
    w = _width(shown)
    target = cfg.bus_target
    quizzed = any(recs for recs in members.values())
    print(f"\n{_prov_line(commits)}")
    print(
        f"Team view ({len(per_dev)} developer(s)); known threshold {cfg.known_threshold}, "
        f"bus target {target}"
    )
    if not quizzed:
        print(
            f"No quiz evidence yet; holders need a quiz score >= {cfg.known_threshold} "
            f"(the own-commit prior stops at {scoring.PRIOR_CAP}), so every count below is "
            "0 until someone takes `udebt quiz`."
        )
    bus = "bus" if quizzed else "quizzed"
    print(f"{'unit':<{w}} {'imp':>5} {'ai%':>4} {'team und':>9} {bus:>7}  holders; responsible")
    for u, depth in shown:
        t = team[u["id"]]
        flag = "" if t["scope"] == "architectural" else "  (blackbox)"
        ai = int(100 * u.get("provenance", {}).get("ai_share", 0))
        assigned = resp.holders_of(u["id"], units)
        label = ("  " * depth) + u["id"]
        print(
            f"{label:<{w}} {t['importance']:>5.2f} {ai:>4} {t['understanding']:>9.2f} "
            f"{t['bus_factor']:>4}/{target:<2}  {', '.join(t['holders']) or '-'}; "
            f"{', '.join(assigned) or '-'}{flag}"
        )
    level_ids = [u["id"] for u in hier.level(units, deep)]
    team_scores = [
        scoring.UnitScore(
            uid,
            team[uid]["importance"],
            team[uid]["understanding"],
            0,
            team[uid]["understanding"],
            team[uid]["scope"],
        )
        for uid in level_ids
    ]
    print(f"\nTeam debt index: {scoring.debt_index(team_scores)} / 100")
    for dev, sc in per_dev.items():
        mine = _my_set(resp, dev, units)
        only = hier.at_level(mine, units, deep) if mine else set(level_ids)
        what = f"over their set of {len(only)}" if mine else "no set, over all units"
        print(f"  {dev:<34} {scoring.debt_index(sc, only=only):>5}   ({what})")
    below = scoring.below_target(team, level_ids, target)
    arch = [uid for uid in level_ids if team[uid]["scope"] == "architectural"]
    if quizzed:
        print(
            f"\nBus target: {len(arch) - len(below)} of {len(arch)} architectural units have "
            f"at least {target} holders (understanding >= {cfg.known_threshold})."
        )
    else:
        print(
            f"\nBus target: not measured yet: no quiz evidence yet; holders need a quiz score "
            f">= {cfg.known_threshold}. {len(arch)} architectural units, each needing {target}."
        )
    if below:
        print("Below target:")
        for uid in below:
            assigned = resp.holders_of(uid, units)
            need = target - team[uid]["bus_factor"]
            who = (
                ", ".join(assigned)
                if assigned
                else "nobody (assign one: `udebt responsibility <dev> add " + uid + "`)"
            )
            print(f"  {uid:<{w}} needs {need} more; responsible: {who}")
    unassigned = resp.unassigned(units, arch)
    if unassigned and resp.developers:
        print(f"In nobody's responsibility set: {', '.join(unassigned)}")
    return 0


def cmd_history(args) -> int:
    root, store = _ctx(args)
    store.require()
    m = store.require_map()
    email = _email(args, root)
    mine = store.config.emails_for(email)
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    scores = {
        s_.unit_id: s_
        for s_ in compute_scores(root, store, m["units"], email, commits=commits, weights=weights)
    }
    by_sha: dict[str, dict[str, Any]] = {}
    for u in hier.flat(m["units"]):
        for h in u.get("history", []):
            row = by_sha.setdefault(h["sha"], {**h, "units": []})
            row["units"].append(u["id"])
    if not by_sha:
        print("The map has no history links. Re-run `udebt map`.")
        return 0
    rows = sorted(by_sha.values(), key=lambda r: r["date"])
    print(f"\nDecisions that shaped this repo, oldest first\n{_prov_line(commits, mine)}\n")
    for r in rows:
        who = r["klass"]
        if r["klass"] != "ai" and (r.get("email") or "").lower() in mine:
            who = "you" if r["klass"] == "human" else "you+ai"
        units = ", ".join(f"{uid} ({scores[uid].understanding:.2f})" for uid in r["units"])
        w = next((v.weight for k, v in weights.items() if k.startswith(r["sha"])), 0.0)
        print(f"{r['date']}  {r['sha']}  {who:<7} w={w:<5} {r['subject'][:62]}")
        print(f"{'':<30}-> {units}")
    print(
        "\nNumbers in parentheses are your current understanding of that unit (0-1); "
        "w is the commit's weight (0-1)."
    )
    print("you+ai = you committed it with an AI co-author. ai = an agent authored it.")
    return 0


def cmd_alias(args) -> int:
    root, store = _ctx(args)
    store.require()
    cfg = store.config
    primary = _email(args, root)
    group = cfg.emails_for(primary)
    key = next((k for k in cfg.identities if k.lower() in group), primary)
    aliases = {a.lower() for a in cfg.identities.get(key, [])}
    aliases |= {e.lower() for e in args.emails if e.lower() != key.lower()}
    cfg.identities[key] = sorted(aliases)
    store.save_config(cfg)
    print(f"{key} also commits as: {', '.join(cfg.identities[key]) or '(none)'}")
    return 0


def cmd_dig(args) -> int:
    root, store = _ctx(args)
    store.require()
    m = store.require_map()
    email = _email(args, root)
    llm = _llm(store, args)
    resp = resp_mod.load(store)
    scores = compute_scores(root, store, m["units"], email, resp=resp)
    mine = _my_set(resp, store.config.primary(email), m["units"])
    within = hier.at_level(mine, m["units"], False) if mine else None
    picked = pick_units(m["units"], scores, 1, [args.unit] if args.unit else None, within=within)
    if not picked:
        raise SystemExit("Nothing to dig into: name a unit, e.g. `udebt dig <unit>`.")
    unit = picked[0]
    print(f"Digging into `{unit['id']}` ({unit['name']})...\n")
    try:
        body, question = walkthrough(root, llm, unit)
    except LLMError as e:
        raise SystemExit(str(e)) from e
    print(body)
    if args.no_check:
        return 0
    if not question:
        print(
            "\n(No check question came back, so nothing was recorded. "
            "Run `udebt quiz --unit " + unit["id"] + "` to verify your understanding.)"
        )
        return 0
    print("\n--- Check yourself ---")
    answered = ask_interactively(
        [
            {
                "unit_id": unit["id"],
                "question": question,
                "reference_answer": "(graded against the code)",
                "evidence": unit.get("paths", []),
            }
        ]
    )
    try:
        results = grade(root, llm, answered)
    except LLMError as e:
        raise SystemExit(str(e)) from e
    record_results(store, root, email, results)
    r = results[0]
    print(f"\n[{r['verdict'].upper()}] {r['feedback']}")
    return 0


def cmd_contributors(args) -> int:
    root, store = _ctx(args)
    store.require()
    cfg = store.config
    email = _email(args, root)
    commits = commits_for(root, store)
    cons = contributors(commits, cfg.identities, cfg.emails_for(email))
    if args.json:
        import json as _json

        print(_json.dumps([as_dict(c) for c in cons], indent=2))
        return 0
    print(f"\n{_prov_line(commits, cfg.emails_for(email))}\n")
    for label, group in grouped(cons).items():
        if not group:
            continue
        print(f"## {label}")
        for c in group:
            you = "  <- you" if c.is_you else ""
            print(
                f"  {c.name:<28} {c.commits:>5} commits  (ai {c.ai}, assisted {c.assisted}, "
                f"human {c.human}, bot {c.bot})  {c.first}..{c.last}  "
                f"conf {c.confidence:.2f}{you}"
            )
            print(f"  {'':<28} {', '.join(c.emails)}")
            print(f"  {'':<28} {'; '.join(c.reasons)}")
        print()
    print(
        "Labels other than AI, Bot and Joint are advisory, from commit metadata only. "
        "Merge identities with `udebt alias`."
    )
    return 0


def cmd_weigh(args) -> int:
    root, store = _ctx(args)
    store.require()
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    by_sha = {c.sha: c for c in commits}
    if args.refine:
        n = args.refine if args.refine > 0 else store.config.refine_top
        cands = sorted(
            (w for w in weights.values() if w.refined is None and w.weight >= 0.3),
            key=lambda w: -w.weight,
        )[:n]
        if not cands:
            print("Nothing left to refine.")
        else:
            llm = _llm(store, args)
            items = []
            for w in cands:
                c = by_sha[w.sha]
                items.append(
                    {
                        "sha": c.short,
                        "klass": c.klass,
                        "date": c.date[:10],
                        "subject": c.subject[:100],
                        "n_files": len(c.files),
                        "files": c.files,
                        "insertions": c.insertions,
                        "deletions": c.deletions,
                        "weight": w.weight,
                        "reasons": w.reasons,
                    }
                )
            print(f"Refining {len(items)} heaviest commits with claude...")
            try:
                res = llm.structured(weigh_prompt(items), WEIGH_SCHEMA, cwd=root, read_tools=True)
            except LLMError as e:
                raise SystemExit(str(e)) from e
            changed = apply_refinement(weights, res.get("commits", []))
            save_cache(store.weights_path, weights)
            print(f"Refined {changed} commits.")
    total = sum(w.weight for w in weights.values())
    print(
        f"\n{len(weights)} commits, total mass {total:.1f} "
        f"(mean {total / max(1, len(weights)):.2f}); heaviest first:"
    )
    print(f"{'sha':<8} {'w':>5} {'by':<9} {'date':<10} subject / reasons")
    shown = sorted(weights.values(), key=lambda w: -w.weight)[: args.top]
    for w in shown:
        c = by_sha.get(w.sha)
        if not c:
            continue
        tag = f"{'*' if w.refined else ' '}{w.weight:.2f}"
        print(f"{c.short:<8} {tag:>5} {c.klass:<9} {c.date[:10]:<10} {c.subject[:60]}")
        print(f"{'':<35} {'; '.join(w.reasons)}")
    print("\n* = LLM-refined. Weight 0 = cannot create debt (merges, lockfiles, whitespace).")
    return 0


def cmd_risk(args) -> int:
    root, store = _ctx(args)
    store.require()
    m = store.require_map()
    units = m["units"]
    cfg = store.config
    email = _email(args, root)
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    resp = resp_mod.load(store)
    scores = {
        s_.unit_id: s_
        for s_ in compute_scores(
            root, store, units, email, commits=commits, weights=weights, resp=resp
        )
    }
    if args.staged:
        c, w = risk_mod.staged_commit(root)
        changes, wmap = [c], {**weights, c.sha: w}
        what = f"staged changes ({len(c.files)} files, weight {w.weight})"
    else:
        rng = args.range or "@{upstream}..HEAD"
        changes = risk_mod.commits_in_range(root, rng, commits)
        wmap = weights
        what = f"{rng} ({len(changes)} commits)"
    if not changes or (args.staged and not changes[0].files):
        print(f"Nothing to assess in {what}.")
        return 0
    my_set = _my_set(resp, cfg.primary(email), units)
    risks = risk_mod.assess(
        units,
        scores,
        changes,
        wmap,
        cfg.known_threshold,
        responsible=my_set or None,
        deep=args.deep,
    )
    level = risk_mod.overall(risks)
    print(f"\nRisk for {email}: {what}  ->  {level}")
    if not risks:
        print("No architectural unit touched.")
        return 0
    w = min(44, max([22, *(len(r.unit_id) + 2 for r in risks)]))
    print(f"{'level':<8} {'unit':<{w}} {'set':<4} {'mass':>5} {'und':>5}  files")
    for r in risks:
        in_set = {True: "in", False: "OUT", None: "-"}[r.in_set]
        parts = f"  [sub-units: {', '.join(r.parts)}]" if r.parts else ""
        print(
            f"{r.level:<8} {r.unit_id:<{w}} {in_set:<4} {r.mass:>5.2f} {r.understanding:>5.2f}  "
            f"{', '.join(r.files)}{parts}"
        )
    worst = next((r for r in risks if r.level != "OK"), None)
    if worst and worst.level == "OUTSIDE":
        assigned = resp.holders_of(worst.unit_id.split()[0], units)
        ask = ", ".join(assigned) if assigned else "a lead (nobody has it in their set)"
        print(
            f"\nOUTSIDE is the strongest signal udebt gives: this change lands mass "
            f"{worst.mass:.2f} in `{worst.unit_id}`, which is outside your responsibility set, "
            f"and your understanding of it is {worst.understanding:.2f}. Nobody expects you to "
            f"know this unit and nothing shows you do. Ask {ask} to review, or learn it: "
            f"`udebt dig {worst.unit_id.split()[0]}`."
        )
    elif worst:
        print(
            f"\nmass = weight landed in the unit by these changes; und = your understanding "
            f"({worst.source}). Raise it: `udebt dig {worst.unit_id.split()[0]}` (a few minutes)."
        )
    if my_set:
        ids = hier.at_level(my_set, units, args.deep) | {r.unit_id.split()[0] for r in risks}
        print(
            f"Your debt index over your set plus the units this change touches: "
            f"{scoring.debt_index(list(scores.values()), only=ids)} / 100"
        )
    else:
        print(
            "You have no responsibility set, so OUTSIDE cannot fire. "
            "`udebt responsibility seed` proposes one."
        )
    if level in ("HIGH", "OUTSIDE") and args.strict:
        return 1
    return 0


def cmd_responsibility(args) -> int:
    root, store = _ctx(args)
    store.require()
    m = store.require_map()
    units = m["units"]
    rest = list(args.rest)
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    if rest[:1] == ["seed"]:
        if len(rest) > 1:
            raise SystemExit("usage: udebt responsibility seed [--force]")
        rep = resp_mod.seed(
            root, store, units, commits, weights, mappable_files(root), force=args.force
        )
        src = f"{rep.codeowners} and footprint" if rep.codeowners else "footprint (no CODEOWNERS)"
        print(f"Seeded responsibility sets from {src}.")
        resp = resp_mod.load(store)
        for dev in sorted(set(rep.written) | set(rep.kept)):
            n = len(resp.units(dev))
            if dev in rep.kept:
                print(f"  {dev}: kept as curated ({n} units); `--force` re-seeds it")
            else:
                origins = sorted(set(resp.units(dev).values()))
                print(f"  {dev}: {n} unit{'s' if n != 1 else ''} ({', '.join(origins)})")
        if rep.pending:
            print("CODEOWNERS handles with no known email (fill them in under `handles` in")
            print(".udebt/config.json, then run `udebt responsibility seed` again):")
            for h, us in rep.pending.items():
                print(f"  {h}: {', '.join(us)}")
        if rep.unassigned:
            print(f"In nobody's set: {', '.join(rep.unassigned)}")
        print(f"Wrote {store.responsibility_path.relative_to(root)}. Commit it.")
        return 0
    resp = resp_mod.load(store)
    if not rest:
        return _responsibility_overview(root, store, units, commits, weights, resp)
    key = _dev_key(args, root, store, rest[0], commits)
    if len(rest) == 1:
        return _responsibility_detail(root, store, units, commits, weights, resp, key)
    action, refs = rest[1], rest[2:]
    if action not in ("add", "remove") or not refs:
        raise SystemExit("usage: udebt responsibility <dev> add|remove <unit>...")
    if action == "add":
        ids = [u["id"] for u in hier.resolve_all(units, refs)]
        done = resp.add(key, ids)
    else:
        ids = [(hier.resolve(units, r) or {"id": r})["id"] for r in refs]
        done = resp.remove(key, ids)
    resp.save(store.responsibility_path)
    verb = "added to" if action == "add" else "removed from"
    print(
        f"{', '.join(done) or 'nothing'} {verb} {key}'s responsibility set "
        f"({len(resp.units(key))} units)"
    )
    return 0


def _responsibility_overview(root, store, units, commits, weights, resp) -> int:
    if not resp.developers and not resp.pending:
        print("No responsibility sets yet. `udebt responsibility seed` proposes them from")
        print("CODEOWNERS and each developer's contribution footprint.")
        return 0
    when = (resp.seeded_at or "never")[:10]
    print(f"\nResponsibility sets ({store.responsibility_path.relative_to(root)}; seeded {when})")
    print(f"{'developer':<34} {'units':>5} {'index':>6}  set")
    for dev in sorted(resp.developers):
        mine = _my_set(resp, dev, units)
        if not mine:
            continue
        sc = compute_scores(root, store, units, dev, commits=commits, weights=weights, resp=resp)
        idx = scoring.debt_index(sc, only=hier.at_level(mine, units, False))
        shown = ", ".join(sorted(mine))
        print(f"{dev:<34} {len(mine):>5} {idx:>6}  {shown[:90]}{'...' if len(shown) > 90 else ''}")
    for dev, gone in resp.stale(units).items():
        print(f"  {dev}: no longer in the map: {', '.join(gone)} (remove them, or seed --force)")
    if resp.pending:
        print("Pending CODEOWNERS handles (map them under `handles` in .udebt/config.json):")
        for h, us in resp.pending.items():
            print(f"  {h}: {', '.join(us)}")
    nobody = resp.unassigned(
        [u for u in units if u.get("scope", "architectural") == "architectural"]
    )
    if nobody:
        print(f"In nobody's set: {', '.join(nobody)}")
    print(
        "Details: `udebt responsibility <dev>`. "
        "Curate: `udebt responsibility <dev> add|remove <unit>`."
    )
    return 0


def _responsibility_detail(root, store, units, commits, weights, resp, key) -> int:
    mine = _my_set(resp, key, units)
    entries = resp.units(key)
    by_id = hier.index(units)
    sc = {
        s_.unit_id: s_
        for s_ in compute_scores(
            root, store, units, key, commits=commits, weights=weights, resp=resp
        )
    }
    print(f"\nResponsibility set for {key}: {len(mine)} units")
    if not mine:
        print("Empty. `udebt responsibility seed`, or `udebt responsibility <dev> add <unit>`.")
    w = min(44, max([22, *(len(i) + 2 for i in mine)]))
    print(f"{'unit':<{w}} {'from':<11} {'imp':>5} {'und':>5} {'debt':>5}  name")
    for uid in sorted(mine, key=lambda i: -sc[i].debt):
        s_ = sc[uid]
        scope = (
            ""
            if s_.scope == "architectural"
            else f"  (blackbox {'for you' if s_.scope_origin == 'you' else 'team-wide'})"
        )
        print(
            f"{uid:<{w}} {entries.get(uid, ''):<11} {s_.importance:>5.2f} {s_.understanding:>5.2f} "
            f"{s_.debt:>5.2f}  {by_id[uid]['name']}{scope}"
        )
    for uid in sorted(set(entries) - mine):
        print(f"{uid:<{w}} {entries[uid]:<11} (no longer in the map)")
    scopes = resp.scope(key)
    if scopes:
        print("Own scope choices: " + ", ".join(f"{k} {v}" for k, v in sorted(scopes.items())))
    if mine:
        idx = scoring.debt_index(list(sc.values()), only=hier.at_level(mine, units, False))
        print(f"\nDebt index over the set: {idx} / 100")
    return 0


def cmd_directed(args) -> int:
    """Agent commits a developer directed count as their assisted work (half weight) in their
    share, prior and footprint; the commits stay `ai` for display and AI share."""
    root, store = _ctx(args)
    store.require()
    cfg = store.config
    if args.action in ("add", "remove"):
        if not args.refs:
            raise SystemExit(f"usage: udebt directed {args.action} <session-or-agent>...")
        key = _dev_key(args, root, store, args.for_dev)
        entry = cfg.directed.setdefault(key, {"sessions": [], "agents": []})
        known = {s["name"] for s in [*BUILTIN_SIGNATURES, *cfg.extra.get("ai_signatures", [])]}
        for ref in args.refs:
            is_session = "session_" in ref or "/" in ref
            kind, value = ("sessions", session_id(ref)) if is_session else ("agents", ref.lower())
            if not is_session and value not in known:
                print(f"Note: {ref} is not a known agent name ({', '.join(sorted(known))}).")
            items = entry.setdefault(kind, [])
            if args.action == "add" and value not in items:
                items.append(value)
            elif args.action == "remove" and value in items:
                items.remove(value)
        if not entry.get("sessions") and not entry.get("agents"):
            cfg.directed.pop(key)
        store.save_config(cfg)
    commits = commits_for(root, store)
    if not cfg.directed:
        print("No directed agent work configured. `udebt directed add <session-or-agent>`.")
        return 0
    print("\nAgent commits counted as a developer's assisted work (half weight):")
    for dev, d in sorted(cfg.directed.items()):
        n = sum(1 for c in commits if c.directed_by == cfg.primary(dev))
        print(f"  {dev}: {n} commits")
        for s in d.get("sessions") or []:
            k = sum(1 for c in commits if c.klass == "ai" and s in sessions_of(c))
            print(f"    session {s}  ({k} agent commits)")
        for a in d.get("agents") or []:
            print(f"    agent {a}  (every {a} commit no session claims)")
    unclaimed = sum(1 for c in commits if c.klass == "ai" and not c.directed_by)
    print(f"Agent commits nobody directed: {unclaimed} (they count for nobody's footprint).")
    return 0


def cmd_experiment(args) -> int:
    from . import synthetic

    root, store = _ctx(args)
    store.require()
    email = _email(args, root)
    if args.suggest:
        targets = [float(x) for x in args.suggest.split(",") if x.strip()]
        rows = synthetic.suggest(root, store, targets, args.units)
        print(
            f"\n{'target':>6} {'sha':<9} {'date':<10} {'commits':>7} {'mass':>6}  units unchanged"
        )
        for r in rows:
            print(
                f"{r['target']:>6.1f} {r['at_sha'][:8]:<9} {r['at_date'][:10]:<10} "
                f"{r['commits_since']:>7} {r['mass_since']:>6.1f}  {r['unchanged']}/{r['units']}"
                f"  {r['changes']}"
            )
        print("\nmass = landed weight of <sha>..HEAD (first-parent commits from HEAD). No LLM.")
        return 0
    revs = [args.at] if args.at else [r.strip() for r in (args.snapshots or "").split(",")]
    revs = [r for r in revs if r]
    if not revs:
        raise SystemExit("Name a snapshot: --at REV or --snapshots REV,REV,...")
    llm = _llm(store, args)
    try:
        report, path = synthetic.run(
            root,
            store,
            llm,
            revs,
            n_units=args.units,
            per_unit=args.per_unit,
            dev=email,
            resume=Path(args.resume) if args.resume else None,
        )
    except LLMError as e:
        raise SystemExit(str(e)) from e
    for snap in report["snapshots"]:
        _print_snapshot(snap)
    if len(report["snapshots"]) > 1:
        pooled, pair = report["pooled"], report["paired"]
        print(
            f"\nPooled over snapshots (n = {pooled['n']}, not independent: the same "
            f"questions): {pooled['reading']}"
        )
        print(
            f"Same unit, two snapshots, more mass since: worse {pair['concordant']}, better "
            f"{pair['discordant']}, same {pair['tied']} (tau {pair['tau']})"
        )
    print(
        "\nsince = weighted mass landed on the unit after the snapshot; score = mean of correct 1,"
        " partial 0.5, wrong and unknown 0; cw = confidently wrong (confidence 3); change = "
        "where the lines since went (files the snapshot had: edited; files it lacked: new)."
    )
    print(
        f"Report: {path.relative_to(root)}  ({report['llm_calls']} model calls; nothing "
        "recorded in .udebt/records/)"
    )
    return 0


def _print_snapshot(snap: dict[str, Any]) -> None:
    rows = list(snap["units"].values())
    w = min(32, max([22, *(len(r["unit"]) + 2 for r in rows)]))
    print(
        f"\nSnapshot {snap['at_sha'][:8]} ({snap['at_date'][:10]}, `{snap['at']}`): "
        f"{snap['commits_since']} commits, mass {snap['mass_since']} since"
    )
    print(
        f"{'unit':<{w}} {'since':>6} {'edited':>6} {'change':<13} {'score':>5} {'cw':>2}  verdicts"
    )
    for r in sorted(rows, key=lambda r: -r["mass_since"]):
        edited = "-" if r["edited_share"] is None else f"{r['edited_share']:.2f}"
        score = "-" if r["score"] is None else f"{r['score']:.2f}"
        conf = ",".join(str(q["confidence"]) for q in r["questions"] if q["answer"])
        print(
            f"{r['unit']:<{w}} {r['mass_since']:>6.2f} {edited:>6} {r['change']:<13} {score:>5} "
            f"{r['confident_wrong']:>2}  {r['verdict'] or '(no question)'}"
            f"{f' (conf {conf})' if conf else ''}"
        )
    sm = snap.get("summary")
    if not sm:
        return
    print(
        f"mean score {sm['mean_score']}; {sm['confident_wrong']} confidently wrong; "
        f"unknown {sm['unknown_share']} of answers; verdicts {sm['verdicts']}"
    )
    print(f"mass since vs 1 - score: {sm['mass_since_vs_wrongness']['reading']}")
    groups = ", ".join(
        f"{k} {g['units']} (wrongness {g['mean_wrongness']}, cw {g['confident_wrong']}, "
        f"unknown {g['unknown']})"
        for k, g in sm["by_change"].items()
        if g["units"]
    )
    print(f"by change: {groups}")


def cmd_cache(args) -> int:
    """The parsed-commit cache (cache.py): what is on disk, or rebuild it from scratch."""
    import time

    from . import cache as cache_mod
    from .weights import cache_version, load_cache

    root, store = _ctx(args)
    store.require()
    pc = cache_mod.open_cache(root, store.dir)
    if args.action == "rebuild":
        pc.clear()
        t0 = time.monotonic()
        commits = commits_for(root, store)
        t1 = time.monotonic()
        pc.inputs(commits)
        t2 = time.monotonic()
        print(
            f"Rebuilt: parsed {pc.stats.parsed} commits in {t1 - t0:.1f}s, weighing inputs "
            f"in {t2 - t1:.1f}s."
        )
    st = pc.status()
    if st["path"] is None:
        print("No parsed-commit cache yet; any command builds it (or `udebt cache rebuild`).")
        return 0
    kind = "gzip" if st["gzip"] else "plain JSON"
    size = f"{st['bytes'] / 1e6:.2f} MB, {kind}"
    print(f"Parsed-commit cache: {st['path'].relative_to(root)} ({size})")
    print(
        f"  {st['cached']} commits cached; {st['reachable']} reachable from HEAD: "
        f"{st['new']} new to parse, {st['stale']} unreachable to drop on the next load"
    )
    names = {"eff": "effective lines", "add": "added files", "ren": "renames", "ver": "versions"}
    print("  weighing inputs: " + ", ".join(f"{names[k]} {n}" for k, n in st["inputs"].items()))
    if not st["format_ok"]:
        print(f"  format {st['format']} is not {cache_mod.FORMAT}: the next load rebuilds it")
    if not st["mailmap_ok"]:
        print("  .mailmap changed since it was written: the next load rebuilds it")
    ignored = (
        subprocess.run(
            ["git", "check-ignore", "-q", str(st["path"])], cwd=root, capture_output=True
        ).returncode
        == 0
    )
    print(f"  gitignored: {'yes' if ignored else 'NO (it is derived; keep it out of git)'}")
    weights = load_cache(store.weights_path)
    refined = sum(1 for w in weights.values() if w.refined)
    print(
        f"Weights: {store.weights_path.relative_to(root)}, version "
        f"{cache_version(store.weights_path)}, {len(weights)} commits ({refined} LLM-refined); "
        "commit this one"
    )
    return 0


def cmd_hook(args) -> int:
    root, store = _ctx(args)
    store.require()
    hooks_path = subprocess.run(
        ["git", "config", "core.hooksPath"], cwd=root, capture_output=True, text=True
    ).stdout.strip()
    hook_dir = (root / hooks_path) if hooks_path else (root / ".git" / "hooks")
    hook_dir.mkdir(parents=True, exist_ok=True)
    target = hook_dir / "pre-push"
    if target.exists() and "udebt" not in target.read_text(encoding="utf-8"):
        raise SystemExit(f"{target} exists and is not udebt's. Add `udebt risk` to it by hand.")
    target.write_text(risk_mod.PRE_PUSH_HOOK, encoding="utf-8")
    target.chmod(0o755)
    print(f"Installed warn-only pre-push hook at {target}")
    return 0


# -- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="udebt", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--version", action="version", version=f"udebt {__version__}")
    p.add_argument("--repo", help="path inside the target repository (default: cwd)")
    p.add_argument("--model", help="claude model to use (default: your claude CLI default)")
    p.add_argument("--as-user", metavar="EMAIL", help="record/view as this developer")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create .udebt/").set_defaults(fn=cmd_init)

    s = sub.add_parser("clusters", help="structural clusters from imports and co-change")
    s.add_argument("--top", type=int, default=30, help="how many clusters to show")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_clusters)

    s = sub.add_parser("map", help="extract architectural units and sub-units")
    s.add_argument("--max-units", type=int, default=12, help="top-level units (default 12)")
    s.add_argument("--max-children", type=int, default=6, help="sub-units per unit (default 6)")
    s.add_argument(
        "--rescore",
        action="store_true",
        help="recompute churn/importance/provenance with current weights, no LLM",
    )
    s.set_defaults(fn=cmd_map)

    sub.add_parser("units", help="list units").set_defaults(fn=cmd_units)

    s = sub.add_parser("scope", help="mark a unit architectural or blackbox, for you or the team")
    s.add_argument("unit")
    s.add_argument("scope", choices=["architectural", "blackbox"])
    who = s.add_mutually_exclusive_group()
    who.add_argument("--for", dest="for_dev", metavar="DEV", help="for this developer")
    who.add_argument("--team", action="store_true", help="for everyone (the map's scope)")
    s.set_defaults(fn=cmd_scope)

    s = sub.add_parser(
        "responsibility",
        help="who is expected to understand which units",
        description="udebt responsibility                    every developer's set\n"
        "udebt responsibility <dev>              one developer's set (dev: email, @handle, me)\n"
        "udebt responsibility <dev> add|remove <unit>...\n"
        "udebt responsibility seed [--force]     propose sets from CODEOWNERS and footprint",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("rest", nargs="*", metavar="ARGS")
    s.add_argument("--force", action="store_true", help="with seed: re-seed curated sets too")
    s.set_defaults(fn=cmd_responsibility)

    s = sub.add_parser("quiz", help="answer prediction questions")
    s.add_argument("-n", "--num", type=int, default=5, help="units to cover (default 5)")
    s.add_argument("--per-unit", type=int, default=1, help="questions per unit (default 1)")
    s.add_argument("--unit", action="append", help="quiz only this unit id (repeatable)")
    s.add_argument("--deep", action="store_true", help="pick among sub-units")
    s.set_defaults(fn=cmd_quiz, team=False)

    s = sub.add_parser("status", help="show debt")
    s.add_argument("unit_ref", nargs="?", metavar="unit", help="one unit and its sub-units")
    s.add_argument("--team", action="store_true", help="every developer; bus target per unit")
    s.add_argument("--deep", action="store_true", help="show sub-units")
    s.set_defaults(fn=cmd_status)

    sub.add_parser("history", help="timeline of decisions and who made them").set_defaults(
        fn=cmd_history
    )

    s = sub.add_parser("alias", help="record other emails you have committed under")
    s.add_argument("emails", nargs="+")
    s.set_defaults(fn=cmd_alias)

    s = sub.add_parser(
        "directed",
        help="agent sessions or agents whose commits you directed",
        description="udebt directed                              list what is configured\n"
        "udebt directed add <session-or-agent>...   e.g. session_01Ab... or claude-code\n"
        "udebt directed remove <session-or-agent>...\n"
        "A session is matched against Claude-Session: trailers; an agent name claims every\n"
        "commit by that agent that no session claims. Matched commits stay `ai`, and count\n"
        "as your assisted work (half weight) in your share, prior and footprint.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("action", nargs="?", choices=["list", "add", "remove"], default="list")
    s.add_argument("refs", nargs="*", metavar="SESSION_OR_AGENT")
    s.add_argument("--for", dest="for_dev", metavar="DEV", help="for this developer")
    s.set_defaults(fn=cmd_directed)

    s = sub.add_parser("contributors", help="who contributed, sorted by kind")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_contributors)

    s = sub.add_parser("weigh", help="per-commit weights")
    s.add_argument(
        "--refine",
        nargs="?",
        const=-1,
        type=int,
        default=None,
        help="LLM-refine the N heaviest unrefined commits (default from config)",
    )
    s.add_argument("--top", type=int, default=15, help="how many to list")
    s.set_defaults(fn=cmd_weigh)

    s = sub.add_parser("risk", help="risk of a change against your understanding")
    s.add_argument("range", nargs="?", help="git revision range (default @{upstream}..HEAD)")
    s.add_argument("--staged", action="store_true", help="assess the staged diff instead")
    s.add_argument("--deep", action="store_true", help="assess sub-units, not top-level units")
    s.add_argument("--strict", action="store_true", help="exit 1 on HIGH or OUTSIDE")
    s.set_defaults(fn=cmd_risk)

    s = sub.add_parser("experiment", help="validation experiments")
    ex = s.add_subparsers(dest="experiment", required=True)
    x = ex.add_parser(
        "synthetic",
        help="quiz synthetic developers frozen at older snapshots (1 + 2 per snapshot calls)",
    )
    which = x.add_mutually_exclusive_group(required=True)
    which.add_argument("--at", metavar="REV", help="one snapshot")
    which.add_argument(
        "--snapshots", metavar="REV,REV,...", help="several snapshots, one shared question set"
    )
    which.add_argument(
        "--suggest",
        metavar="MASS,...",
        help="list the first-parent commits that many weighted mass units before HEAD (no LLM)",
    )
    x.add_argument("--units", type=int, help="the N most important units (default all)")
    x.add_argument("--per-unit", type=int, default=2, help="questions per unit (default 2)")
    x.add_argument("--resume", metavar="REPORT", help="finish a report that stopped part way")
    x.set_defaults(fn=cmd_experiment)

    s = sub.add_parser("cache", help="the parsed-commit cache: status, or rebuild it")
    s.add_argument("action", nargs="?", choices=["status", "rebuild"], default="status")
    s.set_defaults(fn=cmd_cache)

    s = sub.add_parser("hook", help="git hooks")
    s.add_argument("action", choices=["install"])
    s.set_defaults(fn=cmd_hook)

    s = sub.add_parser("dig", help="guided walkthrough of a unit")
    s.add_argument("unit", nargs="?")
    s.add_argument("--no-check", action="store_true", help="skip the check question")
    s.set_defaults(fn=cmd_dig)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except BrokenPipeError:  # e.g. piped into `head`
        return 0


if __name__ == "__main__":
    sys.exit(main())
