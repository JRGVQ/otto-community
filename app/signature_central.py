"""Centraal handtekening-beheer: IT beheert de (per-domein) handtekeningen; een gebruiker
kan zijn eigen velden alleen wijzigen via een voorstel dat IT goedkeurt.

De handtekening-lay-out blijft centraal (de per-domein/standaard org-handtekening). Wat een
gebruiker mag voorstellen, zijn de eigen profielvelden die de placeholders voeden
(functie/afdeling/locatie/telefoon/mobiel) plus zijn waarde van de org-brede variabelen.
Persoonlijke variabelen blijven vrij (die zijn per definitie van de gebruiker zelf).

Bij goedkeuring worden de voorgestelde waarden op de echte velden toegepast, zodat de
resolver (app/signatures.py) en placeholder_values ongewijzigd blijven.
"""
from __future__ import annotations

# De vaste profielvelden die de handtekening-placeholders voeden, met hun maxlen.
CENTRAL_FIELDS = {"job_title": 128, "department": 128, "office_location": 128,
                  "phone": 64, "mobile": 64}


def is_central(org) -> bool:
    """Staat centraal beheer met goedkeuringsflow aan voor deze organisatie?"""
    org_id = getattr(org, "id", None)
    if not org_id:
        return False
    try:
        from app.extensions import db
        from app.models import CloudSignatureSetting

        row = db.session.query(CloudSignatureSetting).filter_by(organization_id=org_id).first()
    except Exception:  # noqa: BLE001 — kolom/tabel nog niet gemigreerd
        return False
    return bool(row and row.central_management)


def apply_payload(user, payload: dict) -> None:
    """Pas een goedgekeurd voorstel toe op de echte profielvelden en org-variabele-waarden.

    Alleen bekende velden en org-brede variabelen (nooit persoonlijke) worden geraakt, zodat
    een voorstel niets buiten de handtekening kan wijzigen."""
    from app.extensions import db
    from app.models import SignatureVariable, SignatureVariableValue

    for field, maxlen in CENTRAL_FIELDS.items():
        if field in payload:
            value = (str(payload[field] or "").strip()[:maxlen]) or None
            setattr(user, field, value)
    for key, raw in (payload or {}).items():
        if not key.startswith("var-"):
            continue
        try:
            var_id = int(key[4:])
        except ValueError:
            continue
        var = db.session.get(SignatureVariable, var_id)
        # Uitsluitend org-brede variabelen van de eigen organisatie.
        if not var or var.organization_id != user.organization_id or var.user_id is not None:
            continue
        value = str(raw or "").strip()[:512] or None
        row = (db.session.query(SignatureVariableValue)
               .filter_by(variable_id=var_id, user_id=user.id).first())
        if row is None:
            if not value:
                continue
            row = SignatureVariableValue(organization_id=user.organization_id,
                                         variable_id=var_id, user_id=user.id)
            db.session.add(row)
        row.value = value
