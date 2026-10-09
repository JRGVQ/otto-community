/* Two modals: invite a user (Beheer → Gebruikers) and add/manage members of a
   shared mailbox (Teams). Each section no-ops if its modal isn't on the page. */
(function () {
  "use strict";
  const { api, toast, $ } = window.Otto;

  // ── Invite user ────────────────────────────────────────────────────────────
  const userModal = $("#user-modal");
  if (userModal) {
    const close = () => (userModal.style.display = "none");
    document.addEventListener("click", (e) => {
      if (e.target.closest('[data-action="invite"]')) { e.preventDefault(); userModal.style.display = "flex"; $("#user-email").focus(); }
    });
    userModal.addEventListener("click", (e) => { if (e.target === userModal) close(); });
    $("#user-cancel").addEventListener("click", close);
    $("#user-save").addEventListener("click", async () => {
      const name = $("#user-name").value.trim();  // optioneel — collega vult anders zelf aan
      const email = $("#user-email").value.trim();
      const role = $("#user-role").value;
      if (!email.includes("@")) { toast("Geef een geldig e-mailadres op."); return; }
      const r = await api("POST", "/api/admin/users/invite", { name, email, role });
      if (!r.ok) { toast(r.error || T("Uitnodigen mislukt.")); return; }
      if (r.link) { window.prompt(T("Test-modus (geen mailer) — kopieer de uitnodigingslink:"), r.link); }
      if (r.toast) toast(r.toast);
      window.Otto.reloadSoon();
    });
  }

  // ── Pending-invite acties (Beheer → Gebruikers) ─────────────────────────────
  document.addEventListener("click", async (e) => {
    const resend = e.target.closest('[data-action="invite-resend"]');
    if (resend) {
      e.preventDefault();
      const r = await api("POST", `/api/admin/users/${resend.dataset.id}/invite/resend`);
      if (r.link) window.prompt(T("Test-modus (geen mailer) — kopieer de uitnodigingslink:"), r.link);
      toast(r.ok ? (r.toast || T("Uitnodiging opnieuw verstuurd.")) : (r.error || T("Versturen mislukt.")));
      return;
    }
    const revoke = e.target.closest('[data-action="invite-revoke"]');
    if (revoke) {
      e.preventDefault();
      if (!window.confirm(T("Uitnodiging intrekken? De gemailde link werkt dan niet meer."))) return;
      const r = await api("POST", `/api/admin/users/${revoke.dataset.id}/invite/revoke`);
      if (r.ok) window.Otto.reloadSoon(); else toast(r.error || "Intrekken mislukt.");
    }
  });

  // ── Add member (Teams) ──────────────────────────────────────────────────────
  const memberModal = $("#member-modal");
  if (memberModal) {
    const close = () => (memberModal.style.display = "none");
    document.addEventListener("click", (e) => {
      if (e.target.closest('[data-action="team-add-member"]')) { e.preventDefault(); memberModal.style.display = "flex"; }
    });
    memberModal.addEventListener("click", (e) => { if (e.target === memberModal) close(); });
    const cancel = $("#member-cancel"); if (cancel) cancel.addEventListener("click", close);
    const save = $("#member-save");
    if (save) save.addEventListener("click", async () => {
      const body = {
        user_id: parseInt($("#member-user").value, 10),
        role: $("#member-role").value,
        can_draft: $("#member-can-draft").checked,
        can_send: $("#member-can-send").checked,
      };
      const r = await api("POST", `/api/teams/${save.dataset.acct}/members`, body);
      if (r.ok) window.Otto.reloadSoon(); else toast(r.error || "Toevoegen mislukt.");
    });
  }

  // ── Per-member role / permission / remove (event delegation) ─────────────────
  document.addEventListener("change", async (e) => {
    const roleSel = e.target.closest("[data-team-role]");
    if (roleSel) {
      const r = await api("POST", `/api/teams/${roleSel.dataset.acct}/members/${roleSel.dataset.uid}`, { role: roleSel.value });
      toast(r.ok ? "Rol bijgewerkt." : (r.error || "Mislukt."));
      return;
    }
    const perm = e.target.closest("[data-team-perm]");
    if (perm) {
      const body = {}; body[perm.dataset.teamPerm] = perm.checked;
      const r = await api("POST", `/api/teams/${perm.dataset.acct}/members/${perm.dataset.uid}`, body);
      if (!r.ok) { toast(r.error || "Mislukt."); perm.checked = !perm.checked; }
    }
  });

  document.addEventListener("click", async (e) => {
    const rm = e.target.closest("[data-team-remove]");
    if (!rm) return;
    e.preventDefault();
    if (!window.confirm(T("Dit lid verwijderen uit het postvak?"))) return;
    const r = await api("DELETE", `/api/teams/${rm.dataset.acct}/members/${rm.dataset.uid}`);
    if (r.ok) window.Otto.reloadSoon(); else toast(r.error || T("Verwijderen mislukt."));
  });

  // ── Snippets: vaste tekstblokken van het team beheren ───────────────────────
  document.addEventListener("click", async (e) => {
    const add = e.target.closest("[data-snippet-add]");
    if (add) {
      e.preventDefault();
      const title = document.getElementById("snippet-title");
      const body = document.getElementById("snippet-body");
      if (!title.value.trim() || !body.value.trim()) {
        toast(T("Geef een titel en een tekst.")); return;
      }
      const r = await api("POST", "/api/snippets", {
        account_id: add.dataset.acct, title: title.value, body: body.value,
      });
      if (r.ok) window.Otto.reloadSoon(); else toast(r.error || T("Opslaan mislukt."));
      return;
    }
    const del = e.target.closest("[data-snippet-delete]");
    if (!del) return;
    e.preventDefault();
    if (!window.confirm(T("Dit blok verwijderen?"))) return;
    const r = await api("DELETE", `/api/snippets/${del.dataset.id}`);
    if (r.ok) window.Otto.reloadSoon(); else toast(r.error || T("Verwijderen mislukt."));
  });

  // ── Collaboration: assign a mail + set its status ────────────────────────────
  document.addEventListener("change", async (e) => {
    const a = e.target.closest('[data-action="assign-msg"]');
    if (!a) return;
    const r = await api("POST", `/api/messages/${a.dataset.id}/assign`, { user_id: a.value });
    if (r.ok) window.Otto.reloadSoon(); else toast(r.error || "Toewijzen mislukt.");
  });
  document.addEventListener("click", async (e) => {
    const s = e.target.closest('[data-action="set-state"]');
    if (!s) return;
    e.preventDefault();
    const r = await api("POST", `/api/messages/${s.dataset.id}/state`, { state: s.dataset.state });
    if (r.ok) window.Otto.reloadSoon(); else toast(r.error || "Status bijwerken mislukt.");
  });

  // Teams categories: show the folder input only when a category leaves the inbox.
  document.addEventListener("change", (e) => {
    const keep = e.target.closest(".mbcat-keep");
    if (!keep) return;
    const folder = keep.closest(".mbcat-row").querySelector(".mbcat-folder");
    if (folder) folder.style.display = keep.checked ? "none" : "";
  });

  // ── Teams: save per-mailbox category config ──────────────────────────────────
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest('[data-action="save-mbcats"]');
    if (!btn) return;
    e.preventDefault();
    const list = $("#mbcat-list");
    if (!list) return;
    const categories = Array.from(list.querySelectorAll(".mbcat-row")).map((row) => {
      const keep = row.querySelector(".mbcat-keep").checked;
      const folder = row.querySelector(".mbcat-folder");
      return {
        category_id: parseInt(row.dataset.cid, 10),
        enabled: row.querySelector(".mbcat-enabled").checked,
        draft_enabled: row.querySelector(".mbcat-draft").checked,
        ai_override: row.querySelector(".mbcat-ai").value.trim(),
        keep_in_inbox: keep,
        target_folder: keep ? "" : (folder ? folder.value.trim() : ""),
      };
    });
    const r = await api("POST", `/api/teams/${btn.dataset.acct}/categories`, { categories });
    if (r.ok) window.Otto.reloadSoon(); else toast(r.error || "Opslaan mislukt.");
  });
})();
