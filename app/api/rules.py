"""Inbox-regels beheren — aanmaken, wijzigen, herordenen en inlezen uit Outlook.

Elke route werkt op één mailbox en controleert dat de aanroeper die mag beheren, zodat
niemand regels kan zetten op een postvak waar hij niet bij hoort.
"""
from __future__ import annotations

from flask import abort, request
from flask_login import current_user, login_required

from app import mailrules as svc
from app.api import api_bp
from app.api._helpers import ok
from app.extensions import db
from app.models import MailAccount, MailRule


def _account_or_403(account_id: int) -> MailAccount:
    acct = db.session.get(MailAccount, account_id)
    if not acct:
        abort(404)
    # organization_id is op oudere mailboxen leeg; val dan terug op de eigenaar, zoals
    # de rest van de codebase dat doet. Blijft de organisatie verschillen → onzichtbaar.
    org_id = acct.organization_id or (acct.user.organization_id if acct.user else None)
    if org_id != current_user.organization_id:
        abort(404)
    allowed = (
        current_user.id in acct.member_user_ids if acct.is_shared else acct.user_id == current_user.id
    )
    if not allowed:
        abort(403)
    return acct


def _rule_or_404(rule_id: int) -> MailRule:
    rule = db.session.get(MailRule, rule_id)
    if not rule or rule.organization_id != current_user.organization_id:
        abort(404)
    _account_or_403(rule.account_id)
    return rule


def _payload() -> tuple[str, dict, dict]:
    data = request.get_json(silent=True) or request.form.to_dict(flat=True) or {}
    name = (data.get("name") or "").strip()
    conditions = data.get("conditions") or {}
    actions = data.get("actions") or {}
    conditions, actions = svc.normalise(conditions, actions)
    svc.validate(name, conditions, actions)
    return name, conditions, actions


@api_bp.route("/rules/<int:account_id>", methods=["GET"])
@login_required
def list_rules(account_id: int):
    acct = _account_or_403(account_id)
    rules = svc.rules_for(acct)
    return ok(rules=[
        {
            "id": r.id, "name": r.name, "enabled": bool(r.enabled), "sequence": r.sequence,
            "in_outlook": r.in_outlook, "last_error": r.last_error,
            "conditions": r.conditions or {}, "actions": r.actions or {},
        }
        for r in rules
    ])


@api_bp.route("/rules/<int:account_id>", methods=["POST"])
@login_required
def create_rule(account_id: int):
    acct = _account_or_403(account_id)
    try:
        name, conditions, actions = _payload()
    except svc.RuleError as exc:
        return ok(error=str(exc)), 400

    last = (
        db.session.query(MailRule)
        .filter_by(account_id=acct.id)
        .order_by(MailRule.sequence.desc())
        .first()
    )
    rule = MailRule(
        # mail_rules.organization_id is verplicht; op oudere mailboxen staat het veld
        # leeg, dus daar valt de organisatie van de aanroeper in.
        organization_id=acct.organization_id or current_user.organization_id,
        account_id=acct.id, name=name,
        conditions=conditions, actions=actions, sequence=((last.sequence or 0) + 1) if last else 1,
    )
    db.session.add(rule)
    db.session.commit()

    pushed = svc.push(rule)
    return ok(id=rule.id, in_outlook=pushed, error=(None if pushed else rule.last_error),
              toast=("Regel staat in Outlook." if pushed else "Regel bewaard, maar nog niet in Outlook."),
              reload=True)


@api_bp.route("/rules/rule/<int:rule_id>", methods=["POST"])
@login_required
def update_rule(rule_id: int):
    rule = _rule_or_404(rule_id)
    try:
        name, conditions, actions = _payload()
    except svc.RuleError as exc:
        return ok(error=str(exc)), 400
    rule.name, rule.conditions, rule.actions = name, conditions, actions
    db.session.commit()
    pushed = svc.push(rule)
    return ok(in_outlook=pushed, error=(None if pushed else rule.last_error),
              toast=("Regel bijgewerkt." if pushed else "Bewaard, maar Outlook weigerde de regel."),
              reload=True)


@api_bp.route("/rules/rule/<int:rule_id>/toggle", methods=["POST"])
@login_required
def toggle_rule(rule_id: int):
    rule = _rule_or_404(rule_id)
    rule.enabled = not rule.enabled
    db.session.commit()
    svc.push(rule)  # de aan/uit-stand hoort ook in Outlook te staan
    return ok(on=rule.enabled, reload=True)


@api_bp.route("/rules/rule/<int:rule_id>/move", methods=["POST"])
@login_required
def move_rule(rule_id: int):
    """Verschuif de regel in de volgorde waarin Outlook ze toepast."""
    rule = _rule_or_404(rule_id)
    direction = (request.get_json(silent=True) or request.form or {}).get("direction")
    siblings = svc.rules_for(rule.account)
    idx = next((i for i, r in enumerate(siblings) if r.id == rule.id), None)
    if idx is None:
        abort(404)
    swap_with = idx - 1 if direction == "up" else idx + 1
    if not (0 <= swap_with < len(siblings)):
        return ok()  # al bovenaan of onderaan

    other = siblings[swap_with]
    rule.sequence, other.sequence = other.sequence, rule.sequence
    db.session.commit()
    for r in (rule, other):
        svc.push(r)
    return ok(reload=True)


@api_bp.route("/rules/rule/<int:rule_id>/delete", methods=["POST", "DELETE"])
@login_required
def delete_rule(rule_id: int):
    svc.remove(_rule_or_404(rule_id))
    return ok(toast="Regel verwijderd.", reload=True)


@api_bp.route("/rules/<int:account_id>/import", methods=["POST"])
@login_required
def import_rules(account_id: int):
    """Lees de regels in die al in Outlook stonden."""
    acct = _account_or_403(account_id)
    added = svc.pull(acct)
    return ok(added=added, reload=True,
              toast=(f"{added} regel(s) ingelezen uit Outlook." if added
                     else "Geen nieuwe regels gevonden in Outlook."))
