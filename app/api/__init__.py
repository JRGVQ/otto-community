"""REST API blueprint (CSRF-protected via X-CSRFToken header; webhooks self-exempt)."""
from __future__ import annotations

from flask import Blueprint

api_bp = Blueprint("api", __name__)

# Register route modules.
from app.api import (  # noqa: E402,F401
    accounts,
    admin,
    categories,
    crm,
    drafts,
    followups,
    integrations,
    knowledge,
    messages,
    notes,
    planner,
    rules,
    settings,
    shared_mailboxes,
    snippets,
    tasks_api,
    teams,
    webhooks,
)
