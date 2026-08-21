"""Syntax-highlighted rendering for code and JSON via Pygments."""

from __future__ import annotations

import json
from pathlib import Path

from pygments import highlight as _highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name, get_lexer_for_filename, guess_lexer
from pygments.util import ClassNotFound

_FORMATTER = HtmlFormatter(cssclass="highlight", linenos="table", wrapcode=True)


def pygments_css() -> str:
    """CSS for the syntax-highlight token classes (scoped to .highlight)."""
    return HtmlFormatter(style="default").get_style_defs(".highlight")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def render_code(path: Path) -> str:
    text = _read_text(path)
    try:
        lexer = get_lexer_for_filename(path.name, stripnl=False)
    except ClassNotFound:
        try:
            lexer = guess_lexer(text)
        except ClassNotFound:
            lexer = TextLexer(stripnl=False)
    return _highlight(text, lexer, _FORMATTER)


def render_json(path: Path) -> str:
    raw = _read_text(path)
    try:
        parsed = json.loads(raw)
        pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        pretty = raw  # show as-is; still highlight as json
    lexer = get_lexer_by_name("json", stripnl=False)
    return _highlight(pretty, lexer, _FORMATTER)
