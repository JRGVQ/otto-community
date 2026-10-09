/* Otto core: fetch wrapper (CSRF), toasts, and the delegated action dispatcher. */
(function () {
  "use strict";

  const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = {};
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok && !data.ok) data.error = data.error || ("HTTP " + res.status);
    return data;
  }

  let toastTimer = null;
  function toast(msg) {
    if (!msg) return;
    const host = $("#toast-host");
    if (!host) return;
    host.innerHTML = '<div class="toast pop"><span class="dot"></span>' + escapeHtml(msg) + "</div>";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (host.innerHTML = ""), 2600);
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function reloadSoon() { window.location.reload(); }

  // Auto-dismiss server-flashed toasts.
  function dismissFlashed() {
    $$("#toast-host [data-autodismiss]").forEach((el) => setTimeout(() => el.remove(), 2600));
  }

  // ── Generic action dispatch ───────────────────────────────────────────
  const handlers = {
    async "toggle-setting"(el) {
      const r = await api("POST", `/api/settings/${el.dataset.key}/toggle`);
      if (r.ok) el.classList.toggle("on", r.on);
    },
    async "toggle-daily-email"(el) {
      const r = await api("POST", "/api/settings/daily-email/toggle");
      if (r.ok) {
        el.classList.toggle("on", r.on);
        toast(r.on ? "Dagelijkse takenmail aan." : "Dagelijkse takenmail uit.");
      }
    },
    async "toggle-integration"(el) {
      const r = await api("POST", `/api/integrations/${el.dataset.key}/toggle`);
      if (r.ok) {
        el.classList.toggle("on", r.connected);
        el.classList.toggle("off", !r.connected);
        el.textContent = r.connected ? "Gekoppeld ✓" : "Koppelen";
        toast(r.toast);
      }
    },
    async "toggle-task"(el) {
      const r = await api("POST", `/api/tasks/${el.dataset.id}/toggle`);
      if (!r.ok) return;
      el.classList.toggle("on", r.done);
      el.classList.toggle("green", r.done);
      el.textContent = r.done ? "✓" : "";
      const title = el.parentElement.querySelector(".task-title");
      if (title) title.classList.toggle("done", r.done);
      // De "X open"-teller in de paginakop beweegt meteen mee, zonder herlaadslag.
      const counter = document.querySelector("[data-open-count]");
      if (counter) {
        const n = Math.max(0, (parseInt(counter.textContent, 10) || 0) + (r.done ? -1 : 1));
        counter.textContent = String(n);
      }
    },
    async "delete-task"(el) {
      if (!window.confirm(T("Deze taak verwijderen?"))) return;
      const r = await api("DELETE", `/api/tasks/${el.dataset.id}`);
      if (r.ok) reloadSoon(); else toast(r.error || T("Verwijderen mislukt."));
    },
    async "toggle-step"(el) {
      const r = await api("POST", `/api/tasks/${el.dataset.task}/steps/${el.dataset.id}/toggle`);
      if (!r.ok) return;
      el.classList.toggle("on", r.done);
      el.textContent = r.done ? "✓" : "";
      const label = el.nextElementSibling;
      if (label) label.classList.toggle("done", r.done);
    },
    async "delete-step"(el) {
      const r = await api("DELETE", `/api/tasks/${el.dataset.task}/steps/${el.dataset.id}`);
      if (r.ok) reloadSoon(); else toast(r.error || T("Verwijderen mislukt."));
    },
    async "delete-attachment"(el) {
      const r = await api("DELETE", `/api/tasks/${el.dataset.task}/attachments/${el.dataset.id}`);
      if (r.ok) reloadSoon(); else toast(r.error || T("Verwijderen mislukt."));
    },
    async "toggle-perm"(el) {
      const r = await api("POST", "/api/admin/perms/toggle", { role: el.dataset.role, perm: el.dataset.perm });
      if (r.locked) { toast(r.toast); return; }
      if (r.ok) { el.classList.toggle("on", r.on); el.textContent = r.on ? "✓" : ""; }
    },
    async "toggle-scope"(el) {
      const r = await api("POST", `/api/admin/scopes/${encodeURIComponent(el.dataset.name)}/toggle`);
      if (r.ok) { el.classList.toggle("on", r.on); el.textContent = r.on ? "✓" : ""; }
    },
    async "toggle-sso"(el) {
      const r = await api("POST", "/api/admin/sso/toggle", { key: el.dataset.key });
      if (r.ok) el.classList.toggle("on", r.on);
    },
    async "sso-method"(el) {
      const r = await api("POST", "/api/admin/sso/method", { method: el.dataset.method });
      if (r.ok) { $$('[data-action="sso-method"]').forEach((b) => b.classList.remove("active")); el.classList.add("active"); }
    },
    async "set-model"(el) {
      const r = await api("POST", "/api/admin/ai/model", { model: el.dataset.model });
      if (r.ok) reloadSoon();
    },
    async "toggle-ai"(el) {
      const r = await api("POST", "/api/admin/ai/toggle", { key: el.dataset.key });
      if (r.ok) el.classList.toggle("on", r.on);
    },
    async "toggle-orgpush"(el) {
      const r = await api("POST", "/api/admin/orgpush/toggle");
      if (r.ok) el.classList.toggle("on", r.on);
    },
    async "toggle-outlook-numbering"(el) {
      const r = await api("POST", "/api/admin/outlook-numbering/toggle");
      if (r.ok) {
        el.classList.toggle("on", r.on);
        if (r.toast) toast(r.toast);
        reloadSoon(); // de kaarten tonen de effectieve Outlook-naam — vers renderen
      }
    },
    async "realign-outlook-labels"(el) {
      el.disabled = true;
      const r = await api("POST", "/api/admin/outlook-labels/realign");
      el.disabled = false;
      if (r.ok && r.toast) toast(r.toast);
    },
    // Niet "recategorize" noemen: die sleutel is al van de per-mail-chip verderop, en in
    // één objectliteral wint de laatste definitie stilletjes — dan is deze knop dood.
    async "resort-inbox"(el) {
      if (!confirm(T("Alle mail in je inbox opnieuw laten sorteren? Verkeerd binnengekomen berichten worden hersteld en opnieuw gecategoriseerd."))) return;
      el.disabled = true;
      const prev = el.textContent;
      el.textContent = T("Bezig…");
      const r = await api("POST", "/api/settings/recategorize");
      el.disabled = false;
      el.textContent = prev;
      if (r.ok && r.toast) toast(r.toast);
    },
    // ── Analyse-bord: widgets herschikken / verbergen / terughalen ──
    async "widget-up"(el) {
      const card = el.closest(".an-widget");
      const prev = card && card.previousElementSibling;
      if (!prev || !prev.classList.contains("an-widget")) return;
      card.parentElement.insertBefore(card, prev);
      await saveWidgetLayout();
    },
    async "widget-down"(el) {
      const card = el.closest(".an-widget");
      const next = card && card.nextElementSibling;
      if (!next || !next.classList.contains("an-widget")) return;
      card.parentElement.insertBefore(next, card);
      await saveWidgetLayout();
    },
    async "widget-hide"(el) {
      const card = el.closest(".an-widget");
      if (!card) return;
      card.remove();
      await saveWidgetLayout({ hide: card.dataset.widget });
      reloadSoon(); // de "Verborgen:"-chips onderaan vers renderen
    },
    async "widget-show"(el) {
      await saveWidgetLayout({ show: el.dataset.widget });
      reloadSoon();
    },
    async "test"(el) {
      const r = await api("POST", "/api/admin/test", { label: el.dataset.label });
      toast(r.toast);
    },
    async "test-integration"(el) {
      el.disabled = true;
      const prev = el.textContent;
      el.textContent = "Testen…";
      const r = await api("POST", `/api/integrations/${el.dataset.key}/test`);
      el.disabled = false;
      el.textContent = prev;
      toast(r.toast || r.error || "Test mislukt.");
    },
    async "test-crm"(el) {
      el.disabled = true;
      const prev = el.textContent;
      el.textContent = "Testen…";
      const r = await api("POST", `/api/integrations/crm/${el.dataset.provider}/test`);
      el.disabled = false;
      el.textContent = prev;
      toast(r.toast || r.error || "Test mislukt.");
    },
    async "test-mailbox"(el) {
      el.disabled = true;
      const prev = el.textContent;
      el.textContent = "Testen…";
      const r = await api("POST", `/api/accounts/${el.dataset.id}/test`);
      if (r.toast) toast(r.toast);
      if (r.reload) { reloadSoon(); return; }
      el.disabled = false;
      el.textContent = prev;
    },
    async "disconnect-mailbox"(el) {
      if (!window.confirm(T("Postvak loskoppelen? Otto verwijdert de toegang en alle gecachte mails en concepten uit Otto. Je Outlook zelf blijft ongewijzigd."))) return;
      const r = await api("DELETE", `/api/accounts/${el.dataset.id}`);
      if (r.ok) { toast(r.toast); reloadSoon(); }
      else toast(r.error || "Loskoppelen mislukt.");
    },
    async "recategorize-all"(el) {
      el.disabled = true;
      const prev = el.textContent;
      el.textContent = "Bezig…";
      const r = await api("POST", "/api/accounts/recategorize");
      if (r.toast) toast(r.toast);
      if (r.reload) { reloadSoon(); return; }
      el.disabled = false;
      el.textContent = prev;
    },
    async "sync-now"(el) {
      el.disabled = true;
      const prev = el.textContent;
      el.textContent = "Bezig…";
      const r = await api("POST", "/api/accounts/sync");
      if (r.toast) toast(r.toast);
      if (r.reload) { reloadSoon(); return; }
      el.disabled = false;
      el.textContent = prev;
    },
    async "archive"(el) {
      const r = await api("POST", `/api/messages/${el.dataset.id}/archive`);
      toast(r.toast);
    },
    async "unsubscribe"(el) {
      if (!window.confirm(T("Uitschrijven bij deze afzender? We verwerken het uitschrijfverzoek en houden er een log van bij."))) return;
      el.disabled = true;
      const r = await api("POST", `/api/messages/${el.dataset.id}/unsubscribe`);
      el.disabled = false;
      // One-click succeeds server-side (no URL); a link/mailto is opened in a new tab.
      if (r.open_url) window.open(r.open_url, "_blank", "noopener");
      if (r.toast) toast(r.toast); else if (r.error) toast(r.error);
      if (r.reload) reloadSoon();
    },
    async "unsubscribe-all"(el) {
      if (!window.confirm(T("Otto schrijft je uit bij alle afzenders in deze weergave die automatisch uitschrijven ondersteunen, en houdt er een log van bij. Doorgaan?"))) return;
      const prev = el.textContent;
      el.disabled = true;
      const r = await api("POST", "/api/messages/unsubscribe-all", {
        cat: el.dataset.cat || "", account_id: el.dataset.accountId || "",
      });
      el.disabled = false; el.textContent = prev;
      if (r.toast) toast(r.toast); else if (r.error) toast(r.error);
      if (r.reload) reloadSoon();
    },
    async "task"(el) {
      // Open the task modal prefilled from the mail (plan + assignee picker live there);
      // fall back to a direct create if the modal isn't on the page for some reason.
      if (window.OttoTasks) {
        window.OttoTasks.open({ message_id: el.dataset.id, title: el.dataset.subject || "" });
        return;
      }
      const r = await api("POST", `/api/messages/${el.dataset.id}/task`);
      if (r.toast) toast(r.toast);
      if (r.reload) reloadSoon();
    },
    async "recategorize"(el) {
      const r = await api("POST", `/api/messages/${el.dataset.id}/recategorize`, { category: el.dataset.cat });
      if (r.ok) reloadSoon(); else toast(r.error || T("Herindelen mislukt."));
    },
    async "send"(el) {
      // De else-tak is essentieel: zonder haar herlaadde de pagina óók na een MISLUKTE
      // verzending, waardoor de gebruiker denkt dat de mail vertrokken is terwijl er niets
      // verstuurd werd. Bij verzenden moet een fout altijd zichtbaar zijn.
      const r = await api("POST", `/api/drafts/${el.dataset.id}/send`);
      if (r.ok) reloadSoon(); else toast(r.error || T("Verzenden mislukt."));
    },
    async "discard"(el) {
      const r = await api("POST", `/api/drafts/${el.dataset.id}/discard`);
      if (r.ok) reloadSoon(); else toast(r.error || T("Verwijderen mislukt."));
    },
    async "force"(el) {
      // "Concept opstellen" forwards the user's instruction (compose from your words);
      // "Leeg concept" (data-blank) makes an empty template.
      const input = $("#compose-intent");
      const intent = el.dataset.blank ? "" : ((input && input.value) || "").trim();
      const r = await api("POST", `/api/drafts/${el.dataset.id}/force`, intent ? { intent } : {});
      if (r.ok) reloadSoon(); else if (r.error) toast(r.error);
    },
    async "regenerate"(el) {
      // The "Pas concept aan" button forwards the user's steer; the plain "Opnieuw
      // genereren" button (data-plain) ignores it and regenerates from scratch.
      const intentEl = $("#draft-intent");
      const intent = el.dataset.plain ? "" : ((intentEl && intentEl.value) || "").trim();
      const r = await api("POST", `/api/drafts/${el.dataset.id}/regenerate`, intent ? { intent } : {});
      if (r.ok) {
        const ta = $("#draft-text");
        if (ta && r.text != null) ta.value = r.text;
        toast(r.toast);
      }
    },
    async "followup-send"(el) {
      const ta = $(`#fu-text-${el.dataset.id}`);
      if (ta) await api("PUT", `/api/follow-ups/${el.dataset.id}`, { text: ta.value });
      const r = await api("POST", `/api/follow-ups/${el.dataset.id}/send`);
      if (r.ok) reloadSoon();
      else if (r.error) toast(r.error);
    },
    async "followup-discard"(el) {
      const r = await api("POST", `/api/follow-ups/${el.dataset.id}/discard`);
      if (r.ok) reloadSoon();
    },
    async "tone"(el) {
      // Only the preferred tone is generated up front; another tone is produced on demand.
      const r = await api("POST", `/api/drafts/${el.dataset.id}/tone`, { tone: el.dataset.tone });
      if (r.ok) {
        const ta = $("#draft-text");
        if (ta && r.text != null) ta.value = r.text;
        $$('[data-action="tone"]').forEach((b) => b.classList.remove("active"));
        el.classList.add("active");
      }
    },
    async "add-shared"(el) {
      const wrap = el.closest("[data-shared-add]");
      const input = wrap ? wrap.querySelector("input") : null;
      const address = ((input && input.value) || "").trim();
      if (!address) { toast("Geef een e-mailadres op."); return; }
      el.disabled = true;
      const r = await api("POST", "/api/shared-mailboxes", { address });
      el.disabled = false;
      if (r.ok) { toast(r.toast); reloadSoon(); }
      else toast(r.error || "Koppelen mislukt.");
    },
    async "leave-shared"(el) {
      if (!window.confirm(T("Dit gedeelde postvak verlaten?"))) return;
      const r = await api("POST", `/api/shared-mailboxes/${el.dataset.id}/leave`);
      if (r.ok) { toast(r.toast); reloadSoon(); }
      else toast(r.error || "Mislukt.");
    },
    async "add-knowledge"(el) {
      const wrap = el.closest("[data-knowledge-add]");
      const name = ((wrap && wrap.querySelector('input[name="name"]').value) || "").trim();
      const site_url = ((wrap && wrap.querySelector('input[name="site_url"]').value) || "").trim();
      if (!site_url) { toast("Geef de SharePoint-URL op."); return; }
      el.disabled = true;
      const r = await api("POST", "/api/knowledge", { name, site_url });
      el.disabled = false;
      if (r.ok) { toast(r.toast); reloadSoon(); }
      else toast(r.error || "Koppelen mislukt.");
    },
    async "remove-knowledge"(el) {
      if (!window.confirm(T("Deze kennisbron verwijderen?"))) return;
      const r = await api("DELETE", `/api/knowledge/${el.dataset.id}`);
      if (r.ok) { toast(r.toast); reloadSoon(); }
      else toast(r.error || "Mislukt.");
    },
    async "add-note"(el) {
      const wrap = el.closest("[data-note-add]");
      const input = wrap ? wrap.querySelector("textarea, input") : null;
      const body = ((input && input.value) || "").trim();
      if (!body) return;
      el.disabled = true;
      const r = await api("POST", `/api/messages/${el.dataset.id}/notes`, { body });
      el.disabled = false;
      if (!r.ok) { toast(r.error || "Mislukt."); return; }
      toast(r.toast);
      if (input) input.value = "";
      const list = $("#note-list");
      if (list && r.note) {
        const empty = $("#note-empty"); if (empty) empty.remove();
        const item = document.createElement("div");
        item.className = "note-item";
        item.style.cssText = "border:1px solid var(--border); border-radius:10px; padding:9px 12px;";
        const meta = document.createElement("div");
        meta.style.cssText = "font-size:11.5px; color:var(--muted); font-weight:700;";
        meta.textContent = r.note.author + " · " + r.note.when;
        const bodyEl = document.createElement("div");
        bodyEl.style.cssText = "font-size:13px; white-space:pre-wrap;";
        bodyEl.textContent = r.note.body;
        item.appendChild(meta); item.appendChild(bodyEl); list.appendChild(item);
      } else { reloadSoon(); }
    },
  };

  // Analyse-bord: de indeling wordt uit de DOM zelf afgelezen (volgorde van de
  // kaarten + de chips van verborgen widgets), zodat er geen aparte client-state is.
  function collectWidgetOrder() {
    return Array.from(document.querySelectorAll(".an-widget")).map((c) => c.dataset.widget);
  }
  function collectHiddenWidgets() {
    return Array.from(document.querySelectorAll('[data-action="widget-show"]')).map((c) => c.dataset.widget);
  }
  // Eén POST-pad voor alle vier de widget-acties: {hide} verhuist een key naar
  // verborgen, {show} haalt hem terug; zonder opties wordt alleen de volgorde bewaard.
  async function saveWidgetLayout(opts) {
    const o = opts || {};
    const order = collectWidgetOrder().filter((k) => k !== o.hide);
    const hidden = collectHiddenWidgets().filter((k) => k !== o.show);
    if (o.hide) hidden.push(o.hide);
    if (o.show) order.push(o.show);
    const r = await api("POST", "/api/analytics/layout", { order, hidden });
    if (!r.ok) toast(r.error || T("Opslaan mislukt."));
  }

  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const fn = handlers[el.dataset.action];
    if (fn) { e.preventDefault(); fn(el); }
  });

  // Role <select> change.
  document.addEventListener("change", async (e) => {
    const el = e.target.closest('[data-action="change-role"]');
    if (!el) return;
    const r = await api("POST", `/api/admin/users/${el.dataset.id}/role`, { role: el.value });
    toast(r.toast);
  });

  // Shared-mailbox switcher in the team inbox rail — jump straight to the chosen mailbox.
  document.addEventListener("change", (e) => {
    const el = e.target.closest("[data-team-switch]");
    if (!el) return;
    window.location.href = `/app/teams/${el.value}`;
  });

  // CRM: standaardcategorie per bedrijf/contact (select change → opslaan).
  document.addEventListener("change", async (e) => {
    const company = e.target.closest('[data-action="crm-company-cat"]');
    const contact = e.target.closest('[data-action="crm-contact-cat"]');
    const el = company || contact;
    if (!el) return;
    const url = company
      ? `/api/crm/company/${el.dataset.id}/category`
      : `/api/crm/contact/${el.dataset.id}/category`;
    const r = await api("POST", url, { category_id: el.value || "" });
    if (r.toast) toast(r.toast); else if (r.error) toast(r.error);
  });

  // CRM: nieuw project (form submit).
  document.addEventListener("submit", async (e) => {
    const form = e.target.closest('[data-action="crm-project-new"]');
    if (!form) return;
    e.preventDefault();
    const name = (form.querySelector('[name="name"]') || {}).value || "";
    if (!name.trim()) return;
    const r = await api("POST", "/api/crm/projects", { name });
    if (r.ok) reloadSoon(); else toast(r.error || T("Opslaan mislukt."));
  });

  // CRM: project verwijderen / mail loskoppelen.
  document.addEventListener("click", async (e) => {
    const del = e.target.closest('[data-action="crm-project-delete"]');
    const rm = e.target.closest('[data-action="crm-project-remove-msg"]');
    if (del) {
      e.preventDefault(); e.stopPropagation();
      if (!confirm(T("Dit project verwijderen? De gekoppelde mail zelf blijft bestaan."))) return;
      const r = await api("POST", `/api/crm/projects/${del.dataset.id}/delete`);
      if (r.ok) reloadSoon(); else toast(r.error || T("Verwijderen mislukt."));
    } else if (rm) {
      e.preventDefault();
      const r = await api("POST", `/api/crm/projects/${rm.dataset.project}/messages/${rm.dataset.id}/delete`);
      if (r.ok) reloadSoon(); else toast(r.error || T("Verwijderen mislukt."));
    }
  });

  // CRM: sleep een mail op een projectkaart (HTML5 drag-and-drop).
  document.addEventListener("dragstart", (e) => {
    const msg = e.target.closest(".crm-drag-msg");
    if (!msg) return;
    e.dataTransfer.setData("text/otto-message", msg.dataset.message);
    e.dataTransfer.effectAllowed = "copy";
  });
  document.addEventListener("dragover", (e) => {
    const drop = e.target.closest(".crm-project-drop");
    if (!drop) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    const hint = drop.querySelector(".crm-drop-hint");
    if (hint) hint.style.display = "block";
  });
  document.addEventListener("dragleave", (e) => {
    const drop = e.target.closest(".crm-project-drop");
    const hint = drop && drop.querySelector(".crm-drop-hint");
    if (hint && !drop.contains(e.relatedTarget)) hint.style.display = "none";
  });
  document.addEventListener("drop", async (e) => {
    const drop = e.target.closest(".crm-project-drop");
    if (!drop) return;
    e.preventDefault();
    const hint = drop.querySelector(".crm-drop-hint");
    if (hint) hint.style.display = "none";
    const messageId = e.dataTransfer.getData("text/otto-message");
    if (!messageId) return;
    const r = await api("POST", `/api/crm/projects/${drop.dataset.project}/messages`, { message_id: messageId });
    if (r.toast) toast(r.toast); else if (r.error) toast(r.error);
  });

  // Expand an earlier (collapsed) message in a conversation thread.
  document.addEventListener("click", (e) => {
    const card = e.target.closest("[data-thread-toggle]");
    if (card) card.classList.toggle("thread-collapsed");
  });

  // AI numeric/range inputs (debounced).
  let aiTimer = null;
  document.addEventListener("input", (e) => {
    const el = e.target.closest('[data-action="ai-value"]');
    if (!el) return;
    if (el.dataset.out) { const out = document.getElementById(el.dataset.out); if (out) out.textContent = el.value; }
    clearTimeout(aiTimer);
    aiTimer = setTimeout(() => api("POST", "/api/admin/ai/value", { key: el.dataset.key, value: el.value }), 300);
  });

  document.addEventListener("DOMContentLoaded", () => { dismissFlashed(); });

  // ── Theme toggle (light/dark) ─────────────────────────────────────────
  window.ottoToggleTheme = function () {
    const el = document.documentElement;
    const goingDark = el.getAttribute("data-theme") !== "dark";
    if (goingDark) el.setAttribute("data-theme", "dark");
    else el.removeAttribute("data-theme");
    const val = goingDark ? "dark" : "light";
    try { localStorage.setItem("theme", val); } catch (e) {}
    document.cookie = "theme=" + val + ";path=/;max-age=31536000;samesite=lax";
  };

  // ── Mobile navigation drawer (hamburger toggles the off-canvas sidebar) ─
  window.ottoToggleNav = function () {
    const shell = document.querySelector(".otto-shell");
    if (shell) shell.classList.toggle("nav-open");
  };

  window.Otto = { api, toast, $, $$, reloadSoon };
})();
