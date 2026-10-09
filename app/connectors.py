"""Per-company connector resolution.

Each organisation can bring its own Microsoft/Google OAuth app ("BYO"): when its
credentials are set, the mailbox OAuth uses the org's own app (so consent stays
inside the customer's tenant); otherwise it falls back to the shared platform app
configured in the environment. Secrets are stored encrypted and decrypted on read.
"""
from __future__ import annotations

from flask import current_app


def microsoft_app(org) -> tuple[str | None, str, str | None]:
    """(client_id, tenant_id, client_secret) for an org's Microsoft 365 connector."""
    from app.security.crypto import decrypt

    if org is not None and org.graph_app_id and org.graph_client_secret_enc:
        return org.graph_app_id, (org.graph_tenant_id or "organizations"), decrypt(org.graph_client_secret_enc)
    cfg = current_app.config
    return cfg.get("MS_CLIENT_ID"), (cfg.get("MS_TENANT_ID") or "common"), cfg.get("MS_CLIENT_SECRET")


def google_app(org) -> tuple[str | None, str | None]:
    """(client_id, client_secret) for an org's Gmail connector."""
    from app.security.crypto import decrypt

    if org is not None and org.google_client_id and org.google_client_secret_enc:
        return org.google_client_id, decrypt(org.google_client_secret_enc)
    cfg = current_app.config
    return cfg.get("GOOGLE_CLIENT_ID"), cfg.get("GOOGLE_CLIENT_SECRET")


def graph_configured_for(org) -> bool:
    """True when this org can connect Microsoft 365 — via its own app or the platform app."""
    if org_uses_own_microsoft(org):
        return True
    return bool(current_app.config.get("GRAPH_CONFIGURED"))


def gmail_configured_for(org) -> bool:
    if org_uses_own_google(org):
        return True
    return bool(current_app.config.get("GMAIL_CONFIGURED"))


def org_uses_own_microsoft(org) -> bool:
    return bool(org is not None and org.graph_app_id and org.graph_client_secret_enc)


def org_uses_own_google(org) -> bool:
    return bool(org is not None and org.google_client_id and org.google_client_secret_enc)
