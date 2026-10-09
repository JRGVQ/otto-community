/* Resizable + collapsible workspace panels (sidebar, category rail, message list).
   Drag the thin bars to resize; the ‹ / › buttons collapse and re-open. Widths and
   collapsed state are remembered per browser via localStorage. */
(function () {
  "use strict";
  var shell = document.querySelector(".otto-shell");
  if (!shell) return;

  var CFG = {
    sidebar: { varName: "--sidebar-w", min: 150, max: 380 },
    rail: { varName: "--rail-w", min: 120, max: 420 },
    list: { varName: "--list-w", min: 220, max: 640 },
  };
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  // Restore saved widths + collapsed state on load.
  Object.keys(CFG).forEach(function (key) {
    var w = lsGet("otto.panel." + key);
    if (w) shell.style.setProperty(CFG[key].varName, clamp(parseInt(w, 10), CFG[key].min, CFG[key].max) + "px");
    if (lsGet("otto.collapsed." + key) === "1") shell.classList.add("collapsed-" + key);
  });

  // Collapse / re-open.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-collapse]");
    if (!btn) return;
    e.preventDefault();
    var key = btn.dataset.collapse;
    var on = shell.classList.toggle("collapsed-" + key);
    lsSet("otto.collapsed." + key, on ? "1" : "0");
  });

  // Drag-to-resize. Each resizer sits directly after the panel it sizes.
  var drag = null;
  document.addEventListener("pointerdown", function (e) {
    var rz = e.target.closest(".col-resizer");
    if (!rz) return;
    var key = rz.dataset.resize;
    var panel = rz.previousElementSibling;
    if (!CFG[key] || !panel) return;
    drag = { cfg: CFG[key], key: key, panel: panel, rz: rz };
    rz.classList.add("dragging");
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    if (rz.setPointerCapture) rz.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  document.addEventListener("pointermove", function (e) {
    if (!drag) return;
    var left = drag.panel.getBoundingClientRect().left;
    var w = clamp(Math.round(e.clientX - left), drag.cfg.min, drag.cfg.max);
    shell.style.setProperty(drag.cfg.varName, w + "px");
  });
  function endDrag() {
    if (!drag) return;
    var w = parseInt(getComputedStyle(shell).getPropertyValue(drag.cfg.varName), 10);
    if (w) lsSet("otto.panel." + drag.key, String(w));
    drag.rz.classList.remove("dragging");
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
    drag = null;
  }
  document.addEventListener("pointerup", endDrag);
  document.addEventListener("pointercancel", endDrag);
})();
