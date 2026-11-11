"""udebt command line.

udebt init                 create .udebt/ in this repo
udebt map                  extract architectural units with the claude CLI
udebt units                list units with scope and importance
udebt scope <id> <scope>   mark a unit architectural | blackbox
udebt quiz [-n N]          answer prediction questions, get graded, record evidence
udebt status [--team]      debt per unit and a 0-100 debt index
udebt dig [<id>]           guided walkthrough of the worst unit (or a chosen one)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import __version__, repo, scoring
from .dig import walkthrough
from .llm import LLMError, get_llm
from .mapping import build_map
from .quiz import ask_interactively, compute_scores, generate_questions, grade, pick_units
from .quiz import record as record_results
from .store import Store


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
    llm = _llm(store, args)
    print("Mapping architecture with claude (this reads the code; expect a minute or two)...")
    try:
        data = build_map(root, store, llm, max_units=args.max_units)
    except LLMError as e:
        raise SystemExit(str(e)) from e
    print(f"Wrote {len(data['units'])} units to {store.map_path.relative_to(root)}")
    _print_units(data["units"])
    return 0


def cmd_units(args) -> int:
    root, store = _ctx(args)
    store.require()
    _print_units(store.require_map()["units"])
    return 0


def _print_units(units: list[dict[str, Any]]) -> None:
    print()
    print(f"{'id':<22} {'kind':<9} {'scope':<13} {'imp':>5}  name")
    for u in units:
        print(
            f"{u['id']:<22} {u['kind']:<9} {u.get('scope', 'architectural'):<13} "
            f"{u.get('importance', 0):>5.2f}  {u['name']}"
        )


def cmd_scope(args) -> int:
    root, store = _ctx(args)
    store.require()
    m = store.require_map()
    for u in m["units"]:
        if u["id"] == args.unit:
            u["scope"] = args.scope
            store.save_map(m)
            print(f"{args.unit}: scope set to {args.scope}")
            return 0
    raise SystemExit(f"Unknown unit id: {args.unit}")


def cmd_quiz(args) -> int:
    root, store = _ctx(args)
    store.require()
    m = store.require_map()
    email = _email(args, root)
    llm = _llm(store, args)
    scores = compute_scores(root, store, m["units"], email)
    units = pick_units(m["units"], scores, args.num, args.unit or None)
    print(f"Quizzing {email} on: {', '.join(u['id'] for u in units)}")
    print("Generating questions from the code...")
    try:
        questions = generate_questions(root, llm, units, per_unit=args.per_unit)
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
    return cmd_status(args)


def cmd_status(args) -> int:
    root, store = _ctx(args)
    store.require()
    m = store.require_map()
    units = m["units"]
    by_id = {u["id"]: u for u in units}
    cfg = store.config

    if getattr(args, "team", False):
        per_dev = {}
        for slug, recs in store.all_records().items():
            per_dev[slug] = compute_scores(root, store, units, slug, records=recs)
        if not per_dev:
            print("No quiz records yet. Run `udebt quiz`.")
            return 0
        team = scoring.team_view(per_dev, cfg.known_threshold)
        print(f"\nTeam view ({len(per_dev)} developer(s)); known threshold {cfg.known_threshold}")
        print(f"{'unit':<22} {'imp':>5} {'team und':>9} {'bus':>4}  holders")
        team_scores = []
        for u in units:
            t = team.get(u["id"])
            if not t:
                continue
            team_scores.append(
                scoring.UnitScore(
                    u["id"], t["importance"], t["understanding"], 0, t["understanding"], t["scope"]
                )
            )
            flag = "" if t["scope"] == "architectural" else " (blackbox)"
            print(
                f"{u['id']:<22} {t['importance']:>5.2f} {t['understanding']:>9.2f} "
                f"{t['bus_factor']:>4}  {', '.join(t['holders']) or '-'}{flag}"
            )
        print(f"\nTeam debt index: {scoring.debt_index(team_scores)} / 100")
        for slug, sc in per_dev.items():
            print(f"  {slug:<30} {scoring.debt_index(sc)}")
        return 0

    email = _email(args, root)
    scores = compute_scores(root, store, units, email)
    print(f"\nUnderstanding debt for {email} at {repo.head(root)[:8]}")
    print(f"{'unit':<22} {'imp':>5} {'und':>5}  {'debt':>5}  {'since':>5}  {'':<10} note")
    for s in sorted(scores, key=lambda s: -s.debt):
        u = by_id[s.unit_id]
        if s.scope != "architectural":
            note = "blackbox (excluded)"
        elif not s.measured:
            note = "unmeasured"
        elif s.commits_since:
            note = f"decayed from {s.verified:.2f}"
        else:
            note = ""
        print(
            f"{s.unit_id:<22} {s.importance:>5.2f} {s.understanding:>5.2f}  {s.debt:>5.2f}  "
            f"{s.commits_since:>5}  {_bar(1 - s.understanding):<10} {note}  [{u['name']}]"
        )
    print(
        f"\nDebt index: {scoring.debt_index(scores)} / 100   (0 = you understand every "
        f"architectural unit; unmeasured counts as 0 understanding)"
    )
    worst = scoring.quiz_priority(scores)
    if worst:
        print(f"Next: `udebt dig {worst[0].unit_id}` or `udebt quiz`")
    return 0


def cmd_dig(args) -> int:
    root, store = _ctx(args)
    store.require()
    m = store.require_map()
    email = _email(args, root)
    llm = _llm(store, args)
    scores = compute_scores(root, store, m["units"], email)
    units = pick_units(m["units"], scores, 1, [args.unit] if args.unit else None)
    unit = units[0]
    print(f"Digging into `{unit['id']}` ({unit['name']})...\n")
    try:
        body, question = walkthrough(root, llm, unit)
    except LLMError as e:
        raise SystemExit(str(e)) from e
    print(body)
    if not question or args.no_check:
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

    s = sub.add_parser("map", help="extract architectural units")
    s.add_argument("--max-units", type=int, default=12)
    s.set_defaults(fn=cmd_map)

    sub.add_parser("units", help="list units").set_defaults(fn=cmd_units)

    s = sub.add_parser("scope", help="mark a unit architectural or blackbox")
    s.add_argument("unit")
    s.add_argument("scope", choices=["architectural", "blackbox"])
    s.set_defaults(fn=cmd_scope)

    s = sub.add_parser("quiz", help="answer prediction questions")
    s.add_argument("-n", "--num", type=int, default=5, help="units to cover (default 5)")
    s.add_argument("--per-unit", type=int, default=1, help="questions per unit (default 1)")
    s.add_argument("--unit", action="append", help="quiz only this unit id (repeatable)")
    s.set_defaults(fn=cmd_quiz, team=False)

    s = sub.add_parser("status", help="show debt")
    s.add_argument("--team", action="store_true", help="aggregate across all recorded developers")
    s.set_defaults(fn=cmd_status)

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


if __name__ == "__main__":
    sys.exit(main())
