// File uploads on the Add/Edit page and the audit page: presign -> upload to storage (or the app) -> confirm.
(function () {
  "use strict";
  // ---------- uploads (presign -> upload straight to storage -> confirm)
  function uploadOne(zone, file, row) {
    var bar = row.querySelector(".progress span");
    var status = row.querySelector("[data-status]");
    var csrf = zone.getAttribute("data-csrf");
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
  document.addEventListener("change", function (e) {
    var input = e.target.closest && e.target.closest("[data-upload-input]");
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
        if (zone.hasAttribute("data-reload-on-done")) window.location.href = zone.getAttribute("data-reload-on-done");
      }
      document.body.dispatchEvent(new CustomEvent("audix:recalc"));
    });
  });
})();
