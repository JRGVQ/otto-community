"""Auto-foldering: suggest the best destination folder for a message by analysing
the mailbox's own folder layout and matching it to the message's category."""
from __future__ import annotations

from flask import current_app


def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def suggest_folder(category, folders: list[dict]) -> dict | None:
    """Pick the folder whose name best matches the message's category (or one of its
    subcategories). Returns {'id','name'} or None when nothing matches."""
    if not category or not folders:
        return None
    names = [category.name] + [sc.name for sc in getattr(category, "subcategories", [])]
    targets = [_norm(n) for n in names if n]
    for f in folders:  # exact normalised match wins
        if _norm(f.get("name", "")) in targets:
            return f
    for f in folders:  # then a substring match either direction
        fn = _norm(f.get("name", ""))
        if fn and any(t and (t in fn or fn in t) for t in targets):
            return f
    return None


def folder_suggestion(message):
    """Return (folders, suggested) for a message's mailbox; ([], None) when the
    provider isn't connected or folders can't be fetched. Never raises."""
    if message is None or message.account is None:
        return [], None
    try:
        from app.providers import get_provider

        provider = get_provider(message.account)
        if not provider.is_configured():
            return [], None
        folders = provider.list_folders()
        return folders, suggest_folder(message.category, folders)
    except Exception:  # noqa: BLE001 — a folder fetch must never break the inbox
        current_app.logger.info("folder_suggestion skipped", exc_info=True)
        return [], None
