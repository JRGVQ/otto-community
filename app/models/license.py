"""Per-organisation access switch — set by the instance administrator.

An organisation is either *active* (everyone in it can use Otto) or *suspended* (the
administrator switched it off from the /owner backoffice). There are no plans, seats,
trials or expiry dates: every feature is available to every organisation.
"""
from __future__ import annotations

from datetime import date

from app.extensions import db
from app.models.base import TimestampMixin


class License(TimestampMixin, db.Model):
    __tablename__ = "licenses"

    STATUSES = ["active", "suspended"]
    ACTIVE_STATUSES = {"active"}

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), unique=True, nullable=False)

    status = db.Column(db.String(16), default="active")
    starts_on = db.Column(db.Date, default=date.today)

    organization = db.relationship("Organization", back_populates="license")

    # ── Derived state ─────────────────────────────────────────────────────
    @property
    def effective_status(self) -> str:
        return "suspended" if self.status == "suspended" else "active"

    @property
    def is_active(self) -> bool:
        return self.effective_status in self.ACTIVE_STATUSES

    @property
    def is_blocked(self) -> bool:
        return not self.is_active

    @classmethod
    def new_active(cls) -> License:
        """The access row every new organisation starts with."""
        return cls(status="active", starts_on=date.today())


STATUSES = License.STATUSES
