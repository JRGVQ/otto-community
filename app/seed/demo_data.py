"""The demo dataset for a throwaway showcase environment.

A fully fictional company (Acme NV) so nothing of the real operator is shown.
Demo login: demo@example.com / otto-demo.

Display strings are wrapped in lazy_gettext (_l) so the *scaffolding* every new
tenant gets (categories, settings, integrations, scopes) — and the demo mock
inbox — appear in the visitor's language. The seed code calls resolve() on the
data at provisioning time (inside a request), which turns each LazyString into a
plain str in the active locale; a LazyString can't be bound as a SQL parameter,
so this resolution step is required. Keys, colours, emails, proper nouns, role
and status values, and technical identifiers stay untranslated (they are logic /
data, not UI). The `time` hints stay literal too — they are parsed into real
timestamps by received_from_hint().
"""
from __future__ import annotations

from flask_babel import lazy_gettext as _l

DEMO_PASSWORD = "otto-demo"
DEMO_EMAIL = "demo@example.com"


def resolve(obj):
    """Deep-copy `obj`, turning every LazyString into a plain str in the active
    locale. Leaves real str/bool/int/float/None untouched (a LazyString is not a
    str subclass, so it falls through to the str() branch and gets resolved)."""
    if isinstance(obj, dict):
        return {k: resolve(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(resolve(v) for v in obj)
    if isinstance(obj, (str, bool, int, float)) or obj is None:
        return obj
    return str(obj)  # LazyString → resolved translation


ORGANIZATION = {
    "name": "Acme NV",
    "domain": "acme.be",
    "org_push_cats": True,
    "graph_app_id": "b7e3f2a1-9c4d-4e8a-bf12-3a6c9d0e1f55",
    "graph_tenant_id": "4f1d9a22-7b3e-46c0-a1d2-9e8f7c6b5a40",
    "graph_consent": True,
    "graph_notifications": True,
    "gmail_pubsub": "projects/example-project/topics/gmail-push",
    "gmail_watch": True,
    "imap_status": "enabled",
}

# ── Categories (with subcategories + plain-language AI instructions) ──────────
CATEGORIES = [
    {
        "key": "te_beantwoorden", "name": _l("Te beantwoorden"), "color": "#6C4CE6",
        "draft_enabled": True, "is_default": True,
        "ai": _l("Mails die een persoonlijk antwoord van mij vragen. Hiervoor zet Otto een concept klaar."),
        "subs": [
            (_l("Klanten"), _l("Bestaande klanten — antwoord vriendelijk, concreet en met een duidelijke volgende stap."), True),
            (_l("Prospecten"), _l("Nieuwe leads — enthousiast, stel een korte kennismaking of call voor."), True),
            (_l("Intern"), _l("Mails van collega’s die een antwoord vragen — kort en duidelijk."), True),
            (_l("Leveranciers"), _l("Leveranciers en partners — zakelijk en to-the-point."), True),
        ],
    },
    {
        "key": "fyi", "name": _l("Ter info (FYI)"), "color": "#2D9CDB",
        "draft_enabled": False, "is_default": True,
        "ai": _l("Belangrijk om te lezen, maar geen antwoord nodig — notulen, updates, cc-mails."),
        "subs": [
            (_l("CC / ter info"), _l("Ik sta in cc — enkel lezen, geen actie."), False),
            (_l("Updates"), _l("Statusupdates en aankondigingen. Vat samen indien lang."), False),
        ],
    },
    {
        "key": "vergaderingen", "name": _l("Vergaderingen"), "color": "#11998E",
        "draft_enabled": False, "is_default": True,
        "ai": _l("Vergaderingen en agenda — uitnodigingen, afspraken en bevestigingen."),
        "subs": [
            (_l("Uitnodigingen"), _l("Nieuwe vergaderverzoeken — controleer mijn agenda voor ik bevestig."), False),
            (_l("Bevestigingen"), _l("Bevestigde afspraken en wijzigingen."), False),
        ],
    },
    {
        "key": "financieel", "name": _l("Financieel"), "color": "#F2C94C",
        "draft_enabled": False, "is_default": True,
        "ai": _l("Facturen, betalingen en boekhouding."),
        "subs": [
            (_l("Facturen"), _l("Inkomende en uitgaande facturen — bewaren en doorsturen naar boekhouding."), False),
            (_l("Betalingen"), _l("Betaalbevestigingen, uitbetalingen en transacties."), False),
            (_l("Boekhouding"), _l("Van mijn boekhouder of administratie — kort en zakelijk."), True),
        ],
    },
    {
        "key": "nieuwsbrief", "name": _l("Nieuwsbrief / Marketing"), "color": "#F2994A",
        "draft_enabled": False, "is_default": True,
        "ai": _l("Bulk- en promotionele berichten, nieuwsbrieven en aanbiedingen."),
        "subs": [
            (_l("Vakliteratuur"), _l("Relevante vaknieuwsbrieven over design en tech — bewaren."), False),
            (_l("Promoties"), _l("Commerciële aanbiedingen — mag stil gearchiveerd worden."), False),
        ],
    },
    {
        "key": "meldingen", "name": _l("Meldingen"), "color": "#9AA4B2",
        "draft_enabled": False, "is_default": True,
        "ai": _l("Geautomatiseerde systeem- en platformmeldingen zoals login-alerts en betalingen."),
        "subs": [
            (_l("Beveiliging"), _l("Login- en beveiligingsmeldingen — markeer als belangrijk bij verdacht gedrag."), False),
            (_l("Systeem"), _l("Systeem- en platformnotificaties."), False),
        ],
    },
    {
        "key": "spam", "name": _l("Spam / Irrelevant"), "color": "#EB5757",
        "draft_enabled": False, "is_default": True,
        "ai": _l("Ongewenste of niet-relevante berichten."), "subs": [],
    },
]


def _d(neutraal, formeel, kort, vriendelijk):
    return {"neutraal": neutraal, "formeel": formeel, "kort": kort, "vriendelijk": vriendelijk}


# ── Messages (10) — bodies + 4 tone-variant drafts for the te_beantwoorden ones ─
# `key` is a stable identifier used to link tasks to their message, so the linking
# survives translation of the subject.
MESSAGES = [
    {
        "key": "offerte_redesign",
        "sender": "Marlies Decroix", "email": "m.decroix@lumen.be", "initials": "MD",
        "avatar": "#6C4CE6", "time": "08:42", "unread": True, "category": "te_beantwoorden",
        "lang": "antwoord in Nederlands",
        "subject": _l("Offerte website redesign — vraag over timing & prijs"),
        "snippet": _l("We willen onze website laten herontwerpen en hoorden goede dingen…"),
        "body": _l("Dag Sam,\n\nWe willen onze website laten herontwerpen en hoorden goede dingen over Acme NV. Kunnen jullie een indicatie geven van de timing en de prijs? We mikken op een lancering voor het najaar.\n\nAlvast bedankt,\nMarlies Decroix\nMarketing, Lumen NV"),
        "draft": _d(
            _l("Hallo Marlies,\n\nBedankt voor je bericht. We kunnen het redesign in ongeveer 6 weken opleveren, met een eerste ontwerp na week 2. De richtprijs ligt rond €8.500 excl. btw, afhankelijk van het aantal templates.\n\nZal ik een korte call inplannen om de scope door te nemen?\n\nGroeten,\nSam"),
            _l("Geachte mevrouw Decroix,\n\nDank voor uw aanvraag. Het redesign kan binnen circa zes weken worden opgeleverd, met een eerste ontwerpvoorstel na twee weken. De indicatieve prijs bedraagt €8.500 excl. btw.\n\nGraag plan ik een gesprek in om de scope te bespreken.\n\nMet vriendelijke groet,\nSam Verlinden\nAcme NV"),
            _l("Hoi Marlies,\n\nKan in ~6 weken, eerste ontwerp na 2 weken. Richtprijs €8.500 excl. btw. Zin in een korte call deze week?\n\nGroet,\nSam"),
            _l("Hey Marlies,\n\nLeuk dat je aan ons denkt! We krijgen het redesign in zo’n 6 weken rond, en na 2 weken laat ik je al een eerste ontwerp zien. Reken op ongeveer €8.500 excl. btw. Zullen we even bellen om alles door te nemen?\n\nWarme groet,\nSam")),
    },
    {
        "key": "btw_q2",
        "sender": "Thomas Vermeulen", "email": "thomas@boekhoudburo.be", "initials": "TV",
        "avatar": "#2D9CDB", "time": "08:15", "unread": True, "category": "te_beantwoorden",
        "lang": "antwoord in Nederlands",
        "subject": _l("BTW-aangifte Q2 — documenten nodig"),
        "snippet": _l("Voor de BTW-aangifte van Q2 heb ik nog je facturen en onkostennota’s nodig…"),
        "body": _l("Hoi Sam,\n\nVoor de BTW-aangifte van Q2 heb ik nog je facturen en onkostennota’s nodig. Kan je die deze week bezorgen? Deadline is eind volgende week.\n\nGroeten,\nThomas Vermeulen"),
        "draft": _d(
            _l("Hallo Thomas,\n\nBedankt voor de herinnering. Ik bezorg je de facturen en onkostennota’s van Q2 ten laatste woensdag. Laat gerust weten als je nog iets specifiek nodig hebt.\n\nGroeten,\nSam"),
            _l("Geachte heer Vermeulen,\n\nDank voor uw herinnering. De facturen en onkostennota’s van het tweede kwartaal bezorg ik u uiterlijk woensdag.\n\nMet vriendelijke groet,\nSam Verlinden"),
            _l("Hoi Thomas,\n\nQ2-documenten volgen woensdag. Iets specifieks nodig?\n\nGroet,\nSam"),
            _l("Hey Thomas,\n\nDank voor het seintje! Ik verzamel de Q2-facturen en onkostennota’s en stuur ze woensdag door. Roep maar als er nog iets ontbreekt.\n\nGroetjes,\nSam")),
    },
    {
        "key": "linkedin",
        "sender": "LinkedIn", "email": "notifications@linkedin.com", "initials": "in",
        "avatar": "#0A66C2", "time": "07:50", "unread": False, "category": "meldingen",
        "lang": "antwoord in Nederlands",
        "subject": _l("Je hebt 5 nieuwe connectieverzoeken"),
        "snippet": _l("Je hebt 5 nieuwe uitnodigingen om te connecten."),
        "body": _l("Je hebt 5 nieuwe uitnodigingen om te connecten. Bekijk ze in je netwerk."),
        "draft": None,
    },
    {
        "key": "stripe_payout",
        "sender": "Stripe", "email": "no-reply@stripe.com", "initials": "St",
        "avatar": "#635BFF", "time": "07:32", "unread": False, "category": "meldingen",
        "lang": "antwoord in Nederlands",
        "subject": _l("Je uitbetaling van €2.480 is onderweg"),
        "snippet": _l("Goed nieuws — je uitbetaling staat binnen 2 werkdagen op je rekening."),
        "body": _l("Goed nieuws — je uitbetaling van €2.480,00 is onderweg en staat binnen 2 werkdagen op je rekening."),
        "draft": None,
    },
    {
        "key": "smashing",
        "sender": "Smashing Magazine", "email": "mail@smashingmagazine.com", "initials": "SM",
        "avatar": "#E25C26", "time": "gisteren", "unread": False, "category": "nieuwsbrief",
        "lang": "antwoord in Nederlands",
        "subject": _l("This week in web design ✨"),
        "snippet": _l("Nieuwe CSS-features, design tokens in de praktijk en 7 portfolio’s…"),
        "body": _l("This week: nieuwe CSS-features, design tokens in de praktijk en 7 portfolio’s die opvallen."),
        "draft": None,
        # Demo unsubscribe: opens the newsletter's unsubscribe page (no server-side POST).
        "unsubscribe": {"url": "https://www.smashingmagazine.com/the-smashing-newsletter/"},
    },
    {
        "key": "notulen_aurora",
        "sender": "Pieter Janssens", "email": "pieter@acme.be", "initials": "PJ",
        "avatar": "#27AE60", "time": "gisteren", "unread": False, "category": "fyi",
        "lang": "antwoord in Nederlands",
        "subject": _l("Notulen kickoff — Project Aurora"),
        "snippet": _l("In bijlage de notulen van de kickoff. Geen actie nodig, puur ter info."),
        "body": _l("Hoi allen,\n\nIn bijlage de notulen van de kickoff van Project Aurora. Geen actie nodig, puur ter info.\n\nPieter"),
        "draft": None,
    },
    {
        "key": "sofie_call",
        "sender": "Sofie Maes", "email": "sofie.maes@novapartners.be", "initials": "SM",
        "avatar": "#9B51E0", "time": "gisteren", "unread": True, "category": "te_beantwoorden",
        "lang": "antwoord in Nederlands",
        "subject": _l("Kunnen we volgende week bellen?"),
        "snippet": _l("We zijn op zoek naar een partner voor een nieuw platform en zouden graag…"),
        "body": _l("Dag Sam,\n\nWe zijn op zoek naar een partner voor een nieuw platform en zouden graag even kennismaken. Heb je volgende week tijd voor een korte call?\n\nVriendelijke groeten,\nSofie Maes"),
        "draft": _d(
            _l("Hallo Sofie,\n\nGraag! Volgende week kan zeker. Schikt dinsdag of woensdag rond 14u? Stuur anders een paar momenten door die jou passen.\n\nGroeten,\nSam"),
            _l("Geachte mevrouw Maes,\n\nGraag plan ik een gesprek in. Volgende week dinsdag of woensdag rond 14u zou mij passen; laat gerust uw voorkeur weten.\n\nMet vriendelijke groet,\nSam Verlinden"),
            _l("Hoi Sofie,\n\nGraag! Di of wo volgende week rond 14u? Anders je voorkeur doorsturen.\n\nGroet,\nSam"),
            _l("Hey Sofie,\n\nSuper, daar maak ik graag tijd voor! Lukt dinsdag of woensdag rond 14u volgende week? Zo niet, stuur me even wat momenten die jou passen.\n\nWarme groet,\nSam")),
    },
    {
        "key": "spam_prijs",
        "sender": "Mega Prijs BV", "email": "win@mega-prijs.info", "initials": "MP",
        "avatar": "#EB5757", "time": "ma", "unread": False, "category": "spam",
        "lang": "antwoord in Nederlands",
        "subject": _l("U heeft €1.000.000 gewonnen!!!"),
        "snippet": _l("GEFELICITEERD! U bent geselecteerd als winnaar…"),
        "body": _l("GEFELICITEERD! U bent geselecteerd als winnaar van €1.000.000. Klik hier om uw prijs te claimen!!!"),
        "draft": None,
    },
    {
        "key": "ms_signin",
        "sender": "Microsoft 365", "email": "account@microsoft.com", "initials": "M",
        "avatar": "#0078D4", "time": "ma", "unread": False, "category": "meldingen",
        "lang": "antwoord in Nederlands",
        "subject": _l("Beveiligingsmelding: nieuwe aanmelding"),
        "snippet": _l("We hebben een nieuwe aanmelding op je account gedetecteerd."),
        "body": _l("We hebben een nieuwe aanmelding op je Microsoft 365-account gedetecteerd vanaf een nieuw apparaat. Was jij dit?"),
        "draft": None,
    },
    {
        "key": "hannah_feedback",
        "sender": "Hannah De Smet", "email": "hannah@brightco.be", "initials": "HD",
        "avatar": "#0EA5A4", "time": "ma", "unread": False, "category": "te_beantwoorden",
        "lang": "antwoord in Nederlands",
        "subject": _l("Feedback op laatste oplevering"),
        "snippet": _l("De laatste oplevering ziet er top uit, het team is enthousiast!"),
        "body": _l("Dag Sam,\n\nDe laatste oplevering ziet er top uit, het team is enthousiast! Twee kleine puntjes op de homepage stuur ik in bijlage. Kan dat nog deze week?\n\nGroeten,\nHannah De Smet"),
        "draft": _d(
            _l("Hallo Hannah,\n\nFijn dat de oplevering goed onthaald werd, bedankt voor je feedback. De twee aanpassingen aan de homepage neem ik mee en bezorg ik je vrijdag.\n\nGroeten,\nSam"),
            _l("Geachte mevrouw De Smet,\n\nDank voor uw feedback op de oplevering. De gevraagde aanpassingen aan de homepage worden doorgevoerd en uiterlijk vrijdag opgeleverd.\n\nMet vriendelijke groet,\nSam Verlinden"),
            _l("Hoi Hannah,\n\nTop, bedankt! De 2 homepage-aanpassingen lever ik vrijdag op.\n\nGroet,\nSam"),
            _l("Hey Hannah,\n\nWat leuk om te horen dat het goed zit! De twee tweaks aan de homepage pak ik er meteen bij — vrijdag staat alles voor je klaar.\n\nWarme groet,\nSam")),
    },
]

# ── Tasks (5) — `linked` is a stable message key (see MESSAGES[].key) ─────────
TASKS = [
    {"title": _l("Offerte website redesign opsturen"), "context": _l("Marlies Decroix wacht op timing + prijs"), "due": _l("Vandaag"), "list": _l("Werk"), "done": False, "linked": "offerte_redesign", "urgent": True},
    {"title": _l("BTW-documenten Q2 verzamelen"), "context": _l("Voor Thomas (boekhouder)"), "due": _l("Wo 25 jun"), "list": _l("Opvolgen"), "done": False, "linked": "btw_q2", "urgent": False},
    {"title": _l("Bellen met Sofie Maes"), "context": _l("Kennismaking, voorkeur volgende week"), "due": _l("Di 1 jul"), "list": _l("Werk"), "done": False, "linked": "sofie_call", "urgent": False},
    {"title": _l("Feedback Hannah verwerken"), "context": _l("2 puntjes op de homepage"), "due": _l("Vr 27 jun"), "list": _l("Werk"), "done": False, "linked": "hannah_feedback", "urgent": False},
    {"title": _l("Contract Project Aurora tekenen"), "context": _l("Afgerond"), "due": _l("Gisteren"), "list": _l("Werk"), "done": True, "linked": "notulen_aurora", "urgent": False},
]

# ── Integrations (7) ─────────────────────────────────────────────────────────
#    CRM staat hier niet meer tussen: echte CRM-koppelingen (Odoo, HubSpot,
#    Pipedrive, webhook) hebben eigen kaarten op de Integraties-pagina (app/crm/).
INTEGRATIONS = [
    {"key": "todo", "name": "Microsoft To Do", "tag": "TD", "color": "#2D6BDB", "desc": _l("Actiepunten worden taken met deadline en een link naar de mail."), "connected": True},
    {"key": "outlook", "name": "Microsoft 365 / Outlook", "tag": "O", "color": "#0078D4", "desc": _l("Mail koppelen, drafts en categorieën synchroniseren."), "connected": True},
    {"key": "agenda", "name": "Outlook / Google Agenda", "tag": "Ag", "color": "#27AE60", "desc": _l("Vergaderverzoeken herkennen en vrije momenten voorstellen."), "connected": False},
    {"key": "teams", "name": "Microsoft Teams", "tag": "Te", "color": "#5B5FC7", "desc": _l("Belangrijke mails of samenvattingen naar een kanaal sturen."), "connected": False},
    {"key": "slack", "name": "Slack", "tag": "Sl", "color": "#611F69", "desc": _l("Samenvattingen en alerts als melding in een kanaal."), "connected": False},
    {"key": "todoist", "name": "Todoist / Asana", "tag": "To", "color": "#E44332", "desc": _l("Alternatieve takenlijsten als je niet met To Do werkt."), "connected": False},
    {"key": "notion", "name": "Notion / OneNote", "tag": "No", "color": "#1B1B22", "desc": _l("Beslissingen en mailinhoud wegschrijven naar een kennisbank."), "connected": False},
]

# ── Settings — per user. Alleen schakelaars die de pijplijn ECHT aanstuurt ──
#    (zie USER_SETTING_KEYS in app/constants.py). vip/samenvatting/briefing/training
#    zijn verwijderd: het waren prototype-switches zonder consumerende code, en een
#    schakelaar die niets doet ondermijnt het vertrouwen in de rest. De belofte
#    "nooit modeltraining" staat nu als vast principe op de Instellingen-pagina.
SETTINGS = [
    {"key": "categorisatie", "label": _l("Automatische categorisatie"), "desc": _l("Otto sorteert elke nieuwe mail meteen in de juiste categorie. Uit? Dan blijft nieuwe mail ongesorteerd (en zonder concept) tot je dit weer aanzet."), "on": True},
    {"key": "drafts", "label": _l("Concept-antwoorden genereren"), "desc": _l("Voor mails die een antwoord vragen, zet Otto een concept klaar."), "on": True},
    {"key": "klaarzetten", "label": _l("Concepten klaarzetten in mailbox"), "desc": _l("Otto schrijft elk concept als draft terug naar je Postvak (Outlook Concepten-map / Gmail drafts), zodat het ook in je mailprogramma klaarstaat. Uit? Dan zie je concepten alleen in Otto."), "on": True},
]

# ── Users (6) — Sam is the demo login (demo@example.com). Role + status are DB
#    enum values (match Role.name / User.status), so they stay untranslated. ──
USERS = [
    {"name": "Sam Verlinden", "email": DEMO_EMAIL, "role": "Beheerder", "mailboxes": 2, "status": "Actief", "last": "nu", "initials": "SV", "color": "#6C4CE6", "is_demo": True},
    {"name": "Pieter Janssens", "email": "pieter@acme.be", "role": "Manager", "mailboxes": 1, "status": "Actief", "last": "12 min", "initials": "PJ", "color": "#27AE60"},
    {"name": "Lena Aerts", "email": "lena@acme.be", "role": "Gebruiker", "mailboxes": 1, "status": "Actief", "last": "1 u", "initials": "LA", "color": "#2D9CDB"},
    {"name": "Tom Claes", "email": "tom@acme.be", "role": "Gebruiker", "mailboxes": 1, "status": "Uitgenodigd", "last": "—", "initials": "TC", "color": "#F2994A"},
    {"name": "Sara Peeters", "email": "sara@acme.be", "role": "Alleen-lezen", "mailboxes": 0, "status": "Actief", "last": "3 d", "initials": "SP", "color": "#9B51E0"},
    {"name": "Karel Wouters", "email": "karel@acme.be", "role": "Gebruiker", "mailboxes": 1, "status": "Geblokkeerd", "last": "2 w", "initials": "KW", "color": "#EB5757"},
]

# ── SSO / Graph / AI / Audit / System status ──────────────────────
SSO = {"enabled": True, "method": "SAML 2.0", "idp": "Microsoft Entra ID", "domain": "acme.be", "verified": True, "enforce": True, "mfa": True, "jit": True, "allowed_domains": ["acme.be", "acme.eu"]}

GRAPH_SCOPES = [
    {"name": "Mail.ReadWrite", "desc": _l("Mail lezen, labelen en concepten aanmaken"), "on": True, "locked": True},
    {"name": "Mail.Send", "desc": _l("Mail verzenden — uit, want Otto verzendt nooit zelf"), "on": False, "locked": True},
    {"name": "MailboxSettings.Read", "desc": _l("Categorieën, handtekening en werkuren lezen"), "on": True, "locked": False},
    {"name": "Tasks.ReadWrite", "desc": _l("Taken aanmaken in Microsoft To Do en Planner"), "on": True, "locked": False},
    {"name": "Calendars.ReadWrite", "desc": _l("Agenda lezen en afspraken voorstellen"), "on": True, "locked": False},
    {"name": "offline_access", "desc": _l("Tokens vernieuwen op de achtergrond"), "on": True, "locked": True},
    {"name": "User.Read", "desc": _l("Basisprofiel van de gebruiker"), "on": True, "locked": True},
    {"name": "User.ReadBasic.All", "desc": _l("Collega's vinden om een taak aan toe te wijzen (vereist beheerderstoestemming)"), "on": True, "locked": False},
]

AI = {"model": "azure", "residency": _l("EU (België / West-Europa)"), "never_train": True, "fewshot": True, "limit": 500, "confidence": 70}

# `ago` = minutes before "now" at seed time, so each row gets a real occurred_at.
# Real names stay untranslated; the pseudo-actor "Systeem" and the actions are display.
AUDIT = [
    {"user": _l("Systeem"), "action": _l("genereerde 6 concepten en plaatste ze in de mailboxen"), "ago": 4, "kind": "system"},
    {"user": "Sam V.", "action": _l("verzond een concept naar m.decroix@lumen.be"), "ago": 12, "kind": "user"},
    {"user": "Sam V.", "action": _l("wijzigde de categorie van een bericht naar ‘Te beantwoorden’"), "ago": 27, "kind": "user"},
    {"user": _l("Systeem"), "action": _l("synchroniseerde het postvak — 3 nieuwe berichten"), "ago": 36, "kind": "system"},
    {"user": "Sam V.", "action": _l("meldde zich aan vanaf Brussel, België"), "ago": 58, "kind": "security"},
    {"user": "Sam V.", "action": _l("wijzigde de rol van Lena Aerts naar Gebruiker"), "ago": 96, "kind": "user"},
    {"user": "Pieter J.", "action": _l("koppelde een Microsoft 365-postvak via Graph"), "ago": 184, "kind": "provider"},
    {"user": "Sam V.", "action": _l("schakelde SSO-afdwinging in voor acme.be"), "ago": 1180, "kind": "security"},
    {"user": _l("Systeem"), "action": _l("verwijderde gecachte mailinhoud (retentie 30 d)"), "ago": 1830, "kind": "system"},
    {"user": "Sara P.", "action": _l("bekeek het audit log"), "ago": 2710, "kind": "user"},
    {"user": _l("Systeem"), "action": _l("maakte een back-up van de organisatiegegevens"), "ago": 4150, "kind": "system"},
    {"user": "Tom D.", "action": _l("voegde gedeeld postvak ‘support@acme.be’ toe"), "ago": 5600, "kind": "provider"},
]

SYSTEM_STATUS = [
    {"name": "Microsoft Graph API", "detail": _l("Change notifications actief"), "ok": True},
    {"name": "Gmail API + Pub/Sub", "detail": _l("Watch actief op 11 postvakken"), "ok": True},
    {"name": "LLM-service · Azure OpenAI (EU)", "detail": _l("Gem. 1,8 s per oproep"), "ok": True},
    {"name": "Taakwachtrij & workers", "detail": _l("12 taken in wachtrij"), "ok": True},
    {"name": "PostgreSQL", "detail": _l("Repliceerd, EU-regio"), "ok": True},
]
