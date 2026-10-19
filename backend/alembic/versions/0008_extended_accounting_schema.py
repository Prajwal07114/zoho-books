"""Extended Accounting Schema: Vendors, Bills, Expenses, Supplier Payments, Credit Notes, and Opening Balances.

Revision ID: 0008_extended_accounting_schema
Revises: 0007_claim_notification_jobs
Create Date: 2026-10-19 17:00:00
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0008_extended_accounting_schema'
down_revision: Union[str, None] = '0007_claim_notification_jobs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Extend app.contacts for vendor master data
    op.execute("""
    ALTER TABLE app.contacts 
    ADD COLUMN IF NOT EXISTS billing_address jsonb NULL,
    ADD COLUMN IF NOT EXISTS tax_identifier text NULL,
    ADD COLUMN IF NOT EXISTS payment_terms_days smallint NULL DEFAULT 0;
    """)

    # 2. Extend app.invoices with credit_noted_amount
    op.execute("""
    ALTER TABLE app.invoices 
    ADD COLUMN IF NOT EXISTS credit_noted_amount numeric(19, 4) NOT NULL DEFAULT 0.0000;
    """)

    # 3. Create app.bills (Purchase Bills / Supplier Invoices)
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.bills (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        vendor_id uuid NOT NULL,
        bill_number text NULL,
        vendor_invoice_number text NOT NULL,
        bill_date date NOT NULL,
        due_date date NOT NULL,
        currency_code char(3) NOT NULL DEFAULT 'USD',
        state text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft', 'posted', 'void')),
        net_amount numeric(19, 4) NOT NULL DEFAULT 0.0000,
        tax_amount numeric(19, 4) NOT NULL DEFAULT 0.0000,
        total_amount numeric(19, 4) NOT NULL DEFAULT 0.0000,
        settled_amount numeric(19, 4) NOT NULL DEFAULT 0.0000,
        notes text NULL,
        post_journal_id uuid NULL,
        void_journal_id uuid NULL,
        created_by uuid NOT NULL REFERENCES identity.users(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, id),
        FOREIGN KEY (organization_id, vendor_id) REFERENCES app.contacts(organization_id, id) ON DELETE RESTRICT
    );
    """)

    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS uq_bills_vendor_invoice 
    ON app.bills (organization_id, vendor_id, vendor_invoice_number) 
    WHERE state != 'void';
    """)

    # 4. Create app.bill_lines
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.bill_lines (
        organization_id uuid NOT NULL,
        bill_id uuid NOT NULL,
        line_no smallint NOT NULL,
        item_id uuid NULL,
        description text NOT NULL,
        quantity numeric(19, 4) NOT NULL DEFAULT 1.0000,
        unit_price numeric(19, 4) NOT NULL DEFAULT 0.0000,
        line_subtotal numeric(19, 4) NOT NULL DEFAULT 0.0000,
        tax_rate_id uuid NULL,
        tax_amount numeric(19, 4) NOT NULL DEFAULT 0.0000,
        line_total numeric(19, 4) NOT NULL DEFAULT 0.0000,
        expense_account_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, bill_id, line_no),
        FOREIGN KEY (organization_id, bill_id) REFERENCES app.bills(organization_id, id) ON DELETE CASCADE,
        FOREIGN KEY (organization_id, expense_account_id) REFERENCES app.accounts(organization_id, id) ON DELETE RESTRICT
    );
    """)

    # 5. Create app.expenses (Direct business disbursements)
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.expenses (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        expense_date date NOT NULL,
        expense_account_id uuid NOT NULL,
        payment_account_id uuid NOT NULL,
        amount numeric(19, 4) NOT NULL DEFAULT 0.0000,
        tax_rate_id uuid NULL,
        tax_amount numeric(19, 4) NOT NULL DEFAULT 0.0000,
        vendor_id uuid NULL,
        reference text NULL,
        description text NOT NULL,
        state text NOT NULL DEFAULT 'posted' CHECK (state IN ('posted', 'void')),
        journal_id uuid NOT NULL,
        void_journal_id uuid NULL,
        created_by uuid NOT NULL REFERENCES identity.users(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, id),
        FOREIGN KEY (organization_id, expense_account_id) REFERENCES app.accounts(organization_id, id) ON DELETE RESTRICT,
        FOREIGN KEY (organization_id, payment_account_id) REFERENCES app.accounts(organization_id, id) ON DELETE RESTRICT
    );
    """)

    # 6. Create app.supplier_payments & app.supplier_payment_allocations
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.supplier_payments (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        vendor_id uuid NOT NULL,
        payment_number text NULL,
        payment_date date NOT NULL,
        amount numeric(19, 4) NOT NULL,
        method text NOT NULL DEFAULT 'bank_transfer',
        payment_account_id uuid NOT NULL,
        reference text NULL,
        notes text NULL,
        state text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'reversed')),
        allocated_amount numeric(19, 4) NOT NULL DEFAULT 0.0000,
        journal_id uuid NOT NULL,
        reversal_journal_id uuid NULL,
        reversed_at timestamptz NULL,
        reversal_reason text NULL,
        created_by uuid NOT NULL REFERENCES identity.users(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, id),
        FOREIGN KEY (organization_id, vendor_id) REFERENCES app.contacts(organization_id, id) ON DELETE RESTRICT,
        FOREIGN KEY (organization_id, payment_account_id) REFERENCES app.accounts(organization_id, id) ON DELETE RESTRICT
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS app.supplier_payment_allocations (
        organization_id uuid NOT NULL,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        payment_id uuid NOT NULL,
        bill_id uuid NOT NULL,
        vendor_id uuid NOT NULL,
        amount numeric(19, 4) NOT NULL CHECK (amount > 0),
        status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'reversed')),
        allocated_at timestamptz NOT NULL DEFAULT now(),
        reversed_at timestamptz NULL,
        PRIMARY KEY (organization_id, id),
        FOREIGN KEY (organization_id, payment_id) REFERENCES app.supplier_payments(organization_id, id) ON DELETE CASCADE,
        FOREIGN KEY (organization_id, bill_id) REFERENCES app.bills(organization_id, id) ON DELETE CASCADE
    );
    """)

    # 7. Create app.credit_notes & app.credit_note_lines
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.credit_notes (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        invoice_id uuid NOT NULL,
        credit_note_number text NULL,
        credit_note_date date NOT NULL,
        reason text NOT NULL,
        currency_code char(3) NOT NULL DEFAULT 'USD',
        state text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft', 'issued', 'void')),
        net_amount numeric(19, 4) NOT NULL DEFAULT 0.0000,
        tax_amount numeric(19, 4) NOT NULL DEFAULT 0.0000,
        total_amount numeric(19, 4) NOT NULL DEFAULT 0.0000,
        allocated_amount numeric(19, 4) NOT NULL DEFAULT 0.0000,
        issue_journal_id uuid NULL,
        void_journal_id uuid NULL,
        created_by uuid NOT NULL REFERENCES identity.users(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, id),
        FOREIGN KEY (organization_id, customer_id) REFERENCES app.contacts(organization_id, id) ON DELETE RESTRICT,
        FOREIGN KEY (organization_id, invoice_id) REFERENCES app.invoices(organization_id, id) ON DELETE RESTRICT
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS app.credit_note_lines (
        organization_id uuid NOT NULL,
        credit_note_id uuid NOT NULL,
        line_no smallint NOT NULL,
        item_id uuid NULL,
        description text NOT NULL,
        quantity numeric(19, 4) NOT NULL DEFAULT 1.0000,
        unit_price numeric(19, 4) NOT NULL DEFAULT 0.0000,
        line_subtotal numeric(19, 4) NOT NULL DEFAULT 0.0000,
        tax_rate_id uuid NULL,
        tax_amount numeric(19, 4) NOT NULL DEFAULT 0.0000,
        line_total numeric(19, 4) NOT NULL DEFAULT 0.0000,
        income_account_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, credit_note_id, line_no),
        FOREIGN KEY (organization_id, credit_note_id) REFERENCES app.credit_notes(organization_id, id) ON DELETE CASCADE,
        FOREIGN KEY (organization_id, income_account_id) REFERENCES app.accounts(organization_id, id) ON DELETE RESTRICT
    );
    """)

    # 8. Create app.opening_balance_batches
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.opening_balance_batches (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        conversion_date date NOT NULL,
        status text NOT NULL DEFAULT 'posted' CHECK (status IN ('posted', 'reversed')),
        journal_id uuid NOT NULL,
        reversal_journal_id uuid NULL,
        notes text NULL,
        created_by uuid NOT NULL REFERENCES identity.users(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, id)
    );
    """)

    # 9. Enable and FORCE Row-Level Security on all new tables
    tables = [
        "app.bills",
        "app.bill_lines",
        "app.expenses",
        "app.supplier_payments",
        "app.supplier_payment_allocations",
        "app.credit_notes",
        "app.credit_note_lines",
        "app.opening_balance_batches",
    ]
    for table in tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(f"""
        CREATE POLICY {table.replace('.', '_')}_tenant_policy ON {table}
            AS PERMISSIVE FOR ALL TO ledgerly_app
            USING (organization_id = app.current_org_id())
            WITH CHECK (organization_id = app.current_org_id());
        """)

    # 10. Grant permissions to runtime role
    op.execute("""
    DO $$ 
    BEGIN 
        IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ledgerly_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO ledgerly_app;
        END IF; 
    END $$;
    """)


def downgrade() -> None:
    tables = [
        "app.opening_balance_batches",
        "app.credit_note_lines",
        "app.credit_notes",
        "app.supplier_payment_allocations",
        "app.supplier_payments",
        "app.expenses",
        "app.bill_lines",
        "app.bills",
    ]
    for table in tables:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")

    op.execute("ALTER TABLE app.invoices DROP COLUMN IF EXISTS credit_noted_amount;")
    op.execute("""
    ALTER TABLE app.contacts 
    DROP COLUMN IF EXISTS payment_terms_days,
    DROP COLUMN IF EXISTS tax_identifier,
    DROP COLUMN IF EXISTS billing_address;
    """)
