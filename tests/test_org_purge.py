"""Definitief wissen van een organisatie laat niets achter.

De opsomming in `delete_org_deep` werd met de hand bijgehouden en liep achter op de
tabellen die er sindsdien bij kwamen. Zo'n gemiste tabel met een NOT NULL org-FK zonder
cascade laat het wissen klappen met een IntegrityError op Postgres — en dan blijft ook de
dagelijkse `purge_expired_trash()` omvallen, zodat de prullenbak nooit leegloopt.

SQLite handhaaft in de tests geen foreign keys, dus we toetsen niet op de crash maar op
wat er werkelijk toe doet: blijven er rijen achter?
"""
from __future__ import annotations

from app.auth.service import create_company
from app.extensions import db
from app.tenancy import delete_org_deep


def test_purge_leaves_no_rows_behind(app):
    from app.models import AutoReplySetting, MailAccount, Signature, SignatureTemplate

    user = create_company("Weg BV", "Owner", "weg@kantoor.be", "geheim123")
    org = user.organization
    oid = org.id

    acct = MailAccount(user_id=user.id, organization_id=oid, provider="outlook",
                       email="weg@kantoor.be", status="connected")
    db.session.add(acct)
    db.session.flush()
    # Tabellen die NIET in de handmatige opsomming stonden:
    db.session.add(SignatureTemplate(organization_id=oid, slug="sjabloon", name="Sjabloon",
                                    html="<div>x</div>"))
    db.session.add(AutoReplySetting(organization_id=oid, mail_account_id=acct.id))
    db.session.add(Signature(organization_id=oid, name="Std", body="Groeten"))
    db.session.commit()

    delete_org_deep(org)
    db.session.commit()

    # Geen enkele tabel mag nog een rij voor deze organisatie hebben.
    achtergebleven = []
    for table in db.metadata.sorted_tables:
        col = table.columns.get("organization_id")
        if col is None:
            continue
        n = db.session.execute(
            db.select(db.func.count()).select_from(table).where(col == oid)
        ).scalar()
        if n:
            achtergebleven.append(f"{table.name}={n}")
    assert achtergebleven == [], f"rijen blijven achter: {achtergebleven}"
