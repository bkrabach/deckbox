/* Deckbox — global UI behaviour: theme toggle + listing filter. */
(function () {
  "use strict";

  // ---- Theme toggle -------------------------------------------------------
  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") || "light";
  }
  function setTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("deckbox-theme", theme); } catch (e) {}
  }
  var toggle = document.querySelector(".theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      setTheme(currentTheme() === "dark" ? "light" : "dark");
    });
  }

  // ---- Listing filter -----------------------------------------------------
  var filter = document.getElementById("filter");
  if (filter) {
    var entries = Array.prototype.slice.call(document.querySelectorAll(".entry"));
    var noMatch = document.querySelector(".no-match");

    function applyFilter() {
      var q = filter.value.trim().toLowerCase();
      var visible = 0;
      entries.forEach(function (el) {
        var name = el.getAttribute("data-name") || "";
        var show = q === "" || name.indexOf(q) !== -1;
        el.hidden = !show;
        if (show) visible++;
      });
      if (noMatch) noMatch.hidden = visible !== 0;
    }

    filter.addEventListener("input", applyFilter);
    filter.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { filter.value = ""; applyFilter(); filter.blur(); }
      if (e.key === "Enter") {
        var first = entries.find(function (el) { return !el.hidden; });
        if (first) { var a = first.querySelector("a"); if (a) a.click(); }
      }
    });

    // "/" focuses the filter (unless already typing in a field).
    document.addEventListener("keydown", function (e) {
      if (e.key === "/" && document.activeElement !== filter) {
        var tag = (document.activeElement && document.activeElement.tagName) || "";
        if (tag !== "INPUT" && tag !== "TEXTAREA") { e.preventDefault(); filter.focus(); }
      }
    });
  }
})();
