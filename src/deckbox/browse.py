"""Directory browsing helpers: safe path resolution and listing."""

from __future__ import annotations

import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from deckbox.renderers import file_kind


class PathOutsideRoot(Exception):
    """Raised when a requested path escapes the served root."""


def url_path(p: Path) -> str:
    """The /view URL segment for a filesystem path: absolute, leading slash
    stripped. e.g. Path('/home/u/notes') -> 'home/u/notes'. The route prepends
    the slash back. The empty string denotes filesystem root '/'."""
    return str(Path(p).resolve()).lstrip("/")


def safe_resolve(root: Path, path: str, *, allow_outside: bool = False) -> Path:
    """Resolve a /view URL path segment to an absolute filesystem path.

    ``path`` is an absolute filesystem path with the leading slash removed (see
    ``url_path``); the empty string means filesystem root '/'. When
    ``allow_outside`` is False the result must be the served root or a descendant
    of it, else PathOutsideRoot — and ``.resolve()`` collapses any '..' and
    follows symlinks first, so neither can be used to escape.
    """
    root = root.resolve()
    candidate = Path("/" + path.lstrip("/")).resolve()
    if allow_outside:
        return candidate
    if candidate == root or root in candidate.parents:
        return candidate
    raise PathOutsideRoot(path)


@dataclass
class Entry:
    name: str
    rel: str
    is_dir: bool
    kind: str
    size: int
    mtime: float

    @property
    def size_human(self) -> str:
        return human_size(self.size)

    @property
    def mtime_human(self) -> str:
        return datetime.fromtimestamp(self.mtime).astimezone().strftime("%Y-%m-%d %H:%M")

    @property
    def letter(self) -> str:
        """First-character bucket for the alphabet jump nav: A-Z, or '#'.

        Bucketed by the FIRST character only, so a name like ``_planning`` (which
        sorts to the top of the list) lands in the '#' bucket at the start of the
        rail — clicking 'P' must not jump to it. Only an ASCII A-Z leading char
        counts as a letter; digits, underscores, dots, and accented leads -> '#'.
        """
        ch = self.name[:1]
        if ch.isascii() and ch.isalpha():
            return ch.upper()
        return "#"


@dataclass
class Crumb:
    name: str
    rel: str


@dataclass
class Listing:
    rel: str
    crumbs: list[Crumb]
    entries: list[Entry] = field(default_factory=list)


def human_size(num: int) -> str:
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def build_crumbs(target: Path, root: Path) -> list[Crumb]:
    """Breadcrumbs from Home (the served root) to ``target``.

    Home always links to the launch root (its ``rel`` is "" and the template
    maps that to "/"). Within the root, the remaining crumbs are the path
    components relative to root. Outside the root (only reachable when
    allow_outside_root is on), they are the absolute path components so every
    ancestor is clickable. Each crumb ``rel`` is a /view URL segment.
    """
    target = target.resolve()
    root = root.resolve()
    crumbs = [Crumb(name="Home", rel="")]  # template maps "" -> "/"
    try:
        parts = target.relative_to(root).parts
        acc = root
    except ValueError:
        # target is not under root — build an absolute breadcrumb from '/'.
        acc = Path("/")
        for part in target.parts[1:]:
            acc = acc / part
            crumbs.append(Crumb(name=part, rel=url_path(acc)))
        return crumbs
    for part in parts:
        acc = acc / part
        crumbs.append(Crumb(name=part, rel=url_path(acc)))
    return crumbs


def list_directory(root: Path, target: Path) -> Listing:
    """Build a sorted listing (directories first, then files, name-insensitive)."""
    root = root.resolve()
    target = target.resolve()
    entries: list[Entry] = []
    try:
        raw = list(target.iterdir())
    except (PermissionError, OSError):
        raw = []

    for child in raw:
        # Show everything, including dotfiles/dotfolders (.gitignore,
        # .amplifier/settings.yaml, etc.) — those are frequently the files that
        # matter. iterdir() already excludes the . and .. entries.
        try:
            st = child.stat()
        except (OSError, ValueError):
            continue
        is_dir = stat.S_ISDIR(st.st_mode)
        entries.append(
            Entry(
                name=child.name,
                rel=url_path(target / child.name),
                is_dir=is_dir,
                kind="folder" if is_dir else file_kind(child),
                size=0 if is_dir else st.st_size,
                mtime=st.st_mtime,
            )
        )

    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return Listing(rel=url_path(target), crumbs=build_crumbs(target, root), entries=entries)
