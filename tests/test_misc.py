"""Enqueue eager path + provider graceful degradation + key interactions."""
from app.extensions import db
from app.models import Message, Task
from app.providers import get_provider
from app.providers.gmail import GmailProvider
from app.tasks.enqueue import enqueue


def test_enqueue_runs_inline_in_eager_mode(app):
    calls = []
    def job(x):
        calls.append(x)
        return x * 2
    assert enqueue(job, 21) == 42
    assert calls == [21]


def test_provider_not_configured_degrades(app):
    sam_account = db.session.query(Message).first().account
    provider = get_provider(sam_account)  # outlook adapter
    assert provider.is_configured() is False  # no Graph creds in test config


def test_gmail_unconfigured_reports_false(app):
    """Resolved per mailbox now (like Outlook): the org brings its own Google app or it
    falls back to the platform one — and this test config has neither."""
    account = db.session.query(Message).first().account
    assert GmailProvider(account).is_configured() is False


def test_recategorize_moves_message(auth_client, app):
    msg = db.session.query(Message).filter_by(subject="Je hebt 5 nieuwe connectieverzoeken").first()
    r = auth_client.post(f"/api/messages/{msg.id}/recategorize", json={"category": "te_beantwoorden"})
    assert r.get_json()["ok"]
    db.session.refresh(msg)
    assert msg.category.key == "te_beantwoorden"


def test_create_task_dedupes(auth_client, app):
    msg = db.session.query(Message).filter_by(subject="Je uitbetaling van €2.480 is onderweg").first()
    before = db.session.query(Task).count()
    auth_client.post(f"/api/messages/{msg.id}/task")  # first time → creates (linked to full subject)
    after_first = db.session.query(Task).count()
    auth_client.post(f"/api/messages/{msg.id}/task")  # second time → deduped
    after_second = db.session.query(Task).count()
    assert after_first == before + 1
    assert after_second == after_first


def test_regenerate_returns_text(auth_client, app):
    msg = db.session.query(Message).filter_by(subject="Kunnen we volgende week bellen?").first()
    r = auth_client.post(f"/api/drafts/{msg.id}/regenerate")
    data = r.get_json()
    assert data["ok"] and data["text"]
