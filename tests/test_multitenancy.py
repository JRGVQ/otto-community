"""Multi-tenancy, access (active/suspended) and the administrator backoffice."""
from app.extensions import db
from app.models import Message, Organization, User
from app.seed.demo_data import DEMO_PASSWORD


def _register(client, company, email):
    return client.post("/auth/register", data={
        "company": company, "name": "Owner Person", "email": email,
        "password": "geheim123", "confirm": "geheim123",
    }, follow_redirects=False)


def test_registration_creates_isolated_company(client, app):
    _register(client, "Alpha BV", "owner@alpha.be")
    org = db.session.query(Organization).filter_by(name="Alpha BV").first()
    assert org is not None and org.is_demo is False
    assert org.slug == "alpha-bv"
    assert org.license.status == "active" and org.license.is_active
    assert org.owner.email == "owner@alpha.be"
    # Not attached to the demo org.
    assert org.id != db.session.query(Organization).filter_by(name="Acme NV").first().id


def test_registered_company_starts_empty(app):
    """A freshly registered company has no messages/tasks — it sets itself up."""
    from app.auth.service import create_company
    from app.models import Task

    user = create_company("Empty BV", "Owner", "owner@empty.be", "geheim123")
    oid = user.organization_id
    assert db.session.query(Message).filter_by(organization_id=oid).count() == 0
    assert db.session.query(Task).filter_by(organization_id=oid).count() == 0
    assert user.onboarded is False  # lands in the setup checklist on first login


def test_delete_org_deep_removes_all_data_and_spares_others(app):
    from app.auth.service import create_company
    from app.tenancy import add_welcome_inbox, delete_org_deep

    keep = create_company("Keep BV", "K", "k@keep.be", "geheim123")
    drop = create_company("Drop BV", "D", "d@drop.be", "geheim123")
    add_welcome_inbox(drop.organization, drop)  # give it messages + a draft
    db.session.commit()
    drop_oid, keep_oid = drop.organization_id, keep.organization_id
    assert db.session.query(Message).filter_by(organization_id=drop_oid).count() > 0

    delete_org_deep(db.session.get(Organization, drop_oid))
    db.session.commit()

    assert db.session.get(Organization, drop_oid) is None
    assert db.session.query(User).filter_by(organization_id=drop_oid).count() == 0
    assert db.session.query(Message).filter_by(organization_id=drop_oid).count() == 0
    assert db.session.get(Organization, keep_oid) is not None  # the other tenant is untouched


def test_two_companies_are_data_isolated(app):
    from app.auth.service import create_company
    from app.tenancy import add_welcome_inbox

    ua = create_company("Alpha BV", "Owner A", "a@alpha.be", "geheim123")
    ub = create_company("Beta BV", "Owner B", "b@beta.be", "geheim123")
    org_a, org_b = ua.organization, ub.organization
    assert org_a.id != org_b.id
    add_welcome_inbox(org_a, ua)  # fresh orgs start empty — add sample data to check isolation
    add_welcome_inbox(org_b, ub)
    db.session.commit()
    a_msgs = {m.id for m in db.session.query(Message).filter_by(organization_id=org_a.id)}
    b_msgs = {m.id for m in db.session.query(Message).filter_by(organization_id=org_b.id)}
    assert a_msgs and b_msgs and a_msgs.isdisjoint(b_msgs)
    # Each owner's category set is scoped to their own org.
    from app.models import Category

    a_cats = {c.id for c in db.session.query(Category).filter_by(organization_id=org_a.id)}
    b_cats = {c.id for c in db.session.query(Category).filter_by(organization_id=org_b.id)}
    from app.seed.demo_data import CATEGORIES

    n_cats = len(CATEGORIES)
    assert a_cats.isdisjoint(b_cats) and len(a_cats) == n_cats and len(b_cats) == n_cats


def test_suspended_license_blocks_access(client, app):
    from app.email import make_verify_token

    _register(client, "Gamma BV", "g@gamma.be")
    client.get(f"/auth/verify/{make_verify_token('g@gamma.be')}")  # verify => logged in
    user = db.session.query(User).filter_by(email="g@gamma.be").first()
    user.onboarded = True
    user.organization.license.status = "suspended"
    db.session.commit()
    r = client.get("/app/inbox")
    assert r.status_code == 302 and "/licentie" in r.headers["Location"]


def test_platform_owner_can_reach_backoffice(client, app):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    assert client.get("/owner/").status_code == 200
    assert client.get("/owner/companies").status_code == 200


def test_normal_owner_cannot_reach_backoffice(client, app):
    from app.email import make_verify_token

    _register(client, "Delta BV", "d@delta.be")  # registrant is org Eigenaar, not platform owner
    client.get(f"/auth/verify/{make_verify_token('d@delta.be')}")  # verify => logged in
    assert client.get("/owner/").status_code == 403


def test_platform_owner_creates_and_updates_company(client, app):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    r = client.post("/owner/companies/new", data={
        "company": "Epsilon BV", "owner_name": "Eve", "owner_email": "eve@epsilon.be", "password": "geheim123",
    }, follow_redirects=False)
    assert r.status_code == 302
    org = db.session.query(Organization).filter_by(name="Epsilon BV").first()
    assert org is not None and org.license.status == "active"
    # Suspend it, then activate it again.
    client.post(f"/owner/companies/{org.id}/status", data={"action": "suspend"})
    db.session.refresh(org.license)
    assert org.license.status == "suspended" and org.license.is_blocked
    client.post(f"/owner/companies/{org.id}/status", data={"action": "activate"})
    db.session.refresh(org.license)
    assert org.license.status == "active"


def test_roles_are_per_org_isolated(app):
    """Each company has its OWN role set; editing one company's permission matrix
    must never touch another company's (RBAC is part of tenant data segregation)."""
    from app.auth.service import create_company
    from app.models import Role
    from app.tenancy import org_role

    ux = create_company("Iso X BV", "Owner X", "owner@isox.be", "geheim123")
    uy = create_company("Iso Y BV", "Owner Y", "owner@isoy.be", "geheim123")
    org_x, org_y = ux.organization, uy.organization

    x_roles = db.session.query(Role).filter_by(organization_id=org_x.id).all()
    y_roles = db.session.query(Role).filter_by(organization_id=org_y.id).all()
    # Same role names, but distinct rows per org.
    assert {r.name for r in x_roles} == {r.name for r in y_roles}
    assert {r.id for r in x_roles}.isdisjoint({r.id for r in y_roles})

    # Flip a permission on X's Manager role; Y's Manager must be untouched.
    mgr_x, mgr_y = org_role(org_x, "Manager"), org_role(org_y, "Manager")
    assert mgr_x.id != mgr_y.id
    before = mgr_x.allows("viewAudit")
    cell = next(rp for rp in mgr_x.perms if rp.permission.key == "viewAudit")
    cell.allowed = not before
    db.session.commit()
    db.session.expire_all()

    assert org_role(org_x, "Manager").allows("viewAudit") == (not before)  # X changed
    assert org_role(org_y, "Manager").allows("viewAudit") == before        # Y untouched


def test_company_cannot_toggle_another_companys_permissions(client, app):
    """The real attack surface: a logged-in owner of company X hits the permission
    toggle endpoint — company Y's permissions must be unaffected."""
    from app.auth.service import create_company
    from app.tenancy import org_role

    ux = create_company("Edge X BV", "Owner X", "ox@edgex.be", "geheim123")
    uy = create_company("Edge Y BV", "Owner Y", "oy@edgey.be", "geheim123")
    org_x, org_y = ux.organization, uy.organization
    ux.email_verified = True  # so the owner can log in
    db.session.commit()
    before = org_role(org_y, "Manager").allows("viewAudit")

    client.post("/auth/login", data={"email": "ox@edgex.be", "password": "geheim123"})
    r = client.post("/api/admin/perms/toggle", json={"role": "Manager", "perm": "viewAudit"})
    assert r.status_code == 200
    db.session.expire_all()

    assert org_role(org_x, "Manager").allows("viewAudit") == (not before)  # X's own role changed
    assert org_role(org_y, "Manager").allows("viewAudit") == before        # Y untouched


def test_new_company_sees_no_other_org_data(client, app):
    """A brand-new environment must see NOTHING of any other org — including the demo /
    platform-owner org (Acme): no shared rows, cross-org reads 404, its own roles."""
    from app.auth.service import create_company
    from app.models import MailAccount, Message, Organization
    from app.tenancy import org_role

    acme = db.session.query(Organization).filter_by(name="Acme NV").first()
    acme_msg = db.session.query(Message).filter_by(organization_id=acme.id).first()
    assert acme is not None and acme_msg is not None  # the demo org is populated

    u = create_company("Verse BV", "Owner V", "v@verse.be", "geheim123")
    u.email_verified = True
    u.onboarded = True
    db.session.commit()
    org = u.organization
    assert org.id != acme.id

    # No shared data: the fresh org is empty while Acme is populated.
    assert db.session.query(Message).filter_by(organization_id=org.id).count() == 0
    assert db.session.query(MailAccount).filter_by(organization_id=org.id).count() == 0
    assert db.session.query(Message).filter_by(organization_id=acme.id).count() > 0
    # Its roles are its own, distinct from Acme's.
    assert org_role(org, "Manager").id != org_role(acme, "Manager").id

    client.post("/auth/login", data={"email": "v@verse.be", "password": "geheim123"})
    # Reading another org's message is denied (IDOR → 404)...
    assert client.get(f"/api/messages/{acme_msg.id}/notes").status_code == 404
    # ...while its own Beheer overview renders fine.
    assert client.get("/admin/overzicht").status_code == 200


def test_platform_owner_can_impersonate_and_stop(auth_client, app):
    """Only the platform owner can impersonate a company; doing so drops platform-owner
    powers (confined to that one company), is audited, and is reversible."""
    from app.auth.service import create_company
    from app.models import AuditLog

    target = create_company("Target BV", "T Owner", "t@target.be", "geheim123")
    org_id = target.organization_id
    db.session.commit()

    # The platform owner (demo user) reaches /owner before impersonating.
    assert auth_client.get("/owner/").status_code == 200

    r = auth_client.post(f"/owner/companies/{org_id}/impersonate")
    assert r.status_code in (301, 302)
    # Now acting AS the company user → platform-owner powers are gone (no /owner access).
    assert auth_client.get("/owner/").status_code == 403
    # The impersonation is recorded as a security audit event on that org.
    assert db.session.query(AuditLog).filter_by(organization_id=org_id, kind="security").count() >= 1

    # Stop → the platform-owner account is restored.
    auth_client.post("/auth/stop-impersonation")
    assert auth_client.get("/owner/").status_code == 200


def test_non_owner_cannot_impersonate(client, app):
    """A normal company owner cannot impersonate anyone — not even their own org."""
    from app.auth.service import create_company

    u = create_company("NoImp BV", "N Owner", "n@noimp.be", "geheim123")
    u.email_verified = True
    db.session.commit()
    client.post("/auth/login", data={"email": "n@noimp.be", "password": "geheim123"})
    # The whole /owner blueprint (incl. impersonate) is platform-owner only → 403.
    assert client.post(f"/owner/companies/{u.organization_id}/impersonate").status_code == 403


def test_owner_console_locks_to_configured_email(auth_client, app):
    """With PLATFORM_OWNER_EMAIL set, only that exact e-mail may use /owner — a user
    carrying the is_platform_owner flag is still denied if the e-mail doesn't match."""
    # The seeded platform owner is demo@example.com; pin the console to a different e-mail.
    app.config["PLATFORM_OWNER_EMAIL"] = "admin@example.com"
    assert auth_client.get("/owner/").status_code == 403
    # Matching e-mail → allowed.
    app.config["PLATFORM_OWNER_EMAIL"] = "demo@example.com"
    assert auth_client.get("/owner/").status_code == 200
    # Unset → the flag alone decides (dev/test default).
    app.config["PLATFORM_OWNER_EMAIL"] = None
    assert auth_client.get("/owner/").status_code == 200


def test_personal_mailbox_mail_is_private_within_an_org(app):
    """A colleague in the same organisation cannot see another user's personal mailbox
    mail — personal mail is scoped to the mailbox owner, not just the tenant."""
    from app.models import MailAccount

    a = db.session.query(User).filter_by(email="demo@example.com").first()
    b = (
        db.session.query(User)
        .filter(User.organization_id == a.organization_id, User.id != a.id)
        .first()
    )
    assert b is not None  # the demo org has colleagues in the same tenant

    a_msg = (
        db.session.query(Message)
        .join(MailAccount, Message.account_id == MailAccount.id)
        .filter(MailAccount.user_id == a.id, MailAccount.mailbox_type != "shared")
        .first()
    )
    assert a_msg is not None  # A owns the seeded personal mail

    assert a_msg.visible_to(a) is True   # the owner sees their own mail
    assert a_msg.visible_to(b) is False  # a same-org colleague does not


def test_personal_inbox_list_is_owner_scoped(app):
    """The inbox LIST query itself must be owner-scoped.

    Regression: `_personal_messages()` filtered only on the organisation, so every
    colleague saw everyone's personal mail in the inbox, the Outlook view, the badge and
    the bulk-unsubscribe list. `visible_to()` was correct but the list never calls it —
    so asserting on the model helper alone missed the leak entirely.
    """
    from flask_login import login_user

    from app.models import MailAccount
    from app.web.context._base import _personal_messages

    a = db.session.query(User).filter_by(email="demo@example.com").first()
    b = (
        db.session.query(User)
        .filter(User.organization_id == a.organization_id, User.id != a.id)
        .first()
    )
    assert b is not None

    a_msg = (
        db.session.query(Message)
        .join(MailAccount, Message.account_id == MailAccount.id)
        .filter(MailAccount.user_id == a.id, MailAccount.mailbox_type != "shared")
        .first()
    )
    assert a_msg is not None

    with app.test_request_context():
        login_user(a)
        assert a_msg.id in {m.id for m in _personal_messages()}      # owner sees own mail
    with app.test_request_context():
        login_user(b)
        assert a_msg.id not in {m.id for m in _personal_messages()}  # colleague does not


def test_the_inbox_badge_counts_only_your_own_waiting_mail(app):
    """De badge telt nu in SQL i.p.v. de hele lijst in te laden.

    Die telling MOET dezelfde scoping houden als de lijst — anders zou de badge
    opnieuw over de mail van collega's tellen (het lek dat eerder in de lijst zat)."""
    from flask_login import login_user

    from app.web.context._base import _personal_messages, _waiting, _waiting_count

    a = db.session.query(User).filter_by(email="demo@example.com").first()
    b = (
        db.session.query(User)
        .filter(User.organization_id == a.organization_id, User.id != a.id)
        .first()
    )
    assert b is not None

    with app.test_request_context():
        login_user(a)
        # Identiek aan de oude Python-telling over de eigen lijst.
        assert _waiting_count() == len(_waiting(_personal_messages()))
    with app.test_request_context():
        login_user(b)
        assert _waiting_count() == len(_waiting(_personal_messages()))
