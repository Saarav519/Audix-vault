// Applies the remembered light/dark choice before first paint.
(function () {
  try {
    var t = localStorage.getItem("audix-theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* storage unavailable */ }
})();
