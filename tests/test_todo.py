"""Microsoft To Do two-way sync: rich local fields, pull mapping, push on
create / toggle / steps, plus Phase 2 (etag conflict, recurrence interval).
Graph HTTP is mocked at the OutlookProvider class level.
"""
import io
import urllib.error
from datetime import date

from app.extensions import db
from app.models import MailAccount, Task, TaskAttachment, TodoList, User
from app.providers.outlook import OutlookProvider
from app.providers.todo import TodoProvider
from app.seed.demo_data import DEMO_PASSWORD
from app.tasks import todo_sync


class _AcctStub:
    """Minimal MailAccount stand-in for provider unit tests (HTTP layer is mocked)."""
    pass


def _login(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})


def _demo() -> User:
    return db.session.query(User).filter_by(email="demo@example.com").first()


def _connect_outlook(user) -> MailAccount:
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email="demo@acme.be", status="connected")
    db.session.add(acct)
    db.session.commit()
    return acct


def _synced_list(user) -> TodoList:
    lst = TodoList(organization_id=user.organization_id, user_id=user.id,
                   name="Werk", provider_list_id="L1")
    db.session.add(lst)
    db.session.commit()
    return lst


# ── Rich local task (no Graph) ────────────────────────────────────────────────
def test_create_local_task_stores_all_todo_fields(app, client):
    _login(client)
    r = client.post("/api/tasks", json={
        "title": "Plan opmaken", "notes": "met de cijfers van Q2",
        "due": "2026-07-02", "reminder": "2026-07-02T09:00",
        "recurrence": "weekly", "important": True,
        "steps": [{"title": "cijfers ophalen"}, {"title": "concept maken"}],
    }).get_json()
    assert r["ok"]
    t = db.session.query(Task).order_by(Task.id.desc()).first()
    assert t.provider_kind == "local"
    assert t.notes == "met de cijfers van Q2" and t.important and t.recurrence == "weekly"
    assert t.due_date == date(2026, 7, 2) and t.reminder_at is not None
    assert [s.title for s in t.steps] == ["cijfers ophalen", "concept maken"]


# ── Push on create (To Do destination) ────────────────────────────────────────
def test_create_todo_task_pushes_task_and_steps(app, client, monkeypatch):
    _login(client)
    user = _demo()
    _connect_outlook(user)
    lst = _synced_list(user)
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)
    posts = []
    monkeypatch.setattr(OutlookProvider, "_post", lambda self, path, payload: posts.append(path) or {
        "id": "T1", "@odata.etag": 'W/"1"', "lastModifiedDateTime": "2026-07-02T10:00:00.0000000Z"})
    r = client.post("/api/tasks", json={
        "title": "Bel de klant", "todo_list_id": lst.id, "steps": [{"title": "nummer zoeken"}],
    }).get_json()
    assert r["ok"] and "To Do" in r["toast"]
    t = db.session.query(Task).order_by(Task.id.desc()).first()
    assert t.provider_kind == "todo" and t.provider_task_id == "T1" and t.todo_list_id == lst.id
    assert t.provider_etag == 'W/"1"' and t.provider_last_modified is not None
    assert any(p == "/me/todo/lists/L1/tasks" for p in posts)            # task created
    assert any("checklistItems" in p for p in posts)                     # step created


# ── Push on toggle ────────────────────────────────────────────────────────────
def test_toggle_done_pushes_status_to_todo(app, client, monkeypatch):
    _login(client)
    user = _demo()
    _connect_outlook(user)
    lst = _synced_list(user)
    task = Task(organization_id=user.organization_id, user_id=user.id, title="x",
                provider_kind="todo", provider_task_id="T9", todo_list_id=lst.id,
                status="notStarted", done=False)
    db.session.add(task)
    db.session.commit()
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)
    patches = []
    monkeypatch.setattr(OutlookProvider, "_patch",
                        lambda self, path, payload, extra_headers=None: patches.append((path, payload)) or {
                            "@odata.etag": 'W/"2"', "lastModifiedDateTime": "2026-07-02T11:00:00.0000000Z"})
    r = client.post(f"/api/tasks/{task.id}/toggle").get_json()
    assert r["done"] is True
    assert patches and patches[-1][0] == "/me/todo/lists/L1/tasks/T9"
    assert patches[-1][1]["status"] == "completed"


# ── Pull (Graph → Otto) ───────────────────────────────────────────────────────
def test_pull_todo_imports_list_task_and_steps(app, monkeypatch):
    user = _demo()
    acct = _connect_outlook(user)
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)

    def get_abs(self, path):
        if path == "/me/todo/lists":
            return {"value": [{"id": "L1", "displayName": "Werk", "wellknownListName": "defaultList"}]}
        if "tasks" in path:  # delta endpoint
            return {"value": [{
                "id": "T1", "title": "Bel klant", "status": "notStarted", "importance": "high",
                "body": {"content": "notitie", "contentType": "text"},
                "dueDateTime": {"dateTime": "2026-07-03T08:00:00.0000000", "timeZone": "Europe/Brussels"},
                "lastModifiedDateTime": "2026-07-03T07:00:00.0000000Z",
            }], "@odata.deltaLink": "DLINK"}
        return {"value": []}

    def get(self, path):
        if "checklistItems" in path:
            return {"value": [{"id": "S1", "displayName": "stap 1", "isChecked": False}]}
        return {"value": []}

    monkeypatch.setattr(OutlookProvider, "_get_abs", get_abs)
    monkeypatch.setattr(OutlookProvider, "_get", get)

    n = todo_sync.pull_todo(acct)
    assert n == 1
    t = db.session.query(Task).filter_by(provider_kind="todo", provider_task_id="T1").first()
    assert t and t.title == "Bel klant" and t.important and t.notes == "notitie"
    assert t.due_date == date(2026, 7, 3)
    assert [s.title for s in t.steps] == ["stap 1"]
    lst = db.session.query(TodoList).filter_by(provider_list_id="L1").first()
    assert lst and lst.delta_link == "DLINK"


def test_pull_todo_skips_unchanged_and_handles_removal(app, monkeypatch):
    user = _demo()
    acct = _connect_outlook(user)
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)
    lst = _synced_list(user)
    from app.models.base import utcnow
    existing = Task(organization_id=user.organization_id, user_id=user.id, title="oud",
                    provider_kind="todo", provider_task_id="T1", todo_list_id=lst.id,
                    provider_last_modified=utcnow())
    db.session.add(existing)
    db.session.commit()

    # A delta feed: T1 unchanged (older lastModified) + T2 a deletion tombstone.
    def get_abs(self, path):
        if path == "/me/todo/lists":
            return {"value": [{"id": "L1", "displayName": "Werk"}]}
        if "tasks" in path:
            return {"value": [
                {"id": "T1", "title": "oud", "lastModifiedDateTime": "2000-01-01T00:00:00Z"},
                {"id": "T2", "@removed": {"reason": "deleted"}},
            ], "@odata.deltaLink": "DL2"}
        return {"value": []}

    monkeypatch.setattr(OutlookProvider, "_get_abs", get_abs)
    monkeypatch.setattr(OutlookProvider, "_get", lambda self, path: {"value": []})
    todo_sync.pull_todo(acct)
    assert existing.title == "oud"  # not clobbered: remote not newer


# ── List + edit + delete endpoints ────────────────────────────────────────────
def test_todo_lists_endpoint(app, client):
    _login(client)
    _synced_list(_demo())
    r = client.get("/api/todo/lists").get_json()
    assert r["ok"] and any(l["name"] == "Werk" and l["synced"] for l in r["lists"])


def test_update_and_delete_task(app, client):
    _login(client)
    r = client.post("/api/tasks", json={"title": "tijdelijk"}).get_json()
    tid = db.session.query(Task).order_by(Task.id.desc()).first().id
    r = client.patch(f"/api/tasks/{tid}", json={"title": "hernoemd", "important": True}).get_json()
    assert r["ok"]
    db.session.expire_all()
    assert db.session.get(Task, tid).title == "hernoemd" and db.session.get(Task, tid).important
    r = client.delete(f"/api/tasks/{tid}").get_json()
    assert r["ok"] and db.session.get(Task, tid) is None


# ── Phase 2: etag conflict, recurrence interval, inline step add ──────────────
def test_update_task_sends_if_match_etag(app, monkeypatch):
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)
    seen = {}
    monkeypatch.setattr(OutlookProvider, "_patch",
                        lambda self, path, payload, extra_headers=None: seen.update(h=extra_headers) or {"@odata.etag": 'W/"x"'})
    TodoProvider(_AcctStub()).update_task("L1", "T1", {"status": "completed"}, etag='W/"abc"')
    assert seen["h"] == {"If-Match": 'W/"abc"'}


def test_update_task_resolves_412_by_refetch_and_retry(app, monkeypatch):
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)
    calls = {"patch": 0}

    def patch(self, path, payload, extra_headers=None):
        calls["patch"] += 1
        if calls["patch"] == 1:  # remote moved on since our etag
            raise urllib.error.HTTPError(path, 412, "precondition failed", {}, None)
        return {"@odata.etag": 'W/"new"'}

    monkeypatch.setattr(OutlookProvider, "_patch", patch)
    monkeypatch.setattr(OutlookProvider, "_get", lambda self, path: {"@odata.etag": 'W/"fresh"'})
    out = TodoProvider(_AcctStub()).update_task("L1", "T1", {"status": "completed"}, etag='W/"old"')
    assert calls["patch"] == 2 and out["@odata.etag"] == 'W/"new"'  # refetched + retried once


def test_recurrence_interval_round_trip():
    p = TodoProvider(_AcctStub())
    payload = p._task_payload({"recurrence": "weekly", "recurrence_interval": 2, "due_date": date(2026, 7, 1)})
    assert payload["recurrence"]["pattern"] == {"type": "weekly", "interval": 2}
    assert TodoProvider.interval_from_graph({"pattern": {"type": "weekly", "interval": 3}}) == 3
    assert TodoProvider.interval_from_graph(None) == 1


def test_create_task_stores_recurrence_interval(app, client):
    _login(client)
    client.post("/api/tasks", json={"title": "Sprintreview", "recurrence": "weekly", "recurrence_interval": 2})
    t = db.session.query(Task).order_by(Task.id.desc()).first()
    assert t.recurrence == "weekly" and t.recurrence_interval == 2


def test_add_step_to_existing_task(app, client):
    _login(client)
    client.post("/api/tasks", json={"title": "met stappen"})
    t = db.session.query(Task).order_by(Task.id.desc()).first()
    r = client.post(f"/api/tasks/{t.id}/steps", json={"title": "eerste stap"}).get_json()
    assert r["ok"]
    db.session.expire_all()
    steps = db.session.get(Task, t.id).steps
    assert [s.title for s in steps] == ["eerste stap"]


# ── Phase 3: attachments ──────────────────────────────────────────────────────
def _todo_task(user, lst) -> Task:
    task = Task(organization_id=user.organization_id, user_id=user.id, title="met bijlage",
                provider_kind="todo", provider_task_id="T1", todo_list_id=lst.id)
    db.session.add(task)
    db.session.commit()
    return task


def test_create_attachment_payload(app, monkeypatch):
    import base64
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)
    posts = []
    monkeypatch.setattr(OutlookProvider, "_post", lambda self, path, payload: posts.append((path, payload)) or {
        "id": "A1", "name": "x.pdf", "contentType": "application/pdf", "size": 3})
    TodoProvider(_AcctStub()).create_attachment("L1", "T1", "x.pdf", "application/pdf", b"abc")
    path, payload = posts[-1]
    assert path == "/me/todo/lists/L1/tasks/T1/attachments"
    assert payload["@odata.type"] == "#microsoft.graph.taskFileAttachment"
    assert payload["contentBytes"] == base64.b64encode(b"abc").decode()


def test_attachment_content_returns_raw_bytes(app, monkeypatch):
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)
    monkeypatch.setattr(OutlookProvider, "_send",
                        lambda self, method, path, payload=None, extra_headers=None: b"FILEDATA")
    assert TodoProvider(_AcctStub()).attachment_content("L1", "T1", "A1") == b"FILEDATA"


def test_add_attachment_local_task_rejected(app, client):
    _login(client)
    client.post("/api/tasks", json={"title": "lokaal"})
    t = db.session.query(Task).order_by(Task.id.desc()).first()
    from app.tasks import task_ops
    att, err = task_ops.add_attachment(t, "x.txt", "text/plain", b"hi")
    assert att is None and "To Do" in err


def test_add_attachment_too_large_rejected(app, client, monkeypatch):
    _login(client)
    user = _demo()
    _connect_outlook(user)
    task = _todo_task(user, _synced_list(user))
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)
    monkeypatch.setattr("app.tasks.task_ops.ATTACH_MAX_BYTES", 5)
    from app.tasks import task_ops
    att, err = task_ops.add_attachment(task, "big.bin", "application/octet-stream", b"123456")
    assert att is None and "groot" in err.lower()


def test_pull_imports_attachments(app, monkeypatch):
    user = _demo()
    acct = _connect_outlook(user)
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)

    def get_abs(self, path):
        if path == "/me/todo/lists":
            return {"value": [{"id": "L1", "displayName": "Werk"}]}
        if "tasks" in path:
            return {"value": [{"id": "T1", "title": "met bijlage",
                               "lastModifiedDateTime": "2026-07-04T07:00:00Z"}], "@odata.deltaLink": "D"}
        return {"value": []}

    def get(self, path):
        if "attachments" in path:
            return {"value": [{"id": "A1", "name": "doc.pdf", "contentType": "application/pdf", "size": 12}]}
        return {"value": []}

    monkeypatch.setattr(OutlookProvider, "_get_abs", get_abs)
    monkeypatch.setattr(OutlookProvider, "_get", get)
    todo_sync.pull_todo(acct)
    t = db.session.query(Task).filter_by(provider_task_id="T1").first()
    assert t and [a.name for a in t.attachments] == ["doc.pdf"]


def test_upload_and_download_attachment_endpoints(app, client, monkeypatch):
    _login(client)
    user = _demo()
    _connect_outlook(user)
    task = _todo_task(user, _synced_list(user))
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)
    monkeypatch.setattr(OutlookProvider, "_post", lambda self, path, payload: {
        "id": "A1", "name": "doc.txt", "contentType": "text/plain", "size": 5})
    r = client.post(f"/api/tasks/{task.id}/attachments",
                    data={"file": (io.BytesIO(b"hello"), "doc.txt")},
                    content_type="multipart/form-data").get_json()
    assert r["ok"]
    att = db.session.query(TaskAttachment).filter_by(task_id=task.id).first()
    assert att and att.name == "doc.txt"

    monkeypatch.setattr(OutlookProvider, "_send",
                        lambda self, method, path, payload=None, extra_headers=None: b"hello")
    resp = client.get(f"/app/tasks/{task.id}/attachments/{att.id}")
    assert resp.status_code == 200 and resp.data == b"hello"
    assert "doc.txt" in resp.headers.get("Content-Disposition", "")
