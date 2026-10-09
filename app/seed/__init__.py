"""Seed the demo organisation 'Acme NV' on top of the shared tenant provisioning."""
from __future__ import annotations

import secrets

from flask import current_app
from flask_babel import gettext as _

from app.constants import DEFAULT_REPLY_INSTRUCTIONS, DEFAULT_TONE, TONES
from app.extensions import db
from app.models import (
    AuditLog,
    Draft,
    DraftToneVariant,
    MailAccount,
    Message,
    Organization,
    Role,
    Setting,
    StyleProfile,
    Task,
    User,
)
from app.seed import demo_data as D


def already_seeded() -> bool:
    return db.session.query(Organization).filter_by(name=D.ORGANIZATION["name"]).first() is not None


def has_real_org() -> bool:
    """True once the operator has set up their own company (a non-demo tenant). We
    never (re)introduce demo data on top of a real environment — so a stray SEED_DEMO=1
    can't resurrect the demo org after `flask wipe-demo`."""
    return (
        db.session.query(Organization)
        .filter(
            Organization.is_demo.is_(False),
            Organization.deleted_at.is_(None),
        )
        .first()
        is not None
    )


def seed_all(reset: bool = False) -> dict:
    """Create the showcase demo org. Idempotent unless reset=True.

    Skips entirely on a database that already holds the demo org *or* a real
    company, so re-running it (e.g. SEED_DEMO=1 on every container boot) never
    re-adds demo data to a configured production environment.

    In production the seeded login gets a RANDOM password and is NOT a platform
    owner — appoint the real owner with `flask create-owner` / `flask grant-owner`.
    """
    is_prod = not (current_app.config.get("DEBUG") or current_app.config.get("TESTING"))
    if reset:
        db.drop_all()
    db.create_all()
    if already_seeded() or has_real_org():
        return {"status": "exists",
                "password": None if is_prod else D.DEMO_PASSWORD, "email": D.DEMO_EMAIL}

    demo_password = secrets.token_urlsafe(16) if is_prod else D.DEMO_PASSWORD

    from app.tenancy import provision_organization

    org = provision_organization(D.ORGANIZATION["name"], domain=D.ORGANIZATION["domain"], demo=True)
    roles = {r.name: r for r in db.session.query(Role).filter_by(organization_id=org.id).all()}

    # ── Demo users ────────────────────────────────────────────────────────
    demo_user = None
    for u in D.resolve(D.USERS):
        user = User(
            email=u["email"], name=u["name"], initials=u["initials"], avatar_color=u["color"],
            organization_id=org.id, role_id=roles[u["role"]].id, status=u["status"],
            last_active=u["last"], mailbox_count=u["mailboxes"], onboarded=bool(u.get("is_demo")),
            email_verified=True,
        )
        if u.get("is_demo"):
            user.set_password(demo_password)
            # Platform-owner is a dev/test convenience ONLY — never in production
            # (it would be a known-credential super-admin over every tenant).
            user.is_platform_owner = not is_prod
            demo_user = user
        db.session.add(user)
    db.session.flush()
    org.owner_user_id = demo_user.id

    db.session.add(StyleProfile(user_id=demo_user.id, tone=DEFAULT_TONE, greeting=_("Hallo"), signoff=_("Groeten"),
                                signature=_("Groeten,\nSam"), default_reply=DEFAULT_REPLY_INSTRUCTIONS))
    for i, s in enumerate(D.resolve(D.SETTINGS)):
        db.session.add(Setting(user_id=demo_user.id, key=s["key"], label=s["label"], description=s["desc"], on=s["on"], position=i))

    account = MailAccount(user_id=demo_user.id, provider="outlook", email=demo_user.email, status="connected", last_sync=_("2 min geleden"), provider_config={"seed": True})
    db.session.add(account)
    db.session.flush()

    # ── Demo messages + drafts ────────────────────────────────────────────
    from app.timefmt import received_from_hint, reltime

    cats = {c.key: c for c in org.categories}
    by_key: dict[str, Message] = {}
    for i, m in enumerate(D.resolve(D.MESSAGES)):
        recv = received_from_hint(m["time"])  # real timestamp from the "08:42"/"gisteren"/"ma" hint
        msg = Message(
            organization_id=org.id, user_id=demo_user.id, account_id=account.id,
            provider_message_id=f"seed-{i+1}", sender=m["sender"], sender_email=m["email"],
            initials=m["initials"], avatar_color=m["avatar"],
            received_at=recv, time_display=reltime(recv, m["time"]),
            unread=m["unread"], category_id=cats[m["category"]].id, subject=m["subject"],
            snippet=m["snippet"], body=m["body"], lang=m["lang"], is_seed=True,
            unsubscribe_url=(m.get("unsubscribe") or {}).get("url"),
            unsubscribe_mailto=(m.get("unsubscribe") or {}).get("mailto"),
            unsubscribe_oneclick=bool((m.get("unsubscribe") or {}).get("one_click")),
        )
        db.session.add(msg)
        db.session.flush()
        if m["draft"]:
            draft = Draft(message_id=msg.id, status="concept", current_tone=DEFAULT_TONE, current_text=m["draft"][DEFAULT_TONE], placed=True, lang=m["lang"])
            db.session.add(draft)
            db.session.flush()
            for tone in TONES:
                db.session.add(DraftToneVariant(draft_id=draft.id, tone=tone, text=m["draft"][tone]))
        by_key[m["key"]] = msg

    # ── Tasks (linked to their message by stable key) ─────────────────────
    for t in D.resolve(D.TASKS):
        linked = by_key.get(t["linked"])
        db.session.add(Task(
            organization_id=org.id, user_id=demo_user.id, title=t["title"], context=t["context"],
            due_display=t["due"], list_name=t["list"], done=t["done"], urgent=t["urgent"],
            linked_subject=linked.subject if linked else "", linked_message_id=linked.id if linked else None,
        ))

    # ── Audit ─────────────────────────────────────────────────────────────
    from datetime import timedelta

    from app.models.base import utcnow
    now = utcnow()
    for i, a in enumerate(D.resolve(D.AUDIT)):
        occurred = now - timedelta(minutes=a["ago"])
        db.session.add(AuditLog(organization_id=org.id, actor=a["user"], action=a["action"],
                                occurred_at=occurred, occurred_display=reltime(occurred, ""),
                                kind=a["kind"], position=i + 1))

    db.session.commit()
    return {"status": "created", "password": demo_password, "email": demo_user.email}
