/* Inbox-regels — aanmaken, herordenen, aan/uit en verwijderen.
 *
 * De bouwer is een pop-up met rijen die je zelf toevoegt. Dat verving een lijst met een
 * vinkje per mogelijke voorwaarde én per mogelijke actie: technisch hetzelfde, maar je
 * moest tien velden lezen om er één in te vullen. Nu kies je per rij wat je bedoelt, en
 * staat er niets op het scherm dat je niet zelf gevraagd hebt.
 */
(function () {
  "use strict";

  function t(s) { return (typeof T === "function") ? T(s) : s; }
  function api(method, url, body) { return window.Otto.api(method, url, body); }
  function toast(m) { if (m) window.Otto.toast(m); }

  const modal = document.getElementById("rule-modal");
  const form = document.getElementById("rule-form");
  if (!modal || !form) return;

  const catalogEl = document.getElementById("rule-catalog");
  let catalog = { conditions: [], actions: [], importance: [], folders: [] };
  try { catalog = JSON.parse(catalogEl.dataset.catalog); } catch (x) { /* laat de lege vorm staan */ }

  const tpl = document.getElementById("rule-row-template");
  const condsBox = document.getElementById("rule-conds");
  const actsBox = document.getElementById("rule-acts");
  const nameEl = document.getElementById("rule-name");
  const warning = document.getElementById("rule-warning");

  function el(tag, attrs) {
    const node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }

  /* Het waardeveld hangt af van het soort: een map krijgt de echte mappenlijst, een
     keuze de vaste niveaus, een lijst een tekstveld, en een vlag helemaal niets. */
  function valueField(entry) {
    if (entry.kind === "map") {
      const wrap = el("span", { style: "display:flex; gap:6px; flex:1;" });
      const sel = el("select", { class: "input rule-val", style: "flex:1; height:38px;" });
      catalog.folders.forEach(function (f) {
        const o = el("option", { value: f });
        o.textContent = f;
        sel.appendChild(o);
      });
      const other = el("option", { value: "__new__" });
      other.textContent = t("Nieuwe map…");
      sel.appendChild(other);
      const typed = el("input", {
        class: "input rule-val-new", style: "flex:1; height:38px; display:none;",
        placeholder: t("naam van de nieuwe map"),
      });
      sel.addEventListener("change", function () {
        typed.style.display = sel.value === "__new__" ? "" : "none";
      });
      if (!catalog.folders.length) { sel.value = "__new__"; typed.style.display = ""; }
      wrap.appendChild(sel);
      wrap.appendChild(typed);
      return wrap;
    }
    if (entry.kind === "keuze") {
      const sel = el("select", { class: "input rule-val", style: "flex:1; height:38px;" });
      catalog.importance.forEach(function (lvl) {
        const o = el("option", { value: lvl });
        o.textContent = lvl;
        sel.appendChild(o);
      });
      return sel;
    }
    if (entry.kind === "lijst") {
      return el("input", {
        class: "input rule-val", style: "flex:1; height:38px;",
        placeholder: t("kommagescheiden"),
      });
    }
    const leeg = el("span", { style: "flex:1; font-size:12.5px; color:var(--muted);" });
    leeg.textContent = t("geen extra invoer nodig");
    return leeg;
  }

  function fillRow(row, entries) {
    const sel = row.querySelector(".rule-row-key");
    sel.innerHTML = "";
    entries.forEach(function (entry) {
      const o = el("option", { value: entry.key });
      o.textContent = entry.label + (entry.destructive ? " ⚠" : "");
      sel.appendChild(o);
    });
    function render() {
      const entry = entries.find(function (x) { return x.key === sel.value; }) || entries[0];
      const holder = row.querySelector(".rule-row-value");
      holder.innerHTML = "";
      holder.appendChild(valueField(entry));
      row.dataset.destructive = entry.destructive ? "1" : "0";
      syncWarning();
    }
    sel.addEventListener("change", render);
    render();
  }

  function addRow(box, entries) {
    if (!entries.length) return;
    const row = tpl.content.firstElementChild.cloneNode(true);
    row.querySelector(".rule-row-remove").addEventListener("click", function () {
      row.remove();
      syncEmpty();
      syncWarning();
    });
    box.appendChild(row);
    fillRow(row, entries);
    syncEmpty();
  }

  function syncEmpty() {
    document.getElementById("rule-conds-empty").style.display =
      condsBox.children.length ? "none" : "";
    document.getElementById("rule-acts-empty").style.display =
      actsBox.children.length ? "none" : "";
  }

  function syncWarning() {
    /* Verwijderen is onomkeerbaar en Outlook voert het uit zonder tussenkomst; dat hoort
       zichtbaar te zijn terwijl je de regel bouwt, niet pas in een confirm() achteraf. */
    warning.style.display = actsBox.querySelector('[data-destructive="1"]') ? "" : "none";
  }

  /* Eén rij uitlezen. Een vlag zonder invoerveld is waar zodra de rij bestaat. */
  function readRow(row) {
    const key = row.querySelector(".rule-row-key").value;
    const field = row.querySelector(".rule-val");
    if (!field) return [key, true];
    if (field.tagName === "SELECT" && field.value === "__new__") {
      const typed = row.querySelector(".rule-val-new");
      return [key, typed ? typed.value.trim() : ""];
    }
    return [key, field.value.trim()];
  }

  function collect(box) {
    const out = {};
    Array.prototype.forEach.call(box.children, function (row) {
      const pair = readRow(row);
      // Een lege waarde is geen keuze: die hoort niet stil in de regel te belanden.
      if (pair[1] !== "") out[pair[0]] = pair[1];
    });
    return out;
  }

  function open() {
    nameEl.value = "";
    condsBox.innerHTML = "";
    actsBox.innerHTML = "";
    addRow(actsBox, catalog.actions);   // een regel zonder actie doet niets
    syncEmpty();
    syncWarning();
    modal.style.display = "flex";
    nameEl.focus();
  }

  function close() { modal.style.display = "none"; }

  async function save() {
    const name = nameEl.value.trim();
    if (!name) { toast(t("Geef de regel een naam.")); return; }
    const actions = collect(actsBox);
    if (!Object.keys(actions).length) { toast(t("Kies minstens één actie.")); return; }
    if (actsBox.querySelector('[data-destructive="1"]') && !window.confirm(
      t("Deze regel verwijdert mail automatisch. Weet je het zeker?"))) return;

    const r = await api("POST", "/api/rules/" + form.dataset.acct,
                        { name: name, conditions: collect(condsBox), actions: actions });
    if (r.error) { toast(r.error); return; }
    close();
    toast(r.toast);
    window.Otto.reloadSoon();
  }

  document.getElementById("rule-add-cond").addEventListener("click", function () {
    addRow(condsBox, catalog.conditions);
  });
  document.getElementById("rule-add-act").addEventListener("click", function () {
    addRow(actsBox, catalog.actions);
  });
  document.getElementById("rule-cancel").addEventListener("click", close);
  document.getElementById("rule-save").addEventListener("click", save);
  modal.addEventListener("click", function (e) { if (e.target === modal) close(); });

  document.addEventListener("click", async function (e) {
    if (e.target.closest("[data-rule-new]")) { e.preventDefault(); open(); return; }

    const mv = e.target.closest("[data-rule-move]");
    if (mv) {
      e.preventDefault();
      const r = await api("POST", "/api/rules/rule/" + mv.dataset.id + "/move",
                          { direction: mv.dataset.dir });
      if (r.ok) window.Otto.reloadSoon(); else toast(r.error || t("Verplaatsen mislukt."));
      return;
    }

    const tg = e.target.closest("[data-rule-toggle]");
    if (tg) {
      e.preventDefault();
      const r = await api("POST", "/api/rules/rule/" + tg.dataset.id + "/toggle");
      if (r.ok) tg.classList.toggle("on", r.on); else toast(r.error || t("Aanpassen mislukt."));
      return;
    }

    const del = e.target.closest("[data-rule-delete]");
    if (del) {
      e.preventDefault();
      if (!window.confirm(t("Deze regel verwijderen? Ze verdwijnt ook uit Outlook."))) return;
      const r = await api("POST", "/api/rules/rule/" + del.dataset.id + "/delete");
      if (r.ok) window.Otto.reloadSoon(); else toast(r.error || t("Verwijderen mislukt."));
      return;
    }

    const imp = e.target.closest("[data-rule-import]");
    if (imp) {
      e.preventDefault();
      imp.disabled = true;
      const r = await api("POST", "/api/rules/" + imp.dataset.acct + "/import");
      imp.disabled = false;
      toast(r.toast || t("Inlezen mislukt."));
      if (r.ok && r.added) window.Otto.reloadSoon();
    }
  });
})();
