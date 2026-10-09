"""Human-friendly inbox timestamps. Provider times are UTC; we display them in the
configured timezone (APP_TIMEZONE) as a short relative label like the prototype:
"08:42" today, "gisteren", a weekday this week, else "DD/MM"."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, UTC

_WEEKDAYS_NL = ["ma", "di", "wo", "do", "vr", "za", "zo"]  # Monday=0 … Sunday=6
_MONTHS_NL = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]


def _tz():
    from zoneinfo import ZoneInfo

    from flask import current_app

    name = (current_app.config.get("APP_TIMEZONE") if current_app else None) or "Europe/Brussels"
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — a bad tz name must never break rendering
        return ZoneInfo("Europe/Brussels")


def parse_graph_dt(value) -> datetime | None:
    """Graph datetime (ISO-8601, e.g. '2026-06-27T08:42:00Z') → naive-UTC.

    Tolerates the 7-digit fractional seconds Graph emits for To Do (`2026-...T08:00:00.0000000`),
    which `datetime.fromisoformat` rejects — trim to the 6 digits it accepts.
    """
    if not value:
        return None
    s = re.sub(r"(\.\d{6})\d+", r"\1", str(value).replace("Z", "+00:00"))
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


def parse_epoch_ms(value) -> datetime | None:
    """Gmail `internalDate` (epoch milliseconds, UTC) → naive-UTC datetime."""
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def reltime(value, fallback: str = "") -> str:
    """Short inbox label for a naive-UTC datetime, in APP_TIMEZONE. Anything that
    isn't a datetime (e.g. None on seed rows) returns `fallback` unchanged."""
    if not isinstance(value, datetime):
        return fallback
    tz = _tz()
    local = value.replace(tzinfo=UTC).astimezone(tz)
    today = datetime.now(tz).date()
    days = (today - local.date()).days
    if days <= 0:
        return local.strftime("%H:%M")
    if days == 1:
        return "gisteren"
    if days < 7:
        return _WEEKDAYS_NL[local.weekday()]
    return local.strftime("%d/%m")


def fulltime(value, fallback: str = "") -> str:
    """Full, unambiguous date + time for an opened item, in APP_TIMEZONE — e.g.
    '27 jun 2026, 14:32'. Non-datetimes return `fallback` unchanged."""
    if not isinstance(value, datetime):
        return fallback
    local = value.replace(tzinfo=UTC).astimezone(_tz())
    return f"{local.day} {_MONTHS_NL[local.month - 1]} {local.year}, {local:%H:%M}"


def received_from_hint(hint: str):
    """Best-effort real datetime (naive-UTC) from a demo 'time' label like '08:42',
    'gisteren' or a weekday ('ma'). Returns None when the hint isn't a clock/day
    label (e.g. 'nu') — callers then fall back to a stored timestamp."""
    h = (hint or "").strip().lower()
    now = datetime.now(_tz())
    base = None
    if ":" in h:
        try:
            hh, mm = h.split(":")[:2]
            base = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            if base > now:
                base -= timedelta(days=1)
        except (ValueError, TypeError):
            base = None
    elif h == "gisteren":
        base = (now - timedelta(days=1)).replace(hour=14, minute=30, second=0, microsecond=0)
    elif h in _WEEKDAYS_NL:
        delta = (now.weekday() - _WEEKDAYS_NL.index(h)) % 7 or 7  # most recent past weekday
        base = (now - timedelta(days=delta)).replace(hour=11, minute=0, second=0, microsecond=0)
    if base is None:
        return None
    return base.astimezone(UTC).replace(tzinfo=None)
