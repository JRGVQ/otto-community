"""Hoe een Otto-categorie in Outlook heet.

Outlook sorteert categorieën alfabetisch en biedt geen eigen volgorde. Een bedrijf dat
op belangrijkheid wil kunnen sorteren, zet daarom een nummer voor de naam: "1. Te
beantwoorden", "2. FYI". Dat is per organisatie in of uit te schakelen.

Het nummer volgt `Category.position` — dezelfde volgorde als in Otto zelf, zodat je maar
op één plek sleept en Outlook meebeweegt.
"""
from __future__ import annotations

import re

# Herkent een reeds genummerde naam, zodat er nooit "1. 2. Naam" ontstaat.
_NUMBERED = re.compile(r"^\s*\d+\.\s+")


def strip_number(name: str) -> str:
    """'2. Te beantwoorden' → 'Te beantwoorden'."""
    return _NUMBERED.sub("", name or "").strip()


def numbering_enabled(org) -> bool:
    return bool(org is not None and getattr(org, "outlook_category_numbering", False))


def label_for(category, org=None) -> str:
    """De naam waaronder deze categorie in Outlook moet staan.

    Zonder nummering gewoon de naam; met nummering voorafgegaan door de plaats in de
    lijst. De positie is 0-gebaseerd, dus er komt 1 bij voor een leesbaar nummer.
    """
    base = strip_number(getattr(category, "name", "") or "")
    if not numbering_enabled(org if org is not None else getattr(category, "organization", None)):
        return base
    position = getattr(category, "position", 0) or 0
    return f"{position + 1}. {base}"


def base_names(org) -> set[str]:
    """De kale (ongenummerde) namen van alle Otto-categorieën van deze organisatie.

    Hiermee herkent de write-back een Otto-label op een bericht ongeacht de vorm
    ("Te beantwoorden", "1. Te beantwoorden", of een oud nummer na herschikken),
    zodat hercategoriseren het oude label VERVANGT in plaats van erbij te plakken —
    één Otto-categorie per bericht, terwijl de eigen tags van de gebruiker blijven."""
    out = set()
    for cat in getattr(org, "categories", []) or []:
        base = strip_number(cat.name or "")
        if base:
            out.add(base)
    return out


def stale_variants(org) -> dict[str, tuple[str, str | None]]:
    """{verouderde labelnaam → (juiste labelnaam, kleur)} voor deze organisatie.

    Somt per categorie alle vormen op waarin het label ooit op mail kan staan
    (ongenummerd én elk plausibel nummer 1..N) en beeldt ze af op de huidige juiste
    vorm. Voedt de opruimsweep die bestaande mail her-tagt na een nummering-switch
    of hernoeming."""
    cats = sorted(getattr(org, "categories", []) or [], key=lambda c: c.position or 0)
    n = len(cats)
    out: dict[str, tuple[str, str | None]] = {}
    for cat in cats:
        base = strip_number(cat.name or "")
        if not base:
            continue
        current = label_for(cat, org)
        for variant in (base, *[f"{i}. {base}" for i in range(1, n + 1)]):
            if variant != current:
                out[variant] = (current, cat.color)
    return out


def all_labels(org, *, numbered: bool) -> list[str]:
    """Alle categorienamen van deze organisatie in één van beide vormen.

    Gebruikt om na het omzetten de niet meer gebruikte vorm uit Outlooks categorielijst
    te halen, zodat je er niet twee naast elkaar overhoudt.
    """
    out = []
    for cat in getattr(org, "categories", []) or []:
        base = strip_number(cat.name or "")
        if not base:
            continue
        out.append(f"{(cat.position or 0) + 1}. {base}" if numbered else base)
    return out
