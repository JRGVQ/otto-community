"""Shared constants — design tokens, tones, roles, permissions.

Mirrors the values baked into the Otto.dc.html prototype so the server-rendered
app stays pixel- and behaviour-identical.
"""
from __future__ import annotations

from flask_babel import lazy_gettext as _l

# Default "how Otto should answer" guidance, shown on the Profile page and applied
# to every draft (StyleProfile.default_reply). A concrete example users can edit.
# NOT translated: this is a seed VALUE persisted to the DB (StyleProfile.default_reply),
# so it must be a plain str — a LazyString can't be bound as a SQL parameter, and once
# saved it's user-editable data, not UI chrome.
DEFAULT_REPLY_INSTRUCTIONS = (
    "Geen “bedankt voor je bericht” of “ik hoop dat alles goed gaat” — "
    "antwoord altijd duidelijk en to the point."
)

# ── Brand / design tokens (the ones referenced from Python; CSS holds the rest) ──
ACCENT = "#6C4CE6"
ACCENT_HOVER = "#5C3DD6"

# Colour palette offered in the category editor (prototype `colorChoices`).
COLOR_CHOICES = [
    "#6C4CE6", "#2D9CDB", "#F2994A", "#27AE60",
    "#EB5757", "#9B51E0", "#0EA5A4", "#E11D74",
]

# Avatar colours cycled for users/senders without an explicit colour.
AVATAR_COLORS = [
    "#6C4CE6", "#2D9CDB", "#27AE60", "#F2994A",
    "#9B51E0", "#0EA5A4", "#EB5757", "#0A66C2",
]

# ── Draft tones (prototype tone selector) ──
TONES = ["neutraal", "formeel", "kort", "vriendelijk"]
TONE_LABELS = {
    "neutraal": _l("Neutraal"),
    "formeel": _l("Formeel"),
    "kort": _l("Kort"),
    "vriendelijk": _l("Vriendelijk"),
}
DEFAULT_TONE = "neutraal"

# ── Draft lifecycle ──
DRAFT_CONCEPT = "concept"
DRAFT_SENT = "verzonden"
DRAFT_DISCARDED = "verworpen"

# ── Personal setting toggles that actually steer the pipeline ──
# Only keys in this tuple are shown on the Settings page and seeded for new users.
# A switch without consuming code does NOT belong here: a toggle that persists but
# changes nothing erodes trust in every other switch (and in the privacy promises).
USER_SETTING_KEYS = ("categorisatie", "drafts", "klaarzetten")

# ── Mailbox sync cadence (offered in Settings; value = seconds) ──
SYNC_INTERVAL_OPTIONS = [
    (120, _l("Elke 2 minuten")),
    (300, _l("Elke 5 minuten")),
    (900, _l("Elke 15 minuten")),
    (1800, _l("Elke 30 minuten")),
    (3600, _l("Elk uur")),
]
SYNC_INTERVAL_VALUES = {seconds for seconds, _ in SYNC_INTERVAL_OPTIONS}

# ── Roles & permissions (prototype roleList / perms / matrix) ──
ROLES = ["Eigenaar", "Beheerder", "Manager", "Gebruiker", "Alleen-lezen"]
OWNER_ROLE = "Eigenaar"

# Role *within a shared mailbox* (MailboxMember.role) — distinct from the org Role.
MAILBOX_ROLES = ["owner", "agent", "viewer"]
MAILBOX_ROLE_LABELS = {
    "owner": _l("Beheerder"),   # configures the mailbox + works it
    "agent": _l("Behandelaar"),  # works the mailbox (draft/send)
    "viewer": _l("Alleen-lezen"),
}

# NOT translated: the label is seeded into the `permissions` table (see app/tenancy.py),
# so it must stay a plain str — a LazyString can't be bound as a SQL parameter. These
# are internal role-matrix labels shown only on the owner-only roles screen (data).
PERMISSIONS = [
    ("viewInbox", "Eigen postvak bekijken"),
    ("editDrafts", "Concepten bewerken"),
    ("sendMail", "Mail verzenden"),
    ("manageOwnCats", "Eigen categorieën beheren"),
    ("manageOrgCats", "Org-categorieën beheren"),
    ("manageSignatureCampaigns", "Banners & brand kits (campagnes)"),
    ("manageUsers", "Gebruikers & rollen"),
    ("manageSSO", "SSO & beveiliging"),
    ("manageProviders", "Mailproviders & Graph"),
    ("manageAI", "AI & model instellen"),
    ("viewAudit", "Audit log inzien"),
]

# role -> {perm_key: 0|1}  (exact prototype matrix)
ROLE_MATRIX = {
    "Eigenaar":     dict(viewInbox=1, editDrafts=1, sendMail=1, manageOwnCats=1, manageOrgCats=1, manageSignatureCampaigns=1, manageUsers=1, manageSSO=1, manageProviders=1, manageAI=1, viewAudit=1),
    "Beheerder":    dict(viewInbox=1, editDrafts=1, sendMail=1, manageOwnCats=1, manageOrgCats=1, manageSignatureCampaigns=1, manageUsers=1, manageSSO=1, manageProviders=1, manageAI=1, viewAudit=1),
    "Manager":      dict(viewInbox=1, editDrafts=1, sendMail=1, manageOwnCats=1, manageOrgCats=0, manageSignatureCampaigns=0, manageUsers=0, manageSSO=0, manageProviders=0, manageAI=0, viewAudit=1),
    "Gebruiker":    dict(viewInbox=1, editDrafts=1, sendMail=1, manageOwnCats=1, manageOrgCats=0, manageSignatureCampaigns=0, manageUsers=0, manageSSO=0, manageProviders=0, manageAI=0, viewAudit=0),
    "Alleen-lezen": dict(viewInbox=1, editDrafts=0, sendMail=0, manageOwnCats=0, manageOrgCats=0, manageSignatureCampaigns=0, manageUsers=0, manageSSO=0, manageProviders=0, manageAI=0, viewAudit=0),
}

# Which admin screen requires which permission.
ADMIN_SCREEN_PERMS = {
    "overzicht": None,
    "users": "manageUsers",
    "roles": "manageUsers",
    "sso": "manageSSO",
    "providers": "manageProviders",
    "orgcats": "manageOrgCats",
    "ai": "manageAI",
    "audit": "viewAudit",
}

USER_SCREENS = ["inbox", "outlook", "briefing", "tasks", "teams", "categories", "integrations", "settings"]
ADMIN_SCREENS = ["overzicht", "users", "roles", "sso", "providers", "orgcats", "ai", "audit"]
