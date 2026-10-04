// Add / edit audit: grid helpers, observation cards, paste from Excel, uploads.
(function () {
  "use strict";
  var form = document.querySelector("[data-entry-form]");
  if (!form) return;

  function recalc() { document.body.dispatchEvent(new CustomEvent("audix:recalc")); }
  function nextIndex() {
    var el = document.getElementById("next-index");
    var n = parseInt(el.value || "0", 10);
    el.value = String(n + 1);
    return n;
  }

  // ---------- remove / add back a category row
  form.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-toggle-row]");
    if (!btn) return;
    var tr = btn.closest("tr");
    var inc = tr.querySelector("[data-include]");
    var on = inc.value !== "1";
    inc.value = on ? "1" : "0";
    tr.classList.toggle("removed", !on);
    btn.textContent = on ? "Remove" : "Add back";
    recalc();
  });

  // ---------- paste a block from Excel into the grid
  form.addEventListener("paste", function (e) {
    var cell = e.target.closest("[data-cell]");
    if (!cell) return;
    var text = (e.clipboardData || window.clipboardData).getData("text");
    if (!text || (text.indexOf("\t") === -1 && text.indexOf("\n") === -1)) return;
    e.preventDefault();
    var rows = text.replace(/\r/g, "").replace(/\n$/, "").split("\n").map(function (r) { return r.split("\t"); });
    var trs = Array.prototype.slice.call(form.querySelectorAll("[data-grid] tbody tr"));
    var startTr = trs.indexOf(cell.closest("tr"));
    var startCol = Array.prototype.slice.call(cell.closest("tr").querySelectorAll("[data-cell]")).indexOf(cell);
    rows.forEach(function (vals, ri) {
      var tr = trs[startTr + ri];
      if (!tr) return;
      var cells = tr.querySelectorAll("[data-cell]");
      vals.forEach(function (v, ci) {
        var c = cells[startCol + ci];
        if (c) c.value = v.trim().replace(/[₹,\s]/g, "");
      });
    });
    recalc();
  });

  // ---------- keyboard: Enter moves down the column
  form.addEventListener("keydown", function (e) {
    var cell = e.target.closest("[data-cell]");
    if (!cell || e.key !== "Enter") return;
    e.preventDefault();
    var tr = cell.closest("tr");
    var col = Array.prototype.slice.call(tr.querySelectorAll("[data-cell]")).indexOf(cell);
    var next = tr.nextElementSibling;
    while (next && next.classList.contains("removed")) next = next.nextElementSibling;
    if (next) next.querySelectorAll("[data-cell]")[col].focus();
  });

  // ---------- observations
  form.addEventListener("click", function (e) {
    if (e.target.closest("[data-add-obs]")) {
      var tpl = document.getElementById("obs-template").innerHTML.replace(/__i__/g, String(nextIndex()));
      var list = document.getElementById("obs-list");
      list.insertAdjacentHTML("beforeend", tpl);
      list.lastElementChild.querySelector("textarea").focus();
      return;
    }
    var s = e.target.closest("[data-suggest]");
    if (s) {
      s.disabled = true;
      var data = new FormData(form);
      fetch(s.getAttribute("data-suggest"), { method: "POST", body: data, credentials: "same-origin",
        headers: { "X-CSRFToken": data.get("csrfmiddlewaretoken") } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(function (html) {
          var list = document.getElementById("obs-list");
          var before = list.children.length;
          list.insertAdjacentHTML("beforeend", html);
          var added = list.children.length - before;
          var el = document.getElementById("next-index");
          el.value = String(parseInt(el.value || "0", 10) + added);
          if (list.children[before]) list.children[before].querySelector("textarea").focus();
          recalc();
        })
        .catch(function () { alert("Could not suggest drafts. Please try again."); })
        .finally(function () { s.disabled = false; });
    }
  });
  form.addEventListener("change", function (e) {
    var cb = e.target.closest("[data-obs-remove]");
    if (cb) cb.closest("[data-obs-card]").style.opacity = cb.checked ? "0.45" : "";
  });

  // ---------- uploads (presign -> upload straight to storage -> confirm)
  function uploadOne(zone, file, row) {
    var bar = row.querySelector(".progress span");
    var status = row.querySelector("[data-status]");
    var csrf = form.querySelector("[name=csrfmiddlewaretoken]").value;
    var body = new FormData();
    body.append("kind", zone.getAttribute("data-kind"));
    body.append("name", file.name);
    body.append("size", String(file.size));
    body.append("content_type", file.type || "");
    status.textContent = "Preparing";
    return fetch(zone.getAttribute("data-presign"), { method: "POST", body: body, credentials: "same-origin", headers: { "X-CSRFToken": csrf } })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "Upload refused"); return j; }); })
      .then(function (p) {
        return new Promise(function (resolve, reject) {
          var fd = new FormData();
          Object.keys(p.fields || {}).forEach(function (k) { fd.append(k, p.fields[k]); });
          if (!p.fields || !p.fields["Content-Type"]) fd.append("Content-Type", p.content_type);
          fd.append(p.file_field || "file", file);
          var xhr = new XMLHttpRequest();
          xhr.open(p.method || "POST", p.url);
          if (p.url.charAt(0) === "/") { xhr.setRequestHeader("X-CSRFToken", csrf); xhr.withCredentials = true; }
          xhr.upload.onprogress = function (ev) { if (ev.lengthComputable) bar.style.width = Math.round(ev.loaded / ev.total * 100) + "%"; };
          xhr.onload = function () { if (xhr.status >= 200 && xhr.status < 300) resolve(p); else reject(new Error("Upload failed (" + xhr.status + ")")); };
          xhr.onerror = function () { reject(new Error("Network error")); };
          status.textContent = "Uploading";
          xhr.send(fd);
        });
      })
      .then(function (p) {
        var c = new FormData();
        c.append("token", p.token);
        c.append("caption", "");
        return fetch(zone.getAttribute("data-confirm"), { method: "POST", body: c, credentials: "same-origin", headers: { "X-CSRFToken": csrf } })
          .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "Could not confirm"); return j; }); });
      })
      .then(function () { bar.style.width = "100%"; status.textContent = "Done"; row.classList.add("ok"); })
      .catch(function (err) {
        status.textContent = err.message + ". ";
        var retry = document.createElement("button");
        retry.type = "button"; retry.className = "btn btn-sm"; retry.textContent = "Retry";
        retry.addEventListener("click", function () { retry.remove(); bar.style.width = "0"; uploadOne(zone, file, row); });
        status.appendChild(retry);
        throw err;
      });
  }
  form.addEventListener("change", function (e) {
    var input = e.target.closest("[data-upload-input]");
    if (!input) return;
    var zone = input.closest("[data-upload-zone]");
    var list = zone.querySelector("[data-upload-list]");
    var jobs = Array.prototype.map.call(input.files, function (file) {
      var row = document.createElement("li");
      row.innerHTML = '<span class="small"></span><span class="small muted" data-status></span><div class="progress" style="flex-basis:100%"><span></span></div>';
      row.firstChild.textContent = file.name;
      list.appendChild(row);
      return uploadOne(zone, file, row).catch(function () { return null; });
    });
    input.value = "";
    Promise.all(jobs).then(function (res) {
      if (res.every(function (x) { return x !== null; })) {
        var note = zone.querySelector("[data-reload-note]");
        if (note) note.hidden = false;
      }
      recalc();
    });
  });
})();
