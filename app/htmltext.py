"""Convert HTML e-mail bodies to clean, readable plain text — stdlib only.

Real Outlook/Graph messages arrive as full HTML documents (with <head>, <style>,
@font-face junk, etc.). Showing that raw in the UI looks terrible and pollutes the
AI's view of the message. This strips it to readable text, dropping non-visible
sections, while leaving genuine plain-text bodies (e.g. the seed data) untouched.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

# Tags whose *content* is never visible body text. Only elements that actually
# have a closing tag — void elements (meta, link) would otherwise unbalance the
# skip counter and swallow the whole body. <head> already covers meta/link/title.
_SKIP = {"style", "script", "head", "title", "noscript"}
# Tags that introduce a line/paragraph break.
_BLOCK = {
    "p", "div", "br", "li", "tr", "blockquote", "section", "article",
    "header", "footer", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6",
}
# Heuristic: only treat input as HTML if it actually looks like markup, so plain
# text (with its own line breaks) passes through unharmed.
_HTMLISH = re.compile(r"<\s*(?:!--|html|body|head|div|p\b|br|span|table|style|meta|font|o:)", re.I)


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # entities decoded into data
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            # Collapse intra-run whitespace; block tags above provide real breaks.
            self.parts.append(re.sub(r"\s+", " ", data))


def html_to_text(value: str | None) -> str:
    """Readable text for an e-mail body. Plain text is returned trimmed and
    unchanged; HTML is stripped to text with paragraph breaks (no <style>/<head>)."""
    if not value:
        return ""
    if not _HTMLISH.search(value):
        return value.strip()
    parser = _Extractor()
    try:
        parser.feed(value)
    except Exception:  # noqa: BLE001 — never let a malformed body break rendering
        return re.sub(r"<[^>]+>", " ", value).strip()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)   # trim spaces around breaks
    text = re.sub(r"\n{3,}", "\n\n", text)          # collapse blank runs
    return text.strip()
