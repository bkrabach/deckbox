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
from pathlib import Path, PurePosixPath
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


# A URL is "external" (left untouched) if it has a scheme (http:, mailto:, …),
# is protocol-relative (//host), an in-page anchor (#sec), or already absolute (/x).
_ABSOLUTE_URL_RE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//|#|/|data:|mailto:)")
# Matches href="..." and src="..." attribute values in the rendered HTML.
_ATTR_RE = re.compile(r'(?P<attr>\b(?:href|src)=")(?P<url>[^"]*)"')


def _resolve_relative(url: str, base_dir: str, route: str) -> str:
    """Resolve a document-relative URL against the current file's directory and
    map it onto a server route (/view for links, /raw for images/media).

    Keeps a trailing #anchor and ?query on the resolved path. Returns the URL
    unchanged if it's external, absolute, or a bare anchor.
    """
    if not url or _ABSOLUTE_URL_RE.match(url):
        return url

    # Split off a trailing #fragment / ?query so only the path is resolved.
    frag = ""
    for sep in ("#", "?"):
        i = url.find(sep)
        if i != -1:
            frag = url[i:] + frag if sep == "#" else url[i:]
            url = url[:i]
    if not url:  # was just "#anchor" (handled above) or "?query"
        return url + frag

    from urllib.parse import quote, unquote

    # base_dir is the POSIX dir of the current file's /view path; join + normalise.
    joined = PurePosixPath(base_dir) / unquote(url)
    parts: list[str] = []
    for part in joined.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    resolved = "/".join(parts)
    return f"{route}/{quote(resolved)}{frag}"


def _rewrite_relative_links(html_body: str, src_path: str) -> str:
    """Rewrite document-relative href/src in rendered markdown to server routes.

    Links (``href``) point at other files -> /view/<path> (so they open in the
    viewer); media (``src``, e.g. images) -> /raw/<path> (so the bytes load).
    """
    if not src_path:
        return html_body
    base_dir = str(PurePosixPath(src_path).parent)

    def repl(m: re.Match[str]) -> str:
        attr, url = m.group("attr"), m.group("url")
        route = "/raw" if attr.startswith("src") else "/view"
        return f'{attr}{_resolve_relative(url, base_dir, route)}"'

    return _ATTR_RE.sub(repl, html_body)


def render(path: Path, *, src_path: str = "") -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body_text = _split_frontmatter(text)
    body = _MD.render(body_text)
    body = _rewrite_relative_links(body, src_path)
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
