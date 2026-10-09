"""Domain verification — claim a domain, prove it with a DNS TXT record, enforce it.

Why: `SsoConfig.allowed_domains` is free text, so without proof of control one company
could list another company's domain and intercept its SSO routing (or split a customer
across two tenants by accident). Verification closes that: a verified domain belongs to
exactly one organisation, and that exclusivity is what SSO and registration check.

DNS resolution needs `dnspython`, which is an optional dependency like every other real
integration in Otto. Without it verification fails *closed* with a clear message — it
never silently reports a domain as verified.
"""
from __future__ import annotations

import re

from app.extensions import db
from app.models import DomainClaim
from app.models.base import utcnow
from app.models.domain import VALUE_PREFIX

# Public-suffix-ish sanity check: at least one dot, no scheme, no path, no whitespace.
_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

# Domains nobody may claim: public mailbox providers. Verifying one is impossible anyway
# (no control over the zone), but rejecting them up front gives a much better error.
PUBLIC_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "icloud.com", "me.com", "proton.me", "protonmail.com", "telenet.be",
    "skynet.be", "aol.com", "gmx.com", "gmx.net", "web.de", "mail.com", "zoho.com",
})


class DnsUnavailable(RuntimeError):
    """Raised when no DNS resolver is installed, so a check cannot be trusted either way."""


def normalize_domain(raw: str) -> str | None:
    """'https://WWW.Acme.be/path' → 'acme.be'. None when it isn't a usable domain."""
    d = (raw or "").strip().lower()
    d = re.sub(r"^[a-z]+://", "", d)      # scheme
    d = d.split("/", 1)[0].split("?", 1)[0]  # path/query
    d = d.split("@")[-1]                   # someone pasting an e-mail address
    d = d.strip().strip(".")
    if d.startswith("www."):
        d = d[4:]
    return d if _DOMAIN_RE.match(d) else None


# Characters a pasted address routinely carries but a real address never contains:
# every flavour of whitespace (including the non-breaking space Word/Outlook insert)
# and the zero-width marks a browser, PDF or chat client leaves behind. They are removed
# *anywhere* in the string, not just at the ends where .strip() stops.
_EMAIL_JUNK_RE = re.compile(r"[\s\u00a0\u180e\u200b-\u200f\u2060\ufeff]+")


def normalize_email(raw: str | None) -> str:
    """A pasted address → the address Otto stores, compares and validates on.

    A trailing space is the single most common signup failure: the address itself is
    correct, but `Email()` sees "jij@bedrijf.be " and rejects it as invalid, and nothing
    on screen shows what the difference is. No legitimate address is changed by cleaning
    this up, so every entry point normalises instead of complaining. Same for the
    `<...>` and `mailto:` wrappers you get from copying out of a mail client.
    """
    email = _EMAIL_JUNK_RE.sub("", raw or "").strip("<>")
    if email[:7].lower() == "mailto:":
        email = email[7:].strip("<>")
    return email.lower()


def domain_of(email: str) -> str:
    email = normalize_email(email)
    return email.rsplit("@", 1)[-1] if "@" in email else ""


# ── DNS ──────────────────────────────────────────────────────────────────────
def resolve_txt(name: str) -> list[str]:
    """TXT values for `name`. Raises DnsUnavailable when no resolver is installed.

    An empty list means "resolved fine, no such record" (or NXDOMAIN) — a legitimate
    'not verified yet', as opposed to 'could not check'.
    """
    try:
        import dns.resolver  # type: ignore
    except ImportError as exc:  # optional dependency, like every other integration
        raise DnsUnavailable("dnspython is niet geïnstalleerd") from exc

    try:
        answers = dns.resolver.resolve(name, "TXT", lifetime=10)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except Exception as exc:  # timeout, SERVFAIL, no nameservers → cannot conclude
        raise DnsUnavailable(str(exc) or "DNS-opzoeking mislukt") from exc

    out: list[str] = []
    for rdata in answers:
        # A TXT record is a sequence of strings; DNS splits values over 255 bytes, and
        # the parts must be joined without a separator to get the original value back.
        parts = getattr(rdata, "strings", None)
        if parts:
            out.append(b"".join(parts).decode("utf-8", "replace"))
        else:
            out.append(str(rdata).strip('"'))
    return out


# ── Claims ───────────────────────────────────────────────────────────────────
def claim_domain(org, raw: str) -> tuple[DomainClaim | None, str | None]:
    """Start (or return) this org's claim on `raw`. Returns (claim, error_message)."""
    domain = normalize_domain(raw)
    if not domain:
        return None, "Dat is geen geldige domeinnaam (bijvoorbeeld: bedrijf.be)."
    if domain in PUBLIC_DOMAINS:
        return None, "Publieke e-mailproviders kunnen niet geclaimd worden."

    existing = db.session.query(DomainClaim).filter_by(organization_id=org.id, domain=domain).first()
    if existing:
        return existing, None
    # Someone else already proved control → never hand out a second claim on it.
    owner = verified_owner(domain)
    if owner is not None and owner.id != org.id:
        return None, "Dit domein is al geverifieerd door een andere organisatie."

    claim = DomainClaim(organization_id=org.id, domain=domain, token=DomainClaim.new_token())
    db.session.add(claim)
    db.session.commit()
    return claim, None


def verify_claim(claim: DomainClaim) -> bool:
    """Check the TXT record now and persist the outcome. Returns True when verified.

    Records a business event on the transitions only, so the org timeline shows "domain
    verified" once instead of on every daily re-check.
    """
    from app.events import record_event

    was_verified = claim.is_verified
    claim.last_checked_at = utcnow()
    try:
        values = resolve_txt(claim.record_name)
    except DnsUnavailable as exc:
        claim.last_error = f"Kon DNS niet raadplegen: {exc}"[:255]
        db.session.commit()
        return False

    # Accept the bare token too — customers routinely paste just the value.
    wanted = {claim.record_value, claim.token}
    found = any(v.strip() in wanted or v.strip().replace(VALUE_PREFIX, "") == claim.token for v in values)

    if found:
        # Re-check exclusivity at verification time: another org may have verified this
        # domain while this claim sat pending.
        owner = verified_owner(claim.domain)
        if owner is not None and owner.id != claim.organization_id:
            claim.verified_at = None
            claim.last_error = "Dit domein is intussen door een andere organisatie geverifieerd."
            db.session.commit()
            return False
        claim.verified_at = claim.verified_at or utcnow()
        claim.last_error = None
    else:
        claim.verified_at = None
        claim.last_error = "TXT-record niet gevonden. DNS-wijzigingen kunnen tot 24 u duren."
    if claim.is_verified and not was_verified:
        record_event(claim.organization, "domain.verified", detail=claim.domain, actor="systeem")
    elif was_verified and not claim.is_verified:
        record_event(claim.organization, "domain.revoked", detail=claim.domain, actor="systeem")
    db.session.commit()
    return claim.is_verified


def verified_owner(domain: str):
    """The organisation that has proven control of `domain`, or None."""
    from app.models import Organization

    d = (domain or "").strip().lower()
    if not d:
        return None
    claim = (
        db.session.query(DomainClaim)
        .join(Organization, DomainClaim.organization_id == Organization.id)
        .filter(DomainClaim.domain == d, DomainClaim.verified_at.isnot(None))
        .filter(Organization.deleted_at.is_(None))  # a trashed tenant never holds a domain
        .first()
    )
    return claim.organization if claim else None


def verified_domains(org) -> set[str]:
    if org is None:
        return set()
    return {
        c.domain
        for c in db.session.query(DomainClaim).filter_by(organization_id=org.id)
        if c.verified_at is not None
    }


def claims_for(org) -> list[DomainClaim]:
    if org is None:
        return []
    return (
        db.session.query(DomainClaim)
        .filter_by(organization_id=org.id)
        .order_by(DomainClaim.domain)
        .all()
    )


# ── Enforcement ──────────────────────────────────────────────────────────────
def registration_blocked_by(email: str, *, org=None):
    """The org that owns this e-mail's domain, when it isn't `org`. None = allowed.

    Unverified domains stay open (nothing changes for customers who never claimed one);
    a *verified* domain is exclusive, so a stranger can't register a second tenant on it.
    """
    owner = verified_owner(domain_of(email))
    if owner is None or (org is not None and owner.id == org.id):
        return None
    return owner
