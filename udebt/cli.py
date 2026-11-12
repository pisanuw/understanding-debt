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
udebt history [--candidates]    decisions that shaped the repo (or the commits the mapper sees)
udebt dig [<id>]                guided walkthrough of the worst unit (or a chosen one)
udebt directed add <session>    count agent commits you directed as your assisted work
udebt experiment synthetic --snapshots <rev>,...   synthetic developers frozen at old snapshots
udebt cache [status|rebuild]    the parsed-commit cache under .udebt/ (gitignored)
udebt snapshot [--every week]   replay debt at past points into .udebt/snapshots.json
udebt timeline [--dev X|--unit U]   the series, and the risk events, as a table, CSV or JSON
udebt export [--format csv|json]    full history for the hosted import
udebt dashboard [--open]        a static, self-contained HTML dashboard
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
    history_candidates,
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
        data = build_map(
            root,
            store,
            llm,
            max_units=args.max_units,
            max_children=args.max_children,
            retry=not args.no_retry,
        )
    except LLMError as e:
        raise SystemExit(str(e)) from e
    st = data.get("structure", {})
    n_kids = sum(len(hier.children_of(u)) for u in data["units"])
    print(_structure_line(st))
    print(_paths_line(data.get("paths", {})))
    print(
        f"Wrote {len(data['units'])} units ({n_kids} sub-units) to "
        f"{store.map_path.relative_to(root)}; they cover {int(100 * st.get('coverage', 0))}% "
        f"of the clustered non-test source files"
    )
    _print_units(data["units"])
    _print_warnings(data)
    return 0


def _paths_line(p: dict[str, Any]) -> str:
    if not p:
        return "Paths: not checked (a map from before path resolution)."
    retried = "; after one retry" if p.get("retried") else ""
    return (
        f"Paths: {p['exact'] + p['suffix']} of {p['given']} resolved ({p['exact']} exact, "
        f"{p['suffix']} by a unique suffix), {p['unresolved']} dropped{retried}"
    )


def _print_warnings(m: dict[str, Any] | None) -> None:
    for w in (m or {}).get("warnings", []):
        print(f"Warning: {w}")


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
        lost = u.get("unresolved") or []
        note = f"  ({len(lost)} path(s) dropped: {', '.join(lost[:3])})" if lost else ""
        print(
            f"{uid:<{w}} {u.get('kind', ''):<9} {u.get('scope', 'architectural'):<13} "
            f"{u.get('importance', 0):>5.2f}  {u['name']}{note}"
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
    by = f"mass {prov.others_since_dev_w}"
    if s_.prior_share is not None:
        by = f"share {s_.prior_share:.2f}, mass {prov.others_since_dev_w}"
    if s_.source == "prior" and s_.understanding < 0.005:
        return pre + (
            f"prior from your commits eroded by {prov.others_since_dev} commits ({by}) by others"
        )
    if s_.source == "prior":
        note = "prior from your commits"
        if prov.others_since_dev:
            note += f", {prov.others_since_dev} commits ({by}) by others since"
        return pre + note
    if s_.share_since is not None and (s_.share_since or s_.commits_since):
        return pre + (
            f"quiz, decayed from {s_.verified:.2f} by share {s_.share_since:.2f} "
            f"(mass {s_.commits_since})"
        )
    if s_.commits_since:
        return pre + f"quiz, decayed from {s_.verified:.2f} by mass {s_.commits_since}"
    return pre + "quiz"


def _decay_line(d: scoring.Decay) -> str:
    if d.model == "share":
        return (
            f"Decay: understanding x exp(-{d.rate:g} x share of the unit changed since), a "
            "commit counting at most the unit's size (config decay_k)."
        )
    return f"Decay: understanding x {d.per_mass:g} per unit of mass landed since (model mass)."


def cmd_status(args) -> int:
    root, store = _ctx(args)
    store.require()
    m = store.require_map()
    _print_warnings(m)
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
    print(_decay_line(cfg.decay))
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
    if args.candidates:
        return _print_candidates(root, store, args.json)
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


def _print_candidates(root: Path, store: Store, as_json: bool) -> int:
    """The decision candidates `udebt map` would hand the model, without calling it."""
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    cands = history_candidates(root, store, commits, weights)
    if as_json:
        import json as _json

        print(_json.dumps(cands, indent=2))
        return 0
    cfg = store.config
    years: dict[str, int] = {}
    for h in cands:
        years[h["date"][:4]] = years.get(h["date"][:4], 0) + 1
    ai = sum(1 for h in cands if h["klass"] in ("ai", "assisted"))
    n = max(1, len(cands))
    print(f"\n{len(cands)} decision candidates `udebt map` would show the model, oldest first")
    print(f"{cfg.history_periods} periods of equal mass, {cfg.history_quota} slots guaranteed each")
    print("by year: " + ", ".join(f"{y} {k}" for y, k in sorted(years.items())))
    print(
        f"mean survival {sum(h['survival'] for h in cands) / n:.2f}; "
        f"AI-involved {ai} ({ai / n:.0%})\n"
    )
    print(
        f"{'date':<10} {'sha':<7} {'p':>1} {'class':<9} {'score':>5} {'w':>5} {'alive':>5}  subject"
    )
    for h in cands:
        print(
            f"{h['date']:<10} {h['sha']:<7} {h['period'] + 1:>1} {h['klass']:<9} "
            f"{h['score']:>5.2f} {h['weight']:>5.2f} {h['survival']:>5.0%}  {h['subject'][:58]}"
        )
        print(f"{'':<36}{'; '.join(h['reasons'])}")
    print(
        "\np = period (1 oldest); score = (w + decision bonuses) x alive, +0.5 if founding; "
        "w = commit weight; alive = share of its files still at HEAD (renames followed)."
    )
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
    record_results(store, root, email, results, source="dig")
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
    if args.history:
        return _risk_history(args)
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


# -- time series (replay.py) ---------------------------------------------------------


def _replayed(args, save: bool = True):
    """The history and its stored series, brought up to date (appending only what is new)."""
    import time

    from . import replay

    root, store = _ctx(args)
    store.require()
    t0 = time.monotonic()
    h = replay.load(root, store)
    res = replay.ensure(h, getattr(args, "every", None), save=save)
    if res.note:
        print(f"Note: {res.note}.", file=sys.stderr)
    return root, store, h, res, time.monotonic() - t0


def cmd_snapshot(args) -> int:
    import time

    from . import replay

    root, store = _ctx(args)
    store.require()
    t0 = time.monotonic()
    h = replay.load(root, store)
    res = replay.ensure(h, args.every, rebuild=args.rebuild)
    secs = time.monotonic() - t0
    pts, ev = res.data["points"], res.data["risk"]["events"]
    p = replay.path(store)
    size = p.stat().st_size if p.exists() else 0
    if res.note:
        print(f"Note: {res.note}.")
    print(
        f"{p.relative_to(root)}: {len(pts)} points ({res.appended} new), every "
        f"{res.data['fingerprint']['every']}; {len(ev)} risk events ({res.events_added} new); "
        f"{len(h.devs)} developer{'s' if len(h.devs) != 1 else ''}; {size / 1024:.1f} KB; "
        f"{secs:.1f}s"
    )
    if pts:
        print(f"From {pts[0]['t'][:10]} to {pts[-1]['t'][:10]} (HEAD {pts[-1]['sha'][:8]}).")
    if res.written:
        print("Units are the current map's, replayed against the past. Commit the file.")
    return 0


def _label_dev(dev: str, width: int = 18) -> str:
    return dev if len(dev) <= width else dev[: width - 1] + "~"


def cmd_timeline(args) -> int:
    import csv
    import json as _json

    from . import replay

    root, store, h, res, _ = _replayed(args)
    pts = res.data["points"]
    events = res.data["risk"]["events"]
    if args.dev:
        dev = _dev_key(args, root, store, args.dev, h.commits)
        if dev not in h.devs:
            raise SystemExit(f"{dev} has no footprint, set or evidence here: {', '.join(h.devs)}")
        events = [e for e in events if e["dev"] == dev]
    unit = None
    if args.unit:
        u = hier.resolve(h.units, args.unit)
        if u is None or hier.parent_of(h.units).get(u["id"]):
            raise SystemExit(f"Not a top-level unit: {args.unit}")
        unit = u["id"]
        events = [e for e in events if e["unit"].split()[0] == unit]
    if args.dev:
        top_ids = sorted(
            {uid for p in pts for uid in p["devs"].get(dev, {}).get("debt", {})},
            key=lambda uid: -(pts[-1]["devs"].get(dev, {}).get("debt", {}).get(uid, 0.0)),
        )
        cols = ["t", "sha", "index", *top_ids]
        rows = [
            [p["t"], p["sha"], p["devs"].get(dev, {}).get("index")]
            + [p["devs"].get(dev, {}).get("debt", {}).get(uid, 0.0) for uid in top_ids]
            for p in pts
        ]
    elif unit:
        cols = ["t", "sha", "importance", "size", *h.devs]
        rows = [
            [p["t"], p["sha"], *(p["units"].get(unit) or [None, None])]
            + [p["devs"].get(d, {}).get("und", {}).get(unit) for d in h.devs]
            for p in pts
        ]
    else:
        cols = ["t", "sha", "commits", "mass", "ai_share", *h.devs]
        rows = []
        for p in pts:
            m = p["mass"]
            total = m["ai"] + m["assisted"] + m["human"]
            ai = round((m["ai"] + 0.5 * m["assisted"]) / total, 3) if total else 0.0
            rows.append(
                [p["t"], p["sha"], p["commits"], round(total, 2), ai]
                + [p["devs"].get(d, {}).get("index") for d in h.devs]
            )
    if args.format == "json":
        out = {"columns": cols, "rows": rows, "risk_events": replay.interesting(events)}
        print(_json.dumps(out, indent=1))
        return 0
    if args.format == "csv":
        w = csv.writer(sys.stdout)
        w.writerow(cols)
        w.writerows(rows)
        return 0
    what = (
        f"debt per unit for {dev} (its share of the index)"
        if args.dev
        else f"understanding of {unit}"
        if unit
        else "debt index per developer"
    )
    print(f"\nTimeline for {root.name}: {what}, {len(pts)} points")
    shown = cols[2:] if not args.dev else cols[2:11]
    heads = [_label_dev(c, 12 if args.dev else 18) for c in shown]
    print(f"{'date':<10} {'sha':<8} " + " ".join(f"{c:>{max(7, len(c))}}" for c in heads))
    for r in rows:
        vals = r[2 : 2 + len(shown)]
        cells = []
        for c, v in zip(heads, vals, strict=True):
            width = max(7, len(c))
            cells.append(f"{'-' if v is None else v:>{width}}")
        print(f"{r[0][:10]:<10} {r[1][:8]:<8} " + " ".join(cells))
    if args.dev and len(cols) > 11:
        print(f"(+ {len(cols) - 11} more units: `--format csv` has them all)")
    _print_events(events, 8, args.all)
    print(
        "\nUnits are the current map's, replayed against the past; a point counts the commits "
        "landed on the first-parent line by then and the evidence recorded by then."
    )
    return 0


def _print_events(events, limit: int | None, all_levels: bool = False, since=None) -> None:
    from collections import Counter

    from . import replay

    if since:
        events = [e for e in events if e["landed"][:10] >= since]
    counts = Counter(e["level"] for e in events)
    keep = [e for e in events if all_levels or e["level"] in ("OUTSIDE", "HIGH")]
    summary = ", ".join(f"{k} {counts[k]}" for k in replay.LEVELS if counts[k]) or "none"
    print(f"\nRisk events: {summary} (a developer landing a change on a unit they understood")
    print("too little at the time; CAUTION shown with --all)")
    if not keep:
        return
    shown = replay.interesting(keep, limit)
    print(
        f"{'landed':<10} {'sha':<8} {'level':<8} {'unit':<26} {'mass':>5} {'und':>5} {'w':>5}  "
        "dev; subject"
    )
    for e in shown:
        print(
            f"{e['landed'][:10]:<10} {e['sha'][:8]:<8} {e['level']:<8} {e['unit'][:26]:<26} "
            f"{e['mass']:>5.2f} {e['understanding']:>5.2f} {e['weight']:>5.2f}  "
            f"{e['dev']}; {e['subject'][:50]}"
        )
    if limit and len(keep) > limit:
        print(f"(strongest {limit} of {len(keep)}; `udebt risk --history` lists them all)")


def _risk_history(args) -> int:
    root, store, h, res, _ = _replayed(args)
    events = res.data["risk"]["events"]
    if args.as_user:
        dev = store.config.primary(args.as_user)
        events = [e for e in events if e["dev"] == dev]
    print(f"\nRisk history for {root.name}: every commit a developer landed, assessed against")
    print("their understanding just before it landed (units as the current map defines them)")
    _print_events(events, None, args.all, args.since)
    return 0


def cmd_export(args) -> int:
    import csv
    import json as _json

    from . import replay

    root, store, h, res, _ = _replayed(args)
    data = replay.export_data(h, res, with_answers=args.with_answers)
    if args.format == "json":
        out = Path(args.out) if args.out else store.dir / "export" / "udebt-export.json"
        text = _json.dumps(data, indent=1, ensure_ascii=False) + "\n"
        if str(out) == "-":
            sys.stdout.write(text)
            return 0
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Wrote {out} ({out.stat().st_size / 1024:.1f} KB)")
    else:
        out = Path(args.out) if args.out else store.dir / "export"
        out.mkdir(parents=True, exist_ok=True)
        tables = _export_tables(data)
        for name, (cols, rows) in tables.items():
            with (out / f"{name}.csv").open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(cols)
                w.writerows(rows)
        print(f"Wrote {len(tables)} CSV files to {out}: {', '.join(tables)}")
    if not args.with_answers:
        print("Evidence without answer text (`--with-answers` adds answers, feedback, references).")
    return 0


def _export_tables(data: dict[str, Any]) -> dict[str, tuple[list[str], list[list[Any]]]]:
    """The export as flat tables, one CSV each."""
    snaps = [
        [p["t"], p["sha"], dev, uid, row["und"].get(uid), row["debt"].get(uid), row["index"]]
        for p in data["snapshots"]
        for dev, row in p["devs"].items()
        for uid in sorted(set(row["und"]) | set(row["debt"]))
    ]
    prov = [
        [
            p["t"],
            p["sha"],
            p["commits"],
            *(p["mass"][k] for k in ("ai", "assisted", "human", "bot")),
        ]
        for p in data["snapshots"]
    ]
    units_t = [
        [p["t"], p["sha"], uid, imp, size]
        for p in data["snapshots"]
        for uid, (imp, size) in p["units"].items()
    ]
    ev_cols = ["ts", "dev", "unit", "type", "verdict", "confidence", "evidence", "head", "question"]
    if data["evidence"] and "answer" in data["evidence"][0]:
        ev_cols += ["answer", "feedback", "reference"]
    risk_cols = [
        "landed", "date", "sha", "dev", "klass", "weight", "unit", "level", "mass",
        "understanding", "source", "in_set", "subject",
    ]  # fmt: skip
    heavy_cols = ["landed", "date", "sha", "dev", "author", "klass", "agent", "weight", "subject"]
    con_cols = [
        "name",
        "key",
        "label",
        "commits",
        "ai",
        "assisted",
        "human",
        "bot",
        "first",
        "last",
    ]
    unit_cols = ["id", "name", "kind", "parent", "scope", "importance", "ai_share"]
    return {
        "snapshots": (["t", "sha", "dev", "unit", "understanding", "debt", "index"], snaps),
        "provenance": (["t", "sha", "commits", "ai", "assisted", "human", "bot"], prov),
        "unit_points": (["t", "sha", "unit", "importance", "size"], units_t),
        "evidence": (ev_cols, [[e.get(k) for k in ev_cols] for e in data["evidence"]]),
        "risk_events": (risk_cols, [[e.get(k) for k in risk_cols] for e in data["risk_events"]]),
        "heavy_commits": (
            [*heavy_cols, "units"],
            [
                [e.get(k) for k in heavy_cols]
                + [";".join(f"{u}={m}" for u, m in e["units"].items())]
                for e in data["heavy_commits"]
            ],
        ),
        "contributors": (
            [*con_cols, "emails"],
            [[c.get(k) for k in con_cols] + [";".join(c["emails"])] for c in data["contributors"]],
        ),
        "units": (
            [*unit_cols, "holders", "responsible"],
            [
                [u.get(k) for k in unit_cols] + [";".join(u["holders"]), ";".join(u["responsible"])]
                for u in data["units"]
            ],
        ),
    }


def cmd_dashboard(args) -> int:
    import time
    import webbrowser

    from . import dashboard, replay

    root, store, h, res, secs = _replayed(args)
    t0 = time.monotonic()
    data = replay.export_data(h, res)
    out = Path(args.out) if args.out else store.dir / "dashboard" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dashboard.render(data), encoding="utf-8")
    secs += time.monotonic() - t0
    pts = res.data["points"]
    print(
        f"Wrote {out} ({out.stat().st_size / 1024:.1f} KB; {len(pts)} points, "
        f"{len(h.devs)} developer{'s' if len(h.devs) != 1 else ''}, "
        f"{len(data['risk_events'])} risk events) in {secs:.1f}s"
    )
    _print_events(res.data["risk"]["events"], 5)
    if args.open:
        webbrowser.open(out.resolve().as_uri())
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
        spec = args.suggest.strip()
        by, _, rest = spec.partition(":")
        if by not in ("share", "mshare") or not rest:
            by, rest = "mass", spec
        spec = rest
        targets = [float(x) for x in spec.split(",") if x.strip()]
        rows = synthetic.suggest(root, store, targets, args.units, by)
        print(
            f"\n{'target':>6} {'sha':<9} {'date':<10} {'commits':>7} {'mass':>6} {'share':>5} "
            f"{'median':>6} {'mshare':>6} {'pred':>5}  units unchanged"
        )
        for r in rows:
            print(
                f"{r['target']:>6.2f} {r['at_sha'][:8]:<9} {r['at_date'][:10]:<10} "
                f"{r['commits_since']:>7} {r['mass_since']:>6.1f} "
                f"{r.get('mean_share_changed', 0):>5.2f} {r.get('median_share_changed', 0):>6.2f} "
                f"{r.get('mean_share_since', 0):>6.3f} {r.get('mean_predicted', 0):>5.2f}  "
                f"{r['unchanged']}/{r['units']}  {r['changes']}"
            )
        print(
            "\nmass = landed weight of <sha>..HEAD (first-parent commits from HEAD); share, "
            "median = the units' mean and median share changed since (weighted changed lines / "
            "size at HEAD); mshare = mean mass since / unit mass; pred = the decay model's mean "
            "predicted score for a developer frozen there. Targets are mass, or share:S,... or "
            "mshare:S,... No LLM."
        )
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
        "\nsince = weighted mass landed on the unit after the snapshot; share = share of the "
        "unit changed since (weighted changed lines, each commit's capped at the unit's size "
        "after it, / size at HEAD); pred = the decay model's "
        "score for a developer who knew the unit at the snapshot; score = mean of correct 1,"
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
        f"{'unit':<{w}} {'since':>6} {'share':>5} {'edited':>6} {'change':<13} {'pred':>5} "
        f"{'score':>5} {'cw':>2}  verdicts"
    )
    for r in sorted(rows, key=lambda r: -r["mass_since"]):
        edited = "-" if r["edited_share"] is None else f"{r['edited_share']:.2f}"
        score = "-" if r["score"] is None else f"{r['score']:.2f}"
        shr = f"{r['share_changed']:.2f}" if "share_changed" in r else "-"
        pred = f"{r['predicted']:.2f}" if "predicted" in r else "-"
        conf = ",".join(str(q["confidence"]) for q in r["questions"] if q["answer"])
        print(
            f"{r['unit']:<{w}} {r['mass_since']:>6.2f} {shr:>5} {edited:>6} {r['change']:<13} "
            f"{pred:>5} {score:>5} {r['confident_wrong']:>2}  {r['verdict'] or '(no question)'}"
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
    if "mean_predicted" in sm:
        print(
            f"share changed vs 1 - score: {sm['share_changed_vs_wrongness']['reading']}; mean "
            f"predicted {sm['mean_predicted']} (rmse {sm['rmse_predicted']}), 0.9^mass "
            f"{sm['mean_predicted_mass']} (rmse {sm['rmse_predicted_mass']}), observed "
            f"{sm['mean_observed']}"
        )
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
    s.add_argument(
        "--no-retry",
        action="store_true",
        help="do not ask again when the paths cover under half of the source files",
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

    s = sub.add_parser("history", help="timeline of decisions and who made them")
    s.add_argument(
        "--candidates",
        action="store_true",
        help="list the decision candidates `udebt map` would show the model (no map, no LLM)",
    )
    s.add_argument("--json", action="store_true", help="with --candidates: as JSON")
    s.set_defaults(fn=cmd_history)

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
    s.add_argument(
        "--history",
        action="store_true",
        help="every past commit a developer landed, against their understanding then",
    )
    s.add_argument("--since", metavar="DATE", help="with --history: landed on or after DATE")
    s.add_argument("--all", action="store_true", help="with --history: CAUTION events too")
    s.set_defaults(fn=cmd_risk)

    s = sub.add_parser("snapshot", help="replay debt at past points into .udebt/snapshots.json")
    s.add_argument("--every", metavar="week|day|mass:N", help="point interval (default week)")
    s.add_argument("--rebuild", action="store_true", help="recompute every point")
    s.set_defaults(fn=cmd_snapshot)

    s = sub.add_parser("timeline", help="the replayed series and its risk events")
    s.add_argument("--dev", help="one developer: debt per unit (email, @handle or me)")
    s.add_argument("--unit", help="one top-level unit: every developer's understanding of it")
    s.add_argument("--format", choices=["table", "csv", "json"], default="table")
    s.add_argument("--all", action="store_true", help="list CAUTION risk events too")
    s.set_defaults(fn=cmd_timeline)

    s = sub.add_parser("export", help="full history (snapshots, evidence, risk) as CSV or JSON")
    s.add_argument("--format", choices=["csv", "json"], default="json")
    s.add_argument(
        "--out", help="file (json; - for stdout) or directory (csv); under .udebt/export/"
    )
    s.add_argument("--with-answers", action="store_true", help="include answer text")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("dashboard", help="a static HTML dashboard, self-contained")
    s.add_argument("--out", help="default .udebt/dashboard/index.html")
    s.add_argument("--open", action="store_true", help="open it in the browser")
    s.set_defaults(fn=cmd_dashboard)

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
        help="list the first-parent commits that many weighted mass units before HEAD, or "
        "with share:S,... (mshare:S,...) that mean share of the units changed (no LLM)",
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
