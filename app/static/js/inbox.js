/* Inbox-specific behaviour: instant tone switching + debounced draft autosave. */
(function () {
  "use strict";
  const { api, $, $$ } = window.Otto;

  // Tone switch — variants are embedded as data-v-<tone> on the .draft-panel,
  // so the swap is instant (mirrors the prototype's precomputed tones).
  document.addEventListener("click", (e) => {
    const el = e.target.closest('[data-action="tone"]');
    if (!el) return;
    e.preventDefault();
    const panel = el.closest(".draft-panel");
    const tone = el.dataset.tone;
    const key = "v" + tone.charAt(0).toUpperCase() + tone.slice(1);
    const text = panel ? panel.dataset[key] : null;
    const ta = $("#draft-text");
    if (ta && text != null) ta.value = text;
    $$(".tone", panel).forEach((b) => b.classList.toggle("active", b === el));
    api("POST", `/api/drafts/${el.dataset.id}/tone`, { tone }); // persist current tone
  });

  // HTML-mailweergave: laat elke sandbox-iframe zijn eigen inhoud passen (geen
  // binnenscrollbars). allow-same-origin (zónder allow-scripts) maakt de meting
  // mogelijk; lukt hij toch niet, dan blijft de CSS-minimumhoogte staan.
  function fitMailFrame(frame) {
    try {
      const doc = frame.contentDocument;
      if (!doc) return;
      const h = Math.max(doc.documentElement.scrollHeight, doc.body ? doc.body.scrollHeight : 0);
      if (h) frame.style.height = Math.min(h + 8, 6000) + "px";
    } catch (_) { /* cross-origin → laat de standaardhoogte staan */ }
  }
  $$("iframe.mail-html").forEach((f) => {
    f.addEventListener("load", () => {
      fitMailFrame(f);
      // Afbeeldingen laden na de load-event door; meet nog een paar keer bij.
      setTimeout(() => fitMailFrame(f), 350);
      setTimeout(() => fitMailFrame(f), 1200);
    });
    if (f.contentDocument && f.contentDocument.readyState === "complete") fitMailFrame(f);
  });

  // Draft autosave (debounced) on edit.
  let editTimer = null;
  document.addEventListener("input", (e) => {
    const el = e.target.closest('[data-action="edit"]');
    if (!el) return;
    clearTimeout(editTimer);
    editTimer = setTimeout(() => api("PUT", `/api/drafts/${el.dataset.id}`, { text: el.value }), 500);
  });
})();
