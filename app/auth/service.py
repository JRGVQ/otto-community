"""Auth/account services — company registration creates an isolated tenant."""
from __future__ import annotations

from app.extensions import db
from app.models import User


def email_exists(email: str) -> bool:
    return db.session.query(User).filter_by(email=email.lower().strip()).first() is not None


def email_in_trashed_org(email: str) -> bool:
    """True when the email belongs to a user whose organisation is in the trash
    (soft-deleted). Such an address can't be reused for a fresh registration until the
    org is restored or permanently deleted — so registration surfaces that clearly
    instead of silently pretending a verification mail went out."""
    user = db.session.query(User).filter_by(email=email.lower().strip()).first()
    org = user.organization if user else None
    return bool(org and org.deleted_at is not None)


def create_company(company: str, name: str, email: str, password: str) -> User:
    """Create a new organisation (tenant) and its owner (Eigenaar).

    Without a company name, the person's own name becomes the workspace name. The org
    starts empty (no messages/tasks); the owner sets it up via onboarding and by
    connecting a real mailbox.
    """
    from app.tenancy import add_owner_user, provision_organization

    domain = email.split("@")[-1] if "@" in email else None
    workspace_name = (company or "").strip() or name.strip()
    org = provision_organization(workspace_name, domain=domain, demo=False)
    user = add_owner_user(org, name, email, password)

    from app.events import record_event

    record_event(org, "org.created", detail=workspace_name, actor=name)
    db.session.commit()
    return user
