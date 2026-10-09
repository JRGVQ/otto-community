"""The explicit user-send route flips status and is permission-gated."""
from app.extensions import db
from app.models import Message, Role, User
from app.seed.demo_data import DEMO_PASSWORD


def _reply_message():
    return (
        db.session.query(Message).join(Message.category).filter_by(key="te_beantwoorden").first()
    )


def test_user_send_marks_verzonden(auth_client, app):
    msg = _reply_message()
    assert msg.draft.status == "concept"
    r = auth_client.post(f"/api/drafts/{msg.id}/send")
    assert r.status_code == 200 and r.get_json()["ok"]
    db.session.refresh(msg)
    assert msg.draft.status == "verzonden"


def test_send_requires_permission(client, app):
    # Demote the demo user to a role without sendMail, then attempt to send.
    user = db.session.query(User).filter_by(email="demo@example.com").first()
    user.role_id = db.session.query(Role).filter_by(name="Alleen-lezen").first().id
    db.session.commit()

    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    msg = _reply_message()
    r = client.post(f"/api/drafts/{msg.id}/send")
    assert r.status_code == 403
    db.session.refresh(msg)
    assert msg.draft.status == "concept"  # unchanged
