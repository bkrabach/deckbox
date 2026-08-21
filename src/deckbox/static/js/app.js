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

  // ---- Content width toggle (file view) -----------------------------------
  var fileview = document.querySelector(".fileview[data-width]");
  var toggle = document.querySelector("[data-width-toggle]");
  if (fileview && toggle) {
    var buttons = Array.prototype.slice.call(toggle.querySelectorAll("[data-width-set]"));

    function applyWidth(mode) {
      fileview.setAttribute("data-width", mode);
      buttons.forEach(function (b) {
        b.setAttribute("aria-pressed", b.getAttribute("data-width-set") === mode ? "true" : "false");
      });
    }

    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        applyWidth(b.getAttribute("data-width-set"));
      });
    });

    // Reflect the server-provided per-kind default in the control state.
    applyWidth(fileview.getAttribute("data-width") || "readable");
  }

  // ---- Copy raw contents to clipboard -------------------------------------
  var copyBtn = document.querySelector("[data-copy-raw]");
  if (copyBtn) {
    var labelEl = copyBtn.querySelector(".btn-label");
    var defaultLabel = labelEl ? labelEl.textContent : "Copy";
    var resetTimer = null;

    function flash(text, ok) {
      if (labelEl) labelEl.textContent = text;
      copyBtn.classList.toggle("is-copied", ok === true);
      copyBtn.classList.toggle("is-error", ok === false);
      if (resetTimer) clearTimeout(resetTimer);
      resetTimer = setTimeout(function () {
        if (labelEl) labelEl.textContent = defaultLabel;
        copyBtn.classList.remove("is-copied", "is-error");
      }, 1600);
    }

    async function writeClipboard(text) {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return;
      }
      // Fallback for non-secure contexts (plain http over LAN).
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.focus(); ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      if (!ok) throw new Error("copy command failed");
    }

    copyBtn.addEventListener("click", function () {
      var url = copyBtn.getAttribute("data-copy-raw");
      fetch(url, { headers: { "Accept": "text/plain" } })
        .then(function (r) {
          if (!r.ok) throw new Error("fetch failed");
          return r.text();
        })
        .then(function (text) { return writeClipboard(text); })
        .then(function () { flash("Copied!", true); })
        .catch(function () { flash("Failed", false); });
    });
  }
})();
