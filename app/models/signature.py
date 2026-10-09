"""Org-managed e-mail signatures — per domein, gebruiker of Microsoft 365-groep.

A company with several brands (or legal entities) under one Otto environment wants each
outgoing reply to carry the right sign-off, set centrally by an admin instead of by each
colleague. A signature with a `domain` applies to mail sent from a mailbox at that
domain; the one without a domain (and without assignments) is the organisation's default.

Naast het domein kan een handtekening ook expliciet worden toegewezen (CodeTwo/Exclaimer-
stijl): aan een specifieke gebruiker of aan een Microsoft 365-groep. Een toewijzing is
specifieker dan een domein en wint dus — zie de volgorde in app/signatures.py.

`body` is de platte tekst (gebruikt in de AI-prompt en als fallback); `html` is de rijke
versie uit de builder, die server-side onder het concept in Outlook wordt gezet. Beide
mogen placeholders bevatten ({{voornaam}}, {{functie}}, {{telefoon}}, …) die per
gebruiker worden ingevuld — zie app/signatures.py:render_placeholders.
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class Signature(TimestampMixin, db.Model):
    __tablename__ = "signatures"
    # Géén unieke (org, domein): een domein mag een permanente standaard + geplande campagnes
    # (tijdvenster) hebben. De app (routes_admin.admin_signature_save) bewaakt dat hun actieve
    # periodes niet overlappen — zie signatures.domain_conflict.

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    # NULL = the organisation's default signature; otherwise a bare lowercase domain.
    domain = db.Column(db.String(128), index=True)
    name = db.Column(db.String(64))  # label shown in the admin list
    body = db.Column(db.Text, nullable=False)
    # Rijke HTML-versie uit de builder. Leeg = alleen platte tekst (gedrag van vóór de
    # builder: de handtekening gaat dan mee in de AI-prompt in plaats van server-side).
    html = db.Column(db.Text)

    # ── Per-handtekening-configuratie (Exclaimer-stijl) ──────────────────────
    # Aan/uit: een uitgeschakelde handtekening wordt nooit gekozen (de standaard neemt over).
    enabled = db.Column(db.Boolean, nullable=False, server_default=db.true(), default=True)
    # Tijdvenster (optioneel): de handtekening geldt alleen tussen start en eind. Buiten het
    # venster wordt ze overgeslagen, zodat de permanente standaard vanzelf weer overneemt.
    # Zo mogen twee handtekeningen op hetzelfde domein bestaan zolang hun vensters niet overlappen.
    starts_at = db.Column(db.DateTime)
    ends_at = db.Column(db.DateTime)
    # Ontvanger-scope: 'all' (standaard), 'internal' of 'external'. Alleen afdwingbaar wanneer
    # de ontvanger bekend is (mailpijplijn); in de add-in/preview telt ze als 'all'.
    recipient_scope = db.Column(db.String(16), nullable=False, server_default="all", default="all")
    # Uitzonderingen: verzenders waarvoor deze handtekening NIET geldt (los van de
    # toewijzingen, zodat is_default niet wijzigt). {"users": [id,…], "groups": [guid,…]}.
    exclusions = db.Column(db.JSON)

    organization = db.relationship("Organization")

    def active_at(self, now) -> bool:
        """Geldt deze handtekening op tijdstip `now`? (ingeschakeld én binnen het venster)."""
        if not self.enabled:
            return False
        if self.starts_at is not None and now < self.starts_at:
            return False
        if self.ends_at is not None and now > self.ends_at:
            return False
        return True

    def excludes(self, user, account=None) -> bool:
        """Is deze verzender (gebruiker of een van zijn M365-groepen) uitgezonderd?"""
        ex = self.exclusions if isinstance(self.exclusions, dict) else {}
        uid = str(getattr(user, "id", "") or "")
        if uid and uid in {str(x) for x in (ex.get("users") or [])}:
            return True
        gids = {str(g) for g in (getattr(account, "graph_group_ids", None) or []) if g}
        return bool(gids and gids & {str(x) for x in (ex.get("groups") or [])})
    assignments = db.relationship(
        "SignatureAssignment", back_populates="signature",
        cascade="all, delete-orphan",
    )

    @property
    def is_default(self) -> bool:
        return not self.domain and not self.assignments

    @property
    def scope_label(self) -> str:
        """Korte omschrijving van de reikwijdte, voor de beheerlijst."""
        if self.domain:
            return self.domain
        if self.assignments:
            return ""  # de toewijzings-pills zeggen het al
        return "standaard"


class SignatureAssignment(TimestampMixin, db.Model):
    """Eén expliciete toewijzing van een handtekening: aan een gebruiker of M365-groep.

    `kind` is 'user' (value = users.id als string) of 'group' (value = de Graph-groep-GUID,
    `label` de groepsnaam zodat de beheerlijst geen Graph-call nodig heeft). Eén gebruiker
    of groep kan maar aan één handtekening hangen (unique per organisatie), zodat de
    volgorde nooit dubbelzinnig is.
    """
    __tablename__ = "signature_assignments"
    __table_args__ = (
        db.UniqueConstraint("organization_id", "kind", "value", name="uq_sig_assign_org_kind_value"),
    )

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    signature_id = db.Column(db.Integer, db.ForeignKey("signatures.id"), nullable=False, index=True)
    kind = db.Column(db.String(16), nullable=False)  # 'user' | 'group'
    value = db.Column(db.String(255), nullable=False)  # user-id of Graph-groep-GUID
    label = db.Column(db.String(255))  # weergavenaam (groepsnaam); users worden live opgezocht

    signature = db.relationship("Signature", back_populates="assignments")


class CloudSignatureSetting(TimestampMixin, db.Model):
    """De handtekening-afleveringsmodus van een organisatie (CodeTwo-stijl).

    - `client`  : Otto zet de handtekening in het concept (huidige gedrag). Alleen zichtbaar
                  vanuit Outlook, maar de Verzonden-map klopt.
    - `cloud`   : een Exchange transport-regel voegt de handtekening ná verzenden toe (ook
                  mobiel). Otto zet dan zelf geen handtekening meer in het concept. Let op:
                  een transport-regel werkt de Verzonden-map NIET bij (dat vraagt een relay).
    - `combo`   : Otto zet de handtekening + een verborgen marker in het concept, en de
                  transport-regel slaat gemarkeerde mail over (zodat er nooit twee komen);
                  mail vanaf mobiel (zonder marker) krijgt de handtekening alsnog server-side.

    Graph heeft geen transport-rule API: Otto genereert de disclaimer-HTML + een PowerShell-
    script dat de admin plakt, en `rule_attested_at` legt vast dat de admin dat deed.
    """
    __tablename__ = "cloud_signature_settings"
    __table_args__ = (db.UniqueConstraint("organization_id", name="uq_cloud_sig_org"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    mode = db.Column(db.String(8), nullable=False, default="client")  # client | cloud | combo
    signature_id = db.Column(db.Integer, db.ForeignKey("signatures.id"))  # NULL = org-standaard
    marker = db.Column(db.String(32), nullable=False, default="otto-sig-v1")
    rule_attested_at = db.Column(db.DateTime)
    # Centraal beheer: staat dit aan, dan worden profielveld-wijzigingen van een gebruiker
    # (die de handtekening-placeholders voeden) niet direct opgeslagen maar als voorstel
    # ingediend — IT keurt ze goed of af. Zie SignatureChangeRequest + routes_user.save_profile.
    central_management = db.Column(db.Boolean, nullable=False, default=False)

    signature = db.relationship("Signature")


class BrandKit(TimestampMixin, db.Model):
    """Een centrale huisstijl (logo + accentkleur) per organisatie of per verzenddomein.

    Een tenant met meerdere merken/domeinen zet per domein een eigen brand kit; sjablonen
    nemen die automatisch over via de {{logo}}-placeholder (en de accentkleur). `domain`
    NULL = de standaard-huisstijl van de organisatie. Wijzig het logo op één plek en alle
    handtekeningen van dat domein volgen. Zie app/signatures.brand_kit_for + placeholder_values.
    """
    __tablename__ = "brand_kits"
    __table_args__ = (db.UniqueConstraint("organization_id", "domain", name="uq_brandkit_org_domain"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    domain = db.Column(db.String(128))  # NULL = org-standaard, anders een verzenddomein
    name = db.Column(db.String(128))
    logo_url = db.Column(db.String(512))       # https of /media/sig-asset/<id> (→ CID bij aflevering)
    accent_color = db.Column(db.String(16))    # hex, bv. #6C4CE6
    # Social-profielen per merk/domein: {"linkedin": "https://…", "x": "…", …}. Voedt de
    # {{social}}-placeholder (e-mailveilige, in de accentkleur gestylede iconenrij). Zie
    # app/signatures.social_html. Live "laatste post"-feeds vergen LinkedIn/Facebook-API-
    # tokens (buiten bereik zonder die tokens) — dit blijft profiel-links, geen feed.
    socials = db.Column(db.JSON)               # dict netwerk→URL, NULL = geen


class BannerStat(TimestampMixin, db.Model):
    """Dagelijkse teller per banner: weergaven + clicks. Puur aggregaat, geen persoons-
    gegevens (geen IP, geen ontvanger) — AVG-vriendelijk. Eén rij per (banner, datum)."""
    __tablename__ = "banner_stats"
    __table_args__ = (db.UniqueConstraint("banner_id", "day", name="uq_bannerstat_banner_day"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    banner_id = db.Column(db.Integer, db.ForeignKey("signature_banners.id"), nullable=False, index=True)
    day = db.Column(db.Date, nullable=False)
    impressions = db.Column(db.Integer, nullable=False, default=0)
    clicks = db.Column(db.Integer, nullable=False, default=0)


class SignatureChangeRequest(TimestampMixin, db.Model):
    """Een door een gebruiker voorgestelde wijziging aan zijn eigen handtekening-velden,
    wachtend op goedkeuring door IT (alleen in centrale modus).

    `payload` is een JSON-map van de voorgestelde waarden (bv. {"phone": "…", "var-5": "…"});
    alleen de velden die de gebruiker echt wijzigde. Bij goedkeuring worden ze op de
    echte profielvelden/variabele-waarden toegepast (zie routes_admin), zodat de resolver
    en placeholder_values ongewijzigd blijven. Eén open voorstel per gebruiker.
    """
    __tablename__ = "signature_change_requests"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    payload = db.Column(db.JSON, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="pending")  # pending | approved | rejected
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    note = db.Column(db.String(255))

    user = db.relationship("User", foreign_keys=[user_id])


class SignatureTemplate(TimestampMixin, db.Model):
    """Een herbruikbaar handtekening-sjabloon in de galerij van een organisatie.

    De vier ingebouwde sjablonen blijven in Python (app/signature_templates.py) — die
    tabel bevat alléén door de organisatie geïmporteerde ('custom') ontwerpen, zodat
    een team zijn eigen huisstijl-startpunten kan bewaren naast de standaardgalerij.
    `html` is al gesanitized (app/htmlview.sanitize_signature_html) en e-mailveilig.
    """
    __tablename__ = "signature_templates"
    __table_args__ = (db.UniqueConstraint("organization_id", "slug", name="uq_sig_tpl_org_slug"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    slug = db.Column(db.String(64), nullable=False)   # uniek per organisatie
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.String(255))
    html = db.Column(db.Text, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))


class SignatureAsset(TimestampMixin, db.Model):
    """Een afbeelding (logo/banner) die in handtekeningen wordt ingebed als verborgen
    bijlage (Content-ID), zodat mailclients ze niet als 'externe afbeelding' blokkeren.

    Org-breed herbruikbaar; `signature_id` NULL = niet aan één handtekening gebonden.
    De bytes staan in de database (klein: logo's/banners), niet op schijf. De builder
    verwijst ernaar als <img src="/media/sig-asset/<id>">; bij de write-back naar Outlook
    wordt die src herschreven naar cid:<token> en gaan de bytes mee als inline-bijlage.
    """
    __tablename__ = "signature_assets"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    signature_id = db.Column(db.Integer, db.ForeignKey("signatures.id"))  # NULL = herbruikbaar
    kind = db.Column(db.String(16))  # 'logo' | 'banner' | 'image'
    content_type = db.Column(db.String(64), nullable=False)
    filename = db.Column(db.String(255))
    data = db.Column(db.LargeBinary, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))


class UserPhoto(TimestampMixin, db.Model):
    """De gecachte Microsoft 365-profielfoto van een gebruiker, voor de {{foto}}-placeholder.

    Apart van de vaak-geladen users-rij (foto's zijn ~5–30 KB). `etag` maakt de
    verversing conditioneel: bij ongewijzigde foto downloadt de sync niets opnieuw.
    Weergave alleen via de geauthenticeerde route /media/user-photo/<id> (preview) of
    als cid: in de aflevering — een geauthenticeerde Otto-URL werkt nooit in het postvak
    van de ontvanger, dus in de mail zelf gaat de foto altijd als inline-bijlage mee.
    """
    __tablename__ = "user_photos"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    content_type = db.Column(db.String(64), nullable=False)
    etag = db.Column(db.String(128))
    data = db.Column(db.LargeBinary, nullable=False)


class SignatureRule(TimestampMixin, db.Model):
    """Een regel die een handtekening toewijst op basis van voorwaarden (CodeTwo-stijl).

    Rijker dan een `SignatureAssignment`: een regel matcht op eigenschappen van de
    verzender én de ontvanger (afdeling, intern/extern, ontvanger-domein, postvak,
    M365-groep). De regels worden op `priority` geëvalueerd (laag wint eerst); de eerste
    ingeschakelde regel die matcht en een bruikbare handtekening heeft, wint. Nul regels =
    het oude gedrag (toewijzingen → domein → standaard). Zie app/signatures.py:resolve_rules.
    """
    __tablename__ = "signature_rules"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    signature_id = db.Column(db.Integer, db.ForeignKey("signatures.id"), nullable=False, index=True)
    name = db.Column(db.String(128))
    priority = db.Column(db.Integer, nullable=False, default=100)  # lager = eerst
    enabled = db.Column(db.Boolean, nullable=False, default=True)

    signature = db.relationship("Signature")
    conditions = db.relationship("SignatureRuleCondition", back_populates="rule",
                                 cascade="all, delete-orphan")


class SignatureRuleCondition(TimestampMixin, db.Model):
    """Eén voorwaarde van een regel. Alle voorwaarden van een regel moeten kloppen (AND).

    `field` ∈ {sender_mailbox, sender_department, sender_group, recipient_domain,
    recipient_scope}; `op` ∈ {equals, contains, in, is}. `value` is de vergelijkingswaarde
    (bij 'in' een komma-lijst). Alle velden zijn afleidbaar uit (gebruiker, postvak,
    bericht) zonder extra Graph-call — zie de context in app/signatures.py.
    """
    __tablename__ = "signature_rule_conditions"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    rule_id = db.Column(db.Integer, db.ForeignKey("signature_rules.id"), nullable=False, index=True)
    field = db.Column(db.String(32), nullable=False)
    op = db.Column(db.String(16), nullable=False, default="equals")
    value = db.Column(db.String(255))

    rule = db.relationship("SignatureRule", back_populates="conditions")


class SignatureBanner(TimestampMixin, db.Model):
    """Een (eventueel geplande) banner die boven of onder de handtekening komt.

    Denk aan een campagne- of kerstbanner die vanzelf stopt. `image_url` mag een externe
    https-URL zijn óf een geüploade asset (/media/sig-asset/<id>) — die laatste wordt in de
    aflevering als CID-bijlage ingebed (zie app/signatures.inline_images_for). De banner is
    actief wanneer hij is ingeschakeld én now binnen [starts_at, ends_at] valt (lege grens =
    open). Bij meerdere actieve banners wint de hoogste `weight`. Alleen de HTML-weergave
    krijgt een banner; de platte-tekstversie blijft ongewijzigd.
    """
    __tablename__ = "signature_banners"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    name = db.Column(db.String(128))
    image_url = db.Column(db.String(512), nullable=False)
    link_url = db.Column(db.String(512))
    starts_at = db.Column(db.DateTime)
    ends_at = db.Column(db.DateTime)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    placement = db.Column(db.String(8), nullable=False, default="below")  # above | below
    weight = db.Column(db.Integer, nullable=False, default=0)
    # Doelbereik: org (iedereen), domain (verzenddomein), department (afdeling van de
    # verzender) of group (Microsoft 365-groep waar het postvak in zit). Zo kan een tenant
    # met meerdere domeinen/afdelingen/groepen per doelgroep een eigen banner tonen.
    scope_kind = db.Column(db.String(16), nullable=False, default="org")  # org|domain|department|group
    scope_value = db.Column(db.String(255))  # domein / afdelingsnaam / M365-groep-GUID
    # Meting (opt-in): telt weergaven (tracking-pixel) en clicks (redirect). Alleen aggregaat
    # per dag, geen persoonsgegevens (geen IP/ontvanger) — zie app/signature_analytics.py.
    track = db.Column(db.Boolean, nullable=False, default=False)

    def is_active(self, now) -> bool:
        if not self.enabled:
            return False
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True


class SignatureVariable(TimestampMixin, db.Model):
    """Een eigen placeholder voor in handtekeningen, naast de vaste M365-profielvelden.

    `user_id` NULL = door een admin gedefinieerd voor de hele organisatie: iedereen kan
    (en de admin kan vóór iedereen) er een waarde voor invullen. Met `user_id` is het
    een persoonlijke variabele: alleen die gebruiker ziet hem en vult hem in. De key is
    uniek per organisatie zodat {{key}} nooit dubbelzinnig is.

    `graph_attr` (optioneel) koppelt de variabele aan een Microsoft 365-/Entra-attribuut
    (bv. "city", "onPremisesExtensionAttributes.extensionAttribute3" of een schema-extension
    "extension_<appId>_kostenplaats"). Is die gezet, dan wordt de waarde per gebruiker uit
    het Graph-profiel gehaald tijdens de sync en read-only in de matrix getoond — de admin
    hoeft hem dan niet handmatig in te vullen.
    """
    __tablename__ = "signature_variables"
    __table_args__ = (db.UniqueConstraint("organization_id", "key", name="uq_sig_var_org_key"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))  # NULL = organisatiebreed
    key = db.Column(db.String(64), nullable=False)   # slug: [a-z][a-z0-9_]*
    label = db.Column(db.String(128), nullable=False)
    graph_attr = db.Column(db.String(128))  # optioneel: gekoppeld M365-/Entra-attribuut

    values = db.relationship("SignatureVariableValue", back_populates="variable",
                             cascade="all, delete-orphan")

    @property
    def is_personal(self) -> bool:
        return self.user_id is not None

    @property
    def is_mapped(self) -> bool:
        return bool(self.graph_attr)


class SignatureVariableValue(TimestampMixin, db.Model):
    """De ingevulde waarde van één variabele voor één gebruiker."""
    __tablename__ = "signature_variable_values"
    __table_args__ = (db.UniqueConstraint("variable_id", "user_id", name="uq_sig_var_value"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    variable_id = db.Column(db.Integer, db.ForeignKey("signature_variables.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    value = db.Column(db.String(512))

    variable = db.relationship("SignatureVariable", back_populates="values")
