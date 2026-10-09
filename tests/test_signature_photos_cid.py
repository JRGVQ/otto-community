"""Fase 2 — profielfoto's, aan Graph gekoppelde variabelen en CID-ingebedde afbeeldingen.

Bewaakte beloftes:
1. graph_value leest gewone, geneste (extensionAttributes) en schema-extension-velden;
   select_fields_for voegt gekoppelde attributen toe aan de $select;
2. refresh_mapped_variables schrijft de Graph-waarde per gebruiker weg en vult zo {{key}};
3. {{foto}} verwijst naar de gecachte profielfoto (of verdwijnt netjes zonder foto);
4. inline_images_for herschrijft interne /media-afbeeldingen naar cid: + verzamelt de bytes;
   externe https-afbeeldingen blijven ongemoeid; write_draft geeft ze door aan de provider;
5. de media-routes zijn strikt org-gescoped (geen IDOR); de asset-upload valideert en de
   verwijderacties blijven binnen de eigen organisatie.
"""
from __future__ import annotations

import io

import pytest

from app.auth.service import create_company
from app.extensions import db
from app.models import (
    MailAccount,
    Signature,
    SignatureAsset,
    SignatureVariable,
    SignatureVariableValue,
    User,
    UserPhoto,
)
from app.seed.demo_data import DEMO_PASSWORD


@pytest.fixture()
def auth(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def _demo_user():
    return db.session.query(User).filter_by(email="demo@example.com").first()


# ── graph_value + select_fields_for ──────────────────────────────────────────
def test_graph_value_reads_plain_nested_and_extension():
    from app.graph_profile import graph_value

    me = {
        "city": "Gent",
        "onPremisesExtensionAttributes": {"extensionAttribute3": "AFD-42"},
        "extension_abc_costCenter": "CC-7",
    }
    assert graph_value(me, "city") == "Gent"
    assert graph_value(me, "onPremisesExtensionAttributes.extensionAttribute3") == "AFD-42"
    assert graph_value(me, "extension_abc_costCenter") == "CC-7"
    assert graph_value(me, "onPremisesExtensionAttributes.extensionAttribute9") == ""
    assert graph_value(me, "onbestaand") == ""
    assert graph_value(None, "city") == ""


def test_select_fields_includes_mapped_attr(app):
    from app.graph_profile import PROFILE_SELECT, select_fields_for

    user = _demo_user()
    db.session.add(SignatureVariable(organization_id=user.organization_id, user_id=None,
                                     key="kostenplaats", label="Kostenplaats",
                                     graph_attr="extension_abc_costCenter"))
    db.session.commit()
    fields = select_fields_for(user.organization).split(",")
    assert "extension_abc_costCenter" in fields
    # onPremisesExtensionAttributes hoeft niet dubbel: zit al in PROFILE_SELECT.
    assert fields.count("onPremisesExtensionAttributes") == 1
    assert "city" in PROFILE_SELECT  # standaardveld uitgebreid


def test_refresh_mapped_variables_fills_value_and_placeholder(app):
    from app.graph_profile import refresh_mapped_variables
    from app.signatures import render_placeholders

    user = _demo_user()
    var = SignatureVariable(organization_id=user.organization_id, user_id=None,
                            key="stad", label="Stad", graph_attr="city")
    db.session.add(var)
    db.session.commit()

    assert refresh_mapped_variables(user, {"city": "Brugge"}) is True
    val = (db.session.query(SignatureVariableValue)
           .filter_by(variable_id=var.id, user_id=user.id).one())
    assert val.value == "Brugge"
    # Graph is de bron van waarheid: een nieuwe waarde overschrijft (caller commit,
    # dus geen refresh — val is hetzelfde object dat de functie bijwerkte).
    assert refresh_mapped_variables(user, {"city": "Antwerpen"}) is True
    assert val.value == "Antwerpen"
    # En de placeholder vult ermee.
    assert render_placeholders("{{stad}}", user) == "Antwerpen"


# ── {{foto}} + inline_images_for ─────────────────────────────────────────────
def _give_photo(user, data=b"JPEGBYTES", ct="image/jpeg", etag="e1"):
    db.session.add(UserPhoto(user_id=user.id, organization_id=user.organization_id,
                             content_type=ct, etag=etag, data=data))
    db.session.commit()


def test_foto_placeholder_present_only_with_a_photo(app):
    from app.signatures import placeholder_values

    user = _demo_user()
    assert placeholder_values(user).get("foto") == ""     # geen foto → leeg (data-ph verdwijnt)
    _give_photo(user)
    assert placeholder_values(user)["foto"] == f"/media/user-photo/{user.id}"


def test_inline_images_rewrites_media_to_cid(app):
    from app.signatures import inline_images_for

    user = _demo_user()
    _give_photo(user, data=b"PHOTO", ct="image/png")
    asset = SignatureAsset(organization_id=user.organization_id, kind="logo",
                           content_type="image/png", data=b"LOGO", filename="l.png")
    db.session.add(asset)
    db.session.commit()

    html = (f'<img src="/media/user-photo/{user.id}">'
            f'<img src="/media/sig-asset/{asset.id}">'
            '<img src="https://acme.be/x.png">')
    out, images = inline_images_for(html)
    assert f"cid:otto-user-photo-{user.id}" in out
    assert f"cid:otto-sig-asset-{asset.id}" in out
    assert "https://acme.be/x.png" in out                 # extern blijft ongemoeid
    cids = {i["content_id"]: i for i in images}
    assert cids[f"otto-user-photo-{user.id}"]["data"] == b"PHOTO"
    assert cids[f"otto-sig-asset-{asset.id}"]["data"] == b"LOGO"


def test_inline_images_leaves_unknown_media_untouched(app):
    from app.signatures import inline_images_for

    out, images = inline_images_for('<img src="/media/sig-asset/999999">')
    assert images == []
    assert "/media/sig-asset/999999" in out and "cid:" not in out


def test_write_draft_passes_inline_images(app):
    from app.constants import DRAFT_CONCEPT
    from app.models import Draft, Message
    from app.tasks import writeback as wb

    owner = create_company("Foto BV", "Fee Foto", "fee@foto-x.be", "geheim123")
    owner.email_verified = owner.onboarded = True
    db.session.commit()
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email="fee@foto-x.be", status="connected",
                       mailbox_type="personal")
    db.session.add(acct)
    _give_photo(owner, data=b"FEEPHOTO")
    # Domein-gebonden (matcht het verzenddomein) zodat de handtekening wint van een
    # eventuele persoonlijke stijl-handtekening — zie de volgorde in resolve_rich.
    db.session.add(Signature(organization_id=owner.organization_id, domain="foto-x.be", name="Std",
                             body="Groeten", html=f'<img src="/media/user-photo/{owner.id}"> {{{{naam}}}}'))
    db.session.flush()
    msg = Message(organization_id=owner.organization_id, user_id=owner.id, account_id=acct.id,
                  sender="K", sender_email="k@x.be", subject="V", provider_message_id="PM1",
                  provider_thread_id="CT1")
    db.session.add(msg)
    db.session.flush()
    draft = Draft(message_id=msg.id, status=DRAFT_CONCEPT, current_text="Dag")
    db.session.add(draft)
    db.session.commit()

    seen = {}

    class FakeProvider:
        def is_configured(self):
            return True

        def create_draft(self, thread_id, subject, body, in_reply_to=None, references=None,
                         signature_html=None, signature_text=None, inline_images=None):
            seen.update(signature_html=signature_html, inline_images=inline_images or [])
            return "PD1"

    orig = wb.get_provider
    wb.get_provider = lambda a: FakeProvider()
    try:
        assert wb.write_draft(draft) == "PD1"
    finally:
        wb.get_provider = orig
    assert f"cid:otto-user-photo-{owner.id}" in seen["signature_html"]
    assert seen["inline_images"] and seen["inline_images"][0]["data"] == b"FEEPHOTO"


# ── media-routes: org-isolatie ───────────────────────────────────────────────
def test_photo_route_is_org_scoped(app, auth):
    user = _demo_user()
    _give_photo(user, data=b"MINE")
    r = auth.get(f"/media/user-photo/{user.id}")
    assert r.status_code == 200 and r.data == b"MINE"

    other = create_company("Ander BV", "Otto Ander", "o@ander-x.be", "geheim123")
    db.session.add(UserPhoto(user_id=other.id, organization_id=other.organization_id,
                             content_type="image/png", data=b"THEIRS"))
    db.session.commit()
    assert auth.get(f"/media/user-photo/{other.id}").status_code == 404  # geen IDOR


def test_asset_route_is_org_scoped(app, auth):
    user = _demo_user()
    mine = SignatureAsset(organization_id=user.organization_id, kind="logo",
                          content_type="image/png", data=b"MINE")
    other_org = create_company("X BV", "X Y", "x@x-org.be", "geheim123").organization_id
    theirs = SignatureAsset(organization_id=other_org, kind="logo",
                            content_type="image/png", data=b"THEIRS")
    db.session.add_all([mine, theirs])
    db.session.commit()
    assert auth.get(f"/media/sig-asset/{mine.id}").status_code == 200
    assert auth.get(f"/media/sig-asset/{theirs.id}").status_code == 404


# ── asset-upload + variabele-mapping routes ──────────────────────────────────
def test_asset_upload_and_delete(app, auth):
    user = _demo_user()
    data = {"kind": "logo", "file": (io.BytesIO(b"\x89PNG..."), "logo.png")}
    r = auth.post("/admin/signatures/assets/upload", data=data,
                  content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    asset = db.session.query(SignatureAsset).filter_by(organization_id=user.organization_id).one()
    assert asset.kind == "logo" and asset.data == b"\x89PNG..."

    # Verkeerd type wordt geweigerd.
    bad = {"file": (io.BytesIO(b"x"), "note.txt")}
    auth.post("/admin/signatures/assets/upload", data=bad,
              content_type="multipart/form-data", follow_redirects=True)
    assert db.session.query(SignatureAsset).count() == 1

    assert auth.post(f"/admin/signatures/assets/{asset.id}/delete",
                     follow_redirects=True).status_code == 200
    assert db.session.get(SignatureAsset, asset.id) is None


def test_get_my_photo_etag_and_bytes(app):
    from app.providers.outlook import OutlookProvider

    acct = MailAccount(provider="outlook", email="x@y.be")
    p = OutlookProvider(acct)
    p._get = lambda path, timeout=None: {"@odata.mediaEtag": "E2", "@odata.mediaContentType": "image/png"}
    p._send = lambda method, path, *a, **k: b"PNGDATA"

    # Ongewijzigde etag → "unchanged", geen download.
    assert p.get_my_photo(etag="E2") == "unchanged"
    # Nieuwe etag → (bytes, content_type, etag).
    data, ct, etag = p.get_my_photo(etag="OUD")
    assert data == b"PNGDATA" and ct == "image/png" and etag == "E2"

    # Geen foto (404 → _get gooit) → None.
    def _boom(path, timeout=None):
        raise RuntimeError("404")

    p._get = _boom
    assert p.get_my_photo() is None


def test_variable_add_with_graph_attr(app, auth):
    user = _demo_user()
    auth.post("/admin/signatures/vars/add",
              data={"label": "Stad", "graph_attr": "city"}, follow_redirects=True)
    var = db.session.query(SignatureVariable).filter_by(
        organization_id=user.organization_id, key="stad").one()
    assert var.graph_attr == "city" and var.is_mapped
    # Ongeldige attribuut-vorm wordt genegeerd (val terug op handmatig).
    auth.post("/admin/signatures/vars/add",
              data={"label": "Raar", "graph_attr": "drop table;"}, follow_redirects=True)
    var2 = db.session.query(SignatureVariable).filter_by(
        organization_id=user.organization_id, key="raar").one()
    assert var2.graph_attr is None
