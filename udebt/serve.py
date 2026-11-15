"""`udebt serve` (Phase 3.3): the teaching surface in the browser, on 127.0.0.1 only.

Stdlib only: `ThreadingHTTPServer`, JSON endpoints, and a hand-written page under
`udebt/static/` (no CDN, no build step). Tabs: Dashboard (the Phase 3 dashboard page, which
fetches `/api/dashboard` when its data blob is empty), Tour, Path, Unit, Quiz, Review, Digest,
Tutor and Teach-back. Every answer is graded and recorded by the same code the CLI uses
(quiz.grade, quiz.mark_revealed, quiz.record), so a record from the browser is the record
the CLI would have written; a quiz or review sitting is graded in one call at its end
(/api/answers), as the CLI grades a run.

Security, for a server that can read the repository and call the model on your account:
- it binds 127.0.0.1 and refuses a request whose Host is not 127.0.0.1 or localhost at its
  port (a DNS-rebinding page cannot reach it under another name);
- a per-run token: pages need `?t=<token>` in the URL, API calls an `X-Udebt-Token` header
  (a page on another origin cannot set it without a CORS preflight, which is never
  answered); the comparison is constant-time;
- POST bodies are JSON (`Content-Type: application/json`), at most 1 MB;
- `/api/excerpt` reads tracked files only (excerpts.py): no `..`, no absolute path, nothing
  untracked or ignored;
- responses carry a Content-Security-Policy that allows only this origin, `no-referrer` (the
  token is in the URL) and `no-store`.

Generation (the tour, a walkthrough, quiz questions) is a POST that fills the caches in
map.json, which the GET endpoints then read; quiz questions are pooled per unit and level
(`questions`), so a question another run generated, or a stored variant of one you missed,
is asked before a new call is made.
"""

from __future__ import annotations

import hmac
import json
import secrets
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import dig, excerpts, ladder, learning, repo, review, scoring, teachback, tutor
from . import orientation as orient
from . import responsibility as resp_mod
from . import risk as risk_mod
from . import tour as tour_mod
from . import units as hier
from .llm import LLMError
from .quiz import (
    compute_scores,
    generate_questions,
    grade,
    levels_for,
    mark_revealed,
    pick_units,
    pool_add,
    pool_pick,
    reveal_text,
    revealed_on,
    reveals,
)
from .quiz import record as record_results
from .store import Store

MAX_BODY = 1_000_000
UNKNOWN_QUESTION = "Unknown question (answered already, or the server restarted)."
STATIC = {
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}
CSP_APP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; frame-src 'self'; base-uri 'none'; form-action 'none'"
)
# the dashboard page keeps its CSS and script inline (it also opens from file://)
CSP_DASHBOARD = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'"
)


class HTTPError(Exception):
    def __init__(self, status: int, message: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.data = data or {}


def _int(v: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


class App:
    """The server's state: the repository, the developer, the token, the questions served
    and not yet answered, and the tutor sessions."""

    def __init__(self, root: Path, store: Store, email: str, llm_factory, token: str | None = None):
        self.root = root
        self.store = store
        self.email = email
        self.token = token or secrets.token_urlsafe(18)
        self.llm_factory = llm_factory
        self._llm = None
        self.lock = threading.RLock()  # one writer at a time for map.json and the records
        self.questions: dict[str, dict[str, Any]] = {}
        self.tutors: dict[str, tutor.Session] = {}
        self.port = 0
        self._graph: tuple[Any, Any] | None = None

    # -- helpers ---------------------------------------------------------------------
    def llm(self):
        if self._llm is None:
            self._llm = self.llm_factory()
        return self._llm

    def graph(self, m: dict[str, Any]):
        key = (repo.head(self.root), m.get("generated_at"), len(m["units"]))
        if self._graph is None or self._graph[0] != key:
            from .mapping import commits_for, weights_for
            from .unitgraph import build

            commits = commits_for(self.root, self.store)
            g = build(
                self.root, m["units"], commits, weights_for(self.root, self.store, commits),
                max_files=self.store.config.cochange_max_files,
            )  # fmt: skip
            self._graph = (key, g)
        return self._graph[1]

    def context(self):
        m = self.store.require_map()
        recs = self.store.load_records(self.email)
        resp = resp_mod.load(self.store)
        scores = compute_scores(self.root, self.store, m["units"], self.email, records=recs,
                                resp=resp)  # fmt: skip
        return m, recs, resp, scores

    def unit(self, m: dict[str, Any], ref: str | None) -> dict[str, Any]:
        u = hier.resolve(m["units"], ref or "") if ref else None
        if u is None:
            raise HTTPError(404, f"Unknown unit: {ref}")
        return u

    def serve_question(self, q: dict[str, Any], source: str, **extra) -> dict[str, Any]:
        qid = uuid.uuid4().hex[:16]
        self.questions[qid] = {**q, "source": source, **extra}
        n = ladder.level_of(q)
        return {
            "id": qid,
            "unit_id": q["unit_id"],
            "level": n,
            "label": ladder.label(n),
            "question": q["question"],
        }

    def answered(self, q: dict[str, Any], answer: str, confidence: int) -> dict[str, Any]:
        """Grade one answer, reveal at levels 1-2, record it as the CLI would."""
        return self.answered_all([(q, answer, confidence)])[0]

    def answered_all(self, items: list[tuple[dict[str, Any], str, int]]) -> list[dict[str, Any]]:
        """Grade a sitting's answers in one call (none when every answer is blank), reveal at
        levels 1-2, record them as the CLI would: questions of one kind share one timestamp,
        so on a unit they are one sitting."""
        answered = []
        for q, answer, confidence in items:
            text = answer.strip()
            a = {**q, "answer": text, "confidence": confidence if text else 1}
            if not text and reveals(q, "unknown") and reveal_text(q):
                a["revealed"] = True
            answered.append(a)
        with self.lock:
            results = mark_revealed(grade(self.root, self.llm(), answered))
            written: dict[int, dict[str, Any]] = {}
            groups: dict[tuple[str, bool, str], list[int]] = {}
            for i, q in enumerate(answered):
                key = (q.get("source", "quiz"), bool(q.get("forced")), q.get("kind", "quiz"))
                groups.setdefault(key, []).append(i)
            for (source, forced, kind), idx in groups.items():
                prior = self.store.load_records(self.email)
                recs = record_results(
                    self.store, self.root, self.email, [results[i] for i in idx], source=source,
                    forced=forced, prior=prior, kind=kind,
                )  # fmt: skip
                written.update(zip(idx, recs, strict=True))
            all_recs = self.store.load_records(self.email)
        out = []
        for i, r in enumerate(results):
            rec = written[i]
            on = [x for x in all_recs if x.get("unit") == r["unit_id"]]
            out.append(
                {
                    "unit_id": r["unit_id"],
                    "level": rec["level"],
                    "question": r["question"],
                    "verdict": r["verdict"],
                    "feedback": r["feedback"],
                    "revealed": bool(r.get("revealed")),
                    "reference": reveal_text(r).replace("The answer: ", "").strip()
                    if r.get("revealed")
                    else None,
                    "next_level": ladder.next_level(on),
                    "review_due": (rec.get("review") or {}).get("due"),
                    "right": r.get("right"),
                    "missing": r.get("missing"),
                    "misconception": r.get("misconception"),
                }
            )
        return out

    # -- GET -------------------------------------------------------------------------
    def get_state(self, q):
        m, recs, resp, scores = self.context()
        by = {s.unit_id: s for s in scores}
        cfg = self.store.config
        key = cfg.primary(self.email)
        mine = resp.ids(key) & set(hier.index(m["units"]))
        level_ids = {u["id"] for u in m["units"]}
        ids = hier.at_level(mine, m["units"], False) if mine else level_ids
        units = []
        for u, parent in hier.walk(m["units"]):
            s = by[u["id"]]
            on = [r for r in recs if r.get("unit") == u["id"]]
            units.append(
                {
                    "id": u["id"],
                    "name": u.get("name", u["id"]),
                    "parent": parent["id"] if parent else None,
                    "kind": u.get("kind"),
                    "scope": s.scope,
                    "importance": s.importance,
                    "understanding": round(s.understanding, 3),
                    "basis": s.basis,
                    "depth": s.depth,
                    "level": ladder.next_level(on),
                    "debt": round(s.debt, 3),
                    "in_set": u["id"] in mine,
                    "oriented": orient.cached(u) is not None,
                    "walkthrough": bool(u.get("walkthrough")),
                }
            )
        return {
            "repo": self.root.name,
            "email": self.email,
            "head": repo.head(self.root)[:8],
            "index": scoring.debt_index(scores, only=ids),
            "basis": scoring.basis_counts([s for s in scores if s.unit_id in ids]),
            "units": units,
            "tour": bool(m.get("tour")),
            "review_due": len(review.due(recs)),
            "session_minutes": tutor.SESSION_SECONDS // 60,
            "ladder": {str(n): lv.name for n, lv in ladder.LEVELS.items()},
        }

    def get_dashboard(self, q):
        from . import replay

        h = replay.load(self.root, self.store)
        res = replay.ensure(h, None, save=False)
        if res.note:  # a stale stored series: replay it in memory, write nothing
            print(f"udebt serve: {res.note}; the dashboard replays it in memory.", file=sys.stderr)
            res = replay.ensure(h, None, rebuild=True, save=False)
        return replay.export_data(h, res)

    def get_tour(self, q):
        m = self.store.require_map()
        g = self.graph(m)
        t = tour_mod.cached(m, g)
        if t is None:
            return {"markdown": None, "stale": bool(m.get("tour"))}
        return self._tour_view(t, m, g)

    def _tour_view(self, t, m, g):
        from .markdown import to_html

        md, mins = tour_mod.markdown(t, m, g, self.root.name)
        return {"markdown": md, "html": to_html(md), "minutes": mins}

    def get_path(self, q):
        m, recs, resp, scores = self.context()
        cfg = self.store.config
        mine = resp.ids(cfg.primary(self.email)) & set(hier.index(m["units"]))
        within = hier.at_level(mine, m["units"], False) if mine and not q.get("all") else None
        steps = learning.path(m["units"], scores, recs, self.graph(m), within=within,
                              threshold=cfg.known_threshold)  # fmt: skip
        first = learning.session(steps)
        return {
            "steps": [s.as_dict() for s in steps],
            "session": [s.unit for s in first],
            "session_minutes": sum(s.minutes for s in first),
            "scope": "set" if within else "all",
        }

    def get_unit(self, q):
        from . import decisions
        from .markdown import to_html

        m, recs, resp, scores = self.context()
        u = self.unit(m, q.get("id"))
        s = {x.unit_id: x for x in scores}[u["id"]]
        o = orient.cached(u)
        w = dig.cached(self.root, u)
        on = [r for r in recs if r.get("unit") == u["id"]]
        pos = next((i for i, x in enumerate(hier.flat(m["units"]), 1) if x["id"] == u["id"]), 0)
        who = decisions.confirmed(self.store.all_records(), u["id"], self.store.config)
        record_md = decisions.record(u, pos, repo.head(self.root), who)
        g = self.graph(m)
        top = u["id"].split("/")[0]
        return {
            "id": u["id"],
            "name": u.get("name", u["id"]),
            "kind": u.get("kind"),
            "summary": u.get("summary", ""),
            "paths": u.get("paths", []),
            "history": u.get("history") or [],
            "orientation": o or orient.fallback(u),
            "decision_html": to_html(record_md),
            "walkthrough": w,
            "walkthrough_stale": bool(u.get("walkthrough")) and w is None,
            "score": {
                "understanding": round(s.understanding, 3),
                "verified": None if s.verified is None else round(s.verified, 3),
                "share_since": None if s.share_since is None else round(s.share_since, 3),
                "decay": scoring.decay_note(
                    s, self.store.config.decay, self.store.config.evidence_model
                ),
                "basis": s.basis,
                "source": s.source,
                "depth": s.depth,
                "level": ladder.next_level(on),
                "label": ladder.label(ladder.next_level(on)),
                "debt": round(s.debt, 3),
                "importance": s.importance,
            },
            "connections": {
                "uses": g.depends_on(top),
                "used_by": g.used_by(top),
                "changes_with": g.changes_with(top),
            },
            "records": [
                {
                    k: r.get(k)
                    for k in (
                        "ts",
                        "level",
                        "source",
                        "type",
                        "verdict",
                        "question",
                        "revealed",
                        "feedback",
                    )
                }  # fmt: skip
                for r in sorted(on, key=lambda r: r.get("ts", ""), reverse=True)
            ],
            "reviews": [
                {"level": it.level, "due": it.state.due} for it in review.items(recs, {u["id"]})
            ],
        }

    def get_excerpt(self, q):
        try:
            ex = excerpts.read(
                self.root, q.get("file", ""), _int(q.get("start"), 1, 1, 10**7),
                _int(q.get("end"), 1, 1, 10**7),
            )  # fmt: skip
        except excerpts.ExcerptError as e:
            raise HTTPError(404 if "tracked" in str(e) else 400, str(e)) from e
        return ex.as_dict()

    def get_review(self, q):
        m = self.store.require_map()
        recs = self.store.load_records(self.email)
        ids = {u["id"] for u in hier.flat(m["units"])}
        items = review.due(recs, unit_ids=ids, everything=bool(q.get("all")))[:5]
        out = [self.serve_question(it.question, "review") for it in items]
        upcoming = review.items(recs, ids)
        return {
            "items": out,
            "upcoming": len(upcoming),
            "next_due": upcoming[0].state.due if upcoming else None,
        }

    def _digest_items(self, q: dict[str, Any]):
        from . import digest
        from .mapping import commits_for, weights_for

        m, recs, resp, _ = self.context()
        units = m["units"]
        cfg = self.store.config
        commits = commits_for(self.root, self.store)
        weights = weights_for(self.root, self.store, commits)
        scores = {
            x.unit_id: x
            for x in compute_scores(
                self.root,
                self.store,
                units,
                self.email,
                records=recs,
                commits=commits,
                weights=weights,
                resp=resp,
            )  # fmt: skip
        }
        mine = resp.ids(cfg.primary(self.email)) & set(hier.index(units))
        within = hier.at_level(mine, units, False) if mine and not q.get("all") else None
        try:
            picked = digest.pick(units, scores, cfg.known_threshold,
                                 only=q.get("unit") or None, within=within)  # fmt: skip
        except SystemExit as e:
            raise HTTPError(404, str(e)) from e
        since = str(q.get("since") or "").strip() or None
        items = digest.plan(self.root, self.store, units, picked, scores, recs, commits,
                            weights, since=since)  # fmt: skip
        return items, within is not None

    def _digest_view(self, items, in_set: bool) -> dict[str, Any]:
        return {
            "items": [it.as_dict() for it in items],
            "missing": sum(1 for it in items if it.commits and it.digest is None),
            "threshold": self.store.config.known_threshold,
            "scope": "set" if in_set else "all",
        }

    def get_digest(self, q):
        """What a digest would cover and the briefings already cached; no call."""
        items, in_set = self._digest_items(q)
        return self._digest_view(items, in_set)

    def post_digest(self, body):
        """Write the missing briefings (one call for all of them) and return the digest."""
        from . import digest

        with self.lock:
            items, in_set = self._digest_items(body)
            digest.ensure(self.root, self.store, self.llm(), items)
        return self._digest_view(items, in_set)

    def tutor_session(self, sid: str) -> tutor.Session | None:
        """A tutor session by id: in memory, else saved under .udebt/tutor/ (a restart)."""
        s = self.tutors.get(sid)
        if s is None and sid:
            s = tutor.find(self.store.dir, self.email, sid)
            if s is not None:
                self.tutors[s.id] = s
        return s

    def get_tutor(self, q):
        """`?session=<id>`: that conversation; `?unit=<id>`: the developer's saved
        conversation on the unit, if any (`saved`), for the page's "resume or start over"."""
        if q.get("unit") and not q.get("session"):
            u = self.unit(self.store.require_map(), q.get("unit"))
            saved = tutor.load(self.store.dir, self.email, u["id"])
            if saved is not None and saved.id in self.tutors:
                saved = self.tutors[saved.id]
            return {"saved": saved.view() if saved else None}
        s = self.tutor_session(str(q.get("session", "")))
        if s is None:
            raise HTTPError(404, "No such tutor session.")
        return s.view()

    # -- POST ------------------------------------------------------------------------
    def post_tour(self, body):
        with self.lock:
            m = self.store.require_map()
            g = self.graph(m)
            t, _ = tour_mod.ensure(self.root, self.store, self.llm(), m, g,
                                   refresh=bool(body.get("refresh")))  # fmt: skip
        return self._tour_view(t, m, g)

    def post_walkthrough(self, body):
        with self.lock:
            m = self.store.require_map()
            u = self.unit(m, body.get("unit"))
            o = orient.cached(u)
            w, fresh = dig.ensure(self.root, self.store, self.llm(), m, u["id"],
                                  refresh=bool(body.get("refresh")),
                                  orientation=orient.text(o) if o else None)  # fmt: skip
        return {"walkthrough": w, "written": fresh}

    def post_questions(self, body):
        """The next quiz: per unit at its ladder level, a stored variant of a question just
        missed, else a pooled question this developer has not been asked, else a new one
        (all new ones in one call), after the orientations a level 1 question needs."""
        with self.lock:
            m, recs, resp, scores = self.context()
            units = m["units"]
            n = _int(body.get("n"), 3, 1, 12)
            only = [str(x) for x in body.get("units") or []] or None
            forced = body.get("level")
            forced = ladder.check(int(forced)) if forced else None
            mine = resp.ids(self.store.config.primary(self.email)) & set(hier.index(units))
            within = None
            if mine and not only:
                touching = risk_mod.touched_units(units, risk_mod.working_files(self.root))
                within = hier.at_level(mine, units, False) | touching
            picked = pick_units(units, scores, n, only, within=within)
            if not picked:
                raise HTTPError(400, "Nothing to quiz: every unit in view is blackbox.")
            levels = levels_for(picked, recs, forced)
            on = {u["id"]: [r for r in recs if r.get("unit") == u["id"]] for u in picked}
            need = [u["id"] for u in picked if orient.wanted(on[u["id"]], levels[u["id"]])]
            orients = orient.ensure(self.root, self.store, self.llm(), m, need) if need else {}
            chosen: dict[str, dict[str, Any]] = {}
            to_write = []
            for u in picked:
                u["level"] = levels[u["id"]]
                got = pool_pick(hier.index(units)[u["id"]], u["level"], on[u["id"]])
                if got is not None:
                    chosen[u["id"]] = got
                    continue
                if u["id"] in orients:
                    u["shown_orientation"] = orient.text(orients[u["id"]])
                seen = revealed_on(on[u["id"]], u["level"])
                if seen:
                    u["revealed"] = seen
                to_write.append(u)
            if to_write:
                for q in generate_questions(self.root, self.llm(), to_write):
                    chosen.setdefault(q["unit_id"], q)
                pool_add(self.store, m, list(chosen.values()))
        out = []
        for u in picked:
            q = chosen.get(u["id"])
            if q is None:
                continue
            view = self.serve_question(q, "quiz", **({"forced": True} if forced else {}))
            view["unit_name"] = u.get("name", u["id"])
            view["orientation"] = orients.get(u["id"])
            out.append(view)
        return {"questions": out}

    def post_answer(self, body):
        """One answer, graded and recorded at once (the tutor's check works this way; the
        quiz and review pages batch their answers through /api/answers)."""
        q = self.questions.pop(str(body.get("id", "")), None)
        if q is None:
            raise HTTPError(404, UNKNOWN_QUESTION)
        return self.answered(q, str(body.get("answer", "")), _int(body.get("confidence"), 2, 1, 3))

    def post_dontknow(self, body):
        """ "I don't know" on a served question: no call. It is held as a blank answer for the
        sitting's batch (it cannot be answered after its reference was shown) and, at levels
        1-2, the reference is returned at once."""
        qid = str(body.get("id", ""))
        q = self.questions.get(qid)
        if q is None:
            raise HTTPError(404, UNKNOWN_QUESTION)
        shown = reveals(q, "unknown") and bool(reveal_text(q))
        q["locked"] = {"answer": "", "confidence": 1}
        return {
            "id": qid,
            "revealed": shown,
            "reference": reveal_text(q).replace("The answer: ", "").strip() if shown else None,
        }

    def post_answers(self, body):
        """A sitting's answers, graded in one call at its end (Phase 3.4): `answers` is a list
        of {id, answer, confidence}; a question marked "I don't know" stays blank whatever is
        sent for it. `drop` lists questions left unanswered, which are forgotten unrecorded."""
        for qid in body.get("drop") or []:
            self.questions.pop(str(qid), None)
        given = body.get("answers")
        if not isinstance(given, list) or not given:
            raise HTTPError(400, "No answers to grade.")
        items = []
        for a in given[:50]:
            if not isinstance(a, dict):
                raise HTTPError(400, "Each answer is an object with an id.")
            q = self.questions.get(str(a.get("id", "")))
            if q is None:
                raise HTTPError(404, UNKNOWN_QUESTION)
            lock = q.get("locked")
            text = lock["answer"] if lock else str(a.get("answer", ""))
            conf = lock["confidence"] if lock else _int(a.get("confidence"), 2, 1, 3)
            items.append((q, text, conf))
        out = self.answered_all(items)
        for a in given[:50]:
            self.questions.pop(str(a.get("id", "")), None)
        return {"results": out}

    def post_teachback(self, body):
        m = self.store.require_map()
        u = self.unit(m, body.get("unit"))
        text = str(body.get("explanation", "")).strip()
        if not text:
            raise HTTPError(400, "Nothing to grade; nothing recorded.")
        o = orient.cached(u)
        recs = self.store.load_records(self.email)
        level = teachback.level_for([x for x in recs if x.get("unit") == u["id"]])
        r = teachback.grade(self.root, self.llm(), u, text, _int(body.get("confidence"), 2, 1, 3),
                            orient.text(o) if o else None, level=level)  # fmt: skip
        q = {k: r[k] for k in ("unit_id", "level", "question", "evidence")}
        with self.lock:
            prior = self.store.load_records(self.email)
            if reveals(r, r["verdict"]) and reveal_text(r):
                r["revealed"] = True
            record_results(self.store, self.root, self.email, [r], source="teachback",
                           kind="teachback", prior=prior)  # fmt: skip
        return {
            **q,
            "label": ladder.label(level),
            "verdict": r["verdict"],
            "right": r["right"],
            "missing": r["missing"],
            "misconception": r["misconception"],
            "revealed": bool(r.get("revealed")),
            "reference": r["reference_answer"] if r.get("revealed") else None,
        }

    def post_tutor(self, body):
        message = str(body.get("message", "")).strip()
        if not message:
            raise HTTPError(400, "Say something to the tutor first.")
        m = self.store.require_map()
        sid = str(body.get("session") or "")
        s = self.tutor_session(sid) if sid else None
        if sid and s is None:
            raise HTTPError(404, "No such tutor session.")
        if s is None:
            u = self.unit(m, body.get("unit"))
            saved = tutor.load(self.store.dir, self.email, u["id"])
            if saved is not None and not body.get("restart"):
                raise HTTPError(
                    409, "There is a saved conversation on this unit: resume it or start over.",
                    {"saved": saved.view()},
                )  # fmt: skip
            if saved is not None:
                tutor.discard(self.store.dir, self.email, u["id"])
                self.tutors.pop(saved.id, None)
            recs = self.store.load_records(self.email)
            level = ladder.next_level([r for r in recs if r.get("unit") == u["id"]])
            s = tutor.Session(u["id"], level, self.email)
            self.tutors[s.id] = s
        u = self.unit(m, s.unit_id)
        view = tutor.turn(self.llm(), self.root, u, s, message)
        tutor.save(self.store.dir, s)
        return view

    def post_tutor_answer(self, body):
        s = self.tutor_session(str(body.get("session", "")))
        if s is None:
            raise HTTPError(404, "No such tutor session.")
        u = self.unit(self.store.require_map(), s.unit_id)
        q = tutor.check_question(u, s)
        if q is None:
            raise HTTPError(400, "The tutor has not proposed a check question yet.")
        res = self.answered({**q, "source": "tutor"}, str(body.get("answer", "")),
                            _int(body.get("confidence"), 2, 1, 3))  # fmt: skip
        s.check = None
        s.turns.append({"role": "check", "text": f"{q['question']} -> {res['verdict']}"})
        tutor.save(self.store.dir, s)
        return {**res, "session": s.view()}

    GET = {
        "/api/state": get_state,
        "/api/dashboard": get_dashboard,
        "/api/tour": get_tour,
        "/api/path": get_path,
        "/api/unit": get_unit,
        "/api/excerpt": get_excerpt,
        "/api/review": get_review,
        "/api/digest": get_digest,
        "/api/tutor": get_tutor,
    }
    POST = {
        "/api/tour": post_tour,
        "/api/walkthrough": post_walkthrough,
        "/api/questions": post_questions,
        "/api/answer": post_answer,
        "/api/answers": post_answers,
        "/api/dontknow": post_dontknow,
        "/api/teachback": post_teachback,
        "/api/digest": post_digest,
        "/api/tutor": post_tutor,
        "/api/tutor/answer": post_tutor_answer,
    }


def static(name: str) -> bytes:
    return resources.files("udebt").joinpath("static", name).read_bytes()


class Handler(BaseHTTPRequestHandler):
    server_version = "udebt"
    sys_version = ""

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):  # the request line carries the token: say nothing
        if getattr(self.server, "verbose", False):
            print(f"[udebt serve] {self.command} {urlsplit(self.path).path}", file=sys.stderr)

    def _send(self, status: int, body: bytes, ctype: str, csp: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        if csp:
            self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", "default-src 'none'")

    def _host_ok(self) -> bool:
        port = self.app.port
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _token_ok(self, given: str | None) -> bool:
        return bool(given) and hmac.compare_digest(str(given), self.app.token)

    def _query(self) -> dict[str, str]:
        return {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}

    def _route(self, method: str) -> None:
        path = urlsplit(self.path).path
        try:
            if not self._host_ok():
                raise HTTPError(403, "Wrong Host: udebt serve answers on 127.0.0.1 only.")
            q = self._query()
            if method == "GET" and not path.startswith("/api/"):
                return self._page(path, q)
            if not self._token_ok(self.headers.get("X-Udebt-Token")):
                raise HTTPError(403, "Missing or wrong X-Udebt-Token.")
            table = App.GET if method == "GET" else App.POST
            fn = table.get(path)
            if fn is None:
                raise HTTPError(404 if path not in App.GET and path not in App.POST else 405,
                                f"No {method} {path}")  # fmt: skip
            if method == "POST":
                q = self._body()
            self._json(200, fn(self.app, q))
        except HTTPError as e:
            self._json(e.status, {**e.data, "error": e.message})
        except LLMError as e:
            self._json(502, {"error": f"The model call failed: {e}"})
        except SystemExit as e:
            self._json(400, {"error": str(e)})
        except Exception as e:  # a local tool: say what broke
            traceback.print_exc()
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _body(self) -> dict[str, Any]:
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise HTTPError(415, "POST bodies are JSON (Content-Type: application/json).")
        n = _int(self.headers.get("Content-Length"), -1, -1, 10**9)
        if n < 0:
            raise HTTPError(411, "Content-Length required.")
        if n > MAX_BODY:
            if n <= 8 * MAX_BODY:  # read it away, so the client hears the 413 (no broken pipe)
                left = n
                while left > 0:
                    got = self.rfile.read(min(left, 65536))
                    if not got:
                        break
                    left -= len(got)
            self.close_connection = True
            raise HTTPError(413, "Body too large.")
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError as e:
            raise HTTPError(400, "Not JSON.") from e
        if not isinstance(data, dict):
            raise HTTPError(400, "The body is a JSON object.")
        return data

    def _page(self, path: str, q: dict[str, str]) -> None:
        if path.startswith("/static/"):
            name = path[len("/static/") :]
            if name not in STATIC:
                raise HTTPError(404, "No such file.")
            return self._send(200, static(name), STATIC[name])
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if not self._token_ok(q.get("t")):
            raise HTTPError(403, "Open the URL `udebt serve` printed: it carries the token.")
        if path in ("/", "/index.html"):
            return self._send(200, static("index.html"), "text/html; charset=utf-8", CSP_APP)
        if path == "/dashboard":
            from .dashboard import render

            page = render(None, self.app.root.name).encode("utf-8")
            return self._send(200, page, "text/html; charset=utf-8", CSP_DASHBOARD)
        raise HTTPError(404, "No such page.")

    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")


def make_server(app: App, port: int = 0, verbose: bool = False) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    httpd.app = app  # type: ignore[attr-defined]
    httpd.verbose = verbose  # type: ignore[attr-defined]
    app.port = httpd.server_address[1]
    return httpd


def url(app: App) -> str:
    return f"http://127.0.0.1:{app.port}/?t={app.token}"


def run(app: App, port: int = 0, *, open_browser: bool = False, verbose: bool = False) -> int:
    import webbrowser

    httpd = make_server(app, port, verbose)
    print(f"udebt serve: {app.root.name} for {app.email}")
    print(f"Open {url(app)}")
    print("Local only (127.0.0.1); the token in the URL is new each run. Ctrl-C stops it.")
    if open_browser:
        webbrowser.open(url(app))
    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


def start_in_thread(app: App, port: int = 0) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """For tests: the server on an ephemeral port, serving in a daemon thread."""
    httpd = make_server(app, port)
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    time.sleep(0.01)
    return httpd, t
