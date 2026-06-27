/* Sports Meet — UI interactions: theme, active nav, counters, scroll reveal. */
(function () {
  "use strict";
  document.body.classList.add("js");

  // ---- Theme toggle -------------------------------------------------------
  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") || "light";
  }
  function setThemeIcon(btn) {
    if (btn) btn.textContent = currentTheme() === "dark" ? "☀️" : "🌙";
  }
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("theme", t); } catch (e) {}
    document.querySelectorAll("#themeToggle").forEach(setThemeIcon);
  }
  document.querySelectorAll("#themeToggle").forEach(function (btn) {
    setThemeIcon(btn);
    btn.addEventListener("click", function () {
      applyTheme(currentTheme() === "dark" ? "light" : "dark");
    });
  });

  // ---- Active sidebar link ------------------------------------------------
  var path = window.location.pathname;
  var best = null, bestLen = -1;
  document.querySelectorAll(".sidebar a").forEach(function (a) {
    var href = a.getAttribute("href");
    if (!href) return;
    if (href === path) { if (path.length > bestLen) { best = a; bestLen = path.length; } }
    else if (href !== "/" && path.indexOf(href) === 0 && href.length > bestLen) { best = a; bestLen = href.length; }
  });
  if (!best) {
    document.querySelectorAll('.sidebar a[href="/"]').forEach(function (a) {
      if (path === "/") best = a;
    });
  }
  if (best) best.classList.add("active");

  // ---- Close role menu on outside click ----------------------------------
  document.addEventListener("click", function (e) {
    document.querySelectorAll(".role-switch.open").forEach(function (rs) {
      if (!rs.contains(e.target)) rs.classList.remove("open");
    });
  });

  // ---- Animated stat counters --------------------------------------------
  function animateCount(el) {
    var raw = el.textContent.trim();
    if (!/^\d+$/.test(raw)) return;
    var target = parseInt(raw, 10);
    if (target <= 0) return;
    var dur = 900, start = null;
    function step(ts) {
      if (!start) start = ts;
      var p = Math.min((ts - start) / dur, 1);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(eased * target).toString();
      if (p < 1) requestAnimationFrame(step);
      else el.textContent = target.toString();
    }
    requestAnimationFrame(step);
  }

  // ---- Scroll reveal + counter trigger -----------------------------------
  var revealEls = Array.prototype.slice.call(
    document.querySelectorAll(".content .card, .content .stat, .auth-card")
  );
  revealEls.forEach(function (el, i) {
    el.setAttribute("data-reveal", "");
    el.style.animationDelay = Math.min(i * 60, 480) + "ms";
  });

  function reveal(el) {
    el.classList.add("revealed");
    el.querySelectorAll(".stat .num, .num").forEach(animateCount);
    if (el.classList.contains("num")) animateCount(el);
  }

  if ("IntersectionObserver" in window && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { reveal(en.target); io.unobserve(en.target); }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });
    revealEls.forEach(function (el) { io.observe(el); });
    // Safety: reveal anything still hidden after 1.5s.
    setTimeout(function () { revealEls.forEach(reveal); }, 1500);
  } else {
    revealEls.forEach(reveal);
  }

  // ---- Bulk-edit dirty highlighting (teams / categories / age groups) ----
  document.querySelectorAll("form.bulk-edit").forEach(function (form) {
    var fields = form.querySelectorAll("input, select, textarea");
    fields.forEach(function (el) {
      if (el.type === "hidden" || el.type === "submit" || el.type === "button") return;
      el.dataset.orig = el.type === "checkbox" ? String(el.checked) : el.value;
      var handler = function () {
        var cur = el.type === "checkbox" ? String(el.checked) : el.value;
        el.classList.toggle("dirty", cur !== el.dataset.orig);
        var row = el.closest("tr");
        if (row) {
          var any = Array.prototype.some.call(
            row.querySelectorAll("input, select, textarea"),
            function (i) { return i.classList.contains("dirty"); });
          row.classList.toggle("row-dirty", any);
        }
      };
      el.addEventListener("input", handler);
      el.addEventListener("change", handler);
    });
  });
})();
