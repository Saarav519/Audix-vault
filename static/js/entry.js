// Add / edit audit: grid helpers, observation cards, paste or upload from Excel.
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

  // ---------- fill the grid from an uploaded Excel file (the server only reads it, nothing is saved)
  var excel = form.querySelector("[data-lines-excel]");
  if (excel) {
    var fileInput = excel.querySelector("[data-lines-file]");
    var result = excel.querySelector("[data-lines-result]");
    var show = function (items) {
      result.innerHTML = "";
      items.forEach(function (it) {
        var li = document.createElement("li");
        li.className = it[0];
        li.textContent = it[1];
        result.appendChild(li);
      });
      result.hidden = !items.length;
    };
    fileInput.addEventListener("change", function (e) {
      e.stopPropagation();  // not a form change for the live preview
      var file = fileInput.files[0];
      if (!file) return;
      var fd = new FormData();
      fd.append("file", file);
      fd.append("client", form.querySelector("[name=client]").value);
      fd.append("audit", form.querySelector("[name=audit]").value);
      show([["info", "Reading " + file.name + " ..."]]);
      fetch(excel.getAttribute("data-upload-url"), { method: "POST", body: fd, credentials: "same-origin",
        headers: { "X-CSRFToken": form.querySelector("[name=csrfmiddlewaretoken]").value } })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          var filled = 0;
          Object.keys(j.values || {}).forEach(function (cid) {
            var tr = form.querySelector('[data-row="' + cid + '"]');
            if (!tr) return;
            var vals = j.values[cid];
            Object.keys(vals).forEach(function (f) {
              var input = tr.querySelector('[name="l-' + cid + "-" + f + '"]');
              if (input) input.value = vals[f];
            });
            var inc = tr.querySelector("[data-include]");
            if (inc.value !== "1") tr.querySelector("[data-toggle-row]").click();
            filled += 1;
          });
          var msgs = [];
          if (filled) msgs.push(["success", "Filled " + filled + " categor" + (filled === 1 ? "y" : "ies") + " from " + file.name + ". Check the numbers, then save."]);
          (j.errors || []).forEach(function (m) { msgs.push(["error", m]); });
          if ((j.unknown || []).length) msgs.push(["warning", "Not a category of this client, so skipped: " + j.unknown.join(", ") + "."]);
          if (filled && (j.missing || []).length) msgs.push(["warning", "Not in the file, left as they were: " + j.missing.join(", ") + "."]);
          show(msgs);
          recalc();
        })
        .catch(function () { show([["error", "The file could not be uploaded. Please try again."]]); })
        .finally(function () { fileInput.value = ""; });
    }, true);
  }

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

})();

// ---------- add a new store from the Store dropdown (no page reload, nothing entered is lost)
(function () {
  "use strict";
  var select = document.querySelector("[data-store-select]");
  var dialog = document.getElementById("quick-store");
  var form = document.getElementById("quick-store-form");
  if (!select || !dialog || !form || typeof dialog.showModal !== "function") return;
  var NEW = "__new__";
  var previous = select.value === NEW ? "" : select.value;
  var saved = false;
  var errorsBox = form.querySelector("[data-qs-errors]");
  var saveBtn = form.querySelector("[data-qs-save]");

  function showErrors(errors) {
    errorsBox.innerHTML = "";
    Object.keys(errors).forEach(function (field) {
      errors[field].forEach(function (msg) {
        var li = document.createElement("li");
        li.className = "error";
        li.textContent = (field === "__all__" ? "" : field.charAt(0).toUpperCase() + field.slice(1) + ": ") + msg;
        errorsBox.appendChild(li);
      });
    });
    errorsBox.hidden = !errorsBox.children.length;
  }

  // Capture phase: runs before the audit form's live-preview listener sees the change.
  select.addEventListener("change", function (e) {
    if (select.value !== NEW) { previous = select.value; return; }
    e.stopPropagation();
    saved = false;
    form.reset();
    showErrors({});
    dialog.showModal();
    form.querySelector("#qs-name").focus();
  }, true);

  dialog.addEventListener("close", function () {
    if (!saved) select.value = previous;  // Cancel / Esc: back to the store chosen before
    select.focus();
  });
  form.querySelector("[data-qs-cancel]").addEventListener("click", function () { dialog.close(); });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var name = form.querySelector("#qs-name");
    if (!name.value.trim()) { showErrors({name: ["Enter the store name."]}); name.focus(); return; }
    var csrf = document.querySelector("#audit-form [name=csrfmiddlewaretoken]").value;
    saveBtn.disabled = true;
    fetch(dialog.getAttribute("data-url"), {method: "POST", body: new FormData(form), credentials: "same-origin",
      headers: {"X-CSRFToken": csrf, "X-Requested-With": "fetch"}})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, body: j}; }); })
      .then(function (res) {
        if (!res.ok) { showErrors(res.body.errors || {__all__: ["The store could not be added."]}); return; }
        var opt = document.createElement("option");
        opt.value = res.body.id;
        opt.textContent = res.body.label;
        select.insertBefore(opt, select.querySelector('option[value="' + NEW + '"]'));
        select.value = res.body.id;
        previous = res.body.id;
        saved = true;
        dialog.close();
        select.dispatchEvent(new Event("change", {bubbles: true}));  // live preview recalculates
      })
      .catch(function () { showErrors({__all__: ["Network error. Please try again."]}); })
      .finally(function () { saveBtn.disabled = false; });
  });
})();
