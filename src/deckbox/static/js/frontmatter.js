/* Deckbox — markdown frontmatter: Table | Tree view toggle.
 *
 * The server renders the formatted Table view inline. This adds an alternative
 * structured Tree view built with the same vendored JSONViewer the JSON/JSONL
 * viewer uses, hydrated from the parsed frontmatter embedded in data-fm-json.
 */
(function () {
  "use strict";

  function init(section) {
    var toggle = section.querySelector("[data-fm-toggle]");
    var tableEl = section.querySelector("[data-fm-table]");
    var treeEl = section.querySelector("[data-fm-tree]");
    if (!toggle || !tableEl || !treeEl) return;
    if (typeof window.JSONViewer !== "function") return;  // component missing

    var raw = section.getAttribute("data-fm-json");
    if (!raw) return;
    var data;
    try { data = JSON.parse(raw); } catch (e) { return; }

    // Enable the toggle now that we can actually render a tree.
    toggle.hidden = false;
    var rendered = false;

    function setView(view) {
      var tree = view === "tree";
      if (tree && !rendered) {
        var viewer = new window.JSONViewer(treeEl, { maxTextLength: 200, forceExpand: true });
        viewer.render(data);
        rendered = true;
      }
      tableEl.hidden = tree;
      treeEl.hidden = !tree;
      toggle.querySelectorAll("[data-fm-view]").forEach(function (b) {
        b.setAttribute("aria-pressed", b.getAttribute("data-fm-view") === view ? "true" : "false");
      });
    }

    toggle.addEventListener("click", function (e) {
      var b = e.target.closest("[data-fm-view]");
      if (b) setView(b.getAttribute("data-fm-view"));
    });
  }

  document.querySelectorAll("[data-frontmatter]").forEach(init);
})();
