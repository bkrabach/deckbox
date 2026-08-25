"""Syntax-highlighted rendering for code and JSON via Pygments."""

from __future__ import annotations

import json
from pathlib import Path

from pygments import highlight as _highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name, get_lexer_for_filename, guess_lexer
from pygments.util import ClassNotFound

_FORMATTER = HtmlFormatter(cssclass="highlight", linenos="table", wrapcode=True)


def _strip_container_bg(css: str, selector: str) -> str:
    """Drop pygments' own ``<selector> { background: ... }`` container rule so the
    app's themed ``--code-bg`` controls the code-block surface instead."""
    needle = f"{selector} {{"
    return "\n".join(line for line in css.splitlines() if not line.strip().startswith(needle))


def pygments_css() -> str:
    """Theme-aware CSS for the syntax-highlight token classes.

    Light tokens (``default``) apply normally; a dark palette is scoped under
    ``[data-theme="dark"] .highlight`` so code is readable in dark mode. Both
    have their hardcoded container background stripped — the app themes the
    surface via ``--code-bg`` — which also fixes pygments' ``.highlight``
    background winning over the app rule purely by stylesheet load order.
    """
    light = HtmlFormatter(style="default").get_style_defs(".highlight")
    dark = HtmlFormatter(style="github-dark").get_style_defs('[data-theme="dark"] .highlight')
    light = _strip_container_bg(light, ".highlight")
    dark = _strip_container_bg(dark, '[data-theme="dark"] .highlight')
    return f"{light}\n{dark}"


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
