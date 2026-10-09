"""Jinja filters/globals for pixel-perfect styling."""
from __future__ import annotations

from flask import Flask

from app.constants import TONE_LABELS


def register_filters(app: Flask) -> None:
    @app.template_filter("alpha")
    def alpha(hex_color: str, suffix: str = "1A") -> str:
        """Append an alpha-hex suffix, mirroring the prototype's `color + '1A'` tints."""
        return f"{hex_color}{suffix}"

    @app.template_filter("tone_label")
    def tone_label(key: str) -> str:
        return TONE_LABELS.get(key, key.capitalize())

    @app.template_filter("mailtext")
    def mailtext(value: str | None) -> str:
        """Render an e-mail body as clean text — strips HTML (drops <head>/<style>),
        leaves plain-text bodies untouched. Covers messages stored before ingestion
        started cleaning HTML, so the reading pane never shows raw markup."""
        from app.htmltext import html_to_text

        return html_to_text(value)

    @app.template_filter("reltime")
    def reltime(value, fallback: str = ""):
        """Inbox time label from a message's received_at (UTC) in APP_TIMEZONE; falls
        back to the stored time_display string for seed rows that have no timestamp."""
        from app.timefmt import reltime as _reltime

        return _reltime(value, fallback)

    @app.template_filter("fulltime")
    def fulltime(value, fallback: str = ""):
        """Full date + time ('27 jun 2026, 14:32') for an opened message or log row."""
        from app.timefmt import fulltime as _fulltime

        return _fulltime(value, fallback)

    @app.template_global("tone_labels")
    def tone_labels() -> dict:
        return TONE_LABELS

    @app.template_global("is_platform_owner")
    def _is_platform_owner() -> bool:
        """Template gate for administrator-only UI (the /owner backoffice)."""
        from app.security.rbac import is_platform_owner

        return is_platform_owner()
