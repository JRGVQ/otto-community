"""Meting van banner-campagnes: weergaven (tracking-pixel) en clicks (redirect).

Opt-in per banner (`SignatureBanner.track`). Er worden alleen **aggregaten per dag** bewaard
(`BannerStat`: weergaven + clicks) — geen IP, geen ontvanger, geen persoonsgegevens; AVG-
vriendelijk. De tracking-URL's zijn publiek (de ontvanger is niet ingelogd) en gebruiken een
ondertekende token, zodat alleen geldige banner-id's geteld worden. De click-redirect
stuurt altijd door naar de link die bij de banner hoort (nooit naar een URL uit de link).

De resolver (app/signatures.active_banner_html) roept banner_pixel/​banner_redirect aan om de
banner-HTML te verrijken; de publieke endpoints staan in app/web/routes_public.py.
"""
from __future__ import annotations

import urllib.parse

from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer

from app.extensions import db
from app.models.base import utcnow

# 1×1 transparante GIF.
_PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00!\xf9\x04\x01\x00"
    b"\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def pixel_gif() -> bytes:
    return _PIXEL_GIF


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="sig-banner-track")


def banner_token(banner_id: int) -> str:
    return _serializer().dumps(int(banner_id))


def read_banner_token(token: str) -> int | None:
    try:
        return int(_serializer().loads(token))
    except (BadSignature, ValueError, TypeError):
        return None


def _base_url() -> str:
    return (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")


def banner_pixel(banner_id: int) -> str:
    """De verborgen tracking-pixel-<img> (absolute URL) voor een banner."""
    url = f"{_base_url()}/t/b/{banner_token(banner_id)}"
    return f'<img src="{url}" width="1" height="1" alt="" style="display:none;border:0;">'


def banner_redirect(banner_id: int, target: str) -> str:
    """Een click-tracking-redirect-URL (absolute) die na tellen naar `target` stuurt."""
    q = urllib.parse.urlencode({"u": target})
    return f"{_base_url()}/r/b/{banner_token(banner_id)}?{q}"


def _bump(banner_id: int, field: str) -> None:
    """Verhoog de dagteller van een banner (weergaven of clicks). Best-effort, publiek."""
    from app.models import BannerStat, SignatureBanner

    banner = db.session.get(SignatureBanner, banner_id)
    if banner is None:
        return
    today = utcnow().date()
    row = db.session.query(BannerStat).filter_by(banner_id=banner_id, day=today).first()
    if row is None:
        row = BannerStat(organization_id=banner.organization_id, banner_id=banner_id, day=today,
                         impressions=0, clicks=0)
        db.session.add(row)
    setattr(row, field, (getattr(row, field) or 0) + 1)
    db.session.commit()


def record_impression(banner_id: int) -> None:
    _bump(banner_id, "impressions")


def record_click(banner_id: int) -> None:
    _bump(banner_id, "clicks")


def banner_totals(org_id: int) -> dict[int, dict]:
    """{banner_id: {impressions, clicks}} — totalen over alle dagen voor het dashboard."""
    from sqlalchemy import func

    from app.models import BannerStat

    rows = (db.session.query(BannerStat.banner_id,
                             func.sum(BannerStat.impressions), func.sum(BannerStat.clicks))
            .filter(BannerStat.organization_id == org_id)
            .group_by(BannerStat.banner_id).all())
    return {bid: {"impressions": int(imp or 0), "clicks": int(clk or 0)} for bid, imp, clk in rows}
