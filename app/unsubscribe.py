"""List-Unsubscribe parsing + safe one-click execution (RFC 2369 / RFC 8058).

Marketing mail advertises how to unsubscribe in the ``List-Unsubscribe`` header — a
comma-separated list of angle-bracketed URIs, e.g.::

    List-Unsubscribe: <https://news.example.com/u/abc>, <mailto:unsub@example.com?subject=stop>

When ``List-Unsubscribe-Post: List-Unsubscribe=One-Click`` is also present the https
target accepts a one-click POST (RFC 8058) — the same mechanism Gmail and Apple Mail
use to unsubscribe without opening a page. We execute that server-side; otherwise we
hand the link/mailto back to the user's browser.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

_URI_RE = re.compile(r"<([^>]+)>")


@dataclass
class UnsubInfo:
    url: str | None = None        # http(s) unsubscribe endpoint
    mailto: str | None = None     # mailto: unsubscribe URI (incl. any ?subject=)
    one_click: bool = False       # RFC 8058 one-click POST supported (https only)

    @property
    def available(self) -> bool:
        return bool(self.url or self.mailto)


def parse_list_unsubscribe(header: str | None, post_header: str | None = None) -> UnsubInfo:
    """Parse the ``List-Unsubscribe`` (+ ``-Post``) headers into a structured result.

    Prefers the first https URL and the first mailto found. One-click is only reported
    when the ``-Post`` header opts in AND we have an https endpoint (POSTing to plain
    http, or without the sender's opt-in, is not spec-compliant)."""
    info = UnsubInfo()
    if not header:
        return info
    for raw in _URI_RE.findall(header):
        uri = raw.strip()
        low = uri.lower()
        if low.startswith("mailto:") and not info.mailto:
            info.mailto = uri
        elif low.startswith(("https://", "http://")) and not info.url:
            info.url = uri
    if post_header and "one-click" in post_header.lower():
        info.one_click = bool(info.url and info.url.lower().startswith("https://"))
    return info


def is_safe_public_url(url: str) -> bool:
    """SSRF guard for the server-side one-click POST: https only, a resolvable host, and
    no loopback / private / link-local / reserved address behind that host."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for *_, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects during the one-click POST.

    ``is_safe_public_url`` only vets the *initial* host; a sender-controlled endpoint could
    otherwise pass that check and then 30x-redirect us to an internal address (cloud
    metadata, a private IP) — the redirect target is never re-validated. Not following the
    redirect closes that SSRF hole. A 3xx is still surfaced (as an HTTPError below) and, per
    the existing contract, treated as success — many real unsubscribe endpoints answer 3xx."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def one_click_post(url: str, timeout: float = 6.0) -> tuple[bool, str]:
    """Perform the RFC 8058 one-click unsubscribe POST. Returns ``(ok, detail)`` where
    detail is a short HTTP status / error string for the log. Never raises.

    Redirects are NOT followed — only the initial URL is SSRF-validated, so following a
    redirect would let a sender bounce the request to internal infrastructure."""
    if not is_safe_public_url(url):
        return False, "onveilige of onbereikbare URL"
    body = urllib.parse.urlencode({"List-Unsubscribe": "One-Click"}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "Otto-Unsubscribe/1.0"},
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:  # noqa: S310 (host validated, no redirects)
            code = resp.getcode()
        return (200 <= code < 400), f"HTTP {code}"
    except urllib.error.HTTPError as exc:
        # Many endpoints answer 2xx; some answer 3xx (which we no longer follow). Treat
        # <400 as success either way; a real redirect means the request was accepted.
        return (exc.code < 400), f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, str(exc)[:200]
