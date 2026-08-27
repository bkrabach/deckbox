/* Deckbox — markdown viewer: Rendered | Source toggle + copy.
 *
 * The server renders the formatted markdown inline plus the raw source in a
 * <pre>. This wires the toolbar toggle between the two and a Copy button that
 * copies the raw markdown source.
 */
(function () {
  "use strict";

  function clipboardWrite(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.focus(); ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      ok ? resolve() : reject(new Error("copy failed"));
    });
  }

  function init(viewer) {
    var toggle = viewer.querySelector("[data-md-toggle]");
    var rendered = viewer.querySelector("[data-md-rendered]");
    var source = viewer.querySelector("[data-md-source]");
    var copyBtn = viewer.querySelector("[data-md-copy]");
    if (!toggle || !rendered || !source) return;

    function setView(view) {
      var showSource = view === "source";
      rendered.hidden = showSource;
      source.hidden = !showSource;
      toggle.querySelectorAll("[data-md-view]").forEach(function (b) {
        b.setAttribute("aria-pressed", b.getAttribute("data-md-view") === view ? "true" : "false");
      });
    }

    toggle.addEventListener("click", function (e) {
      var b = e.target.closest("[data-md-view]");
      if (b) setView(b.getAttribute("data-md-view"));
    });

    if (copyBtn) {
      var resetT = null;
      copyBtn.addEventListener("click", function () {
        clipboardWrite(source.textContent || "").then(function () {
          copyBtn.textContent = "Copied!";
          if (resetT) clearTimeout(resetT);
          resetT = setTimeout(function () { copyBtn.textContent = "Copy"; }, 1500);
        }).catch(function () {
          copyBtn.textContent = "Failed";
          if (resetT) clearTimeout(resetT);
          resetT = setTimeout(function () { copyBtn.textContent = "Copy"; }, 1500);
        });
      });
    }
  }

  document.querySelectorAll("[data-md-viewer]").forEach(init);
})();
