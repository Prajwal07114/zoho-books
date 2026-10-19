"""Core Notification Service: Transactional Outbox, Queue Processor, and Reminder Engine."""

from datetime import date, datetime, timedelta
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import AsyncSessionLocal, set_tenant_context
from app.models.invoice import Invoice, InvoiceLine
from app.models.master import Contact
from app.models.notification import (
    InvoiceReminderHistory,
    NotificationAttempt,
    NotificationJob,
    ReminderPolicy,
)
from app.models.organization import Organization
from app.models.payment import Payment, PaymentAllocation
from app.schemas.notification import (
    ProcessBatchResponse,
    ReminderPolicyUpdate,
    ReminderScanResponse,
)
from app.services.email_provider import EmailAttachment, get_email_provider
from app.services.pdf_service import InvoicePDFGenerator

logger = logging.getLogger(__name__)


class NotificationService:
    """Enterprise Transactional Outbox Service with Concurrency Protection."""

    @staticmethod
    async def queue_notification(
        db: AsyncSession,
        organization_id: uuid.UUID,
        event_type: str,
        source_type: str,
        source_id: uuid.UUID,
        idempotency_key: str,
        recipient_email: str,
        recipient_name: Optional[str],
        subject: str,
        payload: Dict[str, Any],
        max_attempts: int = 5,
    ) -> Optional[NotificationJob]:
        """Enqueue a notification inside the caller's transaction (Transactional Outbox).
        
        Guarantees atomicity with business operations. If transaction rolls back, job is never queued.
        """
        # Idempotency check
        existing_stmt = select(NotificationJob).where(
            NotificationJob.organization_id == organization_id,
            NotificationJob.idempotency_key == idempotency_key,
        )
        existing = (await db.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            logger.info(
                f"Notification job already exists for idempotency key '{idempotency_key}'. Skipping duplicate."
            )
            return existing

        job = NotificationJob(
            organization_id=organization_id,
            event_type=event_type,
            source_type=source_type,
            source_id=source_id,
            idempotency_key=idempotency_key,
            recipient_email=recipient_email,
            recipient_name=recipient_name,
            subject=subject,
            payload_json=payload,
            status="pending",
            attempts_count=0,
            max_attempts=max_attempts,
            next_retry_at=datetime.utcnow(),
        )
        db.add(job)
        await db.flush()
        return job

    @staticmethod
    async def process_pending_jobs(
        batch_size: int = settings.NOTIFICATION_WORKER_BATCH_SIZE,
        worker_id: Optional[str] = None,
        organization_id: Optional[uuid.UUID] = None,
    ) -> ProcessBatchResponse:
        """Atomic queue worker using PostgreSQL 'FOR UPDATE SKIP LOCKED'."""
        worker_name = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        now = datetime.utcnow()
        stale_cutoff = now - timedelta(seconds=settings.NOTIFICATION_WORKER_LOCK_TIMEOUT_SECONDS)

        claimed_jobs: List[NotificationJob] = []

        # Step 1: Claim jobs atomically
        async with AsyncSessionLocal() as session:
            if organization_id:
                # Scoped to specific organization (e.g. triggered via tenant API)
                await set_tenant_context(session, organization_id=organization_id)
                claim_stmt = (
                    select(NotificationJob)
                    .where(
                        NotificationJob.organization_id == organization_id,
                        or_(
                            and_(
                                NotificationJob.status == "pending",
                                NotificationJob.next_retry_at <= now,
                            ),
                            and_(
                                NotificationJob.status == "processing",
                                NotificationJob.locked_at < stale_cutoff,
                            ),
                        ),
                    )
                    .order_by(NotificationJob.next_retry_at.asc())
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
                result = await session.execute(claim_stmt)
                jobs_to_lock = result.scalars().all()
                for job in jobs_to_lock:
                    job.status = "processing"
                    job.locked_at = now
                    job.locked_by = worker_name
                    claimed_jobs.append(job)
                await session.commit()
            else:
                # Cross-tenant background worker: use atomic SECURITY DEFINER claim function
                claim_sql = text("""
                    SELECT * FROM app.claim_notification_jobs(:batch_size, :worker_name, :stale_seconds)
                """)
                res = await session.execute(
                    claim_sql,
                    {
                        "batch_size": batch_size,
                        "worker_name": worker_name,
                        "stale_seconds": settings.NOTIFICATION_WORKER_LOCK_TIMEOUT_SECONDS,
                    },
                )
                rows = res.mappings().all()
                for row in rows:
                    job = NotificationJob(
                        organization_id=row["organization_id"],
                        id=row["id"],
                        event_type=row["event_type"],
                        source_type=row["source_type"],
                        source_id=row["source_id"],
                        idempotency_key=row["idempotency_key"],
                        recipient_email=row["recipient_email"],
                        recipient_name=row["recipient_name"],
                        subject=row["subject"],
                        payload_json=row["payload_json"],
                        status=row["status"],
                        attempts_count=row["attempts_count"],
                        max_attempts=row["max_attempts"],
                        next_retry_at=row["next_retry_at"],
                        locked_at=row["locked_at"],
                        locked_by=row["locked_by"],
                        sent_at=row["sent_at"],
                        failed_at=row["failed_at"],
                        error_message=row["error_message"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                    claimed_jobs.append(job)
                await session.commit()

        if not claimed_jobs:
            return ProcessBatchResponse(claimed=0, succeeded=0, failed=0, details=[])

        succeeded_count = 0
        failed_count = 0
        details: List[str] = []

        # Step 2: Process each claimed job with its respective organization context
        email_provider = get_email_provider()

        for job in claimed_jobs:
            async with AsyncSessionLocal() as job_session:
                # Strictly establish transaction-local tenant context
                await set_tenant_context(
                    job_session, organization_id=job.organization_id
                )

                # Re-fetch locked job row
                job_stmt = select(NotificationJob).where(
                    NotificationJob.organization_id == job.organization_id,
                    NotificationJob.id == job.id,
                )
                active_job = (await job_session.execute(job_stmt)).scalar_one()

                try:
                    # Prepare email payload & attachments
                    subject, body_html, body_text, attachments = await NotificationService._render_job_email(
                        job_session, active_job
                    )

                    # Dispatch email
                    res = await email_provider.send_email(
                        to_email=active_job.recipient_email,
                        subject=subject,
                        body_html=body_html,
                        body_text=body_text,
                        attachments=attachments,
                    )

                    # Record Attempt
                    attempt = NotificationAttempt(
                        organization_id=active_job.organization_id,
                        job_id=active_job.id,
                        attempt_number=active_job.attempts_count + 1,
                        status="success" if res.success else "failure",
                        provider_message_id=res.provider_message_id,
                        error_message=res.error_message,
                    )
                    job_session.add(attempt)

                    if res.success:
                        active_job.attempts_count += 1
                        active_job.status = "sent"
                        active_job.sent_at = datetime.utcnow()
                        active_job.error_message = None
                        active_job.locked_at = None
                        active_job.locked_by = None
                        succeeded_count += 1
                        details.append(f"Job {active_job.id} ({active_job.event_type}) sent successfully.")
                    else:
                        active_job.attempts_count += 1
                        sanitized_err = (res.error_message or "Unknown provider error")[:500]
                        active_job.error_message = sanitized_err
                        active_job.locked_at = None
                        active_job.locked_by = None

                        if active_job.attempts_count >= active_job.max_attempts:
                            active_job.status = "failed"
                            active_job.failed_at = datetime.utcnow()
                            failed_count += 1
                            details.append(
                                f"Job {active_job.id} permanently failed after {active_job.attempts_count} attempts: {sanitized_err}"
                            )
                        else:
                            # Bounded exponential backoff: 2^(attempts-1) * 10 seconds (max 1 hour)
                            delay_secs = min(10 * (2 ** (active_job.attempts_count - 1)), 3600)
                            active_job.status = "pending"
                            active_job.next_retry_at = datetime.utcnow() + timedelta(seconds=delay_secs)
                            details.append(
                                f"Job {active_job.id} retry {active_job.attempts_count}/{active_job.max_attempts} scheduled in {delay_secs}s."
                            )

                    await job_session.commit()

                except Exception as ex:
                    logger.exception(f"Unexpected worker exception processing job {active_job.id}: {ex}")
                    active_job.attempts_count += 1
                    active_job.error_message = str(ex)[:500]
                    active_job.locked_at = None
                    active_job.locked_by = None
                    if active_job.attempts_count >= active_job.max_attempts:
                        active_job.status = "failed"
                        active_job.failed_at = datetime.utcnow()
                        failed_count += 1
                    else:
                        active_job.status = "pending"
                        active_job.next_retry_at = datetime.utcnow() + timedelta(seconds=15)
                    await job_session.commit()

        return ProcessBatchResponse(
            claimed=len(claimed_jobs),
            succeeded=succeeded_count,
            failed=failed_count,
            details=details,
        )

    @staticmethod
    async def _render_job_email(
        session: AsyncSession, job: NotificationJob
    ) -> tuple[str, str, str, List[EmailAttachment]]:
        """Render templates and dynamic PDF attachments from persisted job payload."""
        p = job.payload_json
        attachments: List[EmailAttachment] = []

        if job.event_type == "invoice_issued":
            inv_number = p.get("invoice_number", "INV")
            total = Decimal(str(p.get("total_amount", 0)))
            balance = Decimal(str(p.get("balance_due", total)))
            due_date = p.get("due_date", "")

            # Generate PDF
            pdf_bytes = InvoicePDFGenerator.generate_invoice_pdf(
                invoice_number=inv_number,
                invoice_date=date.fromisoformat(p.get("invoice_date", date.today().isoformat())),
                due_date=date.fromisoformat(due_date) if due_date else date.today(),
                customer_name=p.get("customer_name", "Valued Customer"),
                customer_email=job.recipient_email,
                org_name=p.get("organization_name", "Ledgerly Commercial"),
                currency_code=p.get("currency_code", "USD"),
                lines=p.get("lines", []),
                subtotal=Decimal(str(p.get("subtotal", total))),
                tax_total=Decimal(str(p.get("tax_amount", 0))),
                total_amount=total,
                settled_amount=Decimal(str(p.get("settled_amount", 0))),
                balance_due=balance,
                notes=p.get("notes"),
            )
            attachments.append(
                EmailAttachment(
                    filename=f"{inv_number}.pdf",
                    content_bytes=pdf_bytes,
                    content_type="application/pdf",
                )
            )

            subject = f"Invoice {inv_number} from {p.get('organization_name', 'Ledgerly')}"
            body_text = (
                f"Dear {p.get('customer_name', 'Customer')},\n\n"
                f"Please find attached your invoice {inv_number} for ${total:,.2f}.\n"
                f"Due Date: {due_date}\n"
                f"Balance Due: ${balance:,.2f}\n\n"
                f"Thank you for your business.\n"
            )
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; color: #111; line-height: 1.5;">
              <h2>Invoice {inv_number}</h2>
              <p>Dear <strong>{p.get('customer_name', 'Customer')}</strong>,</p>
              <p>Please find attached your invoice <strong>{inv_number}</strong>.</p>
              <table style="border-collapse: collapse; width: 100%; max-width: 400px; margin: 16px 0;">
                <tr><td style="padding: 6px; border-bottom: 1px solid #ddd;">Total Amount:</td><td style="padding: 6px; border-bottom: 1px solid #ddd; text-align: right;"><strong>${total:,.2f}</strong></td></tr>
                <tr><td style="padding: 6px; border-bottom: 1px solid #ddd;">Due Date:</td><td style="padding: 6px; border-bottom: 1px solid #ddd; text-align: right;">{due_date}</td></tr>
                <tr><td style="padding: 6px; border-bottom: 1px solid #ddd;">Balance Due:</td><td style="padding: 6px; border-bottom: 1px solid #ddd; text-align: right; color: #d97706;"><strong>${balance:,.2f}</strong></td></tr>
              </table>
              <p>The formal legal invoice is attached as a PDF document.</p>
              <p>Thank you for your business!</p>
            </body>
            </html>
            """
            return subject, body_html, body_text, attachments

        elif job.event_type == "payment_received":
            ref = p.get("reference", "REC")
            amount = Decimal(str(p.get("amount", 0)))
            pay_date = p.get("payment_date", "")

            subject = f"Payment Receipt: {ref} (${amount:,.2f})"
            allocs_text = "\n".join(
                [f"- Invoice {a.get('invoice_number', 'INV')}: ${Decimal(str(a.get('amount', 0))):,.2f} (Remaining: ${Decimal(str(a.get('balance_due', 0))):,.2f})" for a in p.get("allocations", [])]
            )
            body_text = (
                f"Dear {p.get('customer_name', 'Customer')},\n\n"
                f"We have received your payment of ${amount:,.2f} on {pay_date}.\n"
                f"Reference: {ref}\n\n"
                f"Allocations:\n{allocs_text}\n\n"
                f"Thank you for your prompt payment!\n"
            )
            allocs_html = "".join(
                [f"<li>Invoice <strong>{a.get('invoice_number', 'INV')}</strong>: ${Decimal(str(a.get('amount', 0))):,.2f} (Remaining Due: ${Decimal(str(a.get('balance_due', 0))):,.2f})</li>" for a in p.get("allocations", [])]
            )
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; color: #111;">
              <h2>Payment Confirmation</h2>
              <p>Dear <strong>{p.get('customer_name', 'Customer')}</strong>,</p>
              <p>Thank you! We have received your payment of <strong style="color: #059669;">${amount:,.2f}</strong> on {pay_date}.</p>
              <p><strong>Payment Reference:</strong> {ref}</p>
              <h3>Allocations:</h3>
              <ul>{allocs_html}</ul>
              <p>Your business is greatly appreciated.</p>
            </body>
            </html>
            """
            return subject, body_html, body_text, attachments

        elif job.event_type == "payment_reminder":
            inv_number = p.get("invoice_number", "INV")
            balance = Decimal(str(p.get("balance_due", 0)))
            due_date = p.get("due_date", "")
            stage = p.get("reminder_stage", "")

            subject = f"Payment Reminder: Invoice {inv_number} (${balance:,.2f} Due)"
            body_text = (
                f"Dear {p.get('customer_name', 'Customer')},\n\n"
                f"This is a friendly reminder that invoice {inv_number} has an outstanding balance of ${balance:,.2f}.\n"
                f"Due Date: {due_date}\n\n"
                f"Please remit payment at your earliest convenience.\n"
            )
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; color: #111;">
              <h2>Payment Reminder: {inv_number}</h2>
              <p>Dear <strong>{p.get('customer_name', 'Customer')}</strong>,</p>
              <p>This is a reminder regarding your invoice <strong>{inv_number}</strong> with an outstanding balance of <strong style="color: #d97706;">${balance:,.2f}</strong>.</p>
              <p><strong>Due Date:</strong> {due_date}</p>
              <p>If you have already sent your payment, please disregard this reminder.</p>
            </body>
            </html>
            """
            return subject, body_html, body_text, attachments

        return (
            job.subject,
            f"<p>{job.subject}</p>",
            job.subject,
            attachments,
        )

    @staticmethod
    async def retry_job(
        db: AsyncSession, organization_id: uuid.UUID, job_id: uuid.UUID
    ) -> NotificationJob:
        """Manually retry an eligible failed notification job."""
        stmt = select(NotificationJob).where(
            NotificationJob.organization_id == organization_id,
            NotificationJob.id == job_id,
        )
        job = (await db.execute(stmt)).scalar_one_or_none()
        if not job:
            raise ValueError(f"Notification job {job_id} not found in this organization.")

        job.status = "pending"
        job.next_retry_at = datetime.utcnow()
        job.locked_at = None
        job.locked_by = None
        job.error_message = None
        await db.commit()
        return job

    @staticmethod
    async def get_or_create_reminder_policy(
        db: AsyncSession, organization_id: uuid.UUID
    ) -> ReminderPolicy:
        """Fetch or create default reminder policy for the organization."""
        stmt = select(ReminderPolicy).where(
            ReminderPolicy.organization_id == organization_id
        )
        policy = (await db.execute(stmt)).scalar_one_or_none()
        if not policy:
            policy = ReminderPolicy(
                organization_id=organization_id,
                is_enabled=True,
                days_before_due=3,
                remind_on_due_date=True,
                days_after_due=3,
                overdue_interval_days=7,
                max_overdue_reminders=3,
            )
            db.add(policy)
            await db.flush()
        return policy

    @staticmethod
    async def update_reminder_policy(
        db: AsyncSession, organization_id: uuid.UUID, payload: ReminderPolicyUpdate
    ) -> ReminderPolicy:
        """Update organization reminder policy."""
        policy = await NotificationService.get_or_create_reminder_policy(
            db, organization_id
        )
        policy.is_enabled = payload.is_enabled
        policy.days_before_due = payload.days_before_due
        policy.remind_on_due_date = payload.remind_on_due_date
        policy.days_after_due = payload.days_after_due
        policy.overdue_interval_days = payload.overdue_interval_days
        policy.max_overdue_reminders = payload.max_overdue_reminders
        policy.updated_at = datetime.utcnow()
        await db.flush()
        return policy

    @staticmethod
    async def evaluate_and_queue_reminders(
        organization_id: uuid.UUID,
        db: Optional[AsyncSession] = None,
        as_of: Optional[date] = None,
    ) -> ReminderScanResponse:
        """Scheduled reminder scanner: re-verifies authoritative balances and deduplicates."""
        if db is None:
            async with AsyncSessionLocal() as session:
                await set_tenant_context(session, organization_id=organization_id)
                return await NotificationService.evaluate_and_queue_reminders(
                    organization_id=organization_id, db=session, as_of=as_of
                )

        today = as_of or date.today()
        policy = await NotificationService.get_or_create_reminder_policy(
            db, organization_id
        )

        if not policy.is_enabled:
            return ReminderScanResponse(
                invoices_scanned=0,
                reminders_queued=0,
                details=["Reminder policy is disabled for this organization."],
            )

        # Query authoritative issued, unsettled invoices
        inv_stmt = (
            select(Invoice)
            .where(
                Invoice.organization_id == organization_id,
                Invoice.state == "issued",
                Invoice.total_amount > Invoice.settled_amount,
            )
        )
        invoices = (await db.execute(inv_stmt)).scalars().all()

        reminders_queued = 0
        details: List[str] = []

        for inv in invoices:
            balance_due = inv.total_amount - inv.settled_amount
            if balance_due <= Decimal("0.0000"):
                continue  # Double-check: skip fully paid

            # Calculate days relative to due date: positive = overdue, negative = before due
            days_diff = (today - inv.due_date).days

            matched_stage: Optional[str] = None

            # 1. Upcoming reminder (e.g. 3 days before)
            if days_diff == -policy.days_before_due:
                matched_stage = f"before_due_{policy.days_before_due}"

            # 2. Due date reminder
            elif days_diff == 0 and policy.remind_on_due_date:
                matched_stage = "on_due_date"

            # 3. Grace period overdue reminder (e.g. 3 days after)
            elif days_diff == policy.days_after_due:
                matched_stage = f"after_due_{policy.days_after_due}"

            # 4. Periodic overdue reminders
            elif days_diff > policy.days_after_due:
                overdue_offset = days_diff - policy.days_after_due
                if overdue_offset % policy.overdue_interval_days == 0:
                    overdue_num = (overdue_offset // policy.overdue_interval_days) + 1
                    if overdue_num <= policy.max_overdue_reminders:
                        matched_stage = f"overdue_repeat_{overdue_num}"

            if not matched_stage:
                continue

            # Check if this exact stage was already sent for this invoice
            hist_stmt = select(InvoiceReminderHistory).where(
                InvoiceReminderHistory.organization_id == organization_id,
                InvoiceReminderHistory.invoice_id == inv.id,
                InvoiceReminderHistory.reminder_stage == matched_stage,
            )
            already_sent = (await db.execute(hist_stmt)).scalar_one_or_none()
            if already_sent:
                continue

            # Customer details
            cust_stmt = select(Contact).where(
                Contact.organization_id == organization_id,
                Contact.id == inv.customer_id,
            )
            customer = (await db.execute(cust_stmt)).scalar_one_or_none()
            if not customer or not customer.email:
                details.append(
                    f"Skipping invoice {inv.invoice_number}: customer has no valid email."
                )
                continue

            # Queue reminder notification job
            idempotency_key = f"reminder_{inv.id}_{matched_stage}"
            job_payload = {
                "invoice_id": str(inv.id),
                "invoice_number": inv.invoice_number,
                "customer_name": customer.display_name,
                "due_date": inv.due_date.isoformat(),
                "total_amount": str(inv.total_amount),
                "settled_amount": str(inv.settled_amount),
                "balance_due": str(balance_due),
                "reminder_stage": matched_stage,
            }

            job = await NotificationService.queue_notification(
                db=db,
                organization_id=organization_id,
                event_type="payment_reminder",
                source_type="invoice",
                source_id=inv.id,
                idempotency_key=idempotency_key,
                recipient_email=customer.email,
                recipient_name=customer.display_name,
                subject=f"Reminder: Invoice {inv.invoice_number} is due",
                payload=job_payload,
            )

            # Record history entry to prevent duplicates
            if job:
                history = InvoiceReminderHistory(
                    organization_id=organization_id,
                    invoice_id=inv.id,
                    reminder_stage=matched_stage,
                    notification_job_id=job.id,
                )
                db.add(history)
                reminders_queued += 1
                details.append(
                    f"Queued reminder '{matched_stage}' for {inv.invoice_number} (${balance_due:,.2f} due)."
                )

        await db.commit()

        return ReminderScanResponse(
            invoices_scanned=len(invoices),
            reminders_queued=reminders_queued,
            details=details,
        )
