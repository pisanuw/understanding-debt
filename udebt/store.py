"""On-disk layout under <repo>/.udebt/.

map.json             shared architectural map, two levels of units (commit this)
records/<slug>.jsonl one developer's evidence, append-only (commit this for team view)
commits.json         per-commit weights (heuristic, LLM-refined for the heaviest); commit this
commits-parsed.json  parsed git history, a cache (`.json.gz` when large); gitignored, derived
responsibility.json  each developer's responsibility set and per-developer scope (commit this)
config.json          settings (model, decay, identities, handles, directed agents, bus target,
                     history quota)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import repo

DIR = ".udebt"


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass
class Config:
    model: str | None = None
    decay_per_commit: float = 0.9
    evidence_window: int = 5
    known_threshold: float = 0.6
    refine_top: int = 40  # how many of the heaviest commits `udebt weigh --refine` classifies
    bus_target: int = 2  # holders above known_threshold every architectural unit should have
    footprint_threshold: float = 0.1  # human share of a unit that seeds responsibility
    cochange_max_files: int = 30  # commits touching more files are left out of co-change
    group_max_commits: int = 50  # a merged branch up to this long is weighed as one commit
    history_periods: int = 4  # history mining cuts history into this many equal-mass periods
    history_quota: int = 10  # candidates each period is guaranteed (of 40): slots by mass
    identities: dict[str, list[str]] = field(default_factory=dict)  # primary email -> aliases
    # CODEOWNERS handle -> email (or list of emails); null marks a handle still to be mapped
    handles: dict[str, Any] = field(default_factory=dict)
    # developer email -> {"sessions": [...], "agents": [...]}: agent commits they directed
    directed: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def primary(self, email: str) -> str:
        """The developer key for `email`: the identity group's primary address, else itself."""
        e = email.lower()
        for primary, aliases in self.identities.items():
            if e == primary.lower() or e in {a.lower() for a in aliases}:
                return primary.lower()
        return e

    def emails_for(self, email: str) -> set[str]:
        """All addresses that belong to the developer identified by `email`."""
        e = email.lower()
        for primary, aliases in self.identities.items():
            group = {primary.lower(), *(a.lower() for a in aliases)}
            if e in group:
                return group
        return {e}

    @classmethod
    def load(cls, path: Path) -> Config:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {k: data.pop(k) for k in list(data) if k in cls.__dataclass_fields__}
        return cls(**known, extra=data)

    def dump(self) -> dict[str, Any]:
        d = {
            "model": self.model,
            "decay_per_commit": self.decay_per_commit,
            "evidence_window": self.evidence_window,
            "known_threshold": self.known_threshold,
            "refine_top": self.refine_top,
            "bus_target": self.bus_target,
            "footprint_threshold": self.footprint_threshold,
            "cochange_max_files": self.cochange_max_files,
            "group_max_commits": self.group_max_commits,
            "history_periods": self.history_periods,
            "history_quota": self.history_quota,
            "identities": self.identities,
            "handles": self.handles,
            "directed": self.directed,
        }
        d.update(self.extra)
        return d


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.dir = root / DIR
        self.map_path = self.dir / "map.json"
        self.records_dir = self.dir / "records"
        self.config_path = self.dir / "config.json"
        self.weights_path = self.dir / "commits.json"
        self.responsibility_path = self.dir / "responsibility.json"

    # -- lifecycle -----------------------------------------------------------
    def exists(self) -> bool:
        return self.dir.is_dir()

    def init(self) -> None:
        from .cache import ensure_gitignore

        self.records_dir.mkdir(parents=True, exist_ok=True)
        ensure_gitignore(self.dir)
        if not self.config_path.exists():
            self.config_path.write_text(
                json.dumps(Config().dump(), indent=2) + "\n", encoding="utf-8"
            )

    def require(self) -> None:
        if not self.exists():
            raise SystemExit("No .udebt/ here. Run `udebt init` first.")

    @property
    def config(self) -> Config:
        return Config.load(self.config_path)

    # -- map -----------------------------------------------------------------
    def save_config(self, cfg: Config) -> None:
        self.config_path.write_text(json.dumps(cfg.dump(), indent=2) + "\n", encoding="utf-8")

    def load_map(self) -> dict[str, Any] | None:
        if not self.map_path.exists():
            return None
        return json.loads(self.map_path.read_text(encoding="utf-8"))

    def save_map(self, data: dict[str, Any]) -> None:
        self.map_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def require_map(self) -> dict[str, Any]:
        m = self.load_map()
        if m is None:
            raise SystemExit("No architectural map yet. Run `udebt map` first.")
        return m

    # -- records -------------------------------------------------------------
    def record_path(self, email: str) -> Path:
        return self.records_dir / f"{repo.slug(email)}.jsonl"

    def append_record(self, email: str, rec: dict[str, Any]) -> None:
        self.records_dir.mkdir(parents=True, exist_ok=True)
        rec = {"ts": now_iso(), **rec}
        with self.record_path(email).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def load_records(self, email: str) -> list[dict[str, Any]]:
        p = self.record_path(email)
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def all_records(self) -> dict[str, list[dict[str, Any]]]:
        """slug -> records, for every developer with a record file."""
        if not self.records_dir.is_dir():
            return {}
        out = {}
        for p in sorted(self.records_dir.glob("*.jsonl")):
            recs = []
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    recs.append(json.loads(line))
            out[p.stem] = recs
        return out
