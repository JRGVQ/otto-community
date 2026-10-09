"""Derive a compact writing-style profile from the user's sent mail."""
from __future__ import annotations

import re

# Regels die de start van geciteerde reply-geschiedenis markeren. Alles vanaf hier is
# niet meer wat de gebruiker zélf schreef, dus voor stijl knippen we daar af.
_QUOTE_MARKERS = re.compile(
    r"^\s*(?:>|"
    r"op .+ schreef|"
    r"on .+ wrote|"
    r"le .+ a écrit|"
    r"-{2,}\s*oorspronkelijk bericht|"
    r"-{2,}\s*original message|"
    r"(?:van|from|de|à|to|aan|verzonden|sent|onderwerp|subject)\s*:.*)",
    re.IGNORECASE,
)

_SIGNOFF_HINTS = (
    "met vriendelijke groeten", "met vriendelijke groet", "vriendelijke groeten",
    "hartelijke groeten", "beste groeten", "groeten", "groetjes", "groet",
    "mvg", "hoogachtend", "alvast bedankt", "bedankt", "dank", "tot binnenkort",
    "tot snel", "cordialement", "bien à vous", "kind regards", "best regards", "regards",
)


def top_reply(text: str) -> str:
    """Enkel wat de gebruiker zelf boven de geciteerde geschiedenis schreef."""
    out = []
    for line in (text or "").splitlines():
        if _QUOTE_MARKERS.match(line):
            break
        out.append(line)
    return "\n".join(out).strip()


def profile_to_text(style_profile) -> str:
    if not style_profile:
        return "vriendelijk-zakelijk, beknopt, Nederlands"
    bits = []
    # De geleerde stijlbeschrijving eerst — dat is de rijkste sturing voor het model.
    description = (getattr(style_profile, "raw_profile", None) or {}).get("description")
    if description and description.strip():
        bits.append(description.strip())
    if style_profile.tone:
        bits.append(f"toon {style_profile.tone}")
    if style_profile.greeting:
        bits.append(f"begroeting '{style_profile.greeting}'")
    if style_profile.signoff:
        bits.append(f"afsluiting '{style_profile.signoff}'")
    # Learned example phrasings (few-shot) keep drafts in the user's own voice.
    examples = (getattr(style_profile, "raw_profile", None) or {}).get("examples")
    if examples:
        bits.append("schrijf in dezelfde stijl als deze voorbeelden: " + " | ".join(examples[:3]))
    text = ", ".join(bits) or "vriendelijk-zakelijk, beknopt"
    # The user's "standaard antwoord" — a default approach applied to every draft.
    default_reply = getattr(style_profile, "default_reply", None)
    if default_reply and default_reply.strip():
        text += f". Standaard aanpak van de gebruiker: {default_reply.strip()}"
    return text


def learn_from_sent(sent_texts: list[str]) -> dict:
    """Heuristic style extraction: most common greeting/sign-off + example phrasings."""
    texts = [t for t in (sent_texts or []) if t and t.strip()]
    if not texts:
        return {"tone": "neutraal", "greeting": "Hallo", "signoff": "Groeten", "examples": []}
    firsts = [t.strip().split("\n", 1)[0].strip() for t in texts]
    greeting = max(set(firsts), key=firsts.count) if firsts else "Hallo"
    # A few short representative sentences become few-shot examples for the prompt.
    examples = []
    for t in texts:
        for line in t.splitlines():
            line = line.strip()
            if 25 <= len(line) <= 140 and not line.lower().startswith(("hallo", "hi", "hey", "beste", "geachte")):
                examples.append(line)
                break
    return {
        "tone": "neutraal",
        "greeting": (greeting.split()[0] if greeting else "Hallo"),
        "signoff": "Groeten",
        "examples": examples[:5],
    }


def describe_from_sent(sent_texts: list[str]) -> dict:
    """Rijkere, LLM-loze stijlanalyse: naast begroeting/afsluiting/voorbeelden ook een
    leesbare beschrijving en een ingeschatte toon. Gebruikt als er geen betaald AI-model
    beschikbaar is (stub / eigen-sleutel-plan zonder sleutel), zodat het vak nooit leeg blijft."""
    tops = [top_reply(t) for t in (sent_texts or [])]
    tops = [t for t in tops if t and t.strip()]
    if not tops:
        return {"description": "", "tone": "neutraal", "greeting": "Hallo",
                "signoff": "Groeten", "examples": []}
    base = learn_from_sent(tops)

    joined = "\n".join(tops).lower()
    woorden = re.findall(r"\b\w+\b", joined) or [""]
    # Aanspreekvorm: overheersen 'u/uw' of 'je/jij/jou'?
    formeel = sum(joined.count(w) for w in (" u ", " uw ", " uw,"))
    informeel = sum(joined.count(w) for w in (" je ", " jij ", " jou ", " jouw "))
    tutoyeert = informeel >= formeel
    # Lengte: gemiddeld aantal woorden per mail.
    avg_len = len(woorden) / max(1, len(tops))
    if avg_len < 40:
        tone, lengte = ("kort", "kort en bondig")
    elif not tutoyeert:
        tone, lengte = ("formeel", "eerder formeel")
    elif avg_len > 90:
        tone, lengte = ("vriendelijk", "uitgebreider")
    else:
        tone, lengte = ("neutraal", "to the point")

    # Afsluiting: laatste betekenisvolle regel die op een groet lijkt.
    signoff = base["signoff"]
    for t in tops:
        for line in reversed([ln.strip() for ln in t.splitlines() if ln.strip()]):
            low = line.lower().rstrip(",.!")
            if any(low == h or low.startswith(h) for h in _SIGNOFF_HINTS) and len(line) <= 40:
                signoff = line.rstrip(",.")
                break
        else:
            continue
        break

    aanspreek = "tutoyeert (je/jij)" if tutoyeert else "gebruikt de u-vorm"
    beschrijving = (
        f"Je antwoordt {lengte}, {aanspreek}. Je opent meestal met “{base['greeting']}” "
        f"en sluit af met “{signoff}”."
    )
    return {
        "description": beschrijving,
        "tone": tone,
        "greeting": base["greeting"],
        "signoff": signoff,
        "examples": base["examples"],
    }
