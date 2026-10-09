"""Task lists — mirrors Microsoft To Do lists (`todoTaskList`), per user.

Otto-local lists have no `provider_list_id`; To Do-backed lists carry the Graph id and
a `delta_link` so the pull only fetches what changed since the last sync.
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class TodoList(TimestampMixin, db.Model):
    __tablename__ = "todo_lists"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    name = db.Column(db.String(128), nullable=False)
    is_default = db.Column(db.Boolean, default=False)
    position = db.Column(db.Integer, default=0)

    provider_list_id = db.Column(db.String(255))   # To Do list id (NULL for Otto-local)
    delta_link = db.Column(db.Text)                 # To Do delta link for incremental pull

    tasks = db.relationship("Task", back_populates="todo_list")
