"""Automatic GitHub issues when the app starts failing in bulk.

A single 500 is noise; the same 500 fifty times in a quarter of an hour is an incident,
and until now the only way to notice one was to open the backoffice and look. This module
watches the error log, and when one *kind* of failure crosses a threshold inside a rolling
window it opens a GitHub issue describing it.

Design notes:

* **Grouped, not per-error.** Errors are fingerprinted by exception type + endpoint, so a
  storm of one bug produces one issue, not five hundred.
* **Cooled down.** A fingerprint that has already been reported stays quiet for
  ERROR_ALERT_COOLDOWN_HOURS, so a bug that keeps firing doesn't file a new issue every
  tick. The mapping lives in `platform_settings`, i.e. it survives a restart.
* **Off by default.** Nothing is ever sent until GITHUB_ISSUE_REPO *and* GITHUB_TOKEN are
  configured. No token, no outbound call.
* **Runs on the scheduler tick**, never inside a web request — reporting an error must not
  add latency to (or be able to break) the request that produced it.
* **Scrubbed.** Tracebacks and messages can quote customer data, and a GitHub issue is a
  far less private place than the database. E-mail addresses are redacted before sending.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import timedelta

from flask import current_app

from app.models.base import utcnow
from app.platform_settings import get_setting, set_setting

GITHUB_API = "https://api.github.com"
_SETTING_PREFIX = "gh_issue:"

# Anything shaped like an e-mail address is customer data; it has no diagnostic value in
# an issue that the traceback's file/line doesn't already carry.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _cfg(key: str, default=None):
    return current_app.config.get(key, default)


def is_enabled() -> bool:
    """True when both a target repository and a token are configured."""
    return bool(_cfg("GITHUB_ISSUE_REPO") and _cfg("GITHUB_TOKEN")
                and _cfg("ERROR_ALERT_ENABLED", True))


def scrub(text: str | None) -> str:
    """Redact e-mail addresses before anything leaves for GitHub."""
    return _EMAIL_RE.sub("[e-mail]", text or "")


def fingerprint(exception_type: str | None, endpoint: str | None) -> str:
    """Stable short id for 'this kind of failure, in this place'."""
    raw = f"{exception_type or '?'}|{endpoint or '?'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Cooldown bookkeeping ─────────────────────────────────────────────────────
def _cooldown_key(fp: str) -> str:
    return f"{_SETTING_PREFIX}{fp}"


def _recently_reported(fp: str) -> bool:
    raw = get_setting(_cooldown_key(fp))
    if not raw:
        return False
    stamp = raw.split("@", 1)[-1]
    try:
        from datetime import datetime

        when = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return False
    hours = int(_cfg("ERROR_ALERT_COOLDOWN_HOURS", 24) or 24)
    return when > utcnow() - timedelta(hours=hours)


def _mark_reported(fp: str, issue_number: int | None) -> None:
    set_setting(_cooldown_key(fp), f"{issue_number or '?'}@{utcnow().isoformat()}")


# ── GitHub ───────────────────────────────────────────────────────────────────
def create_issue(title: str, body: str) -> int | None:
    """Open one GitHub issue. Returns its number, or None when it could not be created."""
    repo, token = _cfg("GITHUB_ISSUE_REPO"), _cfg("GITHUB_TOKEN")
    payload = {"title": title[:250], "body": body[:60000]}
    labels = [s for s in (_cfg("GITHUB_ISSUE_LABELS") or "").split(",") if s.strip()]
    if labels:
        payload["labels"] = [s.strip() for s in labels]
    req = urllib.request.Request(
        f"{GITHUB_API}/repos/{repo}/issues",
        data=json.dumps(payload).encode(), method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "otto-alerting",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=int(_cfg("GITHUB_TIMEOUT_SECONDS", 15) or 15)) as resp:  # noqa: S310
            return (json.loads(resp.read().decode() or "{}") or {}).get("number")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        current_app.logger.warning("[alerting] GitHub issue failed: HTTP %s %s", exc.code, detail)
    except Exception as exc:  # noqa: BLE001 — network/DNS/timeout
        current_app.logger.warning("[alerting] GitHub issue failed: %s", exc)
    return None


# ── The sweep ────────────────────────────────────────────────────────────────
def _groups(window_minutes: int) -> list[dict]:
    """Error groups inside the window, biggest first."""
    from sqlalchemy import func

    from app.extensions import db
    from app.models import ErrorLog

    since = utcnow() - timedelta(minutes=window_minutes)
    rows = (
        db.session.query(
            ErrorLog.exception_type, ErrorLog.endpoint,
            func.count(ErrorLog.id).label("n"),
            func.max(ErrorLog.id).label("last_id"),
        )
        .filter(ErrorLog.occurred_at >= since)
        .group_by(ErrorLog.exception_type, ErrorLog.endpoint)
        .order_by(func.count(ErrorLog.id).desc())
        .all()
    )
    return [{"type": r[0], "endpoint": r[1], "count": r[2], "last_id": r[3]} for r in rows]


def _issue_text(group: dict, window_minutes: int) -> tuple[str, str]:
    from app.extensions import db
    from app.models import ErrorLog

    sample = db.session.get(ErrorLog, group["last_id"])
    title = (f"[auto] {group['type'] or 'Fout'} op {group['endpoint'] or 'onbekend'} "
             f"— {group['count']}× in {window_minutes} min")
    version = _cfg("APP_VERSION") or "onbekend"
    lines = [
        "> Automatisch aangemaakt door Otto omdat deze fout een drempel overschreed.",
        "",
        f"- **Aantal**: {group['count']}× binnen {window_minutes} minuten",
        f"- **Type**: `{group['type'] or '?'}`",
        f"- **Endpoint**: `{group['endpoint'] or '?'}`",
        f"- **Versie**: `{version}`",
    ]
    if sample is not None:
        lines += [
            f"- **Pad**: `{sample.method or ''} {scrub(sample.path) or '?'}`",
            f"- **Laatst gezien**: {sample.occurred_at:%Y-%m-%d %H:%M} UTC",
            f"- **Request-id**: `{sample.request_id or '—'}`",
            "",
            "### Melding",
            "```",
            scrub(sample.short_message or sample.message or "")[:1000],
            "```",
        ]
        if sample.traceback:
            lines += ["", "<details><summary>Traceback</summary>", "",
                      "```python", scrub(sample.traceback)[:6000], "```", "", "</details>"]
    lines += ["", "_Volledige context in de backoffice: `/owner/debug` en `/owner/logs`._"]
    return title, "\n".join(lines)


def check_error_spike() -> int:
    """Open a GitHub issue for every error group over the threshold. Returns how many
    issues were created. Never raises — it runs inside the scheduler loop."""
    try:
        if not is_enabled():
            return 0
        window = int(_cfg("ERROR_ALERT_WINDOW_MINUTES", 15) or 15)
        threshold = int(_cfg("ERROR_ALERT_THRESHOLD", 10) or 10)
        created = 0
        for group in _groups(window):
            if group["count"] < threshold:
                break  # ordered by count — nothing after this can qualify either
            fp = fingerprint(group["type"], group["endpoint"])
            if _recently_reported(fp):
                continue
            title, body = _issue_text(group, window)
            number = create_issue(title, body)
            # Mark reported either way: a failed call must not retry every single tick.
            _mark_reported(fp, number)
            if number:
                created += 1
                current_app.logger.warning(
                    "[alerting] opened GitHub issue #%s for %s×%s on %s",
                    number, group["count"], group["type"], group["endpoint"],
                )
        return created
    except Exception:  # noqa: BLE001 — alerting must never break the scheduler
        try:
            current_app.logger.warning("[alerting] error-spike check failed", exc_info=True)
        except Exception:  # noqa: BLE001
            pass
        return 0
