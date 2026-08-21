"""Markdown rendering with a rich, modern feature set."""

from __future__ import annotations

from pathlib import Path

import markdown

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


def render(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    md = markdown.Markdown(extensions=_EXTENSIONS, extension_configs=_EXTENSION_CONFIGS)
    body = md.convert(text)
    return f'<article class="markdown-body">{body}</article>'
