"""Efficient, lazy viewing of large JSON / JSONL / NDJSON files.

The whole design keeps the *file on the server* and only ever sends the browser
small previews plus lazily-fetched slices:

  * A per-line byte index (cached by path+mtime+size) lets us read row N without
    touching the rest of a 20 MB file.
  * Row previews carry only top-level shape + a few truncated scalar fields.
  * Node drill-down returns ONE level at a JSON Pointer, inlining small children
    and returning previews + an "expandable" handle for anything that exceeds the
    thresholds (child count, depth, payload size, string length).
  * Search substring-scans raw lines (no JSON parse) and returns matching rows.

Two backends share one interface:
  * JSONL / NDJSON  -> one row per non-empty line (lazy per-row parse).
  * JSON            -> a single parsed document; a top-level array becomes rows,
                       anything else is a single root row.
"""

from __future__ import annotations

import abc
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# --- Thresholds ------------------------------------------------------------
@dataclass(frozen=True)
class Thresholds:
    inline_children: int = 50  # inline a container only if <= this many children
    inline_depth: int = 2  # how many levels to inline in one response
    node_bytes: int = 16_384  # don't inline a subtree larger (est.) than this
    string_preview: int = 300  # truncate string previews to this many chars
    row_preview_fields: int = 12  # top-level scalar fields carried per row preview
    row_field_chars: int = 120  # truncate each row-preview field value
    max_columns: int = 24  # suggested table columns
    column_sample: int = 200  # rows sampled to detect homogeneous columns
    search_scan_cap: int = 500_000  # max rows scanned per search
    search_match_cap: int = 5_000  # max match line numbers returned


DEFAULT = Thresholds()

# Files larger than this are refused for the whole-document JSON backend
# (JSONL streams line-by-line and is not subject to this).
MAX_JSON_BYTES = 128 * 1024 * 1024


# --- JSON Pointer (RFC 6901) ----------------------------------------------
def _unescape_token(tok: str) -> str:
    return tok.replace("~1", "/").replace("~0", "~")


def _escape_token(tok: str) -> str:
    return tok.replace("~", "~0").replace("/", "~1")


def pointer_tokens(pointer: str) -> list[str]:
    if not pointer:
        return []
    if not pointer.startswith("/"):
        pointer = "/" + pointer
    return [_unescape_token(t) for t in pointer.split("/")[1:]]


def resolve_pointer(value: Any, pointer: str) -> Any:
    for tok in pointer_tokens(pointer):
        if isinstance(value, dict):
            value = value[tok]
        elif isinstance(value, list):
            value = value[int(tok)]
        else:
            raise KeyError(pointer)
    return value


# --- Cheap size estimator (early-exit) ------------------------------------
def _exceeds(value: Any, limit: int) -> bool:
    """True if the serialized size of *value* is (roughly) over *limit*.

    Early-exits as soon as the running estimate passes the limit, so it never
    walks a whole 5 MB subtree just to decide it is too big.
    """
    total = 0
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            total += len(v) + 2
        elif isinstance(v, bool):
            total += 5
        elif v is None:
            total += 4
        elif isinstance(v, (int, float)):
            total += len(str(v))
        elif isinstance(v, dict):
            total += 2 + len(v)
            for k, val in v.items():
                total += len(str(k)) + 3
                stack.append(val)
        elif isinstance(v, list):
            total += 2 + len(v)
            stack.extend(v)
        if total > limit:
            return True
    return False


# --- Node descriptors ------------------------------------------------------
def _type_of(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "unknown"


def _scalar_node(value: Any, pointer: str, key: Any, th: Thresholds) -> dict:
    t = _type_of(value)
    node: dict[str, Any] = {"type": t, "pointer": pointer}
    if key is not None:
        node["key"] = key
    if t == "string":
        s = value
        node["value"] = s[: th.string_preview]
        node["truncated"] = len(s) > th.string_preview
        node["len"] = len(s)
    else:
        node["value"] = value
    return node


def _container_preview(value: Any, th: Thresholds) -> str:
    if isinstance(value, dict):
        keys = list(value.keys())
        head = ", ".join(str(k) for k in keys[:5])
        more = f", +{len(keys) - 5}" if len(keys) > 5 else ""
        return f"{{{head}{more}}}" if keys else "{}"
    n = len(value)
    return f"[{n} item{'s' if n != 1 else ''}]"


def _stub(value: Any, pointer: str, key: Any, th: Thresholds) -> dict:
    """A single-level, non-recursive descriptor: enough to render one collapsed
    row per child without shipping the child's own subtree."""
    t = _type_of(value)
    if t in ("object", "array"):
        return {
            "type": t,
            "pointer": pointer,
            "key": key,
            "size": len(value),
            "preview": _container_preview(value, th),
            "children": None,
            "expandable": len(value) > 0,
        }
    return _scalar_node(value, pointer, key, th)


def _child_stubs(value: Any, pointer: str, th: Thresholds, cap: int = 200) -> dict:
    """Bounded list of child stubs for a container we declined to inline."""
    stubs: list[dict] = []
    if isinstance(value, dict):
        for i, (k, v) in enumerate(value.items()):
            if i >= cap:
                break
            stubs.append(_stub(v, f"{pointer}/{_escape_token(str(k))}", k, th))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            if i >= cap:
                break
            stubs.append(_stub(v, f"{pointer}/{i}", i, th))
    return {"items": stubs, "shown": len(stubs), "total": len(value)}


def describe_node(
    value: Any, pointer: str, th: Thresholds, key: Any = None, depth: int | None = None
) -> dict:
    """Describe one node, inlining children up to the thresholds."""
    if depth is None:
        depth = th.inline_depth
    t = _type_of(value)
    if t not in ("object", "array"):
        return _scalar_node(value, pointer, key, th)

    size = len(value)
    node: dict[str, Any] = {
        "type": t,
        "pointer": pointer,
        "size": size,
        "preview": _container_preview(value, th),
    }
    if key is not None:
        node["key"] = key

    too_many = size > th.inline_children
    too_big = _exceeds(value, th.node_bytes)
    if depth <= 0 or too_many or too_big:
        # Don't inline the subtree, but still hand back lightweight child stubs
        # (key/index + pointer + type + preview) so the client can render one
        # collapsed row per child, each independently drillable. Cap how many
        # stubs we emit so a million-element array stays bounded.
        node["children"] = None
        node["expandable"] = size > 0
        node["child_stubs"] = _child_stubs(value, pointer, th)
        node["stub_total"] = size
        return node

    children: list[dict] = []
    if t == "object":
        for k, v in value.items():
            child_ptr = f"{pointer}/{_escape_token(str(k))}"
            children.append(describe_node(v, child_ptr, th, key=k, depth=depth - 1))
    else:
        for i, v in enumerate(value):
            child_ptr = f"{pointer}/{i}"
            children.append(describe_node(v, child_ptr, th, key=i, depth=depth - 1))
    node["children"] = children
    node["expandable"] = size > 0
    return node


def row_preview_fields(value: Any, th: Thresholds) -> dict[str, Any] | None:
    """Top-level scalar fields for an object row (powers list + table view)."""
    if not isinstance(value, dict):
        return None
    fields: dict[str, Any] = {}
    for k, v in list(value.items())[: th.row_preview_fields]:
        t = _type_of(v)
        if t in ("object", "array"):
            fields[str(k)] = _container_preview(v, th)
        elif t == "string":
            fields[str(k)] = v[: th.row_field_chars] + ("…" if len(v) > th.row_field_chars else "")
        else:
            fields[str(k)] = v
    return fields


def make_row_preview(
    value: Any, line: int, size: int, th: Thresholds, error: str | None = None
) -> dict:
    if error is not None:
        return {"line": line, "size": size, "type": "error", "preview": error, "expandable": False}
    t = _type_of(value)
    prev: dict[str, Any] = {"line": line, "size": size, "type": t}
    if t in ("object", "array"):
        prev["preview"] = _container_preview(value, th)
        prev["count"] = len(value)
        prev["expandable"] = len(value) > 0
        fields = row_preview_fields(value, th)
        if fields is not None:
            prev["fields"] = fields
    else:
        scalar = _scalar_node(value, "", None, th)
        prev["preview"] = _short_scalar(scalar)
        prev["expandable"] = False
    return prev


def _short_scalar(scalar: dict) -> str:
    t = scalar["type"]
    if t == "string":
        return '"' + str(scalar["value"]) + ('…"' if scalar.get("truncated") else '"')
    if t == "null":
        return "null"
    return str(scalar["value"])


# --- Backends --------------------------------------------------------------
class _Source(abc.ABC):
    """Common interface. Subclasses implement count/_value/_raw."""

    def __init__(self, path: Path, th: Thresholds):
        self.path = path
        self.th = th

    @abc.abstractmethod
    def count(self) -> int: ...

    @abc.abstractmethod
    def _value(self, line: int) -> tuple[Any, int, str | None]: ...

    @abc.abstractmethod
    def _raw(self, line: int) -> str: ...

    # -- public operations --
    def rows(self, offset: int, limit: int) -> list[dict]:
        total = self.count()
        out = []
        for i in range(offset, min(offset + limit, total)):
            value, size, err = self._value(i)
            out.append(make_row_preview(value, i, size, self.th, error=err))
        return out

    def node(self, line: int, pointer: str) -> dict:
        value, _size, err = self._value(line)
        if err is not None:
            return {"type": "error", "pointer": pointer, "preview": err}
        target = resolve_pointer(value, pointer)
        return describe_node(target, pointer, self.th)

    def raw_row(self, line: int) -> str:
        return self._raw(line)

    def pretty_row(self, line: int) -> str:
        value, _size, err = self._value(line)
        if err is not None:
            return self._raw(line)
        return json.dumps(value, indent=2, ensure_ascii=False)

    def columns(self) -> dict:
        """Detect homogeneous object rows and suggest table columns."""
        th = self.th
        total = self.count()
        sample = min(total, th.column_sample)
        freq: dict[str, int] = {}
        order: list[str] = []
        obj_rows = 0
        for i in range(sample):
            value, _s, err = self._value(i)
            if err is not None or not isinstance(value, dict):
                continue
            obj_rows += 1
            for k in value:
                ks = str(k)
                if ks not in freq:
                    freq[ks] = 0
                    order.append(ks)
                freq[ks] += 1
        homogeneous = sample > 0 and obj_rows >= max(1, int(sample * 0.6))
        cols = sorted(order, key=lambda k: (-freq[k], order.index(k)))[: th.max_columns]
        return {"homogeneous": homogeneous, "columns": cols, "sampled": sample}

    def search(self, query: str, offset: int, limit: int) -> dict:
        th = self.th
        if not query:
            return {"matches": [], "total_matched": 0, "scanned": 0, "capped": False, "rows": []}
        needle = query.lower()
        total = self.count()
        scan = min(total, th.search_scan_cap)
        matches: list[int] = []
        capped = False
        for i in range(scan):
            raw = self._raw(i)
            if needle in raw.lower():
                matches.append(i)
                if len(matches) >= th.search_match_cap:
                    capped = True
                    break
        page_lines = matches[offset : offset + limit]
        rows = []
        for i in page_lines:
            value, size, err = self._value(i)
            rows.append(make_row_preview(value, i, size, th, error=err))
        return {
            "matches": matches,
            "total_matched": len(matches),
            "scanned": scan,
            "capped": capped or scan < total,
            "rows": rows,
        }


class JsonlSource(_Source):
    """One row per non-empty line, indexed by byte offset."""

    def __init__(self, path: Path, th: Thresholds):
        super().__init__(path, th)
        self._spans: list[tuple[int, int]] = []  # (start, end) byte ranges
        self._build_index()

    def _build_index(self) -> None:
        spans: list[tuple[int, int]] = []
        with self.path.open("rb") as fh:
            while True:
                chunk_start = fh.tell()
                line = fh.readline()
                if not line:
                    break
                if line.strip():
                    # store range without trailing newline/whitespace
                    content_len = len(line.rstrip(b"\r\n"))
                    spans.append((chunk_start, chunk_start + content_len))
        self._spans = spans

    def count(self) -> int:
        return len(self._spans)

    def _raw(self, line: int) -> str:
        start, end = self._spans[line]
        with self.path.open("rb") as fh:
            fh.seek(start)
            data = fh.read(end - start)
        return data.decode("utf-8", "replace")

    def _value(self, line: int) -> tuple[Any, int, str | None]:
        raw = self._raw(line)
        size = len(raw.encode("utf-8"))
        try:
            return json.loads(raw), size, None
        except (json.JSONDecodeError, ValueError) as exc:
            return None, size, f"invalid JSON: {exc}"


class JsonSource(_Source):
    """A single parsed JSON document. Top-level array => rows; else single row."""

    def __init__(self, path: Path, th: Thresholds):
        super().__init__(path, th)
        text = path.read_text(encoding="utf-8", errors="replace")
        self._doc = json.loads(text)
        self._is_array = isinstance(self._doc, list)

    def count(self) -> int:
        return len(self._doc) if self._is_array else 1

    def _elem(self, line: int) -> Any:
        return self._doc[line] if self._is_array else self._doc

    def _value(self, line: int) -> tuple[Any, int, str | None]:
        value = self._elem(line)
        # Size is estimated lazily and only matters for display.
        return value, -1, None

    def _raw(self, line: int) -> str:
        return json.dumps(self._elem(line), ensure_ascii=False)

    def pretty_row(self, line: int) -> str:
        return json.dumps(self._elem(line), indent=2, ensure_ascii=False)

    def search(self, query: str, offset: int, limit: int) -> dict:
        # For a single-document object (not an array), search is not row-based.
        if not self._is_array:
            raw = self._raw(0)
            hit = query.lower() in raw.lower() if query else False
            rows = [make_row_preview(self._elem(0), 0, len(raw), self.th)] if hit else []
            return {
                "matches": [0] if hit else [],
                "total_matched": 1 if hit else 0,
                "scanned": 1,
                "capped": False,
                "rows": rows,
            }
        return super().search(query, offset, limit)


# --- Source cache ----------------------------------------------------------
@dataclass
class _CacheEntry:
    key: tuple[float, int]
    source: _Source


_cache: dict[str, _CacheEntry] = {}
_cache_lock = threading.Lock()


def _fingerprint(path: Path) -> tuple[float, int]:
    st = path.stat()
    return (st.st_mtime, st.st_size)


def open_source(path: Path, kind: str, th: Thresholds = DEFAULT) -> _Source:
    """Return a cached source for *path*. ``kind`` is 'jsonl' or 'json'."""
    key = str(path)
    fp = _fingerprint(path)
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None and entry.key == fp:
            return entry.source
    # Build outside the lock (parsing/indexing can be slow).
    if kind == "json":
        if fp[1] > MAX_JSON_BYTES:
            raise ValueError(
                f"JSON file is too large to load whole ({fp[1]} bytes > {MAX_JSON_BYTES})."
            )
        source: _Source = JsonSource(path, th)
    else:
        source = JsonlSource(path, th)
    with _cache_lock:
        _cache[key] = _CacheEntry(key=fp, source=source)
        # keep the cache small
        if len(_cache) > 8:
            for k in list(_cache.keys())[:-8]:
                _cache.pop(k, None)
    return source
