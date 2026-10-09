"""Content-Security-Policy: nonce-gebaseerd script-src.

Een nonce is niet optioneel-strenger maar exclusief: zodra hij aanwezig is, negeert de
browser 'unsafe-inline' volledig. Elk inline event-handler-attribuut in een template
zou dus stil stoppen met werken. Deze tests bewaken die afspraak — inclusief een sweep
over álle templates, zodat een nieuw onclick= meteen opvalt in plaats van in productie.
"""
from __future__ import annotations

import pathlib
import re

TEMPLATES = pathlib.Path("app/templates")


def _csp(client, path="/auth/login"):
    return client.get(path).headers.get("Content-Security-Policy", "")


# ── De header ────────────────────────────────────────────────────────────────
def test_script_src_uses_a_nonce_and_not_unsafe_inline(client):
    policy = _csp(client)
    script_src = next(p for p in policy.split(";") if p.strip().startswith("script-src"))
    assert "'nonce-" in script_src
    assert "'unsafe-inline'" not in script_src


def test_nonce_differs_per_request(client):
    """Een herbruikte nonce is geen nonce meer — dan raadt een aanvaller hem gewoon."""
    assert _csp(client) != _csp(client)


def test_page_scripts_carry_the_request_nonce(client):
    resp = client.get("/auth/login")
    policy = resp.headers["Content-Security-Policy"]
    nonce = re.search(r"'nonce-([^']+)'", policy).group(1)
    html = resp.get_data(as_text=True)
    for tag in re.findall(r"<script[^>]*>", html):
        if "src=" in tag:
            continue  # externe scripts vallen onder de host-allowlist
        assert f'nonce="{nonce}"' in tag, f"inline script zonder nonce: {tag[:80]}"


def test_fonts_are_self_hosted(client):
    """Het merklettertype staat onder /static/fonts: geen enkele bezoeker-request naar
    een fonthost van derden, dus die hosts horen ook niet in de policy."""
    policy = _csp(client)
    assert "fonts.googleapis.com" not in policy
    assert "fonts.gstatic.com" not in policy
    assert "font-src 'self'" in policy


def test_the_dangerous_directives_stay_locked(client):
    policy = _csp(client)
    assert "object-src 'none'" in policy
    assert "base-uri 'self'" in policy
    assert "form-action 'self'" in policy


# ── De templates ─────────────────────────────────────────────────────────────
INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=\s*[\"']", re.IGNORECASE)


def test_no_template_uses_an_inline_event_handler():
    """Zou stil breken onder de nonce-CSP. Gebruik data-attributen + behaviors.js."""
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if INLINE_HANDLER.search(line):
                offenders.append(f"{path.relative_to(TEMPLATES)}:{i}")
    assert not offenders, "inline event-handlers gevonden: " + ", ".join(offenders)


def test_every_inline_script_in_every_template_has_a_nonce():
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        for tag in re.findall(r"<script[^>]*>", path.read_text(encoding="utf-8")):
            if "src=" not in tag and "nonce=" not in tag:
                offenders.append(f"{path.relative_to(TEMPLATES)}: {tag[:60]}")
    assert not offenders, "inline scripts zonder nonce: " + ", ".join(offenders)


def test_behaviors_script_is_loaded(client):
    assert "js/behaviors.js" in client.get("/auth/login").get_data(as_text=True)
