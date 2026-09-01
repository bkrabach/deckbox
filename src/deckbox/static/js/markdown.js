/* Deckbox — markdown viewer: Rendered | Source toggle.
 *
 * The server renders the formatted markdown inline plus the raw source in a
 * <pre>. This wires the toolbar toggle between the two. Copy (rich + raw) and
 * download live in the file-view toolbar (app.js), not here.
 */
(function () {
  "use strict";

  function init(viewer) {
    var toggle = viewer.querySelector("[data-md-toggle]");
    var rendered = viewer.querySelector("[data-md-rendered]");
    var source = viewer.querySelector("[data-md-source]");
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
  }

  document.querySelectorAll("[data-md-viewer]").forEach(init);
})();
