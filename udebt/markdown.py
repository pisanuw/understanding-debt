"""A small Markdown-to-HTML renderer for the pages `udebt serve` shows (the tour, decision
records): headings, paragraphs, bullet and numbered lists, fenced code, rules, and inline
code, bold, italics and relative links. Everything is HTML-escaped first, so text from the
model or the map cannot inject markup; anything else renders as plain text."""

from __future__ import annotations

import html
import re

_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_EM = re.compile(r"(?<![\w*])[*_]([^*_\s][^*_]*?)[*_](?![\w*])")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_SAFE_HREF = re.compile(r"^(?:#|[\w./-]+$)")


def inline(text: str) -> str:
    parts = _CODE.split(text)
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(f"<code>{html.escape(part)}</code>")
            continue
        t = html.escape(part, quote=False)
        t = _BOLD.sub(r"<strong>\1</strong>", t)
        t = _EM.sub(r"<em>\1</em>", t)

        def link(m: re.Match) -> str:
            href = m.group(2)
            if not _SAFE_HREF.match(href):
                return m.group(0)
            return f'<a href="{html.escape(href)}">{m.group(1)}</a>'

        t = _LINK.sub(link, t)
        out.append(t)
    return "".join(out)


def to_html(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    para: list[str] = []
    lst: str | None = None
    i = 0

    def flush() -> None:
        nonlocal lst
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()
        if lst:
            out.append(f"</{lst}>")
            lst = None

    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("```"):
            flush()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            out.append("<pre><code>" + html.escape("\n".join(body)) + "</code></pre>")
            i += 1
            continue
        h = re.match(r"^(#{1,6})\s+(.*)$", s)
        bullet = re.match(r"^[-*]\s+(.*)$", s)
        num = re.match(r"^\d+\.\s+(.*)$", s)
        if not s:
            flush()
        elif s in ("---", "***"):
            flush()
            out.append("<hr>")
        elif h:
            flush()
            n = len(h.group(1))
            out.append(f"<h{n}>{inline(h.group(2))}</h{n}>")
        elif (bullet or num) and not ln.startswith("    "):
            kind = "ul" if bullet else "ol"
            if para:
                out.append("<p>" + inline(" ".join(para)) + "</p>")
                para.clear()
            if lst != kind:
                if lst:
                    out.append(f"</{lst}>")
                out.append(f"<{kind}>")
                lst = kind
            out.append("<li>" + inline((bullet or num).group(1)) + "</li>")
        elif lst and ln.startswith((" ", "\t")) and out and out[-1].startswith("<li>"):
            out[-1] = out[-1][: -len("</li>")] + " " + inline(s) + "</li>"
        else:
            if lst:
                out.append(f"</{lst}>")
                lst = None
            para.append(s)
        i += 1
    flush()
    return "\n".join(out)
