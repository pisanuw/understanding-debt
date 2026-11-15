"""The three sample repositories (HOSTING.md, owner decision 7) and their load.

The samples are analysed on the owner's subscription by another session with

    udebt worker run <url> --rev <pin> --llm --walkthroughs 12 --no-excerpts --out <dir>

(references, no stored code). This module checks each output and writes the SQL the Plabs
session runs with the Supabase admin workflow (`sql <file>`, after the owner's yes):

    python -m udebt.hosted.samples <dir> [<dir> ...] --out <sql dir>

Each script is `udebt worker sql --public` for that sample: idempotent (the repository upserted
by URL, the shared analysis of that commit replaced in one transaction), the URL and licence
from SAMPLES, no export, no commit emails. The admin workflow's logs are public, and so is
everything in these scripts: analyses of public repositories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from ..loadsql import analysis_sql

SAMPLES: dict[str, dict[str, str]] = {
    "huggingface_hub": {"url": "https://github.com/huggingface/huggingface_hub",
                        "license": "Apache-2.0",
                        "sha": "2c61ae8c11eedb1b79e262dd9eb5e68962cac063"},
    "sentry-javascript": {"url": "https://github.com/getsentry/sentry-javascript",
                          "license": "MIT", "sha": "6bee2f2ccccdac123199d1b610c70a3bad5c8194"},
    "mlflow": {"url": "https://github.com/mlflow/mlflow", "license": "Apache-2.0",
               "sha": "f6ff35427d7e1134a83f8f9d8215d0049149162f"},
}  # fmt: skip


def problems(out_dir: Path, sample: dict[str, str] | None, any_sha: bool = False) -> list[str]:
    """What keeps a worker output from loading as a sample (empty when it may load)."""
    out = Path(out_dir)
    try:
        manifest = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
        specs = json.loads((out / "specs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"{out}: not a worker output ({e})"]
    found: list[str] = []
    if sample is None:
        return [f"{manifest.get('repo')}: not one of the samples ({', '.join(SAMPLES)})"]
    if not any_sha and manifest.get("sha") != sample["sha"]:
        found.append(
            f"analysed at {str(manifest.get('sha'))[:12]}, the pin is {sample['sha'][:12]}"
        )
    if manifest.get("map_source") == "none":
        found.append("no map")
    if manifest.get("code_excerpts"):
        found.append("code excerpts in the specs (run with --no-excerpts)")
    if any(st.get("lines") for w in (specs.get("walkthroughs") or {}).values()
           for st in w.get("steps", [])):  # fmt: skip
        found.append("walkthrough steps carry code lines")
    if not specs.get("units"):
        found.append("no units")
    return found


def summary(out_dir: Path) -> dict[str, Any]:
    out = Path(out_dir)
    m = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
    s = json.loads((out / "specs.json").read_text(encoding="utf-8"))
    return {"repo": m.get("repo"), "sha": m.get("sha"), "units": len(s.get("units", [])),
            "orientations": sum(1 for u in s.get("units", []) if u.get("orientation")),
            "walkthroughs": len(s.get("walkthroughs") or {}), "tour": bool(s.get("tour")),
            "llm_calls": m.get("llm_calls")}  # fmt: skip


def sql_for(out_dir: Path, url: str, license: str | None) -> str:
    return analysis_sql(Path(out_dir), public=True, url=url, license=license)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m udebt.hosted.samples", description="check the samples, write their SQL"
    )
    p.add_argument("dirs", nargs="+", help="worker outputs (udebt worker run --out)")
    p.add_argument("--out", required=True, help="where to write <sample>.sql")
    p.add_argument("--any-sha", action="store_true", help="accept another commit than the pin")
    args = p.parse_args(argv)
    dest = Path(args.out)
    dest.mkdir(parents=True, exist_ok=True)
    bad = 0
    for d in args.dirs:
        try:
            name = json.loads((Path(d) / "analysis.json").read_text(encoding="utf-8"))["repo"]
        except (OSError, ValueError, KeyError) as e:
            print(f"{d}: not a worker output ({e})", file=sys.stderr)
            bad += 1
            continue
        sample = SAMPLES.get(name)
        found = problems(Path(d), sample, args.any_sha)
        if found:
            print(f"{name}: not written: {'; '.join(found)}", file=sys.stderr)
            bad += 1
            continue
        assert sample is not None
        sql = sql_for(Path(d), sample["url"], sample["license"])
        f = dest / f"{name}.sql"
        f.write_text(sql, encoding="utf-8")
        digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        print(f"{f}: {len(sql) / 1024:.0f} KB, sha256 {digest}, {json.dumps(summary(Path(d)))}")
    return 1 if bad else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
