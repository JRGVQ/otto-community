"""The few routes that must work without a login.

There is no public website: `/` sends visitors to the app (or the login / first-run
setup). What remains here is what logged-out visitors and e-mail recipients still need:
the UI-language switch and the signature-banner tracking links that are embedded in
mail that was already sent (their paths must therefore never change).
"""
from __future__ import annotations

from flask import Blueprint, Response, current_app, redirect, request, url_for
from flask_login import current_user

public_bp = Blueprint("public", __name__)


@public_bp.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("web.inbox"))
    from app.auth.routes import needs_setup

    if needs_setup():
        return redirect(url_for("auth.setup"))
    return redirect(url_for("auth.login"))


@public_bp.route("/robots.txt")
def robots_txt():
    """A self-hosted instance has nothing to index."""
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")


@public_bp.route("/lang/<code>")
def set_language(code: str):
    """Switch the UI language: persist the choice in a cookie, then return to
    where the visitor was."""
    languages = current_app.config["LANGUAGES"]
    # Only redirect to a safe, same-origin relative path (never an absolute URL
    # from the Referer/next param — that would be an open-redirect).
    target = request.args.get("next") or request.referrer or "/"
    if not target.startswith("/") or target.startswith("//"):
        target = "/"
    resp = redirect(target)
    if code in languages:
        resp.set_cookie(
            "lang", code, max_age=60 * 60 * 24 * 365,
            samesite="Lax", secure=request.is_secure,
        )
    return resp


@public_bp.route("/t/b/<token>")
def banner_pixel_track(token: str):
    """Publieke tracking-pixel voor een banner-weergave (opt-in). Telt alleen een aggregaat;
    geen persoonsgegevens. Geeft altijd een 1×1 GIF terug (ook bij een ongeldige token)."""
    from app.signature_analytics import pixel_gif, read_banner_token, record_impression

    banner_id = read_banner_token(token)
    if banner_id is not None:
        try:
            record_impression(banner_id)
        except Exception:  # noqa: BLE001 — meting mag nooit een fout aan de ontvanger tonen
            current_app.logger.info("Banner-weergave tellen overgeslagen")
    resp = Response(pixel_gif(), mimetype="image/gif")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    return resp


@public_bp.route("/r/b/<token>")
def banner_click_track(token: str):
    """Publieke click-redirect voor een banner: telt de click en stuurt door naar de link
    van díe banner.

    Het doel komt uit de database, nooit uit de URL: de `?u=`-parameter in oudere links
    wordt genegeerd, zodat deze route geen open redirect is. Een ongeldige token of een
    banner zonder (geldige) link gaat naar de startpagina."""
    from urllib.parse import urlparse

    from app.extensions import db
    from app.models import SignatureBanner
    from app.signature_analytics import read_banner_token, record_click

    banner_id = read_banner_token(token)
    banner = db.session.get(SignatureBanner, banner_id) if banner_id is not None else None
    target = (banner.link_url or "").strip() if banner else ""
    if urlparse(target).scheme.lower() not in ("http", "https"):
        return redirect(url_for("public.home"))
    try:
        record_click(banner.id)
    except Exception:  # noqa: BLE001
        current_app.logger.info("Banner-click tellen overgeslagen")
    return redirect(target, code=302)
