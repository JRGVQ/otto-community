"""Organization and org-level configuration (SSO, AI, providers, audit)."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin, utcnow


class Organization(TimestampMixin, db.Model):
    __tablename__ = "organizations"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    slug = db.Column(db.String(64), unique=True, index=True)  # tenant/environment key
    domain = db.Column(db.String(128))
    # Plain id (no DB-level FK) to avoid a users↔organizations constraint cycle.
    owner_user_id = db.Column(db.Integer)
    is_demo = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime, index=True)  # soft-delete: NULL = active
    org_push_cats = db.Column(db.Boolean, default=True)
    # Categorieën in Outlook nummeren ("1. Te beantwoorden") volgens hun eigen volgorde,
    # zodat de belangrijkste bovenaan sorteert in Outlooks alfabetische categorielijst.
    outlook_category_numbering = db.Column(db.Boolean, default=False, nullable=False,
                                           server_default="0")

    # Provider config surfaced on the admin "Providers & Graph" screen.
    graph_app_id = db.Column(db.String(64))
    graph_tenant_id = db.Column(db.String(64))
    graph_consent = db.Column(db.Boolean, default=True)
    # When the org's Microsoft 365 admin granted tenant-wide consent via the one-click
    # admin-consent flow (NULL = not yet). Lets every user then connect without their own
    # consent prompt, and surfaces a "✓ approved" state on the Integraties page.
    graph_admin_consented_at = db.Column(db.DateTime)
    graph_notifications = db.Column(db.Boolean, default=True)
    gmail_pubsub = db.Column(db.String(128))
    gmail_watch = db.Column(db.Boolean, default=True)
    imap_status = db.Column(db.String(32), default="enabled")

    # Per-company "bring your own app" connector credentials (secrets encrypted at
    # rest). When set, OAuth uses the org's own Microsoft/Google app instead of the
    # shared platform app — consent then stays inside the customer's own tenant.
    graph_client_secret_enc = db.Column(db.Text)
    google_client_id = db.Column(db.String(128))
    google_client_secret_enc = db.Column(db.Text)

    users = db.relationship("User", back_populates="organization")
    roles = db.relationship(
        "Role", back_populates="organization", cascade="all, delete-orphan",
    )
    license = db.relationship(
        "License", back_populates="organization", uselist=False,
        cascade="all, delete-orphan",
    )
    categories = db.relationship(
        "Category", back_populates="organization",
        cascade="all, delete-orphan", order_by="Category.position",
    )
    integrations = db.relationship(
        "Integration", back_populates="organization",
        cascade="all, delete-orphan", order_by="Integration.position",
    )
    graph_scopes = db.relationship(
        "GraphScope", back_populates="organization",
        cascade="all, delete-orphan", order_by="GraphScope.position",
    )
    system_status = db.relationship(
        "SystemStatus", back_populates="organization",
        cascade="all, delete-orphan", order_by="SystemStatus.position",
    )
    audit_logs = db.relationship(
        "AuditLog", back_populates="organization",
        cascade="all, delete-orphan", order_by="AuditLog.position",
    )
    sso = db.relationship("SsoConfig", back_populates="organization", uselist=False, cascade="all, delete-orphan")
    ai = db.relationship("AiConfig", back_populates="organization", uselist=False, cascade="all, delete-orphan")

    @property
    def owner(self):
        from app.models.user import User

        return db.session.get(User, self.owner_user_id) if self.owner_user_id else None

    @property
    def allows_ai_training(self) -> bool:
        """Org-level opt-in: may Otto use this org's data to improve its AI?

        Default False (the AiConfig defaults never_train=True). Admin toggles it
        on the AI screen; a DPA governs all app data either way."""
        return bool(self.ai and not self.ai.never_train)


class SsoConfig(db.Model):
    __tablename__ = "sso_configs"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    enabled = db.Column(db.Boolean, default=True)
    method = db.Column(db.String(32), default="SAML 2.0")
    idp = db.Column(db.String(64), default="Microsoft Entra ID")
    domain = db.Column(db.String(128))
    verified = db.Column(db.Boolean, default=True)
    enforce = db.Column(db.Boolean, default=True)
    mfa = db.Column(db.Boolean, default=True)
    jit = db.Column(db.Boolean, default=True)
    allowed_domains = db.Column(db.JSON, default=list)

    # Real OIDC connection (Microsoft Entra / Google / Okta). Secret encrypted.
    oidc_issuer = db.Column(db.String(255))       # e.g. https://login.microsoftonline.com/<tenant>/v2.0
    oidc_client_id = db.Column(db.String(255))
    oidc_client_secret_enc = db.Column(db.Text)

    # SAML 2.0 connection — for IdPs that don't speak OIDC (older ADFS, Okta SAML apps).
    # No secret here: SAML trust rests on the IdP's signing certificate, which is public
    # by design. See app/auth/oauth/saml.py.
    saml_entity_id = db.Column(db.String(255))    # the IdP's EntityID (its "issuer")
    saml_sso_url = db.Column(db.String(512))      # where we send the AuthnRequest
    saml_x509_cert = db.Column(db.Text)           # the IdP's signing certificate (PEM/base64)

    @property
    def saml_ready(self) -> bool:
        """True when this org can actually complete a SAML login."""
        return bool(self.enabled and self.saml_sso_url and self.saml_x509_cert)

    @property
    def oidc_ready(self) -> bool:
        return bool(self.enabled and self.oidc_issuer and self.oidc_client_id)

    organization = db.relationship("Organization", back_populates="sso")


class AiConfig(db.Model):
    __tablename__ = "ai_configs"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    model = db.Column(db.String(32), default="azure")  # azure / openai / anthropic / self
    residency = db.Column(db.String(64), default="EU (België / West-Europa)")
    never_train = db.Column(db.Boolean, default=True)
    fewshot = db.Column(db.Boolean, default=True)
    limit = db.Column(db.Integer, default=500)
    confidence = db.Column(db.Integer, default=70)

    # ── Bring-your-own LLM: the organisation's own provider key, used instead of the
    #    instance-wide model. The key is stored encrypted (Fernet). ──
    byo_provider = db.Column(db.String(32))      # anthropic | openai | azure | compatible
    byo_api_key_enc = db.Column(db.Text)
    byo_model = db.Column(db.String(128))         # model id, or Azure deployment name
    byo_base_url = db.Column(db.String(255))      # OpenAI-compatible base URL, or Azure endpoint

    organization = db.relationship("Organization", back_populates="ai")


class Integration(db.Model):
    __tablename__ = "integrations"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    key = db.Column(db.String(32), nullable=False)
    name = db.Column(db.String(64), nullable=False)
    tag = db.Column(db.String(4))
    color = db.Column(db.String(9))
    description = db.Column(db.Text)
    connected = db.Column(db.Boolean, default=False)
    position = db.Column(db.Integer, default=0)

    organization = db.relationship("Organization", back_populates="integrations")


class GraphScope(db.Model):
    __tablename__ = "graph_scopes"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    name = db.Column(db.String(64), nullable=False)
    description = db.Column(db.Text)
    on = db.Column(db.Boolean, default=False)
    locked = db.Column(db.Boolean, default=False)
    position = db.Column(db.Integer, default=0)

    organization = db.relationship("Organization", back_populates="graph_scopes")


class SystemStatus(db.Model):
    __tablename__ = "system_status"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    name = db.Column(db.String(64), nullable=False)
    detail = db.Column(db.String(128))
    ok = db.Column(db.Boolean, default=True)
    position = db.Column(db.Integer, default=0)

    organization = db.relationship("Organization", back_populates="system_status")


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    actor = db.Column(db.String(64))
    action = db.Column(db.Text)
    occurred_at = db.Column(db.DateTime, default=utcnow)  # real event time (source of truth)
    occurred_display = db.Column(db.String(64))           # legacy fallback label
    kind = db.Column(db.String(32), default="user")  # security / system / provider / user
    position = db.Column(db.Integer, default=0)

    organization = db.relationship("Organization", back_populates="audit_logs")
