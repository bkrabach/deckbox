"""Markdown rendering with a rich, modern feature set."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

import markdown
import yaml

_EXTENSIONS = [
    "pymdownx.superfences",
    "pymdownx.highlight",
    "pymdownx.tasklist",
    "pymdownx.tilde",
    "pymdownx.betterem",
    "pymdownx.magiclink",
    "pymdownx.saneheaders",
    "tables",
    "footnotes",
    "sane_lists",
    "admonition",
    "toc",
]

_EXTENSION_CONFIGS = {
    "pymdownx.highlight": {"css_class": "highlight", "guess_lang": False},
    "pymdownx.tasklist": {"custom_checkbox": True},
    "toc": {"permalink": True},
}


# Leading YAML frontmatter: --- ... --- (or the ... terminator) at the very top.
_FRONTMATTER_RE = re.compile(r"^\ufeff?---[ \t]*\n(.*?)\n(?:---|\.\.\.)[ \t]*(?:\n|$)", re.DOTALL)


def _split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    """Return (parsed_frontmatter, remaining_body). Frontmatter is only split
    off when it's a leading YAML block that parses to a mapping."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None, text
    if not isinstance(data, dict) or not data:
        return None, text
    return data, text[m.end() :]


def _format_value(value: Any) -> str:
    """Human-readable, escaped HTML for a frontmatter value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return '<span class="fm-null">—</span>'
    if isinstance(value, (list, tuple)):
        if not value:
            return '<span class="fm-null">—</span>'
        items = "".join(f"<li>{_format_value(v)}</li>" for v in value)
        return f'<ul class="fm-list">{items}</ul>'
    if isinstance(value, dict):
        rows = "".join(
            f'<div class="fm-subrow"><span class="fm-subkey">{html.escape(str(k))}</span>'
            f'<span class="fm-subval">{_format_value(v)}</span></div>'
            for k, v in value.items()
        )
        return f'<div class="fm-sub">{rows}</div>'
    return html.escape(str(value))


def _render_frontmatter(data: dict[str, Any]) -> str:
    rows = "".join(
        f'<div class="fm-row"><div class="fm-key">{html.escape(str(k))}</div>'
        f'<div class="fm-val">{_format_value(v)}</div></div>'
        for k, v in data.items()
    )
    return (
        '<section class="frontmatter" aria-label="Document metadata">'
        '<div class="fm-head"><span class="fm-tag">Frontmatter</span></div>'
        f'<div class="fm-grid">{rows}</div></section>'
    )


def render(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body_text = _split_frontmatter(text)
    md = markdown.Markdown(extensions=_EXTENSIONS, extension_configs=_EXTENSION_CONFIGS)
    body = md.convert(body_text)
    fm_html = _render_frontmatter(frontmatter) if frontmatter else ""
    return f'<article class="markdown-body">{fm_html}{body}</article>'
