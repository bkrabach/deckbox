/* Deckbox — lazy JSON / JSONL viewer.
 *
 * The server holds the file; this only ever pulls small previews and
 * lazily-fetched slices via /api/jsonl*. Rows are virtualized (Clusterize) and
 * carry a rich inline preview (key: value). Container tree nodes preview the
 * same rich way as the top-level row.
 *
 * Two mutually-exclusive detail modes (toggle in the toolbar):
 *  - Inline: the row's ▸ chevron expands a tree IN PLACE. Multiple rows can be
 *    open at once and survive list scrolling (expansion state is a model, every
 *    row re-renders from it). Each expanded row has its own Tree/Pretty/Raw +
 *    Copy header.
 *  - Panel: clicking a row opens it in a docked side panel with the same
 *    Tree/Pretty/Raw + Copy, one row at a time.
 */
(function () {
  "use strict";

  function initViewer(viewer) {
    var src = viewer.getAttribute("data-src") || "";
    var main = viewer.querySelector("[data-jsonl-main]");
    var detail = viewer.querySelector("[data-jsonl-detail]");
    var detailTitle = viewer.querySelector("[data-jsonl-detail-title]");
    var detailBody = viewer.querySelector("[data-jsonl-detail-body]");
    var detailTabs = viewer.querySelector("[data-jsonl-tabs]");
    var copyBtn = viewer.querySelector("[data-jsonl-copy]");
    var closeBtn = viewer.querySelector("[data-jsonl-close]");
    var countEl = viewer.querySelector("[data-jsonl-count]");
    var statusEl = viewer.querySelector("[data-jsonl-status]");
    var searchInput = viewer.querySelector(".jsonl-search");
    var searchClear = viewer.querySelector(".jsonl-search-clear");
    var viewToggle = viewer.querySelector("[data-jsonl-viewtoggle]");
    var detailModeToggle = viewer.querySelector("[data-jsonl-detailmode]");
    if (!main || !src) return;

    var PAGE = 100;
    var TABLE_PAGE = 500;
    var state = {
      total: 0, rows: [], columns: [], homogeneous: false,
      mode: "list",          // list | table
      detailMode: "inline",  // inline | panel
      selected: null,        // panel: selected line
      detailTab: "tree",     // panel tab
      search: "", searchMatches: null, tablePage: 0,
      rowOpen: {},           // line -> bool (inline row expansion)
      rowTab: {},            // line -> tree|pretty|raw (inline per-row tab)
      rowText: {},           // line -> {pretty, raw}
      nodeOpen: {},          // line -> { pointer: true }
      nodeCache: {},         // "line|pointer" -> node descriptor
    };

    function api(path, params) {
      var qs = Object.keys(params).map(function (k) {
        return encodeURIComponent(k) + "=" + encodeURIComponent(params[k]);
      }).join("&");
      return fetch(path + "?" + qs, { headers: { "Accept": "application/json" } })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, status: r.status, j: j }; }); });
    }
    function setStatus(msg) { if (statusEl) { statusEl.textContent = msg || ""; statusEl.hidden = !msg; } }
    function esc(s) {
      return String(s).replace(/[&<>"]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
      });
    }

    // ---- value rendering --------------------------------------------------
    function valHtml(t, v, trunc) {
      if (t === "string") return '<span class="j-str">"' + esc(v) + (trunc ? "…" : "") + '"</span>';
      if (t === "number") return '<span class="j-num">' + esc(v) + "</span>";
      if (t === "boolean") return '<span class="j-bool">' + esc(v) + "</span>";
      if (t === "null") return '<span class="j-null">null</span>';
      if (t === "object" || t === "array") return '<span class="j-preview">' + esc(v) + "</span>";
      return esc(String(v));
    }
    function scalarHtml(node) {
      if (node.type === "string") {
        return '<span class="j-str">"' + esc(node.value) + (node.truncated ? "…" : "") + '"</span>' +
          (node.truncated ? ' <span class="j-more">(' + node.len + " chars)</span>" : "");
      }
      return valHtml(node.type, node.value);
    }
    function typeBadge(node) {
      if (node.type === "object") return '<span class="j-badge">{} ' + node.size + "</span>";
      if (node.type === "array") return '<span class="j-badge">[] ' + node.size + "</span>";
      return "";
    }
    function summaryHtml(summary) {
      if (!summary || !summary.length) return "";
      return summary.map(function (it) {
        return '<span class="j-pair"><span class="j-sk">' + esc(it.k) + "</span>" +
          valHtml(it.t, it.v, it.trunc) + "</span>";
      }).join('<span class="j-sep">·</span>');
    }
    // A container's collapsed value: badge + rich key:value summary (same as a
    // row), falling back to the shape preview.
    function containerInner(node) {
      var body = (node.summary && node.summary.length) ? summaryHtml(node.summary)
        : '<span class="j-preview">' + esc(node.preview) + "</span>";
      return typeBadge(node) + " " + body;
    }

    // ---- model-driven tree (HTML strings) ---------------------------------
    function childrenOf(line, node) {
      if (node.children) return { items: node.children, stub: null };
      if (node.child_stubs) return { items: node.child_stubs.items, stub: node.child_stubs };
      var cached = state.nodeCache[line + "|" + node.pointer];
      if (cached) {
        if (cached.children) return { items: cached.children, stub: null };
        if (cached.child_stubs) return { items: cached.child_stubs.items, stub: cached.child_stubs };
      }
      return null;
    }
    function nodeHtml(line, node) {
      var isContainer = node.type === "object" || node.type === "array";
      var open = isContainer && state.nodeOpen[line] && state.nodeOpen[line][node.pointer];
      var chev = (isContainer && node.size > 0)
        ? '<button class="j-chevron" data-node="' + esc(node.pointer) + '" aria-label="Toggle">' + (open ? "▾" : "▸") + "</button>"
        : '<span class="j-chevron-spacer"></span>';
      var keyHtml = (node.key !== undefined && node.key !== null) ? '<span class="j-key">' + esc(node.key) + "</span>: " : "";
      var valInner = isContainer ? containerInner(node) : scalarHtml(node);
      var html = '<div class="j-node j-' + node.type + '"><div class="j-node-head">' +
        chev + keyHtml + '<span class="j-val">' + valInner + "</span></div>";
      if (open) {
        var kids = childrenOf(line, node);
        html += '<div class="j-children">';
        if (kids) {
          html += kids.items.map(function (c) { return nodeHtml(line, c); }).join("");
          if (kids.stub && kids.stub.total > kids.stub.shown) {
            html += '<div class="j-more-row">… ' + (kids.stub.total - kids.stub.shown) + " more not shown</div>";
          }
        } else { html += '<div class="j-fetching">…</div>'; }
        html += "</div>";
      }
      return html + "</div>";
    }
    function inlineTreeHtml(line) {
      var root = state.nodeCache[line + "|"];
      if (!root) return '<div class="jsonl-tree"><div class="j-fetching">…</div></div>';
      var kids = childrenOf(line, root), inner;
      if (kids) {
        inner = kids.items.map(function (c) { return nodeHtml(line, c); }).join("");
        if (kids.stub && kids.stub.total > kids.stub.shown) {
          inner += '<div class="j-more-row">… ' + (kids.stub.total - kids.stub.shown) + " more not shown</div>";
        }
      } else { inner = nodeHtml(line, root); }
      return '<div class="jsonl-tree">' + inner + "</div>";
    }

    // Per-row inline header (Tree/Pretty/Raw + Copy) and body.
    function inlineDetailHtml(line) {
      var tab = state.rowTab[line] || "tree";
      function tb(id, label) {
        return '<button type="button" data-rowtab="' + id + '" aria-pressed="' + (tab === id) + '">' + label + "</button>";
      }
      var header = '<div class="jsonl-inline-head">' +
        '<div class="jsonl-seg jsonl-inline-tabs">' + tb("tree", "Tree") + tb("pretty", "Pretty") + tb("raw", "Raw") + "</div>" +
        '<button type="button" class="jsonl-inline-copy" data-rowcopy>Copy</button></div>';
      var body;
      if (tab === "tree") {
        body = inlineTreeHtml(line);
      } else {
        var txt = (state.rowText[line] || {})[tab];
        body = txt === undefined ? '<div class="j-fetching">…</div>'
          : '<pre class="jsonl-rawpre">' + esc(txt) + "</pre>";
      }
      return '<div class="jsonl-inline">' + header + body + "</div>";
    }

    // ---- row wrapper HTML -------------------------------------------------
    function rowHtml(line) {
      var r = state.rows[line];
      var selCls = (state.detailMode === "panel" && line === state.selected) ? " is-selected" : "";
      if (!r) {
        return '<div class="jsonl-rowwrap' + selCls + '" data-line="' + line + '">' +
          '<div class="jsonl-row is-stub"><span class="jsonl-row-chev"></span>' +
          '<span class="jsonl-row-num">' + line + '</span>' +
          '<span class="jsonl-row-preview j-loading-row">…</span></div></div>';
      }
      var inline = state.detailMode === "inline";
      var isOpen = inline && !!state.rowOpen[line];
      var chev;
      if (inline && r.expandable) chev = '<button class="jsonl-row-chev" data-rowchev aria-label="Expand">' + (isOpen ? "▾" : "▸") + "</button>";
      else chev = '<span class="jsonl-row-chev"></span>';
      var body;
      if (r.type === "error") body = '<span class="j-badge j-badge-err">!</span><span class="jsonl-row-preview">' + esc(r.preview) + "</span>";
      else if (r.summary && r.summary.length) body = '<span class="jsonl-row-preview jsonl-row-summary">' + summaryHtml(r.summary) + "</span>";
      else body = '<span class="jsonl-row-preview">' + esc(r.preview || "") + "</span>";
      var rowLine = '<div class="jsonl-row' + (isOpen ? " is-open" : "") + '">' + chev +
        '<span class="jsonl-row-num">' + line + "</span>" + body + "</div>";
      return '<div class="jsonl-rowwrap' + selCls + '" data-line="' + line + '">' +
        rowLine + (isOpen ? inlineDetailHtml(line) : "") + "</div>";
    }

    // ---- lazy fetch helpers ----------------------------------------------
    function ensureNode(line, pointer, cb) {
      var key = line + "|" + pointer;
      if (state.nodeCache[key]) { cb(); return; }
      api("/api/jsonl/node", { path: src, line: line, pointer: pointer }).then(function (res) {
        if (res.ok) state.nodeCache[key] = res.j;
        cb();
      });
    }
    function ensureRowText(line, fmt, cb) {
      state.rowText[line] = state.rowText[line] || {};
      if (state.rowText[line][fmt] !== undefined) { cb(); return; }
      api("/api/jsonl/row", { path: src, line: line, format: fmt }).then(function (res) {
        state.rowText[line][fmt] = res.ok ? res.j.text : "(failed to load)";
        cb();
      });
    }
    function ensureRowData(line, cb) {
      var tab = state.rowTab[line] || "tree";
      if (tab === "tree") ensureNode(line, "", cb); else ensureRowText(line, tab, cb);
    }
    function toggleRow(line) {
      if (state.rowOpen[line]) { delete state.rowOpen[line]; refreshList(); return; }
      state.rowOpen[line] = true;
      ensureRowData(line, refreshList);
    }
    function toggleNode(line, pointer) {
      state.nodeOpen[line] = state.nodeOpen[line] || {};
      if (state.nodeOpen[line][pointer]) { delete state.nodeOpen[line][pointer]; refreshList(); return; }
      state.nodeOpen[line][pointer] = true;
      ensureNode(line, pointer, refreshList);
    }

    // ---- detail pane (panel mode) ----------------------------------------
    function applySelection(scope) {
      (scope || main).querySelectorAll("[data-line]").forEach(function (el) {
        if (el.classList.contains("jsonl-rowwrap") || el.tagName === "TR") {
          el.classList.toggle("is-selected", state.detailMode === "panel" && parseInt(el.getAttribute("data-line"), 10) === state.selected);
        }
      });
    }
    function selectRow(line) {
      state.selected = line; applySelection();
      detail.hidden = false;
      if (detailTitle) detailTitle.textContent = "Row " + line;
      renderDetail();
    }
    function closeDetail() { state.selected = null; detail.hidden = true; applySelection(); }
    function setTab(tab) {
      state.detailTab = tab;
      if (detailTabs) detailTabs.querySelectorAll("[data-tab]").forEach(function (b) {
        b.setAttribute("aria-pressed", b.getAttribute("data-tab") === tab ? "true" : "false");
      });
      renderDetail();
    }
    function renderDetail() {
      if (state.selected === null) return;
      var line = state.selected, tab = state.detailTab;
      detailBody.innerHTML = '<div class="j-fetching">…</div>';
      if (tab === "tree") {
        api("/api/jsonl/node", { path: src, line: line, pointer: "" }).then(function (res) {
          if (state.selected !== line) return;
          detailBody.innerHTML = "";
          if (!res.ok) { detailBody.innerHTML = '<div class="jsonl-error">failed to load</div>'; return; }
          var box = document.createElement("div"); box.className = "jsonl-tree";
          var kids = res.j.children || (res.j.child_stubs ? res.j.child_stubs.items : null);
          if (kids) kids.forEach(function (c) { box.appendChild(detailNodeEl(line, c)); });
          else box.appendChild(detailNodeEl(line, res.j));
          detailBody.appendChild(box);
        });
      } else {
        api("/api/jsonl/row", { path: src, line: line, format: tab }).then(function (res) {
          if (state.selected !== line) return;
          detailBody.innerHTML = "";
          if (!res.ok) { detailBody.innerHTML = '<div class="jsonl-error">failed to load</div>'; return; }
          var pre = document.createElement("pre"); pre.className = "jsonl-rawpre";
          pre.textContent = res.j.text; detailBody.appendChild(pre);
        });
      }
    }
    function detailNodeEl(line, node) {
      var wrap = document.createElement("div"); wrap.className = "j-node j-" + node.type;
      var head = document.createElement("div"); head.className = "j-node-head";
      var isContainer = node.type === "object" || node.type === "array";
      var chevBtn = null;
      if (isContainer && node.size > 0) {
        chevBtn = document.createElement("button"); chevBtn.className = "j-chevron"; chevBtn.innerHTML = "▸";
        head.appendChild(chevBtn);
      } else { var sp = document.createElement("span"); sp.className = "j-chevron-spacer"; head.appendChild(sp); }
      if (node.key !== undefined && node.key !== null) {
        var k = document.createElement("span"); k.className = "j-key"; k.textContent = node.key;
        head.appendChild(k); head.appendChild(document.createTextNode(": "));
      }
      var val = document.createElement("span"); val.className = "j-val";
      val.innerHTML = isContainer ? containerInner(node) : scalarHtml(node);
      head.appendChild(val); wrap.appendChild(head);
      var childBox = document.createElement("div"); childBox.className = "j-children"; childBox.hidden = true;
      wrap.appendChild(childBox);
      var built = false;
      function render(kids, stub) {
        childBox.innerHTML = "";
        kids.forEach(function (c) { childBox.appendChild(detailNodeEl(line, c)); });
        if (stub && stub.total > stub.shown) {
          var m = document.createElement("div"); m.className = "j-more-row";
          m.textContent = "… " + (stub.total - stub.shown) + " more not shown"; childBox.appendChild(m);
        }
        built = true;
      }
      if (isContainer && node.children) { render(node.children); built = true; }
      else if (isContainer && node.child_stubs) { render(node.child_stubs.items, node.child_stubs); built = true; }
      if (chevBtn) {
        var open = false;
        chevBtn.addEventListener("click", function () {
          open = !open; chevBtn.innerHTML = open ? "▾" : "▸"; childBox.hidden = !open;
          if (open && !built) {
            childBox.innerHTML = '<div class="j-fetching">…</div>';
            api("/api/jsonl/node", { path: src, line: line, pointer: node.pointer }).then(function (res) {
              if (res.ok && res.j.children) render(res.j.children);
              else if (res.ok && res.j.child_stubs) render(res.j.child_stubs.items, res.j.child_stubs);
              else childBox.innerHTML = '<div class="j-fetching">(empty)</div>';
            });
          }
        });
      }
      return wrap;
    }

    if (detailTabs) detailTabs.addEventListener("click", function (e) {
      var b = e.target.closest("[data-tab]"); if (b) setTab(b.getAttribute("data-tab"));
    });
    if (closeBtn) closeBtn.addEventListener("click", closeDetail);
    if (copyBtn) copyBtn.addEventListener("click", function () {
      if (state.selected === null) return;
      var fmt = state.detailTab === "pretty" ? "pretty" : "raw";
      api("/api/jsonl/row", { path: src, line: state.selected, format: fmt }).then(function (res) {
        if (res.ok) copyText(res.j.text, copyBtn);
      });
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeDetail(); });

    // ---- list view --------------------------------------------------------
    var clusterize = null;
    function rowLineNumbers() {
      if (state.searchMatches) return state.searchMatches;
      var arr = new Array(state.total);
      for (var i = 0; i < state.total; i++) arr[i] = i;
      return arr;
    }
    function buildRowsHtml() { return rowLineNumbers().map(rowHtml); }
    function refreshList() { if (clusterize) clusterize.update(buildRowsHtml()); }
    function ensureRows(lines, cb) {
      var missing = lines.filter(function (l) { return !state.rows[l]; });
      if (!missing.length) { cb && cb(); return; }
      var lo = Math.min.apply(null, missing), hi = Math.max.apply(null, missing);
      api("/api/jsonl", { path: src, offset: lo, limit: Math.min(500, hi - lo + 1) }).then(function (res) {
        if (res.ok && res.j.rows) res.j.rows.forEach(function (row) { state.rows[row.line] = row; });
        cb && cb();
      });
    }
    function renderList() {
      main.innerHTML = "";
      var scroll = document.createElement("div"); scroll.className = "jsonl-scroll clusterize-scroll";
      var content = document.createElement("div"); content.className = "jsonl-rows clusterize-content";
      content.innerHTML = '<div class="clusterize-no-data">Loading…</div>';
      scroll.appendChild(content); main.appendChild(scroll);

      clusterize = new Clusterize({
        rows: buildRowsHtml(), scrollElem: scroll, contentElem: content, rows_in_block: 20,
        callbacks: {
          clusterChanged: function () {
            applySelection(content);
            var stubs = content.querySelectorAll(".jsonl-row.is-stub");
            if (!stubs.length) return;
            var lines = Array.prototype.map.call(stubs, function (el) {
              return parseInt(el.closest(".jsonl-rowwrap").getAttribute("data-line"), 10);
            });
            ensureRows(lines, refreshList);
          },
        },
      });

      content.addEventListener("click", function (e) {
        var wrap = e.target.closest(".jsonl-rowwrap");
        if (!wrap) return;
        var line = parseInt(wrap.getAttribute("data-line"), 10);
        var nodeChev = e.target.closest(".j-chevron[data-node]");
        if (nodeChev) { e.stopPropagation(); toggleNode(line, nodeChev.getAttribute("data-node")); return; }
        var rowTabBtn = e.target.closest("[data-rowtab]");
        if (rowTabBtn) { e.stopPropagation(); setRowTab(line, rowTabBtn.getAttribute("data-rowtab")); return; }
        if (e.target.closest("[data-rowcopy]")) {
          e.stopPropagation();
          var fmt = (state.rowTab[line] || "tree") === "pretty" ? "pretty" : "raw";
          ensureRowText(line, fmt, function () { copyText((state.rowText[line] || {})[fmt], e.target.closest("[data-rowcopy]")); });
          return;
        }
        if (e.target.closest(".jsonl-row-chev[data-rowchev]")) { toggleRow(line); return; }
        if (wrap.querySelector(".jsonl-row.is-stub")) return;
        // row-body click
        if (state.detailMode === "inline") toggleRow(line);
        else selectRow(line);
      });
    }
    function setRowTab(line, tab) {
      state.rowTab[line] = tab;
      ensureRowData(line, refreshList);
    }

    // ---- table view (paged) ----------------------------------------------
    function colValue(row, col) {
      if (!row || !row.summary) return undefined;
      for (var i = 0; i < row.summary.length; i++) {
        if (String(row.summary[i].k) === col) {
          var it = row.summary[i];
          return it.t === "string" ? it.v + (it.trunc ? "…" : "") : String(it.v);
        }
      }
      return undefined;
    }
    function renderTable() {
      main.innerHTML = "";
      var cols = state.columns.slice(0, 24);
      var lines = rowLineNumbers();
      var pages = Math.max(1, Math.ceil(lines.length / TABLE_PAGE));
      if (state.tablePage >= pages) state.tablePage = pages - 1;
      var startI = state.tablePage * TABLE_PAGE;
      var pageLines = lines.slice(startI, startI + TABLE_PAGE);
      var wrap = document.createElement("div"); wrap.className = "jsonl-table-wrap";
      var table = document.createElement("table"); table.className = "jsonl-table";
      table.innerHTML = "<thead><tr><th class=\"j-col-num\">#</th>" +
        cols.map(function (c) { return "<th>" + esc(c) + "</th>"; }).join("") + "</tr></thead><tbody></tbody>";
      wrap.appendChild(table);
      var tbody = table.querySelector("tbody");
      ensureRows(pageLines, function () {
        tbody.innerHTML = pageLines.map(function (line) {
          var r = state.rows[line] || {};
          var tds = cols.map(function (c) {
            var v = colValue(r, c);
            return "<td>" + (v === undefined ? '<span class="j-empty">—</span>' : esc(v)) + "</td>";
          }).join("");
          var sel = line === state.selected ? " is-selected" : "";
          return '<tr class="' + sel + '" data-line="' + line + '"><td class="j-col-num">' + line + "</td>" + tds + "</tr>";
        }).join("");
      });
      tbody.addEventListener("click", function (e) {
        var tr = e.target.closest("tr[data-line]");
        if (tr) selectRow(parseInt(tr.getAttribute("data-line"), 10));
      });
      if (pages > 1) {
        var pager = document.createElement("div"); pager.className = "jsonl-pager";
        pager.innerHTML =
          '<button type="button" data-page="prev"' + (state.tablePage === 0 ? " disabled" : "") + ">‹ Prev</button>" +
          '<span class="jsonl-pager-info">Rows ' + (startI + 1).toLocaleString() + "–" +
          Math.min(startI + TABLE_PAGE, lines.length).toLocaleString() + " of " + lines.length.toLocaleString() +
          "  (page " + (state.tablePage + 1) + " / " + pages + ")</span>" +
          '<button type="button" data-page="next"' + (state.tablePage >= pages - 1 ? " disabled" : "") + ">Next ›</button>";
        pager.addEventListener("click", function (e) {
          var b = e.target.closest("[data-page]"); if (!b || b.disabled) return;
          state.tablePage += b.getAttribute("data-page") === "next" ? 1 : -1; renderTable();
        });
        wrap.appendChild(pager);
      }
      main.appendChild(wrap);
    }

    function renderMode() {
      // The detail-mode toggle only applies to list view; table always uses the panel.
      if (detailModeToggle) detailModeToggle.hidden = state.mode !== "list";
      if (state.mode === "table") renderTable(); else renderList();
    }

    // ---- search -----------------------------------------------------------
    var searchTimer = null;
    function runSearch(q) {
      state.search = q; state.tablePage = 0;
      if (searchClear) searchClear.hidden = !q;
      if (!q) { state.searchMatches = null; updateCount(); renderMode(); return; }
      setStatus("Searching…");
      api("/api/jsonl/search", { path: src, q: q, offset: 0, limit: 200 }).then(function (res) {
        if (!res.ok) { setStatus("search failed"); return; }
        state.searchMatches = res.j.matches || [];
        (res.j.rows || []).forEach(function (row) { state.rows[row.line] = row; });
        setStatus(res.j.capped ? "Showing first " + state.searchMatches.length + " matches" : "");
        updateCount(); renderMode();
      });
    }
    if (searchInput) searchInput.addEventListener("input", function () {
      clearTimeout(searchTimer);
      var q = searchInput.value.trim();
      searchTimer = setTimeout(function () { runSearch(q); }, 250);
    });
    if (searchClear) searchClear.addEventListener("click", function () { searchInput.value = ""; runSearch(""); });
    function updateCount() {
      if (!countEl) return;
      countEl.textContent = state.searchMatches
        ? state.searchMatches.length + " / " + state.total.toLocaleString() + " rows"
        : state.total.toLocaleString() + " rows";
    }

    if (viewToggle) viewToggle.addEventListener("click", function (e) {
      var b = e.target.closest("[data-view]"); if (!b) return;
      state.mode = b.getAttribute("data-view");
      viewToggle.querySelectorAll("[data-view]").forEach(function (btn) {
        btn.setAttribute("aria-pressed", btn === b ? "true" : "false");
      });
      renderMode();
    });
    if (detailModeToggle) detailModeToggle.addEventListener("click", function (e) {
      var b = e.target.closest("[data-detailmode]"); if (!b) return;
      state.detailMode = b.getAttribute("data-detailmode");
      detailModeToggle.querySelectorAll("[data-detailmode]").forEach(function (btn) {
        btn.setAttribute("aria-pressed", btn === b ? "true" : "false");
      });
      if (state.detailMode === "inline") closeDetail();  // panel and inline are exclusive
      renderMode();
    });

    // ---- boot -------------------------------------------------------------
    api("/api/jsonl", { path: src, offset: 0, limit: PAGE }).then(function (res) {
      if (!res.ok) {
        main.innerHTML = '<div class="jsonl-error">' + esc((res.j && res.j.detail) || "Could not load this file.") + "</div>";
        return;
      }
      state.total = res.j.total || 0;
      state.columns = res.j.columns || [];
      state.homogeneous = !!res.j.homogeneous;
      (res.j.rows || []).forEach(function (row) { state.rows[row.line] = row; });
      updateCount();
      if (state.homogeneous && state.columns.length && viewToggle) viewToggle.hidden = false;
      renderMode();
    });
  }

  function copyText(text, btn) {
    function done(ok) {
      if (!btn) return;
      var old = btn.textContent;
      btn.textContent = ok ? "Copied!" : "Failed";
      setTimeout(function () { btn.textContent = old; }, 1400);
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
      return;
    }
    var ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.focus(); ta.select();
    var ok = false; try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta); done(ok);
  }

  document.querySelectorAll("[data-deckbox-jsonl]").forEach(initViewer);
})();
