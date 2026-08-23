"""Server side of the lazy JSON/JSONL viewer.

Emits only a small shell — the row list, tree, table, and drill-down are all
hydrated by static/js/jsonl.js talking to the /api/jsonl* endpoints, so this
never reads the (possibly 20 MB) file at page-render time.
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
    <div class="jsonl-view-toggle" role="group" aria-label="View mode" hidden data-jsonl-viewtoggle>
      <button type="button" data-view="list" aria-pressed="true" title="List view">List</button>
      <button type="button" data-view="table" aria-pressed="false" title="Table view">Table</button>
    </div>
  </div>
  <div class="jsonl-body" data-jsonl-body>
    <div class="jsonl-loading">Loading…</div>
  </div>
  <div class="jsonl-status" data-jsonl-status hidden></div>
</div>
"""
