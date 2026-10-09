"""Kant-en-klare handtekening-sjablonen — de galerij die je in de editor laadt.

Elk sjabloon is e-mailveilige HTML met inline styles en placeholders ({{naam}},
{{functie}}, …). Je klikt "Gebruik dit sjabloon", het opent in de WYSIWYG-editor en
je past het aan. Puur startpunten: ze worden niet in de database bewaard tot je een
handtekening opslaat, dus een sjabloon toevoegen is gewoon een dict hieronder.

`--ACCENT--` wordt in de builder vervangen door de gekozen accentkleur bij het laden;
zo blijft één sjabloondefinitie bruikbaar in elke huisstijl.
"""
from __future__ import annotations

from flask_babel import gettext as _

_ACCENT = "--ACCENT--"


def _klassiek() -> str:
    # Geen slotgroet in het blok: die schrijft het concept zelf al één keer erboven,
    # zodat "Met vriendelijke groeten," niet dubbel verschijnt (zie app/signatures.py).
    return (
        '<table cellpadding="0" cellspacing="0" border="0" style="font-family:Verdana,Arial,sans-serif;font-size:13px;color:#1f2328;">'
        f'<tr><td style="border-left:3px solid {_ACCENT};padding-left:14px;vertical-align:top;">'
        f'<div style="font-weight:bold;font-size:14px;color:{_ACCENT};">{{{{naam}}}}</div>'
        '<div data-ph="functie" style="color:#57606a;font-size:12.5px;">{{functie}}</div>'
        '<div style="font-weight:600;font-size:12.5px;margin-top:2px;">{{bedrijf}}</div>'
        '<div style="margin-top:8px;font-size:12px;color:#57606a;">'
        '<span data-ph="telefoon" style="margin-right:12px;white-space:nowrap;">📞 {{telefoon}}</span>'
        '<span data-ph="email" style="margin-right:12px;white-space:nowrap;">✉️ {{email}}</span>'
        '</div></td></tr></table>'
    )


def _modern_logo() -> str:
    return (
        '<table cellpadding="0" cellspacing="0" border="0" style="font-family:\'Segoe UI\',Arial,sans-serif;font-size:13px;color:#1f2328;">'
        '<tr>'
        # {{logo}} komt uit de brand kit van het verzenddomein; zonder brand kit verdwijnt de cel.
        '<td data-ph="logo" style="padding-right:16px;vertical-align:middle;">'
        '<img src="{{logo}}" alt="{{bedrijf}}" width="72" style="max-width:72px;height:auto;border-radius:10px;">'
        '</td>'
        f'<td style="border-left:2px solid {_ACCENT};padding-left:16px;vertical-align:middle;">'
        '<div style="font-weight:700;font-size:15px;">{{naam}}</div>'
        f'<div data-ph="functie" style="color:{_ACCENT};font-size:12.5px;font-weight:600;">{{{{functie}}}} · {{{{bedrijf}}}}</div>'
        '<div style="margin-top:7px;font-size:12px;color:#57606a;">'
        '<span data-ph="mobiel" style="margin-right:12px;">📱 {{mobiel}}</span>'
        '<span data-ph="email">✉️ {{email}}</span>'
        '</div></td></tr></table>'
    )


def _compact() -> str:
    return (
        '<div style="font-family:Arial,sans-serif;font-size:13px;color:#1f2328;">'
        f'<span style="font-weight:700;color:{_ACCENT};">{{{{naam}}}}</span>'
        '<span data-ph="functie" style="color:#57606a;"> — {{functie}}</span>'
        '<span style="color:#8b949e;"> | </span>'
        '<span data-ph="telefoon">{{telefoon}}</span>'
        '<span style="color:#8b949e;"> | </span>'
        '<span data-ph="email">{{email}}</span>'
        '</div>'
    )


def _disclaimer() -> str:
    # Zie _klassiek: de slotgroet komt uit het concept zelf, niet uit het blok.
    return (
        '<div style="font-family:Verdana,Arial,sans-serif;font-size:13px;color:#1f2328;">'
        f'<div style="font-weight:bold;font-size:14px;color:{_ACCENT};">{{{{naam}}}}</div>'
        '<div data-ph="functie" style="color:#57606a;font-size:12.5px;">{{functie}} · {{bedrijf}}</div>'
        '<div style="margin-top:6px;font-size:12px;color:#57606a;">'
        '<span data-ph="telefoon" style="margin-right:12px;">📞 {{telefoon}}</span>'
        '<span data-ph="email">✉️ {{email}}</span></div>'
        '</div>'
        '<div style="margin-top:12px;padding-top:8px;border-top:1px solid #e3e6ea;font-size:10.5px;color:#8b949e;max-width:520px;">'
        'Deze e-mail is vertrouwelijk en enkel bestemd voor de geadresseerde. Bent u niet de '
        'bedoelde ontvanger, verwittig dan de afzender en verwijder dit bericht.'
        '</div>'
    )


def _minimaal() -> str:
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#1f2328;line-height:1.5;">'
        '<div style="font-weight:700;font-size:14px;">{{naam}}</div>'
        '<div data-ph="functie" style="color:#57606a;">{{functie}}</div>'
        '<div data-ph="telefoon" style="margin-top:4px;">{{telefoon}}</div>'
        '<div data-ph="email"><a href="mailto:{{email}}" style="color:' + _ACCENT + ';text-decoration:none;">{{email}}</a></div>'
        '</div>'
    )


def _met_social() -> str:
    return (
        '<table cellpadding="0" cellspacing="0" border="0" style="font-family:\'Segoe UI\',Arial,sans-serif;font-size:13px;color:#1f2328;">'
        '<tr><td style="vertical-align:top;">'
        f'<div style="font-weight:700;font-size:15px;color:{_ACCENT};">{{{{naam}}}}</div>'
        '<div data-ph="functie" style="color:#57606a;font-size:12.5px;">{{functie}} · {{bedrijf}}</div>'
        '<div style="margin-top:7px;font-size:12px;color:#57606a;">'
        '<span data-ph="telefoon" style="margin-right:12px;">📞 {{telefoon}}</span>'
        '<span data-ph="email">✉️ {{email}}</span>'
        '</div>'
        '<div style="margin-top:9px;font-size:12px;">'
        f'<a href="{{{{linkedin}}}}" data-ph="linkedin" style="color:{_ACCENT};text-decoration:none;margin-right:10px;">LinkedIn</a>'
        f'<a href="{{{{website}}}}" data-ph="website" style="color:{_ACCENT};text-decoration:none;">Website</a>'
        '</div>'
        '</td></tr></table>'
    )


def _tweetalig() -> str:
    return (
        '<table cellpadding="0" cellspacing="0" border="0" style="font-family:Verdana,Arial,sans-serif;font-size:13px;color:#1f2328;">'
        f'<tr><td style="border-left:3px solid {_ACCENT};padding-left:14px;">'
        f'<div style="font-weight:bold;font-size:14px;color:{_ACCENT};">{{{{naam}}}}</div>'
        '<div data-ph="functie" style="color:#57606a;font-size:12.5px;">{{functie}}</div>'
        '<div style="font-weight:600;font-size:12.5px;margin-top:2px;">{{bedrijf}}</div>'
        '<div style="margin-top:8px;font-size:12px;color:#57606a;">'
        '<span data-ph="telefoon" style="margin-right:12px;">📞 {{telefoon}}</span>'
        '<span data-ph="email">✉️ {{email}}</span></div>'
        '<div style="margin-top:10px;font-size:10px;color:#8b949e;line-height:1.5;">'
        'Denk aan het milieu voor u deze e-mail afdrukt. · '
        'Pensez à l’environnement avant d’imprimer. · '
        'Please consider the environment before printing.'
        '</div>'
        '</td></tr></table>'
    )


def _met_foto() -> str:
    return (
        '<table cellpadding="0" cellspacing="0" border="0" style="font-family:\'Segoe UI\',Arial,sans-serif;font-size:13px;color:#1f2328;">'
        '<tr>'
        # Hele fotocel verdwijnt netjes als de gebruiker geen M365-profielfoto heeft.
        '<td data-ph="foto" style="padding-right:16px;vertical-align:middle;">'
        '<img src="{{foto}}" alt="{{naam}}" width="64" style="width:64px;height:64px;border-radius:50%;object-fit:cover;">'
        '</td>'
        f'<td style="border-left:2px solid {_ACCENT};padding-left:16px;vertical-align:middle;">'
        '<div style="font-weight:700;font-size:15px;">{{naam}}</div>'
        f'<div data-ph="functie" style="color:{_ACCENT};font-size:12.5px;font-weight:600;">{{{{functie}}}} · {{{{bedrijf}}}}</div>'
        '<div style="margin-top:7px;font-size:12px;color:#57606a;">'
        '<span data-ph="telefoon" style="margin-right:12px;">📞 {{telefoon}}</span>'
        '<span data-ph="email">✉️ {{email}}</span>'
        '</div></td></tr></table>'
    )


def _merk_social() -> str:
    # Toont de brand-kit-integratie: het domein-logo ({{logo}}) plus de merk-social-rij
    # ({{social}}, e-mailveilige profiel-links in de accentkleur). Beide komen uit de brand
    # kit van het verzenddomein — één sjabloon, per merk het juiste logo en de juiste links.
    return (
        '<table cellpadding="0" cellspacing="0" border="0" style="font-family:\'Segoe UI\',Arial,sans-serif;font-size:13px;color:#1f2328;">'
        '<tr>'
        '<td data-ph="logo" style="padding-right:16px;vertical-align:middle;">'
        '<img src="{{logo}}" alt="{{bedrijf}}" style="max-height:40px;max-width:120px;">'
        '</td>'
        f'<td style="border-left:2px solid {_ACCENT};padding-left:16px;vertical-align:middle;">'
        '<div style="font-weight:700;font-size:15px;">{{naam}}</div>'
        '<div data-ph="functie" style="color:#57606a;font-size:12.5px;">{{functie}} · {{bedrijf}}</div>'
        '<div style="margin-top:7px;font-size:12px;color:#57606a;">'
        '<span data-ph="telefoon" style="margin-right:12px;">📞 {{telefoon}}</span>'
        '<span data-ph="email">✉️ {{email}}</span>'
        '</div>'
        '<div data-ph="social" style="margin-top:8px;font-size:12px;">{{social}}</div>'
        '</td></tr></table>'
    )


def builtins() -> list[dict]:
    """De ingebouwde sjablonen (bron van waarheid in Python)."""
    return [
        {"key": "klassiek", "name": _("Klassiek"),
         "desc": _("Naam met accentbalk, functie en contactregel"), "html": _klassiek()},
        {"key": "foto", "name": _("Met profielfoto"),
         "desc": _("Ronde M365-profielfoto naast naam en functie"), "html": _met_foto()},
        {"key": "modern", "name": _("Modern met logo"),
         "desc": _("Logo naast naam en functie — strak en zakelijk"), "html": _modern_logo()},
        {"key": "compact", "name": _("Compact"),
         "desc": _("Alles op één regel — minimalistisch"), "html": _compact()},
        {"key": "minimaal", "name": _("Minimaal"),
         "desc": _("Naam, functie en contact onder elkaar — rustig"), "html": _minimaal()},
        {"key": "social", "name": _("Met social links"),
         "desc": _("Contactregel plus LinkedIn- en website-link"), "html": _met_social()},
        {"key": "merk_social", "name": _("Merk + social (brand kit)"),
         "desc": _("Domein-logo en merk-social-rij uit de brand kit"), "html": _merk_social()},
        {"key": "tweetalig", "name": _("Tweetalig (NL/FR/EN)"),
         "desc": _("Met een drietalige milieu-voetregel"), "html": _tweetalig()},
        {"key": "disclaimer", "name": _("Met disclaimer"),
         "desc": _("Volledige handtekening met vertrouwelijkheidsclausule"), "html": _disclaimer()},
    ]


def gallery(org=None) -> list[dict]:
    """De sjablonen voor de galerij op Beheer → Handtekeningen: de ingebouwde ontwerpen
    plus de door deze organisatie geïmporteerde ('custom') sjablonen.

    Elk item: {key, name, desc, html, source, id}. Built-ins hebben source='builtin'
    (id=None); custom-sjablonen krijgen een collision-vrije key 'c<id>' zodat ze in de
    front-end naast de built-in-slugs kunnen bestaan.
    """
    items = [{**t, "source": "builtin", "id": None} for t in builtins()]
    org_id = getattr(org, "id", None)
    if org_id is None:
        return items
    try:
        from app.extensions import db
        from app.models import SignatureTemplate

        rows = (
            db.session.query(SignatureTemplate)
            .filter_by(organization_id=org_id)
            .order_by(SignatureTemplate.id.desc())
            .all()
        )
    except Exception:  # noqa: BLE001 — tabel nog niet gemigreerd → alleen de built-ins
        return items
    custom = [
        {"key": f"c{r.id}", "name": r.name, "desc": r.description or "",
         "html": r.html, "source": "custom", "id": r.id}
        for r in rows
    ]
    return custom + items
