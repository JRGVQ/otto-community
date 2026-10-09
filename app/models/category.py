"""Categories and subcategories (with plain-language AI instructions)."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class Category(TimestampMixin, db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))  # null => org-wide

    key = db.Column(db.String(64), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    color = db.Column(db.String(9), default="#6C4CE6")
    ai = db.Column(db.Text)  # plain-language instruction
    draft_enabled = db.Column(db.Boolean, default=False)
    is_default = db.Column(db.Boolean, default=False)
    is_org_template = db.Column(db.Boolean, default=True)
    position = db.Column(db.Integer, default=0)

    # Auto-foldering (opt-in per mailbox via *_settings.auto_folder_enabled): when False,
    # mail in this category is moved out of the inbox into `target_folder` (created on the
    # provider if missing). Default True keeps every category in the inbox — no surprises.
    keep_in_inbox = db.Column(db.Boolean, default=True)
    target_folder = db.Column(db.String(128))  # provider folder display-name to move into

    # Ruis (zie app/noise.py): mail in deze categorie vraagt geen mens. Bulkmail wordt er
    # zónder AI-classificatie in gelegd, er komt nooit een concept voor, en ze blijft uit
    # de dagelijkse briefing. Opt-in per postvak — standaard is geen enkele categorie ruis.
    is_noise = db.Column(db.Boolean, default=False, nullable=False)

    organization = db.relationship("Organization", back_populates="categories")
    subcategories = db.relationship(
        "Subcategory", back_populates="category",
        cascade="all, delete-orphan", order_by="Subcategory.position",
    )
    messages = db.relationship("Message", back_populates="category")

    @property
    def message_count(self) -> int:
        return len([m for m in self.messages])

    def to_modal_dict(self) -> dict:
        """Serialised form consumed by the category edit modal (categories.js)."""
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "ai": self.ai or "",
            "draft_enabled": bool(self.draft_enabled),
            "keep_in_inbox": bool(self.keep_in_inbox),
            "target_folder": self.target_folder or "",
            "is_noise": bool(self.is_noise),
            "can_delete": True,
            "subs": [
                {"name": s.name, "ai": s.ai or "", "draft_enabled": bool(s.draft_enabled)}
                for s in self.subcategories
            ],
        }


class Subcategory(db.Model):
    __tablename__ = "subcategories"
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    ai = db.Column(db.Text)
    draft_enabled = db.Column(db.Boolean, default=False)
    position = db.Column(db.Integer, default=0)

    category = db.relationship("Category", back_populates="subcategories")
