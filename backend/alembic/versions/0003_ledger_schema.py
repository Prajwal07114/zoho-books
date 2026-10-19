"""Milestone 3: Ledger Schema - Journals, Journal Lines, Immutability, and Balance Verification.

Revision ID: 0003_ledger_schema
Revises: 0002_master_data
Create Date: 2026-10-19 15:05:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_ledger_schema"
down_revision = "0002_master_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. Journals Table (Immutable Header)
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.journals (
        organization_id uuid NOT NULL REFERENCES app.organizations(id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        journal_number text NULL,
        accounting_date date NOT NULL,
        memo text NULL,
        source_type text NOT NULL CHECK (source_type IN (
            'manual', 'invoice', 'invoice_void', 'credit_note', 'credit_note_void',
            'payment', 'payment_reversal', 'bill', 'bill_void', 'bill_payment',
            'bill_payment_reversal', 'expense', 'expense_reversal', 'opening_balance',
            'journal_reversal'
        )),
        source_id uuid NULL,
        currency_code char(3) NOT NULL DEFAULT 'USD',
        total_amount numeric(19, 4) NOT NULL CHECK (total_amount > 0),
        reverses_journal_id uuid NULL,
        reason text NULL,
        created_by uuid NOT NULL REFERENCES identity.users(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        created_txid bigint NOT NULL DEFAULT txid_current(),
        correlation_id uuid NULL,
        PRIMARY KEY (organization_id, id),
        CONSTRAINT uq_journals_date UNIQUE (organization_id, id, accounting_date),
        CONSTRAINT fk_journals_reversal FOREIGN KEY (organization_id, reverses_journal_id)
            REFERENCES app.journals(organization_id, id) ON DELETE RESTRICT,
        CONSTRAINT ck_journals_reversal_shape CHECK (
            (reverses_journal_id IS NULL) OR (reverses_journal_id <> id AND reason IS NOT NULL)
        )
    )
    """)

    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS uq_journals_single_reversal 
    ON app.journals (organization_id, reverses_journal_id) 
    WHERE reverses_journal_id IS NOT NULL
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_journals_source 
    ON app.journals (organization_id, source_type, source_id) 
    WHERE source_id IS NOT NULL
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_journals_date 
    ON app.journals (organization_id, accounting_date, id)
    """)

    # -----------------------------------------------------------------------
    # 2. Journal Lines Table (Immutable Entries)
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS app.journal_lines (
        organization_id uuid NOT NULL,
        journal_id uuid NOT NULL,
        line_no smallint NOT NULL CHECK (line_no > 0),
        account_id uuid NOT NULL,
        debit numeric(19, 4) NOT NULL DEFAULT 0 CHECK (debit >= 0),
        credit numeric(19, 4) NOT NULL DEFAULT 0 CHECK (credit >= 0),
        accounting_date date NOT NULL,
        contact_id uuid NULL,
        tax_rate_id uuid NULL,
        description text NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id, journal_id, line_no),
        CONSTRAINT fk_lines_journal FOREIGN KEY (organization_id, journal_id, accounting_date)
            REFERENCES app.journals(organization_id, id, accounting_date) ON DELETE RESTRICT,
        CONSTRAINT fk_lines_account FOREIGN KEY (organization_id, account_id)
            REFERENCES app.accounts(organization_id, id) ON DELETE RESTRICT,
        CONSTRAINT fk_lines_contact FOREIGN KEY (organization_id, contact_id)
            REFERENCES app.contacts(organization_id, id) ON DELETE RESTRICT,
        CONSTRAINT ck_journal_lines_debit_or_credit CHECK (
            (debit = 0 AND credit > 0) OR (credit = 0 AND debit > 0)
        )
    )
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_journal_lines_report 
    ON app.journal_lines (organization_id, account_id, accounting_date, journal_id, line_no)
    """)

    # -----------------------------------------------------------------------
    # 3. Double-Entry Balance Checking Function & Deferred Constraint Trigger
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION app.check_journal_balance()
    RETURNS TRIGGER AS $$
    DECLARE
        v_count int;
        v_sum_debit numeric(19, 4);
        v_sum_credit numeric(19, 4);
    BEGIN
        SELECT COUNT(*), COALESCE(SUM(debit), 0), COALESCE(SUM(credit), 0)
        INTO v_count, v_sum_debit, v_sum_credit
        FROM app.journal_lines
        WHERE organization_id = NEW.organization_id AND journal_id = NEW.id;

        IF v_count < 2 THEN
            RAISE EXCEPTION 'Journal must contain at least 2 lines (found %)', v_count;
        END IF;

        IF v_sum_debit <> v_sum_credit THEN
            RAISE EXCEPTION 'Journal entry does not balance: total debit (%) != total credit (%)', v_sum_debit, v_sum_credit;
        END IF;

        IF v_sum_debit <> NEW.total_amount THEN
            RAISE EXCEPTION 'Journal lines total (%) does not match header total_amount (%)', v_sum_debit, NEW.total_amount;
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE CONSTRAINT TRIGGER tg_journals__balanced
        AFTER INSERT OR UPDATE ON app.journals
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION app.check_journal_balance()
    """)

    # -----------------------------------------------------------------------
    # 4. Period Lock Enforcement Function & Trigger
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION app.enforce_period_lock()
    RETURNS TRIGGER AS $$
    DECLARE
        v_lock_date date;
    BEGIN
        SELECT lock_date INTO v_lock_date
        FROM app.organization_settings
        WHERE organization_id = NEW.organization_id
        FOR SHARE;

        IF v_lock_date IS NOT NULL AND NEW.accounting_date <= v_lock_date THEN
            RAISE EXCEPTION 'Cannot post into a closed accounting period: accounting_date (%) <= lock_date (%)', NEW.accounting_date, v_lock_date;
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER tg_journals__period_lock
        BEFORE INSERT ON app.journals
        FOR EACH ROW
        EXECUTE FUNCTION app.enforce_period_lock()
    """)

    # -----------------------------------------------------------------------
    # 5. Immutability Function & Triggers (No UPDATE / DELETE on posted records)
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION app.enforce_journal_immutability()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'Posted journal entries and journal lines are strictly immutable and cannot be updated or deleted';
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER tg_journals__immutable
        BEFORE UPDATE OR DELETE ON app.journals
        FOR EACH ROW
        EXECUTE FUNCTION app.enforce_journal_immutability()
    """)

    op.execute("""
    CREATE TRIGGER tg_journal_lines__immutable
        BEFORE UPDATE OR DELETE ON app.journal_lines
        FOR EACH ROW
        EXECUTE FUNCTION app.enforce_journal_immutability()
    """)

    # -----------------------------------------------------------------------
    # 6. Same-Transaction Invariant on Journal Lines
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION app.enforce_same_transaction_lines()
    RETURNS TRIGGER AS $$
    DECLARE
        v_created_txid bigint;
    BEGIN
        SELECT created_txid INTO v_created_txid
        FROM app.journals
        WHERE organization_id = NEW.organization_id AND id = NEW.journal_id;

        IF v_created_txid IS NULL OR v_created_txid <> txid_current() THEN
            RAISE EXCEPTION 'Cannot append lines to an already-committed journal (same-transaction invariant violated)';
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER tg_journal_lines__same_transaction
        BEFORE INSERT ON app.journal_lines
        FOR EACH ROW
        EXECUTE FUNCTION app.enforce_same_transaction_lines()
    """)

    # -----------------------------------------------------------------------
    # 7. Reversal Validation Trigger (Cannot reverse a reversal)
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION app.check_reversal_invariants()
    RETURNS TRIGGER AS $$
    DECLARE
        v_target_reversal uuid;
    BEGIN
        IF NEW.reverses_journal_id IS NOT NULL THEN
            SELECT reverses_journal_id INTO v_target_reversal
            FROM app.journals
            WHERE organization_id = NEW.organization_id AND id = NEW.reverses_journal_id;

            IF v_target_reversal IS NOT NULL THEN
                RAISE EXCEPTION 'Cannot reverse a reversal journal entry';
            END IF;
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER tg_journals__reversal_invariants
        BEFORE INSERT ON app.journals
        FOR EACH ROW
        EXECUTE FUNCTION app.check_reversal_invariants()
    """)

    # -----------------------------------------------------------------------
    # 8. Row-Level Security Enablement and Forced Enforcement
    # -----------------------------------------------------------------------
    for table in ["app.journals", "app.journal_lines"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
        CREATE POLICY {table.replace('.', '_')}_tenant_policy ON {table}
            AS PERMISSIVE FOR ALL TO ledgerly_app
            USING (organization_id = app.current_org_id())
            WITH CHECK (organization_id = app.current_org_id())
        """)

    # -----------------------------------------------------------------------
    # 9. Role Grants & Explicit Revocations
    # -----------------------------------------------------------------------
    op.execute("GRANT SELECT, INSERT ON app.journals TO ledgerly_app")
    op.execute("GRANT SELECT, INSERT ON app.journal_lines TO ledgerly_app")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON app.journals FROM ledgerly_app")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON app.journal_lines FROM ledgerly_app")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tg_journals__reversal_invariants ON app.journals")
    op.execute("DROP TRIGGER IF EXISTS tg_journal_lines__same_transaction ON app.journal_lines")
    op.execute("DROP TRIGGER IF EXISTS tg_journal_lines__immutable ON app.journal_lines")
    op.execute("DROP TRIGGER IF EXISTS tg_journals__immutable ON app.journals")
    op.execute("DROP TRIGGER IF EXISTS tg_journals__period_lock ON app.journals")
    op.execute("DROP TRIGGER IF EXISTS tg_journals__balanced ON app.journals")

    op.execute("DROP FUNCTION IF EXISTS app.check_reversal_invariants()")
    op.execute("DROP FUNCTION IF EXISTS app.enforce_same_transaction_lines()")
    op.execute("DROP FUNCTION IF EXISTS app.enforce_journal_immutability()")
    op.execute("DROP FUNCTION IF EXISTS app.enforce_period_lock()")
    op.execute("DROP FUNCTION IF EXISTS app.check_journal_balance()")

    op.execute("DROP TABLE IF EXISTS app.journal_lines CASCADE")
    op.execute("DROP TABLE IF EXISTS app.journals CASCADE")
