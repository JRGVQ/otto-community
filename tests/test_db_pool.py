"""De databaseverbinding wordt vrijgegeven vóór trage externe calls.

Een LLM- of Graph-call duurt seconden tot minuten. Bleef de sessie ondertussen een
transactie openhouden, dan hield hij een connectie uit de pool bezet ("idle in
transaction"). Met pool_size=5 + max_overflow=10 per worker was de pool leeg zodra een
handvol postvakken tegelijk een concept genereerde — het "no available server"-patroon
uit de storing van augustus 2026.
"""
from __future__ import annotations

from app.extensions import db, release_db_connection
from app.models import Organization


def test_releasing_ends_the_open_transaction(app):
    db.session.query(Organization).first()          # opent een transactie
    assert db.session().in_transaction() is True
    assert release_db_connection() is True
    assert db.session().in_transaction() is False


def test_objects_still_work_after_releasing(app):
    """Na het loslaten zijn objecten verlopen — ze moeten zichzelf opnieuw laden."""
    org = db.session.query(Organization).first()
    naam = org.name
    release_db_connection()
    assert org.name == naam                          # herlaadt zichzelf, geen fout


def test_pending_work_is_never_committed_behind_your_back(app):
    """Veiligheidsklep: hangt er werk in de sessie, dan blijft de verbinding staan.

    Committen zou andermans halve wijziging vastleggen, rollback zou ze weggooien —
    allebei erger dan een tijdelijk geclaimde connectie."""
    org = db.session.query(Organization).first()
    org.name = "Halverwege gewijzigd"
    assert db.session.dirty
    assert release_db_connection() is False          # niets gedaan
    assert db.session.dirty                          # wijziging staat er nog
    db.session.rollback()


def test_the_draft_path_releases_before_the_model(app, demo_user, monkeypatch):
    """Het echte pad: tijdens de modelaanroep mag er geen transactie openstaan."""
    from app.ai.service import LLMService

    gezien = {}

    class _Adapter:
        name = "meetadapter"
        model_name = "meet"
        last_usage = (0, 0)

        def complete(self, system, user, *, max_tokens=800, json=False):
            gezien["in_transactie"] = db.session().in_transaction()
            return "Een concept."

    db.session.query(Organization).first()            # transactie openen
    assert db.session().in_transaction() is True

    LLMService(_Adapter()).draft(
        "Jan", "Onderwerp", "body", "neutraal", None,
        org=demo_user.organization, account=None,
    )
    assert gezien["in_transactie"] is False, "verbinding was nog geclaimd tijdens de LLM-call"
