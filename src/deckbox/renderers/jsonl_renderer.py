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
    <div class="jsonl-seg" role="group" aria-label="Detail mode" data-jsonl-detailmode>
      <button type="button" data-detailmode="inline" aria-pressed="true" title="Expand rows in place">Inline</button>
      <button type="button" data-detailmode="panel" aria-pressed="false" title="Open rows in a side panel">Panel</button>
    </div>
    <div class="jsonl-seg" role="group" aria-label="View mode" hidden data-jsonl-viewtoggle>
      <button type="button" data-view="list" aria-pressed="true" title="List view">List</button>
      <button type="button" data-view="table" aria-pressed="false" title="Table view">Table</button>
    </div>
  </div>
  <div class="jsonl-layout" data-jsonl-layout>
    <div class="jsonl-main" data-jsonl-main>
      <div class="jsonl-loading">Loading…</div>
    </div>
    <aside class="jsonl-detail" data-jsonl-detail hidden aria-label="Row detail">
      <div class="jsonl-detail-head">
        <span class="jsonl-detail-title" data-jsonl-detail-title>Row</span>
        <div class="jsonl-detail-tabs" role="group" aria-label="Row view" data-jsonl-tabs>
          <button type="button" data-tab="tree" aria-pressed="true">Tree</button>
          <button type="button" data-tab="pretty" aria-pressed="false">Pretty</button>
          <button type="button" data-tab="raw" aria-pressed="false">Raw</button>
        </div>
        <button type="button" class="jsonl-detail-copy" data-jsonl-copy title="Copy this row">Copy</button>
        <button type="button" class="jsonl-detail-close" data-jsonl-close aria-label="Close detail">×</button>
      </div>
      <div class="jsonl-detail-body" data-jsonl-detail-body></div>
    </aside>
  </div>
  <div class="jsonl-status" data-jsonl-status hidden></div>
</div>
"""
