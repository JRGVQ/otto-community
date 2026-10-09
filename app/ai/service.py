"""High-level AI service: categorisation, draft generation, style learning.

Uses an adapter's native `categorize`/`draft` when available (the stub), otherwise
builds prompts and calls `complete()` (real LLM providers).
"""
from __future__ import annotations

import json

from app.ai.guardrails import is_sensitive, wrap_as_data
from app.ai.knowledge import retrieve
from app.ai.prompts import categorize_system, draft_system, draft_user, followup_user
from app.ai.style import learn_from_sent, profile_to_text
from app.extensions import release_db_connection


class LLMService:
    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self._usage = (0, 0)  # accumulated (input, output) tokens since last pop_usage()

    @property
    def is_stub(self) -> bool:
        return getattr(self.adapter, "name", "") == "stub"

    @property
    def model_name(self) -> str:
        return getattr(self.adapter, "model_name", "") or getattr(self.adapter, "name", "stub")

    def _accumulate(self) -> None:
        """Fold the adapter's last-call token usage into the running total."""
        last = getattr(self.adapter, "last_usage", None)
        if last:
            self._usage = (self._usage[0] + last[0], self._usage[1] + last[1])

    def pop_usage(self) -> tuple[int, int]:
        """Return accumulated (input, output) tokens and reset the counter."""
        usage, self._usage = self._usage, (0, 0)
        return usage

    # ── Categorisation ────────────────────────────────────────────────────
    def categorize(self, subject: str, sender_email: str, body: str, categories: list[dict]) -> dict:
        if hasattr(self.adapter, "categorize"):
            result = self.adapter.categorize(subject, sender_email, body, categories)
        else:
            lines = "\n".join(f"{c['key']}: {c['name']} — {c.get('ai', '')}" for c in categories)
            release_db_connection()
            raw = self.adapter.complete(
                categorize_system(lines), wrap_as_data(f"{subject}\n{body}"), json=True, max_tokens=120,
            )
            self._accumulate()
            result = self._parse_category(raw, categories)
        result["sensitive"] = is_sensitive(f"{subject} {body}")
        return result

    # ── Draft generation ──────────────────────────────────────────────────
    def draft(self, sender, subject, body, tone, style_profile, lang="antwoord in Nederlands", signature="Met vriendelijke groeten", org=None, intent=None, thread=None, history=None, account=None) -> str:
        style = profile_to_text(style_profile)
        if hasattr(self.adapter, "draft"):
            # Stub path: deterministic, offline — no knowledge retrieval (and no Graph call).
            return self.adapter.draft(sender, subject, body, tone, style, signature, intent=intent, thread=thread)
        # Ground the reply in the org's connected documentation when available (no-ops to []).
        # `account` bepaalt de scope van de kennisbank: zonder mailbox alleen de
        # organisatiebrede blokken, zodat wat support@ weet niet in een persoonlijk
        # concept opduikt.
        knowledge = retrieve(org, subject, body, account=account) if org is not None else []
        # Vrije momenten uit de agenda — alleen als de mail erom vraagt en het postvak het
        # aan heeft staan. Geeft [] terug zodra er iets niet lukt (zie app/scheduling.py).
        from app.scheduling import slots_for

        slots = slots_for(account, subject, body)
        # Hier is al het databasewerk gedaan (kennisbank, agenda, stijl) en staat alles
        # als platte tekst klaar. Laat de verbinding los vóór het model: die call duurt
        # seconden tot minuten en zou anders een connectie uit de pool bezet houden.
        release_db_connection()
        out = self.adapter.complete(
            draft_system(style, lang, tone, knowledge=knowledge, slots=slots),
            draft_user(sender, subject, body, signature, intent=intent, thread=thread,
                       history=history), max_tokens=600,
        ).strip()
        self._accumulate()
        return out

    def followup(self, subject, body, tone, style_profile, lang="antwoord in Nederlands", signature="Met vriendelijke groeten", org=None) -> str:
        """Draft a polite reminder for a sent mail that got no reply."""
        style = profile_to_text(style_profile)
        if hasattr(self.adapter, "followup"):
            return self.adapter.followup(subject, body, tone, style, signature)
        release_db_connection()
        out = self.adapter.complete(
            draft_system(style, lang, tone),
            followup_user(subject, body, signature), max_tokens=400,
        ).strip()
        self._accumulate()
        return out

    def learn_style(self, sent_texts: list[str]) -> dict:
        return learn_from_sent(sent_texts)

    def analyze_style(self, sent_texts: list[str]) -> dict:
        """Rijke stijlanalyse voor de knop "Leer uit mijn verzonden mail": een leesbare
        beschrijving + begroeting/afsluiting/toon/voorbeelden. Gebruikt een echt AI-model
        wanneer beschikbaar; anders de heuristiek (describe_from_sent), zodat het vak nooit
        leeg blijft en de €0/eigen-sleutel-plannen niets kosten."""
        from app.ai.prompts import style_analysis_system, style_analysis_user
        from app.ai.style import describe_from_sent, top_reply

        texts = [top_reply(t) for t in (sent_texts or [])]
        texts = [t for t in texts if t and t.strip()]
        if not texts:
            return {}
        if hasattr(self.adapter, "analyze_style"):
            return self.adapter.analyze_style(texts)
        if self.is_stub:
            return describe_from_sent(sent_texts)
        release_db_connection()
        raw = self.adapter.complete(
            style_analysis_system(), style_analysis_user(texts[:40]), json=True, max_tokens=500,
        )
        self._accumulate()
        return self._parse_style(raw, sent_texts)

    def _parse_style(self, raw: str, sent_texts: list[str]) -> dict:
        """Parse de JSON-stijlanalyse; val terug op de heuristiek bij onbruikbare uitvoer."""
        from app.ai.style import describe_from_sent

        try:
            data = json.loads(raw)
        except Exception:
            return describe_from_sent(sent_texts)
        tone = data.get("tone")
        if tone not in ("neutraal", "formeel", "kort", "vriendelijk"):
            tone = "neutraal"
        examples = [e.strip() for e in (data.get("examples") or []) if isinstance(e, str) and e.strip()]
        return {
            "description": (data.get("description") or "").strip(),
            "greeting": (data.get("greeting") or "").strip()[:64] or None,
            "signoff": (data.get("signoff") or "").strip()[:64] or None,
            "tone": tone,
            "examples": examples[:5],
        }

    # ── helpers ───────────────────────────────────────────────────────────
    def _parse_category(self, raw: str, categories: list[dict]) -> dict:
        keys = {c["key"] for c in categories}
        try:
            data = json.loads(raw)
            key = data.get("category_key")
            conf = int(data.get("confidence", 70))
        except Exception:
            key, conf = raw.strip().split()[0] if raw.strip() else None, 70
        if key not in keys:
            key = "te_beantwoorden" if "te_beantwoorden" in keys else next(iter(keys), None)
        return {"category_key": key, "confidence": conf}
