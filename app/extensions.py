"""Flask extension singletons, instantiated here to avoid circular imports."""
from __future__ import annotations

from flask_babel import Babel
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import MetaData

# Named constraints — required for Alembic batch migrations on SQLite, and good
# practice everywhere (predictable constraint names across dialects).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

db = SQLAlchemy(metadata=MetaData(naming_convention=NAMING_CONVENTION))
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=[])
babel = Babel()

login_manager.login_view = "auth.login"
# Lazily translated so the message follows the visitor's locale, not the locale
# active at import time. Resolved when Flask-Login renders the flash.
from flask_babel import lazy_gettext as _l  # noqa: E402

login_manager.login_message = _l("Meld je aan om Otto te gebruiken.")
login_manager.login_message_category = "info"


LANGUAGE_NAMES = {"nl": "Nederlands", "fr": "Français", "en": "English"}


def select_locale() -> str:
    """Resolve the active UI language for this request.

    Order of precedence: an explicit `lang` cookie (set by the switcher) wins;
    otherwise fall back to the browser's Accept-Language, then the default (nl).
    Only languages in config['LANGUAGES'] are ever honoured.

    Flask-Babel may call this while resolving a lazy string outside a request
    (background threads, CLI, tests) — with no `request` to read, fall back to
    the configured default instead of raising "outside of request context".
    """
    from flask import current_app, has_request_context, request

    default = current_app.config["BABEL_DEFAULT_LOCALE"]
    languages = current_app.config["LANGUAGES"]
    if not has_request_context():
        return default
    # An explicit ?lang= wins (per-language crawlable URLs for hreflang / shared links),
    # then the switcher cookie, then the browser's Accept-Language.
    query = request.args.get("lang")
    if query in languages:
        return query
    chosen = request.cookies.get("lang")
    if chosen in languages:
        return chosen
    return request.accept_languages.best_match(languages) or default


@login_manager.user_loader
def load_user(user_id: str):
    from app.models.user import User

    return db.session.get(User, int(user_id)) if user_id and user_id.isdigit() else None


def release_db_connection() -> bool:
    """Geef de databaseverbinding terug aan de pool vóór een trage externe call.

    Een LLM- of Graph-call duurt seconden tot minuten. Zolang de sessie een transactie
    openhoudt, blijft er al die tijd een connectie uit de pool geclaimd — "idle in
    transaction", zonder dat er iets gebeurt. Met pool_size=5 + max_overflow=10 per
    Gunicorn-worker is de pool leeg zodra een handvol postvakken tegelijk een concept
    genereert: `QueuePool limit ... connection timed out`. Dat is exact het
    "no available server"-patroon uit de storing van augustus 2026.

    Na deze aanroep zijn de objecten in de sessie verlopen; ze laden zichzelf opnieuw
    zodra je ze weer aanraakt (en checken dan pas een verse verbinding uit).

    Veiligheidsklep: hangt er nog werk in de sessie, dan doen we NIETS. Committen zou
    andermans halve wijzigingen vastleggen en rollback zou ze weggooien — beide erger
    dan een tijdelijk geclaimde connectie. Geeft True terug als er losgelaten is.
    """
    if db.session.new or db.session.dirty or db.session.deleted:
        return False
    try:
        db.session.commit()      # niets openstaands → sluit enkel de transactie af
        return True
    except Exception:            # noqa: BLE001 — vrijgeven mag nooit de aanroeper breken
        db.session.rollback()
        return False
