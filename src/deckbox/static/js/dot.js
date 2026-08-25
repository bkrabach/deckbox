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

    // Run a layout mutation (e.g. opening/closing the inspector, which shrinks
    // or grows the stage) while keeping whatever content was at the centre of
    // the stage centred. When the stage width changes by Δ, shifting the pan by
    // Δ/2 holds the centre point fixed — so the content the user was looking at
    // stays put instead of the view sliding sideways.
    function recenterAround(mutate) {
      var before = stage.getBoundingClientRect();
      mutate();
      var after = stage.getBoundingClientRect();
      var dw = after.width - before.width;
      var dh = after.height - before.height;
      if (dw || dh) {
        tx += dw / 2;
        ty += dh / 2;
        apply();
      }
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
      // Opening the inspector (if it was hidden) shrinks the stage; keep the
      // centred content centred. Switching between nodes leaves it visible, so
      // there is no width change and no shift.
      recenterAround(function () { showInspector(name, nodes[name] || {}); });
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
        // Long / code-ish values get a <pre> for readability. DOT attribute
        // values carry escapes as literal backslash sequences (\n, \t) — turn
        // them into real newlines/tabs so prompts read as authored.
        var val = String(attrs[k]).replace(/\\r\\n/g, "\n").replace(/\\n/g, "\n").replace(/\\t/g, "\t");
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
      // Let oversized <pre> boxes grow into any spare vertical space, sharing
      // it fairly when several are oversized. Deferred so layout is settled.
      requestAnimationFrame(function () { distributePreHeights(bodyEl); });
    }

    // Grow attribute <pre> boxes to fit their content when the inspector body
    // has room; if the combined natural height overflows, share the available
    // space fairly (water-filling) so no single box hogs it, and each box only
    // scrolls once it has been given as much as its fair share allows.
    var MIN_PRE = 90;   // don't shrink a box below this when sharing
    function distributePreHeights(bodyEl) {
      var pres = Array.prototype.slice.call(bodyEl.querySelectorAll(".dot-attr-val pre"));
      if (!pres.length) return;

      // Reset to natural measurement: cap removed so scrollHeight is the true
      // content height.
      pres.forEach(function (pre) { pre.style.maxHeight = "none"; pre.style.height = "auto"; });

      // Space the body can show without scrolling = its clientHeight. Height
      // taken by everything that is NOT the growable pre boxes (keys, labels,
      // padding, borders, other rows) is fixed; the pre boxes divide the rest.
      var avail = bodyEl.clientHeight;
      if (avail <= 0) return;
      var nonPre = bodyEl.scrollHeight;  // full natural height of all content
      pres.forEach(function (pre) { nonPre -= pre.offsetHeight; });
      var budget = avail - nonPre;       // vertical space the pre boxes may fill

      // Each box's desired (natural) height, including its own border/padding.
      var need = pres.map(function (pre) {
        return pre.scrollHeight + (pre.offsetHeight - pre.clientHeight);
      });
      var totalNeed = need.reduce(function (a, b) { return a + b; }, 0);

      if (budget <= 0) {
        // No spare room at all — fall back to the fixed cap so the body scrolls.
        pres.forEach(function (pre) { pre.style.maxHeight = "260px"; pre.style.height = "auto"; });
        return;
      }
      if (totalNeed <= budget) {
        // Everything fits: let each box be exactly its content height.
        pres.forEach(function (pre, i) { pre.style.maxHeight = need[i] + "px"; });
        return;
      }

      // Not enough room for everyone: water-fill. Boxes that need less than an
      // equal share are satisfied fully; the surplus is shared among the rest,
      // repeated until stable. Each capped box then scrolls within its share.
      var remaining = budget;
      var unsettled = pres.map(function (_, i) { return i; });
      var alloc = need.slice();
      var changed = true;
      while (changed && unsettled.length) {
        changed = false;
        var share = remaining / unsettled.length;
        var stillUnsettled = [];
        unsettled.forEach(function (i) {
          if (need[i] <= share) { alloc[i] = need[i]; remaining -= need[i]; changed = true; }
          else { stillUnsettled.push(i); }
        });
        if (!changed) {
          // Split what's left equally among the remaining boxes (>= a floor).
          var each = Math.max(MIN_PRE, remaining / stillUnsettled.length);
          stillUnsettled.forEach(function (i) { alloc[i] = each; });
        }
        unsettled = stillUnsettled;
      }
      pres.forEach(function (pre, i) { pre.style.maxHeight = Math.round(alloc[i]) + "px"; });
    }

    function closeInspector() {
      if (inspector && !inspector.hidden) {
        recenterAround(function () { inspector.hidden = true; });
      }
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
      } else if (act === "download") downloadSvg(svgEl(), exportBase(src));
      else if (act === "download-png") downloadPng(svgEl(), exportBase(src), btn);
      else if (act === "copy-png") copyPng(svgEl(), btn);
      else if (act === "close-inspector") closeInspector();
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeInspector();
    });

    // Re-share the inspector's vertical space when the viewport height changes.
    var resizeT = null;
    window.addEventListener("resize", function () {
      if (!inspector || inspector.hidden) return;
      var bodyEl = inspector.querySelector(".dot-inspector-body");
      if (!bodyEl) return;
      clearTimeout(resizeT);
      resizeT = setTimeout(function () { distributePreHeights(bodyEl); }, 120);
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
    // Copy-to-clipboard as an image only works in a secure context (which
    // browsers grant for HTTPS and for localhost / 127.0.0.1) with ClipboardItem
    // support. Where it can't work, hide the button rather than offer a dead one.
    if (!(window.isSecureContext && navigator.clipboard && navigator.clipboard.write && window.ClipboardItem)) {
      var copyBtn = viewer.querySelector('[data-act="copy-png"]');
      if (copyBtn) copyBtn.hidden = true;
    }

    syncRankdirVisibility();
    bindNodes();
    requestAnimationFrame(fit);
  }

  function parseNodes(attr) {
    if (!attr) return {};
    try { return JSON.parse(attr) || {}; } catch (e) { return {}; }
  }

  // Export base name = the source file's name with its extension dropped, so
  // 02-plan-implement-test.dot exports as 02-plan-implement-test.{png,svg}.
  function exportBase(src) {
    var last = String(src || "").split("/").pop() || "graph";
    try { last = decodeURIComponent(last); } catch (e) {}
    return last.replace(/\.[^.]+$/, "") || "graph";
  }

  function triggerDownload(blob, filename) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function downloadSvg(svg, name) {
    if (!svg) return;
    var clone = svg.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    var data = new XMLSerializer().serializeToString(clone);
    triggerDownload(new Blob([data], { type: "image/svg+xml;charset=utf-8" }), name + ".svg");
  }

  // Rasterise the graph's SVG to a PNG blob at ~2x for crisp output. Uses the
  // SVG's own width/height (its intrinsic size), not the on-screen zoom, so the
  // export is the full diagram regardless of pan/zoom state.
  function svgToPng(svg, scale, cb) {
    if (!svg) { cb(null); return; }
    scale = scale || 2;
    var clone = svg.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");

    // Determine intrinsic pixel size from width/height or the viewBox.
    var w = parseFloat(clone.getAttribute("width")) || 0;
    var h = parseFloat(clone.getAttribute("height")) || 0;
    var vb = (clone.getAttribute("viewBox") || "").split(/[ ,]+/).map(parseFloat);
    if ((!w || !h) && vb.length === 4) { w = w || vb[2]; h = h || vb[3]; }
    if (!w || !h) { var r = svg.getBoundingClientRect(); w = r.width; h = r.height; }
    // Give the export a solid background (the on-screen stage is white).
    var bg = getComputedStyle(document.querySelector(".dot-stage") || document.body).backgroundColor;

    var data = new XMLSerializer().serializeToString(clone);
    var svgUrl = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(data);
    var img = new Image();
    img.onload = function () {
      var canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(w * scale));
      canvas.height = Math.max(1, Math.round(h * scale));
      var ctx = canvas.getContext("2d");
      if (bg && bg !== "rgba(0, 0, 0, 0)" && bg !== "transparent") {
        ctx.fillStyle = bg;
      } else {
        ctx.fillStyle = "#ffffff";
      }
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(function (blob) { cb(blob); }, "image/png");
    };
    img.onerror = function () { cb(null); };
    img.src = svgUrl;
  }

  function downloadPng(svg, name, btn) {
    svgToPng(svg, 2, function (blob) {
      if (!blob) { flashBtn(btn, "Failed"); return; }
      triggerDownload(blob, name + ".png");
    });
  }

  // Copy the diagram to the clipboard as a real PNG image. No download fallback:
  // if the clipboard image API isn't available or is blocked, say so plainly.
  //
  // Safari requires the ClipboardItem to be constructed synchronously from a
  // Promise<Blob> (async blob generation inside a later .write() loses the user
  // gesture). We pass a promise that resolves to the rasterised PNG, which both
  // Chromium and Safari accept and which keeps the write within the gesture.
  function copyPng(svg, btn) {
    if (!svg) { flashBtn(btn, "Failed"); return; }
    if (!(navigator.clipboard && navigator.clipboard.write && window.ClipboardItem)) {
      flashBtn(btn, window.isSecureContext ? "Unsupported" : "Needs HTTPS");
      return;
    }
    var pngPromise = new Promise(function (resolve, reject) {
      svgToPng(svg, 2, function (blob) { blob ? resolve(blob) : reject(new Error("render failed")); });
    });
    Promise.resolve()
      .then(function () {
        return navigator.clipboard.write([new window.ClipboardItem({ "image/png": pngPromise })]);
      })
      .then(function () { flashBtn(btn, "Copied!"); })
      .catch(function (err) {
        // A denied permission surfaces here as a NotAllowedError.
        var msg = (err && err.name === "NotAllowedError") ? "Denied" : "Failed";
        flashBtn(btn, msg);
      });
  }

  // Briefly show a status label on a toolbar button, then restore it.
  function flashBtn(btn, text) {
    if (!btn) return;
    if (btn._flashT) clearTimeout(btn._flashT);
    if (btn._label === undefined) btn._label = btn.textContent;
    btn.textContent = text;
    btn._flashT = setTimeout(function () { btn.textContent = btn._label; }, 1500);
  }

  document.querySelectorAll("[data-deckbox-dot]").forEach(initViewer);
})();
