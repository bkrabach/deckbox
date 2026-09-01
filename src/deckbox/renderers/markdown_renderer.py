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

import nh3
import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token
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


# GitHub alerts: a blockquote whose first line is exactly [!NOTE] / [!TIP] /
# [!IMPORTANT] / [!WARNING] / [!CAUTION] renders as a coloured callout.
_ALERT_RE = re.compile(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$", re.IGNORECASE)


def _github_alerts_plugin(md: MarkdownIt) -> None:
    """Transform GitHub-style alert blockquotes into styled `<div>` callouts.

    Runs as a core rule after block parsing: a `blockquote_open` whose first
    paragraph begins with an alert marker (`[!NOTE]`, …) becomes
    `<div class="markdown-alert markdown-alert-note">` with a title row, and the
    marker text is stripped from the body.
    """

    def rule(state) -> None:  # noqa: ANN001
        tokens = state.tokens
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if (
                tok.type == "blockquote_open"
                and i + 2 < len(tokens)
                and tokens[i + 1].type == "paragraph_open"
                and tokens[i + 2].type == "inline"
            ):
                inline = tokens[i + 2]
                kids = inline.children or []
                m = _ALERT_RE.match(kids[0].content) if kids and kids[0].type == "text" else None
                if m:
                    kind = m.group(1).upper()
                    lower = kind.lower()
                    # Retag the blockquote (and its matching close) as an alert div.
                    tok.tag = "div"
                    tok.attrSet("class", f"markdown-alert markdown-alert-{lower}")
                    depth = 0
                    for j in range(i, len(tokens)):
                        if tokens[j].type == "blockquote_open":
                            depth += 1
                        elif tokens[j].type == "blockquote_close":
                            depth -= 1
                            if depth == 0:
                                tokens[j].tag = "div"
                                break
                    # Drop the "[!NOTE]" marker text and the softbreak after it.
                    del kids[0]
                    if kids and kids[0].type == "softbreak":
                        del kids[0]
                    # Inject a title row just inside the alert div.
                    title = Token("html_block", "", 0)
                    title.content = (
                        f'<p class="markdown-alert-title">{kind.capitalize()}</p>'
                    )
                    tokens.insert(i + 1, title)
            i += 1

    md.core.ruler.push("github_alerts", rule)


def _make_md() -> MarkdownIt:
    # html=True renders raw inline/block HTML the way GitHub does (centered
    # <h1>/<p align>, <img> badges, <details>, etc.). The rendered body is then
    # run through nh3 (Rust/ammonia) sanitisation before display, so <script>,
    # event handlers, and javascript: URLs never reach the page — see _sanitize.
    md = MarkdownIt("gfm-like", {"html": True, "linkify": True, "typographer": False})
    # linkify treats a bare word ending in a real TLD as a link, and `.md`
    # (Moldova) / `.sh` / `.py` are real TLDs — so `smart-tools.md` would become
    # <a href="http://smart-tools.md">. Turn OFF fuzzy link detection so only
    # explicit http(s):// / www. / emails autolink, not filenames.
    if md.linkify is not None:
        md.linkify.set({"fuzzy_link": False, "fuzzy_email": False})
    md.use(footnote_plugin)
    md.use(deflist_plugin)
    md.use(admon_plugin)
    md.use(tasklists_plugin, enabled=True, label=True)
    md.use(_github_alerts_plugin)
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


# ---- HTML sanitisation (GitHub-like) --------------------------------------
# Rendered markdown may contain raw HTML (html=True). Before it reaches the
# page it is sanitised with nh3 (Rust/ammonia): script/style/iframe/object,
# event handlers (on*), and javascript:/data: URLs are stripped, while the
# tags GitHub allows in a README — including the ones our own pipeline emits
# (pygments `<span class>`, heading anchors, task-list checkboxes) — survive.
_ALLOWED_TAGS = {
    # headings / text structure
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "span", "br", "hr",
    "blockquote", "pre", "code", "kbd", "samp", "var",
    # lists / definition lists
    "ul", "ol", "li", "dl", "dt", "dd",
    # inline emphasis
    "a", "b", "i", "strong", "em", "s", "del", "ins", "sub", "sup", "mark",
    "small", "abbr", "cite", "q", "u",
    # media / figures
    "img", "picture", "source", "figure", "figcaption",
    # tables
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
    # disclosure + task-list inputs
    "details", "summary", "input",
}
# Attributes: a generous-but-safe set. `align` powers GitHub's centred headers;
# `class`/`id` keep pygments highlighting, heading anchors, and callouts styled.
_ALLOWED_ATTRS = {
    "*": {"align", "class", "id", "title", "dir", "lang", "role"},
    "a": {"href", "name", "target"},  # nh3 adds `rel` itself via link_rel
    "img": {"src", "alt", "width", "height", "loading", "decoding", "srcset", "sizes"},
    "source": {"src", "srcset", "sizes", "media", "type"},
    "ol": {"start", "type"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "col": {"span"},
    "colgroup": {"span"},
    "input": {"type", "checked", "disabled"},
}
_ALLOWED_URL_SCHEMES = {"http", "https", "mailto", "tel"}


def _sanitize(html_body: str) -> str:
    return nh3.clean(
        html_body,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        url_schemes=_ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer nofollow",
    )


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
    # Sanitise AFTER rewriting so the /view and /raw relative URLs are preserved;
    # strips any script/style/on*/javascript: that raw HTML (html=True) let in.
    body = _sanitize(body)
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
        "</div>"
        f'<div class="md-rendered" data-md-rendered>{rendered}</div>'
        f'<pre class="md-source jsonl-rawpre" data-md-source hidden>{source}</pre>'
        "</div>"
    )
