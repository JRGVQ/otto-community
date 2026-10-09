"""Domain claims — proof that a company controls the e-mail domain it says it does.

A company claims a domain, publishes the TXT record Otto hands it, and Otto verifies
it against public DNS. Verification grants *exclusivity*: once a domain is verified by
one organisation, no other organisation can route SSO for it or register on it. See
app/domains.py for the resolver and the enforcement helpers.
"""
from __future__ import annotations

import secrets

from app.extensions import db
from app.models.base import utcnow

# The TXT record lives on a dedicated subdomain so it never collides with a customer's
# SPF/DMARC records at the apex, and its value is prefixed so a shared DNS zone can hold
# several vendors' verification records side by side.
RECORD_PREFIX = "_otto-verify"
VALUE_PREFIX = "otto-verify="


class DomainClaim(db.Model):
    __tablename__ = "domain_claims"
    __table_args__ = (db.UniqueConstraint("organization_id", "domain", name="uq_domain_claim_org_domain"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    domain = db.Column(db.String(128), nullable=False, index=True)  # bare + lowercase, e.g. "acme.be"
    token = db.Column(db.String(64), nullable=False)

    verified_at = db.Column(db.DateTime, index=True)  # NULL = claimed but not yet proven
    last_checked_at = db.Column(db.DateTime)
    last_error = db.Column(db.String(255))  # why the most recent check failed (UI hint)
    created_at = db.Column(db.DateTime, default=utcnow)

    organization = db.relationship("Organization")

    @staticmethod
    def new_token() -> str:
        return secrets.token_urlsafe(24)

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None

    @property
    def record_name(self) -> str:
        """The DNS name the customer must create the TXT record on."""
        return f"{RECORD_PREFIX}.{self.domain}"

    @property
    def record_value(self) -> str:
        """The exact TXT value Otto looks for."""
        return f"{VALUE_PREFIX}{self.token}"
