"""The pull-request check (ROADMAP "Gating, explained"): `udebt risk` as a GitHub check run
(Phase 4a; no web framework: whatever receives the webhook calls this, and `udebt risk
--check-run` prints it).

Advisory by default: the check always passes, with the verdict in its title and a table of the
units the change lands weight in, the author's understanding of each and the fastest way to
raise it. A repository owner can opt into a **soft block**: a change that is HIGH or OUTSIDE
then fails the check until a lead (or owner) acknowledges it, by adding the label
`udebt-acknowledged` or commenting `/udebt ack` on the pull request; the acknowledgement is
recorded (who, how, for which head) and the check is posted again as passing. Never a hard
block: nothing waits on the author's quiz score.
"""

from __future__ import annotations

from typing import Any

NAME = "udebt understanding"
ORDER = {"OUTSIDE": 0, "HIGH": 1, "CAUTION": 2, "OK": 3}
BLOCKING = ("OUTSIDE", "HIGH")


def overall(risks: list[dict[str, Any]]) -> str:
    return min((r["level"] for r in risks), key=ORDER.__getitem__, default="OK")


def payload(
    head_sha: str,
    author: str,
    risks: list[dict[str, Any]],
    *,
    soft_block: bool = False,
    ack: dict[str, Any] | None = None,
    ack_label: str = "udebt-acknowledged",
    details_url: str | None = None,
) -> dict[str, Any]:
    """The Checks API body (POST /repos/{owner}/{repo}/check-runs) for one head commit."""
    level = overall(risks)
    flagged = [r for r in risks if r["level"] != "OK"]
    worst = flagged[0] if flagged else None
    blocked = soft_block and level in BLOCKING and ack is None
    if ack is not None and level in BLOCKING:
        conclusion = "success"
        title = f"{level} in {worst['unit']}, acknowledged by @{ack.get('login') or 'a lead'}"
    elif blocked:
        conclusion = "failure"
        title = f"{level} in {worst['unit']}: a lead's acknowledgement clears this"
    elif worst is not None:
        conclusion = "success"
        title = f"Advisory: {level} in {worst['unit']}"
    else:
        conclusion = "success"
        title = "No architectural unit at risk"
    lines = [
        f"**{level}** for @{author}: the weight this change lands in each unit it touches, "
        "against the author's understanding of that unit (udebt's quiz evidence, or the "
        "own-commit prior).",
        "",
    ]
    if risks:
        lines += ["| level | unit | in set | mass | understanding |", "|---|---|---|--:|--:|"]
        for r in risks:
            in_set = {True: "in", False: "**out**", None: "-"}[r.get("in_set")]
            lines.append(f"| {r['level']} | `{r['unit']}` | {in_set} | {r['mass']:.2f} | "
                         f"{r['understanding']:.2f} |")  # fmt: skip
    else:
        lines.append("The change touches no architectural unit.")
    if worst is not None:
        u = worst["unit"].split()[0]
        lines += ["", f"To raise it: `udebt dig {u}` (a few minutes), or catch up on what landed "
                  f"there: `udebt digest --unit {u}`."]  # fmt: skip
    if blocked:
        lines += [
            "",
            f"This repository opted into soft blocking. A lead can acknowledge it by adding the "
            f"label `{ack_label}` or commenting `/udebt ack`; the acknowledgement is recorded "
            "as the lead taking responsibility for review.",
        ]
    elif soft_block and level not in BLOCKING:
        lines += ["", "Soft blocking is on for this repository; nothing here needs it."]
    else:
        lines += ["", "Advisory: this check always passes unless the repository owner opts into "
                  "soft blocking."]  # fmt: skip
    if ack is not None:
        lines += ["", f"Acknowledged by @{ack.get('login')} via {ack.get('via')}."]
    out = {
        "name": NAME,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": {"title": title[:240], "summary": "\n".join(lines)[:60000]},
    }
    if details_url:
        out["details_url"] = details_url
    return out


def rows(risks: list[Any]) -> list[dict[str, Any]]:
    """risk.UnitRisk rows (risk.assess) as the dicts `payload` takes; muted rows are OK."""
    return [
        {"unit": r.unit_id, "name": r.name, "level": r.level, "mass": r.mass,
         "understanding": round(r.understanding, 3), "in_set": r.in_set, "files": r.files}
        for r in risks
    ]  # fmt: skip
