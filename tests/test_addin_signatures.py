"""Handtekening-keuze in het Outlook-taakvenster.

Bewaakte beloftes:
1. /addin/signatures vereist een sessie (geen gegevens zonder login);
2. het levert de org-handtekeningen met de placeholders van de gebruiker ingevuld,
   met de aanbevolen vooraan en gemarkeerd als default;
3. het manifest ondersteunt compose (invoegen) met ReadWriteItem;
4. het taakvenster bevat de handtekening-invoegcode.
"""
from __future__ import annotations

from app.extensions import db
from app.models import Signature, User


def _demo_user():
    return db.session.query(User).filter_by(email="demo@example.com").first()


def test_signatures_requires_a_session(client):
    r = client.get("/addin/signatures")
    assert r.status_code == 200
    assert r.get_json() == {"authenticated": False, "signatures": []}


def test_signatures_are_rendered_for_the_user(app, auth_client):
    user = _demo_user()
    user.job_title = "Zaakvoerder"
    # Wis een persoonlijke tekst-handtekening zodat de org-HTML-standaard de aanbevolen wordt.
    if user.style_profile:
        user.style_profile.signature = None
    db.session.add(Signature(organization_id=user.organization_id, domain=None,
                             name="Huis", body="x", html="<b>{{naam}}</b> — {{functie}}"))
    db.session.commit()

    data = auth_client.get("/addin/signatures").get_json()
    assert data["authenticated"] is True
    sigs = data["signatures"]
    assert sigs, "verwacht minstens één handtekening"
    # De aanbevolen (HTML-standaard) staat vooraan en is default.
    assert sigs[0]["default"] is True
    joined = " ".join(s["html"] for s in sigs)
    assert "Zaakvoerder" in joined and user.name.split()[0] in joined
    assert "{{" not in joined  # placeholders zijn ingevuld, niet rauw


def test_manifest_supports_compose_with_readwrite(client):
    body = client.get("/addin/manifest.xml").get_data(as_text=True)
    assert "ReadWriteItem" in body
    assert 'FormType="Edit"' in body
    assert "MessageComposeCommandSurface" in body


def test_taskpane_has_signature_insertion(client):
    body = client.get("/addin/taskpane").get_data(as_text=True)
    assert "/addin/signatures" in body
    assert "setSignatureAsync" in body        # invoegen in het handtekeninggebied
    assert "otto_auto_sig" in body            # auto-op-nieuwe-mail voorkeur
