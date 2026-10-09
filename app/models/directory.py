"""Otto's eigen lichte CRM: bedrijven (domeinen), contacten en projecten.

Anders dan `app/crm/` (dat inkomende mail naar een EXTERN CRM zoals Odoo/HubSpot logt),
bouwt dit een adresboek ín Otto op — passief uit het mailverkeer. Een bedrijf is een
domein (`acme.be`); een contact een e-mailadres. Beide kunnen een STANDAARDCATEGORIE
krijgen: mail van dat contact/domein gaat dan deterministisch in die categorie, zonder
AI-aanroep (het contact wint van het domein).

Projecten groeperen contacten en mail; een bericht kan naar een project 'gesleept'
worden. Dit is een Teams/Enterprise-functie (zie app/billing.org_has_teams).
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class Company(TimestampMixin, db.Model):
    """Een bedrijf/relatie, geïdentificeerd door zijn e-maildomein."""
    __tablename__ = "crm_companies"
    __table_args__ = (db.UniqueConstraint("organization_id", "domain", name="uq_crm_company_org_domain"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    domain = db.Column(db.String(255), nullable=False)   # kaal, lowercase: "acme.be"
    name = db.Column(db.String(255))                     # weergavenaam; leeg = het domein
    # Standaardcategorie voor álle mail van dit domein (contact-override gaat vóór).
    default_category_id = db.Column(db.Integer, db.ForeignKey("categories.id"))
    notes = db.Column(db.Text)

    default_category = db.relationship("Category")
    contacts = db.relationship("Contact", back_populates="company", cascade="all, delete-orphan")

    @property
    def display_name(self) -> str:
        return self.name or self.domain


class Contact(TimestampMixin, db.Model):
    """Eén e-mailcontact, passief opgebouwd uit inkomende/uitgaande mail."""
    __tablename__ = "crm_contacts"
    __table_args__ = (db.UniqueConstraint("organization_id", "email", name="uq_crm_contact_org_email"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    company_id = db.Column(db.Integer, db.ForeignKey("crm_companies.id"), index=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    name = db.Column(db.String(255))
    # Standaardcategorie voor dít contact — wint van de bedrijfsstandaard.
    default_category_id = db.Column(db.Integer, db.ForeignKey("categories.id"))
    message_count = db.Column(db.Integer, default=0)
    last_seen = db.Column(db.DateTime)

    company = db.relationship("Company", back_populates="contacts")
    default_category = db.relationship("Category")

    @property
    def display_name(self) -> str:
        return self.name or self.email


class Project(TimestampMixin, db.Model):
    """Een project waaraan contacten en mail gekoppeld worden."""
    __tablename__ = "crm_projects"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    color = db.Column(db.String(9), default="#6C4CE6")
    status = db.Column(db.String(16), default="open")   # open / done
    notes = db.Column(db.Text)

    members = db.relationship("ProjectContact", back_populates="project", cascade="all, delete-orphan")
    messages = db.relationship("ProjectMessage", back_populates="project", cascade="all, delete-orphan")


class ProjectContact(TimestampMixin, db.Model):
    """Koppeling contact ↔ project (many-to-many)."""
    __tablename__ = "crm_project_contacts"
    __table_args__ = (db.UniqueConstraint("project_id", "contact_id", name="uq_crm_projcontact"),)

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("crm_projects.id"), nullable=False, index=True)
    contact_id = db.Column(db.Integer, db.ForeignKey("crm_contacts.id"), nullable=False, index=True)

    project = db.relationship("Project", back_populates="members")
    contact = db.relationship("Contact")


class ProjectMessage(TimestampMixin, db.Model):
    """Een bericht dat aan een project is gekoppeld ('gesleept naar het project')."""
    __tablename__ = "crm_project_messages"
    __table_args__ = (db.UniqueConstraint("project_id", "message_id", name="uq_crm_projmsg"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey("crm_projects.id"), nullable=False, index=True)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=False, index=True)

    project = db.relationship("Project", back_populates="messages")
    message = db.relationship("Message")
