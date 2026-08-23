"""Server side of the lazy JSON/JSONL viewer.

Emits only a small shell — the row list is hydrated by static/js/jsonl.js talking
to the /api/jsonl* endpoints, and opened rows are rendered client-side by the
vendored JSONViewer component (static/js/vendor/json-viewer.js), so this never
reads the (possibly 20 MB) file at page-render time.
"""

from __future__ import annotations

import html
from pathlib import Path


def render(path: Path, *, src_path: str = "") -> str:
    src = html.escape(src_path, quote=True)
    return f"""
<div class="jsonl-viewer" data-deckbox-jsonl data-src="{src}">
  <div class="jsonl-toolbar">
    <div class="jsonl-search-wrap">
      <svg class="jsonl-search-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.2-3.2"/></svg>
      <input type="text" class="jsonl-search" placeholder="Search all rows…" autocomplete="off" spellcheck="false" aria-label="Search rows">
      <button type="button" class="jsonl-search-clear" hidden aria-label="Clear search">×</button>
    </div>
    <span class="jsonl-count" data-jsonl-count></span>
    <span class="jsonl-spacer"></span>
    <div class="jsonl-seg" role="group" aria-label="View mode" hidden data-jsonl-viewtoggle>
      <button type="button" data-view="list" aria-pressed="true" title="List view">List</button>
      <button type="button" data-view="table" aria-pressed="false" title="Table view">Table</button>
    </div>
  </div>
  <div class="jsonl-layout" data-jsonl-layout>
    <div class="jsonl-main" data-jsonl-main>
      <div class="jsonl-loading">Loading…</div>
    </div>
    <aside class="jsonl-reader" data-jsonl-reader hidden aria-label="Opened rows">
      <div class="jsonl-reader-head">
        <span class="jsonl-reader-title">Opened rows</span>
        <button type="button" class="jsonl-reader-closeall" data-jsonl-closeall>Close all</button>
      </div>
      <div class="jsonl-reader-cards" data-jsonl-reader-cards></div>
    </aside>
  </div>
  <div class="jsonl-status" data-jsonl-status hidden></div>
</div>
"""
