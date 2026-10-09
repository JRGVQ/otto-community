"""Cloud-modus: een Exchange transport-regel die de handtekening server-side toevoegt.

Microsoft Graph heeft GEEN transport-rule API, dus Otto zet de regel niet zelf. In plaats
daarvan is Otto **generator + detector**: het vertaalt een handtekening naar een e-mail-
disclaimer met Exchange-AD-tokens (%%DisplayName%% …) en levert een kant-en-klaar
PowerShell-script (`New-TransportRule`) dat de beheerder in Exchange Online plakt. De regel
krijgt een uitzondering op Otto's verborgen marker, zodat mail die Otto (of Outlook) al
tekende niet nóg een handtekening krijgt (combo-modus).

Eerlijke grenzen (bewust): een transport-regel plakt onderaan, werkt met Exchange-tokens
i.p.v. Otto's eigen variabelen (custom velden en {{foto}} kunnen niet mee), embedt geen
verborgen (CID) afbeeldingen, en werkt de map 'Verzonden items' NIET bij — dat laatste
vraagt een mailrelay, die hier bewust niet gebruikt wordt.
"""
from __future__ import annotations

import re

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")

# Otto-placeholder → Exchange transport-rule AD-token. Alleen deze kunnen server-side mee;
# eigen variabelen en {{foto}} hebben geen equivalent (zie unmappable_placeholders).
PLACEHOLDER_TO_TOKEN = {
    "naam": "%%DisplayName%%",
    "voornaam": "%%FirstName%%",
    "achternaam": "%%LastName%%",
    "functie": "%%Title%%",
    "afdeling": "%%Department%%",
    "telefoon": "%%PhoneNumber%%",
    "mobiel": "%%MobileNumber%%",
    "email": "%%Email%%",
    "bedrijf": "%%Company%%",
    "locatie": "%%Office%%",
}

DEFAULT_MARKER = "otto-sig-v1"


def unmappable_placeholders(html: str | None) -> list[str]:
    """De {{keys}} in de handtekening die geen Exchange-token hebben (waarschuwing voor
    de beheerder: die worden server-side niet ingevuld)."""
    seen = []
    for m in _PLACEHOLDER_RE.finditer(html or ""):
        key = m.group(1)
        if key not in PLACEHOLDER_TO_TOKEN and key not in seen:
            seen.append(key)
    return seen


def disclaimer_html(html: str | None) -> str:
    """De handtekening-HTML omgezet naar een transport-disclaimer: mappable placeholders
    worden Exchange-tokens; onmapbare worden verwijderd (die kunnen server-side niet). De
    verborgen data-ph-elementen blijven staan (Exchange strlooit ze niet, maar ze zijn
    onzichtbaar); externe afbeeldingen blijven — CID/interne /media werken hier niet."""
    def _sub(m: re.Match) -> str:
        return PLACEHOLDER_TO_TOKEN.get(m.group(1), "")

    return _PLACEHOLDER_RE.sub(_sub, html or "").strip()


def otto_marker_html(marker: str = DEFAULT_MARKER) -> str:
    """Het verborgen merkteken dat Otto in zijn eigen concepten zet; de transport-regel
    slaat mail met dit merkteken over (geen dubbele handtekening in combo-modus)."""
    return f'<span style="display:none;font-size:0;line-height:0;">{marker}</span>'


def powershell_script(rule_name: str, disclaimer: str, marker: str = DEFAULT_MARKER,
                      message_type: str | None = None) -> str:
    """Een kant-en-klaar Exchange Online PowerShell-script voor de beheerder.

    Gebruikt een single-quoted here-string voor de HTML (geen variabele-interpolatie), met
    verdubbelde apostroffen zoals PowerShell dat vereist. De uitzondering op de marker zorgt
    dat Otto-/Outlook-getekende mail niet nóg een handtekening krijgt.

    `message_type` (optioneel) beperkt de regel tot één berichttype via
    `-MessageTypeMatches`, bv. "Calendaring" voor vergader-/Teams-uitnodigingen."""
    safe_html = (disclaimer or "").replace("'", "''")
    type_line = f"  -MessageTypeMatches {message_type} `\n" if message_type else ""
    return (
        "# Verbind eerst met Exchange Online (eenmalig):\n"
        "#   Install-Module ExchangeOnlineManagement -Scope CurrentUser\n"
        "#   Connect-ExchangeOnline\n"
        f"$html = @'\n{safe_html}\n'@\n"
        f"New-TransportRule -Name '{rule_name}' `\n"
        "  -ApplyHtmlDisclaimerLocation Append `\n"
        "  -ApplyHtmlDisclaimerText $html `\n"
        "  -ApplyHtmlDisclaimerFallbackAction Wrap `\n"
        f"{type_line}"
        f"  -ExceptIfSubjectOrBodyContainsWords '{marker}' `\n"
        "  -SentToScope NotInOrganization\n"
    )


def meeting_powershell_script(rule_name: str, disclaimer: str, marker: str = DEFAULT_MARKER) -> str:
    """Zoals powershell_script, maar de regel treft alléén vergader-/Teams-uitnodigingen
    (`-MessageTypeMatches Calendaring`). Zo krijgt elke uitnodiging server-side dezelfde
    huisstijl-disclaimer — de Exchange-native tegenhanger van 'Teams meeting branding'
    (Otto verzendt zelf niets; Exchange plakt de disclaimer)."""
    return powershell_script(rule_name, disclaimer, marker, message_type="Calendaring")


def instructions() -> list[str]:
    """Korte stappen voor de beheerder (naast het script)."""
    return [
        "Open Exchange Online PowerShell (of het Exchange-beheercentrum → E-mailstroom → Regels).",
        "Plak en voer het script hieronder uit — het maakt de regel 'Otto cloud-handtekening'.",
        "Controleer in het beheercentrum dat de regel is ingeschakeld.",
        "Bevestig hieronder dat je de regel hebt toegepast.",
    ]
