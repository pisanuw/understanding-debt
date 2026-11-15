"""The samples as static JSON in the site, for visitors who are not signed in (HOSTING.md
decision 10). Anonymous visitors get no grants in app schemas (the platform's rule), so the page
cannot read the samples from Supabase for them; it reads them from the site instead:

    web/public/samples/index.json            the registered samples (SAMPLES), each with its pin,
                                             licence, and the files below when they exist
    web/public/samples/<name>.json           the sample's rows, exactly what `udebt worker sql
                                             --dialect json --public` writes (the preview reads
                                             the same shape), derived from samples/<name>.sql
    web/public/samples/<name>.quizzes.json   advance quizzes (`udebt worker quizzes`), when made

Regenerate after a sample's load script or an advance-quiz file changes:

    python -m udebt.hosted.static_samples            # writes web/public/samples/
    python -m udebt.hosted.static_samples --check    # exit 1 when the committed files are stale

The JSON comes from the committed load scripts (`loadsql.rows_from_sql` reads back what
`analysis_sql` wrote), so no worker output has to be kept. A registered sample without a load
script is listed with `analysis: null` (the page shows it with "No analysis yet"), and one without
an advance-quiz file with `quizzes: null`. A script or quiz file at another commit than the pin
is refused: the functions open excerpts to visitors who are not signed in only at the pins.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .. import contract
from ..loadsql import json_from_sql, rows_from_sql
from .samples import SAMPLES

FORMAT = "udebt-samples"
VERSION = 1
ROOT = Path(__file__).resolve().parent.parent.parent


class StaticError(ValueError):
    pass


def quizzes_entry(path: Path, name: str, sample: dict[str, Any]) -> int:
    """The number of questions in a sample's advance-quiz file, or StaticError."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise StaticError(f"{path.name}: not JSON ({e})") from e
    errors = contract.validate("quizzes", doc)
    if errors:
        raise StaticError(f"{path.name}: {'; '.join(errors[:3])}")
    if doc["repo"] != sample["url"] or doc["sha"] != sample["sha"]:
        raise StaticError(f"{path.name}: written for {doc['repo']} at {str(doc['sha'])[:12]}, "
                          f"the pin is {sample['url']} at {str(sample['sha'])[:12]}")  # fmt: skip
    return len(doc["questions"])


def build(sql_dir: Path, out_dir: Path) -> dict[str, str]:
    """File name -> content for `out_dir`: each sample's JSON and the index."""
    files: dict[str, str] = {}
    entries = []
    for name, sample in SAMPLES.items():
        sql = Path(sql_dir) / f"{name}.sql"
        entry: dict[str, Any] = {"name": name, "url": sample["url"], "license": sample["license"],
                                 "sha": sample["sha"], "analysis": None, "quizzes": None,
                                 "units": None, "questions": None}  # fmt: skip
        if sql.exists():
            text = sql.read_text(encoding="utf-8")
            rows = rows_from_sql(text)
            repo, an = rows["repository"], rows["analysis"]
            if repo["url"] != sample["url"] or an["sha"] != sample["sha"]:
                pin = f"{sample['url']} at {str(sample['sha'])[:12]}"
                raise StaticError(f"{sql.name}: loads {repo['url']} at {str(an['sha'])[:12]}, "
                                  f"the pin is {pin}")  # fmt: skip
            if an["user_id"] is not None or not repo["is_sample"]:
                raise StaticError(f"{sql.name}: not a shared sample load")
            files[f"{name}.json"] = json_from_sql(text)
            entry["analysis"] = f"{name}.json"
            entry["units"] = len((an["specs"] or {}).get("units") or [])
        quiz = Path(out_dir) / f"{name}.quizzes.json"
        if quiz.exists():
            entry["questions"] = quizzes_entry(quiz, name, sample)
            entry["quizzes"] = quiz.name
        entries.append(entry)
    index = {"format": FORMAT, "version": VERSION,
             "comment": "Written by `python -m udebt.hosted.static_samples`; do not edit by hand.",
             "samples": entries}  # fmt: skip
    files["index.json"] = json.dumps(index, ensure_ascii=False, indent=1) + "\n"
    return files


def stale(sql_dir: Path, out_dir: Path) -> list[str]:
    """What differs between `out_dir` and what `build` writes (empty when current)."""
    out = []
    for name, text in build(sql_dir, out_dir).items():
        p = Path(out_dir) / name
        if not p.exists():
            out.append(f"{name}: missing")
        elif p.read_text(encoding="utf-8") != text:
            out.append(f"{name}: stale")
    known = {f"{n}.json" for n in SAMPLES} | {f"{n}.quizzes.json" for n in SAMPLES} | {"index.json"}
    for p in sorted(Path(out_dir).glob("*.json")) if Path(out_dir).is_dir() else []:
        if p.name not in known:
            out.append(f"{p.name}: not a registered sample's file")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m udebt.hosted.static_samples",
                                 description="the samples as static JSON for the site")  # fmt: skip
    ap.add_argument("--sql", default=str(ROOT / "samples"), help="the load scripts (samples/)")
    ap.add_argument("--out", default=str(ROOT / "web" / "public" / "samples"))
    ap.add_argument("--check", action="store_true", help="report stale files, write nothing")
    args = ap.parse_args(argv)
    try:
        if args.check:
            found = stale(Path(args.sql), Path(args.out))
            for f in found:
                print(f, file=sys.stderr)
            if not found:
                print(f"{args.out}: current")
            return 1 if found else 0
        files = build(Path(args.sql), Path(args.out))
    except StaticError as e:
        print(f"static samples: {e}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
        print(f"{out / name}: {len(text.encode('utf-8')) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
