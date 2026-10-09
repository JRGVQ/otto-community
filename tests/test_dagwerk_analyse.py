"""Dagelijks werk & Analyse: echte briefingcijfers, levende schakelaars, en de
nummering-schakelaar die zijn effect meteen naar Outlook duwt.

Achtergrond (audit 23-08-2026): de briefing toonde hardgecodeerde demodata, zes van de
zeven Instellingen-schakelaars stuurden niets aan, en de nummering-schakelaar ruimde in
Outlook alleen de oude namen op zonder de nieuwe te zetten — waardoor hij stuk leek.
Deze tests bewaken dat dat zo blijft opgelost.
"""
from __future__ import annotations

import json

import pytest

from app.auth.service import create_company
from app.extensions import db
from app.models import Category, MailAccount, Setting, User


@pytest.fixture()
def owner(app):
    user = create_company("Acme BV", "Owner", "owner@acme-dw.be", "geheim123")
    user.email_verified = True
    user.onboarded = True
    db.session.commit()
    return user


def _login(client, email="owner@acme-dw.be"):
    client.post("/auth/login", data={"email": email, "password": "geheim123"})
    return client


# ── Nummering-schakelaar: effect meteen zichtbaar ────────────────────────────
class _RecordingProvider:
    def __init__(self):
        self.ensured: list[tuple[str, str | None]] = []
        self.removed: list[str] = []

    def is_configured(self):
        return True

    def ensure_master_category(self, name, color=None):
        self.ensured.append((name, color))

    def remove_master_category(self, name):
        self.removed.append(name)


def _outlook_account(owner):
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email=owner.email, status="connected")
    db.session.add(acct)
    db.session.commit()
    return acct


def test_toggle_pushes_the_new_names_to_outlook(app, client, owner, monkeypatch):
    """Aanzetten moet de genummerde namen METEEN in Outlooks lijst zetten — anders is
    het enige zichtbare effect dat de oude namen verdwijnen en lijkt de schakelaar stuk."""
    _outlook_account(owner)
    db.session.add(Category(organization_id=owner.organization_id, key="te_beantwoorden",
                            name="Te beantwoorden", color="#6C4CE6", position=0))
    db.session.add(Category(organization_id=owner.organization_id, key="fyi",
                            name="FYI", color="#2D9CDB", position=1))
    db.session.commit()

    provider = _RecordingProvider()
    monkeypatch.setattr("app.providers.get_provider", lambda a: provider)

    _login(client).post("/api/admin/outlook-numbering/toggle")  # aan
    assert ("1. Te beantwoorden", "#6C4CE6") in provider.ensured
    assert ("2. FYI", "#2D9CDB") in provider.ensured
    assert "Te beantwoorden" in provider.removed  # oude vorm opgeruimd

    provider.ensured.clear()
    provider.removed.clear()
    client.post("/api/admin/outlook-numbering/toggle")  # weer uit
    assert ("Te beantwoorden", "#6C4CE6") in provider.ensured
    assert "1. Te beantwoorden" in provider.removed


def test_orgcats_screen_shows_the_effective_outlook_name(app, client, owner):
    db.session.add(Category(organization_id=owner.organization_id, key="te_beantwoorden",
                            name="Te beantwoorden", color="#6C4CE6", position=0))
    owner.organization.outlook_category_numbering = True
    db.session.commit()
    html = _login(client).get("/admin/orgcats").get_data(as_text=True)
    assert "in Outlook:" in html and "1. Te beantwoorden" in html

    owner.organization.outlook_category_numbering = False
    db.session.commit()
    # De "Bijvoorbeeld: 1. …"-uitleg bij de schakelaar blijft; de effectieve naam
    # op de kaart ("in Outlook: …") verdwijnt zodra de nummering uit staat.
    assert "in Outlook:" not in client.get("/admin/orgcats").get_data(as_text=True)


# ── Instellingen: alleen levende schakelaars ─────────────────────────────────
def test_only_live_settings_are_seeded_and_shown(app, client, owner):
    keys = {s.key for s in owner.settings}
    assert keys == {"categorisatie", "drafts", "klaarzetten"}

    html = _login(client).get("/app/settings").get_data(as_text=True)
    assert "Automatische categorisatie" in html
    assert "VIP-afzenders" not in html
    assert "modeltraining" not in html  # geen dode schakelaar meer …
    assert "Jouw data traint nooit een model" in html  # … maar een vast principe


def test_dead_setting_keys_cannot_be_toggled(app, client, owner):
    # Een oude prototype-rij die nog in de database staat, blijft onschakelbaar.
    db.session.add(Setting(user_id=owner.id, key="vip", label="VIP", description="", on=True))
    db.session.commit()
    r = _login(client).post("/api/settings/vip/toggle")
    assert r.status_code == 404


def test_setting_falls_back_to_its_default_when_the_row_is_missing(app, owner):
    assert owner.setting("bestaat_niet") is False
    assert owner.setting("bestaat_niet", default=True) is True


# ── Schakelaars sturen de pijplijn echt aan ──────────────────────────────────
def _message_for(owner, acct, **kw):
    from app.models import Message

    msg = Message(organization_id=owner.organization_id, user_id=owner.id,
                  account_id=acct.id, provider_message_id=kw.get("pid", "m-dw-1"),
                  sender="Klant", sender_email="klant@voorbeeld.be",
                  subject=kw.get("subject", "Vraag over de offerte"),
                  body="Kunnen jullie iets sturen?")
    db.session.add(msg)
    db.session.commit()
    return msg


def _set(owner, key, on):
    row = next((s for s in owner.settings if s.key == key), None)
    assert row is not None, f"setting {key} hoort geseed te zijn"
    row.on = on
    db.session.commit()


def test_categorisatie_off_leaves_new_mail_unsorted(app, owner):
    from app.tasks.pipeline import process_incoming

    acct = _outlook_account(owner)
    msg = _message_for(owner, acct)
    _set(owner, "categorisatie", False)

    process_incoming(msg.id)
    db.session.refresh(msg)
    assert msg.category_id is None and msg.draft is None


def test_klaarzetten_off_keeps_the_draft_inside_otto(app, owner, monkeypatch):
    """Het concept bestaat gewoon in Otto, maar wordt niet naar de Concepten-map
    van de provider geschreven."""
    from app.tasks import draft as draft_mod

    acct = _outlook_account(owner)
    cat = Category(organization_id=owner.organization_id, key="te_beantwoorden",
                   name="Te beantwoorden", color="#6C4CE6", position=0, draft_enabled=True)
    db.session.add(cat)
    db.session.commit()
    msg = _message_for(owner, acct)
    msg.category_id = cat.id
    db.session.commit()

    written: list[int] = []
    monkeypatch.setattr(draft_mod, "write_draft", lambda d: written.append(d.id))

    _set(owner, "klaarzetten", False)
    draft_mod.generate_draft(msg.id)
    db.session.refresh(msg)
    assert msg.draft is not None and msg.draft.placed is False
    assert written == []

    _set(owner, "klaarzetten", True)
    draft_mod.generate_draft(msg.id, force=True)
    assert written  # nu wél teruggeschreven


# ── Briefing: echte cijfers, geen decor ──────────────────────────────────────
def test_briefing_shows_todays_real_date_and_no_fake_numbers(auth_client):
    from flask_babel import format_date

    from app.models.base import utcnow

    html = auth_client.get("/app/briefing").get_data(as_text=True)
    assert "Maandag 23 juni 2026" not in html  # het hardgecodeerde decor is weg
    assert format_date(utcnow().date(), "EEEE d MMMM y").capitalize() in html
    assert "2u 14m" not in html  # de verzonnen reactietijd
    assert "Otto deed dit vandaag" in html
    assert "quick-task-add" in html  # snelle taak-invoer staat erop


def test_briefing_counts_come_from_the_data(app, client, owner):
    html = _login(client).get("/app/briefing").get_data(as_text=True)
    # Een verse organisatie zonder mail: alles staat op nul in plaats van nepdata.
    assert "Gesorteerd vandaag" in html
    assert "Openstaande opvolgingen" in html


# ── Analyse-bord: widgets, periode en indeling ───────────────────────────────
def test_analyse_renders_all_default_widgets(auth_client):
    html = auth_client.get("/app/analyse").get_data(as_text=True)
    from app.web.context.widgets import WIDGET_KEYS

    for key in WIDGET_KEYS:
        assert f'data-widget="{key}"' in html


def test_analyse_accepts_a_period_and_falls_back_on_junk(auth_client):
    assert auth_client.get("/app/analyse?p=7").status_code == 200
    assert auth_client.get("/app/analyse?p=alles").status_code == 200
    assert auth_client.get("/app/analyse?p=onzin").status_code == 200


def test_layout_can_be_saved_and_changes_the_order(app, auth_client):
    from app.web.context.widgets import WIDGET_KEYS

    reordered = list(WIDGET_KEYS[::-1])
    r = auth_client.post("/api/analytics/layout",
                         json={"order": reordered, "hidden": []})
    assert r.status_code == 200 and r.get_json()["order"] == reordered

    user = db.session.query(User).filter_by(email="demo@example.com").first()
    assert json.loads(user.analytics_layout)["order"] == reordered

    html = auth_client.get("/app/analyse").get_data(as_text=True)
    assert html.index(f'data-widget="{reordered[0]}"') < html.index(f'data-widget="{reordered[-1]}"')


def test_hidden_widgets_leave_the_board_but_stay_reachable(auth_client):
    from app.web.context.widgets import WIDGET_KEYS

    order = [k for k in WIDGET_KEYS if k != "drukste_uren"]
    r = auth_client.post("/api/analytics/layout",
                         json={"order": order, "hidden": ["drukste_uren"]})
    assert r.status_code == 200

    html = auth_client.get("/app/analyse").get_data(as_text=True)
    assert 'data-widget="drukste_uren"' not in html.split("Verborgen:")[0]
    assert 'data-action="widget-show" data-widget="drukste_uren"' in html


def test_layout_ignores_unknown_keys_and_broken_json_falls_back(app, auth_client):
    r = auth_client.post("/api/analytics/layout",
                         json={"order": ["kerncijfers", "bestaat_niet"], "hidden": [42]})
    data = r.get_json()
    assert "bestaat_niet" not in data["order"] and data["hidden"] == []

    # Kapotte JSON in de database → gewoon de standaardindeling, geen 500.
    user = db.session.query(User).filter_by(email="demo@example.com").first()
    user.analytics_layout = "{dit is geen json"
    db.session.commit()
    assert auth_client.get("/app/analyse").status_code == 200


def test_new_widgets_join_a_saved_layout_automatically(app, demo_user):
    """Een indeling die vóór een nieuwe widget is opgeslagen mag die widget niet
    voorgoed kwijt zijn: onbekende (nieuwe) keys sluiten achteraan aan."""
    from app.web.context.widgets import WIDGET_KEYS, _layout_for

    demo_user.analytics_layout = json.dumps({"order": ["kerncijfers"], "hidden": []})
    db.session.commit()
    order, hidden = _layout_for(demo_user)
    assert order[0] == "kerncijfers" and set(order) == set(WIDGET_KEYS) and hidden == []


# ── Ruis-rapport en AI-verbruik op het bord ──────────────────────────────────
def test_ruis_widget_hints_when_there_is_no_noise_category(app, client, owner):
    html = _login(client).get("/app/analyse").get_data(as_text=True)
    assert 'data-widget="ruis_rapport"' in html
    assert "nog geen ruiscategorie" in html


def test_ruis_widget_counts_filtered_mail_and_top_senders(app, client, owner):
    acct = _outlook_account(owner)
    ruis = Category(organization_id=owner.organization_id, key="ruis", name="Nieuwsbrieven",
                    color="#F2994A", position=0, is_noise=True)
    db.session.add(ruis)
    db.session.commit()
    for i in range(3):
        msg = _message_for(owner, acct, pid=f"m-ruis-{i}", subject=f"Nieuwsbrief {i}")
        msg.sender_email = "news@shop.be" if i < 2 else "info@krant.be"
        msg.category_id = ruis.id
        msg.received_at = msg.received_at or None
        db.session.commit()

    html = _login(client).get("/app/analyse?p=alles").get_data(as_text=True)
    assert "mails weggevangen" in html
    assert "shop.be" in html  # drukste ruisafzender


def test_ai_widget_is_only_for_ai_managers(app, client, owner):
    from app.tenancy import org_role

    # De eigenaar (manageAI) ziet de widget …
    html = _login(client).get("/app/analyse").get_data(as_text=True)
    assert 'data-widget="ai_verbruik"' in html

    # … een gewone gebruiker niet — ook niet als chip wanneer hij "verborgen" zou zijn.
    member = User(name="Lid Gewoon", email="lid@acme-dw.be", organization_id=owner.organization_id,
                  role_id=org_role(owner.organization, "Gebruiker").id,
                  email_verified=True, onboarded=True, initials="LG")
    member.set_password("geheim123")
    member.analytics_layout = json.dumps({"order": [], "hidden": ["ai_verbruik"]})
    db.session.add(member)
    db.session.commit()

    client.get("/auth/logout")
    c2 = _login(client, email="lid@acme-dw.be")
    html = c2.get("/app/analyse").get_data(as_text=True)
    assert 'data-widget="ai_verbruik"' not in html
    assert 'data-widget="ruis_rapport"' in html  # de rest van het bord gewoon wel
    assert 'data-action="widget-show" data-widget="ai_verbruik"' not in html


def test_ai_widget_shows_this_months_cost_per_model(app, client, owner):
    from app.ai.metering import record_usage

    app.config["LLM_MONTHLY_BUDGET_EUR"] = 25  # met een plafond toont de kaart ook het budget
    record_usage(owner.organization_id, "claude-haiku-4-5", 1_000_000, 0)  # $1 ≈ €0.95
    html = _login(client).get("/app/analyse").get_data(as_text=True)
    assert "claude-haiku-4-5" in html
    assert "1.000.000" in html and "tokens" in html
    assert "maandbudget" in html


# ── classified_by: exact tellen wie de categorie zette ───────────────────────
def test_noise_filter_stamps_classified_by_filter(app, owner):
    from app.models import StyleProfile
    from app.tasks.categorize import categorize_message

    acct = _outlook_account(owner)
    if owner.style_profile is None:
        db.session.add(StyleProfile(user_id=owner.id))
    owner.style_profile.noise_filter_enabled = True
    ruis = Category(organization_id=owner.organization_id, key="ruis", name="Ruis",
                    is_noise=True, position=99)
    db.session.add(ruis)
    db.session.commit()

    msg = _message_for(owner, acct, pid="m-cb-filter", subject="Aanbiedingen!")
    msg.sender_email = "news@shop.be"
    msg.unsubscribe_url = "https://shop.be/unsub"
    db.session.commit()

    categorize_message(msg.id)
    db.session.refresh(msg)
    assert msg.category_id == ruis.id and msg.classified_by == "filter"


def test_ai_classification_stamps_classified_by_ai(app, owner):
    from app.tasks.categorize import categorize_message

    acct = _outlook_account(owner)
    db.session.add(Category(organization_id=owner.organization_id, key="te_beantwoorden",
                            name="Te beantwoorden", position=0))
    db.session.commit()
    msg = _message_for(owner, acct, pid="m-cb-ai")

    categorize_message(msg.id)
    db.session.refresh(msg)
    assert msg.category_id is not None and msg.classified_by == "ai"


def test_manual_recategorize_stamps_classified_by_user(app, client, owner):
    acct = _outlook_account(owner)
    # Unieke key: create_company provisioneert al standaardcategorieën (o.a. "fyi").
    cat = Category(organization_id=owner.organization_id, key="cb_test", name="CB-test", position=42)
    db.session.add(cat)
    db.session.commit()
    msg = _message_for(owner, acct, pid="m-cb-user")

    r = _login(client).post(f"/api/messages/{msg.id}/recategorize", json={"category": "cb_test"})
    assert r.status_code == 200
    db.session.refresh(msg)
    assert msg.category_id == cat.id and msg.classified_by == "user"


def test_ruis_widget_counts_only_filtered_mail_as_saved_ai_calls(app, client, owner):
    """Handmatig of door de AI in ruis gelegde mail is géén bespaarde AI-aanroep —
    liever een te laag cijfer dan een verzonnen besparing."""
    acct = _outlook_account(owner)
    ruis = Category(organization_id=owner.organization_id, key="ruis", name="Ruis",
                    is_noise=True, position=99)
    db.session.add(ruis)
    db.session.commit()

    by_filter = _message_for(owner, acct, pid="m-sv-1")
    by_filter.category_id, by_filter.classified_by = ruis.id, "filter"
    by_ai = _message_for(owner, acct, pid="m-sv-2")
    by_ai.category_id, by_ai.classified_by = ruis.id, "ai"
    legacy = _message_for(owner, acct, pid="m-sv-3")
    legacy.category_id = ruis.id  # NULL classified_by — rij van vóór de kolom
    db.session.commit()

    from app.web.context.widgets import _build_ruis_rapport

    with app.test_request_context():
        from flask_login import login_user

        login_user(owner)
        data = _build_ruis_rapport(
            db.session.query(type(by_filter)).filter_by(account_id=acct.id).all(), None,
            [ruis])
    assert data["count"] == 3 and data["zonder_ai"] == 1


# ── Review-fixes: gates zonder gaten ─────────────────────────────────────────
class _SendRecordingProvider:
    """Provider-fake voor het verzendpad: registreert plaatsen én versturen."""

    def __init__(self):
        self.created: list[str] = []
        self.sent: list[str] = []
        self.deleted: list[str] = []

    def is_configured(self):
        return True

    def create_draft(self, thread_id, subject, body, in_reply_to=None, **kw):
        self.created.append(body)
        return f"prov-{len(self.created)}"

    def delete_draft(self, draft_id):
        self.deleted.append(draft_id)

    def send_user_draft(self, draft_id):
        self.sent.append(draft_id)


def _draft_message(owner, acct, *, pid="m-send-1"):
    from app.models import Draft

    cat = Category(organization_id=owner.organization_id, key=f"cat-{pid}", name="Te beantwoorden",
                   position=0, draft_enabled=True)
    db.session.add(cat)
    db.session.commit()
    msg = _message_for(owner, acct, pid=pid)
    msg.category_id = cat.id
    draft = Draft(message_id=msg.id, status="concept", current_text="Dag, bij deze het antwoord.")
    db.session.add(draft)
    db.session.commit()
    return msg, draft


def test_send_with_klaarzetten_off_places_the_draft_and_really_sends(app, client, owner, monkeypatch):
    """Zonder deze fix markeerde Verzenden het concept als verzonden zonder dat er
    ooit mail vertrok (provider_draft_id was None door de klaarzetten-gate)."""
    acct = _outlook_account(owner)
    msg, draft = _draft_message(owner, acct)
    _set(owner, "klaarzetten", False)
    assert draft.provider_draft_id is None

    provider = _SendRecordingProvider()
    monkeypatch.setattr("app.providers.get_provider", lambda a: provider)
    monkeypatch.setattr("app.tasks.writeback.get_provider", lambda a: provider)

    r = _login(client).post(f"/api/drafts/{msg.id}/send")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert provider.created, "het concept hoort alsnog geplaatst te worden"
    assert provider.sent == ["prov-1"], "en daarna écht verzonden"
    db.session.refresh(draft)
    assert draft.status == "verzonden" and draft.provider_draft_id == "prov-1"


def test_regenerate_with_klaarzetten_off_cleans_the_stale_provider_draft(app, owner, monkeypatch):
    """Anders bleef het oude concept in Outlook staan en verstuurde de Verzenden-knop
    (die op provider_draft_id rijdt) een verouderde tekst."""
    from app.tasks import draft as draft_mod

    acct = _outlook_account(owner)
    msg, draft = _draft_message(owner, acct, pid="m-stale-1")
    draft.provider_draft_id = "prov-oud"
    db.session.commit()

    provider = _SendRecordingProvider()
    monkeypatch.setattr("app.tasks.writeback.get_provider", lambda a: provider)

    _set(owner, "klaarzetten", False)
    draft_mod.generate_draft(msg.id, force=True)
    db.session.refresh(draft)
    assert draft.provider_draft_id is None
    assert "prov-oud" in provider.deleted
    assert provider.created == []  # en er is géén nieuw concept geplaatst


def test_reenabling_categorisation_processes_the_backlog(app, client, owner, monkeypatch):
    """Mail die binnenkwam terwijl de schakelaar uit stond wordt bij het weer
    aanzetten alsnog opgepakt — de sync enqueuet elk bericht maar één keer."""
    acct = _outlook_account(owner)
    msg = _message_for(owner, acct, pid="m-backlog-1")
    assert msg.category_id is None

    picked_up: list[int] = []
    monkeypatch.setattr("app.tasks.enqueue.enqueue", lambda fn, *a: picked_up.append(a[0]))

    c = _login(client)
    c.post("/api/settings/categorisatie/toggle")  # uit
    assert picked_up == []
    c.post("/api/settings/categorisatie/toggle")  # weer aan → inhaalslag
    assert msg.id in picked_up


def test_rejected_setting_toggle_says_ok_false(app, client, owner):
    """`ok(error=...), 404` stuurde {"ok": true} — de switch klapte dan visueel om
    terwijl er niets was opgeslagen."""
    r = _login(client).post("/api/settings/vip/toggle")
    assert r.status_code == 404 and r.get_json()["ok"] is False


def test_deleting_a_category_clears_the_classification_stamp(app, client, owner):
    acct = _outlook_account(owner)
    weg = Category(organization_id=owner.organization_id, key="weg", name="Weg", position=5)
    blijft = Category(organization_id=owner.organization_id, key="blijft", name="Blijft",
                      position=0, is_noise=True)
    db.session.add_all([weg, blijft])
    db.session.commit()
    msg = _message_for(owner, acct, pid="m-delcat-1")
    msg.category_id, msg.classified_by = weg.id, "filter"
    db.session.commit()

    _login(client).delete(f"/api/categories/{weg.id}")
    db.session.refresh(msg)
    # Fallback-verhuizing is geen filter-actie: de stempel gaat naar "onbekend",
    # anders telt het ruis-rapport een besparing die nooit heeft plaatsgevonden.
    # (De fallback is de eerste overgebleven categorie — welke maakt hier niet uit.)
    assert msg.category_id is not None and msg.category_id != weg.id
    assert msg.classified_by is None


def test_numbering_toggle_prefers_the_bulk_sync(app, client, owner, monkeypatch):
    """Eén lijst-GET in plaats van een Graph-rondreis per naam per mailbox."""
    _outlook_account(owner)
    db.session.add(Category(organization_id=owner.organization_id, key="te_beantwoorden",
                            name="Te beantwoorden", color="#6C4CE6", position=0))
    db.session.commit()

    calls: list[tuple] = []

    class _BulkProvider(_RecordingProvider):
        def sync_master_categories(self, wanted, obsolete):
            calls.append((tuple(wanted), tuple(obsolete)))

    provider = _BulkProvider()
    monkeypatch.setattr("app.providers.get_provider", lambda a: provider)
    monkeypatch.setattr("app.tasks.retag.retag_org_labels", lambda org, variants: None)

    _login(client).post("/api/admin/outlook-numbering/toggle")
    assert len(calls) == 1
    assert ("1. Te beantwoorden", "#6C4CE6") in calls[0][0]
    assert provider.ensured == [] and provider.removed == []  # geen per-naam terugval


def test_save_layout_ignores_widgets_the_user_cannot_see(app, client, owner):
    from app.tenancy import org_role
    from app.web.context.widgets import _layout_for

    member = User(name="Lid Twee", email="lid2@acme-dw.be", organization_id=owner.organization_id,
                  role_id=org_role(owner.organization, "Gebruiker").id,
                  email_verified=True, onboarded=True, initials="LT")
    member.set_password("geheim123")
    db.session.add(member)
    db.session.commit()

    c = _login(client, email="lid2@acme-dw.be")
    r = c.post("/api/analytics/layout",
               json={"order": ["ai_verbruik", "kerncijfers"], "hidden": ["ruis_rapport"]})
    data = r.get_json()
    assert "ai_verbruik" not in data["order"]  # bestaat niet voor deze gebruiker
    assert "ruis_rapport" in data["hidden"]
    order, hidden = _layout_for(member)
    assert "ai_verbruik" not in json.loads(member.analytics_layout)["order"]


def test_briefing_created_today_does_not_shrink_when_tasks_are_finished(app, client, owner):
    from flask_login import login_user

    from app.models import Task
    from app.web.context.inbox import briefing_context

    t1 = Task(user_id=owner.id, organization_id=owner.organization_id, title="Eén")
    t2 = Task(user_id=owner.id, organization_id=owner.organization_id, title="Twee", done=True)
    db.session.add_all([t1, t2])
    db.session.commit()

    with app.test_request_context():
        login_user(owner)
        ctx = briefing_context()
    created = next(i for i in ctx["otto_today"] if str(i["label"]) == "taken aangemaakt")
    assert created["value"] == 2  # de afgevinkte taak telt gewoon mee


def test_volume_trend_counts_exactly_what_the_period_contains(app, owner):
    """De periode-filter en de dagstaafjes delen dezelfde daggrens — mail uit de
    'gedeeltelijke' eerste dag verdween eerder wel uit de grafiek maar niet uit de
    kerncijfers."""
    from datetime import datetime, time, timedelta

    from app.models.base import utcnow
    from app.web.context.widgets import _build_volume_trend, _in_period

    acct = _outlook_account(owner)
    oud = _message_for(owner, acct, pid="m-rand-1")
    # Vroeg op de eerste dag van het 7-daagse venster — vóór het huidige tijdstip.
    oud.received_at = datetime.combine(utcnow().date() - timedelta(days=6), time(0, 30))
    vandaag = _message_for(owner, acct, pid="m-rand-2")
    vandaag.received_at = utcnow()
    db.session.commit()

    msgs = _in_period([oud, vandaag], 7)
    assert len(msgs) == 2
    trend = _build_volume_trend(msgs, 7, [])
    assert sum(b["count"] for b in trend["buckets"]) == 2
