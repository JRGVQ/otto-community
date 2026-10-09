"""Import every model so SQLAlchemy metadata (and Alembic autogenerate) see them all."""
from app.models.ai_usage import AiUsage
from app.models.category import Category, Subcategory
from app.models.draft import Draft, DraftToneVariant
from app.models.email_log import EmailLog, EmailTemplate
from app.models.error_log import ErrorLog
from app.models.followup import FollowUp
from app.models.knowledge import KnowledgeEntry, KnowledgeSource
from app.models.license import License
from app.models.mail_account import MailAccount
from app.models.mailbox_member import MailboxMember
from app.models.mail_rule import MailRule
from app.models.mailbox_category import MailboxCategory
from app.models.mailbox_settings import MailboxSettings
from app.models.message import Message, Thread
from app.models.note import MessageNote
from app.models.platform_setting import PlatformSetting
from app.models.request_log import RequestLog
from app.models.org import (
    AiConfig,
    AuditLog,
    GraphScope,
    Integration,
    Organization,
    SsoConfig,
    SystemStatus,
)
from app.models.profile import Feedback, StyleProfile
from app.models.scheduler import SchedulerLock
from app.models.task import Task, TaskAttachment, TaskStep
from app.models.todo_list import TodoList
from app.models.domain import DomainClaim
from app.models.crm_connection import CrmConnection
from app.models.directory import (
    Company,
    Contact,
    Project,
    ProjectContact,
    ProjectMessage,
)
from app.models.autoreply import AutoReplySetting
from app.models.org_event import OrgEvent
from app.models.signature import (
    BannerStat,
    BrandKit,
    CloudSignatureSetting,
    Signature,
    SignatureAssignment,
    SignatureAsset,
    SignatureBanner,
    SignatureChangeRequest,
    SignatureRule,
    SignatureRuleCondition,
    SignatureTemplate,
    SignatureVariable,
    SignatureVariableValue,
    UserPhoto,
)
from app.models.snippet import Snippet
from app.models.staged_appointment import StagedAppointment
from app.models.teams_pending import TeamsPending
from app.models.unsubscribe_log import UnsubscribeLog
from app.models.user import Permission, Role, RolePermission, Setting, User

__all__ = [
    "Category", "Subcategory",
    "Draft", "DraftToneVariant",
    "EmailLog", "EmailTemplate",
    "ErrorLog",
    "FollowUp",
    "KnowledgeEntry",
    "KnowledgeSource",
    "License",
    "MailAccount",
    "MailRule",
    "MailboxMember",
    "MailboxCategory",
    "MailboxSettings",
    "Message", "Thread",
    "MessageNote",
    "AiConfig", "AiUsage", "AuditLog", "GraphScope", "Integration",
    "Organization", "SsoConfig", "SystemStatus",
    "Feedback", "StyleProfile",
    "SchedulerLock",
    "Task", "TaskStep", "TaskAttachment", "TodoList",
    "DomainClaim",
    "CrmConnection",
    "Company", "Contact", "Project", "ProjectContact", "ProjectMessage",
    "OrgEvent",
    "AutoReplySetting", "BannerStat", "BrandKit", "CloudSignatureSetting", "SignatureBanner", "SignatureChangeRequest",
    "Signature", "SignatureAssignment", "SignatureAsset",
    "SignatureRule", "SignatureRuleCondition", "SignatureTemplate",
    "SignatureVariable", "SignatureVariableValue", "UserPhoto",
    "Snippet",
    "StagedAppointment",
    "TeamsPending",
    "UnsubscribeLog",
    "PlatformSetting",
    "RequestLog",
    "Permission", "Role", "RolePermission", "Setting", "User",
]
