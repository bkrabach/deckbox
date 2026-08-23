/* Deckbox — lazy JSON / JSONL viewer.
 *
 * The server holds the file; this only ever pulls small previews and one row's
 * JSON at a time via /api/jsonl*. The row list is virtualized (Clusterize) and
 * carries a rich inline preview. Opening a row adds a card to the reader pane,
 * where its JSON is rendered by the vendored JSONViewer component — expand and
 * collapse happen in place (pure DOM show/hide, no re-render, no refetch), so
 * drilling into a deep node never redraws or scroll-jumps, and open cards
 * persist regardless of how the list is scrolled. Multiple rows can be open.
 */
(function () {
  "use strict";

  function initViewer(viewer) {
    var src = viewer.getAttribute("data-src") || "";
    var main = viewer.querySelector("[data-jsonl-main]");
    var reader = viewer.querySelector("[data-jsonl-reader]");
    var readerCards = viewer.querySelector("[data-jsonl-reader-cards]");
    var closeAllBtn = viewer.querySelector("[data-jsonl-closeall]");
    var countEl = viewer.querySelector("[data-jsonl-count]");
    var statusEl = viewer.querySelector("[data-jsonl-status]");
    var searchInput = viewer.querySelector(".jsonl-search");
    var searchClear = viewer.querySelector(".jsonl-search-clear");
    var viewToggle = viewer.querySelector("[data-jsonl-viewtoggle]");
    if (!main || !src) return;

    var PAGE = 100;
    var TABLE_PAGE = 500;
    var state = {
      total: 0, rows: [], columns: [], homogeneous: false,
      mode: "list", search: "", searchMatches: null, tablePage: 0,
      open: [],       // ordered open line numbers
      cards: {},      // line -> { el, tab, raw, parsed, viewer }
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

    // ---- rich row preview (key: value inline) -----------------------------
    function valHtml(t, v, trunc) {
      if (t === "string") return '<span class="j-str">"' + esc(v) + (trunc ? "…" : "") + '"</span>';
      if (t === "number") return '<span class="j-num">' + esc(v) + "</span>";
      if (t === "boolean") return '<span class="j-bool">' + esc(v) + "</span>";
      if (t === "null") return '<span class="j-null">null</span>';
      if (t === "object" || t === "array") return '<span class="j-preview">' + esc(v) + "</span>";
      return esc(String(v));
    }
    function summaryHtml(summary) {
      if (!summary || !summary.length) return "";
      return summary.map(function (it) {
        return '<span class="j-pair"><span class="j-sk">' + esc(it.k) + "</span>" +
          valHtml(it.t, it.v, it.trunc) + "</span>";
      }).join('<span class="j-sep">·</span>');
    }
    function rowHtml(line) {
      var r = state.rows[line];
      var selCls = state.cards[line] ? " is-open" : "";
      if (!r) {
        return '<div class="jsonl-row is-stub' + selCls + '" data-line="' + line + '">' +
          '<span class="jsonl-row-num">' + line + '</span>' +
          '<span class="jsonl-row-preview j-loading-row">…</span></div>';
      }
      var body;
      if (r.type === "error") body = '<span class="j-badge j-badge-err">!</span><span class="jsonl-row-preview">' + esc(r.preview) + "</span>";
      else if (r.summary && r.summary.length) body = '<span class="jsonl-row-preview jsonl-row-summary">' + summaryHtml(r.summary) + "</span>";
      else body = '<span class="jsonl-row-preview">' + esc(r.preview || "") + "</span>";
      return '<div class="jsonl-row' + selCls + '" data-line="' + line + '">' +
        '<span class="jsonl-row-num">' + line + "</span>" + body + "</div>";
    }

    // ---- reader pane: one JSONViewer card per opened row ------------------
    function openCard(line) {
      if (state.cards[line]) { flashCard(line); return; }
      var card = document.createElement("div");
      card.className = "reader-card";
      card.setAttribute("data-line", line);
      card.innerHTML =
        '<div class="reader-card-head">' +
        '<span class="reader-card-title">Row ' + line + "</span>" +
        '<div class="jsonl-seg reader-card-tabs">' +
        '<button type="button" data-cardtab="data" aria-pressed="true">Data</button>' +
        '<button type="button" data-cardtab="raw" aria-pressed="false">Raw</button>' +
        "</div>" +
        '<button type="button" class="reader-card-copy" data-cardcopy>Copy</button>' +
        '<button type="button" class="reader-card-close" data-cardclose aria-label="Close">×</button>' +
        "</div>" +
        '<div class="reader-card-body"><div class="j-fetching">…</div></div>';
      readerCards.appendChild(card);
      var entry = { el: card, tab: "data", raw: null, parsed: undefined, viewer: null };
      state.cards[line] = entry;
      state.open.push(line);
      showReader();
      markRowOpen(line, true);

      card.addEventListener("click", function (e) {
        var tabBtn = e.target.closest("[data-cardtab]");
        if (tabBtn) { setCardTab(line, tabBtn.getAttribute("data-cardtab")); return; }
        if (e.target.closest("[data-cardcopy]")) { copyCard(line, e.target.closest("[data-cardcopy]")); return; }
        if (e.target.closest("[data-cardclose]")) { closeCard(line); return; }
      });

      api("/api/jsonl/row", { path: src, line: line, format: "raw" }).then(function (res) {
        entry.raw = res.ok ? res.j.text : "(failed to load)";
        try { entry.parsed = JSON.parse(entry.raw); }
        catch (e) { entry.parsed = undefined; }
        renderCard(line);
        flashCard(line);
      });
    }

    function renderCard(line) {
      var entry = state.cards[line];
      if (!entry) return;
      var body = entry.el.querySelector(".reader-card-body");
      body.innerHTML = "";
      if (entry.tab === "data" && entry.parsed !== undefined) {
        var host = document.createElement("div");
        body.appendChild(host);
        // A fresh JSONViewer per (re)render. Expansion within it is pure DOM
        // toggling — it never calls back here, so nothing redraws or scrolls.
        entry.viewer = new JSONViewer(host, { maxTextLength: 200 });
        entry.viewer.render(entry.parsed);
      } else if (entry.tab === "raw" || entry.parsed === undefined) {
        var pre = document.createElement("pre");
        pre.className = "jsonl-rawpre";
        pre.textContent = entry.parsed !== undefined
          ? JSON.stringify(entry.parsed, null, 2)
          : (entry.raw || "");
        body.appendChild(pre);
      }
    }

    function setCardTab(line, tab) {
      var entry = state.cards[line];
      if (!entry || entry.tab === tab) return;
      entry.tab = tab;
      entry.el.querySelectorAll("[data-cardtab]").forEach(function (b) {
        b.setAttribute("aria-pressed", b.getAttribute("data-cardtab") === tab ? "true" : "false");
      });
      renderCard(line);
    }

    function copyCard(line, btn) {
      var entry = state.cards[line];
      if (!entry) return;
      copyText(entry.raw || "", btn);
    }

    function closeCard(line) {
      var entry = state.cards[line];
      if (!entry) return;
      entry.el.parentNode.removeChild(entry.el);
      delete state.cards[line];
      state.open = state.open.filter(function (l) { return l !== line; });
      markRowOpen(line, false);
      if (!state.open.length) hideReader();
    }

    function closeAll() {
      state.open.slice().forEach(closeCard);
    }

    function flashCard(line) {
      var entry = state.cards[line];
      if (!entry) return;
      entry.el.scrollIntoView({ block: "nearest", behavior: "smooth" });
      entry.el.classList.remove("flash");
      void entry.el.offsetWidth;  // restart animation
      entry.el.classList.add("flash");
    }

    function showReader() { reader.hidden = false; }
    function hideReader() { reader.hidden = true; }
    function markRowOpen(line, on) {
      main.querySelectorAll('[data-line="' + line + '"]').forEach(function (el) {
        el.classList.toggle("is-open", on);
      });
    }
    function reapplyOpenMarks(scope) {
      (scope || main).querySelectorAll(".jsonl-row[data-line], tr[data-line]").forEach(function (el) {
        el.classList.toggle("is-open", !!state.cards[parseInt(el.getAttribute("data-line"), 10)]);
      });
    }
    if (closeAllBtn) closeAllBtn.addEventListener("click", closeAll);

    // ---- list view (virtualized) -----------------------------------------
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
        rows: buildRowsHtml(), scrollElem: scroll, contentElem: content, rows_in_block: 30,
        callbacks: {
          clusterChanged: function () {
            reapplyOpenMarks(content);
            var stubs = content.querySelectorAll(".jsonl-row.is-stub");
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
        openCard(parseInt(row.getAttribute("data-line"), 10));
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
          var sel = state.cards[line] ? " is-open" : "";
          return '<tr class="' + sel + '" data-line="' + line + '"><td class="j-col-num">' + line + "</td>" + tds + "</tr>";
        }).join("");
      });
      tbody.addEventListener("click", function (e) {
        var tr = e.target.closest("tr[data-line]");
        if (tr) openCard(parseInt(tr.getAttribute("data-line"), 10));
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

    function renderMode() { if (state.mode === "table") renderTable(); else renderList(); }

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
