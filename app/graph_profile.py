"""Profielvelden uit het Microsoft 365-profiel (/me) — de bron voor de handtekening.

Gebruikt op twee momenten: bij het koppelen (app/auth/oauth/microsoft.py) en daarna
±dagelijks tijdens de sync (app/tasks/sync.py), zodat een wijziging in M365 — nieuwe
functietitel, ander nummer — vanzelf in de handtekening-placeholders belandt.

Alleen LEGE velden worden ingevuld: wat de gebruiker zelf op Instellingen typte, wint
altijd van wat M365 zegt.
"""
from __future__ import annotations

# De standaard-velden voor fill_profile_blanks. onPremisesExtensionAttributes zit erbij
# zodat extensionAttribute1..15 als gekoppeld handtekening-veld beschikbaar zijn zonder
# een tweede Graph-call. De extra adres/bedrijf-velden voeden custom placeholders.
PROFILE_SELECT = (
    "displayName,jobTitle,mobilePhone,businessPhones,department,officeLocation,"
    "companyName,city,country,state,streetAddress,postalCode,faxNumber,employeeId,"
    "userPrincipalName,mail,onPremisesExtensionAttributes"
)

# users.<veld> ← Graph /me-antwoord (fill-blanks-only; maxlen beschermt de kolommen).
_FIELDS = (
    ("job_title", lambda me: me.get("jobTitle"), 128),
    ("department", lambda me: me.get("department"), 128),
    ("office_location", lambda me: me.get("officeLocation"), 128),
    ("mobile", lambda me: me.get("mobilePhone"), 64),
    ("phone", lambda me: next(iter(me.get("businessPhones") or []), None) or me.get("mobilePhone"), 64),
)


def fill_profile_blanks(user, me: dict) -> bool:
    """Vul lege profielvelden van `user` in vanuit een Graph /me-antwoord.

    Geeft terug of er iets veranderde (de aanroeper commit)."""
    changed = False
    for field, pick, maxlen in _FIELDS:
        value = pick(me or {})
        if not getattr(user, field, None) and value:
            setattr(user, field, str(value).strip()[:maxlen])
            changed = True
    return changed


def graph_value(me: dict, attr: str) -> str:
    """De waarde van één (mogelijk geneste) Graph-attribuut uit een /me-antwoord.

    Ondersteunt gewone velden ("city"), het geneste extensionAttributes-object
    ("onPremisesExtensionAttributes.extensionAttribute3") en directory/schema-extensions
    die als platte property terugkomen ("extension_<appId>_kostenplaats"). Onbekend of
    leeg → lege string, zodat de placeholder netjes verdwijnt."""
    if not attr or not isinstance(me, dict):
        return ""
    head, _, rest = attr.partition(".")
    val = me.get(head)
    if rest:
        val = (val or {}).get(rest) if isinstance(val, dict) else None
    return str(val).strip() if val not in (None, "") else ""


def select_fields_for(org) -> str:
    """PROFILE_SELECT plus het top-level segment van elk gekoppeld variabele-attribuut.

    Schema-/directory-extensions (extension_<appId>_x) moeten expliciet ge-$select't
    worden; standaardvelden en onPremisesExtensionAttributes zitten al in PROFILE_SELECT."""
    fields = list(PROFILE_SELECT.split(","))
    for attr in _mapped_attrs(org):
        top = attr.split(".", 1)[0]
        if top and top not in fields:
            fields.append(top)
    return ",".join(fields)


def _mapped_attrs(org) -> list[str]:
    org_id = getattr(org, "id", None)
    if not org_id:
        return []
    try:
        from app.extensions import db
        from app.models import SignatureVariable

        rows = (db.session.query(SignatureVariable.graph_attr)
                .filter(SignatureVariable.organization_id == org_id,
                        SignatureVariable.user_id.is_(None),
                        SignatureVariable.graph_attr.isnot(None))
                .all())
        return [r[0] for r in rows if r[0]]
    except Exception:  # noqa: BLE001 — kolom nog niet gemigreerd → geen mappings
        return []


def refresh_mapped_variables(user, me: dict) -> bool:
    """Vul de waarden van aan Graph gekoppelde org-variabelen bij voor deze gebruiker.

    Voor gekoppelde variabelen is Graph de bron van waarheid: de waarde wordt
    overschreven (niet fill-blanks-only, anders bevriest een oude waarde). Geeft terug
    of er iets veranderde (de aanroeper commit)."""
    org_id = getattr(user, "organization_id", None) if user else None
    if not org_id or not isinstance(me, dict):
        return False
    try:
        from app.extensions import db
        from app.models import SignatureVariable, SignatureVariableValue

        variables = (db.session.query(SignatureVariable)
                     .filter(SignatureVariable.organization_id == org_id,
                             SignatureVariable.user_id.is_(None),
                             SignatureVariable.graph_attr.isnot(None))
                     .all())
    except Exception:  # noqa: BLE001 — nog niet gemigreerd
        return False
    changed = False
    for var in variables:
        value = graph_value(me, var.graph_attr)[:512]
        row = (db.session.query(SignatureVariableValue)
               .filter_by(variable_id=var.id, user_id=user.id).first())
        if row is None:
            if not value:
                continue
            row = SignatureVariableValue(organization_id=org_id, variable_id=var.id, user_id=user.id)
            db.session.add(row)
        if (row.value or "") != value:
            row.value = value or None
            changed = True
    return changed
