/* Handtekening-builder (Beheer → Handtekeningen).
 *
 * Vier bouwwijzen: een WYSIWYG-editor (contenteditable + werkbalk), een eenvoudige
 * builder (velden → tabel-HTML), ruwe HTML en alleen tekst. Bovenaan een sjabloongalerij
 * om een kant-en-klaar ontwerp in de editor te laden en te bewerken. De live preview
 * vult placeholders met de gegevens van de ingelogde beheerder en rendert in een
 * sandbox-iframe — wat je ziet is wat het concept krijgt.
 *
 * Placeholders in de editor tonen als niet-bewerkbare chips (<span class="ph-chip"
 * data-ph="key">{{key}}</span>) maar serialiseren terug naar platte {{key}}, zodat de
 * opgeslagen HTML geen chip-opmaak bevat en de resolver ze per gebruiker invult.
 *
 * De builder-instellingen reizen mee IN de opgeslagen HTML als een commentaarregel
 * (<!--otto-builder:{…}-->): zo zet "Bewerken" de velden exact terug zonder extra kolom.
 */
(function () {
  "use strict";
  const builder = document.getElementById("sig-builder");
  if (!builder) return;

  const $ = (sel) => document.querySelector(sel);
  const samples = JSON.parse(builder.dataset.samples || "{}");
  const CFG_RE = /<!--otto-builder:(\{[\s\S]*?\})-->/;
  const PH_RE = /\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}/g;
  let templates = {};
  try {
    const raw = document.getElementById("sig-templates-data");
    if (raw) JSON.parse(raw.textContent).forEach((t) => { templates[t.key] = t; });
  } catch (_) { templates = {}; }

  const els = {
    form: $("#sig-form"), id: $("#sig-id"), name: $("#sig-name"), domain: $("#sig-domain"),
    users: $("#sig-users"), groups: $("#sig-groups"),
    htmlOut: $("#sig-html"), htmlSrc: $("#sig-html-src"), body: $("#sig-body"),
    editor: $("#sig-editor-area"), fore: $("#sig-fore"), fontsize: $("#sig-fontsize"),
    insertPanel: $("#sig-insert-panel"), insertUrl: $("#sig-insert-url"),
    insertExtra: $("#sig-insert-extra"), insertOk: $("#sig-insert-ok"), insertCancel: $("#sig-insert-cancel"),
    preview: $("#sig-preview"), title: $("#sig-builder-title"), reset: $("#sig-reset"),
    modes: $("#sig-modes"),
    logo: $("#sb-logo"), color: $("#sb-color"), site: $("#sb-site"),
    functie: $("#sb-functie"), afdeling: $("#sb-afdeling"), locatie: $("#sb-locatie"),
    tel: $("#sb-tel"), mobiel: $("#sb-mobiel"), mail: $("#sb-mail"),
    groet: $("#sb-groet"), disclaimer: $("#sb-disclaimer"),
  };
  let mode = "simple";
  let lastFocusedText = null;

  const esc = (s) => String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  function builderConfig() {
    return {
      logo: els.logo.value.trim(), color: els.color.value, site: els.site.value.trim(),
      functie: els.functie.checked, afdeling: els.afdeling.checked, locatie: els.locatie.checked,
      tel: els.tel.checked, mobiel: els.mobiel.checked, mail: els.mail.checked,
      groet: els.groet.value, disclaimer: els.disclaimer.value.trim(),
    };
  }

  // Velden → nette tabel-handtekening (e-mailveilige inline styles, placeholders erin).
  function buildSimpleHtml(cfg) {
    const c = esc(cfg.color || "#6C4CE6");
    const lines = [];
    if (cfg.functie) lines.push('<div data-ph="functie" style="color:#57606a;font-size:12.5px;">{{functie}}</div>');
    if (cfg.afdeling) lines.push('<div data-ph="afdeling" style="color:#57606a;font-size:12.5px;">{{afdeling}}</div>');
    const bedrijf = '<div style="font-weight:600;font-size:12.5px;margin-top:2px;">{{bedrijf}}'
      + (cfg.locatie ? '<span data-ph="locatie" style="font-weight:400;color:#57606a;"> · {{locatie}}</span>' : "")
      + "</div>";
    const contact = [];
    if (cfg.tel) contact.push('<span data-ph="telefoon" style="margin-right:12px;white-space:nowrap;">📞 {{telefoon}}</span>');
    if (cfg.mobiel) contact.push('<span data-ph="mobiel" style="margin-right:12px;white-space:nowrap;">📱 {{mobiel}}</span>');
    if (cfg.mail) contact.push('<span data-ph="email" style="margin-right:12px;white-space:nowrap;">✉️ {{email}}</span>');
    if (cfg.site) contact.push(`<a href="${esc(cfg.site)}" style="color:${c};text-decoration:none;white-space:nowrap;">${esc(cfg.site.replace(/^https?:\/\//, ""))}</a>`);
    const logoCell = cfg.logo
      ? `<td style="padding-right:14px;vertical-align:top;"><img src="${esc(cfg.logo)}" alt="{{bedrijf}}" width="72" style="max-width:72px;height:auto;border-radius:8px;"></td>`
      : "";
    return [
      `<!--otto-builder:${JSON.stringify(cfg)}-->`,
      cfg.groet ? `<div style="font-size:13.5px;color:#1f2328;margin-bottom:10px;">${esc(cfg.groet)}</div>` : "",
      '<table cellpadding="0" cellspacing="0" border="0" style="font-family:Verdana,Arial,sans-serif;font-size:13px;color:#1f2328;">',
      "<tr>", logoCell,
      `<td style="border-left:3px solid ${c};padding-left:14px;vertical-align:top;">`,
      `<div style="font-weight:bold;font-size:14px;color:${c};">{{naam}}</div>`,
      lines.join(""),
      bedrijf,
      contact.length ? `<div style="margin-top:8px;font-size:12px;color:#57606a;">${contact.join("")}</div>` : "",
      "</td></tr></table>",
      cfg.disclaimer
        ? `<div style="margin-top:12px;padding-top:8px;border-top:1px solid #e3e6ea;font-size:10.5px;color:#8b949e;max-width:520px;">${esc(cfg.disclaimer)}</div>`
        : "",
    ].join("");
  }

  function buildSimpleText(cfg) {
    const out = [];
    if (cfg.groet) out.push(cfg.groet, "");
    out.push("{{naam}}");
    if (cfg.functie) out.push("{{functie}}");
    if (cfg.afdeling) out.push("{{afdeling}}");
    out.push(cfg.locatie ? "{{bedrijf}} · {{locatie}}" : "{{bedrijf}}");
    const contact = [];
    if (cfg.tel) contact.push("{{telefoon}}");
    if (cfg.mobiel) contact.push("{{mobiel}}");
    if (cfg.mail) contact.push("{{email}}");
    if (cfg.site) contact.push(cfg.site);
    if (contact.length) out.push(contact.join(" · "));
    return out.join("\n");
  }

  // ── WYSIWYG-editor: HTML ⇄ contenteditable met placeholder-chips ──────────
  const chip = (k) => `<span class="ph-chip" data-ph="${esc(k)}" contenteditable="false">{{${esc(k)}}}</span>`;

  function htmlToEditor(html) {
    // {{key}} → zichtbare chip, maar ALLEEN in tekstinhoud — nooit binnen een
    // tag-attribuut (bv. alt="{{bedrijf}}"), want een chip-span daar breekt de tag.
    // Daarom eerst parsen, dan enkel tekstknopen bewerken.
    const box = document.createElement("div");
    box.innerHTML = String(html || "").replace(CFG_RE, "");
    const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
    const texts = [];
    while (walker.nextNode()) texts.push(walker.currentNode);
    for (const node of texts) {
      if (!PH_RE.test(node.nodeValue)) continue;
      PH_RE.lastIndex = 0;
      const frag = document.createElement("span");
      frag.innerHTML = esc(node.nodeValue).replace(PH_RE, (_, k) => chip(k));
      node.replaceWith(...frag.childNodes);
    }
    return box.innerHTML;
  }

  // Maak editor-HTML e-mailveilig: <style>/<script> weg en class/id gestript. Mailclients
  // strippen die tóch, dus de opgeslagen handtekening leunt volledig op inline styles.
  // (De server sanitizet geïmporteerde sjablonen apart; dit houdt óók geplakte inhoud net.)
  function normalizeEmailHtml(root) {
    root.querySelectorAll("style, script").forEach((n) => n.remove());
    root.querySelectorAll("[class], [id]").forEach((n) => {
      n.removeAttribute("class");
      n.removeAttribute("id");
    });
  }

  function editorToHtml() {
    // Chips terug naar platte {{key}}, chip-attributen weg — de opgeslagen HTML is schoon.
    const clone = els.editor.cloneNode(true);
    clone.querySelectorAll(".ph-chip[data-ph]").forEach((c) => {
      c.replaceWith(document.createTextNode("{{" + c.dataset.ph + "}}"));
    });
    normalizeEmailHtml(clone);
    return clone.innerHTML.trim();
  }

  function currentHtml() {
    if (mode === "editor") return editorToHtml();
    if (mode === "simple") return buildSimpleHtml(builderConfig());
    if (mode === "html") return els.htmlSrc.value.trim();
    return "";
  }

  function fillPlaceholders(s) {
    return String(s || "").replace(PH_RE, (_, k) => esc(samples[k] || `⟨${k}⟩`));
  }

  function renderPreview() {
    const html = mode === "text"
      ? esc(fillPlaceholders(els.body.value)).replace(/\n/g, "<br>")
      : fillPlaceholders(currentHtml().replace(CFG_RE, ""));
    els.preview.srcdoc = "<!doctype html><html><head><meta charset=\"utf-8\"><style>" +
      "body{margin:0;padding:10px;background:#fff;font:14px/1.55 -apple-system,'Segoe UI',Roboto,sans-serif;color:#1f2328;}" +
      "img{max-width:100%;height:auto;}</style></head><body>" + html + "</body></html>";
  }

  function setMode(next) {
    mode = next;
    ["editor", "simple", "html", "text"].forEach((m) => {
      const el = document.getElementById("sig-mode-" + m);
      if (el) el.hidden = mode !== m;
    });
    els.modes.querySelectorAll("a").forEach((a) =>
      a.classList.toggle("active", a.dataset.mode === mode));
    renderPreview();
  }

  function loadIntoEditor(html) {
    els.editor.innerHTML = htmlToEditor(html);
    setMode("editor");
  }

  els.modes.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-mode]");
    if (!a) return;
    e.preventDefault();
    const next = a.dataset.mode;
    // Wissel builder → editor/HTML neemt de gegenereerde HTML mee, zodat je kan bijschaven.
    if (next === "editor" && !editorToHtml()) {
      els.editor.innerHTML = htmlToEditor(mode === "html" ? els.htmlSrc.value : buildSimpleHtml(builderConfig()));
    }
    if (next === "html" && mode === "simple" && !els.htmlSrc.value.trim()) {
      els.htmlSrc.value = buildSimpleHtml(builderConfig());
    }
    if (next === "html" && mode === "editor") els.htmlSrc.value = editorToHtml();
    setMode(next);
  });

  // Sjabloon laden — werkt zowel voor de inline galerij in de editor als voor de
  // sjabloonkiezer-modal (data-from-modal=1). Vanuit de modal = een nieuwe handtekening:
  // eerst het formulier leegmaken, dan het ontwerp laden en naar de editor-weergave.
  document.addEventListener("click", (e) => {
    const btn = e.target.closest('[data-action="sig-load-template"]');
    if (!btn) return;
    e.preventDefault();
    const fromModal = btn.dataset.fromModal === "1";
    if (fromModal) resetBuilder();
    const key = btn.dataset.tpl;
    if (key && templates[key]) {
      const t = templates[key];
      const html = String(t.html).replaceAll("--ACCENT--", els.color.value || "#6C4CE6");
      if (!els.name.value.trim()) els.name.value = t.name;
      loadIntoEditor(html);
    } else if (!key) {
      if (els.editor) els.editor.innerHTML = "";   // leeg beginnen
      setMode("editor");
    }
    if (fromModal) { closeTpl(); showEditor(); }
  });

  // ── Inline invoeg-paneel (link/afbeelding) i.p.v. een browser-prompt ──────
  let pendingInsert = null;   // 'link' | 'image'
  let savedRange = null;      // de editor-selectie bewaard vóór het paneel focus steelt

  function saveEditorRange() {
    const sel = window.getSelection();
    if (sel && sel.rangeCount && els.editor.contains(sel.anchorNode)) {
      savedRange = sel.getRangeAt(0).cloneRange();
    }
  }
  function restoreEditorRange() {
    els.editor.focus();
    if (!savedRange) return;
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(savedRange);
  }
  function openInsert(kind) {
    pendingInsert = kind;
    saveEditorRange();
    els.insertUrl.value = "";
    els.insertUrl.placeholder = kind === "image" ? "https://…/logo.png" : "https:// of mailto:";
    els.insertExtra.value = "";
    els.insertExtra.placeholder = "breedte (px)";
    els.insertExtra.hidden = kind !== "image";
    els.insertPanel.hidden = false;
    els.insertUrl.focus();
  }
  function closeInsert() {
    els.insertPanel.hidden = true;
    pendingInsert = null;
  }
  if (els.insertOk) {
    els.insertOk.addEventListener("click", () => {
      const url = els.insertUrl.value.trim();
      if (!url) { closeInsert(); return; }
      restoreEditorRange();
      if (pendingInsert === "image") {
        const w = parseInt(els.insertExtra.value, 10);
        const widthAttr = w > 0 ? ` width="${w}"` : "";
        document.execCommand("insertHTML", false,
          `<img src="${esc(url)}" alt=""${widthAttr} style="max-width:100%;height:auto;">`);
      } else {
        // Geen selectie? Voeg de URL als klikbare tekst in.
        if (savedRange && savedRange.collapsed) {
          document.execCommand("insertHTML", false, `<a href="${esc(url)}">${esc(url)}</a>`);
        } else {
          document.execCommand("createLink", false, url);
        }
      }
      closeInsert();
      renderPreview();
    });
  }
  if (els.insertCancel) els.insertCancel.addEventListener("click", closeInsert);

  // ── Werkbalk (execCommand — universeel voor contenteditable-basis) ────────
  const toolbar = document.getElementById("sig-toolbar");
  if (toolbar) {
    toolbar.addEventListener("mousedown", (e) => {
      // mousedown i.p.v. click: behoud de selectie in de editor.
      const b = e.target.closest("button[data-cmd]");
      if (!b) return;
      e.preventDefault();
      const cmd = b.dataset.cmd;
      if (cmd === "link" || cmd === "image") {
        openInsert(cmd);
        return;
      }
      els.editor.focus();
      if (cmd === "unlink") {
        document.execCommand("removeFormat");
        document.execCommand("unlink");
      } else {
        document.execCommand(cmd);
      }
      renderPreview();
    });
  }
  if (els.fore) {
    els.fore.addEventListener("input", () => {
      els.editor.focus();
      document.execCommand("foreColor", false, els.fore.value);
      renderPreview();
    });
  }
  if (els.fontsize) {
    // mousedown bewaart de selectie; op change passen we de grootte toe en resetten we.
    els.fontsize.addEventListener("mousedown", saveEditorRange);
    els.fontsize.addEventListener("change", () => {
      if (!els.fontsize.value) return;
      restoreEditorRange();
      document.execCommand("fontSize", false, els.fontsize.value);
      els.fontsize.value = "";
      renderPreview();
    });
  }
  if (els.editor) {
    els.editor.addEventListener("input", renderPreview);
  }

  builder.addEventListener("input", (e) => {
    if (e.target.closest(".sig-b, #sig-html-src, #sig-body")) renderPreview();
  });
  builder.addEventListener("change", (e) => {
    if (e.target.closest(".sig-b")) renderPreview();
  });

  // Placeholder-chips: invoegen op de cursorpositie (editor: als chip; anders als tekst).
  builder.addEventListener("focusin", (e) => {
    if (e.target.matches("textarea, input[type=text], input:not([type])")) lastFocusedText = e.target;
  });
  builder.addEventListener("click", (e) => {
    const chipBtn = e.target.closest('[data-action="sig-placeholder"]');
    if (!chipBtn) return;
    e.preventDefault();
    const key = chipBtn.dataset.ph;
    if (mode === "editor") {
      els.editor.focus();
      document.execCommand("insertHTML", false, chip(key) + "&nbsp;");
      renderPreview();
      return;
    }
    const target = lastFocusedText ||
      (mode === "html" ? els.htmlSrc : mode === "text" ? els.body : els.groet);
    const token = "{{" + key + "}}";
    const start = target.selectionStart ?? target.value.length;
    target.value = target.value.slice(0, start) + token + target.value.slice(target.selectionEnd ?? start);
    target.focus();
    target.selectionStart = target.selectionEnd = start + token.length;
    renderPreview();
  });

  // Boeking-knop-snippets: voeg een kant-en-klare knop in (admin past de link daarna aan).
  const BOOKING = {
    calendly: { label: "Plan een meeting", url: "https://calendly.com/jouw-link" },
    bookings: { label: "Boek een afspraak", url: "https://outlook.office365.com/book/jouworganisatie/" },
    chilipiper: { label: "Plan een gesprek", url: "https://jouwbedrijf.chilipiper.com/" },
  };
  function bookingHtml(key) {
    const b = BOOKING[key] || BOOKING.calendly;
    return '<a href="' + b.url + '" style="display:inline-block;background:#6C4CE6;color:#ffffff;'
      + 'text-decoration:none;padding:8px 14px;border-radius:8px;font-size:12.5px;font-weight:600;">'
      + b.label + '</a>';
  }
  builder.addEventListener("click", (e) => {
    const snip = e.target.closest('[data-action="sig-snippet"]');
    if (!snip) return;
    e.preventDefault();
    const html = bookingHtml(snip.dataset.snippet);
    if (mode !== "editor") {
      if (!editorToHtml()) els.editor.innerHTML = htmlToEditor(currentHtml());
      setMode("editor");
    }
    els.editor.focus();
    document.execCommand("insertHTML", false, html + "&nbsp;");
    renderPreview();
  });

  // Bij opslaan: de juiste velden vullen voor de gekozen bouwwijze.
  els.form.addEventListener("submit", () => {
    if (mode === "text") {
      els.htmlOut.value = "";
    } else {
      els.htmlOut.value = currentHtml();
      if (mode === "simple") els.body.value = buildSimpleText(builderConfig());
    }
  });

  // "Bewerken" op een bestaande kaart: builder/editor vooraf invullen (incl. toewijzingen).
  document.addEventListener("click", (e) => {
    const btn = e.target.closest('[data-action="sig-edit"]');
    if (!btn) return;
    e.preventDefault();
    const data = JSON.parse(btn.closest("[data-sig]").dataset.sig);
    els.id.value = data.id;
    els.name.value = data.name;
    els.domain.value = data.domain;
    els.body.value = data.body;
    Array.from(els.users?.options || []).forEach((o) => { o.selected = data.users.includes(o.value); });
    Array.from(els.groups?.options || []).forEach((o) => { o.selected = data.groups.includes(o.value.split("|")[0]); });
    // Per-handtekening-config: aan/uit, ontvanger-scope, tijdvenster, uitzonderingen.
    setPerConfig(data);
    const m = (data.html || "").match(CFG_RE);
    if (m) {
      try {
        const cfg = JSON.parse(m[1]);
        els.logo.value = cfg.logo || ""; els.color.value = cfg.color || "#6C4CE6";
        els.site.value = cfg.site || ""; els.functie.checked = !!cfg.functie;
        els.afdeling.checked = !!cfg.afdeling; els.locatie.checked = !!cfg.locatie;
        els.tel.checked = !!cfg.tel; els.mobiel.checked = !!cfg.mobiel; els.mail.checked = !!cfg.mail;
        els.groet.value = cfg.groet || ""; els.disclaimer.value = cfg.disclaimer || "";
        setMode("simple");
      } catch (_) { loadIntoEditor(data.html); }
    } else if (data.html) {
      loadIntoEditor(data.html);   // vrije HTML → open in de WYSIWYG-editor
    } else {
      setMode("text");
    }
    els.title.textContent = builder.dataset.i18nEdit;
    els.reset.hidden = false;
    showEditor();
    renderPreview();
  });

  els.reset.addEventListener("click", () => resetBuilder());

  // ── Regels: voorwaarde-rijen + bewerken ──────────────────────────────────
  const ruleForm = document.getElementById("sig-rule-form");
  const rulesCard = document.getElementById("sig-rules");
  if (ruleForm && rulesCard) {
    const condsBox = document.getElementById("sig-rule-conds");
    const rEls = {
      id: $("#rule-id"), name: $("#rule-name"), priority: $("#rule-priority"),
      signature: $("#rule-signature"), enabled: $("#rule-enabled"),
      title: $("#sig-rule-title"), reset: $("#sig-rule-reset"),
    };
    let rulesData = {};
    try {
      const raw = document.getElementById("sig-rules-data");
      if (raw) JSON.parse(raw.textContent).forEach((r) => { rulesData[r.id] = r; });
    } catch (_) { rulesData = {}; }

    const firstRow = () => condsBox.querySelector(".sig-cond-row");
    function fillRow(row, f, o, v) {
      row.querySelector(".cond-field").value = f || "";
      row.querySelector(".cond-op").value = o || "equals";
      row.querySelector(".cond-value").value = v || "";
    }
    function addCondRow(f, o, v) {
      const row = firstRow().cloneNode(true);
      fillRow(row, f, o, v);
      condsBox.appendChild(row);
    }
    function resetConds() {
      condsBox.querySelectorAll(".sig-cond-row").forEach((r, i) => (i === 0 ? fillRow(r, "", "equals", "") : r.remove()));
    }

    document.getElementById("sig-cond-add").addEventListener("click", () => addCondRow("", "equals", ""));
    condsBox.addEventListener("click", (e) => {
      if (!e.target.closest(".cond-remove")) return;
      const rows = condsBox.querySelectorAll(".sig-cond-row");
      if (rows.length > 1) e.target.closest(".sig-cond-row").remove();
      else fillRow(firstRow(), "", "equals", "");
    });
    rEls.reset.addEventListener("click", () => {
      ruleForm.reset(); rEls.id.value = ""; resetConds();
      rEls.enabled.checked = true; rEls.priority.value = "100";
      rEls.title.textContent = rulesCard.dataset.i18nNewrule;
      rEls.reset.hidden = true;
    });
    document.addEventListener("click", (e) => {
      const btn = e.target.closest('[data-action="sig-rule-edit"]');
      if (!btn) return;
      e.preventDefault();
      const r = rulesData[btn.dataset.ruleId];
      if (!r) return;
      rEls.id.value = r.id; rEls.name.value = r.name || "";
      rEls.priority.value = r.priority; rEls.enabled.checked = !!r.enabled;
      rEls.signature.value = r.signature_id;
      resetConds();
      (r.conds || []).forEach((c, i) => (i === 0 ? fillRow(firstRow(), c[0], c[1], c[2]) : addCondRow(c[0], c[1], c[2])));
      rEls.title.textContent = rulesCard.dataset.i18nEditrule;
      rEls.reset.hidden = false;
      rulesCard.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  // Ingebedde afbeelding invoegen: de asset-kaart staat buiten #sig-builder, dus
  // op document-niveau. Voegt <img src="/media/sig-asset/N"> in de editor in; bij de
  // aflevering herschrijft de write-back die src naar een cid:-bijlage.
  document.addEventListener("click", (e) => {
    const btn = e.target.closest('[data-action="sig-insert-asset"]');
    if (!btn) return;
    e.preventDefault();
    const src = btn.dataset.src;
    if (mode !== "editor") {
      if (!editorToHtml()) els.editor.innerHTML = htmlToEditor(currentHtml());
      setMode("editor");
    }
    showEditor();
    els.editor.focus();
    document.execCommand("insertHTML", false, `<img src="${esc(src)}" style="max-width:100%;height:auto;">`);
    renderPreview();
  });

  // ── Banner bewerken: vul het bannerformulier vanuit de JSON-blob ──────────
  const bannerForm = document.getElementById("sig-banner-form");
  const bannersCard = document.getElementById("sig-banners");
  if (bannerForm && bannersCard) {
    let bannersData = {};
    try {
      const raw = document.getElementById("sig-banners-data");
      if (raw) JSON.parse(raw.textContent).forEach((b) => { bannersData[b.id] = b; });
    } catch (_) { bannersData = {}; }
    const bEls = {
      id: $("#banner-id"), name: $("#banner-name"), image: $("#banner-image"), link: $("#banner-link"),
      placement: $("#banner-placement"), weight: $("#banner-weight"), start: $("#banner-start"),
      end: $("#banner-end"), enabled: $("#banner-enabled"), title: $("#sig-banner-title"), reset: $("#sig-banner-reset"),
      scopeKind: $("#banner-scope-kind"), scopeValue: $("#banner-scope-value"), track: $("#banner-track"),
    };
    bEls.reset.addEventListener("click", () => {
      bannerForm.reset(); bEls.id.value = ""; bEls.enabled.checked = true;
      bEls.title.textContent = bannersCard.dataset.i18nNewbanner; bEls.reset.hidden = true;
    });
    document.addEventListener("click", (e) => {
      const btn = e.target.closest('[data-action="sig-banner-edit"]');
      if (!btn) return;
      e.preventDefault();
      const b = bannersData[btn.dataset.bannerId];
      if (!b) return;
      bEls.id.value = b.id; bEls.name.value = b.name || ""; bEls.image.value = b.image_url || "";
      bEls.link.value = b.link_url || ""; bEls.placement.value = b.placement || "below";
      bEls.weight.value = b.weight; bEls.start.value = b.starts_at || ""; bEls.end.value = b.ends_at || "";
      bEls.scopeKind.value = b.scope_kind || "org"; bEls.scopeValue.value = b.scope_value || "";
      bEls.track.checked = !!b.track;
      bEls.enabled.checked = !!b.enabled;
      bEls.title.textContent = bannersCard.dataset.i18nEditbanner; bEls.reset.hidden = false;
      bannersCard.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  // ── Brand kit bewerken ────────────────────────────────────────────────────
  const brandForm = document.getElementById("sig-brand-form");
  const brandCard = document.getElementById("sig-brandkits");
  if (brandForm && brandCard) {
    let brandData = {};
    try {
      const raw = document.getElementById("sig-brandkits-data");
      if (raw) JSON.parse(raw.textContent).forEach((k) => { brandData[k.id] = k; });
    } catch (_) { brandData = {}; }
    const SOCIALS = ["linkedin", "x", "facebook", "instagram", "youtube"];
    const kEls = {
      id: $("#brand-id"), name: $("#brand-name"), domain: $("#brand-domain"),
      color: $("#brand-color"), logo: $("#brand-logo"), title: $("#sig-brand-title"), reset: $("#sig-brand-reset"),
    };
    SOCIALS.forEach((n) => { kEls["social_" + n] = $("#brand-social-" + n); });
    kEls.reset.addEventListener("click", () => {
      brandForm.reset(); kEls.id.value = ""; kEls.color.value = "#6C4CE6";
      kEls.title.textContent = brandCard.dataset.i18nNewbrand; kEls.reset.hidden = true;
    });
    document.addEventListener("click", (e) => {
      const btn = e.target.closest('[data-action="sig-brand-edit"]');
      if (!btn) return;
      e.preventDefault();
      const k = brandData[btn.dataset.brandId];
      if (!k) return;
      kEls.id.value = k.id; kEls.name.value = k.name || ""; kEls.domain.value = k.domain || "";
      kEls.color.value = k.accent_color || "#6C4CE6"; kEls.logo.value = k.logo_url || "";
      const soc = k.socials || {};
      SOCIALS.forEach((n) => { if (kEls["social_" + n]) kEls["social_" + n].value = soc[n] || ""; });
      kEls.title.textContent = brandCard.dataset.i18nEditbrand; kEls.reset.hidden = false;
      brandCard.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  // ── Weergave-wissel: lijst ↔ editor, tabs en sjabloonkiezer ───────────────
  const listView = document.getElementById("sig-listview");
  const editorView = document.getElementById("sig-editorview");
  const tplScrim = document.getElementById("sig-tpl-scrim");

  function showEditor() {
    if (listView) listView.hidden = true;
    if (editorView) editorView.hidden = false;
    window.scrollTo(0, 0);
  }
  function showList() {
    if (editorView) editorView.hidden = true;
    if (listView) listView.hidden = false;
    window.scrollTo(0, 0);
  }
  function openTpl() { if (tplScrim) tplScrim.hidden = false; }
  function closeTpl() { if (tplScrim) tplScrim.hidden = true; }
  function resetBuilder() {
    els.form.reset();
    els.id.value = "";
    els.htmlSrc.value = "";
    if (els.editor) els.editor.innerHTML = "";
    els.title.textContent = builder.dataset.i18nNew;
    els.reset.hidden = true;
    // Per-handtekening-config terug naar de standaard (nieuw = ingeschakeld, alle ontvangers).
    setPerConfig({ enabled: true, recipient_scope: "all", starts_at: "", ends_at: "", exclusions: {} });
    switchEtab("etab-design");
    setMode("simple");
  }
  // ── Editor-subtabs (Ontwerp / Afzenders / Ontvangers / Datum-tijd) ────────
  function switchEtab(id) {
    document.querySelectorAll(".etab").forEach((x) =>
      x.setAttribute("aria-selected", x.dataset.etab === id ? "true" : "false"));
    document.querySelectorAll(".etab-panel").forEach((p) => { p.hidden = (p.id !== id); });
  }
  document.querySelectorAll(".etab").forEach((t) =>
    t.addEventListener("click", () => switchEtab(t.dataset.etab)));
  // Vul de per-handtekening-velden vanuit een data-sig-blob (of standaarden).
  function setPerConfig(data) {
    const en = document.getElementById("sig-enabled");
    if (en) en.checked = data.enabled !== false;
    const start = document.getElementById("sig-start"), end = document.getElementById("sig-end");
    if (start) start.value = data.starts_at || "";
    if (end) end.value = data.ends_at || "";
    const scope = data.recipient_scope || "all";
    document.querySelectorAll('input[name="recipient_scope"]').forEach((r) => { r.checked = r.value === scope; });
    const ex = data.exclusions || {};
    const exU = (ex.users || []).map(String), exG = (ex.groups || []).map(String);
    Array.from(document.getElementById("sig-ex-users")?.options || []).forEach((o) => { o.selected = exU.includes(o.value); });
    Array.from(document.getElementById("sig-ex-groups")?.options || []).forEach((o) => { o.selected = exG.includes(o.value.split("|")[0]); });
  }
  function switchTab(id) {
    document.querySelectorAll(".sig-tab").forEach((x) =>
      x.setAttribute("aria-selected", x.dataset.tab === id ? "true" : "false"));
    ["tab-sigs", "tab-brand", "tab-deliver"].forEach((s) => {
      const el = document.getElementById(s);
      if (el) el.hidden = (s !== id);
    });
  }
  document.querySelectorAll(".sig-tab").forEach((t) =>
    t.addEventListener("click", () => switchTab(t.dataset.tab)));

  document.addEventListener("click", (e) => {
    if (e.target.closest('[data-action="sig-new"]')) {
      e.preventDefault(); resetBuilder(); openTpl();
    } else if (e.target.closest('[data-action="sig-back"]')) {
      e.preventDefault(); resetBuilder(); showList();
    } else if (e.target.closest('[data-action="sig-tpl-close"]')) {
      e.preventDefault(); closeTpl();
    } else if (e.target.closest('[data-action="sig-goto-rules"]')) {
      e.preventDefault(); switchTab("tab-sigs");
      const r = document.getElementById("sig-rules");
      if (r) r.scrollIntoView({ behavior: "smooth", block: "start" });
    } else if (e.target.closest('[data-action="sig-goto-import"]')) {
      e.preventDefault(); switchTab("tab-brand");
      const im = document.getElementById("sig-import");
      if (im) im.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
  if (tplScrim) tplScrim.addEventListener("click", (e) => { if (e.target === tplScrim) closeTpl(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && tplScrim && !tplScrim.hidden) closeTpl();
  });

  renderPreview();
})();
