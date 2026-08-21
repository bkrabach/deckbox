"""Elegant GraphViz DOT rendering.

Strategy for a nicer result than raw ``dot -Tsvg``:

  1. Supply tasteful *default* graph/node/edge attributes as Graphviz
     command-line flags (-G/-N/-E). Graphviz applies these before reading the
     file, so the author's explicit attributes always win — and, crucially,
     this can never corrupt the source the way text injection could (a brace in
     a comment or string used to break the old "insert after the first {"
     approach).
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

# Theme defaults, passed as `dot` command-line flags. These set the initial
# graph/node/edge defaults; any attribute the file sets explicitly overrides
# them, so author styling is always respected.
_THEME_ARGS = [
    # graph (-G)
    "-Gbgcolor=transparent", "-Gfontname=Helvetica", "-Gfontsize=12",
    "-Gpad=0.35", "-Gnodesep=0.35", "-Granksep=0.55",
    # node (-N)
    "-Nfontname=Helvetica", "-Nfontsize=12", "-Nshape=box",
    "-Nstyle=rounded,filled", "-Nfillcolor=#eef2f8", "-Ncolor=#c2ccdb",
    "-Nfontcolor=#1f2733", "-Npenwidth=1.0", "-Nmargin=0.22,0.14",
    # edge (-E)
    "-Efontname=Helvetica", "-Efontsize=11", "-Ecolor=#9aa4b5",
    "-Earrowsize=0.75", "-Epenwidth=1.1",
]

_FONT_FAMILY_RE = re.compile(r'font-family="[^"]*"')
_SVG_OPEN_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)


class GraphvizNotInstalled(RuntimeError):
    """Raised when the `dot` binary is not on PATH."""


class GraphvizRenderError(RuntimeError):
    """Raised when `dot` exits non-zero (invalid graph, etc.)."""


def graphviz_available() -> bool:
    return shutil.which("dot") is not None


def _run_dot(source: str) -> str:
    if not graphviz_available():
        raise GraphvizNotInstalled("the 'dot' binary (graphviz) is not installed")
    try:
        proc = subprocess.run(
            ["dot", *_THEME_ARGS, "-Tsvg"],
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

    # Tag the root <svg> so the viewer can find it. We intentionally KEEP the
    # intrinsic width/height that Graphviz emits: the SVG is placed inside an
    # absolutely-positioned canvas and zoom/pan is applied via a CSS transform,
    # so the element needs a real intrinsic size or it collapses to nothing.
    def _tag(match: re.Match) -> str:
        return match.group(0).replace("<svg", '<svg class="dot-svg"', 1)

    return _SVG_OPEN_RE.sub(_tag, svg, count=1)


def render(path: Path) -> str:
    source = path.read_text(encoding="utf-8", errors="replace")
    escaped_source = html.escape(source)
    try:
        svg = _restyle_svg(_run_dot(source))
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
