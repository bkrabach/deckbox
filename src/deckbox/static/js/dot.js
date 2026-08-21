/* Deckbox — interactive GraphViz DOT viewer.
 * Pan / zoom / fit, selectable layout engine + direction, and a node inspector.
 */
(function () {
  "use strict";

  function initViewer(viewer) {
    var stage = viewer.querySelector(".dot-stage");
    var canvas = viewer.querySelector(".dot-canvas");
    var inspector = viewer.querySelector(".dot-inspector");
    var loading = viewer.querySelector(".dot-loading");
    var engineSel = viewer.querySelector('[data-ctl="engine"]');
    var rankdirSel = viewer.querySelector('[data-ctl="rankdir"]');
    var rankdirWrap = viewer.querySelector('[data-ctl-wrap="rankdir"]');
    if (!stage || !canvas) return;

    var src = viewer.getAttribute("data-src") || "";
    var nodes = parseNodes(viewer.getAttribute("data-nodes"));
    var scale = 1, tx = 0, ty = 0;
    var min = 0.05, max = 8;
    var selected = null;

    function svgEl() { return canvas.querySelector("svg"); }

    function apply() {
      canvas.style.transform = "translate(" + tx + "px," + ty + "px) scale(" + scale + ")";
    }

    // Natural (untransformed) pixel size of the rendered SVG.
    function baseSize() {
      var svg = svgEl();
      if (!svg) return { w: 1, h: 1 };
      var prev = canvas.style.transform;
      canvas.style.transform = "none";
      var r = svg.getBoundingClientRect();
      canvas.style.transform = prev;
      if (r.width && r.height) return { w: r.width, h: r.height };
      var vb = svg.viewBox && svg.viewBox.baseVal;
      if (vb && vb.width) return { w: vb.width, h: vb.height };
      return { w: 1, h: 1 };
    }

    function fit() {
      var sz = baseSize();
      var rect = stage.getBoundingClientRect();
      var pad = 40;
      var s = Math.min((rect.width - pad) / sz.w, (rect.height - pad) / sz.h);
      scale = Math.max(min, Math.min(max, s || 1));
      tx = (rect.width - sz.w * scale) / 2;
      ty = (rect.height - sz.h * scale) / 2;
      apply();
    }

    function reset() {
      scale = 1;
      var sz = baseSize();
      var rect = stage.getBoundingClientRect();
      tx = (rect.width - sz.w) / 2;
      ty = (rect.height - sz.h) / 2;
      apply();
    }

    function zoomAt(factor, cx, cy) {
      var next = Math.max(min, Math.min(max, scale * factor));
      var rect = stage.getBoundingClientRect();
      var px = (cx - rect.left - tx) / scale;
      var py = (cy - rect.top - ty) / scale;
      scale = next;
      tx = cx - rect.left - px * scale;
      ty = cy - rect.top - py * scale;
      apply();
    }

    // ---- Node inspector ---------------------------------------------------
    function nodeNameFromEl(g) {
      var title = g.querySelector("title");
      return title ? title.textContent.trim() : "";
    }

    function clearSelection() {
      if (selected) { selected.classList.remove("dot-node-selected"); selected = null; }
    }

    function selectNode(g) {
      clearSelection();
      selected = g;
      g.classList.add("dot-node-selected");
      var name = nodeNameFromEl(g);
      showInspector(name, nodes[name] || {});
    }

    function showInspector(name, attrs) {
      if (!inspector) return;
      var titleEl = inspector.querySelector(".dot-inspector-title");
      var bodyEl = inspector.querySelector(".dot-inspector-body");
      var hintEl = inspector.querySelector(".dot-inspector-hint");
      if (titleEl) titleEl.textContent = name || "Node";
      if (hintEl) hintEl.hidden = true;
      bodyEl.innerHTML = "";

      var keys = Object.keys(attrs);
      // Preferred order: the human-meaningful fields first.
      var order = ["label", "prompt", "tool_command", "condition", "goal",
                   "shape", "model", "fidelity", "thread_id", "max_retries", "weight"];
      keys.sort(function (a, b) {
        var ia = order.indexOf(a), ib = order.indexOf(b);
        if (ia === -1) ia = 999; if (ib === -1) ib = 999;
        return ia - ib || a.localeCompare(b);
      });

      if (!keys.length) {
        var empty = document.createElement("p");
        empty.className = "dot-attr-empty";
        empty.textContent = "No attributes on this node.";
        bodyEl.appendChild(empty);
      }
      keys.forEach(function (k) {
        var row = document.createElement("div");
        row.className = "dot-attr";
        var kEl = document.createElement("div");
        kEl.className = "dot-attr-key";
        kEl.textContent = k;
        var vEl = document.createElement("div");
        vEl.className = "dot-attr-val";
        // Long / code-ish values get a <pre> for readability.
        var val = String(attrs[k]);
        if (k === "tool_command" || k === "prompt" || val.length > 60 || val.indexOf("\n") !== -1) {
          var pre = document.createElement("pre");
          pre.textContent = val;
          if (k === "tool_command") pre.classList.add("is-code");
          vEl.appendChild(pre);
        } else {
          vEl.textContent = val;
        }
        row.appendChild(kEl); row.appendChild(vEl);
        bodyEl.appendChild(row);
      });
      inspector.hidden = false;
    }

    function closeInspector() {
      if (inspector) inspector.hidden = true;
      clearSelection();
    }

    function bindNodes() {
      var svg = svgEl();
      if (!svg) return;
      svg.querySelectorAll("g.node").forEach(function (g) {
        g.classList.add("dot-node");
        g.addEventListener("click", function (e) {
          e.stopPropagation();
          selectNode(g);
        });
      });
    }

    // ---- Interaction ------------------------------------------------------
    stage.addEventListener("wheel", function (e) {
      e.preventDefault();
      zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX, e.clientY);
    }, { passive: false });

    // Pan by dragging. Critically, we only capture the pointer AFTER movement
    // crosses a small threshold — capturing on pointerdown would retarget the
    // subsequent click to the stage and swallow node clicks (breaking the
    // inspector for real users). A plain click never captures, so it reaches
    // the node's own click handler naturally.
    var pointerDown = false, dragging = false, moved = false;
    var lastX = 0, lastY = 0, startX = 0, startY = 0, capturedId = null;

    stage.addEventListener("pointerdown", function (e) {
      pointerDown = true; dragging = false; moved = false;
      startX = lastX = e.clientX; startY = lastY = e.clientY;
    });
    stage.addEventListener("pointermove", function (e) {
      if (!pointerDown) return;
      if (!dragging) {
        if (Math.abs(e.clientX - startX) + Math.abs(e.clientY - startY) <= 3) return;
        dragging = true; moved = true;
        stage.classList.add("grabbing");
        try { stage.setPointerCapture(e.pointerId); capturedId = e.pointerId; } catch (err) {}
        lastX = e.clientX; lastY = e.clientY;
        return;
      }
      tx += e.clientX - lastX; ty += e.clientY - lastY;
      lastX = e.clientX; lastY = e.clientY;
      apply();
    });
    function endDrag() {
      pointerDown = false; dragging = false; stage.classList.remove("grabbing");
      if (capturedId !== null) {
        try { stage.releasePointerCapture(capturedId); } catch (err) {}
        capturedId = null;
      }
    }
    stage.addEventListener("pointerup", endDrag);
    stage.addEventListener("pointercancel", endDrag);

    // Click on empty stage clears selection (but not after a drag).
    stage.addEventListener("click", function (e) {
      if (moved) return;
      if (!e.target.closest("g.node")) closeInspector();
    });
    stage.addEventListener("dblclick", function (e) { zoomAt(1.5, e.clientX, e.clientY); });

    // Toolbar buttons
    viewer.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-act]");
      if (!btn) return;
      var act = btn.getAttribute("data-act");
      var rect = stage.getBoundingClientRect();
      var cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
      if (act === "zoom-in") zoomAt(1.25, cx, cy);
      else if (act === "zoom-out") zoomAt(1 / 1.25, cx, cy);
      else if (act === "fit") fit();
      else if (act === "reset") reset();
      else if (act === "toggle-source") {
        var s = viewer.querySelector(".dot-source");
        if (s) s.hidden = !s.hidden;
      } else if (act === "download") downloadSvg(svgEl());
      else if (act === "close-inspector") closeInspector();
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeInspector();
    });

    // ---- Layout controls (re-render on the server) ------------------------
    function syncRankdirVisibility() {
      if (rankdirWrap) rankdirWrap.style.display = (engineSel && engineSel.value === "dot") ? "" : "none";
    }

    function relayout() {
      if (!src) return;
      var engine = engineSel ? engineSel.value : "dot";
      var rankdir = rankdirSel ? rankdirSel.value : "TB";
      if (loading) loading.hidden = false;
      var url = "/api/dot?path=" + encodeURIComponent(src) +
                "&engine=" + encodeURIComponent(engine) +
                "&rankdir=" + encodeURIComponent(rankdir);
      fetch(url, { headers: { "Accept": "application/json" } })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (!res.ok || res.j.error) {
            if (loading) { loading.hidden = false; loading.textContent = "render failed"; }
            return;
          }
          canvas.innerHTML = res.j.svg;
          if (res.j.nodes) nodes = res.j.nodes;
          closeInspector();
          bindNodes();
          requestAnimationFrame(fit);
          if (loading) { loading.hidden = true; loading.textContent = "rendering…"; }
        })
        .catch(function () {
          if (loading) { loading.hidden = false; loading.textContent = "render failed"; }
        });
    }

    if (engineSel) engineSel.addEventListener("change", function () {
      syncRankdirVisibility(); relayout();
    });
    if (rankdirSel) rankdirSel.addEventListener("change", relayout);

    // ---- Init -------------------------------------------------------------
    syncRankdirVisibility();
    bindNodes();
    requestAnimationFrame(fit);
  }

  function parseNodes(attr) {
    if (!attr) return {};
    try { return JSON.parse(attr) || {}; } catch (e) { return {}; }
  }

  function downloadSvg(svg) {
    if (!svg) return;
    var clone = svg.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    var data = new XMLSerializer().serializeToString(clone);
    var blob = new Blob([data], { type: "image/svg+xml;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url; a.download = "graph.svg";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  document.querySelectorAll("[data-deckbox-dot]").forEach(initViewer);
})();
