"""Deterministic, dependency-free LLM stand-in.

Keeps every AI-driven screen working offline (the default `LLM_PROVIDER=stub`):
keyword categorisation + templated Dutch drafts per tone. Real adapters only need
to implement `complete()`; this one additionally provides native `categorize()` /
`draft()` so the demo is fully functional with no API key.
"""
from __future__ import annotations

from app.ai.base import LLMAdapter

_SPAM = ("gewonnen", "gefeliciteerd", "winnaar", "geclaimd", "claim uw", "klik hier", "!!!")
_NOTIF = ("no-reply", "noreply", "notifications", "notification", "aanmelding",
          "uitbetaling", "beveiligingsmelding", "connectieverzoek", "account@", "payout")
_NEWS = ("nieuwsbrief", "newsletter", "unsubscribe", "uitschrijven", "this week", "promo")
_FYI = ("notulen", "ter info", "fyi", "geen actie nodig", "in cc", "bijlage de notulen")

_GREETING = {"neutraal": "Hallo", "formeel": "Geachte heer/mevrouw", "kort": "Hoi", "vriendelijk": "Hey"}
# Name-less fallback sign-offs — the user's own signature (when set) is used instead,
# so no placeholder name ever leaks into a real user's draft.
_SIGNOFF = {
    "neutraal": "Met vriendelijke groeten,",
    "formeel": "Met vriendelijke groet,",
    "kort": "Groet,",
    "vriendelijk": "Warme groet,",
}


class StubAdapter(LLMAdapter):
    name = "stub"

    def complete(self, system: str, user: str, *, max_tokens: int = 800, json: bool = False) -> str:
        return ""  # heuristics live in categorize()/draft()

    # ── Native heuristics ─────────────────────────────────────────────────
    def categorize(self, subject: str, sender_email: str, body: str, categories: list[dict]) -> dict:
        keys = {c["key"] for c in categories}
        text = f"{subject} {body} {sender_email}".lower()

        def pick(key: str, conf: int):
            chosen = key if key in keys else next(iter(keys), "te_beantwoorden")
            return {"category_key": chosen, "confidence": conf}

        if any(w in text for w in _SPAM):
            return pick("spam", 96)
        if any(w in text for w in _NOTIF):
            return pick("meldingen", 88)
        if any(w in text for w in _NEWS):
            return pick("nieuwsbrief", 90)
        if any(w in text for w in _FYI):
            return pick("fyi", 80)
        return pick("te_beantwoorden", 78)

    def draft(self, sender: str, subject: str, body: str, tone: str, style: str, signature: str | None = None, intent: str | None = None, thread: str | None = None) -> str:
        tone = tone if tone in _GREETING else "neutraal"
        first = (sender or "").split()[0] if sender else "daar"
        greeting = _GREETING[tone]
        opener = greeting if tone == "formeel" else f"{greeting} {first}"
        # When the user steered this reply, reflect their intent instead of the generic line
        # so the guided-reply path is observable offline (and in tests).
        if intent and intent.strip():
            middle = intent.strip()
        else:
            middle = {
                "neutraal": f"Bedankt voor je bericht over ‘{subject}’. Ik kom hier zo snel mogelijk bij je op terug.",
                "formeel": f"Dank voor uw bericht betreffende ‘{subject}’. Ik kom hier spoedig bij u op terug.",
                "kort": "Dank, ik kom er snel op terug.",
                "vriendelijk": "Dank je wel voor je bericht! Ik duik er even in en laat snel iets weten.",
            }[tone]
        signoff = signature.strip() if signature else _SIGNOFF[tone]
        return f"{opener},\n\n{middle}\n\n{signoff}"

    def followup(self, subject: str, body: str, tone: str, style: str, signature: str | None = None) -> str:
        tone = tone if tone in _GREETING else "neutraal"
        greeting = _GREETING[tone]
        opener = greeting if tone == "formeel" else greeting
        line = (f"Ik wilde even vriendelijk opvolgen op mijn eerdere bericht over ‘{subject}’. "
                "Heb je hier al naar kunnen kijken? Laat gerust weten of je nog iets nodig hebt.")
        signoff = signature.strip() if signature else _SIGNOFF[tone]
        return f"{opener},\n\n{line}\n\n{signoff}"
