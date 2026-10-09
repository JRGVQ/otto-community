"""Users, roles, permissions and per-user settings."""
from __future__ import annotations

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.constants import ROLE_MATRIX
from app.extensions import db
from app.models.base import TimestampMixin


class Permission(db.Model):
    __tablename__ = "permissions"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False)
    label = db.Column(db.String(128), nullable=False)


class Role(db.Model):
    __tablename__ = "roles"
    id = db.Column(db.Integer, primary_key=True)
    # Per-organisation: each tenant owns its role set, so one company's role/permission
    # edits can never affect another company (RBAC is part of tenant data segregation).
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    name = db.Column(db.String(64), nullable=False)
    is_owner = db.Column(db.Boolean, default=False, nullable=False)

    organization = db.relationship("Organization", back_populates="roles")
    perms = db.relationship(
        "RolePermission", back_populates="role", cascade="all, delete-orphan"
    )

    __table_args__ = (db.UniqueConstraint("organization_id", "name", name="uq_role_org_name"),)

    def allows(self, perm_key: str) -> bool:
        for rp in self.perms:
            if rp.permission and rp.permission.key == perm_key:
                return bool(rp.allowed)
        # Fall back to the canonical matrix if a row is missing.
        return bool(ROLE_MATRIX.get(self.name, {}).get(perm_key, 0))


class RolePermission(db.Model):
    """One cell of the role × permission matrix."""

    __tablename__ = "role_permissions"
    id = db.Column(db.Integer, primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False)
    permission_id = db.Column(db.Integer, db.ForeignKey("permissions.id"), nullable=False)
    allowed = db.Column(db.Boolean, default=False, nullable=False)

    role = db.relationship("Role", back_populates="perms")
    permission = db.relationship("Permission")

    __table_args__ = (db.UniqueConstraint("role_id", "permission_id"),)


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255))
    name = db.Column(db.String(128), nullable=False)
    initials = db.Column(db.String(4))
    avatar_color = db.Column(db.String(9), default="#6C4CE6")
    # Profile extras — filled in by the user on Instellingen, or automatically from the
    # Microsoft 365 profile (/me) after connecting a mailbox. Feed a.o. the signature
    # placeholders ({{functie}}, {{telefoon}}, {{mobiel}}, {{afdeling}}, {{locatie}}).
    job_title = db.Column(db.String(128))
    phone = db.Column(db.String(64))    # vaste lijn (Graph: businessPhones[0])
    mobile = db.Column(db.String(64))   # gsm (Graph: mobilePhone)
    department = db.Column(db.String(128))       # Graph: department
    office_location = db.Column(db.String(128))  # Graph: officeLocation

    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"))
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"))
    status = db.Column(db.String(32), default="Actief")  # Actief / Uitgenodigd / Geblokkeerd
    last_active = db.Column(db.String(32), default="nu")  # display string from prototype
    mailbox_count = db.Column(db.Integer, default=0)
    onboarded = db.Column(db.Boolean, default=False)
    # Whether the user has seen the post-onboarding intro ("Rondleiding"). New owners see it
    # once right after finishing onboarding; everyone can revisit it from the sidebar.
    intro_seen = db.Column(db.Boolean, default=False, nullable=False)
    is_platform_owner = db.Column(db.Boolean, default=False)  # instance administrator (/owner)
    email_verified = db.Column(db.Boolean, default=False)
    verify_sent_at = db.Column(db.DateTime)

    # Opt-in daily task-digest email. `daily_email_sent_on` is the last date a digest
    # was sent/checked, so the scheduler emails each user at most once per day.
    daily_email_enabled = db.Column(db.Boolean, default=False, nullable=False)
    daily_email_sent_on = db.Column(db.Date)

    # Analyse-bord: volgorde + verborgen widgets per gebruiker, als JSON
    # ({"order": [...], "hidden": [...]}). NULL = de standaardindeling.
    analytics_layout = db.Column(db.Text)

    organization = db.relationship("Organization", back_populates="users")
    role = db.relationship("Role")
    mail_accounts = db.relationship(
        "MailAccount", back_populates="user", cascade="all, delete-orphan"
    )
    settings = db.relationship(
        "Setting", back_populates="user", cascade="all, delete-orphan",
        order_by="Setting.position",
    )
    style_profile = db.relationship(
        "StyleProfile", back_populates="user", uselist=False,
        cascade="all, delete-orphan",
    )

    # ── Auth helpers ──────────────────────────────────────────────────────
    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return bool(self.password_hash) and check_password_hash(self.password_hash, password)

    def touch(self) -> None:
        self.last_active = "nu"

    # ── RBAC ──────────────────────────────────────────────────────────────
    @property
    def role_name(self) -> str:
        return self.role.name if self.role else "Gebruiker"

    def has_perm(self, perm_key: str) -> bool:
        return bool(self.role and self.role.allows(perm_key))

    def setting(self, key: str, default: bool = False) -> bool:
        """De stand van een persoonlijke schakelaar; `default` als de rij ontbreekt.

        Pipeline-gates geven default=True mee: een gebruiker van vóór de introductie
        van een schakelaar mag nooit stilletjes functionaliteit verliezen."""
        for s in self.settings:
            if s.key == key:
                return bool(s.on)
        return default


class Setting(db.Model):
    """A per-user feature toggle (prototype settings list)."""

    __tablename__ = "settings"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    key = db.Column(db.String(64), nullable=False)
    label = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text)
    on = db.Column(db.Boolean, default=True)
    position = db.Column(db.Integer, default=0)

    user = db.relationship("User", back_populates="settings")


def initials_for(name: str) -> str:
    """Two letters for the avatar. Clipped to the column width (String(4)): uppercasing
    can lengthen a character (\u00df \u2192 SS, \ufb04 \u2192 FFL), and an oversized value is a hard
    error on Postgres."""
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()[:4]
    return (parts[0][0] + parts[-1][0]).upper()[:4]
