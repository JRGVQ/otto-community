"""User setting toggles."""
from __future__ import annotations

from flask_login import current_user, login_required

from app.api import api_bp
from app.api._helpers import fail, ok
from app.extensions import db
from app.models import Setting


def _catch_up_categorisation(user, limit: int = 200) -> None:
    """Verwerk de achterstand nadat "Automatische categorisatie" weer aan ging.

    De sync enqueuet elk bericht precies één keer; mail die binnenkwam terwijl de
    schakelaar uit stond zou anders voorgoed ongesorteerd blijven. Nieuwste eerst en
    begrensd — een gigantische achterstand hoort niet in één request, en de rest volgt
    bij een volgende keer omzetten of blijft bewust handmatig sorteerbaar."""
    from app.models import Message
    from app.tasks.enqueue import enqueue
    from app.tasks.pipeline import process_incoming

    acct_ids = [a.id for a in user.mail_accounts if not a.is_shared]
    if not acct_ids:
        return
    backlog = (
        db.session.query(Message.id)
        .filter(Message.account_id.in_(acct_ids),
                Message.category_id.is_(None),
                Message.is_seed.is_(False))
        .order_by(Message.id.desc())
        .limit(limit)
        .all()
    )
    for (mid,) in backlog:
        enqueue(process_incoming, mid)


@api_bp.route("/settings/<key>/toggle", methods=["POST"])
@login_required
def toggle_setting(key: str):
    from app.constants import USER_SETTING_KEYS

    # Alleen levende schakelaars: oude prototype-rijen (vip, samenvatting, …) staan
    # nog in de database maar zijn niet meer zichtbaar én niet meer schakelbaar.
    if key not in USER_SETTING_KEYS:
        return fail("onbekend", 404)
    setting = (
        db.session.query(Setting)
        .filter_by(user_id=current_user.id, key=key)
        .first()
    )
    if not setting:
        return fail("onbekend", 404)
    setting.on = not setting.on
    db.session.commit()
    if key == "categorisatie" and setting.on:
        _catch_up_categorisation(current_user)
    return ok(on=setting.on)


@api_bp.route("/settings/recategorize", methods=["POST"])
@login_required
def recategorize_all():
    """Herstel + herclassificeer de eigen inbox — de zelfhulp-knop.

    Bedoeld voor wie mail verkeerd (bv. als spam) zag binnenkomen door de smalle-cursor-
    bug: leeg-opgeslagen berichten krijgen hun echte inhoud terug en alles gaat opnieuw
    door de sortering. Alleen persoonlijke postvakken; gedeelde mailboxen niet."""
    from flask_babel import gettext as _

    from app.tasks.recategorize import recategorize_user

    n = recategorize_user(current_user)
    if not n:
        return ok(toast=_("Geen berichten om opnieuw te sorteren."))
    return ok(toast=_("%(n)s berichten worden opnieuw gesorteerd — dit kan even duren.", n=n))


@api_bp.route("/settings/daily-email/toggle", methods=["POST"])
@login_required
def toggle_daily_email():
    """Turn the opt-in daily task-digest email on/off for the current user."""
    current_user.daily_email_enabled = not current_user.daily_email_enabled
    db.session.commit()
    return ok(on=current_user.daily_email_enabled)


@api_bp.route("/analytics/layout", methods=["POST"])
@login_required
def save_analytics_layout():
    """Bewaar de indeling van het Analyse-bord (volgorde + verborgen widgets).

    Onbekende widget-keys worden genegeerd in plaats van geweigerd: de client kan een
    oudere pagina open hebben staan dan de code die dit afhandelt."""
    from flask import request

    from app.web.context.widgets import save_layout

    data = request.get_json(silent=True) or {}
    order = data.get("order") if isinstance(data.get("order"), list) else []
    hidden = data.get("hidden") if isinstance(data.get("hidden"), list) else []
    clean_order, clean_hidden = save_layout(current_user, order, hidden)
    return ok(order=clean_order, hidden=clean_hidden)
