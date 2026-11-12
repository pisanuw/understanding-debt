"""How much understanding debt a commit can create: a weight in [0, 1] per commit.

Not all commits are equal. A whitespace reformat of 200 files or a lockfile bump teaches
nothing and so cannot put anyone in debt; a new dependency, a schema change or a large
insert-heavy change across several directories can. The weight scales everything that used
to be a commit count: churn, decay, provenance shares and the own-commit prior.

The weight is heuristic from cheap git signals (one extra `git log -w` pass gives the
whitespace-ignored line counts for every commit). `refine()` lets an LLM re-classify the
heaviest commits; results are cached in .udebt/commits.json so the cost is paid once.

Branch commits are weighed as one piece of work. A repository that squashes each pull
request lands it as one commit; one that merges keeps its ten small commits, which weighed
on their own would carry several times the weight for the same work. So the commits a merge
brings in from its branch form a group: the group is weighed once, as if squashed, and its
weight is shared among its commits by their effective lines. Commits on the main line (the
first-parent chain from HEAD) stay as they are.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .filekinds import VERSION_PATHSPECS, mechanical, version_candidate
from .filekinds import kind as _kind
from .history import KEYWORDS, _toplevel
from .provenance import Commit, git_log

# LLM refinement classes and the weight each implies.
CLASS_WEIGHT = {"architectural": 1.0, "behavioural": 0.6, "refactor": 0.3, "cosmetic": 0.05}
CACHE_VERSION = 4  # 2: branch commits grouped; 3: version bumps; 4: major upgrades weigh
VERSION_BUMP_WEIGHT = 0.03  # a release that only moves version numbers, like a lockfile bump
NEAR_ZERO = {"docs", "lock", "generated", "media"}  # kinds that leave a version bump a bump
GROUP_MAX_COMMITS = 50  # a longer branch is a long-lived line of work, not a pull request


@dataclass
class CommitWeight:
    sha: str
    weight: float
    reasons: list[str] = field(default_factory=list)
    effective_lines: int = 0  # whitespace-ignored insertions + deletions
    refined: str | None = None  # LLM class, if refined
    group: str | None = None  # the merge whose branch brought this commit in, if grouped
    standalone: float | None = None  # the commit's heuristic weight on its own, if grouped


def effective_lines(root: Path) -> dict[str, int]:
    """sha -> insertions + deletions ignoring whitespace and blank lines, one git pass."""
    return {sha: total for sha, (total, _) in _effective(root).items()}


def _effective(root: Path, shas: list[str] | None = None) -> dict[str, tuple[int, int]]:
    """sha -> (effective lines, effective lines outside lockfiles and generated files), over
    history from HEAD or over exactly `shas`."""
    if shas is not None and not shas:
        return {}
    out = git_log(root, ["--format=%x1e%H", "--numstat", "-w", "--ignore-blank-lines"], shas)
    result: dict[str, tuple[int, int]] = {}
    for rec in out.split("\x1e"):
        lines = rec.strip().splitlines()
        if not lines:
            continue
        sha, total, counted = lines[0].strip(), 0, 0
        for ln in lines[1:]:
            m = re.match(r"^(\d+|-)\t(\d+|-)\t(.*)$", ln)
            if m:
                n = (int(m.group(1)) if m.group(1).isdigit() else 0) + (
                    int(m.group(2)) if m.group(2).isdigit() else 0
                )
                total += n
                if not mechanical(m.group(3)):
                    counted += n
        result[sha] = (total, counted)
    return result


def added_files(root: Path, shas: list[str] | None = None) -> dict[str, set[str]]:
    """sha -> files first added in that commit, over history from HEAD or exactly `shas`."""
    if shas is not None and not shas:
        return {}
    out = git_log(
        root, ["--format=%x1e%H", "--name-status", "--diff-filter=A", "--no-renames"], shas
    )
    result: dict[str, set[str]] = {}
    for rec in out.split("\x1e"):
        lines = rec.strip().splitlines()
        if not lines:
            continue
        sha = lines[0].strip()
        result[sha] = {ln.split("\t", 1)[1] for ln in lines[1:] if ln.startswith("A\t")}
    return result


# -- version bumps ----------------------------------------------------------------------

# A line that sets a version: `"version": "1.2.3",` (package.json, lerna.json), `version =
# "1.2.3"` (pyproject.toml, Cargo.toml, setup.cfg), `version: 1.2.3` (pubspec, Chart.yaml),
# `__version__ = "1.2.3"`, `version="1.2.3",` (setup.py), `<version>1.2.3</version>` (Maven),
# `version '1.2.3'` (Gradle), or a bare version (a VERSION file).
_VERSION_LINE = re.compile(
    r"""^["']?(__version__|version)["']?\s*[:=]|^version\s+["']|^<version>[^<]*</version>$|"""
    r"""^["']?v?\d+(\.\d+)+([-+][0-9a-z.-]+)?["']?,?$""",
    re.I,
)
# A version string: at least major.minor, so `"port": 3000` is not one.
_VERSION_TOKEN = re.compile(r"v?\d+(\.\d+)+([-+][0-9A-Za-z.-]+)?")
_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def major(token: str) -> tuple[int, ...] | None:
    """The part of a version string a breaking upgrade changes: the first number, or the
    first two for 0.x (0.x minors break). None for a calendar version (a first number of
    1000 or more), where the first number is a year, not a promise about compatibility."""
    m = re.match(r"v?(\d+)\.(\d+)", token, re.I)
    if m is None:
        return None
    first, second = int(m.group(1)), int(m.group(2))
    if first >= 1000:
        return None
    return (0, second) if first == 0 else (first,)


def _tokens(line: str) -> list[str]:
    return [m.group(0) for m in _VERSION_TOKEN.finditer(line)]


def version_only(removed: list[str], added: list[str]) -> bool:
    """Whether a file's diff (its removed and added lines) changes nothing but versions.
    Every changed line either sets a version, or is a pin whose removed and added forms are
    the same line apart from version strings (`"@sentry/core": "8.1.0"` to `"8.2.0"`, which
    a monorepo release rewrites in every package), within the same major version. A pin
    moved to a new major (`8.1.0` to `9.0.0`, `0.23` to `0.30`, `<2.0.0` to `<3.0.0`) is a
    dependency change: someone has to understand what the upgrade broke. The project's own
    version lines never count as one, nor does a pin moved to the version this diff gives
    the project (a monorepo's major release pinning its own packages). Blank lines are
    ignored."""
    rem = [ln.strip() for ln in removed if ln.strip()]
    add = [ln.strip() for ln in added if ln.strip()]
    if not rem and not add:
        return False
    pins = [ln for ln in rem + add if not _VERSION_LINE.search(ln)]
    if any(not _VERSION_TOKEN.search(ln) for ln in pins):
        return False

    def shaped(lines: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for ln in lines:
            if not _VERSION_LINE.search(ln):
                out.setdefault(_VERSION_TOKEN.sub("#", ln), []).append(ln)
        return out

    before, after = shaped(rem), shaped(add)
    if Counter({k: len(v) for k, v in before.items()}) != Counter(
        {k: len(v) for k, v in after.items()}
    ):
        return False
    own = {t.lower().lstrip("v") for ln in add if _VERSION_LINE.search(ln) for t in _tokens(ln)}
    for key, olds in before.items():
        for old, new in zip(olds, after[key], strict=True):
            for a, b in zip(_tokens(old), _tokens(new), strict=True):
                ma, mb = major(a), major(b)
                if (
                    ma != mb
                    and ma is not None
                    and mb is not None
                    and b.lower().lstrip("v") not in own
                ):
                    return False
    return True


def parse_version_diff(out: str) -> dict[str, bool]:
    """path -> version-only, from one commit's `-p -U0` diff of manifest and version files.
    Hunk headers give the line counts, so a removed line that itself starts with `--` is
    read as content."""
    changes: dict[str, tuple[list[str], list[str]]] = {}
    cur: tuple[list[str], list[str]] | None = None
    old_path = ""
    minus = plus = 0
    for ln in out.split("\n"):
        if minus or plus:
            if ln.startswith("-") and minus and cur is not None:
                cur[0].append(ln[1:])
                minus -= 1
            elif ln.startswith("+") and plus and cur is not None:
                cur[1].append(ln[1:])
                plus -= 1
            elif not ln.startswith("\\"):  # "\ No newline at end of file"
                minus = plus = 0
            continue
        if ln.startswith("diff --git "):
            cur, old_path = None, ""
        elif ln.startswith("--- ") and cur is None:
            old_path = ln[4:]
        elif ln.startswith("+++ ") and cur is None:
            path = ln[4:] if ln[4:] != "/dev/null" else old_path
            if path.startswith('"'):  # quoted like numstat quotes it, without the a/ b/
                path = '"' + path[3:]
            elif path[:2] in ("a/", "b/"):
                path = path[2:]
            cur = changes.setdefault(path, ([], []))
        elif cur is not None and (m := _HUNK.match(ln)):
            minus = 1 if m.group(1) is None else int(m.group(1))
            plus = 1 if m.group(2) is None else int(m.group(2))
    return {p: version_only(r, a) for p, (r, a) in changes.items()}


def version_bumps(
    root: Path, shas: list[str] | None = None, commits: list[Commit] | None = None
) -> dict[str, set[str]]:
    """sha -> the manifest and version files whose diff in that commit changes only version
    strings. One `git log -p -U0` pass restricted to those paths: over history from HEAD, or
    over exactly `shas`. With `commits`, only the ones that touched such a file are read."""
    if commits is not None:
        want = set(shas) if shas is not None else None
        shas = [
            c.sha
            for c in commits
            if (want is None or c.sha in want)
            and not c.is_merge
            and any(version_candidate(f) for f in c.files)
        ]
    if shas is not None and not shas:
        return {}
    args = ["-p", "-U0", "--no-renames", "--no-ext-diff", "--no-color", "--format=%x1e%H"]
    if shas is None:
        args.append("--full-history")
    out = git_log(root, [*args, "--", *VERSION_PATHSPECS], shas)
    result: dict[str, set[str]] = {}
    for rec in out.split("\x1e"):
        sha, _, diff = rec.partition("\n")
        sha = sha.strip()
        if not sha:
            continue
        bumped = {p for p, ok in parse_version_diff(diff).items() if ok and version_candidate(p)}
        if bumped:
            result[sha] = bumped
    return result


def heuristic_weight(
    c: Commit,
    eff_lines: int,
    added: set[str],
    renames: set[str],
    new_dirs: set[str],
    version: set[str] | None = None,
) -> CommitWeight:
    """`version` is the files whose diff here is a version bump (version_bumps): they count
    as their own kind, not as a dependency change, and a commit that only bumps versions
    (with docs such as a changelog alongside) weighs like a lockfile bump."""
    if not c.files:  # merge commits and empty commits land nothing themselves
        return CommitWeight(c.sha, 0.0, ["no diff (merge or empty)"], 0)
    version = version or set()
    kinds = ["version" if f in version else _kind(f) for f in c.files]
    kindset = set(kinds)
    reasons: list[str] = []
    raw_lines = c.insertions + c.deletions

    if kindset <= {"lock", "generated", "media"}:
        return CommitWeight(c.sha, 0.02, ["only lockfiles, generated or media files"], eff_lines)
    n_version = kinds.count("version")
    if n_version and kindset <= NEAR_ZERO | {"version"}:
        files = f"{n_version} file{'s' if n_version != 1 else ''}"
        return CommitWeight(c.sha, VERSION_BUMP_WEIGHT, [f"version bump only ({files})"], eff_lines)
    if raw_lines > 0 and eff_lines == 0:
        return CommitWeight(c.sha, 0.05, ["whitespace-only change"], 0)
    if c.sha in renames and eff_lines == 0:
        return CommitWeight(c.sha, 0.05, ["pure rename or move"], 0)
    if kindset <= {"docs", "lock", "generated", "media"}:
        return CommitWeight(c.sha, 0.05, ["documentation only"], eff_lines)
    if re.match(r"^Revert\b", c.subject):
        return CommitWeight(c.sha, 0.3, ["revert"], eff_lines)

    # Size: log scale, 500 effective lines saturate.
    size = min(1.0, math.log1p(eff_lines) / math.log1p(500)) if eff_lines else 0.1
    w = 0.1 + 0.6 * size
    reasons.append(f"{eff_lines} effective lines")

    code_files = [f for f, k in zip(c.files, kinds, strict=True) if k == "code"]
    if not code_files and kindset <= NEAR_ZERO | {"test", "version"}:
        w *= 0.4
        reasons.append("tests only")
    if "manifest" in kindset:
        w += 0.3
        reasons.append("dependencies changed")
    if n_version:
        reasons.append(f"{n_version} version-only file{'s' if n_version != 1 else ''}")
    if "infra" in kindset:
        w += 0.15
        reasons.append("infrastructure changed")
    new_code = [f for f in code_files if f in added]
    if new_code:
        w += min(0.2, 0.05 * len(new_code))
        reasons.append(f"{len(new_code)} new code files")
    if new_dirs:
        w += 0.3
        reasons.append("new top-level dir " + ",".join(sorted(new_dirs))[:30])
    tops = {
        t for f, k in zip(c.files, kinds, strict=True) if k != "version" and (t := _toplevel(f))
    }
    if len(tops) > 1:
        w += min(0.2, 0.1 * (len(tops) - 1))
        reasons.append(f"spans {len(tops)} top-level dirs")
    if KEYWORDS.search(c.subject):
        w += 0.1
        reasons.append("decision keyword in subject")
    return CommitWeight(c.sha, round(max(0.0, min(1.0, w)), 3), reasons, eff_lines)


@dataclass
class WeighInputs:
    """What the heuristics read from git besides the commits themselves, per sha. The
    parsed-commit cache (cache.py) keeps them so `weigh` does not rerun its passes."""

    eff: dict[str, tuple[int, int]]  # (effective lines, effective lines outside lockfiles)
    added: dict[str, set[str]]  # files first added
    renames: set[str]  # commits with a rename or move
    versions: dict[str, set[str]]  # files whose diff is a version bump

    @classmethod
    def from_git(cls, root: Path, commits: list[Commit], renames: set[str]) -> WeighInputs:
        """Full passes over history (no cache)."""
        return cls(_effective(root), added_files(root), renames, version_bumps(root, None, commits))


def compute_weights(
    root: Path,
    commits: list[Commit],
    renames: set[str],
    group_max: int = GROUP_MAX_COMMITS,
    inputs: WeighInputs | None = None,
) -> dict[str, CommitWeight]:
    """Heuristic weights for every commit, branch commits grouped under their merge.
    `inputs` comes from the parsed-commit cache; without it the passes run over history."""
    inputs = inputs or WeighInputs.from_git(root, commits, renames)
    eff, added, versions = inputs.eff, inputs.added, inputs.versions
    seen_dirs: set[str] = set()
    new_dirs: dict[str, set[str]] = {}
    out: dict[str, CommitWeight] = {}
    for i, c in enumerate(commits):  # oldest first
        dirs = {d for d in (_toplevel(f) for f in c.files) if d}
        new_dirs[c.sha] = (dirs - seen_dirs) if i >= 3 else set()
        seen_dirs |= dirs
        out[c.sha] = heuristic_weight(
            c,
            eff.get(c.sha, (0, 0))[0],
            added.get(c.sha, set()),
            inputs.renames,
            new_dirs[c.sha],
            versions.get(c.sha),
        )
    by_sha = {c.sha: c for c in commits}
    for merge, members in branch_groups(commits).items():
        work = [by_sha[s] for s in members if by_sha[s].files]  # merges inside land nothing
        if 2 <= len(work) <= group_max:
            _weigh_group(by_sha[merge], work, out, inputs, new_dirs)
    return out


# -- branch groups -------------------------------------------------------------------


def branch_groups(commits: list[Commit]) -> dict[str, list[str]]:
    """merge sha -> the commits its branch brought in, oldest first.

    A merge's branch is the first-parent chain of each of its other parents, back to where it
    meets history already merged. Its own commits form the merge's group; merges on that
    chain (a pull request merged into a long-lived branch, say) form groups of their own, so
    every commit belongs to the innermost merge that brought it in. A "merge main into my
    branch" commit brings in nothing new and has no group. Commits on the main line from HEAD
    belong to no group."""
    by_sha = {c.sha: c for c in commits}
    if not by_sha:
        return {}
    order = {c.sha: i for i, c in enumerate(commits)}
    parent_of = {p for c in commits for p in c.parents}
    tips = [c.sha for c in commits if c.sha not in parent_of]
    seen: set[str] = set()  # ancestor-closed: everything merged so far
    groups: dict[str, list[str]] = {}

    def chain(start: str) -> list[str]:
        """First-parent chain from `start` back to merged history, oldest first."""
        out: list[str] = []
        cur: str | None = start
        while cur is not None and cur in by_sha and cur not in seen:
            out.append(cur)
            ps = by_sha[cur].parents
            cur = ps[0] if ps else None
        return out[::-1]

    def walk(line: list[str]) -> None:
        # Oldest first, so when a merge is reached everything below its first parent is
        # already in `seen` and its branch stops exactly where it forked.
        stack: list[tuple[list[str], int, str | None]] = [(line, 0, None)]
        while stack:
            line_, i, owner = stack.pop()
            if i >= len(line_):
                continue
            sha = line_[i]
            stack.append((line_, i + 1, owner))
            if sha in seen:
                continue
            seen.add(sha)
            if owner is not None and not by_sha[sha].is_merge:
                groups.setdefault(owner, []).append(sha)
            for p in by_sha[sha].parents[1:]:
                sub = chain(p)
                if sub:
                    stack.append((sub, 0, sha))

    for tip in tips[::-1]:  # HEAD is the newest tip
        walk(chain(tip))
    return {m: sorted(ms, key=order.__getitem__) for m, ms in groups.items()}


_DEFAULT_MERGE = re.compile(
    r"^Merge (pull request #\d+ from|(remote-tracking )?branch(es)?|tag|commit) ", re.I
)


def _title(merge: Commit) -> str | None:
    """What the work was called: a merge's own subject, or for a default `Merge pull request
    #12 from ...` message the pull request title on the body's first line."""
    if not _DEFAULT_MERGE.match(merge.subject):
        return merge.subject
    first = merge.body.strip().splitlines()[0].strip() if merge.body.strip() else ""
    return first or None


def _weigh_group(
    merge: Commit,
    work: list[Commit],
    out: dict[str, CommitWeight],
    inputs: WeighInputs,
    new_dirs: dict[str, set[str]],
) -> None:
    """Weigh a branch's commits as if squashed into one, then share that weight among them
    by their effective lines outside lockfiles and generated files, the same lines that
    decide which units a commit's weight lands in. A file is a version bump in the squashed
    commit when every branch commit that touched it only bumped a version there."""
    eff, added, renames, versions = inputs.eff, inputs.added, inputs.renames, inputs.versions
    files: list[str] = []
    per_file: dict[str, int] = {}
    for c in work:
        for f in c.files:
            if f not in per_file:
                files.append(f)
            per_file[f] = per_file.get(f, 0) + c.file_lines.get(f, 0)
    squashed = Commit(
        sha=merge.sha + ":branch",
        date=merge.date,
        author_name=merge.author_name,
        author_email=merge.author_email,
        subject=_title(merge) or " / ".join(c.subject for c in work),
        body="",
        files=files,
        insertions=sum(c.insertions for c in work),
        deletions=sum(c.deletions for c in work),
        file_lines=per_file,
    )
    renamed = {squashed.sha} if any(c.sha in renames for c in work) else set()
    bumped = {f for f in files if all(f in versions.get(c.sha, ()) for c in work if f in c.files)}
    g = heuristic_weight(
        squashed,
        sum(eff.get(c.sha, (0, 0))[0] for c in work),
        set().union(*(added.get(c.sha, set()) for c in work)),
        renamed,
        set().union(*(new_dirs.get(c.sha, set()) for c in work)),
        bumped,
    )
    basis = [eff.get(c.sha, (0, 0))[1] for c in work]
    if sum(basis) <= 0:
        basis = [eff.get(c.sha, (0, 0))[0] for c in work]
    if sum(basis) <= 0:
        basis = [1] * len(work)
    total = sum(basis)
    note = f"branch of {merge.short}: {len(work)} commits weighed as one ({g.weight})"
    for c, b in zip(work, basis, strict=True):
        own = out[c.sha]
        out[c.sha] = CommitWeight(
            c.sha,
            round(g.weight * b / total, 3),
            [note, f"{b / total:.0%} of its lines", *g.reasons],
            own.effective_lines,
            group=merge.sha,
            standalone=own.weight,
        )


# -- cache -----------------------------------------------------------------------


def cache_version(path: Path) -> int:
    if not path.exists():
        return CACHE_VERSION
    return int(json.loads(path.read_text(encoding="utf-8")).get("version", 1))


def load_cache(path: Path) -> dict[str, CommitWeight]:
    """Version 1 and 2 caches both load; version 1 entries simply have no group."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = set(CommitWeight.__dataclass_fields__)
    return {
        sha: CommitWeight(**{k: v for k, v in d.items() if k in fields})
        for sha, d in data.get("commits", {}).items()
    }


def save_cache(path: Path, weights: dict[str, CommitWeight]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    commits = {}
    for sha, w in weights.items():
        d = asdict(w)
        for k in ("group", "standalone"):  # keep ungrouped entries as small as version 1
            if d[k] is None:
                del d[k]
        commits[sha] = d
    data = {"version": CACHE_VERSION, "commits": commits}
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def ensure_weights(
    root: Path,
    cache_path: Path,
    commits: list[Commit],
    renames: set[str] | None = None,
    group_max: int = GROUP_MAX_COMMITS,
    inputs: Any = None,
) -> dict[str, CommitWeight]:
    """Cached weights. When history has commits the cache has not seen, or the cache is an
    older version, the heuristics are recomputed for every commit (a new merge can group
    commits weighed before) and the LLM classes of refined commits are blended in again on
    the new heuristic, so a refinement is paid for once and never lost. `inputs` is a
    WeighInputs, or a callable returning one, called only when a recompute is needed (the
    parsed-commit cache passes one, so a current commits.json needs no git pass at all)."""
    cached = load_cache(cache_path)
    stale = cache_version(cache_path) < CACHE_VERSION
    if stale or any(c.sha not in cached for c in commits):
        if callable(inputs):
            inputs = inputs()
        if renames is None:
            from .history import rename_shas

            renames = inputs.renames if inputs is not None else rename_shas(root)
        fresh = compute_weights(root, commits, renames, group_max, inputs)
        for sha, w in fresh.items():
            old = cached.get(sha)
            if old is not None and old.refined in CLASS_WEIGHT:
                _blend(w, old.refined)
            cached[sha] = w
        save_cache(cache_path, cached)
    return cached


def decision_weight(w: CommitWeight) -> float:
    """How much a commit itself did, for ranking decision candidates (history.py). A branch
    commit's weight is its share of its branch's weight, which is right for mass but ranks a
    decision by how many other commits its branch had; so a grouped commit is ranked by its
    weight on its own (`standalone`), with its LLM class blended in as for any commit."""
    if w.group is None or w.standalone is None:
        return w.weight
    if w.refined in CLASS_WEIGHT:
        return round(0.5 * w.standalone + 0.5 * CLASS_WEIGHT[w.refined], 3)
    return w.standalone


def mass(commits: list[Commit], weights: dict[str, CommitWeight]) -> float:
    return round(sum(weights[c.sha].weight for c in commits if c.sha in weights), 3)


def _blend(w: CommitWeight, cls: str) -> None:
    w.weight = round(0.5 * w.weight + 0.5 * CLASS_WEIGHT[cls], 3)
    w.refined = cls
    w.reasons = [*w.reasons, f"llm: {cls}"]


def apply_refinement(weights: dict[str, CommitWeight], grades: list[dict[str, Any]]) -> int:
    """Blend LLM classes into the heuristic weights. Returns how many changed."""
    n = 0
    for g in grades:
        w = weights.get(g["sha"]) or next(
            (v for k, v in weights.items() if k.startswith(g["sha"])), None
        )
        cls = g.get("klass")
        if w is None or cls not in CLASS_WEIGHT:
            continue
        _blend(w, cls)
        n += 1
    return n
