/* Category create/edit modal. */
(function () {
  "use strict";
  const { api, $, $$ } = window.Otto;

  const modal = $("#cat-modal");
  if (!modal) return;

  const elTitle = $("#cat-modal-title");
  const elName = $("#cat-name");
  const elAi = $("#cat-ai");
  const elSubs = $("#cat-subs");
  const elDraft = $("#cat-draft-toggle");
  const elKeep = $("#cat-keep-toggle");
  const elNoise = $("#cat-noise-toggle");
  const elFolderWrap = $("#cat-folder-wrap");
  const elFolder = $("#cat-folder");
  const elDelete = $("#cat-delete");
  const tpl = $("#cat-sub-template");

  function syncFolderVisibility() {
    // The target folder only matters when the category leaves the inbox.
    elFolderWrap.style.display = elKeep.classList.contains("on") ? "none" : "";
  }

  function syncNoise() {
    // Ruis en concepten sluiten elkaar uit — de server dwingt dit ook af, maar het scherm
    // hoort niet iets te tonen dat na opslaan anders blijkt te zijn.
    const noise = elNoise.classList.contains("on");
    if (noise) elDraft.classList.remove("on");
    elDraft.classList.toggle("disabled", noise);
  }

  let editingId = null;
  let color = "#6C4CE6";

  function setColor(c) {
    color = c;
    $$(".color-swatch", modal).forEach((s) => s.classList.toggle("sel", s.dataset.color === c));
  }

  function addSubRow(sub) {
    const node = tpl.content.firstElementChild.cloneNode(true);
    node.querySelector(".sub-name").value = (sub && sub.name) || "";
    node.querySelector(".sub-ai").value = (sub && sub.ai) || "";
    node.dataset.draftEnabled = sub && sub.draft_enabled ? "1" : "0";
    node.querySelector(".sub-remove").addEventListener("click", () => node.remove());
    elSubs.appendChild(node);
  }

  function open(payload) {
    editingId = payload ? payload.id : null;
    elTitle.textContent = editingId ? "Categorie bewerken" : "Nieuwe categorie";
    elName.value = payload ? payload.name : "";
    elAi.value = payload ? payload.ai : "";
    setColor(payload ? payload.color : "#6C4CE6");
    elDraft.classList.toggle("on", !!(payload && payload.draft_enabled));
    // keep_in_inbox defaults to true for a brand-new category.
    elKeep.classList.toggle("on", payload ? !!payload.keep_in_inbox : true);
    elFolder.value = (payload && payload.target_folder) || "";
    elNoise.classList.toggle("on", !!(payload && payload.is_noise));
    syncFolderVisibility();
    syncNoise();
    elSubs.innerHTML = "";
    ((payload && payload.subs) || []).forEach(addSubRow);
    elDelete.style.display = editingId ? "" : "none";
    modal.style.display = "flex";
  }

  function close() { modal.style.display = "none"; }

  async function save() {
    const name = elName.value.trim();
    if (!name) { window.Otto.toast("Geef de categorie een naam"); return; }
    const subs = $$(".cat-sub-row", elSubs).map((row) => ({
      name: row.querySelector(".sub-name").value.trim(),
      ai: row.querySelector(".sub-ai").value.trim(),
      draft_enabled: row.dataset.draftEnabled === "1",
    })).filter((s) => s.name);
    const keepInInbox = elKeep.classList.contains("on");
    const body = {
      name, color, ai: elAi.value.trim(), draft_enabled: elDraft.classList.contains("on"),
      keep_in_inbox: keepInInbox,
      target_folder: keepInInbox ? "" : elFolder.value.trim(),
      is_noise: elNoise.classList.contains("on"),
      subs,
    };
    const r = editingId
      ? await api("PUT", `/api/categories/${editingId}`, body)
      : await api("POST", "/api/categories", body);
    if (r.ok) window.Otto.reloadSoon();
    else window.Otto.toast(r.error || "Opslaan mislukt");
  }

  async function remove() {
    if (!editingId) return;
    const r = await api("DELETE", `/api/categories/${editingId}`);
    if (r.ok) window.Otto.reloadSoon();
  }

  // Wiring
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-cat-new]")) { e.preventDefault(); open(null); }
    const edit = e.target.closest("[data-cat-edit]");
    if (edit) { e.preventDefault(); try { open(JSON.parse(edit.dataset.payload)); } catch (x) {} }
  });
  modal.addEventListener("click", (e) => { if (e.target === modal) close(); });
  $$(".color-swatch", modal).forEach((s) => s.addEventListener("click", () => setColor(s.dataset.color)));
  elDraft.addEventListener("click", () => {
    // Een ruiscategorie schrijft nooit concepten — de knop doet dan niets.
    if (elNoise.classList.contains("on")) return;
    elDraft.classList.toggle("on");
  });
  elNoise.addEventListener("click", () => { elNoise.classList.toggle("on"); syncNoise(); });
  elKeep.addEventListener("click", () => { elKeep.classList.toggle("on"); syncFolderVisibility(); });
  $("#cat-add-sub").addEventListener("click", () => addSubRow(null));
  $("#cat-cancel").addEventListener("click", close);
  $("#cat-save").addEventListener("click", save);
  elDelete.addEventListener("click", remove);
})();
