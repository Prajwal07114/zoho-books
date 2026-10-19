"""Milestone 5: Payments Schema - Payments, Allocations, Settlement Rollups, and Reversals.

Revision ID: 0005_payments_schema
Revises: 0004_invoices_schema
Create Date: 2026-10-19 15:15:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005_payments_schema"
down_revision = "0004_invoices_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. Payments Table
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.payments (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        payment_number text NULL,
        payment_date date NOT NULL,
        amount numeric(19, 4) NOT NULL CHECK (amount > 0),
        currency_code char(3) NOT NULL DEFAULT 'USD',
        method text NOT NULL DEFAULT 'bank_transfer',
        deposit_account_id uuid NOT NULL,
        reference text NULL,
        notes text NULL,
        state text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'reversed')),
        allocated_amount numeric(19, 4) NOT NULL DEFAULT 0 CHECK (allocated_amount >= 0 AND allocated_amount <= amount),
        journal_id uuid NOT NULL,
        reversal_journal_id uuid NULL,
        reversed_at timestamptz NULL,
        reversed_by uuid NULL REFERENCES identity.users(id),
        reversal_reason text NULL,
        created_by uuid NOT NULL REFERENCES identity.users(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, id),
        CONSTRAINT uq_payments_customer UNIQUE (organization_id, id, customer_id),
        CONSTRAINT fk_payments_customer FOREIGN KEY (organization_id, customer_id)
            REFERENCES app.contacts(organization_id, id) ON DELETE RESTRICT,
        CONSTRAINT fk_payments_deposit_account FOREIGN KEY (organization_id, deposit_account_id)
            REFERENCES app.accounts(organization_id, id) ON DELETE RESTRICT,
        CONSTRAINT fk_payments_journal FOREIGN KEY (organization_id, journal_id)
            REFERENCES app.journals(organization_id, id) ON DELETE RESTRICT,
        CONSTRAINT fk_payments_reversal_journal FOREIGN KEY (organization_id, reversal_journal_id)
            REFERENCES app.journals(organization_id, id) ON DELETE RESTRICT,
        CONSTRAINT ck_payments_reversal_shape CHECK (
            (state = 'active' AND reversal_journal_id IS NULL AND reversed_at IS NULL) OR
            (state = 'reversed' AND reversal_journal_id IS NOT NULL AND reversed_at IS NOT NULL AND reversal_reason IS NOT NULL AND allocated_amount = 0)
        )
    )
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_payments_customer 
    ON app.payments (organization_id, customer_id, payment_date DESC, id DESC)
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_payments_journal 
    ON app.payments (organization_id, journal_id)
    """)

    # -----------------------------------------------------------------------
    # 2. Payment Allocations Table
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.payment_allocations (
        organization_id uuid NOT NULL,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        payment_id uuid NOT NULL,
        invoice_id uuid NOT NULL,
        customer_id uuid NOT NULL,
        amount numeric(19, 4) NOT NULL CHECK (amount > 0),
        status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'reversed')),
        allocated_at timestamptz NOT NULL DEFAULT now(),
        reversed_at timestamptz NULL,
        reversed_by uuid NULL REFERENCES identity.users(id),
        PRIMARY KEY (organization_id, id),
        CONSTRAINT fk_alloc_payment FOREIGN KEY (organization_id, payment_id, customer_id)
            REFERENCES app.payments(organization_id, id, customer_id) ON DELETE RESTRICT,
        CONSTRAINT fk_alloc_invoice FOREIGN KEY (organization_id, invoice_id, customer_id)
            REFERENCES app.invoices(organization_id, id, customer_id) ON DELETE RESTRICT,
        CONSTRAINT ck_allocations_reversal CHECK (
            (status = 'active' AND reversed_at IS NULL) OR
            (status = 'reversed' AND reversed_at IS NOT NULL)
        )
    )
    """)

    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS uq_allocations_active_pair 
    ON app.payment_allocations (organization_id, payment_id, invoice_id) 
    WHERE status = 'active'
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_allocations_invoice 
    ON app.payment_allocations (organization_id, invoice_id) 
    WHERE status = 'active'
    """)

    # -----------------------------------------------------------------------
    # 3. Rollup Triggers: Automatically adjust invoice and payment settled rollups
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION app.apply_payment_allocation_rollup()
    RETURNS TRIGGER AS $$
    DECLARE
        v_rows_updated int;
    BEGIN
        -- Handle New Active Allocation
        IF TG_OP = 'INSERT' AND NEW.status = 'active' THEN
            -- Update Invoice settled_amount
            UPDATE app.invoices
            SET settled_amount = settled_amount + NEW.amount
            WHERE organization_id = NEW.organization_id
              AND id = NEW.invoice_id
              AND state = 'issued';

            GET DIAGNOSTICS v_rows_updated = ROW_COUNT;
            IF v_rows_updated = 0 THEN
                RAISE EXCEPTION 'Cannot allocate payment to invoice %: invoice not found or not in issued state', NEW.invoice_id;
            END IF;

            -- Update Payment allocated_amount
            UPDATE app.payments
            SET allocated_amount = allocated_amount + NEW.amount
            WHERE organization_id = NEW.organization_id
              AND id = NEW.payment_id
              AND state = 'active';

            RETURN NEW;
        END IF;

        -- Handle Reversal of Allocation
        IF TG_OP = 'UPDATE' AND OLD.status = 'active' AND NEW.status = 'reversed' THEN
            -- Restore Invoice settled_amount
            UPDATE app.invoices
            SET settled_amount = settled_amount - OLD.amount
            WHERE organization_id = OLD.organization_id
              AND id = OLD.invoice_id;

            -- Restore Payment allocated_amount
            UPDATE app.payments
            SET allocated_amount = allocated_amount - OLD.amount
            WHERE organization_id = OLD.organization_id
              AND id = OLD.payment_id;

            RETURN NEW;
        END IF;

        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Payment allocation records cannot be deleted; reverse them instead';
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER tg_payment_allocations__apply
        AFTER INSERT OR UPDATE OR DELETE ON app.payment_allocations
        FOR EACH ROW
        EXECUTE FUNCTION app.apply_payment_allocation_rollup()
    """)

    # -----------------------------------------------------------------------
    # 4. Row-Level Security Enablement and Forced Enforcement
    # -----------------------------------------------------------------------
    for table in ["app.payments", "app.payment_allocations"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
        CREATE POLICY {table.replace('.', '_')}_tenant_policy ON {table}
            AS PERMISSIVE FOR ALL TO ledgerly_app
            USING (organization_id = app.current_org_id())
            WITH CHECK (organization_id = app.current_org_id())
        """)

    # -----------------------------------------------------------------------
    # 5. Role Grants
    # -----------------------------------------------------------------------
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app.payments TO ledgerly_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app.payment_allocations TO ledgerly_app")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tg_payment_allocations__apply ON app.payment_allocations")
    op.execute("DROP FUNCTION IF EXISTS app.apply_payment_allocation_rollup()")

    op.execute("DROP TABLE IF EXISTS app.payment_allocations CASCADE")
    op.execute("DROP TABLE IF EXISTS app.payments CASCADE")
