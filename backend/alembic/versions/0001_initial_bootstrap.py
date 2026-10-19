"""Initial bootstrap: schemas and tenant context helper functions.

Revision ID: 0001_initial_bootstrap
Revises: 
Create Date: 2026-10-19 13:25:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0001_initial_bootstrap'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create Core Schemas
    op.execute("CREATE SCHEMA IF NOT EXISTS identity;")
    op.execute("CREATE SCHEMA IF NOT EXISTS app;")
    op.execute("CREATE SCHEMA IF NOT EXISTS audit;")
    op.execute("CREATE SCHEMA IF NOT EXISTS platform;")

    # 2. Create Tenant Context Helper Functions (SECURITY INVOKER)
    op.execute("""
    CREATE OR REPLACE FUNCTION app.current_org_id() 
    RETURNS uuid 
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ 
        SELECT nullif(current_setting('app.current_org_id', true), '')::uuid 
    $$;
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION app.current_user_id() 
    RETURNS uuid 
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ 
        SELECT nullif(current_setting('app.current_user_id', true), '')::uuid 
    $$;
    """)

    # 3. Grant schema usage to runtime role if it exists
    op.execute("""
    DO $$ 
    BEGIN 
        IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ledgerly_app') THEN
            GRANT USAGE ON SCHEMA app TO ledgerly_app;
            GRANT USAGE ON SCHEMA identity TO ledgerly_app;
            GRANT USAGE ON SCHEMA audit TO ledgerly_app;
            GRANT USAGE ON SCHEMA platform TO ledgerly_app;
            GRANT EXECUTE ON FUNCTION app.current_org_id() TO ledgerly_app;
            GRANT EXECUTE ON FUNCTION app.current_user_id() TO ledgerly_app;
        END IF; 
    END $$;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app.current_user_id();")
    op.execute("DROP FUNCTION IF EXISTS app.current_org_id();")
    op.execute("DROP SCHEMA IF EXISTS platform CASCADE;")
    op.execute("DROP SCHEMA IF EXISTS audit CASCADE;")
    op.execute("DROP SCHEMA IF EXISTS app CASCADE;")
    op.execute("DROP SCHEMA IF EXISTS identity CASCADE;")
