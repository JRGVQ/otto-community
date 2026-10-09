"""In-app documentation (/docs) — setup and usage guides."""
from __future__ import annotations

from flask import Blueprint, abort, render_template
from flask_babel import lazy_gettext as _l

docs_bp = Blueprint("docs", __name__, url_prefix="/docs")

# Sidebar structure, shared by every docs page. Group headers and item labels use
# lazy_gettext so they follow the visitor's language; the page KEYS (first tuple
# element) are route identifiers and stay untranslated.
DOCS_NAV = [
    {"grp": _l("Beginnen"), "items": [
        ("aan-de-slag", _l("Aan de slag")),
        ("mailbox", _l("Mailbox koppelen")),
    ]},
    {"grp": _l("Dagelijks gebruik"), "items": [
        ("inbox", _l("Inbox & triage")),
        ("categorieen", _l("Categorieën")),
        ("concepten", _l("Concepten & antwoorden")),
        ("mappen", _l("Automatisch mappen")),
        ("ruis", _l("Ruis & vaste momenten")),
        ("agenda", _l("Agenda & afspraken")),
        ("taken", _l("Taken")),
        ("analyse", _l("Analyse")),
    ]},
    {"grp": _l("Beheer"), "items": [
        ("instellingen", _l("Profiel & instellingen")),
        ("handtekeningen", _l("Handtekeningen")),
        ("bedrijfskennis", _l("Bedrijfskennis")),
        ("eigen-app", _l("Eigen app koppelen")),
        ("sso", _l("Login-SSO (OIDC)")),
    ]},
    {"grp": _l("Meer"), "items": [
        ("problemen", _l("Problemen oplossen")),
        ("faq", _l("Veelgestelde vragen")),
    ]},
]
_PAGES = {key for group in DOCS_NAV for key, _ in group["items"]}


@docs_bp.route("/")
def home():
    return render_template("docs/home.html", nav=DOCS_NAV, active="home")


@docs_bp.route("/<topic>")
def page(topic: str):
    if topic not in _PAGES:
        abort(404)
    return render_template(f"docs/{topic}.html", nav=DOCS_NAV, active=topic)
