"""Pydantic schemas for Notification Jobs, Attempts, and Reminder Policies."""

from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ReminderPolicyBase(BaseModel):
    is_enabled: bool = True
    days_before_due: int = Field(default=3, ge=0, le=30)
    remind_on_due_date: bool = True
    days_after_due: int = Field(default=3, ge=0, le=30)
    overdue_interval_days: int = Field(default=7, ge=1, le=60)
    max_overdue_reminders: int = Field(default=3, ge=1, le=10)


class ReminderPolicyUpdate(ReminderPolicyBase):
    pass


class ReminderPolicyResponse(ReminderPolicyBase):
    model_config = ConfigDict(from_attributes=True)

    organization_id: uuid.UUID
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class NotificationAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    attempt_number: int
    status: str
    provider_message_id: Optional[str] = None
    error_message: Optional[str] = None
    attempted_at: datetime


class NotificationJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    event_type: str
    source_type: str
    source_id: uuid.UUID
    idempotency_key: str
    recipient_email: str
    recipient_name: Optional[str] = None
    subject: str
    status: str
    attempts_count: int
    max_attempts: int
    next_retry_at: datetime
    locked_at: Optional[datetime] = None
    locked_by: Optional[str] = None
    sent_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class NotificationJobDetailResponse(NotificationJobResponse):
    payload_json: Dict[str, Any]
    attempts: List[NotificationAttemptResponse] = []


class ProcessBatchResponse(BaseModel):
    claimed: int
    succeeded: int
    failed: int
    details: List[str] = []


class ReminderScanResponse(BaseModel):
    invoices_scanned: int
    reminders_queued: int
    details: List[str] = []
