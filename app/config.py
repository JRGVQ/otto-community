"""Configuration for Otto.

The defaults encode the *runnable-by-default* contract: with no environment
configured, the app runs on SQLite, executes tasks inline (eager), uses the
deterministic stub LLM, and treats every external provider as "not configured"
(falling back to the seeded demo mailbox). Each real integration switches on the
moment its credentials appear in the environment.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"

# Publicly-known dev/test Fernet key. It is a *valid* key so token encryption
# works out of the box locally and in tests; ProdConfig refuses to boot if this
# value ever reaches production (it would make every stored OAuth token readable
# by anyone with the source).
_DEFAULT_TOKEN_KEY = "CqVFPJbqo3VBPvopEY2ZZH9vZDnfX5ZIw43yGDz1ZZ0="


def _bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int = 0) -> int:
    val = os.environ.get(name)
    try:
        return int(val) if val is not None and val.strip() != "" else default
    except ValueError:
        return default


def _float(name: str, default: float = 0.0) -> float:
    val = os.environ.get(name)
    try:
        return float(val) if val is not None and val.strip() != "" else default
    except ValueError:
        return default


class BaseConfig:
    # ── Core ──────────────────────────────────────────────────────────────
    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-insecure-change-me"
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL") or "http://localhost:5000"

    # Timezone for displaying message times (provider times are UTC). Belgian default.
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE") or "Europe/Brussels"

    # ── i18n (Flask-Babel) ────────────────────────────────────────────────
    # UI languages Otto ships. Order = fallback preference for Accept-Language
    # matching; the first entry is the source/default locale. This is the UI
    # language, distinct from the AI reply language stored per-draft (`lang`).
    LANGUAGES = ["nl", "fr", "en"]
    BABEL_DEFAULT_LOCALE = os.environ.get("BABEL_DEFAULT_LOCALE") or "nl"
    BABEL_DEFAULT_TIMEZONE = APP_TIMEZONE
    BABEL_TRANSLATION_DIRECTORIES = "translations"

    # ── E-mail — verification mails. Two backends: SMTP, or Microsoft Graph ──
    # (app-only sendMail, since M365 retired basic-auth SMTP in March 2026).
    MAIL_PROVIDER = (os.environ.get("MAIL_PROVIDER") or "smtp").lower()  # smtp | graph
    SMTP_HOST = os.environ.get("SMTP_HOST") or None
    SMTP_PORT = _int("SMTP_PORT", default=587)
    SMTP_USER = os.environ.get("SMTP_USER") or None
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD") or None
    SMTP_USE_TLS = (os.environ.get("SMTP_USE_TLS", "true").lower() != "false")
    MAIL_FROM = os.environ.get("MAIL_FROM") or "Otto <otto@example.com>"
    # Microsoft Graph mailer (app-only client credentials, dedicated platform app
    # with Mail.Send restricted to GRAPH_MAIL_SENDER via an Application Access Policy).
    GRAPH_MAIL_TENANT_ID = os.environ.get("GRAPH_MAIL_TENANT_ID") or None
    GRAPH_MAIL_CLIENT_ID = os.environ.get("GRAPH_MAIL_CLIENT_ID") or None
    GRAPH_MAIL_CLIENT_SECRET = os.environ.get("GRAPH_MAIL_CLIENT_SECRET") or None
    GRAPH_MAIL_SENDER = os.environ.get("GRAPH_MAIL_SENDER") or None
    # Hard ceiling on any single Microsoft Graph call. urlopen's own default is "wait
    # forever", which lets one unanswered request pin a gunicorn worker (or the sole
    # background poller thread) indefinitely.
    GRAPH_TIMEOUT_SECONDS = _int("GRAPH_TIMEOUT_SECONDS", default=20)

    # ── Knowledge (SharePoint via Microsoft Graph Search, app-only) ───────
    # Dedicated app-only registration (least privilege: Sites.Read.All). Search runs
    # in the background for every user's drafts, so it must be app-only (never tied to
    # a user session). Region is REQUIRED for app-only /search/query requests.
    GRAPH_KNOWLEDGE_TENANT_ID = os.environ.get("GRAPH_KNOWLEDGE_TENANT_ID") or None
    GRAPH_KNOWLEDGE_CLIENT_ID = os.environ.get("GRAPH_KNOWLEDGE_CLIENT_ID") or None
    GRAPH_KNOWLEDGE_CLIENT_SECRET = os.environ.get("GRAPH_KNOWLEDGE_CLIENT_SECRET") or None
    GRAPH_KNOWLEDGE_REGION = os.environ.get("GRAPH_KNOWLEDGE_REGION") or None

    # Show the verification link on-screen when no e-mail is sent. Convenient on a
    # test environment (set SHOW_VERIFY_LINK=1); OFF in production, where a mailer must
    # be configured so a real e-mail goes out.
    SHOW_VERIFY_LINK = _bool("SHOW_VERIFY_LINK", default=False)

    # ── Monitoring ────────────────────────────────────────────────────────
    SENTRY_DSN = os.environ.get("SENTRY_DSN") or None
    APP_VERSION = os.environ.get("APP_VERSION") or None
    # Access log: one row per served request (owner Logboek). On everywhere except tests.
    REQUEST_LOG_ENABLED = _bool("REQUEST_LOG_ENABLED", default=True)
    # Default retention (days) for request/error logs. The owner can override this live in
    # the backoffice (stored in platform_settings); this env value is only the fallback.
    LOG_RETENTION_DAYS = _int("LOG_RETENTION_DAYS", default=30)

    # ── Automatic GitHub issues on an error spike (app/alerting.py) ───────
    # Entirely opt-in: nothing leaves the server until BOTH a repo and a token are set.
    # The token needs only `issues: write` on that one repository.
    GITHUB_ISSUE_REPO = os.environ.get("GITHUB_ISSUE_REPO") or None      # e.g. "your-org/otto"
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or None
    GITHUB_ISSUE_LABELS = os.environ.get("GITHUB_ISSUE_LABELS", "bug")   # comma-separated
    GITHUB_TIMEOUT_SECONDS = _int("GITHUB_TIMEOUT_SECONDS", default=15)
    ERROR_ALERT_ENABLED = _bool("ERROR_ALERT_ENABLED", default=True)     # kill switch
    ERROR_ALERT_THRESHOLD = _int("ERROR_ALERT_THRESHOLD", default=10)    # errors per group…
    ERROR_ALERT_WINDOW_MINUTES = _int("ERROR_ALERT_WINDOW_MINUTES", default=15)  # …in this window
    ERROR_ALERT_COOLDOWN_HOURS = _int("ERROR_ALERT_COOLDOWN_HOURS", default=24)  # re-report after

    # ── Instance administrator ────────────────────────────────────────────
    # Accounts flagged is_platform_owner (the first account from /auth/setup, or via
    # `flask create-owner` / `flask grant-owner`) may use the /owner backoffice. Set
    # PLATFORM_OWNER_EMAIL to additionally pin that right to one e-mail address.
    PLATFORM_OWNER_EMAIL = os.environ.get("PLATFORM_OWNER_EMAIL") or None

    # ── Database (SQLite default; DATABASE_URL switches to Postgres) ───────
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{INSTANCE_DIR / 'otto.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Survive a database that goes away underneath us. Without pre-ping, every
    # connection still in the pool when Postgres restarts (or a proxy/firewall drops an
    # idle TCP session) is handed out dead, and EVERY request 500s with an
    # OperationalError until the workers happen to recycle — an outage that looks like
    # it "fixed itself". pre_ping costs one cheap SELECT 1 per checkout and turns that
    # into a transparent reconnect; recycle retires connections before the far side does.
    # SQLite keeps the plain defaults (single file, no server to lose).
    SQLALCHEMY_ENGINE_OPTIONS = (
        {}
        if SQLALCHEMY_DATABASE_URI.startswith("sqlite")
        else {
            "pool_pre_ping": True,
            "pool_recycle": _int("DB_POOL_RECYCLE", default=280),  # < typical 300s idle cut-off
            "pool_size": _int("DB_POOL_SIZE", default=5),
            "max_overflow": _int("DB_MAX_OVERFLOW", default=10),
            "pool_timeout": _int("DB_POOL_TIMEOUT", default=10),  # fail fast, never hang a worker
            "connect_args": {
                # A hung TCP connect/read would otherwise block a gunicorn worker forever.
                "connect_timeout": _int("DB_CONNECT_TIMEOUT", default=10),
            },
        }
    )

    # ── Background tasks (eager unless Redis is configured) ───────────────
    REDIS_URL = os.environ.get("REDIS_URL") or None
    # Run the task pipeline (sync → categorize → draft) inline instead of via a Celery
    # worker. Defaults to eager only without Redis, but can be FORCED on with
    # TASK_ALWAYS_EAGER=1 so a single web container is fully self-sufficient — no
    # separate worker/beat needed — even when Redis is configured for sessions/limits.
    TASK_ALWAYS_EAGER = _bool("TASK_ALWAYS_EAGER", default=REDIS_URL is None)

    # ── Periodic mailbox sync ─────────────────────────────────────────────
    # Default poll cadence per mailbox (each account can override it in Settings).
    SYNC_INTERVAL_SECONDS = _int("SYNC_INTERVAL_SECONDS", default=300)
    SYNC_MIN_INTERVAL_SECONDS = _int("SYNC_MIN_INTERVAL_SECONDS", default=60)
    # How often the in-app scheduler wakes to look for mailboxes that are due.
    SCHEDULER_TICK_SECONDS = _int("SCHEDULER_TICK_SECONDS", default=60)
    # In-process scheduler thread that polls mailboxes on their interval. Defaults ON
    # whenever the pipeline runs inline (eager) — so an eager single-container deploy
    # still polls without a Celery beat — and OFF when a real beat+worker drives sync
    # (to avoid double-syncing). Force either way with BACKGROUND_SYNC=1/0.
    BACKGROUND_SYNC = _bool("BACKGROUND_SYNC", default=TASK_ALWAYS_EAGER)

    # ── Daily task-digest email ───────────────────────────────────────────
    # Hour (in APP_TIMEZONE) from which the opt-in daily task email may go out.
    DAILY_EMAIL_HOUR = _int("DAILY_EMAIL_HOUR", default=7)

    # ── Rate limiting (Redis in prod, in-memory otherwise) ────────────────
    RATELIMIT_STORAGE_URI = REDIS_URL or "memory://"
    RATELIMIT_HEADERS_ENABLED = True

    # ── Session cookie (hardened in prod) ─────────────────────────────────
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False  # prod overrides to True
    SESSION_COOKIE_DOMAIN = None   # host-scoped

    # Logniveau van de applicatie. INFO, want veel degradaties (Graph-hapering, taak
    # overgeslagen, scheduler-tick) worden bewust op INFO gelogd.
    LOG_LEVEL = os.environ.get("LOG_LEVEL") or "INFO"

    # ── Registration ──────────────────────────────────────────────────────
    # Off by default: the administrator creates organisations in /owner and their
    # owners invite colleagues. On → anyone can create their own organisation at
    # /auth/register. (The very first account is always created via /auth/setup.)
    REGISTRATION_OPEN = _bool("REGISTRATION_OPEN", default=False)

    # ── LLM (stub unless a real provider + key is configured) ─────────────
    LLM_PROVIDER = os.environ.get("LLM_PROVIDER") or "stub"
    LLM_MODEL = os.environ.get("LLM_MODEL") or None
    # USD→EUR rate used to price token usage (usage overview + optional budget cap).
    LLM_USD_EUR = _float("LLM_USD_EUR", default=0.95)
    # Optional monthly AI budget per organisation (EUR) on the instance-wide key. Once
    # reached, that organisation falls back to the stub until next month. Unset = no cap.
    # An organisation's own key (Beheer → AI & model) is never capped.
    LLM_MONTHLY_BUDGET_EUR = _float("LLM_MONTHLY_BUDGET_EUR", default=0.0) or None
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY") or None
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or None
    AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT") or None
    AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY") or None
    AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT") or None

    # ── Mail providers — presence of credentials means "configured" ───────
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID") or None
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET") or None
    MS_CLIENT_ID = os.environ.get("MS_CLIENT_ID") or None
    MS_CLIENT_SECRET = os.environ.get("MS_CLIENT_SECRET") or None
    MS_TENANT_ID = os.environ.get("MS_TENANT_ID") or "common"
    # Shared secret echoed in every Graph change-notification; its presence is the
    # explicit opt-in that turns realtime (webhook) sync on. Unset → polling only.
    GRAPH_CLIENT_STATE = os.environ.get("GRAPH_CLIENT_STATE") or None

    # ── Gmail realtime (Pub/Sub push) ─────────────────────────────────────
    # Google signs every push with an OIDC token. Verifying that signature only
    # proves *some* Google project sent it, so the service-account e-mail must be
    # pinned too — otherwise anyone with a Google Cloud account could push to us.
    # Both must be set before realtime is trusted; unset → polling only.
    GMAIL_PUSH_AUDIENCE = os.environ.get("GMAIL_PUSH_AUDIENCE") or None
    GMAIL_PUSH_SA_EMAIL = os.environ.get("GMAIL_PUSH_SA_EMAIL") or None
    # The Pub/Sub topic `users.watch` publishes to, as Gmail wants it spelled:
    # projects/<project-id>/topics/<topic>. Without it no mailbox can be watched, so
    # Gmail stays on the poll however well the receiving side is configured.
    GMAIL_PUBSUB_TOPIC = os.environ.get("GMAIL_PUBSUB_TOPIC") or None

    # ── CRM connectors (an organisation's own Odoo/HubSpot/Pipedrive) ─────
    CRM_TIMEOUT_SECONDS = _int("CRM_TIMEOUT_SECONDS", default=10)

    # ── Outlook add-in ────────────────────────────────────────────────────
    # The add-in manifest needs a GUID that is unique per installation (two Otto
    # instances with the same id clash inside one Microsoft 365 tenant). Unset → derived
    # from SECRET_KEY, which is unique per installation. Override with a fixed GUID
    # (`python -c "import uuid; print(uuid.uuid4())"`) if you ever rotate SECRET_KEY.
    ADDIN_ID = os.environ.get("ADDIN_ID") or None

    # ── Security ──────────────────────────────────────────────────────────
    # Valid Fernet key so token encryption works out-of-the-box in dev/test.
    # Production generates its own (entrypoint) and must set it via the environment.
    TOKEN_ENCRYPTION_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY") or _DEFAULT_TOKEN_KEY
    WTF_CSRF_TIME_LIMIT = None  # tie CSRF token lifetime to the session
    # Trust X-Forwarded-* headers — ONLY enable behind a known reverse proxy.
    # ProdConfig turns this on so rate-limiting sees the real client IP and
    # generated URLs use https. Left off in dev so headers can't be spoofed.
    TRUST_PROXY = _bool("TRUST_PROXY", default=False)

    # ── Derived capability flags (injected into templates) ────────────────
    @property
    def GMAIL_CONFIGURED(self) -> bool:
        return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET)

    @property
    def GRAPH_CONFIGURED(self) -> bool:
        return bool(self.MS_CLIENT_ID and self.MS_CLIENT_SECRET)

    @property
    def GMAIL_REALTIME(self) -> bool:
        """Realtime Gmail push needs all three legs of the round trip: a topic to watch
        onto, and the audience + service account that let us trust what comes back.
        Miss one and the push either never leaves Google or is (rightly) ignored here,
        so realtime stays off and the mailbox syncs on the poll."""
        return bool(self.GMAIL_PUBSUB_TOPIC and self.GMAIL_PUSH_AUDIENCE and self.GMAIL_PUSH_SA_EMAIL)

    @property
    def GMAIL_REALTIME_MISSING(self) -> list[str]:
        """Which of those settings are still empty — named so an admin can act on the
        status screen instead of guessing which of the three is the problem."""
        return [name for name in ("GMAIL_PUBSUB_TOPIC", "GMAIL_PUSH_AUDIENCE", "GMAIL_PUSH_SA_EMAIL")
                if not getattr(self, name)]

    @property
    def GRAPH_REALTIME(self) -> bool:
        """True only when Graph webhook subscriptions can run: opted in via a
        client-state secret, reachable on a public HTTPS URL, and with a real
        worker/beat (non-eager) to renew them. Otherwise we fall back to polling."""
        return bool(
            self.GRAPH_CLIENT_STATE
            and (self.PUBLIC_BASE_URL or "").startswith("https://")
            and not self.TASK_ALWAYS_EAGER
        )

    @property
    def SMTP_CONFIGURED(self) -> bool:
        return bool(self.SMTP_HOST)

    @property
    def GRAPH_MAIL_CONFIGURED(self) -> bool:
        return bool(self.GRAPH_MAIL_CLIENT_ID and self.GRAPH_MAIL_CLIENT_SECRET
                    and self.GRAPH_MAIL_TENANT_ID and self.GRAPH_MAIL_SENDER)

    @property
    def KNOWLEDGE_CONFIGURED(self) -> bool:
        """True when the app-only SharePoint search connector is wired."""
        return bool(self.GRAPH_KNOWLEDGE_CLIENT_ID and self.GRAPH_KNOWLEDGE_CLIENT_SECRET
                    and self.GRAPH_KNOWLEDGE_TENANT_ID)

    @property
    def MAIL_CONFIGURED(self) -> bool:
        """True when a real mailer is wired (SMTP or Microsoft Graph)."""
        if self.MAIL_PROVIDER == "graph":
            return self.GRAPH_MAIL_CONFIGURED
        return self.SMTP_CONFIGURED

    @property
    def LLM_IS_STUB(self) -> bool:
        return self.LLM_PROVIDER == "stub"

    @property
    def DEMO_MODE(self) -> bool:
        """True when no real mailbox provider is wired — the app rides on seed data."""
        return not (self.GMAIL_CONFIGURED or self.GRAPH_CONFIGURED)


class DevConfig(BaseConfig):
    DEBUG = True
    SHOW_VERIFY_LINK = True  # localhost dev: surface the link (no SMTP needed)


class TestingConfig(BaseConfig):
    TESTING = True
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    # Keep the single in-memory connection alive across the app/threads.
    from sqlalchemy.pool import StaticPool

    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
    TASK_ALWAYS_EAGER = True
    BACKGROUND_SYNC = False  # never spawn the poller thread in the test process
    LOG_LEVEL = "WARNING"            # houdt de testuitvoer leesbaar
    REGISTRATION_OPEN = True  # tests registreren volop; de gesloten modus heeft eigen tests
    LLM_PROVIDER = "stub"
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False  # individual tests opt in
    SECRET_KEY = "testing"


class ProdConfig(BaseConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"
    TRUST_PROXY = _bool("TRUST_PROXY", default=True)

    def __init__(self) -> None:
        # Fail fast on missing/insecure critical secrets in production.
        for key in ("SECRET_KEY", "TOKEN_ENCRYPTION_KEY"):
            val = str(getattr(self, key, None) or "").lower()
            if not val or any(marker in val for marker in ("insecure", "change-me", "change_me", "changeme")):
                raise RuntimeError(f"{key} must be set to a secure value in production")
        if self.TOKEN_ENCRYPTION_KEY == _DEFAULT_TOKEN_KEY:
            raise RuntimeError(
                "TOKEN_ENCRYPTION_KEY is still the public default — generate a unique "
                "Fernet key (python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\")."
            )


_CONFIGS = {"dev": DevConfig, "testing": TestingConfig, "prod": ProdConfig}


def get_config(name: str | None = None) -> BaseConfig:
    name = (name or os.environ.get("FLASK_CONFIG", "dev")).lower()
    return _CONFIGS.get(name, DevConfig)()
