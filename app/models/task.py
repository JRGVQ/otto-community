"""Tasks — Otto's task list, two-way synced with Microsoft To Do (personal) and
mirrored to Microsoft Planner (team). A task targets exactly one backend, recorded in
`provider_kind`: 'local' (Otto only), 'todo' (Microsoft To Do), or 'planner'."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class Task(TimestampMixin, db.Model):
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    title = db.Column(db.String(255), nullable=False)
    context = db.Column(db.String(255))
    notes = db.Column(db.Text)                          # To Do task body
    due_display = db.Column(db.String(32))  # "Vandaag" / "Wo 25 jun"
    due_date = db.Column(db.Date)
    reminder_at = db.Column(db.DateTime)                # To Do reminderDateTime
    recurrence = db.Column(db.String(32))               # '', daily, weekdays, weekly, monthly, yearly
    recurrence_interval = db.Column(db.Integer, default=1)  # "every N" (e.g. 2 = every 2 weeks)
    list_name = db.Column(db.String(64), default="Werk")
    done = db.Column(db.Boolean, default=False)
    # To Do status: notStarted / inProgress / completed. Kept in sync with `done`
    # (done == completed) so existing toggle/templates keep working.
    status = db.Column(db.String(16), default="notStarted")
    important = db.Column(db.Boolean, default=False)    # To Do importance == high (the star)
    urgent = db.Column(db.Boolean, default=False)       # Otto UI flag (red due date)

    linked_message_id = db.Column(db.Integer, db.ForeignKey("messages.id"))
    linked_subject = db.Column(db.String(512))
    # Het verzonden bericht waaruit deze taak automatisch is afgeleid (app/commitments.py).
    # Verzonden mail komt niet als Message binnen, dus linked_message_id kan hier niet;
    # dit veld voorkomt dat dezelfde toezegging bij elke sweep opnieuw een taak oplevert.
    source_sent_message_id = db.Column(db.String(255), index=True)

    # ── Backend linkage ──────────────────────────────────────────────────────
    provider_kind = db.Column(db.String(16), default="local")  # local / todo / planner
    provider_task_id = db.Column(db.String(255))        # To Do task id OR Planner task id
    provider_etag = db.Column(db.String(255))           # To Do @odata.etag (conflict/If-Match)
    provider_last_modified = db.Column(db.DateTime)     # remote lastModifiedDateTime
    # To Do list this task belongs to (NULL for Planner/local).
    todo_list_id = db.Column(db.Integer, db.ForeignKey("todo_lists.id"))

    # Microsoft Planner linkage + assignee (mirrored locally for rendering).
    plan_id = db.Column(db.String(64))
    plan_name = db.Column(db.String(255))
    bucket_id = db.Column(db.String(64))
    bucket_name = db.Column(db.String(255))
    assignee_id = db.Column(db.String(64))
    assignee_name = db.Column(db.String(255))
    assignee_email = db.Column(db.String(255))

    linked_message = db.relationship("Message")
    todo_list = db.relationship("TodoList", back_populates="tasks")

    def to_modal_dict(self) -> dict:
        """Fields the edit modal (tasks.js) needs, in its input formats."""
        return {
            "id": self.id,
            "title": self.title or "",
            "notes": self.notes or "",
            "due": self.due_date.isoformat() if self.due_date else "",
            "reminder": self.reminder_at.strftime("%Y-%m-%dT%H:%M") if self.reminder_at else "",
            "recurrence": self.recurrence or "",
            "recurrence_interval": self.recurrence_interval or 1,
            "important": bool(self.important),
        }
    steps = db.relationship(
        "TaskStep", back_populates="task", cascade="all, delete-orphan",
        order_by="TaskStep.position",
    )
    attachments = db.relationship(
        "TaskAttachment", back_populates="task", cascade="all, delete-orphan",
    )


class TaskStep(TimestampMixin, db.Model):
    """A subtask — mirrors a Microsoft To Do checklistItem."""
    __tablename__ = "task_steps"
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    done = db.Column(db.Boolean, default=False)
    position = db.Column(db.Integer, default=0)
    provider_step_id = db.Column(db.String(255))        # To Do checklistItem id

    task = db.relationship("Task", back_populates="steps")


class TaskAttachment(TimestampMixin, db.Model):
    """A file attachment — mirrors a Microsoft To Do taskFileAttachment. Only the
    metadata is stored; the bytes are fetched from Graph on demand (download route)."""
    __tablename__ = "task_attachments"
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False)
    provider_attachment_id = db.Column(db.String(255))  # To Do attachment id
    name = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(128))
    size = db.Column(db.Integer)

    task = db.relationship("Task", back_populates="attachments")
