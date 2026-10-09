"""Bedrijfskennis die de klant zelf invoert — de bron die géén Microsoft 365 vereist.

De grens die deze tests vooral bewaken: kennis van de ene organisatie of van één
gedeelde mailbox mag nooit in het concept van een andere terechtkomen, en niets in
deze keten mag een concept kunnen laten mislukken.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app import knowledge_base as kb
from app.auth.service import create_company
from app.extensions import db
from app.models import KnowledgeEntry, MailAccount, MailboxMember, Role, User
from app.models.user import initials_for


@pytest.fixture()
def owner(app):
    user = create_company("Acme BV", "Owner", "owner@acme-kb.be", "geheim123")
    user.email_verified = True
    user.onboarded = True
    db.session.commit()
    return user


def _shared(owner, email="support@acme-kb.be"):
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id, provider="outlook",
                       email=email, status="connected", mailbox_type="shared")
    db.session.add(acct)
    db.session.flush()
    db.session.add(MailboxMember(mail_account_id=acct.id, user_id=owner.id, role="agent"))
    db.session.commit()
    return acct


def _docx(paragraphs: list[str]) -> bytes:
    """Een minimaal geldig .docx: een zip met word/document.xml erin."""
    runs = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="x"><w:body>{runs}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return buf.getvalue()


# ── Matchen ──────────────────────────────────────────────────────────────────
def test_relevant_block_is_found_and_irrelevant_one_is_not(app, owner):
    org = owner.organization
    kb.create(org, title="Leveringstermijnen", body="Standaard leveren we binnen 5 werkdagen.")
    kb.create(org, title="Vacatures", body="We zoeken een boekhouder met ervaring.")

    hits = kb.search(org, "Vraag over levering", "Wanneer wordt mijn bestelling geleverd?")
    assert [h["title"] for h in hits] == ["Leveringstermijnen"]


def test_title_outweighs_a_passing_mention_in_the_body(app, owner):
    org = owner.organization
    kb.create(org, title="Garantie", body="Op elk toestel geldt twee jaar garantie.")
    kb.create(org, title="Algemene info", body="Wij zijn een familiebedrijf. Garantie regelen we netjes.")

    hits = kb.search(org, "Garantie op mijn toestel", "Hoelang loopt de garantie?", top_n=2)
    assert hits[0]["title"] == "Garantie"


def test_a_mail_about_nothing_in_the_base_grounds_nothing(app, owner):
    org = owner.organization
    kb.create(org, title="Leveringstermijnen", body="Standaard leveren we binnen 5 werkdagen.")
    assert kb.search(org, "Uitnodiging teambuilding", "Kom je zaterdag mee kajakken?") == []


def test_snippet_is_capped_and_shows_the_relevant_window(app, owner):
    org = owner.organization
    body = ("inleiding " * 200) + "de garantietermijn bedraagt vierentwintig maanden " + ("slot " * 200)
    kb.create(org, title="Voorwaarden", body=body)

    hits = kb.search(org, "garantietermijn", "Hoelang is de garantietermijn?")
    assert len(hits) == 1
    snippet = hits[0]["snippet"]
    assert len(snippet) <= kb.SNIPPET_MAX + 2          # plus de ellipsis-tekens
    assert "vierentwintig maanden" in snippet


def test_snippet_is_stable_across_runs(app, owner):
    """Zelfde mail, zelfde fragment — anders is niet uit te leggen waaróm de AI iets zei."""
    org = owner.organization
    kb.create(org, title="Voorwaarden", body="x " * 300 + "levering binnen 5 werkdagen na betaling " + "y " * 300)
    first = kb.search(org, "levering en betaling", "Wanneer levering na betaling?")[0]["snippet"]
    for _ in range(5):
        assert kb.search(org, "levering en betaling", "Wanneer levering na betaling?")[0]["snippet"] == first


def test_usage_is_counted_so_you_can_see_what_earns_its_place(app, owner):
    org = owner.organization
    entry = kb.create(org, title="Leveringstermijnen", body="Binnen 5 werkdagen geleverd.")
    kb.search(org, "levering", "Wanneer wordt er geleverd?")
    kb.search(org, "levering", "Wanneer wordt er geleverd?")
    assert db.session.get(KnowledgeEntry, entry.id).used_count == 2


# ── Scoping: de grens tussen mailboxen en organisaties ───────────────────────
def test_mailbox_knowledge_stays_out_of_a_personal_draft(app, owner):
    org = owner.organization
    support = _shared(owner)
    kb.create(org, title="Retourbeleid", body="Retour binnen 14 dagen.", account=support)

    assert kb.search(org, "retour", "Kan ik dit retourneren?") == []                       # persoonlijk
    assert kb.search(org, "retour", "Kan ik dit retourneren?", account=support)            # in de mailbox


def test_one_shared_mailbox_never_sees_anothers_knowledge(app, owner):
    org = owner.organization
    support = _shared(owner, "support@acme-kb.be")
    sales = _shared(owner, "sales@acme-kb.be")
    kb.create(org, title="Retourbeleid", body="Retour binnen 14 dagen.", account=support)

    assert [h["title"] for h in kb.search(org, "retour", "retour?", account=support)] == ["Retourbeleid"]
    assert kb.search(org, "retour", "retour?", account=sales) == []


def test_knowledge_never_crosses_organizations(app, owner):
    other = create_company("Concurrent BV", "O", "owner@concurrent-kb.be", "geheim123").organization
    kb.create(other, title="Prijzen", body="Onze uurprijs is 95 euro.")

    assert kb.search(owner.organization, "prijzen", "Wat is jullie uurprijs?") == []


def test_disabled_block_is_ignored(app, owner):
    org = owner.organization
    entry = kb.create(org, title="Oude prijzen", body="De uurprijs bedraagt 80 euro.")
    entry.enabled = False
    db.session.commit()
    assert kb.search(org, "uurprijs", "Wat is de uurprijs?") == []


# ── Nooit stukgaan ───────────────────────────────────────────────────────────
def test_search_survives_a_broken_database(app, owner, monkeypatch):
    """Grondslag is een bonus. Klapt de query, dan hoort er een ongegrond concept uit
    te komen — geen uitzondering die de hele conceptketen meesleurt."""
    def boom(*a, **k):
        raise RuntimeError("db weg")

    monkeypatch.setattr(kb, "for_org", boom)
    assert kb.search(owner.organization, "levering", "wanneer?") == []


def test_search_handles_empty_and_none_input(app, owner):
    org = owner.organization
    kb.create(org, title="Leveringstermijnen", body="Binnen 5 werkdagen.")
    assert kb.search(org, "", "") == []
    assert kb.search(org, None, None) == []
    assert kb.search(None, "levering", "wanneer?") == []


# ── Tekst uit bestanden ──────────────────────────────────────────────────────
def test_plain_text_file_is_read(app):
    text, err = kb.extract_text("faq.txt", b"Wij leveren binnen 5 werkdagen.")
    assert err is None and "5 werkdagen" in text


def test_docx_is_read_without_an_extra_dependency(app):
    text, err = kb.extract_text("beleid.docx", _docx(["Leveringstermijn: 5 werkdagen.", "Retour: 14 dagen."]))
    assert err is None
    assert "Leveringstermijn: 5 werkdagen." in text
    # Alinea's blijven gescheiden — anders plakt de ene zin aan de andere en matcht
    # er een combinatie van termen die in het document niet naast elkaar stond.
    assert "\n" in text and "werkdagen.Retour" not in text


def test_docx_entities_are_unescaped(app):
    text, _err = kb.extract_text("x.docx", _docx(["Prijs &lt; 100 &amp; gratis levering"]))
    assert "Prijs < 100 & gratis levering" in text


def test_unsupported_and_oversized_files_are_refused_politely(app):
    _text, err = kb.extract_text("plan.pptx", b"whatever")
    assert err and ".docx" in err
    _text, err = kb.extract_text("groot.txt", b"x" * (kb.MAX_UPLOAD_BYTES + 1))
    assert err and "2 MB" in err


def test_a_corrupt_file_never_raises(app):
    text, err = kb.extract_text("stuk.docx", b"dit is geen zipbestand")
    assert text == "" and err


# ── retrieve(): de twee bronnen samen ────────────────────────────────────────
def test_own_knowledge_comes_first_and_sharepoint_fills_the_rest(app, owner, monkeypatch):
    import app.ai.knowledge as K

    org = owner.organization
    kb.create(org, title="Leveringstermijnen", body="Binnen 5 werkdagen geleverd.")
    monkeypatch.setattr(K, "_sharepoint",
                        lambda o, s, b, n: [{"title": f"SP{i}", "snippet": "x", "url": "u"} for i in range(n)])

    out = K.retrieve(org, "levering", "Wanneer wordt er geleverd?", top_n=3)
    assert [h["title"] for h in out] == ["Leveringstermijnen", "SP0", "SP1"]


def test_sharepoint_is_not_queried_when_own_knowledge_already_fills_the_slots(app, owner, monkeypatch):
    import app.ai.knowledge as K

    org = owner.organization
    for n in ("Leveringstermijnen", "Leveringskosten", "Leveringsgebied"):
        kb.create(org, title=n, body="Over de levering van bestellingen.")

    def boom(*a, **k):
        raise AssertionError("SharePoint mag hier niet bevraagd worden")

    monkeypatch.setattr(K, "_sharepoint", boom)
    assert len(K.retrieve(org, "levering", "Vraag over de levering", top_n=3)) == 3


def test_own_knowledge_reaches_the_prompt(app, owner):
    """De hele keten: blok in de kennisbank → gefenced in de systeemprompt."""
    from app.ai.service import LLMService

    org = owner.organization
    kb.create(org, title="Leveringstermijnen", body="Standaard leveren we binnen 5 werkdagen.")

    class FakeAdapter:
        name = "fake"
        model_name = "fake-1"
        last_usage = (1, 1)

        def complete(self, system, user, **kw):
            self._system = system
            return "Beste klant, zie [1]."

    adapter = FakeAdapter()
    LLMService(adapter).draft("Klant", "Levering", "Wanneer wordt er geleverd?",
                              "neutraal", None, "nl", "Groeten", org=org)
    assert "BEDRIJFSDOCUMENTEN" in adapter._system
    assert "5 werkdagen" in adapter._system
    assert "<<<MAIL>>>" in adapter._system          # als gegevens gefenced, nooit als instructie


# ── Beheerscherm ─────────────────────────────────────────────────────────────
def _login(client, email="owner@acme-kb.be"):
    return client.post("/auth/login", data={"email": email, "password": "geheim123"})


def test_owner_can_add_edit_and_delete_a_block(client, app, owner):
    _login(client)
    assert client.get("/admin/knowledge").status_code == 200

    client.post("/admin/knowledge", data={"title": "Openingsuren", "body": "Open van 9 tot 18u."},
                follow_redirects=True)
    entry = db.session.query(KnowledgeEntry).filter_by(title="Openingsuren").first()
    assert entry is not None and entry.is_org_wide

    client.post("/admin/knowledge", data={"entry_id": entry.id, "title": "Openingsuren",
                                          "body": "Open van 8 tot 17u."}, follow_redirects=True)
    assert "8 tot 17u" in db.session.get(KnowledgeEntry, entry.id).body

    client.post(f"/admin/knowledge/{entry.id}/delete", follow_redirects=True)
    assert db.session.get(KnowledgeEntry, entry.id) is None


def test_upload_stores_the_text_not_the_file(client, app, owner):
    _login(client)
    client.post("/admin/knowledge/upload", data={
        "title": "Beleid",
        "file": (io.BytesIO(_docx(["Retour binnen 14 dagen."])), "beleid.docx"),
    }, content_type="multipart/form-data", follow_redirects=True)

    entry = db.session.query(KnowledgeEntry).filter_by(title="Beleid").first()
    assert entry is not None
    assert entry.source == "file" and entry.filename == "beleid.docx"
    assert "Retour binnen 14 dagen." in entry.body


def test_a_regular_user_cannot_manage_knowledge(client, app, owner):
    org = owner.organization
    role = db.session.query(Role).filter_by(organization_id=org.id, name="Gebruiker").first()
    u = User(email="g@acme-kb.be", name="G", initials=initials_for("G"), avatar_color="#111",
             organization_id=org.id, role_id=role.id if role else None, status="Actief",
             email_verified=True, onboarded=True)
    u.set_password("geheim123")
    db.session.add(u)
    db.session.commit()

    _login(client, "g@acme-kb.be")
    assert client.post("/admin/knowledge", data={"title": "T", "body": "B"}).status_code == 403
    assert client.post("/admin/knowledge/upload", data={}).status_code == 403


def test_a_block_from_another_organization_cannot_be_touched(client, app, owner):
    other = create_company("Concurrent BV", "O", "owner@concurrent-kb2.be", "geheim123")
    other.email_verified = True
    other.onboarded = True
    db.session.commit()
    theirs = kb.create(other.organization, title="Prijzen", body="Uurprijs 95 euro.")

    _login(client)
    assert client.post(f"/admin/knowledge/{theirs.id}/delete").status_code == 404
    assert client.post("/admin/knowledge", data={"entry_id": theirs.id, "title": "X", "body": "Y"}).status_code == 404


def test_removing_an_organization_takes_its_knowledge_with_it(app, owner):
    from app.tenancy import delete_org_deep

    org = owner.organization
    kb.create(org, title="Openingsuren", body="Open van 9 tot 18u.")
    delete_org_deep(org)
    db.session.commit()
    assert db.session.query(KnowledgeEntry).filter_by(organization_id=org.id).count() == 0
