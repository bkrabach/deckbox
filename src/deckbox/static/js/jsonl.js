/* Deckbox — lazy JSON / JSONL viewer.
 *
 * The server holds the file; this only ever pulls small previews and
 * lazily-fetched slices via /api/jsonl*. Rows are virtualized (Clusterize),
 * each row expands into a server-driven lazy tree, deep/large nodes are
 * fetched on demand, and search scans server-side.
 */
(function () {
  "use strict";

  function initViewer(viewer) {
    var src = viewer.getAttribute("data-src") || "";
    var body = viewer.querySelector("[data-jsonl-body]");
    var countEl = viewer.querySelector("[data-jsonl-count]");
    var statusEl = viewer.querySelector("[data-jsonl-status]");
    var searchInput = viewer.querySelector(".jsonl-search");
    var searchClear = viewer.querySelector(".jsonl-search-clear");
    var viewToggle = viewer.querySelector("[data-jsonl-viewtoggle]");
    if (!body || !src) return;

    var PAGE = 100;
    var state = {
      total: 0,
      loaded: 0,          // rows fetched into `rows`
      rows: [],           // row previews, indexed by line
      expanded: {},       // line -> bool
      trees: {},          // line -> root node (fetched)
      columns: [],
      homogeneous: false,
      mode: "list",
      search: "",
      searchMatches: null, // array of line numbers when searching
      loading: false,
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

    function setStatus(msg) {
      if (!statusEl) return;
      statusEl.textContent = msg || "";
      statusEl.hidden = !msg;
    }

    // ---- escaping / formatting helpers ------------------------------------
    function esc(s) {
      return String(s).replace(/[&<>"]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
      });
    }

    function scalarHtml(node) {
      var t = node.type, v = node.value;
      if (t === "string") {
        var q = node.truncated ? '"…' : '"';
        return '<span class="j-str">"' + esc(v) + q + "</span>" +
          (node.truncated ? ' <span class="j-more">(' + node.len + " chars)</span>" : "");
      }
      if (t === "number") return '<span class="j-num">' + esc(v) + "</span>";
      if (t === "boolean") return '<span class="j-bool">' + esc(v) + "</span>";
      if (t === "null") return '<span class="j-null">null</span>';
      return esc(String(v));
    }

    function typeBadge(node) {
      if (node.type === "object") return '<span class="j-badge">{} ' + node.size + "</span>";
      if (node.type === "array") return '<span class="j-badge">[] ' + node.size + "</span>";
      return "";
    }

    // ---- lazy tree rendering ----------------------------------------------
    // A node element renders itself; containers with children render them,
    // containers without (expandable) show a chevron that fetches on click.
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
      if (isContainer) {
        val.innerHTML = typeBadge(node) + ' <span class="j-preview">' + esc(node.preview) + "</span>";
      } else {
        val.innerHTML = scalarHtml(node);
      }
      head.appendChild(val);
      wrap.appendChild(head);

      var childBox = document.createElement("div");
      childBox.className = "j-children";
      childBox.hidden = true;
      wrap.appendChild(childBox);

      var built = false;
      function renderChildren(children, stubInfo) {
        childBox.innerHTML = "";
        children.forEach(function (c) { childBox.appendChild(nodeEl(line, c)); });
        if (stubInfo && stubInfo.total > stubInfo.shown) {
          var more = document.createElement("div");
          more.className = "j-more-row";
          more.textContent = "… " + (stubInfo.total - stubInfo.shown) + " more not shown";
          childBox.appendChild(more);
        }
        built = true;
      }
      // Fully-inlined children returned by the server.
      if (isContainer && node.children) { renderChildren(node.children); built = true; }
      // Not inlined, but the server handed us one-level child stubs — render
      // them now (each is itself lazily expandable). This is what lets a huge
      // row (>16KB) be drilled into instead of showing "(empty)".
      else if (isContainer && node.child_stubs) {
        renderChildren(node.child_stubs.items, node.child_stubs);
        built = true;
      }

      if (isContainer && node.size > 0) {
        var chevBtn = head.querySelector(".j-chevron");
        var open = false;
        chevBtn.addEventListener("click", function () {
          open = !open;
          chevBtn.classList.toggle("open", open);
          chevBtn.innerHTML = open ? "▾" : "▸";
          childBox.hidden = !open;
          if (open && !built) {
            childBox.innerHTML = '<div class="j-fetching">…</div>';
            api("/api/jsonl/node", { path: src, line: line, pointer: node.pointer })
              .then(function (res) {
                if (res.ok && res.j.children) renderChildren(res.j.children);
                else if (res.ok && res.j.child_stubs) renderChildren(res.j.child_stubs.items, res.j.child_stubs);
                else if (res.ok) { childBox.innerHTML = '<div class="j-fetching">(empty)</div>'; }
                else childBox.innerHTML = '<div class="j-fetching">failed to load</div>';
              });
          }
        });
      }
      return wrap;
    }

    // ---- row list (virtualized via Clusterize) ----------------------------
    var clusterize = null;

    function rowLineNumbers() {
      if (state.searchMatches) return state.searchMatches;
      var arr = [];
      for (var i = 0; i < state.total; i++) arr.push(i);
      return arr;
    }

    function rowPreviewHtml(line) {
      var r = state.rows[line];
      if (!r) {
        return '<div class="jsonl-row is-stub" data-line="' + line + '">' +
          '<span class="jsonl-row-num">' + line + '</span>' +
          '<span class="jsonl-row-preview j-loading-row">…</span></div>';
      }
      var badge = "";
      if (r.type === "object") badge = '<span class="j-badge">{} ' + (r.count || 0) + "</span>";
      else if (r.type === "array") badge = '<span class="j-badge">[] ' + (r.count || 0) + "</span>";
      else if (r.type === "error") badge = '<span class="j-badge j-badge-err">!</span>';
      var chev = r.expandable ? '<span class="jsonl-row-chev">▸</span>' : '<span class="jsonl-row-chev spacer"></span>';
      return '<div class="jsonl-row" data-line="' + line + '">' +
        chev +
        '<span class="jsonl-row-num">' + line + '</span>' +
        badge +
        '<span class="jsonl-row-preview">' + esc(r.preview || "") + "</span>" +
        '</div>';
    }

    function buildRowsHtml() {
      var lines = rowLineNumbers();
      return lines.map(function (line) { return rowPreviewHtml(line); });
    }

    function refreshList() {
      if (!clusterize) return;
      clusterize.update(buildRowsHtml());
    }

    // Ensure previews for a set of lines are loaded (batch fetch by range).
    function ensureRows(lines, cb) {
      var missing = lines.filter(function (l) { return !state.rows[l]; });
      if (!missing.length) { cb && cb(); return; }
      // Fetch a contiguous window around the missing block.
      var lo = Math.min.apply(null, missing);
      var hi = Math.max.apply(null, missing);
      api("/api/jsonl", { path: src, offset: lo, limit: Math.min(500, hi - lo + 1) })
        .then(function (res) {
          if (res.ok && res.j.rows) {
            res.j.rows.forEach(function (row) { state.rows[row.line] = row; });
          }
          cb && cb();
        });
    }

    // ---- expand a row inline (below its preview) --------------------------
    function toggleRow(line, rowEl) {
      var existing = rowEl.nextElementSibling;
      if (existing && existing.classList.contains("jsonl-row-detail")) {
        existing.parentNode.removeChild(existing);
        rowEl.classList.remove("is-open");
        return;
      }
      rowEl.classList.add("is-open");
      var detail = document.createElement("div");
      detail.className = "jsonl-row-detail";
      detail.innerHTML =
        '<div class="jsonl-row-actions">' +
        '<button type="button" data-row-act="copy">Copy row</button>' +
        '<button type="button" data-row-act="pretty">Pretty</button>' +
        '<button type="button" data-row-act="raw">Raw</button>' +
        "</div>" +
        '<div class="jsonl-tree"><div class="j-fetching">…</div></div>';
      rowEl.parentNode.insertBefore(detail, rowEl.nextSibling);

      var treeBox = detail.querySelector(".jsonl-tree");
      api("/api/jsonl/node", { path: src, line: line, pointer: "" }).then(function (res) {
        treeBox.innerHTML = "";
        if (!res.ok) { treeBox.innerHTML = '<div class="j-fetching">failed to load</div>'; return; }
        var node = res.j;
        if (node.type === "object" || node.type === "array") {
          if (node.children) {
            node.children.forEach(function (c) { treeBox.appendChild(nodeEl(line, c)); });
          } else {
            // large root: render its own expandable node
            treeBox.appendChild(nodeEl(line, node));
          }
        } else {
          treeBox.appendChild(nodeEl(line, node));
        }
      });

      detail.addEventListener("click", function (e) {
        var b = e.target.closest("[data-row-act]");
        if (!b) return;
        var act = b.getAttribute("data-row-act");
        if (act === "copy") {
          api("/api/jsonl/row", { path: src, line: line, format: "raw" }).then(function (res) {
            if (res.ok) copyText(res.j.text, b);
          });
        } else {
          var fmt = act; // pretty | raw
          api("/api/jsonl/row", { path: src, line: line, format: fmt }).then(function (res) {
            if (!res.ok) return;
            var pre = detail.querySelector(".jsonl-rawpre");
            if (!pre) {
              pre = document.createElement("pre");
              pre.className = "jsonl-rawpre";
              detail.appendChild(pre);
            }
            pre.textContent = res.j.text;
            pre.hidden = false;
          });
        }
      });
    }

    // ---- table view -------------------------------------------------------
    function buildTable() {
      var cols = state.columns.slice(0, 24);
      var wrap = document.createElement("div");
      wrap.className = "jsonl-table-wrap";
      var table = document.createElement("table");
      table.className = "jsonl-table";
      var thead = "<thead><tr><th class=\"j-col-num\">#</th>" +
        cols.map(function (c) { return "<th>" + esc(c) + "</th>"; }).join("") +
        "</tr></thead>";
      table.innerHTML = thead + "<tbody></tbody>";
      wrap.appendChild(table);
      var tbody = table.querySelector("tbody");

      var lines = rowLineNumbers();
      var shown = Math.min(lines.length, 500);
      ensureRows(lines.slice(0, shown), function () {
        var htmlRows = [];
        for (var i = 0; i < shown; i++) {
          var line = lines[i];
          var r = state.rows[line] || {};
          var fields = r.fields || {};
          var tds = cols.map(function (c) {
            var v = fields[c];
            return "<td>" + (v === undefined ? '<span class="j-empty">—</span>' : esc(String(v))) + "</td>";
          }).join("");
          htmlRows.push('<tr data-line="' + line + '"><td class="j-col-num">' + line + "</td>" + tds + "</tr>");
        }
        tbody.innerHTML = htmlRows.join("");
        if (lines.length > shown) {
          var note = document.createElement("div");
          note.className = "jsonl-table-note";
          note.textContent = "Showing first " + shown + " of " + lines.length + " rows. Use search or list view for more.";
          wrap.appendChild(note);
        }
      });

      tbody.addEventListener("click", function (e) {
        var tr = e.target.closest("tr[data-line]");
        if (!tr) return;
        var line = parseInt(tr.getAttribute("data-line"), 10);
        var next = tr.nextElementSibling;
        if (next && next.classList.contains("jsonl-table-detail")) {
          next.parentNode.removeChild(next); return;
        }
        var dr = document.createElement("tr");
        dr.className = "jsonl-table-detail";
        dr.innerHTML = '<td colspan="' + (cols.length + 1) + '"><div class="jsonl-tree"><div class="j-fetching">…</div></div></td>';
        tr.parentNode.insertBefore(dr, tr.nextSibling);
        var treeBox = dr.querySelector(".jsonl-tree");
        api("/api/jsonl/node", { path: src, line: line, pointer: "" }).then(function (res) {
          treeBox.innerHTML = "";
          if (!res.ok) return;
          var node = res.j;
          if ((node.type === "object" || node.type === "array") && node.children) {
            node.children.forEach(function (c) { treeBox.appendChild(nodeEl(line, c)); });
          } else { treeBox.appendChild(nodeEl(line, node)); }
        });
      });
      return wrap;
    }

    // ---- mode switching ---------------------------------------------------
    function renderMode() {
      body.innerHTML = "";
      if (state.mode === "table") {
        body.appendChild(buildTable());
        return;
      }
      // list view: clusterize scroll + content
      var scroll = document.createElement("div");
      scroll.className = "jsonl-scroll clusterize-scroll";
      var content = document.createElement("div");
      content.className = "jsonl-rows clusterize-content";
      content.innerHTML = '<div class="clusterize-no-data">Loading…</div>';
      scroll.appendChild(content);
      body.appendChild(scroll);

      clusterize = new Clusterize({
        rows: buildRowsHtml(),
        scrollElem: scroll,
        contentElem: content,
        rows_in_block: 30,
        callbacks: {
          clusterChanged: function () {
            // Lazy-load previews for rows that became visible as stubs.
            var stubs = content.querySelectorAll(".jsonl-row.is-stub");
            if (!stubs.length) return;
            var lines = Array.prototype.map.call(stubs, function (el) {
              return parseInt(el.getAttribute("data-line"), 10);
            });
            ensureRows(lines, function () { refreshList(); });
          },
        },
      });

      // Row click -> expand/collapse detail
      content.addEventListener("click", function (e) {
        var row = e.target.closest(".jsonl-row");
        if (!row || row.classList.contains("is-stub")) return;
        var line = parseInt(row.getAttribute("data-line"), 10);
        var r = state.rows[line];
        if (!r || !r.expandable) return;
        var chev = row.querySelector(".jsonl-row-chev");
        if (chev) chev.textContent = row.classList.contains("is-open") ? "▸" : "▾";
        toggleRow(line, row);
      });
    }

    // ---- search -----------------------------------------------------------
    var searchTimer = null;
    function runSearch(q) {
      state.search = q;
      if (searchClear) searchClear.hidden = !q;
      if (!q) {
        state.searchMatches = null;
        updateCount();
        renderMode();
        return;
      }
      setStatus("Searching…");
      api("/api/jsonl/search", { path: src, q: q, offset: 0, limit: 200 }).then(function (res) {
        if (!res.ok) { setStatus("search failed"); return; }
        state.searchMatches = res.j.matches || [];
        (res.j.rows || []).forEach(function (row) { state.rows[row.line] = row; });
        setStatus("");
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
    if (searchClear) {
      searchClear.addEventListener("click", function () {
        searchInput.value = ""; runSearch("");
      });
    }

    function updateCount() {
      if (!countEl) return;
      if (state.searchMatches) {
        countEl.textContent = state.searchMatches.length + " / " + state.total + " rows";
      } else {
        countEl.textContent = state.total.toLocaleString() + " rows";
      }
    }

    // ---- view toggle ------------------------------------------------------
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
    setStatus("");
    api("/api/jsonl", { path: src, offset: 0, limit: PAGE }).then(function (res) {
      if (!res.ok) {
        body.innerHTML = '<div class="jsonl-error">' +
          esc((res.j && res.j.detail) || "Could not load this file.") + "</div>";
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
