"""FastAPI application factory for Deckbox."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from deckbox import __version__
from deckbox.auth import PamAuthMiddleware, launch_user
from deckbox.browse import PathOutsideRoot, build_crumbs, list_directory, safe_resolve
from deckbox.config import ResolvedConfig
from deckbox.renderers import (
    MAX_INLINE_BYTES,
    file_kind,
    mode_for,
    render_inline,
)
from deckbox.renderers.text_renderer import pygments_css

_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_PKG_DIR / "templates"))

# Pygments token CSS, computed once. Served at /assets/highlight.css.
_HIGHLIGHT_CSS = pygments_css()


def create_app(cfg: ResolvedConfig, *, auth_required: bool) -> FastAPI:
    root = cfg.directory.resolve()
    app = FastAPI(title="Deckbox", version=__version__)
    app.state.launch_user = launch_user()
    app.state.auth_required = auth_required
    app.add_middleware(PamAuthMiddleware)
    app.mount("/static", StaticFiles(directory=str(_PKG_DIR / "static")), name="static")

    def resolve_or_404(path: str) -> Path:
        try:
            target = safe_resolve(root, path)
        except PathOutsideRoot as exc:
            raise HTTPException(status_code=404, detail="Not found") from exc
        if not target.exists():
            raise HTTPException(status_code=404, detail="Not found")
        return target

    def base_ctx(request: Request) -> dict:
        return {
            "request": request,
            "app_name": "Deckbox",
            "version": __version__,
            "root_name": root.name or str(root),
        }

    def render_view(request: Request, path: str) -> HTMLResponse:
        target = resolve_or_404(path)
        if target.is_dir():
            return _render_dir(request, root, target)
        return _render_file(request, base_ctx(request), root, target, path)

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    @app.get("/assets/highlight.css")
    async def highlight_css() -> Response:
        return Response(content=_HIGHLIGHT_CSS, media_type="text/css")

    @app.get("/api/dot")
    async def api_dot(path: str, engine: str = "dot", rankdir: str = "TB") -> JSONResponse:
        from deckbox.renderers.dot_renderer import (
            GraphvizNotInstalled,
            GraphvizRenderError,
            node_data,
            render_svg,
        )

        target = resolve_or_404(path)
        if target.is_dir() or file_kind(target) != "dot":
            raise HTTPException(status_code=404, detail="Not a DOT file")
        source = target.read_text(encoding="utf-8", errors="replace")
        try:
            svg = render_svg(source, engine, rankdir)
        except GraphvizNotInstalled as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        except GraphvizRenderError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse({"svg": svg, "nodes": node_data(source), "error": None})

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return render_view(request, "")

    @app.get("/view/{path:path}", response_class=HTMLResponse)
    async def view(request: Request, path: str) -> HTMLResponse:
        return render_view(request, path)

    @app.get("/raw/{path:path}")
    async def raw(path: str) -> FileResponse:
        target = resolve_or_404(path)
        if target.is_dir():
            raise HTTPException(status_code=404, detail="Not found")
        media_type, _ = mimetypes.guess_type(target.name)
        return FileResponse(
            target,
            media_type=media_type or "application/octet-stream",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get("/download/{path:path}")
    async def download(path: str) -> FileResponse:
        target = resolve_or_404(path)
        if target.is_dir():
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(
            target,
            media_type="application/octet-stream",
            filename=target.name,
        )

    def _render_dir(request: Request, root: Path, target: Path) -> HTMLResponse:
        listing = list_directory(root, target)
        readme_html = _maybe_readme(target)
        ctx = base_ctx(request)
        ctx.update(
            {
                "listing": listing,
                "crumbs": listing.crumbs,
                "title": target.name or (root.name or "Home"),
                "readme_html": readme_html,
            }
        )
        return _TEMPLATES.TemplateResponse(request=request, name="browse.html", context=ctx)

    def _render_file(
        request: Request, ctx: dict, root: Path, target: Path, path: str
    ) -> HTMLResponse:
        kind = file_kind(target)
        mode = mode_for(kind)
        rel = str(target.relative_to(root))
        size = target.stat().st_size
        ctx.update(
            {
                "title": target.name,
                "crumbs": build_crumbs(rel),
                "kind": kind,
                "mode": mode,
                "rel": rel,
                "parent_rel": str(Path(rel).parent) if Path(rel).parent != Path(".") else "",
                "size": size,
                "raw_url": f"/raw/{path}",
                "download_url": f"/download/{path}",
                "content_html": None,
                "render_error": None,
            }
        )

        if mode == "inline":
            if size > MAX_INLINE_BYTES:
                ctx["mode"] = "download"
                ctx["render_error"] = "File is too large to preview."
            else:
                try:
                    ctx["content_html"] = render_inline(target, kind, src_path=path)
                except Exception as exc:  # noqa: BLE001 - surface any renderer failure
                    ctx["mode"] = "download"
                    ctx["render_error"] = f"Could not render this file: {exc}"

        return _TEMPLATES.TemplateResponse(request=request, name="view.html", context=ctx)

    return app


def _maybe_readme(directory: Path) -> str | None:
    for name in ("README.md", "readme.md", "Readme.md", "README.markdown"):
        candidate = directory / name
        if candidate.is_file():
            try:
                from deckbox.renderers.markdown_renderer import render as render_md

                return render_md(candidate)
            except Exception:  # noqa: BLE001
                return None
    return None
