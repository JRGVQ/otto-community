"""Snippets — herbruikbare tekstblokken voor antwoorden.

Een gedeelde mailbox beantwoordt dezelfde vragen telkens opnieuw: openingsuren,
retourvoorwaarden, "we kijken het na". Een snippet is zo'n stuk tekst, één keer goed
geschreven en door het hele team te hergebruiken, zodat iedereen hetzelfde antwoordt.

Zonder `account_id` geldt een snippet voor de hele organisatie; met een account hoort
hij bij één gedeelde mailbox. Zie app/snippets.py voor het samenvoegen van beide.
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class Snippet(TimestampMixin, db.Model):
    __tablename__ = "snippets"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    # NULL = geldt organisatiebreed; anders alleen in deze (gedeelde) mailbox.
    account_id = db.Column(db.Integer, db.ForeignKey("mail_accounts.id", ondelete="CASCADE"), index=True)

    title = db.Column(db.String(120), nullable=False)
    body = db.Column(db.Text, nullable=False)
    position = db.Column(db.Integer, default=0)
    # Hoe vaak dit blok in een concept is ingevoegd — laat zien wat het team echt gebruikt.
    used_count = db.Column(db.Integer, default=0, nullable=False)

    organization = db.relationship("Organization")
    account = db.relationship("MailAccount")

    @property
    def is_org_wide(self) -> bool:
        return self.account_id is None
