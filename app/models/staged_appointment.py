"""Een klaargezette afspraak, wachtend op één klik van de gebruiker.

Otto herkent in een binnenkomend antwoord dat de klant een moment bevestigt (zie
`app/appointment.py`) en zet de afspraak hier klaar — hij boekt hem nóóit zelf. Pas als de
gebruiker op "Zet in agenda" klikt, maakt de provider het echte agenda-item en vullen we
`event_id`. Zo blijft de agenda van de gebruiker en kost een verkeerd gelezen zin hoogstens
één klik "weggooien".

We bewaren een momentopname (onderwerp, tegenpartij) net als de Teams-wachtrij: de kaart
moet leesbaar blijven ook als het bericht intussen verplaatst is, zonder join.
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class StagedAppointment(TimestampMixin, db.Model):
    __tablename__ = "staged_appointments"

    id = db.Column(db.Integer, primary_key=True)
    mail_account_id = db.Column(
        db.Integer, db.ForeignKey("mail_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"))
    # Wie de afspraak te zien krijgt en mag bevestigen (postvak-eigenaar of toegewezen lid).
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Eén klaargezette afspraak per gesprek — anders zet elke ronde van de klant er een bij.
    provider_thread_id = db.Column(db.String(255), index=True)
    source_message_id = db.Column(db.Integer, db.ForeignKey("messages.id", ondelete="SET NULL"))

    # Momentopname voor de kaart — geen FK-afhankelijkheid, zie de moduledocstring.
    subject = db.Column(db.String(512))
    counterparty = db.Column(db.String(255))

    start_at = db.Column(db.DateTime(timezone=True), nullable=False)
    duration_min = db.Column(db.Integer, default=30)

    status = db.Column(db.String(16), default="pending")  # pending / confirmed / dismissed
    event_id = db.Column(db.String(255))                   # gezet zodra de afspraak echt geboekt is

    account = db.relationship("MailAccount", back_populates="staged_appointments")
