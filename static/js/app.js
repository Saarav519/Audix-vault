// Audix Vault: small progressive enhancements (theme, tooltips, drawer, lightbox, calendar).
(function () {
  "use strict";

  // ---------- theme toggle
  function currentTheme() {
    var t = document.documentElement.getAttribute("data-theme");
    if (t) return t;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-theme-toggle]");
    if (!btn) return;
    var next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("audix-theme", next); } catch (err) { /* ignore */ }
  });

  // ---------- clickable table rows
  document.addEventListener("click", function (e) {
    var row = e.target.closest("tr[data-href]");
    if (!row || e.target.closest("a, button, input, select, label")) return;
    var link = row.querySelector("a[data-drawer]") || row.querySelector("a");
    if (link) { link.click(); } else { window.location = row.getAttribute("data-href"); }
  });

  // ---------- chart tooltips
  var tip = null;
  function showTip(el, x, y) {
    if (!tip) { tip = document.createElement("div"); tip.className = "chart-tip"; tip.setAttribute("role", "status"); document.body.appendChild(tip); }
    tip.textContent = el.getAttribute("data-tip");
    tip.style.display = "block";
    var w = tip.offsetWidth;
    tip.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, x - w / 2)) + "px";
    tip.style.top = Math.max(8, y - tip.offsetHeight - 12) + "px";
  }
  function hideTip() { if (tip) tip.style.display = "none"; }
  document.addEventListener("mouseover", function (e) {
    var el = e.target.closest && e.target.closest("[data-tip]");
    if (el) showTip(el, e.clientX, e.clientY);
  });
  document.addEventListener("mousemove", function (e) {
    var el = e.target.closest && e.target.closest("[data-tip]");
    if (el) showTip(el, e.clientX, e.clientY); else hideTip();
  });
  document.addEventListener("focusin", function (e) {
    var el = e.target.closest && e.target.closest("[data-tip]");
    if (el) { var r = el.getBoundingClientRect(); showTip(el, r.left + r.width / 2, r.top); }
  });
  document.addEventListener("focusout", hideTip);
  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest("svg [data-tip]");
    if (el) { showTip(el, e.clientX, e.clientY); }
  });

  // ---------- audit detail drawer (desktop); full page on mobile
  var lastFocus = null;
  function closeDrawer() {
    var root = document.getElementById("drawer-root");
    if (root && root.firstChild) {
      root.innerHTML = "";
      document.body.style.overflow = "";
      if (lastFocus) lastFocus.focus();
    }
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest("a[data-drawer]");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || window.innerWidth < 860) return;
    e.preventDefault();
    lastFocus = a;
    var url = a.getAttribute("href");
    fetch(url + (url.indexOf("?") > -1 ? "&" : "?") + "panel=1", { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) {
        var root = document.getElementById("drawer-root");
        root.innerHTML = '<div class="drawer-backdrop" data-drawer-close></div><aside class="drawer" role="dialog" aria-modal="true" aria-label="Audit detail" tabindex="-1">' +
          '<div class="drawer-bar"><a class="btn btn-sm" href="' + url + '">Open as page</a><button class="btn btn-sm" type="button" data-drawer-close>Close</button></div>' +
          '<div class="drawer-body">' + html + "</div></aside>";
        document.body.style.overflow = "hidden";
        root.querySelector(".drawer").focus();
        if (window.htmx) window.htmx.process(root);
      })
      .catch(function () { window.location = url; });
  });
  document.addEventListener("click", function (e) { if (e.target.closest("[data-drawer-close]")) closeDrawer(); });

  // ---------- lightbox
  var lb = { items: [], index: 0 };
  function lbShow(i) {
    var box = document.getElementById("lightbox");
    lb.index = (i + lb.items.length) % lb.items.length;
    var it = lb.items[lb.index];
    box.querySelector("[data-lb-img]").src = it.getAttribute("data-full");
    box.querySelector("[data-lb-img]").alt = it.getAttribute("data-caption") || "Photo";
    box.querySelector("[data-lb-caption]").textContent = (it.getAttribute("data-caption") || "Photo") + " (" + (lb.index + 1) + " of " + lb.items.length + ")";
    box.querySelector("[data-lb-download]").href = it.getAttribute("data-download");
  }
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-lightbox]");
    if (t) {
      var group = t.closest("[data-lightbox-group]") || document;
      lb.items = Array.prototype.slice.call(group.querySelectorAll("[data-lightbox]"));
      lastFocus = t;
      var box = document.getElementById("lightbox");
      box.hidden = false;
      lbShow(lb.items.indexOf(t));
      box.querySelector("[data-lb-close]").focus();
      return;
    }
    if (e.target.closest("[data-lb-close]")) { document.getElementById("lightbox").hidden = true; if (lastFocus) lastFocus.focus(); }
    if (e.target.closest("[data-lb-next]")) lbShow(lb.index + 1);
    if (e.target.closest("[data-lb-prev]")) lbShow(lb.index - 1);
  });
  document.addEventListener("keydown", function (e) {
    var box = document.getElementById("lightbox");
    if (box && !box.hidden) {
      if (e.key === "ArrowRight") lbShow(lb.index + 1);
      else if (e.key === "ArrowLeft") lbShow(lb.index - 1);
      else if (e.key === "Escape") { box.hidden = true; if (lastFocus) lastFocus.focus(); }
      return;
    }
    if (e.key === "Escape") closeDrawer();
  });

  // ---------- calendar day selection
  document.addEventListener("click", function (e) {
    var day = e.target.closest("[data-cal-day]");
    if (!day) return;
    var cal = day.closest("[data-cal]");
    cal.querySelectorAll("[data-cal-day]").forEach(function (d) { d.setAttribute("aria-pressed", "false"); });
    day.setAttribute("aria-pressed", "true");
    var key = day.getAttribute("data-cal-day");
    document.querySelectorAll("[data-cal-list]").forEach(function (el) { el.hidden = el.getAttribute("data-cal-list") !== key; });
  });

  // ---------- generate password
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-generate-password]");
    if (!b) return;
    var target = document.getElementById(b.getAttribute("data-generate-password"));
    var chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789";
    var arr = new Uint32Array(14); window.crypto.getRandomValues(arr);
    var out = ""; for (var i = 0; i < arr.length; i++) out += chars[arr[i] % chars.length];
    target.value = out; target.type = "text"; target.focus();
  });

  // ---------- auto-submit filters
  document.addEventListener("change", function (e) {
    var el = e.target.closest("[data-autosubmit]");
    if (el && el.form) el.form.submit();
  });

  // ---------- compare page: a new store picks its latest audit
  document.addEventListener("change", function (e) {
    var sel = e.target.closest("[data-compare-store]");
    if (!sel) return;
    var side = sel.getAttribute("data-compare-store");
    var audit = document.getElementById(side + "-audit");
    if (audit) audit.disabled = true;
    sel.form.submit();
  });

  // ---------- confirm buttons
  document.addEventListener("submit", function (e) {
    var msg = e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) e.preventDefault();
  });
})();
