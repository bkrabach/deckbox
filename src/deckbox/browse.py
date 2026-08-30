"""Directory browsing helpers: safe path resolution and listing."""

from __future__ import annotations

import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from deckbox.renderers import file_kind


class PathOutsideRoot(Exception):
    """Raised when a requested path escapes the served root."""


def safe_resolve(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root``, refusing any escape via .. or symlinks."""
    root = root.resolve()
    candidate = (root / rel.lstrip("/")).resolve()
    if candidate != root and root not in candidate.parents:
        raise PathOutsideRoot(rel)
    return candidate


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


def build_crumbs(rel: str) -> list[Crumb]:
    crumbs = [Crumb(name="Home", rel="")]
    parts = [p for p in rel.strip("/").split("/") if p]
    acc = ""
    for part in parts:
        acc = f"{acc}/{part}" if acc else part
        crumbs.append(Crumb(name=part, rel=acc))
    return crumbs


def list_directory(root: Path, target: Path) -> Listing:
    """Build a sorted listing (directories first, then files, name-insensitive)."""
    rel = "" if target == root.resolve() else str(target.relative_to(root.resolve()))
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
        child_rel = f"{rel}/{child.name}" if rel else child.name
        entries.append(
            Entry(
                name=child.name,
                rel=child_rel,
                is_dir=is_dir,
                kind="folder" if is_dir else file_kind(child),
                size=0 if is_dir else st.st_size,
                mtime=st.st_mtime,
            )
        )

    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return Listing(rel=rel, crumbs=build_crumbs(rel), entries=entries)
