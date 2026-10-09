/* Task modal: full To Do feature set (notes, steps, reminder, repeat, importance) +
   destination (Otto / a To Do list / Planner with assignee). Handles create and edit. */
(function () {
  "use strict";
  const { api, $, $$, toast, reloadSoon } = window.Otto;

  const modal = $("#task-modal");
  if (!modal) return;

  const hasGraph = modal.dataset.graph === "1";
  const elTitleHdr = $("#task-modal-title");
  const elId = $("#task-id");
  const elTitle = $("#task-title");
  const elNotes = $("#task-notes");
  const elDue = $("#task-due");
  const elReminder = $("#task-reminder");
  const elRecur = $("#task-recurrence");
  const elRecurInterval = $("#task-recur-interval");
  const elRecurWrap = $("#task-recur-interval-wrap");
  const elImportant = $("#task-important");
  const elSteps = $("#task-steps");
  const stepTpl = $("#task-step-template");
  // Graph-only nodes
  const elDest = $("#task-dest");
  const elDestTodo = $("#task-dest-todo");
  const elPlannerFields = $("#task-planner-fields");
  const elPlan = $("#task-plan");
  const elBucketWrap = $("#task-bucket-wrap");
  const elBucket = $("#task-bucket");
  const elSearch = $("#task-assignee-search");
  const elResults = $("#task-assignee-results");
  const elChosen = $("#task-assignee-chosen");
  const elChosenLabel = $("#task-assignee-label");

  let messageId = null;
  let editingId = null;
  let important = false;
  let assignee = null;
  let listsLoaded = false;
  let plansLoaded = false;

  function syncRecurUI() { elRecurWrap.style.display = elRecur.value ? "" : "none"; }

  function setImportant(on) {
    important = !!on;
    elImportant.classList.toggle("on", important);
    elImportant.textContent = (important ? "★" : "☆") + " Belangrijk";
  }

  function addStepRow(title) {
    const node = stepTpl.content.firstElementChild.cloneNode(true);
    node.querySelector(".step-title").value = title || "";
    node.querySelector(".step-remove").addEventListener("click", () => node.remove());
    elSteps.appendChild(node);
  }

  function collectSteps() {
    return $$(".task-step-row", elSteps)
      .map((r) => ({ title: r.querySelector(".step-title").value.trim() }))
      .filter((s) => s.title);
  }

  function setAssignee(u) {
    assignee = u;
    if (!elChosen) return;
    if (u) {
      elChosenLabel.textContent = u.name + (u.email ? " · " + u.email : "");
      elChosen.style.display = "flex";
      elSearch.style.display = "none";
    } else {
      elChosen.style.display = "none";
      elSearch.style.display = "";
      elSearch.value = "";
    }
    elResults.innerHTML = "";
  }

  async function loadLists() {
    if (!hasGraph || listsLoaded) return;
    listsLoaded = true;
    const r = await api("GET", "/api/todo/lists");
    elDestTodo.innerHTML = "";
    (r.lists || []).filter((l) => l.synced).forEach((l) => {
      const o = document.createElement("option");
      o.value = "todo:" + l.id;
      o.textContent = l.name;
      elDestTodo.appendChild(o);
    });
    if (!elDestTodo.children.length) elDestTodo.label = "Microsoft To Do (nog geen lijst — sync eerst)";
  }

  async function loadPlans() {
    if (!hasGraph || plansLoaded) return;
    plansLoaded = true;
    const r = await api("GET", "/api/planner/plans");
    (r.plans || []).forEach((p) => {
      const o = document.createElement("option");
      o.value = p.id; o.textContent = p.title;
      elPlan.appendChild(o);
    });
    if (r.error) toast(r.error);
  }

  async function loadBuckets(planId) {
    elBucket.innerHTML = '<option value="">— Geen bucket —</option>';
    if (!planId) { elBucketWrap.style.display = "none"; return; }
    const r = await api("GET", `/api/planner/plans/${encodeURIComponent(planId)}/buckets`);
    (r.buckets || []).forEach((b) => {
      const o = document.createElement("option");
      o.value = b.id; o.textContent = b.name;
      elBucket.appendChild(o);
    });
    elBucketWrap.style.display = (r.buckets && r.buckets.length) ? "" : "none";
  }

  function onDestChange() {
    const planner = elDest.value === "planner";
    elPlannerFields.style.display = planner ? "" : "none";
    if (planner) loadPlans();
  }

  let searchTimer = null;
  function onSearch() {
    clearTimeout(searchTimer);
    const q = elSearch.value.trim();
    searchTimer = setTimeout(async () => {
      const r = await api("GET", "/api/planner/users?q=" + encodeURIComponent(q));
      elResults.innerHTML = "";
      (r.users || []).forEach((u) => {
        const row = document.createElement("div");
        row.className = "task-person";
        row.textContent = u.name + (u.email ? " · " + u.email : "");
        row.addEventListener("click", () => setAssignee(u));
        elResults.appendChild(row);
      });
    }, 250);
  }

  function open(payload) {
    payload = payload || {};
    const t = payload.task || null;
    editingId = t ? t.id : null;
    messageId = payload.message_id || null;
    elTitleHdr.textContent = editingId ? "Taak bewerken" : (messageId ? "Taak uit mail" : "Nieuwe taak");
    $("#task-save").textContent = editingId ? "Opslaan" : "Taak aanmaken";

    elTitle.value = t ? (t.title || "") : (payload.title || "");
    elNotes.value = t ? (t.notes || "") : "";
    elDue.value = t ? (t.due || "") : "";
    elReminder.value = t ? (t.reminder || "") : "";
    elRecur.value = t ? (t.recurrence || "") : "";
    elRecurInterval.value = (t && t.recurrence_interval) ? t.recurrence_interval : 1;
    syncRecurUI();
    setImportant(t ? t.important : false);

    // Steps + destination only apply to create (existing tasks manage steps on the card).
    elSteps.innerHTML = "";
    const showSteps = !editingId;
    elSteps.style.display = showSteps ? "" : "none";
    $("#task-add-step").style.display = showSteps ? "" : "none";
    if (hasGraph) {
      $("#task-dest-wrap").style.display = editingId ? "none" : "";
      elPlannerFields.style.display = "none";
      if (!editingId) {
        elDest.value = "";
        setAssignee(null);
        loadLists();
      }
    }
    modal.style.display = "flex";
    elTitle.focus();
  }

  function close() { modal.style.display = "none"; }

  async function save() {
    const title = elTitle.value.trim();
    if (!title) { toast("Geef de taak een titel."); return; }
    const btn = $("#task-save");
    btn.disabled = true;

    const base = {
      title,
      notes: elNotes.value.trim(),
      due: elDue.value || "",
      reminder: elReminder.value || "",
      recurrence: elRecur.value || "",
      recurrence_interval: parseInt(elRecurInterval.value, 10) || 1,
      important: important,
    };

    let r;
    if (editingId) {
      r = await api("PATCH", `/api/tasks/${editingId}`, base);
    } else {
      const body = Object.assign({ steps: collectSteps() }, base);
      if (hasGraph && elDest.value === "planner") {
        const planOpt = elPlan.options[elPlan.selectedIndex];
        const bucketOpt = elBucket.options[elBucket.selectedIndex];
        body.plan_id = elPlan.value;
        body.plan_name = elPlan.value && planOpt ? planOpt.textContent : "";
        body.bucket_id = elBucketWrap.style.display !== "none" ? elBucket.value : "";
        body.bucket_name = elBucket.value && bucketOpt ? bucketOpt.textContent : "";
        if (assignee) { body.assignee_id = assignee.id; body.assignee_name = assignee.name; body.assignee_email = assignee.email; }
      } else if (hasGraph && elDest.value.startsWith("todo:")) {
        body.todo_list_id = elDest.value.slice(5);
      }
      const url = messageId ? `/api/messages/${messageId}/task` : "/api/tasks";
      r = await api("POST", url, body);
    }
    btn.disabled = false;
    if (r.toast) toast(r.toast);
    if (r.ok && r.reload) { reloadSoon(); return; }
    if (!r.ok) toast(r.error || "Opslaan mislukt.");
  }

  // Wiring
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-task-new]")) { e.preventDefault(); open(null); }
    const edit = e.target.closest("[data-task-edit]");
    if (edit) { e.preventDefault(); try { open({ task: JSON.parse(edit.dataset.payload) }); } catch (x) {} }
  });
  modal.addEventListener("click", (e) => { if (e.target === modal) close(); });
  $("#task-cancel").addEventListener("click", close);
  $("#task-save").addEventListener("click", save);
  $("#task-add-step").addEventListener("click", () => addStepRow(""));
  elRecur.addEventListener("change", syncRecurUI);
  elImportant.addEventListener("click", () => setImportant(!important));
  if (hasGraph) {
    elDest.addEventListener("change", onDestChange);
    elPlan.addEventListener("change", () => loadBuckets(elPlan.value));
    elSearch.addEventListener("input", onSearch);
    $("#task-assignee-clear").addEventListener("click", () => setAssignee(null));
  }

  // Inline "+ stap toevoegen" on a task card (Enter submits).
  document.addEventListener("keydown", async (e) => {
    const el = e.target.closest(".step-add");
    if (!el || e.key !== "Enter") return;
    e.preventDefault();
    const title = el.value.trim();
    if (!title) return;
    el.disabled = true;
    const r = await api("POST", `/api/tasks/${el.dataset.task}/steps`, { title });
    if (r.ok) reloadSoon(); else { el.disabled = false; toast(r.error || "Mislukt."); }
  });

  // Snelle taak-invoer op de briefing (Enter maakt de taak meteen aan).
  document.addEventListener("keydown", async (e) => {
    const el = e.target.closest(".quick-task-add");
    if (!el || e.key !== "Enter") return;
    e.preventDefault();
    const title = el.value.trim();
    if (!title) return;
    el.disabled = true;
    const r = await api("POST", "/api/tasks", { title });
    if (r.toast) toast(r.toast);
    if (r.ok) reloadSoon(); else { el.disabled = false; }
  });

  // File attachment upload (multipart — Otto.api is JSON-only, so use fetch directly).
  const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  document.addEventListener("change", async (e) => {
    const el = e.target.closest(".attach-input");
    if (!el || !el.files || !el.files.length) return;
    const fd = new FormData();
    fd.append("file", el.files[0]);
    let data = {};
    try {
      const res = await fetch(`/api/tasks/${el.dataset.task}/attachments`, {
        method: "POST", headers: { "X-CSRFToken": csrf }, body: fd,
      });
      data = await res.json();
    } catch (x) { /* network */ }
    if (data.ok && data.reload) { reloadSoon(); return; }
    toast(data.error || "Uploaden mislukt.");
  });

  window.OttoTasks = { open };
})();
