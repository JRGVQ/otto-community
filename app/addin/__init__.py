"""Outlook-taakvenster (fase 6.7).

Een Office-invoegtoepassing die náást de mail in Outlook laat zien wat Otto er al van weet:
de categorie, het concept-antwoord, de gekoppelde taken en de snippets. Geen eigen
mailclient — het venster leunt volledig op wat Otto al bepaald heeft, en toont het waar de
gebruiker toch al werkt.

Drie routes, bewust in een eigen blueprint:
- `/addin/manifest.xml` en `/addin/taskpane` zijn openbaar: Outlook laadt ze zonder
  Otto-sessie. Ze bevatten geen gegevens.
- `/addin/context` geeft de gegevens terug en checkt zelf of er een ingelogde gebruiker is;
  niet ingelogd → {"authenticated": false}, zodat het venster een inloglink kan tonen in
  plaats van een harde 302 op te vangen.
"""
from __future__ import annotations

from flask import Blueprint

addin_bp = Blueprint("addin", __name__, url_prefix="/addin")

from app.addin import routes  # noqa: E402,F401
