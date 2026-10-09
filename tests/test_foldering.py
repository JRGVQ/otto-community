"""Auto-foldering: folder suggestion matching + the one-click move endpoint."""
from app.extensions import db
from app.foldering import suggest_folder
from app.models import MailAccount, Message, Organization


class _Cat:
    def __init__(self, name, subs=()):
        self.name = name
        self.subcategories = [type("S", (), {"name": s})() for s in subs]


def test_suggest_folder_matching():
    folders = [{"id": "1", "name": "Inbox"}, {"id": "2", "name": "Boekhouding"},
               {"id": "3", "name": "Klanten 2026"}]
    assert suggest_folder(_Cat("Financieel", ["Boekhouding"]), folders)["name"] == "Boekhouding"
    assert suggest_folder(_Cat("Klanten"), folders)["name"] == "Klanten 2026"  # substring
    assert suggest_folder(_Cat("Spam"), folders) is None
    assert suggest_folder(None, folders) is None
    assert suggest_folder(_Cat("X"), []) is None


def test_move_message_endpoint(auth_client, monkeypatch):
    moved = {}

    class FakeProvider:
        def move_to_folder(self, pmid, fid):
            moved["call"] = (pmid, fid)

    monkeypatch.setattr("app.providers.get_provider", lambda acct: FakeProvider())

    org = db.session.query(Organization).filter_by(is_demo=True).first()
    acct = db.session.query(MailAccount).first()
    m = Message(organization_id=org.id, account_id=acct.id, provider_message_id="pmid-1",
                subject="x", sender_email="a@b.be")
    db.session.add(m)
    db.session.commit()

    auth_client.post(f"/app/messages/{m.id}/move", data={"folder_id": "F1"})
    assert moved.get("call") == ("pmid-1", "F1")
    db.session.refresh(m)
    assert m.archived is True
