"""Gedeelde helpers voor de backoffice-schermen."""
from __future__ import annotations

from flask_login import current_user

from app.extensions import db
from app.models import MailAccount, Message, Organization, User


def _company_row(org: Organization) -> dict:
    from datetime import timedelta

    from app.tasks.schedule import TRASH_RETENTION_DAYS

    lic = org.license
    users = [u for u in org.users]
    purge_on = org.deleted_at + timedelta(days=TRASH_RETENTION_DAYS) if org.deleted_at else None
    return {
        "org": org,
        "license": lic,
        "users": len(users),
        "mailboxes": db.session.query(MailAccount).join(User, MailAccount.user_id == User.id)
            .filter(User.organization_id == org.id).count(),
        "messages": db.session.query(Message).filter_by(organization_id=org.id).count(),
        "status": lic.effective_status if lic else "geen",
        "purge_on": purge_on,
    }


def _ctx(active: str) -> dict:
    return {"me": current_user, "active": active}


