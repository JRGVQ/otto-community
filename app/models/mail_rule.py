"""Inbox-regels — door Otto beheerd, door Outlook uitgevoerd.

De regel wordt naar Exchange geschreven en draait daar, dus ook wanneer Otto stilstaat.
Otto houdt een eigen kopie bij zodat je ze hier kan bekijken, aanpassen en herordenen,
en zodat een mislukte push zichtbaar is in plaats van stil te verdwijnen.

`conditions` en `actions` zijn JSON in Otto's eigen woordenschat (zie app/mailrules.py);
die wordt pas bij het pushen naar de Graph-vorm vertaald. Dat scheidt wat de gebruiker
instelt van hoe Microsoft het toevallig noemt.
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class MailRule(TimestampMixin, db.Model):
    __tablename__ = "mail_rules"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("mail_accounts.id", ondelete="CASCADE"),
                           nullable=False, index=True)

    name = db.Column(db.String(120), nullable=False)
    sequence = db.Column(db.Integer, default=0)      # volgorde waarin Outlook ze toepast
    enabled = db.Column(db.Boolean, default=True, nullable=False)

    conditions = db.Column(db.JSON, default=dict)
    actions = db.Column(db.JSON, default=dict)

    # Het id van de regel in Outlook. Leeg = nog niet (of niet meer) gepusht.
    provider_rule_id = db.Column(db.String(255))
    last_error = db.Column(db.String(255))           # waarom de laatste push mislukte
    synced_at = db.Column(db.DateTime)

    organization = db.relationship("Organization")
    account = db.relationship("MailAccount")

    @property
    def in_outlook(self) -> bool:
        return bool(self.provider_rule_id)
