# Ledgerly Phase 5: Notifications & Automation — Implementation Plan
**Author:** Senior Backend & Database Architect  
**Date:** October 19, 2026  
**Status:** **APPROVED FOR IMPLEMENTATION**

---

## 1. Baseline Architecture Verification

The system baseline stands at **Milestone 7** with 33 passing automated tests running on PostgreSQL 16.
Existing foundational layers:
- **Tenancy & Security:** Multi-tenant PostgreSQL Row-Level Security (RLS) under application role `ledgerly_app` (`NOBYPASSRLS`). Context set per transaction via `set_config('app.current_org_id', :org_id, true)`.
- **Accounting Core:** Atomic Posting Gateway enforcing double-entry invariants, period locks, and posted journal immutability (`app.journals`, `app.journal_lines`).
- **Sales & Billing:** Sales invoicing (`app.invoices`, `app.invoice_lines`) with Half-Up rounding, legal numbering (`INV-xxxx`), and atomic JP-01 journal creation.
- **Payments:** Customer receipts (`app.payments`, `app.payment_allocations`) with atomic JP-03 journals and database allocation triggers maintaining settled balances.
- **Authoritative Reporting:** Real-time net-zero Trial Balance and Accounts Receivable aging reconciled 100% to GL Account `1200`.

---

## 2. Phase 5 Scope & Core Invariants

### Primary Objectives
1. **Feature A: Invoice-Issued Email**  
   - Generated post-issuance from immutable legal invoice snapshot.
   - Includes invoice number, issue date, due date, total, outstanding balance, and valid PDF attachment.
   - Enqueued in the same database transaction as invoice issuance (Transactional Outbox); rolls back if issuance fails.
2. **Feature B: Payment Confirmation & Receipt**  
   - Triggered upon payment recording.
   - Includes payment reference, amount received, payment date, method, and allocated invoice references with remaining balances.
   - Deduplicated by payment event idempotency key.
3. **Feature C: Scheduled Payment Reminders**  
   - Policy-driven: `-3` days before due date, `0` days (on due date), `+3` days overdue, and periodic overdue intervals.
   - Strict dynamic re-evaluation: queries authoritative database state immediately prior to queuing. Skips drafts, voided invoices, and fully settled invoices (`total_amount <= settled_amount`).
   - Deduplicated per `(organization_id, invoice_id, reminder_stage)`.
4. **Feature D: Delivery Retries, Concurrency, and Worker Reliability**  
   - PostgreSQL-backed queue using `SELECT ... FOR UPDATE SKIP LOCKED` for atomic job claiming.
   - States: `pending`, `processing`, `sent`, `failed`.
   - Bounded exponential backoff for transient failures (max 5 attempts).
   - Recovery of stale in-flight jobs on worker crash.
   - Tenant-safe worker execution: derives `organization_id` from trusted job row and binds transaction-local tenant context before executing queries under `ledgerly_app`.
5. **Feature E: Pluggable Email Provider**  
   - Interface `EmailProvider` with `DevelopmentEmailProvider` (in-memory/file delivery logs for tests & local demo) and `SMTPEmailProvider` (standard SMTP).
6. **Feature F: Optional n8n Webhook Outbound Event Interface**  
   - HMAC-SHA256 signed outbound webhooks for `invoice.issued`, `payment.recorded`, `invoice.overdue`, `notification.failed`.
7. **Feature G: Interactive Demo SPA Integration**  
   - Notifications dashboard tab in demo UI displaying queue status, delivery attempts, manual retry, and reminder scanner.

### Strict Accounting & Architectural Invariants
- **Side Effect Isolation:** Notification failures must **never** roll back or corrupt an accounting transaction.
- **Outbox Atomicity:** Notifications must **never** be delivered for uncommitted or rolled-back transactions.
- **No Floating-Point Math:** All currency values formatted from `decimal.Decimal`.
- **No Infrastructure Creep:** Zero external queue dependencies (no Celery, Redis, Kafka). All queueing is durable within PostgreSQL.

---

## 3. Database Design & Migration (`0006_notifications_schema.py`)

All new tables reside in schema `app` with `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`.

### Table 1: `app.reminder_policies`
Organization-level reminder configuration:
- `organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE`
- `id uuid NOT NULL DEFAULT gen_random_uuid()`
- `is_enabled boolean NOT NULL DEFAULT true`
- `days_before_due smallint NOT NULL DEFAULT 3`
- `remind_on_due_date boolean NOT NULL DEFAULT true`
- `days_after_due smallint NOT NULL DEFAULT 3`
- `overdue_interval_days smallint NOT NULL DEFAULT 7`
- `max_overdue_reminders smallint NOT NULL DEFAULT 3`
- `created_at timestamptz NOT NULL DEFAULT now()`
- `updated_at timestamptz NOT NULL DEFAULT now()`
- `PRIMARY KEY (organization_id, id)`
- `UNIQUE (organization_id)`

### Table 2: `app.notification_jobs`
Durable transactional outbox jobs:
- `organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE`
- `id uuid NOT NULL DEFAULT gen_random_uuid()`
- `event_type varchar(50) NOT NULL` (`invoice_issued`, `payment_received`, `payment_reminder`)
- `source_type varchar(50) NOT NULL` (`invoice`, `payment`)
- `source_id uuid NOT NULL`
- `idempotency_key varchar(120) NOT NULL`
- `recipient_email varchar(255) NOT NULL`
- `recipient_name varchar(255) NULL`
- `subject varchar(255) NOT NULL`
- `payload_json jsonb NOT NULL`
- `status varchar(30) NOT NULL DEFAULT 'pending'` (`pending`, `processing`, `sent`, `failed`)
- `attempts_count int NOT NULL DEFAULT 0`
- `max_attempts int NOT NULL DEFAULT 5`
- `next_retry_at timestamptz NOT NULL DEFAULT now()`
- `locked_at timestamptz NULL`
- `locked_by varchar(100) NULL`
- `sent_at timestamptz NULL`
- `failed_at timestamptz NULL`
- `error_message text NULL`
- `created_at timestamptz NOT NULL DEFAULT now()`
- `updated_at timestamptz NOT NULL DEFAULT now()`
- `PRIMARY KEY (organization_id, id)`
- `UNIQUE (organization_id, idempotency_key)`
- Index: `ix_notification_jobs_claim ON app.notification_jobs (status, next_retry_at) WHERE status IN ('pending', 'processing')`

### Table 3: `app.notification_attempts`
Delivery audit trail:
- `organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE`
- `id uuid NOT NULL DEFAULT gen_random_uuid()`
- `job_id uuid NOT NULL`
- `attempt_number int NOT NULL`
- `status varchar(20) NOT NULL` (`success`, `failure`)
- `provider_message_id varchar(255) NULL`
- `error_message text NULL`
- `attempted_at timestamptz NOT NULL DEFAULT now()`
- `PRIMARY KEY (organization_id, id)`
- `FOREIGN KEY (organization_id, job_id) REFERENCES app.notification_jobs(organization_id, id) ON DELETE CASCADE`

### Table 4: `app.invoice_reminder_history`
Deduplication tracking for scheduled reminder stages:
- `organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE`
- `id uuid NOT NULL DEFAULT gen_random_uuid()`
- `invoice_id uuid NOT NULL`
- `reminder_stage varchar(50) NOT NULL` (e.g. `before_due_3`, `due_date`, `after_due_3`, `overdue_1`)
- `notification_job_id uuid NOT NULL`
- `sent_at timestamptz NOT NULL DEFAULT now()`
- `PRIMARY KEY (organization_id, id)`
- `UNIQUE (organization_id, invoice_id, reminder_stage)`

---

## 4. Background Job Concurrency & Tenant-Safe Worker Pattern

To prevent duplicate job processing and connection pool leaks:
```sql
-- Atomic Claiming Query
SELECT organization_id, id, event_type, recipient_email, subject, payload_json, attempts_count
FROM app.notification_jobs
WHERE (
    (status = 'pending' AND next_retry_at <= now())
    OR (status = 'processing' AND locked_at < now() - INTERVAL '5 minutes') -- Stale recovery
)
ORDER BY next_retry_at ASC
FOR UPDATE SKIP LOCKED
LIMIT :batch_size;
```

### Safe Execution Model:
1. Worker queries claimable jobs using administrative query or dedicated worker session.
2. For each claimed job:
   - Sets `status = 'processing'`, `locked_at = now()`, `locked_by = worker_id`.
   - Starts a child transaction, establishes `set_tenant_context(session, organization_id=job.organization_id)` under role `ledgerly_app`.
   - Dispatches email via configured `EmailProvider`.
   - On success: updates `status = 'sent'`, `sent_at = now()`, logs attempt.
   - On transient failure: calculates exponential backoff (`now() + INTERVAL (2 ^ attempts) * 10 seconds`), sets `status = 'pending'`, increments `attempts_count`.
   - On final failure (`attempts_count >= max_attempts`): sets `status = 'failed'`, `failed_at = now()`.

---

## 5. API Endpoints

- `GET /api/v1/notifications`: Paginated list of notification jobs for active organization.
- `GET /api/v1/notifications/{id}`: Detailed inspection of job and its delivery attempt history.
- `POST /api/v1/notifications/{id}/retry`: Manually reset a failed job to `pending` with `next_retry_at = now()`.
- `GET /api/v1/notifications/reminder-policy`: Retrieve organization reminder settings.
- `PUT /api/v1/notifications/reminder-policy`: Update reminder schedule and overdue frequencies.
- `POST /api/v1/notifications/process`: Execute an on-demand batch run of the notification worker.
- `POST /api/v1/notifications/run-reminders`: Execute reminder scanner across all active tenant invoices.

---

## 6. Implementation Sequence

1. **Milestone 1:** Baseline verification, documentation inspection, and implementation plan (Completed).
2. **Milestone 2:** Database migration `0006_notifications_schema.py`, SQLAlchemy models, and RLS policies.
3. **Milestone 3:** Email provider interface (`DevelopmentEmailProvider`, `SMTPEmailProvider`) and standalone PDF document renderer.
4. **Milestone 4:** Notification service, worker daemon/batch processor (`FOR UPDATE SKIP LOCKED`), and failure retry engine.
5. **Milestone 5:** Service integration:
   - Invoice issuance hook in `InvoiceService.issue_invoice()` (Feature A).
   - Payment receipt hook in `PaymentService.record_payment()` (Feature B).
   - Scheduled reminder evaluator in `ReminderService.evaluate_reminders()` (Feature C).
6. **Milestone 6:** REST API endpoints (`/api/v1/notifications/*`), router registration, and UI integration tab in `frontend/index.html`.
7. **Milestone 7:** Optional authenticated n8n outbound webhook interface with HMAC-SHA256 signatures (`docs/N8N_INTEGRATION_GUIDE.md`).
8. **Milestone 8:** Comprehensive automated test suite expansion (covering all regression, outbox atomicity, reminder deduplication, retries, and cross-tenant isolation).
