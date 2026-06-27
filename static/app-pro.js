/* Sports Meet — PRO interactions: scroll progress, 3D tilt, magnetic buttons, ripple. */
(function () {
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- Scroll progress bar ----
  var bar = document.getElementById("scrollProgress");
  if (bar) {
    var onScroll = function () {
      var h = document.documentElement;
      var max = (h.scrollHeight - h.clientHeight) || 1;
      bar.style.width = (Math.min(h.scrollTop / max, 1) * 100) + "%";
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    onScroll();
  }

  if (reduce) return;

  // ---- 3D tilt on cards & stat tiles ----
  var MAX = 7; // degrees
  function bindTilt(el) {
    var raf = null, rx = 0, ry = 0;
    el.addEventListener("mousemove", function (e) {
      var r = el.getBoundingClientRect();
      var px = (e.clientX - r.left) / r.width - 0.5;
      var py = (e.clientY - r.top) / r.height - 0.5;
      ry = px * MAX * 2;
      rx = -py * MAX * 2;
      if (!raf) raf = requestAnimationFrame(function () {
        el.style.transform = "perspective(900px) rotateX(" + rx.toFixed(2) + "deg) rotateY(" +
          ry.toFixed(2) + "deg) translateY(-4px)";
        raf = null;
      });
    });
    el.addEventListener("mouseleave", function () {
      el.style.transform = "";
    });
  }
  // Avoid tilting huge full-width cards (tables) — keep it to compact tiles & link cards.
  document.querySelectorAll(".content .stat, .content a.card").forEach(bindTilt);

  // ---- Magnetic buttons ----
  document.querySelectorAll(".btn").forEach(function (btn) {
    btn.addEventListener("mousemove", function (e) {
      var r = btn.getBoundingClientRect();
      var x = e.clientX - r.left - r.width / 2;
      var y = e.clientY - r.top - r.height / 2;
      btn.style.transform = "translate(" + (x * 0.18).toFixed(1) + "px," + (y * 0.28).toFixed(1) + "px)";
    });
    btn.addEventListener("mouseleave", function () { btn.style.transform = ""; });
  });

  // ---- Ripple on click (buttons) ----
  document.querySelectorAll(".btn, .btn-sm").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      var r = btn.getBoundingClientRect();
      var d = Math.max(r.width, r.height);
      var s = document.createElement("span");
      s.className = "rippl";
      s.style.width = s.style.height = d + "px";
      s.style.left = (e.clientX - r.left - d / 2) + "px";
      s.style.top = (e.clientY - r.top - d / 2) + "px";
      btn.appendChild(s);
      setTimeout(function () { s.remove(); }, 620);
    });
  });
})();
