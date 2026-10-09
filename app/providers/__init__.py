"""Mail provider abstraction + factory.

The pipeline only ever talks to a `MailProvider`, whose contract deliberately
omits any send capability — that is the structural half of Otto's
"never sends automatically" guarantee. User-initiated sending lives in the
separate `UserInitiatedSender` mixin, reachable only from the explicit send route.
"""
from __future__ import annotations

from app.providers.base import MailProvider, UserInitiatedSender
from app.providers.exceptions import ProviderAuthError, ProviderNotConfigured, RateLimited


def get_provider(mail_account) -> MailProvider:
    """Return the concrete provider adapter for a MailAccount."""
    provider = (mail_account.provider or "").lower()
    if provider == "gmail":
        from app.providers.gmail import GmailProvider

        return GmailProvider(mail_account)
    if provider == "outlook":
        from app.providers.outlook import OutlookProvider

        return OutlookProvider(mail_account)
    if provider == "imap":
        from app.providers.imap import ImapProvider

        return ImapProvider(mail_account)
    raise ProviderNotConfigured(f"Unknown provider: {mail_account.provider!r}")


def provider_is_configured(provider: str) -> bool:
    from flask import current_app

    provider = (provider or "").lower()
    if provider == "gmail":
        return bool(current_app.config.get("GMAIL_CONFIGURED"))
    if provider == "outlook":
        return bool(current_app.config.get("GRAPH_CONFIGURED"))
    if provider == "imap":
        return True  # IMAP needs per-account credentials, entered at connect time
    return False


__all__ = [
    "MailProvider", "UserInitiatedSender", "get_provider", "provider_is_configured",
    "ProviderNotConfigured", "ProviderAuthError", "RateLimited",
]
