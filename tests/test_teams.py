"""Teams environment: per-mailbox inbox + settings screens, plan gating, member access."""
from app.extensions import db
from app.models import (
    Category,
    Draft,
    MailAccount,
    MailboxCategory,
    MailboxMember,
    MailboxSettings,
    Message,
    Task,
    User,
)
from app.security.crypto import encrypt


# ── helpers ──────────────────────────────────────────────────────────────────
def _personal(user) -> MailAccount:
    acct = MailAccount(
        user_id=user.id, organization_id=user.organization_id, provider="outlook",
        email=user.email, status="connected", mailbox_type="personal",
        access_token_enc=encrypt("AT"), refresh_token_enc=encrypt("RT"),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _shared_for(user, address, role="owner") -> MailAccount:
    personal = next(
        (a for a in user.mail_accounts if a.provider == "outlook" and not a.delegated_via_account_id),
        None,
    ) or _personal(user)
    acct = MailAccount(
        user_id=user.id, organization_id=user.organization_id, provider="outlook",
        email=address, status="connected", mailbox_type="shared",
        target_address=address, delegated_via_account_id=personal.id,
    )
    db.session.add(acct)
    db.session.flush()
    db.session.add(MailboxMember(mail_account_id=acct.id, user_id=user.id, role=role))
    db.session.commit()
    return acct


def _user_in(org_id, email, name="Collega") -> User:
    u = User(email=email, name=name, organization_id=org_id, email_verified=True, onboarded=True)
    u.set_password("geheim123")
    db.session.add(u)
    db.session.commit()
    return u


# ── list screen ───────────────────────────────────────────────────────────────
def test_teams_nav_and_empty_state(auth_client):
    r = auth_client.get("/app/teams")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Teams" in body
    assert "Nog geen gedeelde postvakken" in body


def test_teams_lists_member_mailboxes(auth_client, demo_user):
    _shared_for(demo_user, "support@acme.be")
    body = auth_client.get("/app/teams").get_data(as_text=True)
    assert "support@acme.be" in body
    assert "Nog geen gedeelde postvakken" not in body


# ── per-mailbox inbox (the primary view) ───────────────────────────────────────
def test_team_inbox_renders_and_lazy_creates_settings(auth_client, demo_user):
    acct = _shared_for(demo_user, "info@acme.be")  # no settings row yet
    assert acct.settings is None

    r = auth_client.get(f"/app/teams/{acct.id}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "info@acme.be" in body      # team header
    assert "Instellingen" in body      # link through to settings

    db.session.refresh(acct)
    assert acct.settings is not None   # resolved on first access


def test_team_inbox_is_scoped_and_personal_inbox_excludes_shared(auth_client, demo_user):
    acct = _shared_for(demo_user, "ops@acme.be")
    db.session.add(Message(
        organization_id=demo_user.organization_id, account_id=acct.id,
        sender="Klant", subject="Hulp nodig met bestelling", snippet="…", unread=True,
    ))
    db.session.commit()

    team = auth_client.get(f"/app/teams/{acct.id}").get_data(as_text=True)
    assert "Hulp nodig met bestelling" in team        # shows in the mailbox's own inbox

    personal = auth_client.get("/app/inbox").get_data(as_text=True)
    assert "Hulp nodig met bestelling" not in personal  # NOT in the personal inbox


# ── settings screen ────────────────────────────────────────────────────────────
def test_team_settings_renders(auth_client, demo_user):
    acct = _shared_for(demo_user, "sales@acme.be")
    r = auth_client.get(f"/app/teams/{acct.id}/settings")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Algemeen" in body and "Leden" in body


# ── editable settings (fase 2) ─────────────────────────────────────────────────
def test_team_settings_owner_can_edit(auth_client, demo_user):
    acct = _shared_for(demo_user, "edit@acme.be")  # demo is the mailbox owner
    r = auth_client.post(f"/app/teams/{acct.id}/settings", data={
        "display_name": "Klantendienst", "tone": "formeel",
        "language": "antwoord in het Engels", "signature": "Groeten, Team",
        "default_reply": "Wees beknopt.", "sla_minutes": "90", "auto_draft": "on",
    })
    assert r.status_code in (301, 302)
    db.session.refresh(acct)
    s = acct.settings
    assert s.display_name == "Klantendienst"
    assert s.tone == "formeel"
    assert s.signature == "Groeten, Team"
    assert s.sla_minutes == 90
    assert s.auto_draft is True
    assert s.approval_required is False  # checkbox absent → off


def test_team_settings_non_owner_cannot_edit(client, demo_user):
    """A plain agent member (not the mailbox owner, not an org owner/admin) can't edit."""
    org_id = demo_user.organization_id
    owner = _user_in(org_id, "owner2@acme.be", name="Owner Two")
    agent = _user_in(org_id, "agent@acme.be", name="Agent")
    acct = _shared_for(owner, "shared2@acme.be")  # owner owns it
    db.session.add(MailboxMember(mail_account_id=acct.id, user_id=agent.id, role="agent"))
    db.session.commit()

    client.post("/auth/login", data={"email": "agent@acme.be", "password": "geheim123"})
    client.get(f"/app/teams/{acct.id}/settings")  # lazily create the settings row
    db.session.refresh(acct)

    r = client.post(f"/app/teams/{acct.id}/settings", data={"tone": "kort", "display_name": "Hacked"})
    assert r.status_code in (301, 302)
    db.session.refresh(acct)
    assert acct.settings.tone == "neutraal"        # rejected — still the default
    assert acct.settings.display_name != "Hacked"


# ── access gating (membership / unknown / type) ────────────────────────────────
def test_team_404_for_non_member(auth_client, demo_user):
    colleague = _user_in(demo_user.organization_id, "collega@acme.be")
    acct = _shared_for(colleague, "secret@acme.be")
    assert auth_client.get(f"/app/teams/{acct.id}").status_code == 404
    assert auth_client.get(f"/app/teams/{acct.id}/settings").status_code == 404


def test_team_404_for_unknown(auth_client):
    assert auth_client.get("/app/teams/999999").status_code == 404


def test_team_404_for_personal_account(auth_client, demo_user):
    personal = _personal(demo_user)
    assert auth_client.get(f"/app/teams/{personal.id}").status_code == 404


# ── Teams is available to every organisation ──────────────────────────────────
def test_teams_available_to_every_org(auth_client, demo_user):
    assert auth_client.get("/app/teams").status_code == 200
    inbox = auth_client.get("/app/inbox").get_data(as_text=True)
    assert 'href="/app/teams"' in inbox  # nav item visible


# ── fase 2: drafts use the mailbox's voice, not the connector's ────────────────
def test_draft_helpers_prefer_mailbox_settings(app, demo_user):
    from app.tasks.draft import _MailboxStyle, preferred_tone_for, signature_for

    class _S:  # minimal MailboxSettings-like object
        tone = "formeel"
        signature = "Team X"
        default_reply = "Wees kort."

    assert signature_for(demo_user, _S) == "Team X"          # mailbox signature wins
    assert preferred_tone_for(demo_user, None, _S) == "formeel"  # mailbox tone wins
    ms = _MailboxStyle(_S)
    assert ms.tone == "formeel" and ms.greeting is None and ms.raw_profile == {}


def test_shared_mailbox_draft_uses_mailbox_voice(app, demo_user):
    from app.tasks.draft import generate_draft

    # The connector has a personal style that must NOT leak into a team draft.
    demo_user.style_profile.tone = "kort"
    demo_user.style_profile.signature = "Sam (persoonlijk)"

    acct = _shared_for(demo_user, "voice@acme.be")
    db.session.add(MailboxSettings(
        mail_account_id=acct.id, tone="formeel", signature="Team Support – Acme NV"))
    msg = Message(organization_id=demo_user.organization_id, account_id=acct.id,
                  sender="Klant", subject="Vraag", body="Hallo", lang="antwoord in Nederlands")
    db.session.add(msg)
    db.session.commit()

    did = generate_draft(msg.id, force=True)
    draft = db.session.get(Draft, did)
    assert draft.current_tone == "formeel"                 # mailbox tone, not personal "kort"
    assert "Team Support – Acme NV" in draft.current_text  # mailbox signature, not personal
    assert "Sam (persoonlijk)" not in draft.current_text


def test_shared_mailbox_auto_draft_toggle_gates_drafting(app, demo_user):
    from app.tasks.pipeline import process_incoming

    acct = _shared_for(demo_user, "gate@acme.be")
    db.session.add(MailboxSettings(mail_account_id=acct.id, auto_draft=False))
    # a category that wants drafts
    from app.models import Category
    cat = (db.session.query(Category)
           .filter_by(organization_id=demo_user.organization_id, draft_enabled=True).first())
    msg = Message(organization_id=demo_user.organization_id, account_id=acct.id,
                  category_id=cat.id if cat else None, sender="Klant", subject="Hulp", body="Hoi")
    db.session.add(msg)
    db.session.commit()

    process_incoming(msg.id)
    db.session.refresh(msg)
    assert msg.draft is None  # auto_draft off → no concept, even though the category wants one


# ── fase 2: member management API ──────────────────────────────────────────────
def test_team_add_member(auth_client, demo_user):
    acct = _shared_for(demo_user, "team1@acme.be")  # demo is owner/manager
    mate = _user_in(demo_user.organization_id, "mate@acme.be", name="Mate")
    r = auth_client.post(f"/api/teams/{acct.id}/members",
                         json={"user_id": mate.id, "role": "agent", "can_draft": True, "can_send": False})
    assert r.status_code == 200 and r.get_json()["ok"]
    db.session.refresh(acct)
    m = next((mm for mm in acct.members if mm.user_id == mate.id), None)
    assert m is not None and m.role == "agent" and m.can_send is False


def test_team_add_member_rejects_duplicate(auth_client, demo_user):
    acct = _shared_for(demo_user, "team2@acme.be")
    r = auth_client.post(f"/api/teams/{acct.id}/members", json={"user_id": demo_user.id, "role": "agent"})
    assert r.get_json().get("error")  # demo is already a member


def test_team_update_member_role_and_perms(auth_client, demo_user):
    acct = _shared_for(demo_user, "team3@acme.be")
    mate = _user_in(demo_user.organization_id, "mate3@acme.be")
    db.session.add(MailboxMember(mail_account_id=acct.id, user_id=mate.id, role="agent"))
    db.session.commit()
    r = auth_client.post(f"/api/teams/{acct.id}/members/{mate.id}", json={"role": "viewer", "can_send": False})
    assert r.get_json()["ok"]
    db.session.refresh(acct)
    m = next(mm for mm in acct.members if mm.user_id == mate.id)
    assert m.role == "viewer" and m.can_send is False


def test_team_remove_member(auth_client, demo_user):
    acct = _shared_for(demo_user, "team4@acme.be")
    mate = _user_in(demo_user.organization_id, "mate4@acme.be")
    db.session.add(MailboxMember(mail_account_id=acct.id, user_id=mate.id, role="agent"))
    db.session.commit()
    r = auth_client.delete(f"/api/teams/{acct.id}/members/{mate.id}")
    assert r.get_json()["ok"]
    db.session.refresh(acct)
    assert mate.id not in acct.member_user_ids


def test_team_remove_last_member_blocked(auth_client, demo_user):
    acct = _shared_for(demo_user, "team5@acme.be")  # only demo
    r = auth_client.delete(f"/api/teams/{acct.id}/members/{demo_user.id}")
    assert r.get_json().get("error")
    db.session.refresh(acct)
    assert demo_user.id in acct.member_user_ids


def test_team_member_mgmt_forbidden_for_non_manager(client, demo_user):
    org_id = demo_user.organization_id
    owner = _user_in(org_id, "o5@acme.be")
    agent = _user_in(org_id, "a5@acme.be")
    acct = _shared_for(owner, "team6@acme.be")
    db.session.add(MailboxMember(mail_account_id=acct.id, user_id=agent.id, role="agent"))
    db.session.commit()
    client.post("/auth/login", data={"email": "a5@acme.be", "password": "geheim123"})
    target = _user_in(org_id, "t5@acme.be")
    r = client.post(f"/api/teams/{acct.id}/members", json={"user_id": target.id, "role": "agent"})
    assert r.status_code == 403


# ── invite user (admin modal) ──────────────────────────────────────────────────
def test_admin_invite_creates_user(auth_client, demo_user):
    r = auth_client.post("/api/admin/users/invite",
                         json={"name": "Nieuwe Collega", "email": "nieuw@acme.be", "role": "Gebruiker"})
    assert r.get_json()["ok"]
    u = db.session.query(User).filter_by(email="nieuw@acme.be").first()
    assert u is not None and u.status == "Uitgenodigd" and u.organization_id == demo_user.organization_id


def test_admin_invite_rejects_bad_email(auth_client):
    r = auth_client.post("/api/admin/users/invite", json={"name": "X", "email": "geen-email"})
    assert r.status_code == 400


# ── fase 3: per-mailbox category config ────────────────────────────────────────
def test_effective_config_inherits_and_overrides(app, demo_user):
    from app.models.mailbox_category import effective_config

    acct = _shared_for(demo_user, "cfg@acme.be")
    cat = db.session.query(Category).filter_by(
        organization_id=demo_user.organization_id, draft_enabled=True).first()
    # No row → inherit org defaults.
    enabled, draft_on, ai_extra = effective_config(acct, cat)
    assert enabled is True and draft_on == bool(cat.draft_enabled) and ai_extra is None
    # Row → honoured.
    db.session.add(MailboxCategory(mail_account_id=acct.id, category_id=cat.id,
                                   enabled=False, draft_enabled=False, ai_override="Alleen VIP-klanten."))
    db.session.commit()
    enabled, draft_on, ai_extra = effective_config(acct, cat)
    assert enabled is False and draft_on is False and ai_extra == "Alleen VIP-klanten."


def test_team_categories_save(auth_client, demo_user):
    acct = _shared_for(demo_user, "catsave@acme.be")
    cat = db.session.query(Category).filter_by(organization_id=demo_user.organization_id).first()
    r = auth_client.post(f"/api/teams/{acct.id}/categories", json={"categories": [
        {"category_id": cat.id, "enabled": False, "draft_enabled": True, "ai_override": "Eigen regel."},
    ]})
    assert r.get_json()["ok"]
    db.session.refresh(acct)
    mc = next(m for m in acct.mailbox_categories if m.category_id == cat.id)
    assert mc.enabled is False and mc.draft_enabled is True and mc.ai_override == "Eigen regel."


def test_mailbox_category_draft_override_off(app, demo_user):
    """Org category drafts, but the mailbox turns drafting OFF for it → no concept."""
    from app.tasks.draft import generate_draft

    acct = _shared_for(demo_user, "doff@acme.be")
    db.session.add(MailboxSettings(mail_account_id=acct.id, auto_draft=True))
    cat = db.session.query(Category).filter_by(
        organization_id=demo_user.organization_id, draft_enabled=True).first()
    db.session.add(MailboxCategory(mail_account_id=acct.id, category_id=cat.id,
                                   enabled=True, draft_enabled=False))
    msg = Message(organization_id=demo_user.organization_id, account_id=acct.id,
                  category_id=cat.id, sender="K", subject="x", body="y")
    db.session.add(msg)
    db.session.commit()
    assert generate_draft(msg.id) is None  # non-force: per-mailbox override wins


def test_mailbox_category_draft_override_on(app, demo_user):
    """Org category does NOT draft, but the mailbox turns drafting ON for it → concept."""
    from app.tasks.draft import generate_draft

    acct = _shared_for(demo_user, "don@acme.be")
    db.session.add(MailboxSettings(mail_account_id=acct.id, auto_draft=True))
    cat = db.session.query(Category).filter_by(
        organization_id=demo_user.organization_id, draft_enabled=False).first()
    db.session.add(MailboxCategory(mail_account_id=acct.id, category_id=cat.id,
                                   enabled=True, draft_enabled=True))
    msg = Message(organization_id=demo_user.organization_id, account_id=acct.id,
                  category_id=cat.id, sender="K", subject="x", body="y")
    db.session.add(msg)
    db.session.commit()
    assert generate_draft(msg.id) is not None


def test_disabled_category_not_offered_to_classifier(app, demo_user, monkeypatch):
    from app.tasks import categorize as cz

    acct = _shared_for(demo_user, "offer@acme.be")
    cat = db.session.query(Category).filter_by(organization_id=demo_user.organization_id).first()
    db.session.add(MailboxCategory(mail_account_id=acct.id, category_id=cat.id, enabled=False))
    msg = Message(organization_id=demo_user.organization_id, account_id=acct.id,
                  sender="K", sender_email="a@b.c", subject="x", body="y")
    db.session.add(msg)
    db.session.commit()

    seen = {}

    class _FakeSvc:
        model_name = "stub"
        def categorize(self, subject, sender_email, body, categories):
            seen["keys"] = [c["key"] for c in categories]
            return {"category_key": categories[0]["key"], "confidence": 90}
        def pop_usage(self):
            return (0, 0)

    monkeypatch.setattr(cz, "get_service_for", lambda org: _FakeSvc())
    cz.categorize_message(msg.id)
    assert cat.key not in seen["keys"]  # the disabled category was withheld


# ── fase 4: collaboration (assign / status) + tasks ────────────────────────────
def test_assign_message_creates_task_for_member(auth_client, demo_user):
    acct = _shared_for(demo_user, "assign@acme.be")
    mate = _user_in(demo_user.organization_id, "amate@acme.be", name="Mate")
    db.session.add(MailboxMember(mail_account_id=acct.id, user_id=mate.id, role="agent"))
    msg = Message(organization_id=demo_user.organization_id, account_id=acct.id,
                  user_id=demo_user.id, sender="Klant", subject="Klantvraag", body="?")
    db.session.add(msg)
    db.session.commit()

    r = auth_client.post(f"/api/messages/{msg.id}/assign", json={"user_id": mate.id})
    assert r.get_json()["ok"]
    db.session.refresh(msg)
    assert msg.assignee_user_id == mate.id
    task = db.session.query(Task).filter_by(linked_message_id=msg.id).first()
    assert task is not None and task.user_id == mate.id  # assignee got the task


def test_assign_rejects_non_member(auth_client, demo_user):
    acct = _shared_for(demo_user, "assign2@acme.be")
    outsider = _user_in(demo_user.organization_id, "out@acme.be")
    msg = Message(organization_id=demo_user.organization_id, account_id=acct.id,
                  user_id=demo_user.id, sender="K", subject="x", body="?")
    db.session.add(msg)
    db.session.commit()
    r = auth_client.post(f"/api/messages/{msg.id}/assign", json={"user_id": outsider.id})
    assert r.status_code == 400


def test_set_message_state(auth_client, demo_user):
    acct = _shared_for(demo_user, "state@acme.be")
    msg = Message(organization_id=demo_user.organization_id, account_id=acct.id,
                  user_id=demo_user.id, sender="K", subject="x", body="?")
    db.session.add(msg)
    db.session.commit()
    r = auth_client.post(f"/api/messages/{msg.id}/state", json={"state": "in_progress"})
    assert r.get_json()["ok"]
    db.session.refresh(msg)
    assert msg.state == "in_progress"


def test_create_task_from_mail(auth_client, demo_user):
    acct = _shared_for(demo_user, "ctask@acme.be")
    msg = Message(organization_id=demo_user.organization_id, account_id=acct.id,
                  user_id=demo_user.id, sender="K", subject="Doe iets", body="?")
    db.session.add(msg)
    db.session.commit()
    r = auth_client.post(f"/api/messages/{msg.id}/task")
    assert r.get_json()["ok"]
    assert db.session.query(Task).filter_by(linked_message_id=msg.id).count() == 1


def test_send_completes_linked_task_and_marks_mail_done(auth_client, demo_user):
    from app.constants import DRAFT_CONCEPT

    msg = Message(organization_id=demo_user.organization_id, user_id=demo_user.id,
                  sender="Klant", subject="Vraag", body="Hoi", state="open")
    db.session.add(msg)
    db.session.flush()
    db.session.add(Draft(message_id=msg.id, status=DRAFT_CONCEPT,
                         current_tone="neutraal", current_text="Hallo"))
    task = Task(organization_id=demo_user.organization_id, user_id=demo_user.id,
                title="Vraag", done=False, linked_message_id=msg.id, linked_subject="Vraag")
    db.session.add(task)
    db.session.commit()

    r = auth_client.post(f"/api/drafts/{msg.id}/send")
    assert r.get_json()["ok"]
    db.session.refresh(msg)
    db.session.refresh(task)
    assert task.done is True                 # sending ticked off the linked task
    assert msg.state == "awaiting_reply"     # a sent reply now awaits their response
    assert msg.replied_at is not None


# ── fase 6 (start): Microsoft Teams webhook integration ────────────────────────
def test_team_settings_save_teams_webhook(auth_client, demo_user):
    acct = _shared_for(demo_user, "tw@acme.be")
    r = auth_client.post(f"/app/teams/{acct.id}/settings", data={
        "tone": "neutraal", "auto_draft": "on",
        "teams_webhook_url": "https://outlook.office.com/webhook/abc", "teams_notify": "on",
    })
    assert r.status_code in (301, 302)
    db.session.refresh(acct)
    assert acct.settings.teams_webhook_url == "https://outlook.office.com/webhook/abc"
    assert acct.settings.teams_notify is True


def test_teams_notify_new_mail_respects_toggle(app, demo_user, monkeypatch):
    from app.tasks import teams_notify as tn

    acct = _shared_for(demo_user, "tn@acme.be")
    db.session.add(MailboxSettings(mail_account_id=acct.id, display_name="Support",
                                   teams_notify=True, teams_webhook_url="https://hook"))
    cat = db.session.query(Category).filter_by(organization_id=demo_user.organization_id).first()
    msg = Message(organization_id=demo_user.organization_id, account_id=acct.id,
                  category_id=cat.id, sender="K", subject="Hulp", body="?")
    db.session.add(msg)
    db.session.commit()

    posted = {}
    monkeypatch.setattr(tn, "_post", lambda url, payload: posted.update(url=url) or True)
    assert tn.notify_new_mail(msg) is True
    assert posted["url"] == "https://hook"

    acct.settings.teams_notify = False  # toggle off → silent no-op
    db.session.commit()
    posted.clear()
    assert tn.notify_new_mail(msg) is False
    assert posted == {}


def test_assign_triggers_teams_notify(auth_client, demo_user, monkeypatch):
    from app.tasks import teams_notify as tn

    acct = _shared_for(demo_user, "ta@acme.be")
    db.session.add(MailboxSettings(mail_account_id=acct.id, display_name="Support",
                                   teams_notify=True, teams_webhook_url="https://hook"))
    mate = _user_in(demo_user.organization_id, "tmate@acme.be", name="Mate")
    db.session.add(MailboxMember(mail_account_id=acct.id, user_id=mate.id, role="agent"))
    msg = Message(organization_id=demo_user.organization_id, account_id=acct.id,
                  user_id=demo_user.id, sender="K", subject="x", body="?")
    db.session.add(msg)
    db.session.commit()

    calls = []
    monkeypatch.setattr(tn, "_post", lambda url, payload: calls.append(url) or True)
    r = auth_client.post(f"/api/messages/{msg.id}/assign", json={"user_id": mate.id})
    assert r.get_json()["ok"]
    assert calls == ["https://hook"]
