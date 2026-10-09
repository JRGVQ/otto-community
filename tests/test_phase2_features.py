"""Phase-2: thread-based already-replied guard + analysis board."""
from app.constants import DRAFT_SENT
from app.extensions import db
from app.models import Draft, MailAccount, Message, Organization
from app.tasks.draft import _thread_already_answered, generate_draft


def _msg(org, acct, pmid, thread, cat_id=None):
    m = Message(organization_id=org.id, account_id=acct.id, provider_message_id=pmid,
                provider_thread_id=thread, sender="Ann", sender_email="ann@klant.be",
                subject="Vraag", body="Kan dit?", category_id=cat_id)
    db.session.add(m)
    db.session.flush()
    return m


def test_thread_guard_helper(app):
    org = db.session.query(Organization).filter_by(is_demo=True).first()
    acct = db.session.query(MailAccount).first()
    _a = _msg(org, acct, "g-a", "TH1")
    b = _msg(org, acct, "g-b", "TH1")
    assert _thread_already_answered(b) is False
    db.session.add(Draft(message_id=_a.id, status=DRAFT_SENT))
    db.session.commit()
    assert _thread_already_answered(b) is True


def test_generate_draft_skips_answered_thread(app):
    org = db.session.query(Organization).filter_by(is_demo=True).first()
    acct = db.session.query(MailAccount).first()
    cat = next(c for c in org.categories if c.draft_enabled)
    a = _msg(org, acct, "h-a", "TH2", cat.id)
    db.session.add(Draft(message_id=a.id, status=DRAFT_SENT))
    b = _msg(org, acct, "h-b", "TH2", cat.id)
    db.session.commit()
    assert generate_draft(b.id) is None                    # skipped — thread already answered
    assert generate_draft(b.id, force=True) is not None     # explicit/forced draft overrides


def test_analyse_page_renders(auth_client):
    body = auth_client.get("/app/analyse").get_data(as_text=True)
    assert "Analyse" in body and "Per categorie" in body
