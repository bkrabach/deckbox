"""Renderer registry and dispatch.

A *kind* is a short string describing how a file should be presented. The
server maps a kind to a *mode* that the template understands:

    inline   -> server-rendered HTML injected into the page (md, json, code, docx, dot)
    iframe   -> served raw inside a sandboxed iframe (pdf, html)
    image    -> shown with an <img> tag (png, svg, ...)
    download -> no preview; offer a download button
"""

from __future__ import annotations

from pathlib import Path

# extension (lower, without dot) -> kind
_EXT_KIND: dict[str, str] = {
    # markup / docs
    "md": "markdown",
    "markdown": "markdown",
    "mdown": "markdown",
    "json": "json",
    "dot": "dot",
    "gv": "dot",
    "pdf": "pdf",
    "html": "html",
    "htm": "html",
    "docx": "docx",
    # images
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "gif": "image",
    "webp": "image",
    "svg": "image",
    "bmp": "image",
    "ico": "image",
    "avif": "image",
}

# extensions that render as syntax-highlighted text
_CODE_EXT = {
    "py",
    "js",
    "mjs",
    "cjs",
    "ts",
    "tsx",
    "jsx",
    "css",
    "scss",
    "less",
    "yaml",
    "yml",
    "toml",
    "ini",
    "cfg",
    "conf",
    "sh",
    "bash",
    "zsh",
    "fish",
    "rs",
    "go",
    "c",
    "h",
    "cpp",
    "hpp",
    "cc",
    "java",
    "kt",
    "rb",
    "php",
    "sql",
    "xml",
    "txt",
    "text",
    "log",
    "csv",
    "tsv",
    "env",
    "rst",
    "make",
    "mk",
    "dockerfile",
    "gitignore",
    "lua",
    "r",
    "swift",
    "pl",
}

KIND_MODE: dict[str, str] = {
    "markdown": "inline",
    "json": "inline",
    "code": "inline",
    "docx": "inline",
    "dot": "inline",
    "pdf": "iframe",
    "html": "iframe",
    "image": "image",
    "download": "download",
}

# kinds rendered to HTML on the server
INLINE_KINDS = frozenset({"markdown", "json", "code", "docx", "dot"})

# Files larger than this are not rendered inline (offered as download instead).
MAX_INLINE_BYTES = 8 * 1024 * 1024


def file_kind(path: Path) -> str:
    """Classify a file into a rendering kind by its name/extension."""
    ext = path.suffix.lower().lstrip(".")
    if not ext:
        # extensionless well-known files
        if path.name.lower() in {"readme", "license", "makefile", "dockerfile"}:
            return "code"
        return "download"
    if ext in _EXT_KIND:
        return _EXT_KIND[ext]
    if ext in _CODE_EXT:
        return "code"
    return "download"


def mode_for(kind: str) -> str:
    return KIND_MODE.get(kind, "download")


def render_inline(path: Path, kind: str, *, src_path: str = "") -> str:
    """Render an inline kind to an HTML fragment. Raises on unknown kind.

    ``src_path`` is the file's URL path (under /view); only the DOT renderer
    uses it, to enable client-side re-layout via /api/dot.
    """
    if kind == "markdown":
        from deckbox.renderers.markdown_renderer import render as render_md

        return render_md(path)
    if kind == "json":
        from deckbox.renderers.text_renderer import render_json

        return render_json(path)
    if kind == "code":
        from deckbox.renderers.text_renderer import render_code

        return render_code(path)
    if kind == "docx":
        from deckbox.renderers.docx_renderer import render as render_docx

        return render_docx(path)
    if kind == "dot":
        from deckbox.renderers.dot_renderer import render as render_dot

        return render_dot(path, src_path=src_path)
    raise ValueError(f"not an inline kind: {kind}")
