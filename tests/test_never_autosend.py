"""The non-negotiable guarantee: the pipeline never sends, only drafts."""
import inspect

from app.extensions import db
from app.models import Message
from app.providers.base import MailProvider
from app.tasks.pipeline import process_incoming


class _RecordingProvider:
    """A 'configured' provider that records calls and explodes if asked to send."""

    def __init__(self):
        self.calls = []
        self.label_color = None

    def is_configured(self):
        return True

    def apply_label(self, *a, **k):
        self.calls.append("apply_label")
        # color is passed as the `color` kwarg (or 3rd positional arg)
        self.label_color = k.get("color", a[2] if len(a) > 2 else None)

    def create_draft(self, *a, **k):
        self.calls.append("create_draft")
        return "provider-draft-1"

    def send_user_draft(self, *a, **k):  # pragma: no cover - must never run
        self.calls.append("send")
        raise AssertionError("The pipeline must NEVER send mail.")


def test_pipeline_creates_drafts_but_never_sends(app, monkeypatch):
    fake = _RecordingProvider()
    monkeypatch.setattr("app.tasks.writeback.get_provider", lambda account: fake)

    # Reset to an unprocessed state so the (now idempotent) pipeline actually runs.
    for msg in db.session.query(Message).all():
        msg.category_id = None
        if msg.draft:
            db.session.delete(msg.draft)
    db.session.commit()
    for msg in db.session.query(Message).all():
        process_incoming(msg.id)

    # Drafts were written back; sending was never attempted.
    assert "create_draft" in fake.calls
    assert "send" not in fake.calls


def test_pipeline_passes_category_color_to_label(app, monkeypatch):
    fake = _RecordingProvider()
    monkeypatch.setattr("app.tasks.writeback.get_provider", lambda account: fake)

    msg = db.session.query(Message).join(Message.category).first()
    msg.category_id = None  # force re-processing (the pipeline is now idempotent)
    if msg.draft:
        db.session.delete(msg.draft)
    db.session.commit()
    process_incoming(msg.id)

    assert "apply_label" in fake.calls
    assert fake.label_color and fake.label_color.startswith("#")  # the category's hex color


def test_mailprovider_contract_has_no_send():
    methods = {name for name, _ in inspect.getmembers(MailProvider, predicate=inspect.isfunction)}
    assert not any("send" in m.lower() for m in methods), "MailProvider must not expose a send method"


def test_concept_drafts_present_for_reply_category(app):
    replies = (
        db.session.query(Message)
        .join(Message.category)
        .filter_by(key="te_beantwoorden")
        .all()
    )
    assert replies
    assert all(m.draft and m.draft.status == "concept" for m in replies)
