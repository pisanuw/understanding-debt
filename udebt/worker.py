"""The analysis worker (Phase 4a): one repository at one commit, analysed in a transient clone,
written out as contract files, the clone deleted. A library function (`run`) and a command
(`udebt worker run <repo-url> --rev <sha> --out <dir>`); no web framework, stdlib only, so the
same code runs on a laptop, in CI or as the hosted platform's worker (HOSTING.md: a Python
service on Render).

1. **Clone** into a fresh temporary directory, within limits: a timeout on the clone, at most
   `max_mb` of git objects and `max_commits` commits up to the revision (defaults 500 MB and
   20,000, the brief's recommendation); over a limit, the clone is deleted and nothing is
   written. Credentials in the URL never reach the output.
2. **A map**: the repository's own committed `.udebt/map.json` when it has one (udebt commits
   it by default), else one given with `--map`, else, with `--llm`, one written by the
   configured model (`udebt map`, plus orientations and the tour: three calls; `--walkthroughs
   N` adds one per unit for the N most important). Without any, the run still writes the
   offline core (weights, structure, history, contributors) and says the map is missing.
3. **The offline core**: commit weights, structural clusters, decision candidates,
   contributors; with a map, the replayed history as the export (`export.json`, contract
   `export`, the import format) and the **specs** (`specs.json`): units and sub-units with their
   decisions, alternatives, why and history, orientations, the top tracked files under any
   directory a unit lists (`files`, topfiles.py), the tour as Markdown, and each
   cached walkthrough with the actual lines of its steps (`--no-excerpts` leaves the lines out;
   for a public repository they let quizzes be graded without a clone).
4. **Delete the clone**, always.

`analysis.json` is the manifest: repository, sha, when, limits, the map's source, counts, the
files written and their sizes, and whether code excerpts are included.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import contract
from .store import Store, now_iso

FORMAT = "udebt-analysis"
VERSION = 1


class WorkerError(Exception):
    pass


@dataclass
class Limits:
    max_commits: int = 20_000
    max_mb: int = 500
    clone_timeout: int = 900  # seconds


def _git(cwd: Path, *args: str, timeout: int | None = None) -> str:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)  # fmt: skip
    except subprocess.TimeoutExpired as e:
        raise WorkerError(f"git {args[0]} took longer than {timeout} s") from e
    if p.returncode != 0:
        raise WorkerError(f"git {' '.join(args[:2])}: {p.stderr.strip()[:400]}")
    return p.stdout


def public_url(url: str) -> str:
    """The URL without credentials (`https://user:token@host/...` -> `https://host/...`)."""
    return re.sub(r"^([a-z+]+://)[^/@]+@", r"\1", url)


def repo_name(url: str) -> str:
    tail = public_url(url).rstrip("/").rsplit("/", 1)[-1]
    return tail[:-4] if tail.endswith(".git") else tail or "repo"


def clone(url: str, dest: Path, rev: str | None, limits: Limits) -> dict[str, Any]:
    """Clone `url` into `dest` at `rev` within `limits`; returns what was measured."""
    _git(dest.parent, "clone", "--quiet", "--no-tags", url, str(dest),
         timeout=limits.clone_timeout)  # fmt: skip
    if rev:
        try:
            _git(dest, "checkout", "--quiet", "--detach", rev)
        except WorkerError:  # not on a branch the clone has (a pull request head): fetch it
            _git(dest, "fetch", "--quiet", "origin", rev, timeout=limits.clone_timeout)
            _git(dest, "checkout", "--quiet", "--detach", "FETCH_HEAD")
    sizes = dict(
        ln.split(": ", 1) for ln in _git(dest, "count-objects", "-v").splitlines() if ": " in ln
    )
    kib = int(sizes.get("size", 0)) + int(sizes.get("size-pack", 0))
    commits = int(_git(dest, "rev-list", "--count", "HEAD").strip() or 0)
    if kib > limits.max_mb * 1024:
        raise WorkerError(f"the clone is {kib / 1024:.0f} MB, over the {limits.max_mb} MB limit")
    if commits > limits.max_commits:
        raise WorkerError(f"{commits} commits, over the {limits.max_commits} limit")
    return {"sha": _git(dest, "rev-parse", "HEAD").strip(), "commits": commits,
            "clone_bytes": kib * 1024}  # fmt: skip


def _write(out: Path, name: str, data: Any, files: dict[str, int]) -> None:
    p = out / name
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    files[name] = p.stat().st_size


def _ensure_map(root: Path, store: Store, map_file: Path | None, llm: bool,
                model: Any = None) -> tuple[str, int]:  # fmt: skip
    """(where the map came from, model calls made). `model` (an llm.LLM) writes a missing map
    in-process; else `llm` runs `udebt map` with the configured model."""
    if store.map_path.exists():
        return "repository", 0
    if map_file is not None:
        store.init()
        data = json.loads(Path(map_file).read_text(encoding="utf-8"))
        contract.check("map", data)
        store.save_map(data)
        # a map taken from another checkout's .udebt/ brings its settings (identities) and
        # responsibility sets when they sit beside it; never its records
        for name in ("config.json", "responsibility.json"):
            src = Path(map_file).parent / name
            if src.is_file():
                shutil.copyfile(src, store.dir / name)
        return "given", 0
    if model is not None:
        from .llm import LLMError
        from .mapping import build_map

        store.init()
        try:
            build_map(root, store, model, retry=False)
        except (LLMError, SystemExit) as e:
            raise WorkerError(f"the map could not be written: {e}") from e
        return "llm", 1
    if llm:
        from . import cli

        store.init()
        if cli.main(["--repo", str(root), "map", "--no-retry"]) != 0:
            raise WorkerError("udebt map failed")
        return "llm", 1
    return "none", 0


def changed_since(root: Path, store: Store, shas: list[str]) -> dict[str, dict[str, float]]:
    """For each earlier commit in `shas` that this history has, the share of every unit (and
    sub-unit) changed since it: the share-changed decay's input, computed as `status` does
    (weighted changed lines landed after that commit over the unit's size now). The hosted app
    fades a person's answers given at an earlier analysis by it. Unknown commits are left out."""
    from . import units as hier
    from .ledger import Ledger
    from .mapping import commits_for, weights_for
    from .quiz import unit_sizes_at
    from .sizes import share
    from .weights import line_weights

    if not shas:
        return {}
    m = store.require_map()
    commits = commits_for(root, store)
    weights = weights_for(root, store, commits)
    ledger = Ledger(commits, m["units"])
    lws = line_weights(weights)
    sizes = unit_sizes_at(root, store, m["units"])
    out: dict[str, dict[str, float]] = {}
    for given in dict.fromkeys(shas):
        try:
            full = _git(root, "rev-parse", "--verify", "--quiet", f"{given}^{{commit}}").strip()
        except WorkerError:
            continue
        if full not in ledger.pos:
            continue
        out[full] = {
            u["id"]: share(
                ledger.since(ledger.touches(u), full, weights, lws)[1], sizes.get(u["id"], 0.0)
            )  # fmt: skip
            for u, _ in hier.walk(m["units"])
        }
    return out


def _specs(root: Path, store: Store, excerpts_too: bool, since: list[str] | None = None
           ) -> dict[str, Any]:  # fmt: skip
    """Units with their decisions and orientations, the tour as Markdown, the walkthroughs with
    their lines: what the hosted app shows and quizzes from. A unit with a directory among its
    paths also gets `files`, the top tracked files under them (topfiles.py), so the hosted
    question writer has code for it with no walkthrough. With `since` (earlier analysed
    commits), `changed_since` says how much of each unit changed after each of them."""
    from . import dig, excerpts, repo, topfiles, tour
    from . import orientation as orient
    from . import units as hier
    from .mapping import commits_for, weights_for
    from .unitgraph import build

    m = store.require_map()
    commits = commits_for(root, store)
    graph = build(root, m["units"], commits, weights_for(root, store, commits),
                  max_files=store.config.cochange_max_files)  # fmt: skip
    tracked = set(repo.tracked_files(root))
    sizes = topfiles.tracked_sizes(root)
    dirs = topfiles.directories(sizes)
    keep = ("id", "name", "kind", "summary", "decision", "alternatives", "why", "paths",
            "centrality", "scope", "importance", "history")  # fmt: skip
    units, walks = [], {}
    for u, parent in hier.walk(m["units"]):
        o = orient.cached(u)
        row = {**{k: u.get(k) for k in keep if k in u}, "parent": parent["id"] if parent else None,
               "orientation": o}  # fmt: skip
        files = topfiles.recorded_files({**u, "orientation": o}, sizes, dirs)
        if files:
            row["files"] = files
        units.append(row)
        w = dig.cached(root, u)
        if w is None:
            continue
        steps = []
        for st in w["steps"]:
            step = {k: st[k] for k in ("file", "start_line", "end_line", "narration")}
            if excerpts_too:
                try:
                    ex = excerpts.read(root, st["file"], st["start_line"], st["end_line"],
                                       tracked=tracked)  # fmt: skip
                    step["lines"] = ex.lines
                except excerpts.ExcerptError:
                    step["lines"] = None
            steps.append(step)
        walks[u["id"]] = {"flow": w.get("flow"), "steps": steps, "trap": w.get("trap"),
                          "head": w.get("head")}  # fmt: skip
    t = tour.cached(m, graph)
    md = tour.markdown(t, m, graph, root.name) if t else None
    specs = {
        "units": units,
        "connections": graph.edges(),
        "hubs": graph.hubs,
        "order": tour.order(m["units"], graph),
        "tour": {"markdown": md[0], "minutes": md[1]} if md else None,
        "walkthroughs": walks,
    }
    if since:
        specs["changed_since"] = changed_since(root, store, since)
    return specs


def _llm_specs(root: Path, store: Store, walkthroughs: int, model: Any = None) -> int:
    """Orientations for every top-level unit, the tour, and walkthroughs for the most
    important units, through `model` (an llm.LLM) or else the configured model; returns the
    calls made."""
    from . import dig, tour
    from . import orientation as orient
    from .llm import get_llm
    from .mapping import commits_for, weights_for
    from .unitgraph import build

    llm = model if model is not None else get_llm(model=store.config.model)
    m = store.require_map()
    calls = 0
    if orient.ensure(root, store, llm, m, [u["id"] for u in m["units"]]):
        calls += 1
    m = store.require_map()
    commits = commits_for(root, store)
    graph = build(root, m["units"], commits, weights_for(root, store, commits),
                  max_files=store.config.cochange_max_files)  # fmt: skip
    _t, fresh = tour.ensure(root, store, llm, m, graph)
    calls += int(fresh)
    top = sorted(m["units"], key=lambda u: -float(u.get("importance") or 0))[:walkthroughs]
    for u in top:
        m = store.require_map()
        o = orient.cached(next(x for x in m["units"] if x["id"] == u["id"]))
        _, fresh = dig.ensure(root, store, llm, m, u["id"], orientation=orient.text(o) if o
                              else None)  # fmt: skip
        calls += int(fresh)
    return calls


def run(
    url: str,
    out: Path,
    rev: str | None = None,
    *,
    limits: Limits | None = None,
    map_file: Path | None = None,
    llm: bool = False,
    walkthroughs: int = 0,
    excerpts: bool = True,
    work_dir: Path | None = None,
    log=print,
    model: Any = None,
    since: list[str] | None = None,
    export: bool = True,
) -> dict[str, Any]:
    """Analyse `url` at `rev` into `out` (made if missing); returns the manifest. `model` (an
    llm.LLM, such as the hosted worker's tool loop over a Messages API) makes the model steps
    in-process and implies `llm`; `since` lists earlier analysed commits for `changed_since`;
    `export=False` skips the replay and the export (the hosted learner app uses neither, and on
    a large team the replay is the slowest part)."""
    from . import replay
    from .contributors import as_dict, contributors
    from .mapping import commits_for, discover, history_candidates, mappable_files, weights_for

    limits = limits or Limits()
    out = Path(out).resolve()
    t0 = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="udebt-worker-", dir=work_dir))
    try:
        if out == tmp or tmp in out.parents:
            raise WorkerError("the output directory cannot be inside the clone")
        root = tmp / repo_name(url)
        log(f"Cloning {public_url(url)}{' at ' + rev if rev else ''}...")
        measured = clone(url, root, rev, limits)
        store = Store(root)
        llm = llm or model is not None
        source, calls = _ensure_map(root, store, map_file, llm, model)
        store.init()
        if llm and source != "none":
            calls += _llm_specs(root, store, walkthroughs, model)
        log(f"Analysing {measured['commits']} commits (map: {source})...")
        commits = commits_for(root, store)
        weights = weights_for(root, store, commits)
        out.mkdir(parents=True, exist_ok=True)
        files: dict[str, int] = {}
        _write(out, "weights.json", json.loads((store.dir / "commits.json").read_text()), files)
        _write(out, "structure.json", discover(root, store, mappable_files(root), commits,
               weights).summary(), files)  # fmt: skip
        _write(out, "history.json", history_candidates(root, store, commits, weights), files)
        cons = contributors(commits, store.config.identities, set())
        _write(out, "contributors.json", [{k: v for k, v in as_dict(c).items() if k != "you"}
               for c in cons], files)  # fmt: skip
        counts: dict[str, Any] = {"commits": measured["commits"], "contributors": len(cons)}
        if source != "none":
            sanitized = contract.sanitize_map(store.require_map())
            if export:
                h = replay.load(root, store)
                res = replay.ensure(h, None, save=False)
                if res.note:  # a committed series made under other rules: replay it afresh
                    res = replay.ensure(h, None, rebuild=True, save=False)
                data = replay.export_data(h, res)
                data["map"] = sanitized
                contract.check("import", data)
                _write(out, "export.json", data, files)
                counts.update(points=len(data["snapshots"]), devs=len(data["devs"]))
            _write(out, "map.json", sanitized, files)
            specs = _specs(root, store, excerpts, since)
            _write(out, "specs.json", specs, files)
            counts.update(units=len(specs["units"]), walkthroughs=len(specs["walkthroughs"]),
                          tour=specs["tour"] is not None)  # fmt: skip
        manifest = {
            "format": FORMAT,
            "version": VERSION,
            "contract": contract.CONTRACT,
            "repo": repo_name(url),
            "url": public_url(url),
            "rev": rev,
            "sha": measured["sha"],
            "generated_at": now_iso(),
            "seconds": round(time.monotonic() - t0, 1),
            "limits": asdict(limits),
            "clone_bytes": measured["clone_bytes"],
            "map_source": source,
            "llm_calls": calls,
            "code_excerpts": bool(excerpts and source != "none"),
            "counts": counts,
            "files": files,
            "note": None if source != "none" else
            "no map: the repository commits none and none was given (--map, or --llm to write one)",
        }  # fmt: skip
        _write(out, "analysis.json", manifest, {})
        return manifest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # the code never stays
