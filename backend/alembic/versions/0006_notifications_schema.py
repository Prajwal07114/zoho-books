"""Phase 5: Notifications Schema - Jobs, Attempts, Reminder Policies, and History.

Revision ID: 0006_notifications_schema
Revises: 0005_payments_schema
Create Date: 2026-10-19 16:05:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0006_notifications_schema"
down_revision = "0005_payments_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. Reminder Policies Table
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.reminder_policies (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        is_enabled boolean NOT NULL DEFAULT true,
        days_before_due smallint NOT NULL DEFAULT 3,
        remind_on_due_date boolean NOT NULL DEFAULT true,
        days_after_due smallint NOT NULL DEFAULT 3,
        overdue_interval_days smallint NOT NULL DEFAULT 7,
        max_overdue_reminders smallint NOT NULL DEFAULT 3,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, id),
        CONSTRAINT uq_reminder_policies_org UNIQUE (organization_id)
    )
    """)

    # -----------------------------------------------------------------------
    # 2. Notification Jobs Table (Transactional Outbox Queue)
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.notification_jobs (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        event_type varchar(50) NOT NULL,
        source_type varchar(50) NOT NULL,
        source_id uuid NOT NULL,
        idempotency_key varchar(120) NOT NULL,
        recipient_email varchar(255) NOT NULL,
        recipient_name varchar(255) NULL,
        subject varchar(255) NOT NULL,
        payload_json jsonb NOT NULL,
        status varchar(30) NOT NULL DEFAULT 'pending',
        attempts_count int NOT NULL DEFAULT 0,
        max_attempts int NOT NULL DEFAULT 5,
        next_retry_at timestamptz NOT NULL DEFAULT now(),
        locked_at timestamptz NULL,
        locked_by varchar(100) NULL,
        sent_at timestamptz NULL,
        failed_at timestamptz NULL,
        error_message text NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, id),
        CONSTRAINT uq_notification_jobs_idempotency UNIQUE (organization_id, idempotency_key),
        CONSTRAINT ck_notification_jobs_status CHECK (status IN ('pending', 'processing', 'sent', 'failed'))
    )
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_notification_jobs_claim 
    ON app.notification_jobs (status, next_retry_at) 
    WHERE status IN ('pending', 'processing')
    """)

    # -----------------------------------------------------------------------
    # 3. Notification Attempts Table (Audit Trail)
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.notification_attempts (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        job_id uuid NOT NULL,
        attempt_number int NOT NULL,
        status varchar(20) NOT NULL,
        provider_message_id varchar(255) NULL,
        error_message text NULL,
        attempted_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, id),
        CONSTRAINT fk_attempts_job FOREIGN KEY (organization_id, job_id)
            REFERENCES app.notification_jobs(organization_id, id) ON DELETE CASCADE,
        CONSTRAINT ck_notification_attempts_status CHECK (status IN ('success', 'failure'))
    )
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_notification_attempts_job 
    ON app.notification_attempts (organization_id, job_id, attempted_at)
    """)

    # -----------------------------------------------------------------------
    # 4. Invoice Reminder History Table (Deduplication)
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.invoice_reminder_history (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        invoice_id uuid NOT NULL,
        reminder_stage varchar(50) NOT NULL,
        notification_job_id uuid NOT NULL,
        sent_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, id),
        CONSTRAINT uq_reminder_history_stage UNIQUE (organization_id, invoice_id, reminder_stage),
        CONSTRAINT fk_reminder_history_invoice FOREIGN KEY (organization_id, invoice_id)
            REFERENCES app.invoices(organization_id, id) ON DELETE CASCADE
    )
    """)

    # -----------------------------------------------------------------------
    # 5. Row-Level Security Enablement and Forced Enforcement
    # -----------------------------------------------------------------------
    tables = [
        "app.reminder_policies",
        "app.notification_jobs",
        "app.notification_attempts",
        "app.invoice_reminder_history",
    ]

    for table in tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
        CREATE POLICY {table.replace('.', '_')}_tenant_policy ON {table}
            AS PERMISSIVE FOR ALL TO ledgerly_app
            USING (organization_id = app.current_org_id())
            WITH CHECK (organization_id = app.current_org_id())
        """)

    # Grant permissions to runtime role
    op.execute("""
    DO $$ 
    BEGIN 
        IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ledgerly_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO ledgerly_app;
        END IF; 
    END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.invoice_reminder_history CASCADE")
    op.execute("DROP TABLE IF EXISTS app.notification_attempts CASCADE")
    op.execute("DROP TABLE IF EXISTS app.notification_jobs CASCADE")
    op.execute("DROP TABLE IF EXISTS app.reminder_policies CASCADE")
