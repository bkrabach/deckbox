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

    // ---- Alphabet jump nav ------------------------------------------------
    // Only worth showing on a long, well-distributed listing: it should feel
    // like a shortcut, not clutter on a handful of files.
    var rail = document.querySelector("[data-alpha-rail]");
    var ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");
    var railActive = false;
    if (rail && entries.length >= 30) {
      var present = {};             // letter -> first entry element
      entries.forEach(function (el) {
        var l = el.getAttribute("data-letter") || "#";
        if (!present[l]) present[l] = el;
      });
      var distinctAlpha = ALPHA.filter(function (l) { return present[l]; }).length;
      if (distinctAlpha >= 8) {
        railActive = true;
        var order = (present["#"] ? ["#"] : []).concat(ALPHA);
        order.forEach(function (l) {
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "alpha-key";
          btn.textContent = l;
          if (present[l]) {
            btn.addEventListener("click", function () { jumpTo(l); });
          } else {
            btn.disabled = true;
            btn.classList.add("is-empty");
          }
          rail.appendChild(btn);
        });
        rail.hidden = false;
        document.querySelector(".listing").classList.add("has-alpha-rail");

        function jumpTo(letter) {
          var el = present[letter];
          if (!el || el.hidden) return;
          fastScrollTo(el);
          // Brief highlight so the eye lands where it jumped.
          el.classList.remove("alpha-flash");
          void el.offsetWidth;
          el.classList.add("alpha-flash");
        }

        // A quick, fixed-duration smooth scroll — the native "smooth" behaviour
        // is distance-proportional and feels tediously slow across a long list.
        function fastScrollTo(el) {
          var offset = parseInt(getComputedStyle(el).scrollMarginTop, 10) || 0;
          var targetY = window.scrollY + el.getBoundingClientRect().top - offset;
          var startY = window.scrollY;
          var dist = targetY - startY;
          if (Math.abs(dist) < 2) return;
          var duration = 260;  // ms — snappy regardless of distance
          var startT = null;
          function step(t) {
            if (startT === null) startT = t;
            var p = Math.min(1, (t - startT) / duration);
            var eased = 1 - Math.pow(1 - p, 3);  // easeOutCubic
            window.scrollTo(0, startY + dist * eased);
            if (p < 1) requestAnimationFrame(step);
          }
          requestAnimationFrame(step);
        }
      }
    }

    filter.addEventListener("input", applyFilter);
    filter.addEventListener("keydown", function (e) {
      // Escape (clear + collapse) is handled by the collapsible module below.
      if (e.key === "Enter") {
        var first = entries.find(function (el) { return !el.hidden; });
        if (first) { var a = first.querySelector("a"); if (a) a.click(); }
      }
    });

    // "/" opens + focuses the filter (unless already typing in a field).
    var filterWrap = filter.closest("[data-collapsible]");
    document.addEventListener("keydown", function (e) {
      if (e.key === "/" && document.activeElement !== filter) {
        var tag = (document.activeElement && document.activeElement.tagName) || "";
        if (tag !== "INPUT" && tag !== "TEXTAREA") {
          e.preventDefault();
          if (filterWrap) filterWrap.classList.add("is-open");
          filter.focus();
        }
      }
    });
  }

  // ---- Collapsible topbar inputs (filter, go-to) --------------------------
  // Icon-only until the toggle is clicked; the input then expands and takes
  // focus. Collapses again on blur when empty — a value keeps it open so the
  // active filter / typed path stays visible.
  Array.prototype.forEach.call(document.querySelectorAll("[data-collapsible]"), function (wrap) {
    var toggle = wrap.querySelector("[data-collapse-toggle]");
    var input = wrap.querySelector("[data-collapse-input]");
    if (!toggle || !input) return;

    function openInput() {
      wrap.classList.add("is-open");
      toggle.setAttribute("aria-expanded", "true");
      input.focus();
    }
    function collapseIfEmpty() {
      if (input.value.trim() !== "") return;
      wrap.classList.remove("is-open");
      toggle.setAttribute("aria-expanded", "false");
    }

    toggle.addEventListener("click", function () {
      if (wrap.classList.contains("is-open")) {
        if (input.value.trim() === "") collapseIfEmpty();
        else input.focus();
      } else {
        openInput();
      }
    });
    // Defer so clicking the toggle (which blurs the input first) doesn't
    // collapse-then-reopen.
    input.addEventListener("blur", function () { setTimeout(collapseIfEmpty, 120); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        input.value = "";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        wrap.classList.remove("is-open");
        toggle.setAttribute("aria-expanded", "false");
        toggle.focus();
      }
    });
  });

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

  // ---- File-view copy actions: raw source + rich (formatted) text ---------
  function flashAct(btn, text, ok) {
    btn.setAttribute("data-flash", text);
    btn.classList.toggle("is-copied", ok === true);
    btn.classList.toggle("is-error", ok === false);
    // Legacy text buttons (with a .btn-label) still get their label swapped.
    var label = btn.querySelector(".btn-label");
    if (label) {
      if (btn._prevLabel == null) btn._prevLabel = label.textContent;
      label.textContent = text;
    }
    if (btn._flashT) clearTimeout(btn._flashT);
    btn._flashT = setTimeout(function () {
      btn.removeAttribute("data-flash");
      btn.classList.remove("is-copied", "is-error");
      if (label && btn._prevLabel != null) label.textContent = btn._prevLabel;
    }, 1600);
  }

  function writePlain(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (res, rej) {
      var ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.focus(); ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      ok ? res() : rej(new Error("copy failed"));
    });
  }

  // Rich copy: a real text/html clipboard payload so it pastes formatted into
  // Docs/Word/email. Falls back to selecting a live contenteditable node and
  // execCommand for insecure (plain-http over LAN) contexts.
  function writeRich(htmlStr) {
    if (navigator.clipboard && window.ClipboardItem && window.isSecureContext) {
      var tmp = document.createElement("div");
      tmp.innerHTML = htmlStr;
      var plain = tmp.innerText || tmp.textContent || "";
      var item = new ClipboardItem({
        "text/html": new Blob([htmlStr], { type: "text/html" }),
        "text/plain": new Blob([plain], { type: "text/plain" }),
      });
      return navigator.clipboard.write([item]);
    }
    return new Promise(function (res, rej) {
      var host = document.createElement("div");
      host.setAttribute("contenteditable", "true");
      host.style.position = "fixed"; host.style.left = "-9999px"; host.style.opacity = "0";
      host.innerHTML = htmlStr;
      document.body.appendChild(host);
      var range = document.createRange();
      range.selectNodeContents(host);
      var sel = window.getSelection();
      sel.removeAllRanges(); sel.addRange(range);
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      sel.removeAllRanges(); document.body.removeChild(host);
      ok ? res() : rej(new Error("rich copy failed"));
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll("[data-copy-raw]"), function (btn) {
    btn.addEventListener("click", function () {
      var url = btn.getAttribute("data-copy-raw");
      fetch(url, { headers: { "Accept": "text/plain" } })
        .then(function (r) { if (!r.ok) throw new Error("fetch failed"); return r.text(); })
        .then(function (text) { return writePlain(text); })
        .then(function () { flashAct(btn, "Copied!", true); })
        .catch(function () { flashAct(btn, "Failed", false); });
    });
  });

  Array.prototype.forEach.call(document.querySelectorAll("[data-copy-rich]"), function (btn) {
    btn.addEventListener("click", function () {
      var selector = btn.getAttribute("data-rich-selector");
      var url = btn.getAttribute("data-rich-url");
      var p;
      if (selector) {
        var el = document.querySelector(selector);
        p = el ? writeRich(el.innerHTML) : Promise.reject(new Error("no content"));
      } else if (url) {
        p = fetch(url, { headers: { "Accept": "text/html" } })
          .then(function (r) { if (!r.ok) throw new Error("fetch failed"); return r.text(); })
          .then(writeRich);
      } else {
        p = Promise.reject(new Error("no source"));
      }
      p.then(function () { flashAct(btn, "Copied!", true); })
        .catch(function () { flashAct(btn, "Failed", false); });
    });
  });

  // ---- Copy button on markdown code blocks (reveal on hover) --------------
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

  var codeBlocks = document.querySelectorAll(".markdown-body .highlight");
  Array.prototype.forEach.call(codeBlocks, function (block) {
    // The code text lives in <code> (pygments), falling back to <pre>. Line
    // numbers, when present, live in a separate .linenos cell — exclude them.
    var codeEl = block.querySelector("td:not(.linenos) code, .code code, pre code, code, pre");
    if (!codeEl) return;

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "code-copy";
    btn.setAttribute("aria-label", "Copy code");
    btn.innerHTML =
      '<svg class="code-copy-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>' +
      '<span class="code-copy-label">Copy</span>';
    block.appendChild(btn);

    var resetT = null;
    btn.addEventListener("click", function () {
      var text = (codeEl.innerText || codeEl.textContent || "").replace(/\n$/, "");
      clipboardWrite(text).then(function () {
        btn.classList.add("is-copied");
        var label = btn.querySelector(".code-copy-label");
        if (label) label.textContent = "Copied!";
        if (resetT) clearTimeout(resetT);
        resetT = setTimeout(function () {
          btn.classList.remove("is-copied");
          if (label) label.textContent = "Copy";
        }, 1500);
      }).catch(function () {
        var label = btn.querySelector(".code-copy-label");
        if (label) label.textContent = "Failed";
        if (resetT) clearTimeout(resetT);
        resetT = setTimeout(function () { if (label) label.textContent = "Copy"; }, 1500);
      });
    });
  });
})();
