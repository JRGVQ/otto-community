/* Inklapbare navigatiegroepen.
 *
 * De stand wordt per groep onthouden in localStorage, zodat je hem niet bij elke
 * paginawissel opnieuw moet dichtklappen. Faalt localStorage (privémodus, geblokkeerde
 * site-data), dan werkt het vouwen gewoon nog — alleen zonder geheugen.
 */
(function () {
  "use strict";

  var KEY = "otto.nav.collapsed";

  function readClosed() {
    try {
      return new Set(JSON.parse(localStorage.getItem(KEY) || "[]"));
    } catch (e) { return new Set(); }
  }

  function writeClosed(set) {
    try { localStorage.setItem(KEY, JSON.stringify(Array.from(set))); } catch (e) { /* geen geheugen */ }
  }

  function apply(head, open) {
    var group = head.getAttribute("data-nav-group");
    head.setAttribute("aria-expanded", open ? "true" : "false");
    document.querySelectorAll('[data-in-group="' + CSS.escape(group) + '"]').forEach(function (item) {
      // De actieve pagina blijft altijd zichtbaar; hem verbergen zou de zijbalk
      // laten zien alsof je ergens anders bent dan waar je staat.
      item.hidden = !open && !item.classList.contains("active");
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var closed = readClosed();
    document.querySelectorAll("[data-nav-group]").forEach(function (head) {
      apply(head, !closed.has(head.getAttribute("data-nav-group")));
    });
  });

  document.addEventListener("click", function (e) {
    var head = e.target.closest("[data-nav-group]");
    if (!head) return;
    e.preventDefault();
    var group = head.getAttribute("data-nav-group");
    var open = head.getAttribute("aria-expanded") !== "true";
    apply(head, open);
    var closed = readClosed();
    if (open) closed.delete(group); else closed.add(group);
    writeClosed(closed);
  });

  // Topnav-dropdowns (details/summary): hooguit één tegelijk open, en een klik
  // buiten de balk — of het volgen van een link erin — sluit hem weer.
  document.addEventListener("toggle", function (e) {
    var drop = e.target;
    if (!drop.matches || !drop.matches("[data-topnav-drop]") || !drop.open) return;
    document.querySelectorAll("[data-topnav-drop][open]").forEach(function (other) {
      if (other !== drop) other.open = false;
    });
  }, true);

  document.addEventListener("click", function (e) {
    var inside = e.target.closest("[data-topnav-drop]");
    document.querySelectorAll("[data-topnav-drop][open]").forEach(function (drop) {
      if (drop !== inside || e.target.closest(".tn-menu-item")) drop.open = false;
    });
  });
})();
