"""Elegant GraphViz DOT rendering.

Strategy for a nicer result than raw ``dot -Tsvg``:

  1. Inject tasteful *default* graph/node/edge attributes (author's explicit
     attributes still win, because our defaults are prepended).
  2. Render to SVG with Graphviz.
  3. Rewrite the SVG font-family to a modern system font stack so text is crisp
     regardless of which fonts Graphviz found on the host.
  4. Return an interactive viewer (pan / zoom / fit / download / source toggle).
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
from pathlib import Path

_FONT_STACK = (
    "-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',Roboto,Helvetica,Arial,sans-serif"
)

# Prepended right after the opening brace of the graph.
_THEME = (
    '  graph [bgcolor="transparent" fontname="Helvetica" fontsize=12 pad=0.35 '
    "nodesep=0.35 ranksep=0.55];\n"
    '  node [fontname="Helvetica" fontsize=12 shape=box style="rounded,filled" '
    'fillcolor="#eef2f8" color="#c2ccdb" fontcolor="#1f2733" penwidth=1.0 '
    'margin="0.22,0.14"];\n'
    '  edge [fontname="Helvetica" fontsize=11 color="#9aa4b5" arrowsize=0.75 '
    "penwidth=1.1];\n"
)

_BRACE_RE = re.compile(r"\{")
_FONT_FAMILY_RE = re.compile(r'font-family="[^"]*"')
_SVG_OPEN_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)


class GraphvizNotInstalled(RuntimeError):
    """Raised when the `dot` binary is not on PATH."""


class GraphvizRenderError(RuntimeError):
    """Raised when `dot` exits non-zero (invalid graph, etc.)."""


def graphviz_available() -> bool:
    return shutil.which("dot") is not None


def _apply_theme(source: str) -> str:
    match = _BRACE_RE.search(source)
    if not match:
        return source
    idx = match.end()
    return source[:idx] + "\n" + _THEME + source[idx:]


def _run_dot(source: str) -> str:
    if not graphviz_available():
        raise GraphvizNotInstalled("the 'dot' binary (graphviz) is not installed")
    try:
        proc = subprocess.run(
            ["dot", "-Tsvg"],
            input=source,
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GraphvizRenderError("graph rendering timed out") from exc
    if proc.returncode != 0:
        raise GraphvizRenderError(proc.stderr.strip() or "dot failed")
    return proc.stdout


def _restyle_svg(svg: str) -> str:
    # Drop the XML/doctype preamble so the SVG embeds cleanly.
    start = svg.find("<svg")
    if start > 0:
        svg = svg[start:]
    # Modern font stack for all text.
    svg = _FONT_FAMILY_RE.sub(f'font-family="{_FONT_STACK}"', svg)

    # Make the root <svg> fluid: keep viewBox, drop fixed width/height.
    def _fluid(match: re.Match) -> str:
        tag = match.group(0)
        tag = re.sub(r'\swidth="[^"]*"', "", tag, count=1)
        tag = re.sub(r'\sheight="[^"]*"', "", tag, count=1)
        return tag.replace("<svg", '<svg class="dot-svg"', 1)

    return _SVG_OPEN_RE.sub(_fluid, svg, count=1)


def render(path: Path) -> str:
    source = path.read_text(encoding="utf-8", errors="replace")
    escaped_source = html.escape(source)
    try:
        svg = _restyle_svg(_run_dot(_apply_theme(source)))
    except GraphvizNotInstalled:
        return _fallback(
            "GraphViz is not installed, so this graph can’t be drawn. "
            "Install it (e.g. <code>apt install graphviz</code>) and reload.",
            escaped_source,
        )
    except GraphvizRenderError as exc:
        return _fallback(
            f"GraphViz could not render this file:<br><code>{html.escape(str(exc))}</code>",
            escaped_source,
        )
    return _viewer(svg, escaped_source)


def _viewer(svg: str, escaped_source: str) -> str:
    return f"""
<div class="dot-viewer" data-deckbox-dot>
  <div class="dot-toolbar">
    <button type="button" data-act="zoom-out" title="Zoom out" aria-label="Zoom out">−</button>
    <button type="button" data-act="zoom-in" title="Zoom in" aria-label="Zoom in">+</button>
    <button type="button" data-act="fit" title="Fit to view">Fit</button>
    <button type="button" data-act="reset" title="Reset to 100%">100%</button>
    <span class="dot-spacer"></span>
    <button type="button" data-act="toggle-source" title="Toggle DOT source">&lt;/&gt; Source</button>
    <button type="button" data-act="download" title="Download SVG">Download</button>
  </div>
  <div class="dot-stage">
    <div class="dot-canvas">{svg}</div>
  </div>
  <pre class="dot-source" hidden><code>{escaped_source}</code></pre>
</div>
"""


def _fallback(message: str, escaped_source: str) -> str:
    return f"""
<div class="dot-viewer dot-fallback">
  <div class="dot-notice">{message}</div>
  <pre class="dot-source"><code>{escaped_source}</code></pre>
</div>
"""
