"""Microsoft Planner integration: provider mapping, picker endpoints, task creation.

The Graph HTTP layer is mocked at the OutlookProvider class level — no network.
"""
import urllib.error

from app.extensions import db
from app.models import MailAccount, Task, User
from app.providers.outlook import OutlookProvider
from app.providers.planner import PlannerProvider
from app.seed.demo_data import DEMO_PASSWORD


class _Acct:
    """Minimal MailAccount stand-in (the patched HTTP layer ignores it)."""
    pass


def _demo(app) -> User:
    return db.session.query(User).filter_by(email="demo@example.com").first()


def _connect_outlook(user) -> MailAccount:
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email="demo@acme.be", status="connected")
    db.session.add(acct)
    db.session.commit()
    return acct


# ── Provider: reads ───────────────────────────────────────────────────────────
def test_list_plans_maps_titles(monkeypatch):
    monkeypatch.setattr(OutlookProvider, "_get",
                        lambda self, path: {"value": [{"id": "p1", "title": "Sales"}, {"id": "p2"}]})
    plans = PlannerProvider(_Acct()).list_plans()
    assert plans == [{"id": "p1", "title": "Sales"}, {"id": "p2", "title": "(naamloos plan)"}]


def test_list_buckets_maps_names(monkeypatch):
    seen = []
    monkeypatch.setattr(OutlookProvider, "_get",
                        lambda self, path: seen.append(path) or {"value": [{"id": "b1", "name": "Te doen"}]})
    buckets = PlannerProvider(_Acct()).list_buckets("plan-9")
    assert buckets == [{"id": "b1", "name": "Te doen"}]
    assert "/planner/plans/plan-9/buckets" in seen[-1]


def test_search_users_filters_and_maps(monkeypatch):
    seen = []
    monkeypatch.setattr(OutlookProvider, "_get", lambda self, path: seen.append(path) or {
        "value": [{"id": "u1", "displayName": "Jan", "mail": "jan@acme.be"}]})
    users = PlannerProvider(_Acct()).search_users("ja")
    assert users == [{"id": "u1", "name": "Jan", "email": "jan@acme.be"}]
    assert "$filter=" in seen[-1] and "startswith" in seen[-1]


def test_search_users_falls_back_when_filter_rejected(monkeypatch):
    calls = []

    def fake_get(self, path):
        calls.append(path)
        if "$filter=" in path:
            raise urllib.error.HTTPError(path, 400, "bad filter", {}, None)
        return {"value": [{"id": "u2", "userPrincipalName": "piet@acme.be"}]}

    monkeypatch.setattr(OutlookProvider, "_get", fake_get)
    users = PlannerProvider(_Acct()).search_users("zz")
    assert users == [{"id": "u2", "name": "piet@acme.be", "email": "piet@acme.be"}]
    assert len(calls) == 2  # filtered attempt + unfiltered fallback


# ── Provider: create ──────────────────────────────────────────────────────────
def test_create_task_builds_assignment_payload(app, monkeypatch):
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)
    posts = []
    monkeypatch.setattr(OutlookProvider, "_post",
                        lambda self, path, payload: posts.append((path, payload)) or {"id": "task-1"})
    tid = PlannerProvider(_Acct()).create_task(
        "plan-9", "Bel de klant", bucket_id="b1", assignee_ids=["u1"], due_iso="2026-07-01T00:00:00Z")
    assert tid == "task-1"
    path, payload = posts[-1]
    assert path == "/planner/tasks"
    assert payload["planId"] == "plan-9" and payload["bucketId"] == "b1"
    assert payload["title"] == "Bel de klant" and payload["dueDateTime"] == "2026-07-01T00:00:00Z"
    a = payload["assignments"]["u1"]
    assert a["@odata.type"] == "#microsoft.graph.plannerAssignment" and a["orderHint"]


def test_create_task_raises_when_not_configured(app, monkeypatch):
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", False)
    from app.providers.exceptions import ProviderNotConfigured

    try:
        PlannerProvider(_Acct()).create_task("plan-9", "x")
        raise AssertionError("expected ProviderNotConfigured")
    except ProviderNotConfigured:
        pass


# ── Picker endpoints ──────────────────────────────────────────────────────────
def test_planner_plans_endpoint_degrades_without_graph(app, client, monkeypatch):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", False)
    r = client.get("/api/planner/plans").get_json()
    assert r["ok"] and r["configured"] is False and r["plans"] == []


def test_planner_plans_endpoint_lists_when_configured(app, client, monkeypatch):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    _connect_outlook(_demo(app))
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)
    monkeypatch.setattr(OutlookProvider, "_get",
                        lambda self, path: {"value": [{"id": "p1", "title": "Sales"}]})
    r = client.get("/api/planner/plans").get_json()
    assert r["configured"] is True and r["plans"] == [{"id": "p1", "title": "Sales"}]


# ── Task creation ─────────────────────────────────────────────────────────────
def test_create_standalone_task_creates_local(app, client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    before = db.session.query(Task).count()
    r = client.post("/api/tasks", json={"title": "Offerte nakijken", "due": "2026-07-01"}).get_json()
    assert r["ok"] and r["reload"]
    assert db.session.query(Task).count() == before + 1
    t = db.session.query(Task).order_by(Task.id.desc()).first()
    assert t.title == "Offerte nakijken" and t.due_display == "01-07-2026"


def test_create_standalone_task_requires_title(app, client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    r = client.post("/api/tasks", json={"title": "   "})
    assert r.status_code == 400


def test_create_task_stores_planner_fields_without_push(app, client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    r = client.post("/api/tasks", json={
        "title": "Bel klant", "plan_id": "plan-1", "plan_name": "Sales",
        "assignee_id": "u1", "assignee_name": "Jan Peeters", "assignee_email": "jan@acme.be",
    }).get_json()
    assert r["ok"]
    t = db.session.query(Task).order_by(Task.id.desc()).first()
    assert t.plan_id == "plan-1" and t.plan_name == "Sales"
    assert t.assignee_name == "Jan Peeters" and t.provider_task_id is None  # not pushed: Graph off


def test_create_task_pushes_to_planner_when_configured(app, client, monkeypatch):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    _connect_outlook(_demo(app))
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)
    posts = []
    monkeypatch.setattr(OutlookProvider, "_post",
                        lambda self, path, payload: posts.append((path, payload)) or {"id": "task-77"})
    r = client.post("/api/tasks", json={
        "title": "Bel klant", "plan_id": "plan-1", "plan_name": "Sales",
        "bucket_id": "b1", "assignee_id": "u1", "assignee_name": "Jan",
    }).get_json()
    assert r["ok"] and "Planner" in r["toast"]
    t = db.session.query(Task).order_by(Task.id.desc()).first()
    assert t.provider_task_id == "task-77"
    path, payload = posts[-1]
    assert path == "/planner/tasks" and payload["planId"] == "plan-1"
    assert payload["assignments"]["u1"]["@odata.type"] == "#microsoft.graph.plannerAssignment"


def test_tasks_page_has_add_button_and_modal(app, client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    html = client.get("/app/tasks").get_data(as_text=True)
    assert "Taak toevoegen" in html and 'id="task-modal"' in html
