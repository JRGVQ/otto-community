"""Signature resolution — which sign-off a draft gets.

Order, most specific first:
  1. the shared mailbox's own signature (a team mailbox speaks for the team);
  2. een handtekening die een admin expliciet aan deze GEBRUIKER toewees;
  3. een handtekening toegewezen aan een Microsoft 365-GROEP waar dit postvak in zit
     (lidmaatschappen gecachet op mail_accounts.graph_group_ids);
  4. the organisation's signature for the sending mailbox's domain — set by an admin,
     which is the point of a central signature: it standardises a brand's mail;
  5. the user's personal signature;
  6. the organisation's default signature;
  7. a generated fallback from the user's name.

Een org-handtekening kan een rijke HTML-versie hebben (uit de builder). Die wordt NIET
aan het taalmodel gevraagd maar deterministisch onder het concept gezet bij de
write-back naar de provider — het model kan er dus nooit iets aan verzinnen. Zonder
HTML blijft het oude gedrag: de platte tekst gaat mee in de prompt.

Placeholders ({{voornaam}}, {{functie}}, …) worden per gebruiker ingevuld met
render_placeholders(); onbekende of lege velden verdwijnen inclusief hun regel niet —
alleen de placeholder zelf wordt leeg gelaten.
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from datetime import datetime

from app.extensions import db
from app.models import Signature, SignatureAssignment

# Alle vaste placeholders (gevuld uit het M365-profiel/de organisatie). De builder toont
# deze lijst als klikbare chips; hou de namen dus Nederlands en voorspelbaar. Daarnaast
# bestaan er eigen variabelen (SignatureVariable): org-breed door een admin gedefinieerd
# of persoonlijk door de gebruiker zelf — zie variables_for()/placeholder_values().
PLACEHOLDER_KEYS = (
    "voornaam", "achternaam", "naam", "functie", "afdeling", "telefoon", "mobiel",
    "email", "bedrijf", "locatie",
)

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
_KEY_RE = re.compile(r"[^a-z0-9_]+")

# Regel-engine (fase 3): de velden waarop een regel kan matchen en de operatoren. Alle
# velden zijn afleidbaar uit (gebruiker, postvak, bericht) zonder extra Graph-call.
RULE_FIELDS = ("sender_mailbox", "sender_department", "sender_group",
               "recipient_domain", "recipient_scope")
RULE_OPS = ("equals", "contains", "in", "is")


def slugify_key(label: str) -> str | None:
    """Label → placeholder-key: 'LinkedIn-URL' → 'linkedin_url'. None = onbruikbaar
    (leeg, botst met een vaste placeholder, of begint niet met een letter)."""
    key = _KEY_RE.sub("_", (label or "").strip().lower()).strip("_")[:64]
    if not key or not key[0].isalpha() or key in PLACEHOLDER_KEYS:
        return None
    return key


def variables_for(user) -> list[tuple]:
    """[(variabele, waarde)] zichtbaar voor deze gebruiker: alle org-brede variabelen
    plus zijn eigen persoonlijke, elk met zíjn ingevulde waarde ('' als leeg)."""
    org_id = getattr(user, "organization_id", None) if user else None
    if not org_id:
        return []
    try:
        from app.models import SignatureVariable, SignatureVariableValue

        rows = (
            db.session.query(SignatureVariable)
            .filter(
                SignatureVariable.organization_id == org_id,
                (SignatureVariable.user_id.is_(None)) | (SignatureVariable.user_id == user.id),
            )
            .order_by(SignatureVariable.user_id.isnot(None), SignatureVariable.id)
            .all()
        )
        if not rows:
            return []
        vals = {
            v.variable_id: (v.value or "")
            for v in db.session.query(SignatureVariableValue)
            .filter(SignatureVariableValue.user_id == user.id,
                    SignatureVariableValue.variable_id.in_([r.id for r in rows]))
            .all()
        }
        return [(r, vals.get(r.id, "")) for r in rows]
    except Exception:  # noqa: BLE001 — tabel nog niet gemigreerd → alleen vaste velden
        return []


def normalize(domain: str | None) -> str | None:
    from app.domains import normalize_domain

    return normalize_domain(domain or "") if domain else None


def placeholder_values(user, account=None) -> dict[str, str]:
    """De invulwaarden voor één gebruiker/postvak. Lege velden worden lege strings."""
    name = (getattr(user, "name", "") or "").strip()
    first, _, last = name.partition(" ")
    org = getattr(user, "organization", None) if user else None
    email = (getattr(account, "email", None) or getattr(user, "email", "") or "").strip()
    out = {
        "voornaam": first,
        "achternaam": last.strip(),
        "naam": name,
        "functie": (getattr(user, "job_title", "") or "").strip(),
        "afdeling": (getattr(user, "department", "") or "").strip(),
        "telefoon": (getattr(user, "phone", "") or "").strip(),
        "mobiel": (getattr(user, "mobile", "") or "").strip(),
        "email": email,
        "bedrijf": (getattr(org, "name", "") or "").strip(),
        "locatie": (getattr(user, "office_location", "") or "").strip(),
    }
    # {{foto}} = pad naar de gecachte M365-profielfoto (of leeg → de <img data-ph="foto">
    # verdwijnt netjes). In de aflevering wordt dit pad naar cid: herschreven (writeback);
    # in de preview laadt de browser het via de geauthenticeerde route.
    out["foto"] = _photo_path(user)
    # {{logo}} + {{merk_kleur}} uit de brand kit van het verzenddomein (of de org-standaard),
    # zodat één sjabloon per domein automatisch het juiste logo en de accentkleur toont.
    domain = sending_domain(account) or (_domain_of(email))
    kit = brand_kit_for(org, domain)
    out["logo"] = (getattr(kit, "logo_url", "") or "") if kit else ""
    out["merk_kleur"] = (getattr(kit, "accent_color", "") or "") if kit else ""
    # {{social}} = e-mailveilige iconenrij (profiel-links) uit de brand kit van het domein.
    out["social"] = social_html(kit)
    # Eigen variabelen erbovenop (org-breed + persoonlijk); vaste velden winnen altijd.
    for var, value in variables_for(user):
        out.setdefault(var.key, (value or "").strip())
    return out


def _domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower() if email and "@" in email else ""


def brand_kit_for(org, domain: str | None):
    """De brand kit voor `domain`, of de org-standaard (domain NULL), of None."""
    org_id = getattr(org, "id", None)
    if not org_id:
        return None
    try:
        from app.models import BrandKit

        rows = db.session.query(BrandKit).filter_by(organization_id=org_id).all()
    except Exception:  # noqa: BLE001 — tabel nog niet gemigreerd
        return None
    d = (domain or "").strip().lower()
    if d:
        for r in rows:
            if (r.domain or "").strip().lower() == d:
                return r
    return next((r for r in rows if not r.domain), None)


# Ondersteunde social-netwerken (volgorde = weergavevolgorde) + hun label.
SOCIAL_NETWORKS = [
    ("linkedin", "LinkedIn"),
    ("x", "X"),
    ("facebook", "Facebook"),
    ("instagram", "Instagram"),
    ("youtube", "YouTube"),
]


def social_html(kit, color: str | None = None) -> str:
    """Een e-mailveilige social-rij uit de brand kit: profiel-links in de accentkleur,
    gescheiden door '·'. Bewust tekst-links (geen SVG/gehoste iconen): die renderen
    betrouwbaar in élke mailclient, inclusief Outlook-desktop, zonder externe afhankelijk-
    heid. Alleen ingevulde netwerken verschijnen; niets ingevuld → lege string (de
    {{social}}-placeholder verdwijnt netjes)."""
    socials = getattr(kit, "socials", None) or {}
    if not isinstance(socials, dict):
        return ""
    accent = (color or getattr(kit, "accent_color", "") or "#6C4CE6").strip() or "#6C4CE6"
    parts = []
    for key, label in SOCIAL_NETWORKS:
        url = (socials.get(key) or "").strip()
        if not url or not url.lower().startswith(("http://", "https://")):
            continue
        safe = _html.escape(url, quote=True)
        parts.append(
            f'<a href="{safe}" style="color:{_html.escape(accent, quote=True)};'
            f'text-decoration:none" target="_blank" rel="noopener">{label}</a>'
        )
    if not parts:
        return ""
    sep = f'<span style="color:{_html.escape(accent, quote=True)}"> · </span>'
    return '<span class="otto-social">' + sep.join(parts) + "</span>"


def _photo_path(user) -> str:
    """/media/user-photo/<id> als deze gebruiker een gecachte profielfoto heeft, anders ''."""
    uid = getattr(user, "id", None) if user else None
    if not uid:
        return ""
    try:
        from app.models import UserPhoto

        exists = db.session.query(UserPhoto.user_id).filter_by(user_id=uid).first()
    except Exception:  # noqa: BLE001 — tabel nog niet gemigreerd
        return ""
    return f"/media/user-photo/{uid}" if exists else ""


# Placeholders waarvan de waarde al veilige HTML is (door Otto opgebouwd), dus niet escapen.
_RAW_HTML_PH = {"social"}


def render_placeholders(template: str, user, account=None, *, html: bool = False) -> str:
    """Vul {{placeholder}}-velden in. In HTML-context worden waarden ge-escaped.

    Lege profielvelden verdwijnen netjes: in HTML wordt een element dat met
    data-ph="<key>" aan een leeg veld hangt volledig verwijderd (zo blijft er
    geen kaal 📞-icoontje staan als iemand geen telefoon heeft), en in tekst
    vervallen regels die na het invullen niets inhoudelijks meer bevatten."""
    values = placeholder_values(user, account)
    out = template or ""
    if html:
        out = _strip_empty_ph(out, values)

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        val = values.get(key, "")
        # Door Otto zelf gegenereerde HTML-placeholders (intern al ge-escaped) gaan
        # ongewijzigd mee; alle profielwaarden worden in HTML-context ge-escaped.
        if key in _RAW_HTML_PH:
            # HTML-context: ongewijzigd (al veilig). Tekst-context: tags eruit, labels blijven.
            return val if html else re.sub(r"<[^>]+>", "", val)
        return _html.escape(val) if html else val

    if html:
        return _PLACEHOLDER_RE.sub(_sub, out)
    # Tekst: een regel die placeholders bevatte en na het invullen niets
    # inhoudelijks meer overhoudt (alleen scheidingstekens), vervalt. Een regel
    # zónder placeholders blijft altijd staan (een bewuste '---'-scheiding bv.).
    lines = []
    for line in out.splitlines():
        had_ph = _PLACEHOLDER_RE.search(line) is not None
        filled = _PLACEHOLDER_RE.sub(_sub, line)
        if had_ph and not re.search(r"[^\W_]", filled, re.UNICODE):
            continue
        lines.append(filled)
    return "\n".join(lines)


# Element met data-ph="key" (geen geneste tags met data-ph erin — de builder houdt
# de inhoud plat: icoontje + placeholder).
_DATA_PH_RE = re.compile(r'<(\w+)[^>]*\bdata-ph="([a-z_][a-z0-9_]*)"[^>]*>.*?</\1>', re.S)


def _strip_empty_ph(html_tpl: str, values: dict[str, str]) -> str:
    def _keep(m: re.Match) -> str:
        return "" if not values.get(m.group(2), "") else m.group(0)

    return _DATA_PH_RE.sub(_keep, html_tpl)


# <img src="/media/user-photo/12"> of "/media/sig-asset/5" — de enige interne bronnen die
# we naar cid: herschrijven. Externe https-plaatjes blijven onaangeroerd (die laadt de
# ontvanger gewoon rechtstreeks).
_MEDIA_IMG_RE = re.compile(r'src="(/media/(user-photo|sig-asset)/(\d+))"')


def inline_images_for(html: str | None):
    """(html_met_cid, [inline_images]) — herschrijf interne /media-afbeeldingen naar
    cid:-verwijzingen en verzamel hun bytes als inline-bijlagen.

    Zo worden de M365-profielfoto en org-logo's/banners in de aflevering embedded (niet
    als externe afbeelding geblokkeerd). Externe https-afbeeldingen blijven ongewijzigd.
    Best-effort: een afbeelding zonder bytes wordt gewoon overgeslagen (de <img> blijft,
    maar zonder inhoud — daarom hoort {{foto}} via data-ph te verdwijnen als er geen foto is).
    """
    src = html or ""
    if "/media/" not in src:
        return src, []
    images: list[dict] = []
    seen: dict[str, str] = {}

    def _load(kind: str, ident: int):
        try:
            from app.models import SignatureAsset, UserPhoto

            if kind == "user-photo":
                row = db.session.get(UserPhoto, ident)
                return (row.data, row.content_type) if row else None
            row = db.session.get(SignatureAsset, ident)
            return (row.data, row.content_type) if row else None
        except Exception:  # noqa: BLE001 — tabel nog niet gemigreerd of fout → geen embed
            return None

    def _sub(m: re.Match) -> str:
        path, kind, ident = m.group(1), m.group(2), int(m.group(3))
        if path in seen:
            return f'src="cid:{seen[path]}"'
        loaded = _load(kind, ident)
        if not loaded or not loaded[0]:
            return m.group(0)  # geen bytes → laat de externe/interne src staan
        data, content_type = loaded
        cid = f"otto-{kind}-{ident}"
        seen[path] = cid
        images.append({"content_id": cid, "content_type": content_type or "image/png",
                       "data": data, "name": f"{cid}.img"})
        return f'src="cid:{cid}"'

    return _MEDIA_IMG_RE.sub(_sub, src), images


@dataclass
class ResolvedSignature:
    """Wat een concept meekrijgt: altijd tekst; html alleen bij een builder-handtekening."""
    text: str
    html: str | None = None
    name: str | None = None  # label van de org-handtekening (voor de UI-hint)


def for_domain(org, domain: str | None, now=None) -> Signature | None:
    """The org signature covering `domain`, falling back to the org default.

    Een handtekening met expliciete toewijzingen is geen domein/standaard-kandidaat
    meer: die geldt alleen voor de toegewezen gebruikers/groepen.

    Een domein kan meerdere handtekeningen hebben (een permanente standaard + geplande
    campagnes met een tijdvenster). We kiezen de nu-actieve; een lopend tijdvenster (de
    campagne) wint van de permanente, zodat die na afloop vanzelf weer overneemt."""
    if org is None:
        return None
    rows = db.session.query(Signature).filter_by(organization_id=org.id).all()
    rows = [r for r in rows if not r.assignments]
    now = now or datetime.utcnow()
    d = (domain or "").strip().lower()
    if d:
        cands = [r for r in rows if (r.domain or "").strip().lower() == d]
        if cands:
            pool = [r for r in cands if r.active_at(now)] or cands
            windowed = [r for r in pool if r.starts_at or r.ends_at]
            return (windowed or pool)[0]
    # Dezelfde selectie voor de domeinloze standaard. Zonder deze filtering kwam hier de
    # EERSTE domeinloze rij uit, ook als die uitgeschakeld of verlopen was — `_usable`
    # verwierp die vervolgens, en dan viel er helemaal géén org-handtekening onder het
    # concept. Precies het scenario na "schakel de andere uit" bij een domeinconflict.
    generic = [r for r in rows if not r.domain]
    if generic:
        pool = [r for r in generic if r.active_at(now)] or generic
        windowed = [r for r in pool if r.starts_at or r.ends_at]
        return (windowed or pool)[0]
    return None


def default_for(org) -> Signature | None:
    return for_domain(org, None)


def _overlap(s1, e1, s2, e2) -> bool:
    """Overlappen twee (mogelijk open) tijdsperiodes? None = onbegrensd."""
    lo1, hi1 = s1 or datetime.min, e1 or datetime.max
    lo2, hi2 = s2 or datetime.min, e2 or datetime.max
    return lo1 <= hi2 and lo2 <= hi1


def domain_conflict(org, domain, starts_at, ends_at, enabled=True, exclude_id=None):
    """Botst een (nieuwe) handtekening met een bestaande op hetzelfde domein?

    Regel: op één domein mag hoogstens één *permanente* handtekening staan; daarnaast mogen
    er geplande campagnes zijn zolang die elkaar niet overlappen. Dus conflict als:
      • beide permanent (geen venster), of
      • beide een venster hebben én die overlappen.
    Een permanente + een gevensterde botsen niet (de campagne overschrijft tijdelijk). Een
    uitgeschakelde handtekening botst nooit. Retourneert de botsende handtekening of None."""
    if org is None or not enabled:
        return None
    d = (domain or "").strip().lower()
    rows = db.session.query(Signature).filter_by(organization_id=getattr(org, "id", org)).all()
    new_windowed = bool(starts_at or ends_at)
    for o in rows:
        if o.id == exclude_id or not o.enabled or o.assignments:
            continue
        if (o.domain or "").strip().lower() != d:
            continue
        o_windowed = bool(o.starts_at or o.ends_at)
        if not new_windowed and not o_windowed:
            return o
        if new_windowed and o_windowed and _overlap(starts_at, ends_at, o.starts_at, o.ends_at):
            return o
    return None


def assigned_for(user, account=None) -> Signature | None:
    """De handtekening die een admin expliciet toewees: gebruiker eerst, dan groep."""
    org_id = getattr(user, "organization_id", None) if user else None
    if not org_id:
        return None
    if user is not None:
        row = (
            db.session.query(SignatureAssignment)
            .filter_by(organization_id=org_id, kind="user", value=str(user.id))
            .first()
        )
        if row is not None and row.signature is not None:
            return row.signature
    group_ids = [g for g in (getattr(account, "graph_group_ids", None) or []) if g]
    if group_ids:
        row = (
            db.session.query(SignatureAssignment)
            .filter(
                SignatureAssignment.organization_id == org_id,
                SignatureAssignment.kind == "group",
                SignatureAssignment.value.in_(group_ids),
            )
            .order_by(SignatureAssignment.id.asc())
            .first()
        )
        if row is not None and row.signature is not None:
            return row.signature
    return None


def _rule_context(user, account, message) -> dict:
    """De eigenschappen waarop een regel matcht, uit (gebruiker, postvak, bericht).

    De 'ontvanger' van ons antwoord is de oorspronkelijke afzender (we antwoorden hém),
    dus recipient_domain/-scope komen uit message.sender_email. Zonder bericht (add-in)
    blijven die None → recipient-regels matchen dan simpelweg niet."""
    from app.domains import domain_of, verified_domains

    org = getattr(user, "organization", None) if user else None
    ctx = {
        "sender_mailbox": (getattr(account, "email", "") or "").strip().lower() or None,
        "sender_department": (getattr(user, "department", "") or "").strip().lower() or None,
        "sender_group": {str(g).lower() for g in (getattr(account, "graph_group_ids", None) or []) if g},
        "recipient_domain": None,
        "recipient_scope": None,
    }
    recip = (getattr(message, "sender_email", "") or "") if message is not None else ""
    dom = (domain_of(recip) if recip else "").lower()
    if dom:
        ctx["recipient_domain"] = dom
        ctx["recipient_scope"] = "internal" if dom in verified_domains(org) else "external"
    return ctx


def _condition_matches(cond, ctx: dict) -> bool:
    actual = ctx.get(cond.field)
    val = (cond.value or "").strip().lower()
    if cond.field == "sender_group":
        wanted = {v.strip().lower() for v in val.split(",") if v.strip()}
        return bool(actual) and bool(wanted & actual)  # lidmaatschap van een van de groepen
    if actual is None:
        return False  # veld niet beschikbaar (bv. recipient zonder bericht) → geen match
    actual = str(actual).lower()
    if cond.op == "in":
        return actual in {v.strip().lower() for v in val.split(",") if v.strip()}
    if cond.op == "contains":
        return bool(val) and val in actual
    return actual == val  # equals / is


def resolve_rules(user, account=None, message=None) -> Signature | None:
    """De handtekening van de eerste ingeschakelde regel die matcht (op priority), of None.

    Nul regels → None, zodat een organisatie zonder regels exact het oude gedrag houdt.
    Een regel zonder voorwaarden matcht nooit (geen ongewild vangnet)."""
    org_id = getattr(user, "organization_id", None) if user else None
    if not org_id:
        return None
    try:
        from app.models import SignatureRule

        rules = (
            db.session.query(SignatureRule)
            .filter_by(organization_id=org_id, enabled=True)
            .order_by(SignatureRule.priority.asc(), SignatureRule.id.asc())
            .all()
        )
    except Exception:  # noqa: BLE001 — tabel nog niet gemigreerd → geen regels
        return None
    if not rules:
        return None
    ctx = _rule_context(user, account, message)
    for rule in rules:
        if rule.conditions and all(_condition_matches(c, ctx) for c in rule.conditions):
            sig = rule.signature
            if sig is not None and (sig.body or sig.html):
                return sig
    return None


def sending_domain(account) -> str | None:
    """The domain a mailbox sends from, e.g. 'team@acme.be' → 'acme.be'."""
    email = getattr(account, "email", "") or ""
    return email.rsplit("@", 1)[-1].strip().lower() if "@" in email else None


def _banner_targets(banner, user, account) -> bool:
    """Matcht deze banner het doelbereik van de verzender (org/domein/afdeling/M365-groep)?"""
    kind = (banner.scope_kind or "org").lower()
    if kind == "org":
        return True
    value = (banner.scope_value or "").strip().lower()
    if not value:
        return True  # doelbereik gekozen maar leeg → val terug op iedereen
    if kind == "domain":
        return (sending_domain(account) or "") == value
    if kind == "department":
        return (getattr(user, "department", "") or "").strip().lower() == value
    if kind == "group":
        return value in {str(g).lower() for g in (getattr(account, "graph_group_ids", None) or []) if g}
    return True


def active_banner_html(org, now=None, user=None, account=None) -> tuple[str, str] | None:
    """(banner_html, placement) van de actieve banner voor deze verzender, of None.

    Actief = ingeschakeld, now binnen [starts_at, ends_at], én het doelbereik matcht
    (org/domein/afdeling/M365-groep). Bij meerdere wint de hoogste `weight` (dan het
    nieuwste). Puur resolve-time: een banner die vandaag afloopt verdwijnt vanzelf."""
    org_id = getattr(org, "id", None)
    if not org_id:
        return None
    from app.models.base import utcnow

    now = now or utcnow()
    try:
        from app.models import SignatureBanner

        banners = db.session.query(SignatureBanner).filter_by(organization_id=org_id, enabled=True).all()
    except Exception:  # noqa: BLE001 — tabel nog niet gemigreerd
        return None
    active = [b for b in banners if b.is_active(now) and _banner_targets(b, user, account)]
    if not active:
        return None
    b = sorted(active, key=lambda x: (x.weight, x.id), reverse=True)[0]
    img = f'<img src="{_html.escape(b.image_url)}" alt="" style="max-width:100%;height:auto;border:0;">'
    href, pixel = b.link_url, ""
    # Opt-in meting: click via redirect, weergave via een verborgen pixel (alleen aggregaat).
    if getattr(b, "track", False):
        try:
            from app.signature_analytics import banner_pixel, banner_redirect

            if b.link_url:
                href = banner_redirect(b.id, b.link_url)
            pixel = banner_pixel(b.id)
        except Exception:  # noqa: BLE001 — meting mag een handtekening nooit breken
            href, pixel = b.link_url, ""
    inner = f'<a href="{_html.escape(href)}" style="text-decoration:none;">{img}</a>' if href else img
    return (f'<div style="margin-top:12px;">{inner}{pixel}</div>', b.placement or "below")


def _with_banner(html_out: str | None, org, user=None, account=None) -> str | None:
    """Splits de actieve (op de verzender gerichte) banner boven of onder de handtekening-HTML."""
    if not html_out:
        return html_out
    banner = active_banner_html(org, user=user, account=account)
    if not banner:
        return html_out
    block, placement = banner
    return block + html_out if placement == "above" else html_out + block


# Slotgroeten die het concept zelf al één keer boven het handtekeningblok schrijft.
# Staat dezelfde groet ook bovenaan de handtekening, dan komt hij dubbel — daarom halen
# we hem hier weg. Zelfherstellend: geldt ook voor handtekeningen die al opgeslagen zijn
# met een ingebakken groet, niet enkel voor de meegeleverde sjablonen.
_GREETING_WORDS = (
    "met vriendelijke groeten", "met vriendelijke groet", "vriendelijke groeten",
    "vriendelijke groet", "hartelijke groeten", "hartelijke groet", "beste groeten",
    "groeten", "groetjes", "groet", "mvg", "hoogachtend",
    "bien à vous", "cordialement", "meilleures salutations", "salutations",
    "kind regards", "best regards", "warm regards", "regards",
)
_GREETING_RE = re.compile(
    r"^\s*(?:" + "|".join(re.escape(w) for w in _GREETING_WORDS) + r")\s*[,.!]?\s*$",
    re.IGNORECASE,
)


def strip_leading_greeting(sig: str | None) -> str | None:
    """Haal een leidende slotgroet ('Met vriendelijke groeten,' e.d.) uit een handtekening.

    Werkt op zowel platte tekst als HTML: bij HTML wordt het eerste blokelement (div/p)
    verwijderd wanneer de zichtbare tekst enkel een slotgroet is. Bij tekst de eerste
    regel. Verwijdert hoogstens één groet en laat de rest ongemoeid; faalt veilig (geeft
    de invoer terug) zodat een handtekening nooit sneuvelt op dit opschonen."""
    if not sig:
        return sig
    try:
        if "<" in sig and ">" in sig:
            # Verwijder één leidend blokelement (evt. voorafgegaan door <br>) waarvan de
            # tekst enkel een slotgroet is.
            pattern = re.compile(
                r"^\s*(?:<br\s*/?>\s*)*<(div|p)\b[^>]*>(.*?)</\1>\s*(?:<br\s*/?>\s*)*",
                re.IGNORECASE | re.DOTALL,
            )
            m = pattern.match(sig)
            if m:
                inner = re.sub(r"<[^>]+>", "", m.group(2))          # strip nested tags
                inner = _html.unescape(inner).replace("\xa0", " ")   # &nbsp; → spatie
                if _GREETING_RE.match(inner):
                    return sig[m.end():]
            return sig
        # Platte tekst: eerste niet-lege regel.
        lines = sig.split("\n")
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            if _GREETING_RE.match(line):
                return "\n".join(lines[i + 1:]).lstrip("\n")
            break
        return sig
    except Exception:  # noqa: BLE001 — opschonen mag een handtekening nooit breken
        return sig


def _rendered(sig: Signature, user, account) -> ResolvedSignature:
    """Eén org-handtekening → ingevulde tekst + (eventueel) ingevulde HTML."""
    # Platte tekst uit `body` gaat als volledige afsluiting de prompt in (het model schrijft
    # er geen eigen groet meer bij) — die laten we ongemoeid. De HTML-versie wordt daarentegen
    # server-side onder een concept geplakt waar het model zelf al een slotgroet boven zette,
    # dus daar (en in de daaruit afgeleide platte-tekst-variant) halen we een dubbele groet weg.
    text = render_placeholders(sig.body or "", user, account)
    if not text and sig.html:
        from app.htmltext import html_to_text

        text = strip_leading_greeting(html_to_text(render_placeholders(sig.html, user, account)))
    html_out = strip_leading_greeting(render_placeholders(sig.html, user, account, html=True)) if sig.html else None
    # De actieve banner komt alleen bij de HTML-weergave; de platte tekst blijft ongewijzigd.
    html_out = _with_banner(html_out, getattr(user, "organization", None) if user else None,
                            user=user, account=account)
    return ResolvedSignature(text=text, html=html_out, name=sig.name or sig.domain)


def _usable(sig, user, account, message, now) -> bool:
    """Mag deze org-handtekening nu voor deze verzender/ontvanger gekozen worden?

    Filtert op: ingeschakeld + binnen het tijdvenster (active_at), niet uitgezonderd, en
    ontvanger-scope. Een handtekening buiten haar venster wordt overgeslagen zodat de
    permanente standaard vanzelf weer overneemt — precies wat een geplande campagne wil."""
    if sig is None or not sig.active_at(now) or sig.excludes(user, account):
        return False
    scope = sig.recipient_scope or "all"
    if scope != "all" and message is not None:
        ctx_scope = _rule_context(user, account, message).get("recipient_scope")
        if ctx_scope is not None and ctx_scope != scope:
            return False
    return True


def resolve_rich(user, *, account=None, mailbox_settings=None, message=None) -> ResolvedSignature:
    """De volledige handtekening voor een concept (tekst + eventuele HTML).

    `message` (optioneel) voedt de regel-engine met de ontvanger-context; zonder bericht
    (bv. het Outlook-taakvenster) worden recipient-regels overgeslagen."""
    # Ook de mailbox- en persoonlijke handtekening mogen placeholders dragen — zo kan
    # een gebruiker zijn eigen variabelen ({{linkedin}} …) in zijn eigen handtekening
    # gebruiken, niet alleen in de door de admin gebouwde org-handtekeningen.
    if mailbox_settings is not None and mailbox_settings.signature:
        return ResolvedSignature(text=render_placeholders(mailbox_settings.signature, user, account))

    now = datetime.utcnow()

    # Regel-engine eerst (specifieker dan de vaste toewijzingen). Nul regels = overslaan.
    if (sig := resolve_rules(user, account, message)) is not None and _usable(sig, user, account, message, now):
        return _rendered(sig, user, account)

    if (sig := assigned_for(user, account)) is not None and (sig.body or sig.html) \
            and _usable(sig, user, account, message, now):
        return _rendered(sig, user, account)

    org = getattr(user, "organization", None) if user else None
    domain = sending_domain(account)
    if domain and (row := for_domain(org, domain)) is not None and row.domain and (row.body or row.html) \
            and _usable(row, user, account, message, now):
        return _rendered(row, user, account)  # an admin set this brand's signature on purpose

    style = user.style_profile if user else None
    if style and style.signature:
        return ResolvedSignature(text=render_placeholders(style.signature, user, account))

    if (row := default_for(org)) is not None and (row.body or row.html) \
            and _usable(row, user, account, message, now):
        return _rendered(row, user, account)

    if user and user.name:
        return ResolvedSignature(text=f"Groeten,\n{user.name.split()[0]}")
    return ResolvedSignature(text="Met vriendelijke groeten")


def resolve(user, *, account=None, mailbox_settings=None, message=None) -> str:
    """The signature text for a draft. Never returns a placeholder."""
    return resolve_rich(user, account=account, mailbox_settings=mailbox_settings, message=message).text
