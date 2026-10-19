"""Milestone 4: Invoices Schema - Sales Invoices, Lines, Snapshotting, and State Immutability.

Revision ID: 0004_invoices_schema
Revises: 0003_ledger_schema
Create Date: 2026-10-19 15:10:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_invoices_schema"
down_revision = "0003_ledger_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. Invoices Header Table
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.invoices (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        state text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft', 'issued', 'void')),
        invoice_number text NULL,
        invoice_date date NOT NULL,
        due_date date NOT NULL,
        currency_code char(3) NOT NULL DEFAULT 'USD',
        notes text NULL,
        terms text NULL,
        net_amount numeric(19, 4) NOT NULL DEFAULT 0 CHECK (net_amount >= 0),
        tax_amount numeric(19, 4) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
        total_amount numeric(19, 4) NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
        settled_amount numeric(19, 4) NOT NULL DEFAULT 0 CHECK (settled_amount >= 0),
        snapshot jsonb NULL,
        issued_at timestamptz NULL,
        issued_by uuid NULL REFERENCES identity.users(id),
        issue_journal_id uuid NULL,
        voided_at timestamptz NULL,
        voided_by uuid NULL REFERENCES identity.users(id),
        void_reason text NULL,
        void_journal_id uuid NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, id),
        CONSTRAINT uq_invoices_customer UNIQUE (organization_id, id, customer_id),
        CONSTRAINT fk_invoices_customer FOREIGN KEY (organization_id, customer_id)
            REFERENCES app.contacts(organization_id, id) ON DELETE RESTRICT,
        CONSTRAINT fk_invoices_issue_journal FOREIGN KEY (organization_id, issue_journal_id)
            REFERENCES app.journals(organization_id, id) ON DELETE RESTRICT,
        CONSTRAINT fk_invoices_void_journal FOREIGN KEY (organization_id, void_journal_id)
            REFERENCES app.journals(organization_id, id) ON DELETE RESTRICT,
        CONSTRAINT ck_invoices_dates CHECK (due_date >= invoice_date),
        CONSTRAINT ck_invoices_total_identity CHECK (total_amount = net_amount + tax_amount),
        CONSTRAINT ck_invoices_settlement_bound CHECK (settled_amount <= total_amount),
        CONSTRAINT ck_invoices_draft_shape CHECK (
            state <> 'draft' OR (
                invoice_number IS NULL AND issue_journal_id IS NULL AND snapshot IS NULL AND settled_amount = 0
            )
        ),
        CONSTRAINT ck_invoices_issued_shape CHECK (
            state = 'draft' OR (
                issued_at IS NOT NULL AND issued_by IS NOT NULL AND issue_journal_id IS NOT NULL AND
                invoice_number IS NOT NULL AND snapshot IS NOT NULL
            )
        ),
        CONSTRAINT ck_invoices_void_shape CHECK (
            state <> 'void' OR (
                voided_at IS NOT NULL AND voided_by IS NOT NULL AND void_journal_id IS NOT NULL AND
                void_reason IS NOT NULL AND settled_amount = 0
            )
        )
    )
    """)

    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS uq_invoices_number 
    ON app.invoices (organization_id, invoice_number) 
    WHERE invoice_number IS NOT NULL
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_invoices_open 
    ON app.invoices (organization_id, customer_id, due_date, id) 
    WHERE state = 'issued' AND total_amount > settled_amount
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_invoices_list 
    ON app.invoices (organization_id, state, invoice_date DESC, id DESC)
    """)

    # -----------------------------------------------------------------------
    # 2. Invoice Lines Table
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.invoice_lines (
        organization_id uuid NOT NULL,
        invoice_id uuid NOT NULL,
        line_no smallint NOT NULL CHECK (line_no > 0),
        item_id uuid NULL,
        description text NOT NULL,
        quantity numeric(19, 4) NOT NULL CHECK (quantity > 0),
        unit_price numeric(19, 4) NOT NULL CHECK (unit_price >= 0),
        line_subtotal numeric(19, 4) NOT NULL CHECK (line_subtotal >= 0),
        tax_rate_id uuid NULL,
        tax_amount numeric(19, 4) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
        line_total numeric(19, 4) NOT NULL CHECK (line_total >= 0),
        income_account_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, invoice_id, line_no),
        CONSTRAINT fk_lines_invoice FOREIGN KEY (organization_id, invoice_id)
            REFERENCES app.invoices(organization_id, id) ON DELETE CASCADE,
        CONSTRAINT fk_lines_item FOREIGN KEY (organization_id, item_id)
            REFERENCES app.items(organization_id, id) ON DELETE RESTRICT,
        CONSTRAINT fk_lines_income_account FOREIGN KEY (organization_id, income_account_id)
            REFERENCES app.accounts(organization_id, id) ON DELETE RESTRICT,
        CONSTRAINT fk_lines_tax_rate FOREIGN KEY (organization_id, tax_rate_id)
            REFERENCES app.tax_rates(organization_id, id) ON DELETE RESTRICT,
        CONSTRAINT ck_invoice_lines_total_identity CHECK (line_total = line_subtotal + tax_amount)
    )
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_invoice_lines_item 
    ON app.invoice_lines (organization_id, item_id)
    """)

    # -----------------------------------------------------------------------
    # 3. Immutability Triggers for Invoices and Lines
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION app.enforce_invoice_line_immutability()
    RETURNS TRIGGER AS $$
    DECLARE
        v_invoice_state text;
    BEGIN
        SELECT state INTO v_invoice_state
        FROM app.invoices
        WHERE organization_id = COALESCE(NEW.organization_id, OLD.organization_id)
          AND id = COALESCE(NEW.invoice_id, OLD.invoice_id);

        IF v_invoice_state IS NOT NULL AND v_invoice_state <> 'draft' THEN
            RAISE EXCEPTION 'Cannot modify or delete lines for an invoice that is already %', v_invoice_state;
        END IF;

        RETURN COALESCE(NEW, OLD);
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER tg_invoice_lines__immutable
        BEFORE INSERT OR UPDATE OR DELETE ON app.invoice_lines
        FOR EACH ROW
        EXECUTE FUNCTION app.enforce_invoice_line_immutability()
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION app.enforce_invoice_header_immutability()
    RETURNS TRIGGER AS $$
    BEGIN
        -- Draft invoices can be updated or deleted freely
        IF OLD.state = 'draft' THEN
            RETURN NEW;
        END IF;

        -- Once issued, only allow transition to void OR settlement updates
        IF OLD.state = 'issued' THEN
            IF NEW.state = 'void' THEN
                -- Valid transition: only void fields may be set
                IF OLD.settled_amount > 0 THEN
                    RAISE EXCEPTION 'Cannot void an invoice that has active payment settlements (settled: %)', OLD.settled_amount;
                END IF;
                RETURN NEW;
            END IF;

            -- If staying issued, only settled_amount or updated_at may change
            IF NEW.state = 'issued' THEN
                IF NEW.customer_id <> OLD.customer_id OR
                   NEW.invoice_number <> OLD.invoice_number OR
                   NEW.invoice_date <> OLD.invoice_date OR
                   NEW.due_date <> OLD.due_date OR
                   NEW.total_amount <> OLD.total_amount OR
                   NEW.net_amount <> OLD.net_amount OR
                   NEW.tax_amount <> OLD.tax_amount OR
                   NEW.issue_journal_id <> OLD.issue_journal_id OR
                   NEW.snapshot <> OLD.snapshot THEN
                    RAISE EXCEPTION 'Issued invoice fields are strictly immutable';
                END IF;
                RETURN NEW;
            END IF;

            RAISE EXCEPTION 'Invalid invoice state transition from % to %', OLD.state, NEW.state;
        END IF;

        -- Void invoices can never be modified
        IF OLD.state = 'void' THEN
            RAISE EXCEPTION 'Void invoices are permanently archived and cannot be modified';
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER tg_invoices__immutable_after_issue
        BEFORE UPDATE ON app.invoices
        FOR EACH ROW
        EXECUTE FUNCTION app.enforce_invoice_header_immutability()
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION app.enforce_invoice_no_delete()
    RETURNS TRIGGER AS $$
    BEGIN
        IF OLD.state <> 'draft' THEN
            RAISE EXCEPTION 'Cannot delete an invoice that is already %; use void instead', OLD.state;
        END IF;
        RETURN OLD;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER tg_invoices__no_delete_unless_draft
        BEFORE DELETE ON app.invoices
        FOR EACH ROW
        EXECUTE FUNCTION app.enforce_invoice_no_delete()
    """)

    # -----------------------------------------------------------------------
    # 4. Row-Level Security Enablement and Forced Enforcement
    # -----------------------------------------------------------------------
    for table in ["app.invoices", "app.invoice_lines"]:
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
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app.invoices TO ledgerly_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app.invoice_lines TO ledgerly_app")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tg_invoices__no_delete_unless_draft ON app.invoices")
    op.execute("DROP TRIGGER IF EXISTS tg_invoices__immutable_after_issue ON app.invoices")
    op.execute("DROP TRIGGER IF EXISTS tg_invoice_lines__immutable ON app.invoice_lines")

    op.execute("DROP FUNCTION IF EXISTS app.enforce_invoice_no_delete()")
    op.execute("DROP FUNCTION IF EXISTS app.enforce_invoice_header_immutability()")
    op.execute("DROP FUNCTION IF EXISTS app.enforce_invoice_line_immutability()")

    op.execute("DROP TABLE IF EXISTS app.invoice_lines CASCADE")
    op.execute("DROP TABLE IF EXISTS app.invoices CASCADE")
