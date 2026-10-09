"""Knowledge sources for grounding AI drafts (RAG) — e.g. a SharePoint library.

Org-scoped. Documents are NEVER copied into Otto: retrieval runs live against
Microsoft Graph Search at draft time and only short snippets reach the LLM
(see app/ai/knowledge.py). This keeps the data footprint minimal (GDPR) and the
content always fresh + permission-trimmed by Microsoft 365.
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class KnowledgeSource(TimestampMixin, db.Model):
    __tablename__ = "knowledge_sources"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    type = db.Column(db.String(24), default="sharepoint")   # sharepoint
    name = db.Column(db.String(128))                         # display name
    site_url = db.Column(db.String(512))                     # SharePoint site/library URL the admin pasted
    library_path = db.Column(db.String(512))                 # KQL `path:` scope (defaults to site_url)
    site_id = db.Column(db.String(255))                      # resolved Graph siteId (optional cache)
    drive_id = db.Column(db.String(255))                     # resolved driveId (optional cache)
    status = db.Column(db.String(16), default="connected")   # connected / error / disabled

    organization = db.relationship("Organization", backref="knowledge_sources")


class KnowledgeEntry(TimestampMixin, db.Model):
    """Bedrijfskennis die de klant zélf invoert — de bron die geen Microsoft 365 vereist.

    De SharePoint-bron hierboven doorzoekt documenten live en laat ze staan waar ze
    staan. Dat is de nettere oplossing, maar ze werkt alleen voor organisaties mét
    M365, een eigen app met Sites.Read.All en een ingevulde GRAPH_KNOWLEDGE_*-config.
    Een Gmail-klant of een KMO zonder SharePoint hield daardoor niets over, en kreeg
    dus concepten die niets van zijn producten, prijzen of termijnen wisten.

    Hier bewaart Otto de tekst wel zelf — dat is de prijs voor een bron die overal
    werkt. Vandaar de scoping op organisatie (en optioneel op één gedeeld postvak,
    net als bij snippets: support@ en sales@ weten andere dingen).
    """
    __tablename__ = "knowledge_entries"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    # NULL = geldt organisatiebreed; anders alleen in deze (gedeelde) mailbox.
    account_id = db.Column(db.Integer, db.ForeignKey("mail_accounts.id", ondelete="CASCADE"), index=True)

    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(16), default="text", nullable=False)  # text / file
    filename = db.Column(db.String(255))                               # alleen bij source=file
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    # Hoe vaak dit blok een concept heeft onderbouwd — laat zien wat echt meetelt.
    used_count = db.Column(db.Integer, default=0, nullable=False)

    organization = db.relationship("Organization")
    account = db.relationship("MailAccount")

    @property
    def is_org_wide(self) -> bool:
        return self.account_id is None
