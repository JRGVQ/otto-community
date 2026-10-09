"""Error pages that cannot themselves fail.

The styled error templates all extend `base.html`, whose context processor loads the
current user, the org's connector state and the language catalogue — every one of which
touches the database. So in the failure that matters most (the database is unreachable)
rendering the 500 page raised a *second* exception inside the error handler and Flask
fell back to its bare "Internal Server Error" text: no branding, no request id, nothing
to quote to support.

`render_error` therefore tries the pretty page and, if that raises for any reason,
serves a small self-contained page built from a plain f-string — no template engine, no
context processors, no database. It always carries the request id, which is the thread
back to the matching row in the owner Logboek (/owner/logs) and the error console
(/owner/debug).
"""
from __future__ import annotations

from flask import g, render_template

# Kept deliberately dumb: inline CSS, no url_for, no template inheritance, no i18n
# lookup. Everything this page needs is in this string.
_FALLBACK = """<!doctype html>
<html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Otto</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:#F7F7FB; color:#1A1A2E; }}
  .card {{ max-width:30rem; padding:2.5rem; text-align:center; background:#fff;
          border-radius:16px; box-shadow:0 6px 30px rgba(0,0,0,.08); margin:1rem; }}
  .code {{ font-size:56px; font-weight:800; color:#6C4CE6; letter-spacing:-.03em; }}
  h1 {{ font-size:18px; margin:.25rem 0 .5rem; }}
  p {{ font-size:14px; color:#5A5A72; margin:0 0 1.25rem; }}
  .rid {{ font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace; color:#8A8AA3; }}
  a.btn {{ display:inline-block; padding:.6rem 1.1rem; border-radius:10px; background:#6C4CE6;
          color:#fff; text-decoration:none; font-weight:600; font-size:14px; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background:#14141F; color:#EDEDF5; }}
    .card {{ background:#1D1D2B; box-shadow:none; }}
    p {{ color:#A0A0BB; }}
  }}
</style></head>
<body><div class="card">
  <div class="code">{code}</div>
  <h1>{title}</h1>
  <p>{message}</p>
  <p><a class="btn" href="/">Terug naar Otto</a></p>
  {rid}
</div></body></html>
"""

# Dutch copy for the bare fallback. The styled templates keep their own translations;
# this page only appears when the machinery behind those translations is unavailable.
_COPY = {
    403: ("Geen toegang", "Je hebt geen toegang tot deze pagina."),
    404: ("Pagina niet gevonden", "Deze pagina bestaat niet (meer)."),
    429: ("Even rustig aan", "Te veel aanvragen na elkaar. Probeer het zo opnieuw."),
    500: ("Er ging iets mis", "Er is een storing aan onze kant. We zijn verwittigd — "
                              "probeer het over een minuut opnieuw."),
    503: ("Tijdelijk niet beschikbaar", "Otto is even niet bereikbaar. Probeer het zo opnieuw."),
}


def request_id() -> str | None:
    """The current request's correlation id, if one was stamped."""
    return getattr(g, "request_id", None)


def fallback_page(code: int) -> str:
    title, message = _COPY.get(code, _COPY[500])
    rid = request_id()
    return _FALLBACK.format(
        code=code, title=title, message=message,
        rid=f'<div class="rid">Foutcode: {rid}</div>' if rid else "",
    )


def render_error(code: int, template: str | None = None, **extra) -> tuple[str, int]:
    """Render the styled error page, degrading to a dependency-free one if that fails.

    Never raises — an exception escaping an error handler is what produced the bare
    server text this exists to avoid.
    """
    try:
        return render_template(template or f"errors/{code}.html",
                               request_id=request_id(), **extra), code
    except Exception:  # noqa: BLE001 — the DB/session/templating may be exactly what broke
        try:
            from flask import current_app

            current_app.logger.warning("error page %s failed to render; served fallback", code,
                                       exc_info=True)
        except Exception:  # noqa: BLE001
            pass
        return fallback_page(code), code
