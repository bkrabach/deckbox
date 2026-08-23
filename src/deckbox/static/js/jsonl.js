/* Deckbox — lazy JSON / JSONL viewer.
 *
 * The server holds the file; this only ever pulls small previews and
 * lazily-fetched slices via /api/jsonl*. Rows are virtualized (Clusterize) and
 * carry a rich inline preview (key: value). Clicking a row opens a persistent
 * master-detail pane (Tree / Pretty / Raw + Copy) that survives list scrolling.
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
    if (!main || !src) return;

    var PAGE = 100;
    var TABLE_PAGE = 500;
    var state = {
      total: 0,
      rows: [],            // row previews by line
      columns: [],
      homogeneous: false,
      mode: "list",
      selected: null,      // selected line (detail pane)
      detailTab: "tree",
      search: "",
      searchMatches: null, // array of line numbers when searching
      tablePage: 0,
    };

    function api(path, params) {
      var qs = Object.keys(params).map(function (k) {
        return encodeURIComponent(k) + "=" + encodeURIComponent(params[k]);
      }).join("&");
      return fetch(path + "?" + qs, { headers: { "Accept": "application/json" } })
        .then(function (r) {
          return r.json().then(function (j) { return { ok: r.ok, status: r.status, j: j }; });
        });
    }

    function setStatus(msg) { if (statusEl) { statusEl.textContent = msg || ""; statusEl.hidden = !msg; } }
    function esc(s) {
      return String(s).replace(/[&<>"]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
      });
    }

    // ---- value rendering (shared by preview + tree) -----------------------
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

    // ---- rich row preview (key: value inline) -----------------------------
    function summaryHtml(summary) {
      if (!summary || !summary.length) return "";
      return summary.map(function (it) {
        var v = valHtml(it.t, it.v, it.trunc);
        return '<span class="j-pair"><span class="j-sk">' + esc(it.k) + "</span>" + v + "</span>";
      }).join('<span class="j-sep">·</span>');
    }

    function rowPreviewHtml(line) {
      var r = state.rows[line];
      var selCls = line === state.selected ? " is-selected" : "";
      if (!r) {
        return '<div class="jsonl-row is-stub' + selCls + '" data-line="' + line + '">' +
          '<span class="jsonl-row-num">' + line + '</span>' +
          '<span class="jsonl-row-preview j-loading-row">…</span></div>';
      }
      var chev = r.expandable ? "▸" : "";
      var body;
      if (r.type === "error") {
        body = '<span class="j-badge j-badge-err">!</span><span class="jsonl-row-preview">' + esc(r.preview) + "</span>";
      } else if (r.summary && r.summary.length) {
        body = '<span class="jsonl-row-preview jsonl-row-summary">' + summaryHtml(r.summary) + "</span>";
      } else {
        body = '<span class="jsonl-row-preview">' + esc(r.preview || "") + "</span>";
      }
      return '<div class="jsonl-row' + selCls + '" data-line="' + line + '">' +
        '<span class="jsonl-row-chev">' + chev + "</span>" +
        '<span class="jsonl-row-num">' + line + "</span>" +
        body + "</div>";
    }

    // ---- lazy tree node (used inside the detail pane) ---------------------
    function nodeEl(line, node) {
      var wrap = document.createElement("div");
      wrap.className = "j-node j-" + node.type;
      var head = document.createElement("div");
      head.className = "j-node-head";
      var isContainer = node.type === "object" || node.type === "array";

      if (isContainer && node.size > 0) {
        var chev = document.createElement("button");
        chev.className = "j-chevron";
        chev.setAttribute("aria-label", "Toggle");
        chev.innerHTML = "▸";
        head.appendChild(chev);
      } else {
        var sp = document.createElement("span");
        sp.className = "j-chevron-spacer";
        head.appendChild(sp);
      }
      if (node.key !== undefined && node.key !== null) {
        var keyEl = document.createElement("span");
        keyEl.className = "j-key";
        keyEl.textContent = node.key;
        head.appendChild(keyEl);
        head.appendChild(document.createTextNode(": "));
      }
      var val = document.createElement("span");
      val.className = "j-val";
      val.innerHTML = isContainer
        ? typeBadge(node) + ' <span class="j-preview">' + esc(node.preview) + "</span>"
        : scalarHtml(node);
      head.appendChild(val);
      wrap.appendChild(head);

      var childBox = document.createElement("div");
      childBox.className = "j-children";
      childBox.hidden = true;
      wrap.appendChild(childBox);

      var built = false;
      function renderChildren(children, stub) {
        childBox.innerHTML = "";
        children.forEach(function (c) { childBox.appendChild(nodeEl(line, c)); });
        if (stub && stub.total > stub.shown) {
          var more = document.createElement("div");
          more.className = "j-more-row";
          more.textContent = "… " + (stub.total - stub.shown) + " more not shown";
          childBox.appendChild(more);
        }
        built = true;
      }
      if (isContainer && node.children) { renderChildren(node.children); built = true; }
      else if (isContainer && node.child_stubs) { renderChildren(node.child_stubs.items, node.child_stubs); built = true; }

      if (isContainer && node.size > 0) {
        var chevBtn = head.querySelector(".j-chevron");
        var open = false;
        chevBtn.addEventListener("click", function () {
          open = !open;
          chevBtn.innerHTML = open ? "▾" : "▸";
          childBox.hidden = !open;
          if (open && !built) {
            childBox.innerHTML = '<div class="j-fetching">…</div>';
            api("/api/jsonl/node", { path: src, line: line, pointer: node.pointer }).then(function (res) {
              if (res.ok && res.j.children) renderChildren(res.j.children);
              else if (res.ok && res.j.child_stubs) renderChildren(res.j.child_stubs.items, res.j.child_stubs);
              else if (res.ok) childBox.innerHTML = '<div class="j-fetching">(empty)</div>';
              else childBox.innerHTML = '<div class="j-fetching">failed to load</div>';
            });
          }
        });
      }
      return wrap;
    }

    // ---- master-detail pane ----------------------------------------------
    function applySelection(scope) {
      (scope || main).querySelectorAll("[data-line]").forEach(function (el) {
        el.classList.toggle("is-selected", parseInt(el.getAttribute("data-line"), 10) === state.selected);
      });
    }

    function selectRow(line) {
      state.selected = line;
      applySelection();
      detail.hidden = false;
      if (detailTitle) detailTitle.textContent = "Row " + line;
      renderDetail();
    }

    function closeDetail() {
      state.selected = null;
      detail.hidden = true;
      applySelection();
    }

    function setTab(tab) {
      state.detailTab = tab;
      if (detailTabs) {
        detailTabs.querySelectorAll("[data-tab]").forEach(function (b) {
          b.setAttribute("aria-pressed", b.getAttribute("data-tab") === tab ? "true" : "false");
        });
      }
      renderDetail();
    }

    function renderDetail() {
      if (state.selected === null) return;
      var line = state.selected;
      var tab = state.detailTab;
      detailBody.innerHTML = '<div class="j-fetching">…</div>';
      if (tab === "tree") {
        api("/api/jsonl/node", { path: src, line: line, pointer: "" }).then(function (res) {
          if (state.selected !== line) return;
          detailBody.innerHTML = "";
          if (!res.ok) { detailBody.innerHTML = '<div class="jsonl-error">failed to load</div>'; return; }
          var tree = document.createElement("div");
          tree.className = "jsonl-tree";
          var node = res.j;
          if ((node.type === "object" || node.type === "array") && node.children) {
            node.children.forEach(function (c) { tree.appendChild(nodeEl(line, c)); });
          } else if ((node.type === "object" || node.type === "array") && node.child_stubs) {
            node.child_stubs.items.forEach(function (c) { tree.appendChild(nodeEl(line, c)); });
          } else {
            tree.appendChild(nodeEl(line, node));
          }
          detailBody.appendChild(tree);
        });
      } else {
        api("/api/jsonl/row", { path: src, line: line, format: tab }).then(function (res) {
          if (state.selected !== line) return;
          detailBody.innerHTML = "";
          if (!res.ok) { detailBody.innerHTML = '<div class="jsonl-error">failed to load</div>'; return; }
          var pre = document.createElement("pre");
          pre.className = "jsonl-rawpre";
          pre.textContent = res.j.text;
          detailBody.appendChild(pre);
        });
      }
    }

    if (detailTabs) {
      detailTabs.addEventListener("click", function (e) {
        var b = e.target.closest("[data-tab]");
        if (b) setTab(b.getAttribute("data-tab"));
      });
    }
    if (closeBtn) closeBtn.addEventListener("click", closeDetail);
    if (copyBtn) {
      copyBtn.addEventListener("click", function () {
        if (state.selected === null) return;
        var fmt = state.detailTab === "pretty" ? "pretty" : "raw";
        api("/api/jsonl/row", { path: src, line: state.selected, format: fmt }).then(function (res) {
          if (res.ok) copyText(res.j.text, copyBtn);
        });
      });
    }
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeDetail(); });

    // ---- list view (virtualized) -----------------------------------------
    var clusterize = null;

    function rowLineNumbers() {
      if (state.searchMatches) return state.searchMatches;
      var arr = new Array(state.total);
      for (var i = 0; i < state.total; i++) arr[i] = i;
      return arr;
    }

    function buildRowsHtml() {
      return rowLineNumbers().map(rowPreviewHtml);
    }

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
      var scroll = document.createElement("div");
      scroll.className = "jsonl-scroll clusterize-scroll";
      var content = document.createElement("div");
      content.className = "jsonl-rows clusterize-content";
      content.innerHTML = '<div class="clusterize-no-data">Loading…</div>';
      scroll.appendChild(content);
      main.appendChild(scroll);

      clusterize = new Clusterize({
        rows: buildRowsHtml(),
        scrollElem: scroll,
        contentElem: content,
        rows_in_block: 30,
        callbacks: {
          clusterChanged: function () {
            var stubs = content.querySelectorAll(".jsonl-row.is-stub");
            applySelection(content);
            if (!stubs.length) return;
            var lines = Array.prototype.map.call(stubs, function (el) {
              return parseInt(el.getAttribute("data-line"), 10);
            });
            ensureRows(lines, refreshList);
          },
        },
      });

      content.addEventListener("click", function (e) {
        var row = e.target.closest(".jsonl-row");
        if (!row || row.classList.contains("is-stub")) return;
        selectRow(parseInt(row.getAttribute("data-line"), 10));
      });
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

      var wrap = document.createElement("div");
      wrap.className = "jsonl-table-wrap";
      var table = document.createElement("table");
      table.className = "jsonl-table";
      table.innerHTML = "<thead><tr><th class=\"j-col-num\">#</th>" +
        cols.map(function (c) { return "<th>" + esc(c) + "</th>"; }).join("") +
        "</tr></thead><tbody></tbody>";
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
        var pager = document.createElement("div");
        pager.className = "jsonl-pager";
        pager.innerHTML =
          '<button type="button" data-page="prev"' + (state.tablePage === 0 ? " disabled" : "") + ">‹ Prev</button>" +
          '<span class="jsonl-pager-info">Rows ' + (startI + 1).toLocaleString() + "–" +
          Math.min(startI + TABLE_PAGE, lines.length).toLocaleString() + " of " + lines.length.toLocaleString() +
          "  (page " + (state.tablePage + 1) + " / " + pages + ")</span>" +
          '<button type="button" data-page="next"' + (state.tablePage >= pages - 1 ? " disabled" : "") + ">Next ›</button>";
        pager.addEventListener("click", function (e) {
          var b = e.target.closest("[data-page]");
          if (!b || b.disabled) return;
          state.tablePage += b.getAttribute("data-page") === "next" ? 1 : -1;
          renderTable();
        });
        wrap.appendChild(pager);
      }
      main.appendChild(wrap);
    }

    function renderMode() {
      if (state.mode === "table") renderTable(); else renderList();
    }

    // ---- search -----------------------------------------------------------
    var searchTimer = null;
    function runSearch(q) {
      state.search = q;
      state.tablePage = 0;
      if (searchClear) searchClear.hidden = !q;
      if (!q) { state.searchMatches = null; updateCount(); renderMode(); return; }
      setStatus("Searching…");
      api("/api/jsonl/search", { path: src, q: q, offset: 0, limit: 200 }).then(function (res) {
        if (!res.ok) { setStatus("search failed"); return; }
        state.searchMatches = res.j.matches || [];
        (res.j.rows || []).forEach(function (row) { state.rows[row.line] = row; });
        setStatus(res.j.capped ? "Showing first " + state.searchMatches.length + " matches" : "");
        updateCount();
        renderMode();
      });
    }
    if (searchInput) {
      searchInput.addEventListener("input", function () {
        clearTimeout(searchTimer);
        var q = searchInput.value.trim();
        searchTimer = setTimeout(function () { runSearch(q); }, 250);
      });
    }
    if (searchClear) searchClear.addEventListener("click", function () { searchInput.value = ""; runSearch(""); });

    function updateCount() {
      if (!countEl) return;
      countEl.textContent = state.searchMatches
        ? state.searchMatches.length + " / " + state.total.toLocaleString() + " rows"
        : state.total.toLocaleString() + " rows";
    }

    if (viewToggle) {
      viewToggle.addEventListener("click", function (e) {
        var b = e.target.closest("[data-view]");
        if (!b) return;
        state.mode = b.getAttribute("data-view");
        viewToggle.querySelectorAll("[data-view]").forEach(function (btn) {
          btn.setAttribute("aria-pressed", btn === b ? "true" : "false");
        });
        renderMode();
      });
    }

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
