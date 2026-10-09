"""Handtekening-builder + HTML-inbox-weergave.

Bewaakte beloftes:
1. placeholders worden per gebruiker ingevuld (en in HTML-context ge-escaped);
2. de volgorde: toewijzing aan gebruiker > toewijzing aan M365-groep > verzenddomein >
   persoonlijk > organisatie-standaard — en een toegewezen handtekening lekt nooit meer
   mee als domein/standaard-kandidaat;
3. een rijke (HTML) handtekening gaat NIET in de AI-prompt maar wordt bij de write-back
   als HTML aan de provider gegeven — deterministisch, wat het model ook schreef;
4. de inbox-HTML-weergave sanitized vijandige mail (scripts/handlers/javascript-links
   eruit, cid:-plaatjes herschreven naar Otto's eigen route) en degradeert zonder nh3
   naar de tekstweergave;
5. de beheerroutes: opslaan met HTML + toewijzingen, en een toewijzing verhuist mee
   naar de laatst gekozen handtekening.
"""
from __future__ import annotations

import pytest

from app.auth.service import create_company
from app.extensions import db
from app.models import MailAccount, Signature, SignatureAssignment
from app.signatures import render_placeholders, resolve, resolve_rich
from app.tasks.draft import prompt_signature_for


@pytest.fixture()
def owner(app):
    user = create_company("Acme BV", "An De Tester", "an@acme-htsig.be", "geheim123")
    user.email_verified = True
    user.onboarded = True
    user.job_title = "Office manager"
    user.phone = "+32 470 00 00 00"
    db.session.commit()
    return user


def _account(owner, email="an@acme-htsig.be", groups=None):
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email=email, status="connected",
                       mailbox_type="personal", graph_group_ids=groups or [])
    db.session.add(acct)
    db.session.commit()
    return acct


def _signature(owner, *, name="Sig", domain=None, body="Groeten", html=None):
    sig = Signature(organization_id=owner.organization_id, domain=domain,
                    name=name, body=body, html=html)
    db.session.add(sig)
    db.session.commit()
    return sig


def _assign(owner, sig, kind, value, label=None):
    db.session.add(SignatureAssignment(organization_id=owner.organization_id,
                                       signature_id=sig.id, kind=kind, value=value, label=label))
    db.session.commit()


# ── Placeholders ─────────────────────────────────────────────────────────────
def test_placeholders_filled_per_user(app, owner):
    out = render_placeholders("{{naam}} — {{functie}} — {{telefoon}} — {{bedrijf}}", owner)
    assert out == "An De Tester — Office manager — +32 470 00 00 00 — Acme BV"


def test_placeholders_escape_in_html_context(app, owner):
    owner.job_title = '<img src=x onerror="p0wn()">'
    db.session.commit()
    out = render_placeholders("<div>{{functie}}</div>", owner, html=True)
    assert "<img" not in out and "&lt;img" in out


def test_unknown_placeholder_becomes_empty(app, owner):
    assert render_placeholders("A{{bestaatniet}}B", owner) == "AB"


# ── Volgorde ─────────────────────────────────────────────────────────────────
def test_user_assignment_beats_domain_signature(app, owner):
    acct = _account(owner)
    _signature(owner, name="Domein", domain="acme-htsig.be", body="Domein-groet")
    personal = _signature(owner, name="Persoonlijk toegewezen", body="Toegewezen-groet")
    _assign(owner, personal, "user", str(owner.id))
    assert resolve(owner, account=acct) == "Toegewezen-groet"


def test_group_assignment_matches_cached_group_ids(app, owner):
    acct = _account(owner, groups=["guid-sales"])
    _signature(owner, name="Domein", domain="acme-htsig.be", body="Domein-groet")
    sales = _signature(owner, name="Sales", body="Sales-groet")
    _assign(owner, sales, "group", "guid-sales", "Sales NL")
    assert resolve(owner, account=acct) == "Sales-groet"

    # Zonder groepslidmaatschap valt hij terug op het domein.
    other = _account(owner, email="an2@acme-htsig.be", groups=[])
    assert resolve(owner, account=other) == "Domein-groet"


def test_assigned_signature_never_acts_as_org_default(app, owner):
    """Een domeinloze handtekening MET toewijzing is geen standaard meer: een collega
    zonder toewijzing krijgt de gegenereerde fallback, niet andermans handtekening."""
    acct = _account(owner)
    special = _signature(owner, name="Special", body="Special-groet")
    _assign(owner, special, "user", str(owner.id + 999))  # iemand anders
    assert resolve(owner, account=acct) == "Groeten,\nAn"


# ── Rijke handtekening: prompt vs write-back ─────────────────────────────────
def test_html_signature_leaves_the_prompt_and_reaches_writeback(app, owner):
    acct = _account(owner)
    _signature(owner, name="Huisstijl", domain="acme-htsig.be",
               body="Met vriendelijke groeten,\n{{naam}}",
               html="<b>{{naam}}</b><i>{{functie}}</i>")

    # 1. De prompt krijgt géén handtekening (None = instructie 'zonder handtekening').
    assert prompt_signature_for(owner, account=acct) is None
    rich = resolve_rich(owner, account=acct)
    assert rich.html == "<b>An De Tester</b><i>Office manager</i>"
    assert rich.text.startswith("Met vriendelijke groeten")

    # 2. write_draft geeft de HTML aan de provider mee.
    from app.constants import DRAFT_CONCEPT
    from app.models import Draft, Message
    from app.tasks.writeback import write_draft

    msg = Message(organization_id=owner.organization_id, user_id=owner.id,
                  account_id=acct.id, sender="Klant", sender_email="k@x.be",
                  subject="Vraag", provider_message_id="PM1", provider_thread_id="CT1")
    db.session.add(msg)
    db.session.flush()
    draft = Draft(message_id=msg.id, status=DRAFT_CONCEPT, current_text="Dag klant")
    db.session.add(draft)
    db.session.commit()

    seen = {}

    class FakeProvider:
        def is_configured(self):
            return True

        def create_draft(self, thread_id, subject, body, in_reply_to=None, references=None,
                         signature_html=None, signature_text=None):
            seen.update(body=body, signature_html=signature_html, signature_text=signature_text)
            return "PD1"

    import app.tasks.writeback as wb

    orig = wb.get_provider
    wb.get_provider = lambda a: FakeProvider()
    try:
        assert write_draft(draft) == "PD1"
    finally:
        wb.get_provider = orig
    assert seen["signature_html"] == "<b>An De Tester</b><i>Office manager</i>"
    assert "An De Tester" in seen["signature_text"]
    assert draft.placed


def test_plain_signature_keeps_the_old_prompt_path(app, owner):
    acct = _account(owner)
    _signature(owner, name="Plat", domain="acme-htsig.be", body="Gewoon plat")
    assert prompt_signature_for(owner, account=acct) == "Gewoon plat"


def test_outlook_create_draft_places_signature_between_reply_and_quote(app):
    """De HTML-handtekening komt onder de conceptekst en vóór het geciteerde origineel."""
    from app.providers.outlook import OutlookProvider

    acct = MailAccount(provider="outlook", email="x@y.be")
    p = OutlookProvider(acct)
    calls = []
    p._post = lambda path, payload, extra_headers=None: (calls.append(("POST", path, payload)) or {"id": "D1", "body": {"content": "<div>QUOTE</div>"}})
    p._patch = lambda path, payload, extra_headers=None: (calls.append(("PATCH", path, payload)) or {})
    p._get = lambda path: {}

    p.create_draft(thread_id="T", subject="RE: x", body="Dag\nKlant",
                   in_reply_to="MSG1", signature_html="<b>SIG</b>")
    patch = next(c for c in calls if c[0] == "PATCH")
    content = patch[2]["body"]["content"]
    assert content.index("Dag<br>Klant") < content.index("<b>SIG</b>") < content.index("QUOTE")


# ── Prompt-instructie ────────────────────────────────────────────────────────
def test_prompt_without_signature_forbids_a_signature_block(app):
    from app.ai.prompts import draft_user

    with_sig = draft_user("K", "S", "B", "Groeten,\nX")
    without = draft_user("K", "S", "B", None)
    assert "Groeten,\nX" in with_sig
    assert "ZONDER naam of handtekeningblok" in without


# ── HTML-weergave ────────────────────────────────────────────────────────────
def test_sanitizer_strips_hostile_html_and_rewrites_cid(app):
    from app.htmlview import sanitize_email_html

    raw = ('<script>alert(1)</script><p onclick="x()" style="color:blue">Hoi</p>'
           '<img src="cid:logo1"><a href="javascript:x()">b</a><a href="https://ok.be">ok</a>')
    out = sanitize_email_html(raw, cid_url="/app/messages/7/inline/__CID__")
    assert "alert(1)" not in out and "onclick" not in out and "javascript:" not in out
    assert 'src="/app/messages/7/inline/logo1"' in out
    assert 'href="https://ok.be"' in out and 'style="color:blue"' in out


def test_htmlview_degrades_without_nh3(app, monkeypatch):
    import app.htmlview as hv

    monkeypatch.setattr(hv, "_nh3", lambda: None)
    assert hv.sanitize_email_html("<p>x</p>") is None
    assert hv.html_view_available() is False


def test_sync_persists_body_html(app, owner):
    from app.providers.dto import MessageDTO
    from app.tasks.sync import _persist

    acct = _account(owner)
    dto = MessageDTO(provider_message_id="HM1", thread_id="HT1", sender="K",
                     sender_email="k@x.be", subject="S", snippet="sn",
                     body="platte tekst", body_html="<p>rijk</p>")
    msg = _persist(acct, dto)
    db.session.commit()
    assert msg.body_html == "<p>rijk</p>" and msg.body == "platte tekst"


def test_thread_html_map_wraps_sanitized_documents(app, owner):
    from app.models import Message
    from app.web.context.inbox import _thread_html_map

    acct = _account(owner)
    msg = Message(organization_id=owner.organization_id, user_id=owner.id,
                  account_id=acct.id, sender="K", sender_email="k@x.be", subject="S",
                  body="tekst", body_html="<p>Hallo <script>x()</script></p>",
                  provider_message_id="TM1")
    db.session.add(msg)
    db.session.commit()

    with app.test_request_context():
        out = _thread_html_map([msg])
    assert msg.id in out
    doc = out[msg.id]
    assert doc.startswith("<!doctype html>") and "Hallo" in doc and "x()" not in doc


# ── Beheerroutes ─────────────────────────────────────────────────────────────
def _login(client, email, password="geheim123"):
    return client.post("/auth/login", data={"email": email, "password": password},
                       follow_redirects=True)


def test_admin_saves_html_signature_with_assignments(app, client, owner):
    _login(client, owner.email)
    resp = client.post("/admin/signatures/save", data={
        "name": "Huisstijl", "domain": "", "body": "",
        "html": "<b>{{naam}}</b>",
        "user_ids": [str(owner.id)],
        "group_ids": ["guid-1|Sales NL"],
    }, follow_redirects=True)
    assert resp.status_code == 200

    sig = db.session.query(Signature).filter_by(name="Huisstijl").one()
    assert sig.html == "<b>{{naam}}</b>"
    assert sig.body  # tekstversie afgeleid uit de HTML
    kinds = {(a.kind, a.value) for a in sig.assignments}
    assert kinds == {("user", str(owner.id)), ("group", "guid-1")}
    group = next(a for a in sig.assignments if a.kind == "group")
    assert group.label == "Sales NL"


def test_reassigning_moves_the_user_to_the_new_signature(app, client, owner):
    _login(client, owner.email)
    client.post("/admin/signatures/save", data={
        "name": "Eerste", "html": "<b>1</b>", "body": "een",
        "user_ids": [str(owner.id)],
    })
    client.post("/admin/signatures/save", data={
        "name": "Tweede", "html": "<b>2</b>", "body": "twee",
        "user_ids": [str(owner.id)],
    })
    rows = db.session.query(SignatureAssignment).filter_by(
        organization_id=owner.organization_id, kind="user", value=str(owner.id)).all()
    assert len(rows) == 1
    assert rows[0].signature.name == "Tweede"


def test_signature_test_mail_without_mailer_flashes_an_error(app, client, owner):
    _login(client, owner.email)
    sig = _signature(owner, name="Test", body="Groeten, {{naam}}")
    resp = client.post(f"/admin/signatures/{sig.id}/test", follow_redirects=True)
    assert resp.status_code == 200  # geen mailer in tests → nette melding, geen crash


# ── Volledig profiel (afdeling/locatie/mobiel) ───────────────────────────────
def test_new_profile_placeholders(app, owner):
    owner.department = "Operations"
    owner.office_location = "Gent"
    owner.mobile = "+32 499 11 22 33"
    db.session.commit()
    out = render_placeholders("{{afdeling}} | {{locatie}} | {{mobiel}}", owner)
    assert out == "Operations | Gent | +32 499 11 22 33"


def test_empty_profile_field_removes_its_html_element(app, owner):
    """Een leeg profielveld laat geen kaal icoontje achter: het hele element met
    data-ph verdwijnt; gevulde velden blijven gewoon staan."""
    owner.phone = None
    owner.mobile = "+32 499 11 22 33"
    db.session.commit()
    tpl = ('<span data-ph="telefoon">📞 {{telefoon}}</span>'
           '<span data-ph="mobiel">📱 {{mobiel}}</span>')
    out = render_placeholders(tpl, owner, html=True)
    assert "📞" not in out and "telefoon" not in out
    assert "📱 +32 499 11 22 33" in out


def test_empty_placeholder_line_disappears_in_text_but_divider_stays(app, owner):
    owner.phone = None
    owner.mobile = None
    db.session.commit()
    out = render_placeholders("An\n{{telefoon}} · {{mobiel}}\n---\nAcme", owner)
    assert out == "An\n---\nAcme"


def test_save_profile_route_updates_all_fields(app, client, owner):
    _login(client, owner.email)
    resp = client.post("/app/settings/profiel", data={
        "job_title": "CTO", "department": "Engineering", "office_location": "Gent",
        "phone": "+32 9 111 11 11", "mobile": "+32 499 00 00 00",
    }, follow_redirects=True)
    assert resp.status_code == 200
    db.session.refresh(owner)
    assert (owner.job_title, owner.department, owner.office_location,
            owner.phone, owner.mobile) == ("CTO", "Engineering", "Gent",
                                           "+32 9 111 11 11", "+32 499 00 00 00")


# ── Samengevoegde navbar ─────────────────────────────────────────────────────
def test_topbar_has_two_dropdown_menus_per_workspace(app, client, owner):
    _login(client, owner.email)
    page = client.get("/app/inbox").get_data(as_text=True)
    assert page.count("data-topnav-drop") == 3  # Werk + Instellen + Teams-knop
    assert "topnav" not in page or "tb-nav" in page  # geen tweede balk meer

    admin = client.get("/admin/overzicht").get_data(as_text=True)
    assert admin.count("data-topnav-drop") == 2  # Organisatie + Instellingen
    assert "tn-menu-head" in admin  # categorie-koppen in het menu
