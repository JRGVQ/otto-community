"""Veilige HTML-weergave van e-mailbodies in de inbox.

E-mail-HTML is vijandig terrein: het komt van buiten en kan scripts, formulieren en
phishing-opmaak bevatten. De verdediging is dubbel:

  1. server-side sanitizen met nh3 (Rust/ammonia) — scripts, event-handlers, iframes
     en gevaarlijke URL-schema's gaan eruit vóór er iets naar de browser gaat;
  2. weergave in een ge-sandboxte <iframe srcdoc> ZONDER allow-scripts, zodat zelfs
     iets dat door de sanitizer zou glippen nooit kan draaien. (allow-same-origin is
     daar veilig naast: gevaarlijk is alleen de combinatie mét allow-scripts — wij
     gebruiken same-origin enkel om de hoogte te kunnen meten.)

nh3 wordt lazy geïmporteerd en degradeert gracieus: zonder de dependency geeft
`sanitize_email_html` None terug en valt de inbox terug op de tekstweergave — dezelfde
filosofie als elke andere optionele integratie in Otto.
"""
from __future__ import annotations

import re
import urllib.parse

# Tags bovenop nh3's veilige standaardset die e-mail-opmaak nodig heeft. <style> mag
# mee: CSS kan in de sandbox-iframe geen script draaien en niet buiten de iframe
# tekenen; zonder <style> ziet vrijwel elke nieuwsbrief eruit als brij.
_EXTRA_TAGS = {"img", "table", "thead", "tbody", "tfoot", "tr", "td", "th",
               "center", "font", "style", "span", "hr", "u", "caption", "colgroup", "col"}
# Opmaak-attributen die overal mogen. E-mail-HTML leunt volledig op inline styles en
# op de klassieke table-attributen; zonder deze set blijft er een kale tekstkolom over.
_GENERIC_ATTRS = {"style", "class", "id", "align", "valign", "width", "height", "border",
                  "cellpadding", "cellspacing", "bgcolor", "color", "face", "size",
                  "colspan", "rowspan", "dir", "lang", "title"}

_CID_SRC_RE = re.compile(r'src="cid:([^"]+)"')


def _nh3():
    try:
        import nh3

        return nh3
    except Exception:  # noqa: BLE001 — optionele dependency: zonder nh3 geen HTML-weergave
        return None


def html_view_available() -> bool:
    return _nh3() is not None


def sanitize_email_html(raw: str | None, *, cid_url: str | None = None) -> str | None:
    """Sanitize één e-mailbody. None = niet mogelijk (geen nh3 of geen HTML) →
    de aanroeper valt terug op de tekstweergave.

    `cid_url` is een URL-sjabloon met '__CID__' erin; inline-afbeeldingen
    (src="cid:…") worden daarnaar herschreven zodat de browser ze via Otto's
    eigen (geauthenticeerde) route ophaalt.
    """
    if not (raw or "").strip():
        return None
    nh3 = _nh3()
    if nh3 is None:
        return None

    attributes = dict(getattr(nh3, "ALLOWED_ATTRIBUTES", {}))
    attributes["*"] = set(attributes.get("*", set())) | _GENERIC_ATTRS
    attributes["img"] = set(attributes.get("img", set())) | {"src", "alt", "width", "height", "style"}
    attributes["a"] = set(attributes.get("a", set())) | {"href", "name", "style"}

    cleaned = nh3.clean(
        raw,
        tags=set(nh3.ALLOWED_TAGS) | _EXTRA_TAGS,
        attributes=attributes,
        # Standaard gooit ammonia de INHOUD van <script> én <style> weg; <style> houden
        # we juist (e-mail-opmaak), dus de lijst moet expliciet zonder 'style'.
        clean_content_tags={"script"},
        # cid: moet de sanitizer overleven zodat we hem hierna kunnen herschrijven.
        url_schemes={"http", "https", "mailto", "tel", "cid", "data"},
        link_rel="noopener noreferrer",
        strip_comments=True,
    )

    if cid_url:
        def _rewrite(m: re.Match) -> str:
            cid = urllib.parse.quote(m.group(1), safe="")
            return f'src="{cid_url.replace("__CID__", cid)}"'

        cleaned = _CID_SRC_RE.sub(_rewrite, cleaned)
    return cleaned


# ── Handtekening-sjablonen: een strengere sanitizer dan de inbox-weergave ──────
# Geïmporteerde handtekening-HTML komt van buiten (een collega plakt iets uit Word,
# CodeTwo, een oude nieuwsbrief) en gaat straks ECHT de mail in, niet enkel een
# sandbox-iframe. Daarom strenger dan `sanitize_email_html`:
#   • <style>/<script> en class/id eruit — mailclients strippen die tóch, en een
#     class die de sanitizer overleeft kan in de aflevering niets goeds doen;
#   • data-ph blijft staan — daar hangt het leeg-veld-strippen (app/signatures.py) aan;
#   • alleen veilige URL-schema's (geen data:/cid: bij import).
_SIG_TAGS = {"img", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "center",
             "font", "span", "hr", "u", "caption", "colgroup", "col"}
# class/id bewust NIET in deze set (e-mailclients strippen ze; laat de opmaak inline).
_SIG_ATTRS = {"style", "align", "valign", "width", "height", "border", "cellpadding",
              "cellspacing", "bgcolor", "color", "face", "size", "colspan", "rowspan",
              "dir", "lang", "title", "data-ph"}


def sanitize_signature_html(raw: str | None) -> str | None:
    """Sanitize geïmporteerde handtekening-HTML tot e-mailveilige, inline-opgemaakte
    HTML. None = niet mogelijk (geen nh3 of lege invoer) → de aanroeper wijst af.

    `{{placeholder}}`-velden zijn gewone tekens en overleven de sanitizer ongemoeid.
    """
    if not (raw or "").strip():
        return None
    nh3 = _nh3()
    if nh3 is None:
        return None

    base_tags = set(nh3.ALLOWED_TAGS) | _SIG_TAGS
    base_tags.discard("style")  # nooit <style> in een aan te leveren handtekening
    attributes = dict(getattr(nh3, "ALLOWED_ATTRIBUTES", {}))
    attributes["*"] = (set(attributes.get("*", set())) | _SIG_ATTRS) - {"class", "id"}
    attributes["img"] = {"src", "alt", "width", "height", "style", "data-ph"}
    attributes["a"] = {"href", "name", "style"}

    cleaned = nh3.clean(
        raw,
        tags=base_tags,
        attributes=attributes,
        clean_content_tags={"script", "style"},
        url_schemes={"http", "https", "mailto", "tel"},
        link_rel="noopener noreferrer",
        strip_comments=True,
    )
    return cleaned.strip() or None


def wrap_email_document(inner_html: str) -> str:
    """Het volledige srcdoc-document rond een gesanitizede body.

    Bewust altijd een lichte achtergrond, ook in donker thema: e-mail-HTML gaat uit
    van wit papier — donker forceren maakt de helft van de nieuwsbrieven onleesbaar.
    <base target="_blank"> laat links buiten de sandbox openen (met allow-popups)."""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<base target=\"_blank\">"
        "<style>"
        "html,body{margin:0;padding:0;background:#fff;}"
        "body{font:14px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
        "color:#1f2328;word-break:break-word;padding:2px;}"
        "img{max-width:100%;height:auto;}"
        "table{max-width:100%;}"
        "blockquote{margin:8px 0 8px 8px;padding-left:10px;border-left:3px solid #d6d9dd;color:#57606a;}"
        "</style></head><body>"
        f"{inner_html}"
        "</body></html>"
    )
