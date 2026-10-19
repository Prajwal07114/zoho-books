"""Notification and Automation Endpoints: History, Retries, Policies, and Manual Processing."""

from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_tenant_session
from app.models.identity import User
from app.models.notification import NotificationAttempt, NotificationJob
from app.schemas.notification import (
    NotificationJobDetailResponse,
    NotificationJobResponse,
    ProcessBatchResponse,
    ReminderPolicyResponse,
    ReminderPolicyUpdate,
    ReminderScanResponse,
)
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["Notifications & Automation"])


@router.get("", response_model=List[NotificationJobResponse])
async def list_notifications(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status (pending, processing, sent, failed)"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_tenant_session),
) -> List[NotificationJob]:
    """List notification jobs for the active tenant with optional status and event filters."""
    stmt = (
        select(NotificationJob)
        .order_by(desc(NotificationJob.created_at))
        .limit(limit)
        .offset(offset)
    )
    if status_filter:
        stmt = stmt.where(NotificationJob.status == status_filter)
    if event_type:
        stmt = stmt.where(NotificationJob.event_type == event_type)

    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.get("/reminder-policy", response_model=ReminderPolicyResponse)
async def get_reminder_policy(
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> ReminderPolicyResponse:
    """Retrieve the reminder policy configuration for the active tenant."""
    org_id = uuid.UUID(x_organization_id)
    policy = await NotificationService.get_or_create_reminder_policy(db, org_id)
    await db.commit()
    return ReminderPolicyResponse.model_validate(policy)


@router.put("/reminder-policy", response_model=ReminderPolicyResponse)
async def update_reminder_policy(
    payload: ReminderPolicyUpdate,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
    current_user: User = Depends(get_current_user),
) -> ReminderPolicyResponse:
    """Update reminder policy configuration for the active tenant."""
    org_id = uuid.UUID(x_organization_id)
    policy = await NotificationService.update_reminder_policy(db, org_id, payload)
    await db.commit()
    return ReminderPolicyResponse.model_validate(policy)


@router.post("/process", response_model=ProcessBatchResponse)
async def process_notifications_batch(
    batch_size: int = Query(20, ge=1, le=100),
    x_organization_id: Optional[str] = Header(None, alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
) -> ProcessBatchResponse:
    """Trigger an immediate processing run of pending outbox notifications."""
    org_id = uuid.UUID(x_organization_id) if x_organization_id else None
    result = await NotificationService.process_pending_jobs(batch_size=batch_size, organization_id=org_id)
    return result


@router.post("/run-reminders", response_model=ReminderScanResponse)
async def run_reminder_scan(
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
) -> ReminderScanResponse:
    """Trigger an immediate reminder scan for outstanding invoices of the active tenant."""
    org_id = uuid.UUID(x_organization_id)
    result = await NotificationService.evaluate_and_queue_reminders(organization_id=org_id)
    return result


@router.get("/{job_id}", response_model=NotificationJobDetailResponse)
async def get_notification_detail(
    job_id: uuid.UUID,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> NotificationJobDetailResponse:
    """Inspect full notification job details including delivery attempts."""
    org_id = uuid.UUID(x_organization_id)
    stmt = (
        select(NotificationJob)
        .where(
            NotificationJob.organization_id == org_id,
            NotificationJob.id == job_id,
        )
        .options(selectinload(NotificationJob.attempts))
    )
    res = await db.execute(stmt)
    job = res.scalar_one_or_none()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification job not found",
        )
    return NotificationJobDetailResponse.model_validate(job)


@router.post("/{job_id}/retry", response_model=NotificationJobResponse)
async def retry_notification(
    job_id: uuid.UUID,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
    current_user: User = Depends(get_current_user),
) -> NotificationJob:
    """Manually reset a failed notification job to pending for immediate retry."""
    org_id = uuid.UUID(x_organization_id)
    try:
        job = await NotificationService.retry_job(db, org_id, job_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification job not found",
        )
    await db.commit()
    return job
