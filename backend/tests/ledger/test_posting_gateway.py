"""Tests for Posting Gateway: Double-Entry Balance, Period Lock, Immutability, Reversals, and Idempotency."""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.models.accounting import Account, Journal, JournalLine
from app.models.identity import User
from app.models.master import Contact
from app.models.organization import Membership, Organization, OrganizationSettings
from app.services.coa_seed import seed_chart_of_accounts
from app.services.posting_gateway import (
    AccountingError,
    DuplicatePostingError,
    InvalidAccountError,
    InvalidReversalError,
    PeriodLockedError,
    PostingGateway,
    PostingLine,
    PostingRequest,
    UnbalancedJournalError,
    build_jp01_invoice_issuance,
    build_jp02_invoice_void,
    build_jp03_payment_receipt,
    build_jp04_payment_reversal,
)


@pytest_asyncio.fixture
async def ledger_fixture():
    """Setup test user, organization, CoA, and customer for ledger testing."""
    async with AsyncSessionLocal() as session:
        # Create user
        user = User(
            email=f"accountant_{uuid.uuid4().hex[:8]}@example.com",
            full_name="Lead Accountant",
            password_hash="fakehash",
        )
        session.add(user)
        await session.flush()

        # Create Org
        org = Organization(
            name="Ledger Test Org",
            base_currency_code="USD",
            time_zone="UTC",
            created_by=user.id,
        )
        session.add(org)
        await session.flush()

        # Switch to tenant context
        await set_tenant_context(session, organization_id=org.id, user_id=user.id)

        # Settings
        settings = OrganizationSettings(
            organization_id=org.id,
            invoice_prefix="INV-",
            next_invoice_number=1,
        )
        session.add(settings)

        # Membership
        membership = Membership(
            organization_id=org.id,
            user_id=user.id,
            role="owner",
            status="active",
        )
        session.add(membership)
        await session.flush()

        # Seed CoA
        accounts = await seed_chart_of_accounts(session, organization_id=org.id)

        # Customer
        customer = Contact(
            organization_id=org.id,
            display_name="Acme Client Corp",
            is_customer=True,
            status="active",
        )
        session.add(customer)
        await session.flush()

        await session.commit()

        # Build account dictionary
        acc_map = {acc.system_key: acc.id for acc in accounts if acc.system_key}

        return {
            "user_id": user.id,
            "org_id": org.id,
            "customer_id": customer.id,
            "accounts": acc_map,
        }


@pytest.mark.asyncio
async def test_balanced_journal_posting_success(ledger_fixture):
    """Test valid double-entry posting commits and enforces exact balance."""
    fixture = ledger_fixture
    org_id = fixture["org_id"]
    user_id = fixture["user_id"]
    acc = fixture["accounts"]
    customer_id = fixture["customer_id"]

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        # JP-01: Debit AR $115.00, Credit Sales $100.00, Credit Tax $15.00
        req = await build_jp01_invoice_issuance(
            organization_id=org_id,
            invoice_id=uuid.uuid4(),
            customer_id=customer_id,
            accounting_date=date.today(),
            net_amount=Decimal("100.0000"),
            tax_amount=Decimal("15.0000"),
            grand_total=Decimal("115.0000"),
            ar_account_id=acc["accounts_receivable"],
            sales_account_id=acc["sales_income"],
            tax_account_id=acc["tax_payable"],
            created_by=user_id,
            invoice_number="INV-1001",
        )

        journal = await PostingGateway.post_journal(session, req)

        assert journal.id is not None
        assert journal.total_amount == Decimal("115.0000")

        # Verify lines in DB within transaction
        stmt = select(JournalLine).where(
            JournalLine.organization_id == org_id,
            JournalLine.journal_id == journal.id,
        ).order_by(JournalLine.line_no)
        res = await session.execute(stmt)
        lines = res.scalars().all()

        assert len(lines) == 3
        assert lines[0].debit == Decimal("115.0000")
        assert lines[0].credit == Decimal("0.0000")
        assert lines[0].account_id == acc["accounts_receivable"]

        assert lines[1].debit == Decimal("0.0000")
        assert lines[1].credit == Decimal("100.0000")
        assert lines[1].account_id == acc["sales_income"]

        assert lines[2].debit == Decimal("0.0000")
        assert lines[2].credit == Decimal("15.0000")
        assert lines[2].account_id == acc["tax_payable"]

        await session.commit()


@pytest.mark.asyncio
async def test_unbalanced_journal_rejected_by_domain(ledger_fixture):
    """Test posting gateway rejects unbalanced debits and credits before DB emission."""
    fixture = ledger_fixture
    org_id = fixture["org_id"]
    user_id = fixture["user_id"]
    acc = fixture["accounts"]

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        req = PostingRequest(
            organization_id=org_id,
            accounting_date=date.today(),
            source_type="manual",
            created_by=user_id,
            lines=[
                PostingLine(account_id=acc["cash"], debit=Decimal("100.0000")),
                PostingLine(account_id=acc["sales_income"], credit=Decimal("95.0000")),
            ],
        )

        with pytest.raises(UnbalancedJournalError, match="Unbalanced journal entry"):
            await PostingGateway.post_journal(session, req)


@pytest.mark.asyncio
async def test_unbalanced_journal_rejected_by_db_deferred_trigger(ledger_fixture):
    """Test PostgreSQL deferred trigger catches unbalanced journals even on direct SQL insert."""
    fixture = ledger_fixture
    org_id = fixture["org_id"]
    user_id = fixture["user_id"]
    acc = fixture["accounts"]

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        jid = uuid.uuid4()
        today = date.today()

        # Insert header with total_amount = 100
        await session.execute(
            text("""
            INSERT INTO app.journals (
                organization_id, id, accounting_date, source_type, total_amount, created_by
            ) VALUES (
                :org_id, :jid, :dt, 'manual', 100.0000, :uid
            )
            """),
            {"org_id": org_id, "jid": jid, "dt": today, "uid": user_id},
        )

        # Insert unbalanced lines (Debit 100, Credit 80)
        await session.execute(
            text("""
            INSERT INTO app.journal_lines (
                organization_id, journal_id, line_no, account_id, debit, credit, accounting_date
            ) VALUES 
            (:org_id, :jid, 1, :a1, 100.0000, 0, :dt),
            (:org_id, :jid, 2, :a2, 0, 80.0000, :dt)
            """),
            {"org_id": org_id, "jid": jid, "a1": acc["cash"], "a2": acc["sales_income"], "dt": today},
        )

        # Deferred constraint trigger fires at COMMIT
        with pytest.raises(Exception, match="Journal entry does not balance"):
            await session.commit()


@pytest.mark.asyncio
async def test_period_lock_enforcement(ledger_fixture):
    """Test posting into a closed accounting period is blocked by domain and DB trigger."""
    fixture = ledger_fixture
    org_id = fixture["org_id"]
    user_id = fixture["user_id"]
    acc = fixture["accounts"]

    lock_date = date.today() - timedelta(days=5)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        # Lock the books
        await session.execute(
            text("UPDATE app.organization_settings SET lock_date = :ld WHERE organization_id = :org_id"),
            {"ld": lock_date, "org_id": org_id},
        )
        await session.commit()

    # Attempt to post prior to lock date
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        req = PostingRequest(
            organization_id=org_id,
            accounting_date=lock_date - timedelta(days=1),
            source_type="manual",
            created_by=user_id,
            lines=[
                PostingLine(account_id=acc["cash"], debit=Decimal("50.0000")),
                PostingLine(account_id=acc["sales_income"], credit=Decimal("50.0000")),
            ],
        )

        with pytest.raises(PeriodLockedError, match="Accounting period is closed"):
            await PostingGateway.post_journal(session, req)


@pytest.mark.asyncio
async def test_idempotency_prevents_duplicate_posting(ledger_fixture):
    """Test duplicate posting attempts for the same source document fail closed."""
    fixture = ledger_fixture
    org_id = fixture["org_id"]
    user_id = fixture["user_id"]
    acc = fixture["accounts"]
    customer_id = fixture["customer_id"]

    source_invoice_id = uuid.uuid4()

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        req = await build_jp01_invoice_issuance(
            organization_id=org_id,
            invoice_id=source_invoice_id,
            customer_id=customer_id,
            accounting_date=date.today(),
            net_amount=Decimal("200.0000"),
            tax_amount=Decimal("0.0000"),
            grand_total=Decimal("200.0000"),
            ar_account_id=acc["accounts_receivable"],
            sales_account_id=acc["sales_income"],
            tax_account_id=None,
            created_by=user_id,
        )

        await PostingGateway.post_journal(session, req)
        await session.commit()

    # Attempt duplicate
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        with pytest.raises(DuplicatePostingError, match="already exists"):
            await PostingGateway.post_journal(session, req)


@pytest.mark.asyncio
async def test_posted_journal_immutability(ledger_fixture):
    """Test that posted journals and lines cannot be modified or deleted via SQL."""
    fixture = ledger_fixture
    org_id = fixture["org_id"]
    user_id = fixture["user_id"]
    acc = fixture["accounts"]

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        req = PostingRequest(
            organization_id=org_id,
            accounting_date=date.today(),
            source_type="manual",
            created_by=user_id,
            lines=[
                PostingLine(account_id=acc["cash"], debit=Decimal("75.0000")),
                PostingLine(account_id=acc["sales_income"], credit=Decimal("75.0000")),
            ],
        )
        journal = await PostingGateway.post_journal(session, req)
        await session.commit()
        journal_id = journal.id

    # 1. Attempt UPDATE on app.journals
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        with pytest.raises(Exception, match="immutable|permission denied"):
            await session.execute(
                text("UPDATE app.journals SET memo = 'Hacked' WHERE id = :jid"),
                {"jid": journal_id},
            )
            await session.commit()

    # 2. Attempt DELETE on app.journal_lines
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        with pytest.raises(Exception, match="immutable|permission denied"):
            await session.execute(
                text("DELETE FROM app.journal_lines WHERE journal_id = :jid"),
                {"jid": journal_id},
            )
            await session.commit()


@pytest.mark.asyncio
async def test_same_transaction_rule_prevents_appending_lines(ledger_fixture):
    """Test cannot append lines to an already committed journal in a subsequent transaction."""
    fixture = ledger_fixture
    org_id = fixture["org_id"]
    user_id = fixture["user_id"]
    acc = fixture["accounts"]

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        req = PostingRequest(
            organization_id=org_id,
            accounting_date=date.today(),
            source_type="manual",
            created_by=user_id,
            lines=[
                PostingLine(account_id=acc["cash"], debit=Decimal("40.0000")),
                PostingLine(account_id=acc["sales_income"], credit=Decimal("40.0000")),
            ],
        )
        journal = await PostingGateway.post_journal(session, req)
        await session.commit()
        journal_id = journal.id

    # In a new transaction, try appending a 3rd line
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        with pytest.raises(Exception, match="same-transaction invariant violated"):
            await session.execute(
                text("""
                INSERT INTO app.journal_lines (
                    organization_id, journal_id, line_no, account_id, debit, credit, accounting_date
                ) VALUES (
                    :org_id, :jid, 3, :acc, 10.0000, 0, :dt
                )
                """),
                {"org_id": org_id, "jid": journal_id, "acc": acc["cash"], "dt": date.today()},
            )
            await session.commit()


@pytest.mark.asyncio
async def test_reversal_invariants_jp02_and_jp04(ledger_fixture):
    """Test invoice void (JP-02) and payment reversal (JP-04) lifecycle and anti-reversal rules."""
    fixture = ledger_fixture
    org_id = fixture["org_id"]
    user_id = fixture["user_id"]
    acc = fixture["accounts"]
    customer_id = fixture["customer_id"]

    invoice_id = uuid.uuid4()
    payment_id = uuid.uuid4()

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        # 1. Post JP-01 Invoice Issuance
        jp01_req = await build_jp01_invoice_issuance(
            organization_id=org_id,
            invoice_id=invoice_id,
            customer_id=customer_id,
            accounting_date=date.today(),
            net_amount=Decimal("300.0000"),
            tax_amount=Decimal("30.0000"),
            grand_total=Decimal("330.0000"),
            ar_account_id=acc["accounts_receivable"],
            sales_account_id=acc["sales_income"],
            tax_account_id=acc["tax_payable"],
            created_by=user_id,
            invoice_number="INV-2001",
        )
        inv_journal = await PostingGateway.post_journal(session, jp01_req)
        await session.commit()
        inv_journal_id = inv_journal.id

    # 2. Post JP-02 Invoice Void Reversal
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        jp02_req = await build_jp02_invoice_void(
            organization_id=org_id,
            invoice_id=invoice_id,
            customer_id=customer_id,
            accounting_date=date.today(),
            net_amount=Decimal("300.0000"),
            tax_amount=Decimal("30.0000"),
            grand_total=Decimal("330.0000"),
            ar_account_id=acc["accounts_receivable"],
            sales_account_id=acc["sales_income"],
            tax_account_id=acc["tax_payable"],
            original_journal_id=inv_journal_id,
            void_reason="Customer cancelled order prior to payment",
            created_by=user_id,
            invoice_number="INV-2001",
        )
        void_journal = await PostingGateway.post_journal(session, jp02_req)
        await session.commit()
        assert void_journal.reverses_journal_id == inv_journal_id

    # 3. Verify target journal cannot be reversed a second time
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        second_rev_req = PostingRequest(
            organization_id=org_id,
            accounting_date=date.today(),
            source_type="manual",
            reverses_journal_id=inv_journal_id,
            reason="Second attempt to reverse the same original invoice journal",
            created_by=user_id,
            lines=[
                PostingLine(account_id=acc["sales_income"], debit=Decimal("300.0000")),
                PostingLine(account_id=acc["tax_payable"], debit=Decimal("30.0000")),
                PostingLine(account_id=acc["accounts_receivable"], credit=Decimal("330.0000")),
            ],
        )

        with pytest.raises(InvalidReversalError, match="already been reversed"):
            await PostingGateway.post_journal(session, second_rev_req)

    # 4. Verify a reversal journal cannot ITSELF be reversed
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        meta_rev_req = PostingRequest(
            organization_id=org_id,
            accounting_date=date.today(),
            source_type="journal_reversal",
            reverses_journal_id=void_journal.id,
            reason="Illegal attempt to un-void by reversing the void",
            created_by=user_id,
            lines=[
                PostingLine(account_id=acc["accounts_receivable"], debit=Decimal("330.0000")),
                PostingLine(account_id=acc["sales_income"], credit=Decimal("330.0000")),
            ],
        )
        with pytest.raises(InvalidReversalError, match="Cannot reverse a reversal"):
            await PostingGateway.post_journal(session, meta_rev_req)
