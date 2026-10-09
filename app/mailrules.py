"""Inbox-regels: Otto's woordenschat, en de vertaling naar Outlook.

Otto bewaart een regel in zijn eigen vorm en vertaalt die pas bij het pushen naar wat
Microsoft ervan verwacht. Dat scheiden loont: de schermen en de opslag hoeven niet mee te
veranderen met Graph, en een regel blijft leesbaar ook als hij (nog) niet gepusht is.

De regel draait in Exchange zelf, dus ook wanneer Otto stilstaat. Otto bepaalt wát er
gebeurt, Outlook voert het uit.
"""
from __future__ import annotations

import re

from flask import current_app

from app.extensions import db
from app.models import MailRule
from app.models.base import utcnow

# ── Woordenschat ─────────────────────────────────────────────────────────────
# Elke voorwaarde: sleutel → (label, soort). "lijst" = meerdere waarden, "vlag" = aan/uit,
# "keuze" = één waarde uit een vaste set.
CONDITIONS: dict[str, tuple[str, str]] = {
    "from_addresses":   ("Afzender is",              "lijst"),
    "sender_contains":  ("Afzender bevat",           "lijst"),
    "subject_contains": ("Onderwerp bevat",          "lijst"),
    "body_contains":    ("Bericht bevat",            "lijst"),
    "recipient_contains": ("Ontvanger bevat",        "lijst"),
    "sent_to_addresses": ("Gestuurd naar",           "lijst"),
    "header_contains":  ("Kopregels bevatten",       "lijst"),
    "sent_to_me":       ("Rechtstreeks aan mij",     "vlag"),
    "sent_only_to_me":  ("Alleen aan mij",           "vlag"),
    "sent_cc_me":       ("Ik sta in cc",             "vlag"),
    "has_attachments":  ("Heeft een bijlage",        "vlag"),
    "is_approval_request": ("Is een goedkeuringsverzoek", "vlag"),
    "is_automatic_reply":  ("Is een automatisch antwoord", "vlag"),
    "importance":       ("Belangrijkheid",           "keuze"),
}
IMPORTANCE = ["low", "normal", "high"]

ACTIONS: dict[str, tuple[str, str]] = {
    "move_to_folder":   ("Verplaats naar map",       "map"),
    "copy_to_folder":   ("Kopieer naar map",         "map"),
    "assign_categories": ("Geef categorie",          "lijst"),
    "mark_as_read":     ("Markeer als gelezen",      "vlag"),
    "mark_importance":  ("Zet belangrijkheid",       "keuze"),
    "forward_to":       ("Stuur door naar",          "lijst"),
    "redirect_to":      ("Leid om naar",             "lijst"),
    "delete":           ("Verwijder",                "vlag"),
    "permanent_delete": ("Verwijder definitief",     "vlag"),
    "stop_processing":  ("Stop met verdere regels",  "vlag"),
}

# Acties die mail onherroepelijk weghalen; die vragen om een expliciete bevestiging in
# de UI en worden hier apart benoemd zodat ze nergens per ongeluk stil worden toegepast.
DESTRUCTIVE = {"delete", "permanent_delete"}


class RuleError(ValueError):
    """De regel klopt niet — de boodschap is bedoeld voor de gebruiker."""


# ── Validatie ────────────────────────────────────────────────────────────────
def _clean_list(value) -> list[str]:
    if isinstance(value, str):
        value = value.replace(",", "\n").split("\n")
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def normalise(conditions: dict, actions: dict) -> tuple[dict, dict]:
    """Snoei naar de bekende woordenschat en gooi weg wat leeg is.

    Onbekende sleutels worden genegeerd in plaats van doorgegeven: Graph weigert een
    regel met een onbekend veld in zijn geheel, en dan zou één typfout de hele regel
    onbruikbaar maken zonder dat duidelijk is waarom.
    """
    out_c: dict = {}
    for key, (_label, kind) in CONDITIONS.items():
        if key not in (conditions or {}):
            continue
        raw = conditions[key]
        if kind == "lijst" and (vals := _clean_list(raw)):
            out_c[key] = vals
        elif kind == "vlag" and bool(raw):
            out_c[key] = True
        elif kind == "keuze" and str(raw or "").strip() in IMPORTANCE:
            out_c[key] = str(raw).strip()

    out_a: dict = {}
    for key, (_label, kind) in ACTIONS.items():
        if key not in (actions or {}):
            continue
        raw = actions[key]
        if kind == "lijst" and (vals := _clean_list(raw)):
            out_a[key] = vals
        elif kind == "map" and str(raw or "").strip():
            out_a[key] = str(raw).strip()
        elif kind == "vlag" and bool(raw):
            out_a[key] = True
        elif kind == "keuze" and str(raw or "").strip() in IMPORTANCE:
            out_a[key] = str(raw).strip()
    return out_c, out_a


def validate(name: str, conditions: dict, actions: dict) -> None:
    if not (name or "").strip():
        raise RuleError("Geef de regel een naam.")
    if not conditions:
        raise RuleError("Een regel zonder voorwaarde zou op álle mail slaan. Voeg er minstens één toe.")
    if not actions:
        raise RuleError("Geef aan wat er moet gebeuren.")
    # Verplaatsen én verwijderen is tegenstrijdig; Outlook doet dan iets onvoorspelbaars.
    if actions.keys() & DESTRUCTIVE and ("move_to_folder" in actions or "copy_to_folder" in actions):
        raise RuleError("Verwijderen gaat niet samen met verplaatsen of kopiëren.")


# ── Vertaling naar Graph ─────────────────────────────────────────────────────
def _addresses(values: list[str]) -> list[dict]:
    return [{"emailAddress": {"address": v}} for v in values]


def to_graph(rule: MailRule, folder_ids: dict[str, str] | None = None) -> dict:
    """Otto's vorm → de payload die Outlook verwacht.

    `folder_ids` vertaalt een mapnaam naar het id dat Graph nodig heeft; ontbreekt de
    map, dan wordt die actie weggelaten in plaats van de hele regel te laten mislukken.
    """
    folder_ids = folder_ids or {}
    c, a = rule.conditions or {}, rule.actions or {}

    conditions: dict = {}
    if v := c.get("from_addresses"):
        conditions["fromAddresses"] = _addresses(v)
    if v := c.get("sender_contains"):
        conditions["senderContains"] = v
    if v := c.get("subject_contains"):
        conditions["subjectContains"] = v
    if v := c.get("body_contains"):
        conditions["bodyContains"] = v
    if v := c.get("recipient_contains"):
        conditions["recipientContains"] = v
    if v := c.get("sent_to_addresses"):
        conditions["sentToAddresses"] = _addresses(v)
    if v := c.get("header_contains"):
        conditions["headerContains"] = v
    for key, graph_key in (
        ("sent_to_me", "sentToMe"), ("sent_only_to_me", "sentOnlyToMe"),
        ("sent_cc_me", "sentCcMe"), ("has_attachments", "hasAttachments"),
        ("is_approval_request", "isApprovalRequest"), ("is_automatic_reply", "isAutomaticReply"),
    ):
        if c.get(key):
            conditions[graph_key] = True
    if c.get("importance"):
        conditions["importance"] = c["importance"]

    actions: dict = {}
    # Een naam die we kennen wint; anders het id dat we bij het inlezen bewaarden, zodat
    # een regel naar een onbekende map gewoon blijft doen wat ze deed.
    if (name := a.get("move_to_folder")) and folder_ids.get(name):
        actions["moveToFolder"] = folder_ids[name]
    elif a.get("move_to_folder_id"):
        actions["moveToFolder"] = a["move_to_folder_id"]
    elif looks_like_graph_id(a.get("move_to_folder")):
        # Oude import bewaarde het rauwe id als 'naam' — dan is dat meteen het id.
        actions["moveToFolder"] = a["move_to_folder"]
    if (name := a.get("copy_to_folder")) and folder_ids.get(name):
        actions["copyToFolder"] = folder_ids[name]
    elif a.get("copy_to_folder_id"):
        actions["copyToFolder"] = a["copy_to_folder_id"]
    elif looks_like_graph_id(a.get("copy_to_folder")):
        actions["copyToFolder"] = a["copy_to_folder"]
    if v := a.get("assign_categories"):
        actions["assignCategories"] = v
    if a.get("mark_as_read"):
        actions["markAsRead"] = True
    if a.get("mark_importance"):
        actions["markImportance"] = a["mark_importance"]
    if v := a.get("forward_to"):
        actions["forwardTo"] = _addresses(v)
    if v := a.get("redirect_to"):
        actions["redirectTo"] = _addresses(v)
    if a.get("delete"):
        actions["delete"] = True
    if a.get("permanent_delete"):
        actions["permanentDelete"] = True
    # Outlook wil dit veld altijd; expliciet False houdt het gedrag voorspelbaar.
    actions["stopProcessingRules"] = bool(a.get("stop_processing"))

    return {
        "displayName": rule.name,
        "sequence": rule.sequence or 1,
        "isEnabled": bool(rule.enabled),
        "conditions": conditions,
        "actions": actions,
    }


def from_graph(raw: dict, folder_names: dict[str, str] | None = None) -> tuple[dict, dict]:
    """Graph → Otto's vorm, voor het inlezen van regels die al in Outlook stonden."""
    folder_names = folder_names or {}
    gc, ga = raw.get("conditions") or {}, raw.get("actions") or {}

    def addr(items):
        return [(i.get("emailAddress") or {}).get("address", "") for i in (items or [])]

    conditions: dict = {}
    for graph_key, key in (
        ("senderContains", "sender_contains"), ("subjectContains", "subject_contains"),
        ("bodyContains", "body_contains"), ("recipientContains", "recipient_contains"),
        ("headerContains", "header_contains"),
    ):
        if gc.get(graph_key):
            conditions[key] = list(gc[graph_key])
    if gc.get("fromAddresses"):
        conditions["from_addresses"] = [a for a in addr(gc["fromAddresses"]) if a]
    if gc.get("sentToAddresses"):
        conditions["sent_to_addresses"] = [a for a in addr(gc["sentToAddresses"]) if a]
    for graph_key, key in (
        ("sentToMe", "sent_to_me"), ("sentOnlyToMe", "sent_only_to_me"),
        ("sentCcMe", "sent_cc_me"), ("hasAttachments", "has_attachments"),
        ("isApprovalRequest", "is_approval_request"), ("isAutomaticReply", "is_automatic_reply"),
    ):
        if gc.get(graph_key):
            conditions[key] = True
    if gc.get("importance"):
        conditions["importance"] = gc["importance"]

    actions: dict = {}
    # Lukt het opzoeken van de mapnaam niet, dan bewaren we het Graph-id apart in plaats
    # van het als naam te laten doorgaan. Vroeger viel dit terug op het id zelf, en dan
    # stond die hele sleutel op het regelscherm alsof het een map heette.
    for graph_key, key in (("moveToFolder", "move_to_folder"), ("copyToFolder", "copy_to_folder")):
        if not ga.get(graph_key):
            continue
        folder_id = ga[graph_key]
        name = folder_names.get(folder_id)
        if name:
            actions[key] = name
        else:
            actions[f"{key}_id"] = folder_id
    if ga.get("assignCategories"):
        actions["assign_categories"] = list(ga["assignCategories"])
    if ga.get("markAsRead"):
        actions["mark_as_read"] = True
    if ga.get("markImportance"):
        actions["mark_importance"] = ga["markImportance"]
    if ga.get("forwardTo"):
        actions["forward_to"] = [a for a in addr(ga["forwardTo"]) if a]
    if ga.get("redirectTo"):
        actions["redirect_to"] = [a for a in addr(ga["redirectTo"]) if a]
    if ga.get("delete"):
        actions["delete"] = True
    if ga.get("permanentDelete"):
        actions["permanent_delete"] = True
    if ga.get("stopProcessingRules"):
        actions["stop_processing"] = True
    return conditions, actions


# ── Samenvatting voor het scherm ─────────────────────────────────────────────
def looks_like_graph_id(value) -> bool:
    """Een Graph-id (base64-achtig, 100+ tekens) vermomd als 'mapnaam'?

    Oudere imports (vóór de submappen-fix in list_folders) bewaarden het rauwe map-id
    als naam; die rijen staan nog in de database en horen nooit op het scherm."""
    v = str(value or "")
    return len(v) >= 40 and " " not in v and re.fullmatch(r"[A-Za-z0-9_=\-/+]+", v) is not None


def describe(rule: MailRule) -> tuple[list[str], list[str]]:
    """(voorwaarden, acties) als leesbare zinnen."""
    def phrase(catalog, key, value):
        label, kind = catalog[key]
        if kind == "vlag":
            return label
        if kind in ("lijst",):
            return f"{label}: {', '.join(value)}"
        if kind == "map" and looks_like_graph_id(value):
            return f"{label}: een map in Outlook"  # nooit een Graph-id van 200 tekens tonen
        return f"{label}: {value}"

    conds = [phrase(CONDITIONS, k, v) for k, v in (rule.conditions or {}).items() if k in CONDITIONS]
    acts = [phrase(ACTIONS, k, v) for k, v in (rule.actions or {}).items() if k in ACTIONS]
    # Een map die we niet bij naam kennen: zeg dát, in plaats van een Graph-id van
    # tweehonderd tekens op het scherm te zetten. De regel werkt gewoon; alleen de naam
    # ontbreekt, en dat is precies wat hier staat.
    for key, label in (("move_to_folder_id", "Verplaats naar map"),
                       ("copy_to_folder_id", "Kopieer naar map")):
        if (rule.actions or {}).get(key):
            acts.append(f"{label}: een map in Outlook")
    return conds, acts


# ── Synchronisatie met Outlook ───────────────────────────────────────────────
def _folder_maps(provider) -> tuple[dict[str, str], dict[str, str]]:
    """(naam→id, id→naam) van de mappen in deze mailbox."""
    try:
        folders = provider.list_folders() or []
    except Exception:  # noqa: BLE001
        return {}, {}
    by_name = {f["name"]: f["id"] for f in folders if f.get("name") and f.get("id")}
    return by_name, {v: k for k, v in by_name.items()}


def push(rule: MailRule, provider=None) -> bool:
    """Schrijf de regel naar Outlook. Geeft terug of het gelukt is.

    Een mislukking wordt op de regel zelf vastgelegd in plaats van stil weggelogd, zodat
    het scherm kan tonen dat deze regel (nog) niet in Outlook staat.
    """
    from app.providers import get_provider

    provider = provider or get_provider(rule.account)
    if not (provider.is_configured() and getattr(provider, "supports_rules", False)):
        rule.last_error = "Deze mailbox ondersteunt geen Outlook-regels."
        db.session.commit()
        return False

    by_name, _ = _folder_maps(provider)
    # Een map die nog niet bestaat wordt aangemaakt, anders zou de actie stil wegvallen.
    for key in ("move_to_folder", "copy_to_folder"):
        name = (rule.actions or {}).get(key)
        if name and name not in by_name:
            try:
                if folder_id := provider.ensure_folder(name):
                    by_name[name] = folder_id
            except Exception:  # noqa: BLE001
                current_app.logger.info("Map aanmaken mislukt voor regel %s: %s", rule.id, name)

    payload = to_graph(rule, by_name)
    try:
        if rule.provider_rule_id:
            provider.update_rule(rule.provider_rule_id, payload)
        else:
            rule.provider_rule_id = provider.create_rule(payload)
        rule.last_error = None
        rule.synced_at = utcnow()
        db.session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 — de fout hoort zichtbaar te zijn, niet stil
        db.session.rollback()
        rule.last_error = str(exc)[:255]
        db.session.commit()
        current_app.logger.warning("Regel %s niet naar Outlook geschreven: %s", rule.id, exc)
        return False


def remove(rule: MailRule, provider=None) -> None:
    """Haal de regel uit Outlook (best-effort) en daarna uit Otto."""
    from app.providers import get_provider

    if rule.provider_rule_id:
        try:
            provider = provider or get_provider(rule.account)
            if provider.is_configured():
                provider.delete_rule(rule.provider_rule_id)
        except Exception as exc:  # noqa: BLE001 — al weg is ook goed
            current_app.logger.info("Regel verwijderen in Outlook overgeslagen: %s", exc)
    db.session.delete(rule)
    db.session.commit()


def pull(account, provider=None) -> int:
    """Lees de regels in die al in Outlook stonden. Geeft het aantal nieuwe terug.

    Bestaande regels worden op hun provider-id herkend en bijgewerkt, zodat inlezen
    herhaald kan worden zonder dubbels te maken.
    """
    from app.providers import get_provider

    provider = provider or get_provider(account)
    if not (provider.is_configured() and getattr(provider, "supports_rules", False)):
        return 0
    try:
        raw_rules = provider.list_rules()
    except Exception as exc:  # noqa: BLE001
        current_app.logger.info("Regels ophalen mislukt voor %s: %s", account.email, exc)
        return 0

    _, by_id = _folder_maps(provider)
    added = 0
    for raw in raw_rules:
        rid = raw.get("id")
        if not rid:
            continue
        rule = db.session.query(MailRule).filter_by(account_id=account.id, provider_rule_id=rid).first()
        conditions, actions = from_graph(raw, by_id)
        if rule is None:
            org_id = account.organization_id or (account.user.organization_id if account.user else None)
            rule = MailRule(organization_id=org_id, account_id=account.id, provider_rule_id=rid)
            db.session.add(rule)
            added += 1
        rule.name = raw.get("displayName") or "Regel uit Outlook"
        rule.sequence = raw.get("sequence") or 0
        rule.enabled = bool(raw.get("isEnabled", True))
        rule.conditions, rule.actions = conditions, actions
        rule.last_error = None
        rule.synced_at = utcnow()
    db.session.commit()
    return added


def rules_for(account) -> list[MailRule]:
    return (
        db.session.query(MailRule)
        .filter_by(account_id=account.id)
        .order_by(MailRule.sequence, MailRule.id)
        .all()
    )
