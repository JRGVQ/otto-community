"""SAML 2.0 login — voor bedrijven met een IdP die geen OIDC spreekt (ADFS, Okta SAML).

Vertrouwen berust hier volledig op de XML-handtekening van de IdP. Die verificatie is
berucht foutgevoelig — XML Signature Wrapping laat een aanvaller een geldig ondertekend
fragment naast vervalste gegevens zetten — dus ze wordt niet zelf geschreven maar aan
`signxml` overgelaten, en er wordt uitsluitend gelezen uit het fragment dat de verificatie
*teruggeeft*, nooit uit het oorspronkelijke document.

Ontbreekt signxml (optionele dependency, zoals elke echte integratie in Otto), dan faalt
het inloggen closed met een duidelijke logregel — nooit stilzwijgend doorlaten.

Buiten de handtekening worden ook de voorwaarden gecontroleerd die een geldige respons
alsnog onbruikbaar maken: geldigheidsvenster, publiek, bestemming, en of dit antwoord
hoort bij het verzoek dat wíj gestuurd hebben.
"""
from __future__ import annotations

import base64
import secrets
import zlib
from datetime import UTC, datetime, timedelta

from flask import current_app, flash, redirect, request, session, url_for
from flask_babel import gettext as _
from flask_login import login_user

from app.extensions import db

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
}
# Speling voor klokverschil tussen ons en de IdP; zonder marge faalt een correcte
# aanmelding zodra de servers een paar seconden uiteenlopen.
CLOCK_SKEW = timedelta(minutes=3)


class SamlError(RuntimeError):
    """Deze respons is niet te vertrouwen. De boodschap gaat naar het log, niet naar de gebruiker."""


# ── Onze eigen identiteit als dienstverlener ─────────────────────────────────
def sp_entity_id() -> str:
    """Het EntityID waarmee Otto zich bij de IdP bekendmaakt."""
    return current_app.config["PUBLIC_BASE_URL"].rstrip("/") + "/auth/saml/metadata"


def acs_url() -> str:
    """Waar de IdP zijn antwoord naartoe post."""
    return current_app.config["PUBLIC_BASE_URL"].rstrip("/") + url_for("auth.saml_acs")


def sp_metadata() -> str:
    """SP-metadata, zodat een beheerder Otto in één keer in zijn IdP kan zetten."""
    return (
        '<?xml version="1.0"?>\n'
        '<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" '
        f'entityID="{sp_entity_id()}">\n'
        '  <SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
        'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">\n'
        '    <NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</NameIDFormat>\n'
        '    <AssertionConsumerService '
        'Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'Location="{acs_url()}" index="0" isDefault="true"/>\n'
        '  </SPSSODescriptor>\n'
        '</EntityDescriptor>\n'
    )


# ── Verzoek ──────────────────────────────────────────────────────────────────
def start_saml(org):
    """Stuur de gebruiker naar de IdP met een AuthnRequest (HTTP-Redirect binding)."""
    import urllib.parse

    sso = org.sso
    request_id = "_" + secrets.token_hex(16)  # moet met een letter of _ beginnen (xs:ID)
    issued = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    xml = (
        '<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID="{request_id}" Version="2.0" IssueInstant="{issued}" '
        'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'AssertionConsumerServiceURL="{acs_url()}" Destination="{sso.saml_sso_url}">'
        f'<saml:Issuer>{sp_entity_id()}</saml:Issuer>'
        '<samlp:NameIDPolicy Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress" '
        'AllowCreate="true"/>'
        "</samlp:AuthnRequest>"
    )
    # HTTP-Redirect binding: deflaten zonder zlib-header, dan base64.
    deflated = zlib.compress(xml.encode())[2:-4]
    relay = secrets.token_urlsafe(24)

    # Onthouden waar dit antwoord bij hoort — de ACS weigert alles wat hier niet op past.
    session["saml_request_id"] = request_id
    session["saml_relay_state"] = relay
    session["saml_org_id"] = org.id

    query = urllib.parse.urlencode({
        "SAMLRequest": base64.b64encode(deflated).decode(),
        "RelayState": relay,
    })
    sep = "&" if "?" in sso.saml_sso_url else "?"
    return redirect(sso.saml_sso_url + sep + query)


# ── Antwoord ─────────────────────────────────────────────────────────────────
def _verified_assertion(raw_xml: bytes, cert: str):
    """Het ondertekende fragment, of een SamlError.

    Geeft bewust terug wat de verificatie oplevert (`signed_xml`) en niet het ingestuurde
    document: dat verschil is precies wat XML Signature Wrapping onmogelijk maakt.
    """
    try:
        from signxml import XMLVerifier
    except ImportError as exc:
        raise SamlError("signxml is niet geïnstalleerd, dus de handtekening kan niet gecontroleerd worden") from exc

    try:
        result = XMLVerifier().verify(raw_xml, x509_cert=cert)
    except Exception as exc:  # noqa: BLE001 — elke twijfel is een weigering
        raise SamlError(f"handtekening niet geldig: {exc}") from exc

    signed = getattr(result, "signed_xml", None)
    if signed is None:
        raise SamlError("verificatie leverde geen ondertekend fragment op")
    return signed


def _text(node, path: str) -> str:
    found = node.find(path, NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def _parse_instant(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _check_conditions(assertion, *, expected_request_id: str | None) -> None:
    """Alles wat een correct ondertekende respons alsnog ongeldig maakt."""
    now = datetime.now(UTC)

    conditions = assertion.find("saml:Conditions", NS)
    if conditions is not None:
        not_before = _parse_instant(conditions.get("NotBefore", ""))
        not_after = _parse_instant(conditions.get("NotOnOrAfter", ""))
        if not_before and now + CLOCK_SKEW < not_before:
            raise SamlError("assertie is nog niet geldig")
        if not_after and now - CLOCK_SKEW >= not_after:
            raise SamlError("assertie is verlopen")

        # Het publiek moet ónze dienst zijn; anders is dit een antwoord voor iemand anders.
        audiences = [
            (a.text or "").strip()
            for a in conditions.iterfind("saml:AudienceRestriction/saml:Audience", NS)
        ]
        if audiences and sp_entity_id() not in audiences:
            raise SamlError(f"assertie is niet voor ons bedoeld (publiek: {audiences})")

    # Hoort dit antwoord bij het verzoek dat wij stuurden? Zonder deze controle kan een
    # elders opgevangen, geldig ondertekende respons hier opnieuw worden afgespeeld.
    for confirmation in assertion.iterfind(
        "saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData", NS
    ):
        in_response_to = confirmation.get("InResponseTo")
        if in_response_to and in_response_to != expected_request_id:
            raise SamlError("antwoord hoort niet bij ons verzoek")
        recipient = confirmation.get("Recipient")
        if recipient and recipient.rstrip("/") != acs_url().rstrip("/"):
            raise SamlError("antwoord was voor een andere bestemming bedoeld")
        expires = _parse_instant(confirmation.get("NotOnOrAfter", ""))
        if expires and now - CLOCK_SKEW >= expires:
            raise SamlError("bevestiging is verlopen")


def _identity(assertion) -> tuple[str, str]:
    """(e-mail, naam) uit de ondertekende assertie."""
    attrs: dict[str, list[str]] = {}
    for attr in assertion.iterfind("saml:AttributeStatement/saml:Attribute", NS):
        name = attr.get("Name") or ""
        values = [(v.text or "").strip() for v in attr.iterfind("saml:AttributeValue", NS)]
        attrs[name] = [v for v in values if v]

    def first(*names):
        for n in names:
            if attrs.get(n):
                return attrs[n][0]
        return ""

    email = first(
        "urn:oid:0.9.2342.19200300.100.1.3",  # mail
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
        "email", "mail", "emailAddress",
    )
    if not email:
        email = _text(assertion, "saml:Subject/saml:NameID")  # meestal het adres zelf
    name = first(
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
        "urn:oid:2.16.840.1.113730.3.1.241",  # displayName
        "displayName", "name",
    )
    email = email.lower().strip()
    if "@" not in email:
        raise SamlError("geen bruikbaar e-mailadres in de assertie")
    return email, (name or email.split("@")[0])


def handle_saml_acs():
    """Verwerk de POST van de IdP en meld de gebruiker aan."""
    from app.auth.oauth.oidc import _find_or_provision
    from app.models import Organization

    org = db.session.get(Organization, session.pop("saml_org_id", 0) or 0)
    expected_request_id = session.pop("saml_request_id", None)
    expected_relay = session.pop("saml_relay_state", None)

    raw = request.form.get("SAMLResponse")
    if not (org and org.sso and org.sso.saml_ready and raw):
        flash(_("Aanmelden met SSO is afgebroken."), "error")
        return redirect(url_for("auth.login"))

    relay = request.form.get("RelayState")
    try:
        if expected_relay and relay and relay != expected_relay:
            raise SamlError("RelayState klopt niet")
        assertion = _verified_assertion(base64.b64decode(raw), org.sso.saml_x509_cert)
        # De IdP mag ofwel de assertie ofwel de hele respons ondertekenen; in het tweede
        # geval zit de assertie erin. Verder werken we alleen met het ondertekende deel.
        if not assertion.tag.endswith("Assertion"):
            inner = assertion.find("saml:Assertion", NS)
            if inner is None:
                raise SamlError("geen ondertekende assertie gevonden")
            assertion = inner
        _check_conditions(assertion, expected_request_id=expected_request_id)
        email, name = _identity(assertion)
    except SamlError as exc:
        current_app.logger.warning("SAML-aanmelding geweigerd voor org %s: %s", org.id, exc)
        flash(_("Aanmelden met SSO is mislukt. Probeer opnieuw."), "error")
        return redirect(url_for("auth.login"))

    user = _find_or_provision(org, email, name)
    if user is None:
        flash(_("SSO-aanmelding geweigerd (geen toegang voor dit account)."), "error")
        return redirect(url_for("auth.login"))
    if user.status == "Geblokkeerd":
        flash(_("Dit account is geblokkeerd."), "error")
        return redirect(url_for("auth.login"))

    user.touch()
    db.session.commit()
    login_user(user)
    return redirect(url_for("web.inbox") if user.onboarded else url_for("auth.onboarding"))
