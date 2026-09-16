"""Safe, bounded, streaming ZIP packet construction for Deckbox."""

from __future__ import annotations

import io
import os
import queue
import threading
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from typing_extensions import Buffer


class ArchiveError(ValueError):
    """A selection cannot safely be turned into a ZIP archive."""


@dataclass(frozen=True)
class ArchiveLimits:
    """Preflight bounds for one ZIP packet."""

    max_members: int = 10_000
    max_source_bytes: int = 1 << 30  # 1 GiB before compression


DEFAULT_LIMITS = ArchiveLimits()


@dataclass(frozen=True)
class ArchiveMember:
    """One file or explicit directory entry in a packet."""

    source: Path
    arcname: str
    is_dir: bool


@dataclass(frozen=True)
class ArchivePlan:
    """Preflighted members and the server-derived attachment name."""

    filename: str
    members: tuple[ArchiveMember, ...]


def _is_within(path: Path, scope: Path | None) -> bool:
    return scope is None or path == scope or scope in path.parents


def _checked_path(path: Path, scope: Path | None) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ArchiveError(f"Cannot read selected path: {path}") from exc
    if not _is_within(resolved, scope):
        raise ArchiveError(f"Selected path is outside the permitted scope: {path}")
    return resolved


def _packet_name(selection: Sequence[Path]) -> str:
    if len(selection) == 1 and selection[0].is_dir():
        return f"{selection[0].name or 'folder'}.zip"
    return f"deckbox-{len(selection)}-items.zip"


def plan_archive(
    selection: Sequence[Path],
    *,
    scope: Path | None,
    limits: ArchiveLimits = DEFAULT_LIMITS,
) -> ArchivePlan:
    """Preflight selected paths and return a deterministic ZIP member plan.

    ``scope=None`` permits any readable path (the app's allow-outside-root
    setting); otherwise every selected item and recursive member must remain
    below the resolved scope. The archive never silently skips an unsafe
    symlink: it fails before any response bytes are sent instead.
    """
    if not selection:
        raise ArchiveError("Select at least one file or folder.")
    if limits.max_members < 1 or limits.max_source_bytes < 0:
        raise ValueError("Archive limits must be positive.")

    permitted_scope = scope.resolve() if scope is not None else None
    selected: list[Path] = []
    seen: set[Path] = set()
    for path in selection:
        resolved = _checked_path(path, permitted_scope)
        if not (resolved.is_file() or resolved.is_dir()):
            raise ArchiveError(f"Selected path is not a regular file or folder: {path}")
        if resolved not in seen:
            seen.add(resolved)
            selected.append(resolved)

    members: list[ArchiveMember] = []
    member_names: set[str] = set()
    source_bytes = 0

    def add(source: Path, arcname: str, *, is_dir: bool) -> None:
        nonlocal source_bytes
        if arcname in member_names:
            raise ArchiveError(f"Duplicate archive member: {arcname}")
        member_names.add(arcname)
        if len(members) >= limits.max_members:
            raise ArchiveError(
                f"Archive member limit exceeded ({limits.max_members:,} members maximum)."
            )
        if not is_dir:
            try:
                source_bytes += source.stat().st_size
            except OSError as exc:
                raise ArchiveError(f"Cannot read selected file: {source}") from exc
            if source_bytes > limits.max_source_bytes:
                raise ArchiveError(
                    f"Archive size limit exceeded ({limits.max_source_bytes:,} source bytes maximum)."
                )
        members.append(ArchiveMember(source=source, arcname=arcname, is_dir=is_dir))

    for selected_path in selected:
        top = selected_path.name or "folder"
        if selected_path.is_file():
            add(selected_path, top, is_dir=False)
            continue

        # Explicit directory entries preserve empty folders in the ZIP.
        for current_raw, dirs, files in os.walk(selected_path, followlinks=False):
            current = _checked_path(Path(current_raw), permitted_scope)
            relative = current.relative_to(selected_path)
            prefix = top if relative == Path(".") else f"{top}/{relative.as_posix()}"
            add(current, f"{prefix}/", is_dir=True)

            dirs.sort()
            files.sort()
            for name in [*dirs, *files]:
                candidate = Path(current_raw) / name
                # Never omit a symlink quietly; resolving validates that it has
                # not escaped the permitted scope, then fail explicitly because
                # ZIP link semantics differ across extractors.
                if candidate.is_symlink():
                    _checked_path(candidate, permitted_scope)
                    raise ArchiveError(f"Symlinks are not supported in ZIP packets: {candidate}")

            for name in files:
                source = _checked_path(Path(current_raw) / name, permitted_scope)
                if not source.is_file():
                    raise ArchiveError(f"Archive member is not a regular file: {source}")
                add(source, f"{prefix}/{name}", is_dir=False)

    return ArchivePlan(
        filename=_packet_name(selected),
        members=tuple(members),
    )


class _ZipQueueWriter(io.RawIOBase):
    """A non-seekable file object that feeds ZIP bytes to a queue."""

    def __init__(
        self,
        output: queue.Queue[bytes | BaseException | None],
        stopped: threading.Event,
    ):
        self._output = output
        self._stopped = stopped
        self._position = 0

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._position

    def write(self, data: Buffer, /) -> int:
        if self._stopped.is_set():
            raise BrokenPipeError("ZIP consumer stopped")
        payload = bytes(data)
        while not self._stopped.is_set():
            try:
                self._output.put(payload, timeout=0.1)
                self._position += len(payload)
                return len(payload)
            except queue.Full:
                continue
        raise BrokenPipeError("ZIP consumer stopped")


def iter_zip(plan: ArchivePlan) -> Iterator[bytes]:
    """Yield a preflighted ZIP packet without creating a temporary archive."""
    output: queue.Queue[bytes | BaseException | None] = queue.Queue(maxsize=16)
    stopped = threading.Event()

    def put(item: bytes | BaseException | None) -> None:
        while not stopped.is_set():
            try:
                output.put(item, timeout=0.1)
                return
            except queue.Full:
                continue

    def produce() -> None:
        try:
            writer = _ZipQueueWriter(output, stopped)
            with zipfile.ZipFile(writer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                for member in plan.members:
                    if member.is_dir:
                        archive.writestr(member.arcname, b"")
                    else:
                        archive.write(member.source, member.arcname)
        except BaseException as exc:  # surfaced to the response iterator
            put(exc)
        finally:
            put(None)

    worker = threading.Thread(target=produce, name="deckbox-zip", daemon=True)
    worker.start()
    try:
        while True:
            item = output.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stopped.set()
        worker.join(timeout=1)
