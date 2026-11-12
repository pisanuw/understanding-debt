"""`udebt dashboard`: one static, self-contained HTML file over the replayed history.

The page is `udebt/dashboard.html`, plain HTML with its CSS and script inline.

Everything is inline: CSS, hand-written JavaScript, SVG drawn by that script. No CDN, no
external font, no request of any kind, so the file opens offline and from `file://`. The
data is one JSON blob (replay.export_data, without answer text) in a
`<script type="application/json" id="udebt-data">` tag: swap the blob and the page redraws
another repository, or regenerate the file with `udebt dashboard`.

Colours follow a validated categorical palette in fixed order (one hue per contributor,
light and dark steps chosen separately), status colours only for risk levels and the bus
target, always with a text label; `prefers-color-scheme` picks the mode.
"""

from __future__ import annotations

import html
import json
from importlib import resources
from typing import Any


def blob(data: dict[str, Any]) -> str:
    """The data as JSON safe inside a <script> element: no `</` (it would close the element)
    and no `<!--`, both written with JSON escapes that JSON.parse reads back unchanged."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("</", "<\\/").replace("<!--", "<\\u0021--")


def template() -> str:
    """The page (udebt/dashboard.html), with __TITLE__ and __DATA__ to fill in."""
    return resources.files("udebt").joinpath("dashboard.html").read_text(encoding="utf-8")


def render(data: dict[str, Any]) -> str:
    title = html.escape(f"Understanding debt: {data.get('repo', '')}")
    return template().replace("__TITLE__", title).replace("__DATA__", blob(data))
