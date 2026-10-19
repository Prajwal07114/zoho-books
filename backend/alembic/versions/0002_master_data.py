"""Master data schema: organizations, memberships, accounts, tax_rates, contacts, items.

Revision ID: 0002_master_data
Revises: 0001_initial_bootstrap
Create Date: 2026-10-19 14:35:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0002_master_data'
down_revision: Union[str, None] = '0001_initial_bootstrap'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Identity Users
    op.execute("""
    CREATE TABLE IF NOT EXISTS identity.users (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        email text NOT NULL UNIQUE,
        full_name text NOT NULL,
        password_hash text NOT NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """)

    # 2. Organizations
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.organizations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        name text NOT NULL,
        legal_name text NULL,
        base_currency_code char(3) NOT NULL DEFAULT 'USD',
        time_zone text NOT NULL DEFAULT 'UTC',
        created_by uuid NOT NULL REFERENCES identity.users(id),
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """)

    # 3. Organization Settings
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.organization_settings (
        organization_id uuid PRIMARY KEY REFERENCES app.organizations(id) ON DELETE CASCADE,
        lock_date date NULL,
        books_start_date date NULL,
        invoice_prefix text NOT NULL DEFAULT 'INV-',
        next_invoice_number bigint NOT NULL DEFAULT 1,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """)

    # 4. Memberships
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.memberships (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        user_id uuid NOT NULL REFERENCES identity.users(id) ON DELETE CASCADE,
        role text NOT NULL DEFAULT 'owner',
        status text NOT NULL DEFAULT 'active',
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, user_id)
    )
    """)

    # 5. Accounts (Chart of Accounts)
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.accounts (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        code text NOT NULL,
        name text NOT NULL,
        account_type text NOT NULL CHECK (account_type IN ('asset', 'liability', 'equity', 'income', 'expense')),
        normal_balance text NOT NULL CHECK (normal_balance IN ('debit', 'credit')),
        is_system boolean NOT NULL DEFAULT false,
        system_key text NULL,
        is_postable boolean NOT NULL DEFAULT true,
        status text NOT NULL DEFAULT 'active',
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, id),
        UNIQUE (organization_id, code)
    )
    """)

    # 6. Tax Rates
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.tax_rates (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        name text NOT NULL,
        rate_percent numeric(9, 6) NOT NULL CHECK (rate_percent >= 0),
        tax_account_id uuid NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, id)
    )
    """)

    # 7. Contacts (Customers and Vendors)
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.contacts (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        display_name text NOT NULL,
        email text NULL,
        phone text NULL,
        is_customer boolean NOT NULL DEFAULT true,
        is_vendor boolean NOT NULL DEFAULT false,
        status text NOT NULL DEFAULT 'active',
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, id)
    )
    """)

    # 8. Items (Products and Services)
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.items (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        name text NOT NULL,
        item_type text NOT NULL DEFAULT 'service' CHECK (item_type IN ('product', 'service')),
        sku text NULL,
        sales_price numeric(19, 4) NOT NULL DEFAULT 0 CHECK (sales_price >= 0),
        income_account_id uuid NULL,
        default_tax_rate_id uuid NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, id)
    )
    """)

    # -----------------------------------------------------------------------
    # Row-Level Security Enablement and Forced Enforcement
    # -----------------------------------------------------------------------
    tables = [
        "app.organizations",
        "app.organization_settings",
        "app.memberships",
        "app.accounts",
        "app.tax_rates",
        "app.contacts",
        "app.items",
    ]
    for table in tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # Standard Tenant Isolation Policy for standard tables
    standard_tables = [
        "app.organization_settings",
        "app.accounts",
        "app.tax_rates",
        "app.contacts",
        "app.items",
    ]
    for table in standard_tables:
        op.execute(f"""
        CREATE POLICY {table.replace('.', '_')}_tenant_policy ON {table}
            AS PERMISSIVE FOR ALL TO ledgerly_app
            USING (organization_id = app.current_org_id())
            WITH CHECK (organization_id = app.current_org_id())
        """)

    # Organizations Policy (Allows listing user's active orgs when current_org_id is unset)
    op.execute("""
    CREATE POLICY organizations_tenant_policy ON app.organizations
        AS PERMISSIVE FOR ALL TO ledgerly_app
        USING (
            id = app.current_org_id() 
            OR (app.current_org_id() IS NULL AND EXISTS (
                SELECT 1 FROM app.memberships m 
                WHERE m.organization_id = organizations.id 
                  AND m.user_id = app.current_user_id() 
                  AND m.status = 'active'
            ))
        )
        WITH CHECK (id = app.current_org_id() OR app.current_org_id() IS NULL)
    """)

    # Memberships Policy
    op.execute("""
    CREATE POLICY memberships_tenant_policy ON app.memberships
        AS PERMISSIVE FOR ALL TO ledgerly_app
        USING (
            organization_id = app.current_org_id() 
            OR (app.current_org_id() IS NULL AND user_id = app.current_user_id())
        )
        WITH CHECK (
            organization_id = app.current_org_id() 
            OR (app.current_org_id() IS NULL AND user_id = app.current_user_id())
        )
    """)

    # Grant permissions to runtime role
    op.execute("""
    DO $$ 
    BEGIN 
        IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ledgerly_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO ledgerly_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA identity TO ledgerly_app;
        END IF; 
    END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.items CASCADE")
    op.execute("DROP TABLE IF EXISTS app.contacts CASCADE")
    op.execute("DROP TABLE IF EXISTS app.tax_rates CASCADE")
    op.execute("DROP TABLE IF EXISTS app.accounts CASCADE")
    op.execute("DROP TABLE IF EXISTS app.memberships CASCADE")
    op.execute("DROP TABLE IF EXISTS app.organization_settings CASCADE")
    op.execute("DROP TABLE IF EXISTS app.organizations CASCADE")
    op.execute("DROP TABLE IF EXISTS identity.users CASCADE")
