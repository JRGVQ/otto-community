"""Wachtrij voor gebundelde Teams-meldingen.

Eén rij per actionabele mail die op zijn beurt wacht om in een gebundelde kaart mee te
gaan. We bewaren hier een momentopname (onderwerp, afzender, categorie) in plaats van
naar het bericht te verwijzen: de kaart moet leesbaar blijven ook als het bericht
intussen verplaatst of opgeruimd is, en zo hoeft de flush geen join te doen.
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class TeamsPending(TimestampMixin, db.Model):
    __tablename__ = "teams_pending"

    id = db.Column(db.Integer, primary_key=True)
    mail_account_id = db.Column(
        db.Integer, db.ForeignKey("mail_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Momentopname voor de kaart — geen FK naar messages, zie de moduledocstring.
    subject = db.Column(db.String(512))
    sender = db.Column(db.String(255))
    category_name = db.Column(db.String(128))

    account = db.relationship("MailAccount", back_populates="teams_pending")
