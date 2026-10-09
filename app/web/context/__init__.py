"""View-context builders — de server-side spiegel van renderVals() uit het prototype.

Elke functie geeft platte data terug (namen, kleuren, tellingen, actieve vlaggen); de
templates zetten daar CSS-klassen en dynamische kleuren op. Alles is gescoped op de
organisatie van de ingelogde gebruiker.

Dit was één bestand van ruim 1100 regels. Het is opgesplitst per schermgroep, met de
gedeelde bouwstenen in `_base`. Dit pakket her-exporteert alles wat de routes gebruiken,
zodat `from app.web import context as ctx` en elke `ctx.…`-aanroep onveranderd werkt.
"""
from __future__ import annotations

from flask_login import current_user  # noqa: F401 — routes gebruiken ctx.current_user

# De onderstreepte namen zijn bewuste her-exports: routes gebruiken ctx._org,
# ctx._personal_messages enz. Ze horen niet in __all__, maar moeten wel bereikbaar zijn.
from app.web.context._base import (  # noqa: F401
    ADMIN_NAV,
    USER_NAV,
    _account_messages,
    _categories,
    _category_counts,
    _messages,
    _my_shared_accounts,
    _org,
    _personal_messages,
    _visible_shared_account_ids,
    _waiting,
    base_context,
    js_i18n,
    provider_name,
)
from app.web.context.widgets import (  # noqa: F401
    analytics_page_context,
    save_layout,
)
from app.web.context.admin import (
    BYO_PROVIDER_GUIDES,
    BYO_PROVIDERS,
    ai_context,
    audit_context,
    autoreplies_context,
    knowledge_context,
    orgcats_context,
    overview_context,
    providers_context,
    roles_context,
    signatures_context,
    sso_context,
    users_context,
)
from app.web.context.inbox import (  # noqa: F401
    _build_inbox_view,
    briefing_context,
    inbox_context,
    outlook_context,
)
from app.web.context.teams import (  # noqa: F401
    _can_manage_mailbox,
    _resolve_team_mailbox,
    _team_header,
    team_analytics_context,
    team_inbox_context,
    team_settings_context,
    teams_context,
)
from app.web.context.crm import (  # noqa: F401
    crm_context,
    project_detail_context,
    projects_context,
)
from app.web.context.user import (
    LIVE_INTEGRATION_KEYS,
    categories_context,
    integrations_context,
    rules_context,
    settings_context,
    tasks_context,
)

__all__ = [
    "ADMIN_NAV",
    "BYO_PROVIDERS",
    "BYO_PROVIDER_GUIDES",
    "LIVE_INTEGRATION_KEYS",
    "USER_NAV",
    "ai_context",
    "analytics_page_context",
    "audit_context",
    "base_context",
    "briefing_context",
    "categories_context",
    "current_user",
    "inbox_context",
    "integrations_context",
    "rules_context",
    "js_i18n",
    "orgcats_context",
    "outlook_context",
    "overview_context",
    "providers_context",
    "provider_name",
    "roles_context",
    "settings_context",
    "knowledge_context",
    "signatures_context",
    "autoreplies_context",
    "sso_context",
    "tasks_context",
    "team_analytics_context",
    "team_inbox_context",
    "team_settings_context",
    "teams_context",
    "crm_context",
    "projects_context",
    "project_detail_context",
    "users_context",
]
