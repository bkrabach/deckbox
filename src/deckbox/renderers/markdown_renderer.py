"""Markdown rendering with a CommonMark / GFM engine (markdown-it-py).

python-markdown is not CommonMark-compliant: it fails to render a list that
follows a paragraph line with no blank line between them, and it needs 4-space
indentation for nested lists. Real-world (GitHub-authored) markdown relies on
both, so this uses markdown-it-py's ``gfm-like`` preset, which renders those the
way GitHub does. YAML frontmatter is split off and rendered as its own card.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

import yaml
from markdown_it import MarkdownIt
from mdit_py_plugins.admon import admon_plugin
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight as _pyg_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound


def _highlight_code(code: str, lang: str) -> str:
    """Themed pygments HTML for a fenced block, or "" to fall back to plain."""
    if not lang:
        return ""
    try:
        lexer = get_lexer_by_name(lang, stripnl=False)
    except ClassNotFound:
        return ""
    return _pyg_highlight(code, lexer, HtmlFormatter(cssclass="highlight", wrapcode=True))


def _render_fence(tokens, idx, options, env):
    """Fence renderer that emits the same `<div class="highlight">` structure the
    old pipeline produced (so the themed CSS and the copy-on-hover button keep
    working), for both syntax-highlighted and plain (no-language) blocks.

    Assigned into ``md.renderer.rules["fence"]``, which invokes rules as plain
    functions with ``(tokens, idx, options, env)`` — no bound renderer arg."""
    token = tokens[idx]
    info = (token.info or "").strip()
    lang = info.split()[0] if info else ""
    out = _highlight_code(token.content, lang)
    if out:
        return out + "\n"
    esc = html.escape(token.content)
    cls = f' class="language-{html.escape(lang)}"' if lang else ""
    return f'<div class="highlight"><pre><code{cls}>{esc}</code></pre></div>\n'


def _make_md() -> MarkdownIt:
    md = MarkdownIt("gfm-like", {"html": False, "linkify": True, "typographer": False})
    md.use(footnote_plugin)
    md.use(deflist_plugin)
    md.use(admon_plugin)
    md.use(tasklists_plugin, enabled=True, label=True)
    # Heading anchors with a hover-revealed pilcrow permalink (styled via CSS).
    md.use(
        anchors_plugin,
        min_level=1,
        max_level=4,
        permalink=True,
        permalinkSymbol="¶",
        permalinkSpace=False,
    )
    md.renderer.rules["fence"] = _render_fence
    return md


# One shared parser (markdown-it-py instances are reusable and stateless).
_MD = _make_md()


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
        # A list of scalars renders as compact inline pills; a list containing
        # objects/lists (e.g. a `steps` array) renders as stacked block cards so
        # long values wrap instead of stretching the row off-screen.
        complex_items = any(isinstance(v, (dict, list, tuple)) for v in value)
        cls = "fm-list fm-list-block" if complex_items else "fm-list"
        items = "".join(f"<li>{_format_value(v)}</li>" for v in value)
        return f'<ul class="{cls}">{items}</ul>'
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
    # Embed the parsed data (JSON) so the client can offer a structured tree
    # view (JSONViewer), the same component the JSON/JSONL viewer uses. The
    # payload is HTML-escaped into a data attribute; the toggle + hydration is
    # handled by static/js/frontmatter.js.
    payload = html.escape(json.dumps(data, ensure_ascii=False, default=str), quote=True)
    return (
        '<section class="frontmatter" data-frontmatter aria-label="Document metadata"'
        f' data-fm-json="{payload}">'
        '<div class="fm-head">'
        '<span class="fm-tag">Frontmatter</span>'
        '<div class="fm-viewtoggle jsonl-seg" role="group" aria-label="Frontmatter view" hidden data-fm-toggle>'
        '<button type="button" data-fm-view="table" aria-pressed="true">Table</button>'
        '<button type="button" data-fm-view="tree" aria-pressed="false">Tree</button>'
        "</div>"
        "</div>"
        f'<div class="fm-grid" data-fm-table>{rows}</div>'
        '<div class="fm-tree" data-fm-tree hidden></div>'
        "</section>"
    )


def render(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body_text = _split_frontmatter(text)
    body = _MD.render(body_text)
    fm_html = _render_frontmatter(frontmatter) if frontmatter else ""
    rendered = f'<article class="markdown-body">{fm_html}{body}</article>'
    # Wrap in a viewer shell with a Rendered | Source toggle and a copy button,
    # matching the DOT/JSONL viewers. The raw source is served in a <pre> for the
    # Source view; static/js/markdown.js wires the toggle + copy.
    source = html.escape(text)
    return (
        '<div class="md-viewer" data-md-viewer>'
        '<div class="md-toolbar">'
        '<div class="jsonl-seg" role="group" aria-label="View mode" data-md-toggle>'
        '<button type="button" data-md-view="rendered" aria-pressed="true">Rendered</button>'
        '<button type="button" data-md-view="source" aria-pressed="false">Source</button>'
        "</div>"
        '<span class="md-spacer"></span>'
        '<button type="button" class="md-copy" data-md-copy>Copy</button>'
        "</div>"
        f'<div class="md-rendered" data-md-rendered>{rendered}</div>'
        f'<pre class="md-source jsonl-rawpre" data-md-source hidden>{source}</pre>'
        "</div>"
    )
