"""Add SECURITY DEFINER claim_notification_jobs function for cross-tenant background worker.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-19 16:15:00
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0007_claim_notification_jobs'
down_revision: Union[str, None] = '0006_notifications_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Atomic, transaction-safe claim function for background workers
    # SECURITY DEFINER allows claiming jobs without needing an arbitrary superuser or bypassrls role
    op.execute("""
    CREATE OR REPLACE FUNCTION app.claim_notification_jobs(
        p_batch_size int,
        p_worker_name varchar,
        p_stale_seconds int
    )
    RETURNS SETOF app.notification_jobs
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = app, pg_catalog
    AS $$
    BEGIN
        RETURN QUERY
        WITH claimable AS (
            SELECT organization_id, id
            FROM app.notification_jobs
            WHERE (status = 'pending' AND next_retry_at <= now())
               OR (status = 'processing' AND locked_at < now() - (p_stale_seconds || ' seconds')::interval)
            ORDER BY next_retry_at ASC
            LIMIT p_batch_size
            FOR UPDATE SKIP LOCKED
        )
        UPDATE app.notification_jobs j
        SET status = 'processing',
            locked_at = now(),
            locked_by = p_worker_name
        FROM claimable c
        WHERE j.organization_id = c.organization_id AND j.id = c.id
        RETURNING j.*;
    END;
    $$;
    """)

    op.execute("""
    DO $$ 
    BEGIN 
        IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ledgerly_app') THEN
            GRANT EXECUTE ON FUNCTION app.claim_notification_jobs(int, varchar, int) TO ledgerly_app;
        END IF; 
    END $$;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app.claim_notification_jobs(int, varchar, int);")
