"""Soft-delete environments: owner trash/restore/destroy + member lockout."""
from app.extensions import db
from app.models import Organization
from app.tenancy import provision_organization, soft_delete_org


def test_soft_delete_moves_company_to_trash(auth_client):
    org = provision_organization("Trash BV", domain="trash.be", demo=False)
    db.session.commit()
    b1 = auth_client.get("/owner/companies").get_data(as_text=True)
    assert "Trash BV" in b1 and "Prullenbak" not in b1   # active, no trash section yet

    soft_delete_org(org)
    b2 = auth_client.get("/owner/companies").get_data(as_text=True)
    assert "Prullenbak" in b2 and "Trash BV" in b2        # now listed under Prullenbak


def test_restore_then_permanent_delete(auth_client):
    org = provision_organization("Gone BV", domain="gone.be", demo=False)
    db.session.commit()
    oid, slug = org.id, org.slug

    soft_delete_org(org)
    auth_client.post(f"/owner/companies/{oid}/restore")
    db.session.expire_all()
    assert db.session.get(Organization, oid).deleted_at is None   # restored

    soft_delete_org(db.session.get(Organization, oid))
    auth_client.post(f"/owner/companies/{oid}/destroy", data={"confirm": slug})  # permanent, only from trash
    db.session.expire_all()
    assert db.session.get(Organization, oid) is None


def test_destroy_requires_matching_slug_confirmation(auth_client):
    """Defence in depth: a permanent delete only proceeds when the typed slug matches."""
    org = provision_organization("Typo BV", domain="typo.be", demo=False)
    db.session.commit()
    oid, slug = org.id, org.slug
    soft_delete_org(org)

    auth_client.post(f"/owner/companies/{oid}/destroy", data={"confirm": "verkeerd"})
    db.session.expire_all()
    assert db.session.get(Organization, oid) is not None          # wrong confirm → kept

    auth_client.post(f"/owner/companies/{oid}/destroy", data={"confirm": slug})
    db.session.expire_all()
    assert db.session.get(Organization, oid) is None              # correct confirm → gone


def test_destroy_purges_knowledge_sources(auth_client):
    """Regression: knowledge_sources has a non-nullable org FK and no cascade, so it
    must be purged explicitly — otherwise the org delete raises a FK violation (a 500
    on Postgres). SQLite doesn't enforce FKs, so assert the rows are actually gone."""
    from app.models import KnowledgeSource

    org = provision_organization("Kennis BV", domain="kennis.be", demo=False)
    db.session.add(KnowledgeSource(organization_id=org.id, name="SharePoint",
                                   site_url="https://contoso.sharepoint.com/sites/x"))
    db.session.commit()
    oid, slug = org.id, org.slug

    soft_delete_org(org)
    r = auth_client.post(f"/owner/companies/{oid}/destroy", data={"confirm": slug}, follow_redirects=False)
    assert r.status_code == 302                                    # not a 500
    db.session.expire_all()
    assert db.session.get(Organization, oid) is None
    assert db.session.query(KnowledgeSource).filter_by(organization_id=oid).count() == 0


def test_destroy_refuses_active_org(auth_client):
    org = provision_organization("Live BV", domain="live.be", demo=False)
    db.session.commit()
    oid, slug = org.id, org.slug
    auth_client.post(f"/owner/companies/{oid}/destroy", data={"confirm": slug})  # not in trash → ignored
    db.session.expire_all()
    assert db.session.get(Organization, oid) is not None


def test_purge_expired_trash_removes_old_keeps_recent(app):
    """The daily purge wipes trash older than the retention window, keeps the rest."""
    from datetime import timedelta

    from app.models.base import utcnow
    from app.tasks.schedule import TRASH_RETENTION_DAYS, purge_expired_trash

    old = provision_organization("Oud BV", domain="oud.be", demo=False)
    recent = provision_organization("Vers BV", domain="vers.be", demo=False)
    db.session.commit()
    old_id, recent_id = old.id, recent.id
    old.deleted_at = utcnow() - timedelta(days=TRASH_RETENTION_DAYS + 1)
    recent.deleted_at = utcnow() - timedelta(days=1)
    db.session.commit()

    n = purge_expired_trash()
    db.session.expire_all()
    assert n == 1
    assert db.session.get(Organization, old_id) is None           # 31 days → purged
    assert db.session.get(Organization, recent_id) is not None    # 1 day → kept


def test_owner_can_delete_own_environment(client, app):
    """Self-service soft delete: owner types the company name + password → trashed."""
    from app.auth.service import create_company

    user = create_company("Zelf BV", "Zelf Owner", "owner@zelfbv.be", "geheim123")
    user.email_verified = True
    user.onboarded = True
    db.session.commit()
    oid = user.organization_id
    client.post("/auth/login", data={"email": "owner@zelfbv.be", "password": "geheim123"})

    # Wrong confirmation → nothing happens.
    client.post("/app/account/delete", data={"confirm_name": "fout", "password": "geheim123"})
    db.session.expire_all()
    assert db.session.get(Organization, oid).deleted_at is None

    # Correct name + password → soft-deleted and logged out.
    r = client.post("/app/account/delete",
                    data={"confirm_name": "Zelf BV", "password": "geheim123"}, follow_redirects=False)
    assert r.status_code == 302
    db.session.expire_all()
    assert db.session.get(Organization, oid).deleted_at is not None
    # Session ended: the app is no longer reachable.
    assert client.get("/app/inbox", follow_redirects=False).status_code == 302


def test_non_owner_cannot_delete_environment(client, app):
    """A regular member hitting the endpoint is refused (403), org stays intact."""
    from app.auth.service import create_company
    from app.models import Role, User

    owner = create_company("Team BV", "Team Owner", "owner@teambv.be", "geheim123")
    org = owner.organization
    role = db.session.query(Role).filter_by(organization_id=org.id, name="Gebruiker").first()
    member = User(email="lid@teambv.be", name="Gewoon Lid", organization_id=org.id,
                  role_id=role.id, status="Actief", email_verified=True, onboarded=True)
    member.set_password("geheim123")
    db.session.add(member)
    db.session.commit()
    oid = org.id

    client.post("/auth/login", data={"email": "lid@teambv.be", "password": "geheim123"})
    r = client.post("/app/account/delete",
                    data={"confirm_name": "Team BV", "password": "geheim123"})
    assert r.status_code == 403
    db.session.expire_all()
    assert db.session.get(Organization, oid).deleted_at is None


def test_soft_deleted_org_locks_out_members(client):
    from app.auth.service import create_company

    user = create_company("Block BV", "Blok Owner", "blok@blokbv.be", "geheim123")
    user.email_verified = True
    user.onboarded = True
    db.session.commit()
    client.post("/auth/login", data={"email": "blok@blokbv.be", "password": "geheim123"})
    assert client.get("/app/inbox").status_code == 200          # has access while active

    soft_delete_org(user.organization)
    # Locked out of the app — but kept signed in and sent to the blocked page (with an
    # "archived" explanation), never silently dumped on the public marketing site.
    r = client.get("/app/inbox", follow_redirects=False)
    assert r.status_code == 302 and "/licentie" in r.headers["Location"]
    assert "gearchiveerd" in client.get("/licentie").get_data(as_text=True)
