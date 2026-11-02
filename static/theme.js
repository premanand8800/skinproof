// Light/dark theme toggle. The choice is remembered per browser; with no choice the OS setting wins.
(function () {
  var KEY = "skinproof-theme";
  var root = document.documentElement;
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function saved() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function current() { return root.getAttribute("data-theme") || (mq && mq.matches ? "dark" : "light"); }
  function paint() {
    var dark = current() === "dark";
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", dark ? "#151412" : "#F6F1E9");
    document.querySelectorAll(".theme-btn").forEach(function (b) {
      b.setAttribute("aria-pressed", dark ? "true" : "false");
      b.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
      b.title = dark ? "Light theme" : "Dark theme";
    });
  }
  var s = saved();
  if (s === "light" || s === "dark") root.setAttribute("data-theme", s);
  function wire() {
    document.querySelectorAll(".theme-btn").forEach(function (b) {
      b.addEventListener("click", function () {
        var next = current() === "dark" ? "light" : "dark";
        root.setAttribute("data-theme", next);
        try { localStorage.setItem(KEY, next); } catch (e) {}
        paint();
      });
    });
    paint();
  }
  if (mq && mq.addEventListener) mq.addEventListener("change", paint);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire); else wire();
})();
