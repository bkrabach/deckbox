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
    "-Gbgcolor=transparent",
    "-Gfontname=Helvetica",
    "-Gfontsize=12",
    "-Gpad=0.35",
    "-Gnodesep=0.35",
    "-Granksep=0.55",
    # node (-N)
    "-Nfontname=Helvetica",
    "-Nfontsize=12",
    "-Nshape=box",
    "-Nstyle=rounded,filled",
    "-Nfillcolor=#eef2f8",
    "-Ncolor=#c2ccdb",
    "-Nfontcolor=#1f2733",
    "-Npenwidth=1.0",
    "-Nmargin=0.22,0.14",
    # edge (-E)
    "-Efontname=Helvetica",
    "-Efontsize=11",
    "-Ecolor=#9aa4b5",
    "-Earrowsize=0.75",
    "-Epenwidth=1.1",
]

_FONT_FAMILY_RE = re.compile(r'font-family="[^"]*"')
_SVG_OPEN_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)


class GraphvizNotInstalled(RuntimeError):
    """Raised when the `dot` binary is not on PATH."""


class GraphvizRenderError(RuntimeError):
    """Raised when `dot` exits non-zero (invalid graph, etc.)."""


def graphviz_available() -> bool:
    return shutil.which("dot") is not None


# Layout engines we expose in the viewer. dot is hierarchical (respects
# rankdir); the others ignore rankdir and use their own strategies.
ENGINES = ("dot", "neato", "fdp", "sfdp", "circo", "twopi")
DEFAULT_ENGINE = "dot"

# rankdir choices (only meaningful for the dot engine). TB = vertical default.
RANKDIRS = ("TB", "LR", "BT", "RL")
DEFAULT_RANKDIR = "TB"


def _run(args: list[str], source: str) -> str:
    if not graphviz_available():
        raise GraphvizNotInstalled("the 'dot' binary (graphviz) is not installed")
    try:
        proc = subprocess.run(
            args,
            input=source,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GraphvizRenderError("graph rendering timed out") from exc
    if proc.returncode != 0:
        raise GraphvizRenderError(proc.stderr.strip() or "dot failed")
    return proc.stdout


def _root_close_index(source: str) -> int:
    """Index of the root graph body's closing brace, skipping braces that live
    inside // or # line comments, /* */ block comments, or "..." strings.

    Returns -1 if a matching top-level brace can't be located.
    """
    depth = 0
    opened = False
    i = 0
    n = len(source)
    in_line = in_block = in_str = escape = False
    while i < n:
        c = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if in_line:
            if c == "\n":
                in_line = False
        elif in_block:
            if c == "*" and nxt == "/":
                in_block = False
                i += 1
        elif in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
        elif c == "/" and nxt == "/":
            in_line = True
            i += 1
        elif c == "#":
            in_line = True
        elif c == "/" and nxt == "*":
            in_block = True
            i += 1
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
            opened = True
        elif c == "}":
            depth -= 1
            if opened and depth == 0:
                return i
        i += 1
    return -1


def _force_rankdir(source: str, rankdir: str) -> str:
    """Append a rankdir override as the last statement of the root graph so it
    wins over any rankdir the file sets (last assignment wins in Graphviz).

    Safe against braces inside comments/strings via _root_close_index.
    """
    if rankdir not in RANKDIRS:
        return source
    idx = _root_close_index(source)
    if idx < 0:
        return source
    return f'{source[:idx]}\n  rankdir="{rankdir}";\n{source[idx:]}'


def _normalise(engine: str | None, rankdir: str | None) -> tuple[str, str]:
    eng = engine if engine in ENGINES else DEFAULT_ENGINE
    rd = rankdir if rankdir in RANKDIRS else DEFAULT_RANKDIR
    return eng, rd


def render_svg(source: str, engine: str | None = None, rankdir: str | None = None) -> str:
    """Render restyled SVG for a given engine and (dot-only) rank direction."""
    eng, rd = _normalise(engine, rankdir)
    prepared = _force_rankdir(source, rd) if eng == "dot" else source
    args = ["dot", f"-K{eng}", *_THEME_ARGS, "-Tsvg"]
    return _restyle_svg(_run(args, prepared))


# Graphviz-internal / geometry attributes we never show in the inspector.
_HIDDEN_ATTRS = frozenset(
    {
        "pos",
        "width",
        "height",
        "rects",
        "lp",
        "bb",
        "tooltip",
        # visual defaults we inject as a theme — not authored content
        "fontname",
        "fontsize",
        "fillcolor",
        "color",
        "fontcolor",
        "penwidth",
        "margin",
        "style",
    }
)


def node_data(source: str) -> dict[str, dict[str, str]]:
    """Map node name -> authored attributes, via Graphviz's own JSON output.

    Using `dot -Tjson` means Graphviz's parser resolves the attributes (so we
    never hand-parse DOT), and it correctly handles comments, quoting, and
    escapes. Returns {} on any failure — the inspector degrades gracefully.
    """
    import json

    try:
        raw = _run(["dot", "-Tjson"], source)
        data = json.loads(raw)
    except (GraphvizNotInstalled, GraphvizRenderError, json.JSONDecodeError, ValueError):
        return {}

    result: dict[str, dict[str, str]] = {}
    for obj in data.get("objects", []):
        name = obj.get("name")
        if not name or name in ("node", "edge", "graph"):
            continue
        attrs: dict[str, str] = {}
        for key, value in obj.items():
            if key in ("name", "_gvid") or key.startswith("_"):
                continue
            if key in _HIDDEN_ATTRS:
                continue
            attrs[key] = value if isinstance(value, str) else str(value)
        result[name] = attrs
    return result


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


def render(path: Path, *, src_path: str = "") -> str:
    """Render the interactive DOT viewer.

    ``src_path`` is the URL path (under /view) used by the client to re-render
    with a different engine/direction via /api/dot. When empty, layout controls
    still render but re-layout requests are disabled.
    """
    import json

    source = path.read_text(encoding="utf-8", errors="replace")
    escaped_source = html.escape(source)
    try:
        svg = render_svg(source, DEFAULT_ENGINE, DEFAULT_RANKDIR)
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
    nodes_json = html.escape(json.dumps(node_data(source)), quote=True)
    return _viewer(svg, escaped_source, src_path=src_path, nodes_json=nodes_json)


def _engine_options() -> str:
    labels = {
        "dot": "Hierarchical (dot)",
        "neato": "Spring (neato)",
        "fdp": "Force (fdp)",
        "sfdp": "Force · large (sfdp)",
        "circo": "Circular (circo)",
        "twopi": "Radial (twopi)",
    }
    return "".join(
        f'<option value="{e}"{" selected" if e == DEFAULT_ENGINE else ""}>{labels[e]}</option>'
        for e in ENGINES
    )


def _rankdir_options() -> str:
    labels = {"TB": "Vertical ↓", "LR": "Horizontal →", "BT": "Vertical ↑", "RL": "Horizontal ←"}
    return "".join(
        f'<option value="{r}"{" selected" if r == DEFAULT_RANKDIR else ""}>{labels[r]}</option>'
        for r in RANKDIRS
    )


def _viewer(svg: str, escaped_source: str, *, src_path: str, nodes_json: str) -> str:
    return f"""
<div class="dot-viewer" data-deckbox-dot data-src="{html.escape(src_path, quote=True)}"
     data-nodes="{nodes_json}" data-engine="{DEFAULT_ENGINE}" data-rankdir="{DEFAULT_RANKDIR}">
  <div class="dot-toolbar">
    <label class="dot-field">
      <span>Layout</span>
      <select data-ctl="engine">{_engine_options()}</select>
    </label>
    <label class="dot-field" data-ctl-wrap="rankdir">
      <span>Direction</span>
      <select data-ctl="rankdir">{_rankdir_options()}</select>
    </label>
    <span class="dot-spacer"></span>
    <span class="dot-loading" hidden>rendering…</span>
    <button type="button" data-act="zoom-out" title="Zoom out" aria-label="Zoom out">−</button>
    <button type="button" data-act="zoom-in" title="Zoom in" aria-label="Zoom in">+</button>
    <button type="button" data-act="fit" title="Fit to view">Fit</button>
    <button type="button" data-act="reset" title="Reset to 100%">100%</button>
    <button type="button" data-act="toggle-source" title="Toggle DOT source">&lt;/&gt; Source</button>
    <button type="button" data-act="copy-png" title="Copy diagram as PNG">Copy PNG</button>
    <button type="button" data-act="download-png" title="Download diagram as PNG">PNG</button>
    <button type="button" data-act="download" title="Download SVG">SVG</button>
  </div>
  <div class="dot-body">
    <div class="dot-stage">
      <div class="dot-canvas">{svg}</div>
    </div>
    <aside class="dot-inspector" hidden aria-label="Node inspector">
      <div class="dot-inspector-head">
        <span class="dot-inspector-title">Node</span>
        <button type="button" class="dot-inspector-close" data-act="close-inspector" aria-label="Close">×</button>
      </div>
      <div class="dot-inspector-body"></div>
      <p class="dot-inspector-hint">Click a node to inspect it.</p>
    </aside>
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
