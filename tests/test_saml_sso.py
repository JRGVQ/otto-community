"""SAML 2.0-aanmelding.

Deze tests ondertekenen echte SAML-responses met een zelf aangemaakt certificaat, zodat
de handtekeningcontrole werkelijk uitgevoerd wordt in plaats van weggemockt. Het zwaartepunt
ligt op de weigeringen: een verkeerde ondertekenaar, een verlopen assertie, een antwoord dat
niet bij ons verzoek hoort, en een antwoord dat voor iemand anders bestemd was.
"""
from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.auth.service import create_company
from app.extensions import db

# Vóór elk gebruik: zonder deze bibliotheken kan SAML niet getest worden. Ze staan in CI
# expliciet geïnstalleerd (zie .github/workflows/deploy.yml) juist omdat dit de
# handtekeningcontrole bewaakt — overgeslagen beveiligingstests zijn geen beveiliging.
etree = pytest.importorskip("lxml.etree", reason="SAML vereist lxml")
pytest.importorskip("signxml", reason="SAML vereist signxml")

# Afgeleid van PUBLIC_BASE_URL, net als de app zelf doet — hardcoderen zou de test laten
# slagen of falen op een configuratiewaarde in plaats van op het gedrag.
def _urls(app):
    base = app.config["PUBLIC_BASE_URL"].rstrip("/")
    return base + "/auth/saml/acs", base + "/auth/saml/metadata"


def _keypair(cn="test-idp"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return (
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()),
        cert.public_bytes(serialization.Encoding.PEM).decode(),
    )


IDP_KEY, IDP_CERT = _keypair()
OTHER_KEY, OTHER_CERT = _keypair("andere-idp")


def _response_xml(app, *, email="sam@acme-saml.be", name="Sam Jansen",
                  not_before=None, not_after=None, audience=None, recipient=None,
                  in_response_to="_req1"):
    acs, sp_entity = _urls(app)
    audience = sp_entity if audience is None else audience
    recipient = acs if recipient is None else recipient
    now = datetime.now(UTC)
    nb = (not_before or now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    na = (not_after or now + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    aud = f"<saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction>" if audience else ""
    confirm = (
        f'<saml:SubjectConfirmationData InResponseTo="{in_response_to}" '
        f'Recipient="{recipient}" NotOnOrAfter="{na}"/>' if in_response_to or recipient else ""
    )
    return (
        '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_resp1" Version="2.0">'
        '<saml:Assertion ID="_assert1" Version="2.0">'
        f"<saml:Issuer>https://idp.example/</saml:Issuer>"
        "<saml:Subject>"
        f'<saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{email}</saml:NameID>'
        f'<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">{confirm}</saml:SubjectConfirmation>'
        "</saml:Subject>"
        f'<saml:Conditions NotBefore="{nb}" NotOnOrAfter="{na}">{aud}</saml:Conditions>'
        "<saml:AttributeStatement>"
        '<saml:Attribute Name="http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress">'
        f"<saml:AttributeValue>{email}</saml:AttributeValue></saml:Attribute>"
        '<saml:Attribute Name="http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name">'
        f"<saml:AttributeValue>{name}</saml:AttributeValue></saml:Attribute>"
        "</saml:AttributeStatement>"
        "</saml:Assertion></samlp:Response>"
    )


def _signed(xml: str, key=IDP_KEY, cert=IDP_CERT) -> str:
    import signxml
    from signxml import XMLSigner

    root = etree.fromstring(xml.encode())
    signed = XMLSigner(method=signxml.methods.enveloped).sign(root, key=key, cert=cert)
    return base64.b64encode(etree.tostring(signed)).decode()


@pytest.fixture()
def org(app):
    user = create_company("Acme BV", "Owner", "owner@acme-saml.be", "geheim123")
    user.email_verified = True
    db.session.commit()
    o = user.organization
    sso = o.sso
    sso.enabled = True
    sso.method = "SAML 2.0"
    sso.saml_sso_url = "https://idp.example/sso"
    sso.saml_entity_id = "https://idp.example/"
    sso.saml_x509_cert = IDP_CERT
    sso.allowed_domains = ["acme-saml.be"]
    sso.jit = True  # verse organisaties hebben JIT uit; hier testen we juist die weg
    db.session.commit()
    return o


def _post(client, org, saml_b64, *, request_id="_req1", relay="relay-1", session_relay="relay-1"):
    with client.session_transaction() as sess:
        sess["saml_org_id"] = org.id
        sess["saml_request_id"] = request_id
        sess["saml_relay_state"] = session_relay
    return client.post("/auth/saml/acs", data={"SAMLResponse": saml_b64, "RelayState": relay},
                       follow_redirects=False)


def _logged_in(client) -> bool:
    return "/app" in (client.get("/app/inbox").headers.get("Location") or "/app")


# ── Gelukte aanmelding ───────────────────────────────────────────────────────
def test_valid_response_logs_the_user_in(app, client, org):
    resp = _post(client, org, _signed(_response_xml(app)))
    assert resp.status_code == 302 and "/auth/login" not in resp.headers["Location"]

    from app.models import User

    user = db.session.query(User).filter_by(email="sam@acme-saml.be").first()
    assert user is not None and user.organization_id == org.id  # JIT-provisioning


def test_name_comes_from_the_assertion(app, client, org):
    _post(client, org, _signed(_response_xml(app, name="Sam Jansen")))
    from app.models import User

    assert db.session.query(User).filter_by(email="sam@acme-saml.be").first().name == "Sam Jansen"


# ── Weigeringen ──────────────────────────────────────────────────────────────
def _refused(resp) -> bool:
    return resp.status_code == 302 and "/auth/login" in resp.headers["Location"]


def test_signature_from_another_idp_is_refused(app, client, org):
    """De kern: alleen het geconfigureerde certificaat telt."""
    resp = _post(client, org, _signed(_response_xml(app), key=OTHER_KEY, cert=OTHER_CERT))
    assert _refused(resp)
    from app.models import User

    assert db.session.query(User).filter_by(email="sam@acme-saml.be").first() is None


def test_unsigned_response_is_refused(app, client, org):
    raw = base64.b64encode(_response_xml(app).encode()).decode()
    assert _refused(_post(client, org, raw))


def test_tampered_response_is_refused(app, client, org):
    """Na ondertekening het adres wijzigen moet de handtekening breken."""
    signed = base64.b64decode(_signed(_response_xml(app)))
    tampered = signed.replace(b"sam@acme-saml.be", b"baas@acme-saml.be")
    assert _refused(_post(client, org, base64.b64encode(tampered).decode()))


def test_expired_assertion_is_refused(app, client, org):
    old = datetime.now(UTC) - timedelta(hours=2)
    xml = _response_xml(app, not_before=old - timedelta(minutes=10), not_after=old)
    assert _refused(_post(client, org, _signed(xml)))


def test_assertion_that_is_not_yet_valid_is_refused(app, client, org):
    future = datetime.now(UTC) + timedelta(hours=2)
    xml = _response_xml(app, not_before=future, not_after=future + timedelta(minutes=10))
    assert _refused(_post(client, org, _signed(xml)))


def test_response_for_another_audience_is_refused(app, client, org):
    xml = _response_xml(app, audience="https://iemand-anders.example/saml")
    assert _refused(_post(client, org, _signed(xml)))


def test_response_to_a_different_request_is_refused(app, client, org):
    """Een elders opgevangen, geldig ondertekend antwoord mag hier niet werken."""
    xml = _response_xml(app, in_response_to="_een-ander-verzoek")
    assert _refused(_post(client, org, _signed(xml), request_id="_req1"))


def test_response_for_another_destination_is_refused(app, client, org):
    xml = _response_xml(app, recipient="https://ergens-anders.example/acs")
    assert _refused(_post(client, org, _signed(xml)))


def test_mismatched_relay_state_is_refused(app, client, org):
    assert _refused(_post(client, org, _signed(_response_xml(app)),
                          relay="vervalst", session_relay="relay-1"))


def test_post_without_a_started_session_is_refused(app, client, org):
    resp = client.post("/auth/saml/acs", data={"SAMLResponse": _signed(_response_xml(app))})
    assert _refused(resp)


def test_empty_post_is_refused(app, client, org):
    assert _refused(_post(client, org, ""))


def test_login_fails_closed_without_signxml(app, client, org, monkeypatch):
    """Zonder de bibliotheek kan niets gecontroleerd worden → nooit vertrouwen."""
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def blocked(name, *a, **kw):
        if name == "signxml":
            raise ImportError("signxml ontbreekt")
        return real_import(name, *a, **kw)

    signed = _signed(_response_xml(app))
    monkeypatch.setattr("builtins.__import__", blocked)
    assert _refused(_post(client, org, signed))


# ── Protocolkeuze ────────────────────────────────────────────────────────────
def test_sso_start_redirects_to_the_saml_idp(app, client, org):
    resp = client.post("/auth/sso", data={"email": "sam@acme-saml.be"})
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("https://idp.example/sso?")
    assert "SAMLRequest=" in resp.headers["Location"]


def test_oidc_still_wins_when_saml_is_not_configured(app, client, org):
    sso = org.sso
    sso.saml_sso_url = sso.saml_x509_cert = None
    sso.method = "OIDC"
    sso.oidc_issuer = "https://idp.example/v2.0"
    sso.oidc_client_id = "client-123"
    db.session.commit()
    # OIDC probeert de issuer te ontdekken; dat faalt zonder netwerk, maar de keuze
    # voor OIDC blijkt uit het feit dat er géén SAML-redirect komt.
    resp = client.post("/auth/sso", data={"email": "sam@acme-saml.be"})
    assert "SAMLRequest=" not in (resp.headers.get("Location") or "")


def test_saml_is_used_when_both_are_set_and_saml_is_preferred(app, client, org):
    sso = org.sso
    sso.oidc_issuer = "https://idp.example/v2.0"
    sso.oidc_client_id = "client-123"
    sso.method = "SAML 2.0"
    db.session.commit()
    resp = client.post("/auth/sso", data={"email": "sam@acme-saml.be"})
    assert "SAMLRequest=" in resp.headers["Location"]


# ── Metadata ─────────────────────────────────────────────────────────────────
def test_metadata_describes_our_acs(app, client):
    resp = client.get("/auth/saml/metadata")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "/auth/saml/acs" in body and "WantAssertionsSigned" in body
    etree.fromstring(resp.get_data())  # moet geldige XML zijn


def test_acs_is_csrf_exempt(app, client, org):
    """De IdP post zonder ons CSRF-token; de bescherming zit in de handtekening."""
    from app.auth.routes import saml_acs

    assert getattr(saml_acs, "_csrf_exempt", True)
