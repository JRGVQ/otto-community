"""AI-verbruiksmeting onder gelijktijdigheid.

De meting is de basis van de budgetcap: telt ze te laag, dan handhaaft de cap elke maand
te weinig. Het gevaar zit in read-modify-write — twee gelijktijdige drafts lezen dezelfde
beginstand en de tweede commit overschrijft de eerste. Deze tests dwingen af dat de
verhoging atomair gebeurt en dat het aanmaken van de maandrij een race overleeft.
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.ai.metering import month_usage, record_usage
from app.auth.service import create_company
from app.extensions import db
from app.models import AiUsage


@pytest.fixture()
def org(app):
    return create_company("Acme BV", "Owner", "owner@acme-met.be", "geheim123").organization


def _row(org):
    return db.session.query(AiUsage).filter_by(organization_id=org.id).one()


# ── Basis ────────────────────────────────────────────────────────────────────
def test_first_call_creates_the_month_row(app, org):
    record_usage(org.id, "claude-sonnet-4-6", 100, 20)
    assert month_usage(org) == (100, 20)


def test_further_calls_accumulate(app, org):
    record_usage(org.id, "claude-sonnet-4-6", 100, 20)
    record_usage(org.id, "claude-sonnet-4-6", 50, 5)
    assert month_usage(org) == (150, 25)


def test_empty_calls_are_ignored(app, org):
    record_usage(org.id, "claude-sonnet-4-6", 0, 0)
    assert db.session.query(AiUsage).count() == 0


def test_missing_org_is_ignored(app):
    record_usage(None, "claude-sonnet-4-6", 10, 10)
    assert db.session.query(AiUsage).count() == 0


def test_usage_is_scoped_per_organisation(app, org):
    other = create_company("Andere NV", "O", "o@andere-met.be", "geheim123").organization
    record_usage(org.id, "claude-sonnet-4-6", 100, 10)
    record_usage(other.id, "claude-sonnet-4-6", 7, 1)
    assert month_usage(org) == (100, 10)
    assert month_usage(other) == (7, 1)


# ── Gelijktijdigheid ─────────────────────────────────────────────────────────
def test_the_increment_is_computed_by_the_database(app, org):
    """De kern: de optelling gebeurt in SQL, niet in Python.

    Dit is precies wat verloren updates voorkomt. Telt Python op, dan schrijft een
    worker de waarde terug die híj las (`SET input_tokens = 150`) en overschrijft
    daarmee wat een gelijktijdige worker intussen bijboekte. Laat je de database
    optellen (`SET input_tokens = input_tokens + 50`), dan telt elke verhoging mee,
    ongeacht wie er tussen kwam.

    Een echte race naspelen lukt niet betrouwbaar binnen één sessie — SQLAlchemy
    verlooptt objecten bij commit, dus de tweede lezing is dan toch weer vers. Daarom
    toetsen we het mechanisme rechtstreeks op de SQL die eruit komt.
    """
    from sqlalchemy import event

    record_usage(org.id, "claude-sonnet-4-6", 100, 10)  # rij bestaat al

    statements: list[str] = []
    engine = db.session.get_bind()

    def capture(conn, cursor, statement, params, context, executemany):
        if "ai_usage" in statement.lower() and statement.strip().lower().startswith("update"):
            statements.append(" ".join(statement.split()).lower())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        record_usage(org.id, "claude-sonnet-4-6", 50, 5)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert statements, "er werd geen UPDATE op ai_usage uitgevoerd"
    update = statements[0]
    set_clause = update.split(" set ", 1)[1].split(" where ", 1)[0]

    # De kolom moet rechts van de toekenning staan (input_tokens = ... input_tokens + n).
    # Telt Python op, dan staat er enkel een parameter: input_tokens=?
    assert set_clause.count("input_tokens") >= 2 and "+" in set_clause, (
        "de UPDATE zet een vooraf berekende waarde in plaats van de kolom te verhogen, "
        f"dus gelijktijdige verhogingen overschrijven elkaar: {set_clause}"
    )
    assert month_usage(org) == (150, 15)


def test_a_lost_row_creation_race_does_not_crash(app, org):
    """Twee workers die tegelijk de eerste rij van de maand aanmaken.

    De unieke sleutel op (organization_id, ym, model) laat de verliezer struikelen; die moet
    dan de bestaande rij verhogen in plaats van een 500 te geven of een duplicaat te
    schrijven.
    """
    engine = db.session.get_bind()
    rival = Session(bind=engine)
    try:
        from app.ai.metering import _ym

        rival.add(AiUsage(organization_id=org.id, ym=_ym(), model="claude-sonnet-4-6",
                          input_tokens=40, output_tokens=4))
        rival.commit()  # de rivaal was eerst, op dezelfde (org, maand, model)
    finally:
        rival.close()

    record_usage(org.id, "claude-sonnet-4-6", 10, 1)  # mag niet stuklopen

    db.session.expire_all()
    assert db.session.query(AiUsage).filter_by(organization_id=org.id).count() == 1
    assert month_usage(org) == (50, 5)


def test_many_sequential_calls_add_up_exactly(app, org):
    for _ in range(25):
        record_usage(org.id, "claude-sonnet-4-6", 10, 2)
    assert month_usage(org) == (250, 50)


# ── Samenhang met de cap ─────────────────────────────────────────────────────
def test_budget_gate_follows_the_recorded_usage(app, org):
    from app.ai.metering import budget_eur, within_budget

    app.config["LLM_MONTHLY_BUDGET_EUR"] = 10
    assert within_budget(org) is True
    # Genoeg tokens om ruim over het maandplafond te gaan.
    budget = budget_eur(org)
    huge = int((budget / 0.95 / 3.0) * 1_000_000) + 1_000_000  # ruim boven de inputprijs
    record_usage(org.id, "claude-sonnet-4-6", huge, 0)
    assert within_budget(org) is False


# ── Prijzen per model ────────────────────────────────────────────────────────
def test_usage_is_tracked_per_model(app, org):
    """Twee modellen in dezelfde maand krijgen elk hun eigen rij."""
    record_usage(org.id, "claude-haiku-4-5", 1_000_000, 0)
    record_usage(org.id, "claude-opus-4-8", 1_000_000, 0)
    rows = {r.model: r for r in db.session.query(AiUsage).filter_by(organization_id=org.id)}
    assert set(rows) == {"claude-haiku-4-5", "claude-opus-4-8"}
    assert month_usage(org) == (2_000_000, 0)  # het totaal telt nog steeds op


def test_each_model_is_priced_at_its_own_rate(app, org):
    """De kern: prijzen verschillen een orde van grootte, dus per model afrekenen.

    Haiku kost $1 per miljoen invoertokens, Opus $5. Eén miljoen van elk hoort dus
    $6 te kosten — niet 2 × de prijs van het model dat toevallig ingesteld staat.
    """
    from app.ai.metering import cost_eur, month_cost_eur

    record_usage(org.id, "claude-haiku-4-5", 1_000_000, 0)
    record_usage(org.id, "claude-opus-4-8", 1_000_000, 0)

    expected = cost_eur("claude-haiku-4-5", 1_000_000, 0) + cost_eur("claude-opus-4-8", 1_000_000, 0)
    assert month_cost_eur(org) == pytest.approx(expected)

    # En het is écht anders dan alles tegen één model afrekenen.
    naive = cost_eur("claude-haiku-4-5", 2_000_000, 0)
    assert month_cost_eur(org) > naive * 2


def test_switching_model_does_not_reprice_past_usage(app, org):
    """Verbruik van vorige week blijft kosten wat het kostte, ook na een modelwissel."""
    from app.ai.metering import month_cost_eur

    record_usage(org.id, "claude-haiku-4-5", 1_000_000, 0)
    before = month_cost_eur(org)

    app.config["LLM_MODEL"] = "claude-opus-4-8"  # duurder model ingesteld
    assert month_cost_eur(org) == pytest.approx(before)


def test_legacy_rows_are_priced_at_the_configured_model(app, org):
    """Rijen van vóór deze splitsing hebben geen model; die worden geprijsd zoals voorheen."""
    from app.ai.metering import _ym, cost_eur, month_cost_eur

    db.session.add(AiUsage(organization_id=org.id, ym=_ym(), model="",
                           input_tokens=1_000_000, output_tokens=0))
    db.session.commit()
    app.config["LLM_MODEL"] = "claude-sonnet-4-6"
    assert month_cost_eur(org) == pytest.approx(cost_eur("claude-sonnet-4-6", 1_000_000, 0))


def test_same_model_still_accumulates_in_one_row(app, org):
    record_usage(org.id, "claude-sonnet-4-6", 100, 10)
    record_usage(org.id, "claude-sonnet-4-6", 50, 5)
    rows = db.session.query(AiUsage).filter_by(organization_id=org.id).all()
    assert len(rows) == 1 and (rows[0].input_tokens, rows[0].output_tokens) == (150, 15)
