/* Snippets — vaste tekstblokken invoegen in een concept.
 *
 * De balk staat verborgen tot blijkt dat er blokken zijn, zodat een team dat er geen
 * gebruikt er ook geen lege rij voor terugkrijgt.
 */
(function () {
  "use strict";

  function t(s) { return (typeof T === "function") ? T(s) : s; }

  function insertAtCursor(field, text) {
    var start = field.selectionStart, end = field.selectionEnd, value = field.value;
    // Op de cursor invoegen, niet achteraan plakken: meestal hoort het blok midden
    // in het antwoord, boven de afsluiting.
    var before = value.slice(0, start), after = value.slice(end);
    var sep = (before && !before.endsWith("\n")) ? "\n" : "";
    field.value = before + sep + text + after;
    var pos = (before + sep + text).length;
    field.focus();
    field.setSelectionRange(pos, pos);
    // De autosave van otto.js hangt aan 'input'; zonder dit zou de invoeging niet bewaard worden.
    field.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function render(bar, items) {
    if (!items.length) return;               // niets te tonen → balk blijft verborgen
    items.forEach(function (s) {
      var b = document.createElement("button");
      b.className = "tone";
      b.type = "button";
      b.textContent = s.title;
      b.title = s.body.length > 160 ? s.body.slice(0, 160) + "…" : s.body;
      b.addEventListener("click", function () {
        var field = document.getElementById("draft-text");
        if (!field) return;
        insertAtCursor(field, s.body);
        // Otto.api is api(method, url, body) — de telling is een extraatje, dus stil bij falen.
        if (window.Otto && Otto.api) Otto.api("POST", "/api/snippets/" + s.id + "/used").catch(function () {});
      });
      bar.appendChild(b);
    });
    bar.style.display = "flex";
  }

  document.addEventListener("DOMContentLoaded", function () {
    var bar = document.querySelector("[data-snippets]");
    if (!bar) return;
    var id = bar.getAttribute("data-message-id");
    fetch("/api/snippets?message_id=" + encodeURIComponent(id), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.snippets) render(bar, d.snippets); })
      .catch(function () { /* blokken zijn een extraatje — nooit het scherm breken */ });
  });
})();
