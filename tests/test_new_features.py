"""Follow-ups, concept-TTL, guided reply and auto-foldering."""
from datetime import timedelta

from app.ai.prompts import draft_user, followup_user
from app.ai.stub_adapter import StubAdapter
from app.constants import DRAFT_CONCEPT, DRAFT_DISCARDED, DRAFT_SENT
from app.extensions import db
from app.models import Category, Draft, FollowUp, MailAccount, Message, Organization, StyleProfile
from app.models.base import utcnow


# ── Feature 3: guided reply ──────────────────────────────────────────────────
def test_guided_reply_intent_in_prompt_and_stub():
    p = draft_user("Jan", "Offerte", "Kan je bevestigen?", "Groeten", intent="bevestig dinsdag 10u")
    assert "bevestig dinsdag 10u" in p
    # No intent → no steer line.
    assert "De gebruiker wil" not in draft_user("Jan", "Offerte", "x", "Groeten")
    # The stub reflects the intent so the path is observable offline.
    out = StubAdapter().draft("Jan", "Offerte", "x", "neutraal", "", "Groeten", intent="bevestig dinsdag 10u")
    assert "bevestig dinsdag 10u" in out


def test_generate_draft_with_intent_not_cached(app):
    from app.tasks.draft import generate_draft

    org = db.session.query(Organization).filter_by(is_demo=True).first()
    acct = db.session.query(MailAccount).first()
    m = Message(organization_id=org.id, account_id=acct.id, user_id=acct.user_id,
                provider_message_id="p1", subject="Vraag", sender="Jan", body="?")
    db.session.add(m)
    db.session.commit()

    generate_draft(m.id, tone="neutraal", force=True, intent="stuur de factuur door")
    db.session.refresh(m)
    assert m.draft is not None
    assert "stuur de factuur door" in m.draft.current_text
    # Intent-steered drafts are one-off — never persisted as a reusable tone variant.
    assert list(m.draft.tone_variants) == []


# ── Feature 2: concept TTL ───────────────────────────────────────────────────
def test_purge_expired_drafts_respects_ttl(app):
    from app.tasks.schedule import purge_expired_drafts

    org = db.session.query(Organization).filter_by(is_demo=True).first()
    acct = db.session.query(MailAccount).first()
    owner = acct.user
    style = owner.style_profile or StyleProfile(user_id=owner.id)
    style.draft_ttl_days = 3
    db.session.add(style)

    def _draft(age_days):
        msg = Message(organization_id=org.id, account_id=acct.id, user_id=owner.id, subject="s", body="b")
        db.session.add(msg)
        db.session.flush()
        d = Draft(message_id=msg.id, status=DRAFT_CONCEPT, current_text="x")
        db.session.add(d)
        db.session.flush()
        d.created_at = utcnow() - timedelta(days=age_days)
        return d

    old = _draft(10)
    fresh = _draft(1)
    db.session.commit()

    assert purge_expired_drafts() == 1
    db.session.refresh(old)
    db.session.refresh(fresh)
    assert old.status == DRAFT_DISCARDED
    assert fresh.status == DRAFT_CONCEPT


# ── Feature 4: auto-foldering ────────────────────────────────────────────────
def test_auto_move_only_when_opted_in(app, monkeypatch):
    from app.tasks import categorize as cz

    org = db.session.query(Organization).filter_by(is_demo=True).first()
    acct = db.session.query(MailAccount).first()
    owner = acct.user
    style = owner.style_profile or StyleProfile(user_id=owner.id)
    style.auto_folder_enabled = True
    db.session.add(style)

    cat = Category(organization_id=org.id, key="news", name="Nieuwsbrief",
                   keep_in_inbox=False, target_folder="Nieuwsbrieven")
    db.session.add(cat)
    msg = Message(organization_id=org.id, account_id=acct.id, user_id=owner.id,
                  provider_message_id="pm-9", subject="promo", body="b")
    db.session.add(msg)
    db.session.commit()

    calls = {}

    class FakeProvider:
        def is_configured(self):
            return True

        def ensure_folder(self, name):
            calls["folder"] = name
            return "FID"

        def move_to_folder(self, pmid, fid):
            calls["move"] = (pmid, fid)

    monkeypatch.setattr("app.providers.get_provider", lambda a: FakeProvider())

    cz._auto_move(msg, cat)
    assert calls["folder"] == "Nieuwsbrieven"
    assert calls["move"] == ("pm-9", "FID")
    assert msg.archived is True

    # A "keep in inbox" category is never moved.
    calls.clear()
    keep = Category(organization_id=org.id, key="resp", name="Te beantwoorden", keep_in_inbox=True)
    msg2 = Message(organization_id=org.id, account_id=acct.id, user_id=owner.id,
                   provider_message_id="pm-10", subject="x", body="b")
    db.session.add_all([keep, msg2])
    db.session.commit()
    cz._auto_move(msg2, keep)
    assert calls == {}
    assert msg2.archived is not True


def test_mailbox_foldering_override_and_inheritance(app):
    from app.models import MailAccount, MailboxCategory
    from app.models.mailbox_category import effective_foldering

    org = db.session.query(Organization).filter_by(is_demo=True).first()
    owner = db.session.query(MailAccount).first().user
    shared = MailAccount(user_id=owner.id, organization_id=org.id, provider="outlook",
                         status="connected", mailbox_type="shared",
                         target_address="support@acme.be", email="support@acme.be")
    cat = Category(organization_id=org.id, key="news2", name="Nieuwsbrief",
                   keep_in_inbox=False, target_folder="Org-Nieuws")
    db.session.add_all([shared, cat])
    db.session.commit()

    # No override row → inherit the org category.
    assert effective_foldering(shared, cat) == (False, "Org-Nieuws")

    # Override sends this mailbox's copies to its own folder.
    db.session.add(MailboxCategory(mail_account_id=shared.id, category_id=cat.id,
                                   enabled=True, keep_in_inbox=False, target_folder="Support-Nieuws"))
    db.session.commit()
    db.session.refresh(shared)
    assert effective_foldering(shared, cat) == (False, "Support-Nieuws")


def test_ensure_folder_default_matches_existing():
    from app.providers.base import MailProvider

    class P(MailProvider):
        @classmethod
        def is_configured(cls):
            return True

        def authenticate(self): ...
        def refresh_token(self): ...
        def list_recent(self, limit=300): return []
        def fetch_new(self, cursor): return [], None
        def get_thread(self, thread_id): return None
        def apply_label(self, *a, **k): ...
        def create_draft(self, *a, **k): return "d"
        def list_folders(self):
            return [{"id": "1", "name": "Nieuwsbrieven"}]

    p = P(account=None)
    assert p.ensure_folder("nieuwsbrieven") == "1"   # case-insensitive match
    assert p.ensure_folder("Onbekend") is None        # absent → no creation in base


# ── Feature 1: follow-ups ────────────────────────────────────────────────────
def test_followup_prompt_and_stub():
    assert "opvolging" in followup_user("Offerte", "eerdere mail", "Groeten").lower()
    out = StubAdapter().followup("Offerte", "b", "neutraal", "", "Groeten")
    assert "Offerte" in out


def test_follow_up_sweep_creates_and_dedupes(app, monkeypatch):
    from app.tasks.followup import generate_follow_ups_for_account

    acct = db.session.query(MailAccount).first()
    owner = acct.user
    acct.status = "connected"
    style = owner.style_profile or StyleProfile(user_id=owner.id)
    style.follow_up_enabled = True
    style.follow_up_days = 3
    db.session.add(style)
    db.session.commit()

    candidate = {
        "conversation_id": "C1", "provider_message_id": "SM1", "subject": "Offerte",
        "to": "klant@acme.be", "sent_at": utcnow() - timedelta(days=4), "body": "eerdere mail",
    }

    class FakeProvider:
        def is_configured(self):
            return True

        def list_awaiting_reply(self, lo, hi, limit=25):
            return [candidate]

        def create_followup_draft(self, pmid, text):
            return "DRAFT-" + pmid

    monkeypatch.setattr("app.providers.get_provider", lambda a: FakeProvider())

    assert generate_follow_ups_for_account(acct) == 1
    fu = db.session.query(FollowUp).filter_by(provider_thread_id="C1").one()
    assert fu.status == DRAFT_CONCEPT
    assert fu.provider_draft_id == "DRAFT-SM1"
    assert fu.to_recipients == "klant@acme.be"
    # Second sweep must not duplicate the same thread.
    assert generate_follow_ups_for_account(acct) == 0


def test_followup_send_and_discard_endpoints(auth_client, monkeypatch):
    org = db.session.query(Organization).filter_by(is_demo=True).first()
    acct = db.session.query(MailAccount).first()
    owner = acct.user

    def _fu():
        f = FollowUp(organization_id=org.id, user_id=owner.id, account_id=acct.id,
                     provider_thread_id="Cx", provider_message_id="SMx",
                     subject="s", status=DRAFT_CONCEPT, current_text="hallo",
                     provider_draft_id="D1")
        db.session.add(f)
        db.session.commit()
        return f

    sent = {}

    class FakeProvider:
        def is_configured(self):
            return True

        def delete_draft(self, did):
            sent.setdefault("deleted", []).append(did)

        def create_followup_draft(self, pmid, text):
            return "D2"

        def send_user_draft(self, did):
            sent["sent"] = did

    monkeypatch.setattr("app.providers.get_provider", lambda a: FakeProvider())

    f1 = _fu()
    auth_client.post(f"/api/follow-ups/{f1.id}/send")
    db.session.refresh(f1)
    assert f1.status == DRAFT_SENT
    assert sent.get("sent") == "D2"

    f2 = _fu()
    auth_client.post(f"/api/follow-ups/{f2.id}/discard")
    db.session.refresh(f2)
    assert f2.status == DRAFT_DISCARDED


def test_follow_ups_page_renders(auth_client):
    assert auth_client.get("/app/follow-ups").status_code == 200


def test_soft_deleted_org_goes_to_blocked_page_not_website(app):
    """A soft-deleted org's owner is kept signed in and sent to the blocked page (with an
    'archived' message) — never silently logged out to the public marketing site."""
    from app.auth.service import create_company
    from app.models.base import utcnow

    u = create_company("Zeta BV", "Zoë", "z@zeta.be", "Sterk-Wachtwoord-123")
    u.email_verified = True
    u.onboarded = True
    u.organization.deleted_at = utcnow()
    db.session.commit()

    c = app.test_client()
    c.post("/auth/login", data={"email": "z@zeta.be", "password": "Sterk-Wachtwoord-123"})
    r = c.get("/app/inbox")
    assert r.status_code == 302 and "/licentie" in r.headers["Location"]
    # Still signed in (not bounced to login/marketing) and shown the archived message.
    page = c.get("/licentie")
    assert page.status_code == 200 and "gearchiveerd" in page.get_data(as_text=True)


# ── Batch 2: sorting, threads, awaiting-reply, filters, compose ──────────────
def _msg(org, user, **kw):
    m = Message(organization_id=org.id, user_id=user.id, body=kw.pop("body", "x"), **kw)
    db.session.add(m)
    db.session.flush()
    return m


def test_inbox_sort_newest_first(auth_client, app):
    from datetime import datetime
    org = db.session.query(Organization).filter_by(is_demo=True).first()
    u = org.users[0]
    _msg(org, u, subject="OUD", received_at=datetime(2026, 7, 1, 9))
    _msg(org, u, subject="NIEUW", received_at=datetime(2026, 7, 9, 9))
    db.session.commit()
    html = auth_client.get("/app/inbox?cat=alle&sort=new").get_data(as_text=True)
    assert html.index("NIEUW") < html.index("OUD")
    html_old = auth_client.get("/app/inbox?cat=alle&sort=old").get_data(as_text=True)
    assert html_old.index("OUD") < html_old.index("NIEUW")


def test_draft_includes_thread_context():
    from app.ai.prompts import draft_user
    p = draft_user("Jan", "Re: Offerte", "laatste mail", "Groeten", thread="[01-07] Klant: eerste vraag")
    assert "eerste vraag" in p and "conversatie" in p.lower()


def test_thread_transcript_from_siblings(app):
    from datetime import datetime
    from app.tasks.draft import _thread_transcript
    org = db.session.query(Organization).filter_by(is_demo=True).first()
    u = org.users[0]
    _msg(org, u, subject="Offerte", provider_thread_id="T9", sender="Klant",
         received_at=datetime(2026, 7, 1, 9), body="eerste vraag")
    cur = _msg(org, u, subject="Re: Offerte", provider_thread_id="T9", sender="Ik",
               received_at=datetime(2026, 7, 2, 9), body="laatste")
    db.session.commit()
    tr = _thread_transcript(cur)
    assert tr and "eerste vraag" in tr and "Klant" in tr


def test_reply_sets_awaiting_and_guard_blocks(auth_client, app):
    from app.tasks.draft import generate_draft
    org = db.session.query(Organization).filter_by(is_demo=True).first()
    from app.models import MailAccount
    acct = db.session.query(MailAccount).first()
    u = acct.user
    m1 = _msg(org, u, account_id=acct.id, subject="Vraag", provider_thread_id="TT",
              sender="Klant", body="?", provider_message_id="pm1")
    d = Draft(message_id=m1.id, status=DRAFT_CONCEPT, current_text="antwoord")
    db.session.add(d)
    db.session.commit()

    auth_client.post(f"/api/drafts/{m1.id}/send")
    db.session.refresh(m1)
    assert m1.state == "awaiting_reply" and m1.replied_at is not None

    # A later message in the same thread must NOT get an auto-concept (already answered).
    m2 = _msg(org, u, account_id=acct.id, subject="Re", provider_thread_id="TT",
              sender="Klant", body="nog iets", provider_message_id="pm2")
    db.session.commit()
    assert generate_draft(m2.id, force=False) is None


def test_inbound_reply_resolves_awaiting(app):
    from app.tasks.sync import _resolve_awaiting_reply
    org = db.session.query(Organization).filter_by(is_demo=True).first()
    u = org.users[0]
    old = _msg(org, u, subject="Q", provider_thread_id="TR", state="awaiting_reply")
    new = _msg(org, u, subject="Re: Q", provider_thread_id="TR", state="open")
    db.session.commit()
    _resolve_awaiting_reply(new)
    db.session.refresh(old)
    assert old.state == "done"


def test_waiting_filter(auth_client, app):
    org = db.session.query(Organization).filter_by(is_demo=True).first()
    u = org.users[0]
    _msg(org, u, subject="WACHTEND", state="awaiting_reply")
    _msg(org, u, subject="GEWOON", state="open")
    db.session.commit()
    html = auth_client.get("/app/inbox?cat=alle&waiting=1").get_data(as_text=True)
    assert "WACHTEND" in html and "GEWOON" not in html


def test_compose_force_with_intent(auth_client, app):
    from app.models import MailAccount
    org = db.session.query(Organization).filter_by(is_demo=True).first()
    acct = db.session.query(MailAccount).first()
    m = _msg(org, acct.user, account_id=acct.id, subject="Interesse", sender="Klant",
             body="Zijn jullie geinteresseerd?", provider_message_id="pmc")
    db.session.commit()
    r = auth_client.post(f"/api/drafts/{m.id}/force", json={"intent": "bevestig interesse en vraag afspraak"})
    assert r.status_code == 200
    db.session.refresh(m)
    assert m.draft is not None
    assert "bevestig interesse en vraag afspraak" in m.draft.current_text
