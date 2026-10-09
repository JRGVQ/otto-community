"""Het Analyse-bord: een register van losse widgets in plaats van één vaste pagina.

Elke widget is een kaart met een eigen contextfunctie en een eigen partial onder
templates/app/user/_widgets/. Een nieuwe analytic toevoegen = één partial schrijven,
één build-functie, en één regel in WIDGETS — de pagina, de periode-selector en het
herschikken (volgorde/verbergen per gebruiker, zie users.analytics_layout) doen de
rest vanzelf.

Alle widgets rekenen op dezelfde momentopname (berichten en categorieën van de
gebruiker binnen de gekozen periode), zodat de kaarten onderling altijd consistent
zijn: elke builder krijgt (msgs, days, cats) aangereikt en doet zelf geen extra
queries op die gegevens.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, time as _time, timedelta

from flask_babel import format_decimal
from flask_babel import gettext as _
from flask_babel import lazy_gettext as _l
from flask_login import current_user

from app.constants import DRAFT_CONCEPT, DRAFT_DISCARDED, DRAFT_SENT
from app.domains import domain_of
from app.extensions import db
from app.models.base import utcnow
from app.web.context._base import _categories, _category_counts, _human_minutes, _messages

# ── Periodes ─────────────────────────────────────────────────────────────────
PERIODS = [
    {"key": "7", "days": 7, "label": _l("7 dagen")},
    {"key": "30", "days": 30, "label": _l("30 dagen")},
    {"key": "90", "days": 90, "label": _l("90 dagen")},
    {"key": "alles", "days": None, "label": _l("Alles")},
]
DEFAULT_PERIOD = "30"


def _period(period_key: str) -> dict:
    return next((p for p in PERIODS if p["key"] == period_key), None) \
        or next(p for p in PERIODS if p["key"] == DEFAULT_PERIOD)


def _in_period(msgs, days: int | None) -> list:
    if days is None:
        return list(msgs)
    # Op dagGRENS uitgelijnd ("7 dagen" = vandaag + de 6 volle dagen ervoor), zodat de
    # volume-trend (die per kalenderdag bucket) exact dezelfde verzameling telt als de
    # kerncijfers — een tijdstip-cutoff liet mail uit de gedeeltelijke eerste dag wel
    # meetellen maar buiten de staafjes vallen.
    cutoff = datetime.combine(utcnow().date() - timedelta(days=days - 1), _time.min)
    # Berichten zonder received_at (oude seed-rijen) tellen mee: liever iets te veel
    # tonen dan mail stilletjes uit de cijfers laten vallen.
    return [m for m in msgs if m.received_at is None or m.received_at >= cutoff]


# ── Gedeelde tellingen (één waarheid per begrip, zie de module-docstring) ────
def _draft_status_counts(msgs) -> dict[str, int]:
    counts = {DRAFT_CONCEPT: 0, DRAFT_SENT: 0, DRAFT_DISCARDED: 0}
    for m in msgs:
        if m.draft and m.draft.status in counts:
            counts[m.draft.status] += 1
    return counts


def _domain_counts(msgs) -> Counter:
    # domain_of (app/domains.py) normaliseert ook "Naam <adres>"-vormen en mailto:-junk.
    counter = Counter()
    for m in msgs:
        domain = domain_of(m.sender_email or "")
        if domain:
            counter[domain] += 1
    return counter


# ── Widget-bouwers ───────────────────────────────────────────────────────────
def _build_kerncijfers(msgs, days, cats):
    counts = _draft_status_counts(msgs)
    return {
        "total": len(msgs),
        "unread": sum(1 for m in msgs if m.unread),
        "concept": counts[DRAFT_CONCEPT],
        "verzonden": counts[DRAFT_SENT],
    }


def _build_per_categorie(msgs, days, cats):
    counts = _category_counts(cats, msgs)
    total = len(msgs)
    denom = total or 1
    by_cat = sorted(
        ({"name": c.name, "color": c.color, "count": counts.get(c.id, 0),
          "pct": round(counts.get(c.id, 0) * 100 / denom)} for c in cats),
        key=lambda x: x["count"], reverse=True,
    )
    return {"total": total, "by_cat": by_cat, "uncategorised": total - sum(counts.values())}


def _build_volume_trend(msgs, days, cats):
    """Mail per dag (korte periodes) of per week (lang/alles), oudste eerst."""
    dated = [m.received_at for m in msgs if m.received_at]
    if not dated:
        return {"buckets": [], "max": 1, "unit": "dag"}
    per_week = days is None or days > 35
    buckets: list[dict] = []
    if per_week:
        counter = Counter(d.date() - timedelta(days=d.weekday()) for d in dated)
        keys = sorted(counter)[-16:]  # hooguit 16 weekstaven — daarna zegt de vorm niets meer
        buckets = [{"label": k.strftime("%d/%m"), "count": counter[k]} for k in keys]
        unit = _("week")
    else:
        end = utcnow().date()
        start = end - timedelta(days=(days or 7) - 1)  # zelfde grens als _in_period
        counter = Counter(d.date() for d in dated)
        day = start
        while day <= end:
            buckets.append({"label": day.strftime("%d/%m"), "count": counter.get(day, 0)})
            day += timedelta(days=1)
        unit = _("dag")
    return {"buckets": buckets, "max": max((b["count"] for b in buckets), default=1) or 1,
            "unit": unit}


def _build_drukste_uren(msgs, days, cats):
    counter = Counter(m.received_at.hour for m in msgs if m.received_at)
    hours = [{"hour": h, "count": counter.get(h, 0)} for h in range(24)]
    top = max(counter, key=counter.get) if counter else None
    return {"hours": hours, "max": max(counter.values(), default=1) or 1, "top_hour": top}


def _build_concept_benutting(msgs, days, cats):
    counts = _draft_status_counts(msgs)
    settled = counts[DRAFT_SENT] + counts[DRAFT_DISCARDED]
    return {
        "concept": counts[DRAFT_CONCEPT],
        "verzonden": counts[DRAFT_SENT],
        "verworpen": counts[DRAFT_DISCARDED],
        "total": counts[DRAFT_CONCEPT] + settled,
        "acceptance": round(counts[DRAFT_SENT] * 100 / settled) if settled else None,
    }


def _build_reactietijd(msgs, days, cats):
    from app.web.context.inbox import _median_reply_minutes

    window = days or 365
    median = _median_reply_minutes(msgs, days=window)
    cutoff = utcnow() - timedelta(days=window)
    n = sum(1 for m in msgs
            if m.draft and m.draft.status == DRAFT_SENT
            and m.draft.updated_at and m.draft.updated_at >= cutoff)
    return {"median_display": _human_minutes(median), "has_data": median is not None, "n": n}


def _build_opvolgingen(msgs, days, cats):
    from app.models import FollowUp

    rows = (
        db.session.query(FollowUp)
        .filter(FollowUp.organization_id == current_user.organization_id,
                FollowUp.user_id == current_user.id,
                FollowUp.status == DRAFT_CONCEPT)
        .order_by(FollowUp.last_sent_at.asc())
        .all()
    )
    today = utcnow().date()
    waits = [(today - f.last_sent_at.date()).days for f in rows if f.last_sent_at]
    return {
        "open": len(rows),
        "avg_wait": round(sum(waits) / len(waits)) if waits else None,
        "oldest": [{"subject": f.subject or _("(zonder onderwerp)"),
                    "days": (today - f.last_sent_at.date()).days if f.last_sent_at else None}
                   for f in rows[:3]],
    }


def _build_taken(msgs, days, cats):
    from app.models import Task

    tasks = db.session.query(Task).filter(Task.user_id == current_user.id).all()
    today = utcnow().date()
    cutoff = None if days is None else utcnow() - timedelta(days=days)
    done_in_period = sum(1 for t in tasks if t.done
                         and (cutoff is None or (t.updated_at and t.updated_at >= cutoff)))
    open_tasks = [t for t in tasks if not t.done]
    return {
        "open": len(open_tasks),
        "done": done_in_period,
        "overdue": sum(1 for t in open_tasks if t.due_date and t.due_date < today),
    }


def _build_top_afzenders(msgs, days, cats):
    top = [{"domain": d, "count": n} for d, n in _domain_counts(msgs).most_common(8)]
    return {"top_domains": top, "max": max((d["count"] for d in top), default=1)}


def _build_ruis_rapport(msgs, days, cats):
    """Wat het ruisfilter oplevert: mail die nooit een concept (en bij een
    afmeldheader ook geen AI-classificatie) heeft gekost."""
    noise_cats = [c for c in cats if c.is_noise]
    noise_ids = {c.id for c in noise_cats}
    noise = [m for m in msgs if m.category_id in noise_ids]
    # Alleen mail die het filter zelf wegzette (classified_by="filter") sloeg de
    # AI-classificatie over. Oudere rijen (NULL) en handmatig/AI-gesorteerde ruis
    # tellen bewust niet mee — liever een te laag besparingscijfer dan een verzonnen.
    zonder_ai = sum(1 for m in noise if m.classified_by == "filter")
    return {
        "has_noise_cat": bool(noise_cats),
        "count": len(noise),
        "zonder_ai": zonder_ai,
        "pct": round(len(noise) * 100 / (len(msgs) or 1)),
        "top": [{"domain": d, "count": n} for d, n in _domain_counts(noise).most_common(3)],
    }


def _build_ai_verbruik(msgs, days, cats):
    """AI-kosten van de organisatie DEZE MAAND, per model — bewust los van de
    periode-selector, want het budgetplafond werkt per kalendermaand."""
    from flask import current_app

    from app.ai.metering import budget_eur, cost_eur, month_rows

    org = current_user.organization
    fallback = current_app.config.get("LLM_MODEL") or "claude-sonnet-4-6"
    rows = sorted(month_rows(org), key=lambda r: (r.input_tokens or 0) + (r.output_tokens or 0),
                  reverse=True)
    per_model = [{
        "model": r.model or fallback,
        "tokens": (r.input_tokens or 0) + (r.output_tokens or 0),
        # In de cijfernotatie van de taal van de bezoeker (nl: 1.234.567).
        "tokens_display": format_decimal((r.input_tokens or 0) + (r.output_tokens or 0)),
        "cost": round(cost_eur(r.model or fallback, r.input_tokens or 0, r.output_tokens or 0), 2),
    } for r in rows]
    # Som van precies de getoonde rijen — geen tweede ai_usage-query via month_cost_eur,
    # en gegarandeerd consistent met wat op de kaart staat.
    total = round(sum(m["cost"] for m in per_model), 2)
    budget = budget_eur(org)
    cap = round(budget, 2) if budget else None
    return {
        "per_model": per_model,
        "total": total,
        "cap": cap,
        "pct": min(100, round(total * 100 / cap)) if cap else None,
    }


# ── Register ─────────────────────────────────────────────────────────────────
# Volgorde = standaardindeling. `key` is de identiteit in users.analytics_layout,
# dus hernoem een key nooit zomaar — dan raakt de opgeslagen indeling hem kwijt
# (onschadelijk, maar de widget springt terug naar zijn standaardplek).
# Optioneel `when(user)`: bepaalt of de widget voor deze gebruiker bestaat (b.v.
# org-brede kosten alleen voor wie AI beheert). Expliciet een user-parameter, geen
# current_user in het predicaat — zo blijft het register bruikbaar buiten een request.
WIDGETS = [
    {"key": "kerncijfers", "title": _l("Kerncijfers"), "build": _build_kerncijfers},
    {"key": "per_categorie", "title": _l("Per categorie"), "build": _build_per_categorie},
    {"key": "volume_trend", "title": _l("Mailvolume"), "build": _build_volume_trend},
    {"key": "concept_benutting", "title": _l("Concept-benutting"), "build": _build_concept_benutting},
    {"key": "reactietijd", "title": _l("Reactietijd"), "build": _build_reactietijd},
    {"key": "drukste_uren", "title": _l("Drukste uren"), "build": _build_drukste_uren},
    {"key": "ruis_rapport", "title": _l("Ruisfilter"), "build": _build_ruis_rapport},
    {"key": "opvolgingen", "title": _l("Opvolgingen"), "build": _build_opvolgingen},
    {"key": "taken", "title": _l("Taken"), "build": _build_taken},
    {"key": "top_afzenders", "title": _l("Meest voorkomende afzenders"), "build": _build_top_afzenders},
    {"key": "ai_verbruik", "title": _l("AI-verbruik (deze maand)"), "build": _build_ai_verbruik,
     "when": lambda user: user.has_perm("manageAI")},
]
WIDGET_KEYS = tuple(w["key"] for w in WIDGETS)


def _visible_keys(user) -> set[str]:
    return {w["key"] for w in WIDGETS if "when" not in w or w["when"](user)}


def _layout_for(user) -> tuple[list[str], list[str]]:
    """(volgorde, verborgen) voor deze gebruiker — onbekende keys worden genegeerd,
    nieuwe widgets die na het opslaan zijn bijgekomen sluiten achteraan aan."""
    order: list[str] = []
    hidden: list[str] = []
    raw = getattr(user, "analytics_layout", None)
    if raw:
        try:
            data = json.loads(raw)
            order = [k for k in data.get("order", []) if k in WIDGET_KEYS]
            hidden = [k for k in data.get("hidden", []) if k in WIDGET_KEYS]
        except (ValueError, TypeError, AttributeError):
            pass  # kapotte JSON → gewoon de standaardindeling
    for key in WIDGET_KEYS:
        if key not in order and key not in hidden:
            order.append(key)
    return order, hidden


def save_layout(user, order: list, hidden: list) -> tuple[list[str], list[str]]:
    """Valideer en bewaar de indeling; geeft de opgeschoonde lijsten terug.

    Gevalideerd tegen wat voor DEZE gebruiker bestaat (_visible_keys), niet tegen alle
    keys: opslaan en renderen delen zo één waarheidsbron, en een permissie-gebonden
    widget kan nooit via de opslag-API in andermans indeling sluipen."""
    visible = _visible_keys(user)
    clean_order = [k for k in order if isinstance(k, str) and k in visible]
    clean_hidden = [k for k in hidden if isinstance(k, str) and k in visible
                    and k not in clean_order]
    user.analytics_layout = json.dumps({"order": clean_order, "hidden": clean_hidden})
    db.session.commit()
    return clean_order, clean_hidden


def analytics_page_context(period_key: str | None = None) -> dict:
    period = _period(period_key or DEFAULT_PERIOD)
    msgs = _in_period(_messages(), period["days"])
    cats = _categories()  # één keer per pagina; builders queryen dit niet zelf
    by_key = {w["key"]: w for w in WIDGETS}
    order, hidden = _layout_for(current_user)
    # Permissie-gebonden widgets bestaan simpelweg niet voor wie ze niet mag zien —
    # niet op het bord én niet als "verborgen"-chip.
    visible = _visible_keys(current_user)
    order = [k for k in order if k in visible]
    hidden = [k for k in hidden if k in visible]
    widgets = [
        {"key": key, "title": by_key[key]["title"],
         "template": f"app/user/_widgets/{key}.html",
         "data": by_key[key]["build"](msgs, period["days"], cats)}
        for key in order
    ]
    return {
        "widgets": widgets,
        "hidden_widgets": [{"key": k, "title": by_key[k]["title"]} for k in hidden],
        "periods": PERIODS,
        "period": period,
    }
