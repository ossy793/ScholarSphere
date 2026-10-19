"""Importing this package registers every model on Base.metadata (Alembic relies on it)."""

from app.models.admin import AdminInvite, AdminUser, AuditLog, AuthSession
from app.models.base import Base
from app.models.dues import ClassDue, DueRevision
from app.models.payments import LedgerAccount, LedgerEntry, Payment, RepWallet, WebhookEvent
from app.models.payouts import BankAccountChange, PayoutRequest
from app.models.refunds import PaymentReminder, Refund
from app.models.support import Announcement, SupportMessage, SupportTicket
from app.models.rep import RepApplication, RepBankAccount
from app.models.system import AppSetting, EmailOutbox, RateLimit
from app.models.user import Class, ClassMembership, University, User, UserToken
from app.models.worker import ReconciliationRun, WorkerJob

__all__ = [
    "Announcement",
    "SupportMessage",
    "SupportTicket",
    "AdminInvite",
    "AdminUser",
    "AppSetting",
    "AuditLog",
    "AuthSession",
    "BankAccountChange",
    "PayoutRequest",
    "Base",
    "Class",
    "ClassDue",
    "ClassMembership",
    "DueRevision",
    "EmailOutbox",
    "LedgerAccount",
    "LedgerEntry",
    "Payment",
    "PaymentReminder",
    "Refund",
    "RepWallet",
    "WebhookEvent",
    "RateLimit",
    "ReconciliationRun",
    "WorkerJob",
    "RepApplication",
    "RepBankAccount",
    "University",
    "User",
    "UserToken",
]
