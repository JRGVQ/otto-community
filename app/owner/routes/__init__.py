"""Backoffice-routes, gegroepeerd per onderwerp.

Dit was één bestand van bijna 800 regels. De groepen registreren zich allemaal op
dezelfde `owner_bp`; dit bestand importeert ze zodat het volstaat om het pakket te
importeren om alle routes te laten bestaan (zoals app/owner/__init__.py doet).
"""
from __future__ import annotations

from app.owner.routes import (  # noqa: F401 — import registreert de routes
    comms,
    companies,
    ops,
)
