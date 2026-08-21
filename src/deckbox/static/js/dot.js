/* Deckbox — interactive GraphViz DOT viewer (pan / zoom / fit / download). */
(function () {
  "use strict";

  function initViewer(viewer) {
    var stage = viewer.querySelector(".dot-stage");
    var canvas = viewer.querySelector(".dot-canvas");
    var svg = canvas ? canvas.querySelector("svg") : null;
    if (!stage || !canvas || !svg) return;

    var scale = 1, tx = 0, ty = 0;
    var min = 0.1, max = 8;

    function apply() {
      canvas.style.transform = "translate(" + tx + "px," + ty + "px) scale(" + scale + ")";
    }

    // Natural (untransformed) pixel size of the rendered SVG. We measure the
    // real rendered element rather than reading viewBox units, because the SVG
    // carries pt-based width/height and the CSS transform scales from rendered
    // pixels — mixing the two units makes Fit overshoot on wide graphs.
    function baseSize() {
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
      var pad = 32;
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

    // Wheel zoom (centered on cursor)
    stage.addEventListener("wheel", function (e) {
      e.preventDefault();
      var factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      zoomAt(factor, e.clientX, e.clientY);
    }, { passive: false });

    // Drag to pan
    var dragging = false, lastX = 0, lastY = 0;
    stage.addEventListener("pointerdown", function (e) {
      dragging = true; lastX = e.clientX; lastY = e.clientY;
      stage.classList.add("grabbing");
      stage.setPointerCapture(e.pointerId);
    });
    stage.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      tx += e.clientX - lastX; ty += e.clientY - lastY;
      lastX = e.clientX; lastY = e.clientY;
      apply();
    });
    function endDrag(e) {
      dragging = false; stage.classList.remove("grabbing");
      try { stage.releasePointerCapture(e.pointerId); } catch (err) {}
    }
    stage.addEventListener("pointerup", endDrag);
    stage.addEventListener("pointercancel", endDrag);

    // Toolbar
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
        var src = viewer.querySelector(".dot-source");
        if (src) src.hidden = !src.hidden;
      } else if (act === "download") downloadSvg(svg);
    });

    // Double-click to zoom in
    stage.addEventListener("dblclick", function (e) { zoomAt(1.5, e.clientX, e.clientY); });

    // Initial fit once layout settles.
    requestAnimationFrame(fit);
  }

  function downloadSvg(svg) {
    var clone = svg.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    var data = new XMLSerializer().serializeToString(clone);
    var blob = new Blob([data], { type: "image/svg+xml;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url; a.download = "graph.svg";
    document.body.appendChild(a); a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  document.querySelectorAll("[data-deckbox-dot]").forEach(initViewer);
})();
