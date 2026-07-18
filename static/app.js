/* File Converter — front-end logic. Vanilla JS, no dependencies, works offline. */
(function () {
  "use strict";

  // ---- Constants ----------------------------------------------------------
  // Category metadata: display order + human label. Keys match the lowercase
  // `category` field from /api/formats (registry.CATEGORIES).
  var CATEGORY_ORDER = ["document", "image", "data", "ebook", "audio"];
  var CATEGORY_LABEL = {
    document: "Document",
    image: "Image",
    data: "Data",
    ebook: "Ebook",
    audio: "Audio",
  };

  // ---- DOM refs -----------------------------------------------------------
  var els = {
    root: document.documentElement,
    themeToggle: document.getElementById("theme-toggle"),
    themeLabel: document.querySelector(".theme-toggle-label"),
    dropzone: document.getElementById("dropzone"),
    fileInput: document.getElementById("file-input"),
    fileMeta: document.getElementById("file-meta"),
    fileName: document.getElementById("file-name"),
    fileSize: document.getElementById("file-size"),
    fileType: document.getElementById("file-type"),
    fileExt: document.getElementById("file-ext"),
    fileClear: document.getElementById("file-clear"),
    targetsSection: document.getElementById("targets-section"),
    targets: document.getElementById("targets"),
    noTargets: document.getElementById("no-targets"),
    actionsSection: document.getElementById("actions-section"),
    convertBtn: document.getElementById("convert-btn"),
    selectedTarget: document.getElementById("selected-target"),
    status: document.getElementById("status"),
    demoBanner: document.getElementById("demo-banner"),
  };

  // ---- State --------------------------------------------------------------
  var state = {
    formats: null,        // /api/formats -> { ext: {name, category, targets:[...]} }
    formatsError: null,   // error message if the formats fetch failed
    file: null,           // currently selected File
    srcExt: null,         // detected source extension (lowercase, no dot)
    target: null,         // chosen target extension
    busy: false,          // a conversion is in flight
  };

  // ---- Theme --------------------------------------------------------------
  function applyTheme(theme) {
    var t = theme === "light" ? "light" : "dark";
    els.root.setAttribute("data-theme", t);
    var isDark = t === "dark";
    els.themeToggle.setAttribute("aria-pressed", String(isDark));
    if (els.themeLabel) els.themeLabel.textContent = isDark ? "Dark" : "Light";
    els.themeToggle.title =
      "Switch to " + (isDark ? "light" : "dark") + " theme";
  }

  function initTheme() {
    var saved = null;
    try { saved = localStorage.getItem("fc-theme"); } catch (e) { /* ignore */ }
    // Default is dark (per spec) unless the user previously chose light.
    applyTheme(saved === "light" ? "light" : "dark");
  }

  els.themeToggle.addEventListener("click", function () {
    var next = els.root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("fc-theme", next); } catch (e) { /* ignore */ }
  });

  // ---- Helpers ------------------------------------------------------------
  function extOf(filename) {
    var name = String(filename || "");
    var dot = name.lastIndexOf(".");
    if (dot < 0 || dot === name.length - 1) return "";
    return name.slice(dot + 1).toLowerCase();
  }

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes)) return "";
    if (bytes < 1024) return bytes + " B";
    var units = ["KB", "MB", "GB", "TB"];
    var i = -1;
    var n = bytes;
    do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
    return (n >= 10 ? n.toFixed(0) : n.toFixed(1)) + " " + units[i];
  }

  function show(el) { if (el) el.hidden = false; }
  function hide(el) { if (el) el.hidden = true; }

  function setStatus(message, kind) {
    if (!message) { hide(els.status); els.status.textContent = ""; return; }
    els.status.textContent = message;
    els.status.className = "notice " + (kind || "info");
    show(els.status);
  }

  // ---- Load conversion matrix --------------------------------------------
  function loadFormats() {
    return fetch("/api/formats", { headers: { Accept: "application/json" } })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        state.formats = (data && data.formats) || {};
        state.formatsError = null;
        renderDemoBanner(data && data.demo);
      })
      .catch(function (err) {
        state.formats = null;
        state.formatsError =
          "Couldn't load the list of supported formats. " +
          "Check your connection and reload. (" + err.message + ")";
      });
  }

  // ---- Demo banner --------------------------------------------------------
  // Shown only when the server runs with DEMO_MODE=1 (public demo instance).
  function renderDemoBanner(demo) {
    if (!els.demoBanner || !demo) return;
    els.demoBanner.textContent =
      "Public demo — files up to " + demo.maxUploadMb + " MB, " +
      demo.ratePerHour + " conversions/hour. ";
    var link = document.createElement("a");
    link.href = demo.repoUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "Self-host the full version →";
    els.demoBanner.appendChild(link);
    show(els.demoBanner);
  }

  // ---- File selection -----------------------------------------------------
  function handleFile(file) {
    if (!file) return;
    state.file = file;
    state.srcExt = extOf(file.name);
    state.target = null;

    // Meta panel
    els.fileName.textContent = file.name;
    els.fileName.title = file.name;
    els.fileSize.textContent = formatBytes(file.size);
    els.fileType.textContent = file.type || "unknown type";
    els.fileExt.textContent = state.srcExt ? state.srcExt.slice(0, 4) : "?";
    show(els.fileMeta);

    renderTargets();
  }

  function clearFile() {
    state.file = null;
    state.srcExt = null;
    state.target = null;
    els.fileInput.value = "";
    hide(els.fileMeta);
    hide(els.targetsSection);
    hide(els.actionsSection);
    els.targets.innerHTML = "";
    hide(els.noTargets);
    setStatus("");
    updateConvertButton();
  }

  // ---- Render target chips ------------------------------------------------
  function renderTargets() {
    els.targets.innerHTML = "";
    hide(els.noTargets);
    setStatus("");
    show(els.targetsSection);
    show(els.actionsSection);

    if (state.formatsError) {
      hide(els.targetsSection);
      hide(els.actionsSection);
      setStatus(state.formatsError, "error");
      show(els.actionsSection);
      updateConvertButton();
      return;
    }

    var src = state.srcExt;
    var entry = src && state.formats ? state.formats[src] : null;
    var targets = entry && Array.isArray(entry.targets) ? entry.targets : [];

    if (!targets.length) {
      var label = src ? "." + src : "this file";
      els.noTargets.textContent =
        "No conversions available for " + label + ".";
      show(els.noTargets);
      updateConvertButton();
      return;
    }

    // Group targets by category, preserving the registry's ordering.
    var groups = {};
    targets.forEach(function (t) {
      var cat = CATEGORY_LABEL[t.category] ? t.category : "document";
      (groups[cat] || (groups[cat] = [])).push(t);
    });

    CATEGORY_ORDER.forEach(function (cat) {
      var items = groups[cat];
      if (!items || !items.length) return;

      var group = document.createElement("div");
      group.className = "cat-group";

      var label = document.createElement("div");
      label.className = "cat-label";
      var dot = document.createElement("span");
      dot.className = "cat-dot";
      dot.style.background = "var(--cat-" + cat + ")";
      label.appendChild(dot);
      label.appendChild(document.createTextNode(CATEGORY_LABEL[cat]));
      group.appendChild(label);

      var row = document.createElement("div");
      row.className = "chip-row";
      items.forEach(function (t) {
        row.appendChild(makeChip(t));
      });
      group.appendChild(row);
      els.targets.appendChild(group);
    });

    updateConvertButton();
  }

  function makeChip(t) {
    var chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.setAttribute("role", "radio");
    chip.setAttribute("aria-checked", "false");
    chip.dataset.ext = t.ext;

    var ext = document.createElement("span");
    ext.className = "chip-ext";
    ext.textContent = t.ext;
    chip.appendChild(ext);

    if (t.name) {
      var name = document.createElement("span");
      name.className = "chip-name";
      name.textContent = t.name;
      chip.appendChild(name);
    }

    chip.addEventListener("click", function () { selectTarget(t.ext, chip); });
    return chip;
  }

  function selectTarget(ext, chipEl) {
    state.target = ext;
    var chips = els.targets.querySelectorAll(".chip");
    Array.prototype.forEach.call(chips, function (c) {
      c.setAttribute("aria-checked", String(c === chipEl));
    });
    setStatus("");
    updateConvertButton();
  }

  function updateConvertButton() {
    var ready = !!(state.file && state.target) && !state.busy;
    els.convertBtn.disabled = !ready;
    if (state.target) {
      els.selectedTarget.innerHTML =
        "Output: <strong>." + escapeHtml(state.target) + "</strong>";
    } else {
      els.selectedTarget.textContent = "";
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }

  // ---- Convert ------------------------------------------------------------
  function parseFilename(disposition, fallback) {
    if (disposition) {
      // RFC 5987 form: filename*=UTF-8''encoded%20name
      var star = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/i.exec(disposition);
      if (star && star[1]) {
        try { return decodeURIComponent(star[1].trim()); } catch (e) { /* fall through */ }
      }
      // Plain form: filename="name.ext" or filename=name.ext
      var plain = /filename\s*=\s*"?([^";]+)"?/i.exec(disposition);
      if (plain && plain[1]) return plain[1].trim();
    }
    return fallback;
  }

  function fallbackName() {
    var stem = "converted";
    if (state.file && state.file.name) {
      var n = state.file.name;
      var dot = n.lastIndexOf(".");
      stem = (dot > 0 ? n.slice(0, dot) : n) || "converted";
    }
    return stem + "." + (state.target || "out");
  }

  function setBusy(busy) {
    state.busy = busy;
    els.convertBtn.classList.toggle("loading", busy);
    els.convertBtn.querySelector(".btn-label").textContent =
      busy ? "Converting…" : "Convert";
    els.fileClear.disabled = busy;
    updateConvertButton();
    if (busy) els.convertBtn.disabled = true;
  }

  function triggerDownload(blob, filename) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    // Revoke after the click has had a chance to start the download.
    setTimeout(function () { URL.revokeObjectURL(url); }, 1500);
  }

  function convert() {
    if (!state.file || !state.target || state.busy) return;

    var form = new FormData();
    form.append("file", state.file);
    form.append("target", state.target);

    setBusy(true);
    setStatus("Converting — this can take a moment for large files…", "info");

    fetch("/convert", { method: "POST", body: form })
      .then(function (res) {
        var ctype = res.headers.get("Content-Type") || "";
        if (res.ok && ctype.indexOf("application/json") === -1) {
          // Successful conversion: a file stream.
          var name = parseFilename(
            res.headers.get("Content-Disposition"),
            fallbackName()
          );
          return res.blob().then(function (blob) {
            triggerDownload(blob, name);
            setBusy(false);
            setStatus("Done. Downloaded “" + name + "”.", "success");
          });
        }
        // Error path: server returns JSON {error: "..."} (or some other body).
        return res
          .json()
          .catch(function () { return null; })
          .then(function (data) {
            var msg =
              (data && data.error) ||
              "Conversion failed (HTTP " + res.status + ").";
            setBusy(false);
            setStatus(msg, "error");
          });
      })
      .catch(function (err) {
        setBusy(false);
        setStatus(
          "Network error — the conversion didn't complete. (" +
            err.message + ")",
          "error"
        );
      });
  }

  // ---- Wire up events -----------------------------------------------------
  els.fileInput.addEventListener("change", function (e) {
    var f = e.target.files && e.target.files[0];
    if (f) handleFile(f);
  });

  els.fileClear.addEventListener("click", clearFile);
  els.convertBtn.addEventListener("click", convert);

  // Keyboard activation for the label-based dropzone (Enter / Space).
  els.dropzone.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
      e.preventDefault();
      els.fileInput.click();
    }
  });

  // Drag & drop.
  ["dragenter", "dragover"].forEach(function (evt) {
    els.dropzone.addEventListener(evt, function (e) {
      e.preventDefault();
      e.stopPropagation();
      els.dropzone.classList.add("dragover");
    });
  });
  ["dragleave", "dragend"].forEach(function (evt) {
    els.dropzone.addEventListener(evt, function (e) {
      e.preventDefault();
      e.stopPropagation();
      els.dropzone.classList.remove("dragover");
    });
  });
  els.dropzone.addEventListener("drop", function (e) {
    e.preventDefault();
    e.stopPropagation();
    els.dropzone.classList.remove("dragover");
    var dt = e.dataTransfer;
    var f = dt && dt.files && dt.files[0];
    if (f) handleFile(f);
  });

  // Prevent the browser from navigating away if a file is dropped outside.
  window.addEventListener("dragover", function (e) { e.preventDefault(); });
  window.addEventListener("drop", function (e) { e.preventDefault(); });

  // ---- Keyboard navigation within a radiogroup of chips -------------------
  els.targets.addEventListener("keydown", function (e) {
    var chips = Array.prototype.slice.call(els.targets.querySelectorAll(".chip"));
    if (!chips.length) return;
    var idx = chips.indexOf(document.activeElement);
    var next = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (idx + 1) % chips.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (idx - 1 + chips.length) % chips.length;
    if (next >= 0) {
      e.preventDefault();
      chips[next].focus();
      selectTarget(chips[next].dataset.ext, chips[next]);
    }
  });

  // ---- Init ---------------------------------------------------------------
  initTheme();
  loadFormats().then(function () {
    if (state.formatsError) {
      setStatus(state.formatsError, "error");
      show(els.actionsSection);
    }
  });

  // Build stamp in the footer: makes a stale container obvious after rebuilds.
  fetch("/health")
    .then(function (r) { return r.json(); })
    .then(function (h) {
      var el = document.getElementById("build-stamp");
      if (el && h.build) el.textContent = "Build: " + h.build;
    })
    .catch(function () { /* footer stamp is best-effort */ });
})();
