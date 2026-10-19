"""Posting Gateway - Authoritative Double-Entry Accounting Service.

Enforces:
- Double-entry balance equality (SUM(debits) == SUM(credits) > 0)
- Valid, active, postable accounts within the same organization
- Period lock restrictions (accounting_date > organization_settings.lock_date)
- Idempotency per source document
- Strict immutability and non-destructive reversal invariants
- Atomicity within caller's database transaction
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import List, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting import Account, Journal, JournalLine
from app.models.organization import OrganizationSettings


class AccountingError(Exception):
    """Base exception for accounting domain violations."""
    pass


class UnbalancedJournalError(AccountingError):
    """Raised when debit and credit totals do not balance."""
    pass


class PeriodLockedError(AccountingError):
    """Raised when posting into a closed fiscal period."""
    pass


class DuplicatePostingError(AccountingError):
    """Raised when a posting for the same source document already exists."""
    pass


class InvalidAccountError(AccountingError):
    """Raised when a referenced account is invalid, inactive, or non-postable."""
    pass


class InvalidReversalError(AccountingError):
    """Raised when reversal invariants are violated."""
    pass


@dataclass(frozen=True)
class PostingLine:
    account_id: uuid.UUID
    debit: Decimal = Decimal("0.0000")
    credit: Decimal = Decimal("0.0000")
    description: Optional[str] = None
    contact_id: Optional[uuid.UUID] = None
    tax_rate_id: Optional[uuid.UUID] = None

    def __post_init__(self):
        if self.debit < 0 or self.credit < 0:
            raise AccountingError("Posting line amounts cannot be negative")
        if (self.debit == 0 and self.credit == 0) or (self.debit > 0 and self.credit > 0):
            raise AccountingError("A line must be strictly a debit OR a credit (exactly one non-zero)")


@dataclass(frozen=True)
class PostingRequest:
    organization_id: uuid.UUID
    accounting_date: date
    source_type: str
    created_by: uuid.UUID
    lines: List[PostingLine]
    source_id: Optional[uuid.UUID] = None
    memo: Optional[str] = None
    currency_code: str = "USD"
    reverses_journal_id: Optional[uuid.UUID] = None
    reason: Optional[str] = None
    correlation_id: Optional[uuid.UUID] = None


class PostingGateway:
    """The Ledger module's sole entry point for journal creation."""

    @staticmethod
    async def validate_posting(db: AsyncSession, request: PostingRequest) -> None:
        """Pure domain validation prior to database emission."""
        # 1. Minimum line count
        if len(request.lines) < 2:
            raise UnbalancedJournalError(f"Journal must contain at least 2 lines (found {len(request.lines)})")

        # 2. Mathematical balance
        total_debits = sum(line.debit for line in request.lines)
        total_credits = sum(line.credit for line in request.lines)

        if total_debits != total_credits:
            raise UnbalancedJournalError(
                f"Unbalanced journal entry: Total debits ({total_debits}) != Total credits ({total_credits})"
            )

        if total_debits <= Decimal("0.0000"):
            raise UnbalancedJournalError("Journal total amount must be strictly greater than zero")

        # 3. Period lock check
        stmt = select(OrganizationSettings.lock_date).where(
            OrganizationSettings.organization_id == request.organization_id
        )
        res = await db.execute(stmt)
        lock_date = res.scalar_one_or_none()
        if lock_date and request.accounting_date <= lock_date:
            raise PeriodLockedError(
                f"Cannot post to date {request.accounting_date}: Accounting period is closed through {lock_date}"
            )

        # 4. Idempotency check for source documents
        if request.source_id is not None:
            dup_stmt = select(Journal.id).where(
                Journal.organization_id == request.organization_id,
                Journal.source_type == request.source_type,
                Journal.source_id == request.source_id,
            )
            dup_res = await db.execute(dup_stmt)
            if dup_res.scalar_one_or_none() is not None:
                raise DuplicatePostingError(
                    f"A journal entry already exists for {request.source_type} ID {request.source_id}"
                )

        # 5. Account eligibility verification
        account_ids = {line.account_id for line in request.lines}
        acc_stmt = select(Account).where(
            Account.organization_id == request.organization_id,
            Account.id.in_(account_ids),
        )
        acc_res = await db.execute(acc_stmt)
        found_accounts = {acc.id: acc for acc in acc_res.scalars().all()}

        for acc_id in account_ids:
            if acc_id not in found_accounts:
                raise InvalidAccountError(f"Account {acc_id} does not exist in this organization")
            acc = found_accounts[acc_id]
            if acc.status != "active":
                raise InvalidAccountError(f"Account {acc.code} ({acc.name}) is not active")
            if not acc.is_postable:
                raise InvalidAccountError(f"Account {acc.code} ({acc.name}) is not a postable detail account")

        # 6. Reversal invariants
        if request.reverses_journal_id is not None:
            if not request.reason:
                raise InvalidReversalError("A reversal journal must provide an explicit explanation reason")

            rev_stmt = select(Journal).where(
                Journal.organization_id == request.organization_id,
                Journal.id == request.reverses_journal_id,
            )
            rev_res = await db.execute(rev_stmt)
            target_journal = rev_res.scalar_one_or_none()
            if not target_journal:
                raise InvalidReversalError(f"Target journal {request.reverses_journal_id} not found")

            if target_journal.reverses_journal_id is not None:
                raise InvalidReversalError("Cannot reverse a reversal journal entry")

            # Check if target is already reversed
            already_rev_stmt = select(Journal.id).where(
                Journal.organization_id == request.organization_id,
                Journal.reverses_journal_id == request.reverses_journal_id,
            )
            already_rev = await db.execute(already_rev_stmt)
            if already_rev.scalar_one_or_none() is not None:
                raise InvalidReversalError("Target journal has already been reversed")

    @classmethod
    async def post_journal(cls, db: AsyncSession, request: PostingRequest) -> Journal:
        """Validate and insert journal entry and lines atomically."""
        await cls.validate_posting(db, request)

        total_amount = sum(line.debit for line in request.lines)

        journal = Journal(
            organization_id=request.organization_id,
            accounting_date=request.accounting_date,
            memo=request.memo,
            source_type=request.source_type,
            source_id=request.source_id,
            currency_code=request.currency_code,
            total_amount=total_amount,
            reverses_journal_id=request.reverses_journal_id,
            reason=request.reason,
            created_by=request.created_by,
            correlation_id=request.correlation_id,
        )
        db.add(journal)
        await db.flush()

        for idx, line in enumerate(request.lines, start=1):
            journal_line = JournalLine(
                organization_id=request.organization_id,
                journal_id=journal.id,
                line_no=idx,
                account_id=line.account_id,
                debit=line.debit,
                credit=line.credit,
                accounting_date=request.accounting_date,
                contact_id=line.contact_id,
                tax_rate_id=line.tax_rate_id,
                description=line.description,
            )
            db.add(journal_line)

        await db.flush()
        return journal


# ---------------------------------------------------------------------------
# Pre-approved Journal Posting Templates (JP-01, JP-02, JP-03, JP-04)
# ---------------------------------------------------------------------------

async def build_jp01_invoice_issuance(
    organization_id: uuid.UUID,
    invoice_id: uuid.UUID,
    customer_id: uuid.UUID,
    accounting_date: date,
    net_amount: Decimal,
    tax_amount: Decimal,
    grand_total: Decimal,
    ar_account_id: uuid.UUID,
    sales_account_id: uuid.UUID,
    tax_account_id: Optional[uuid.UUID],
    created_by: uuid.UUID,
    invoice_number: Optional[str] = None,
) -> PostingRequest:
    """JP-01: Sales Invoice Issuance.
    Debit Accounts Receivable (grand_total)
    Credit Sales Income (net_amount)
    Credit Tax Payable (tax_amount, if tax > 0)
    """
    lines = [
        PostingLine(
            account_id=ar_account_id,
            debit=grand_total,
            credit=Decimal("0.0000"),
            contact_id=customer_id,
            description=f"Invoice {invoice_number or invoice_id} issuance",
        ),
        PostingLine(
            account_id=sales_account_id,
            debit=Decimal("0.0000"),
            credit=net_amount,
            contact_id=customer_id,
            description="Sales revenue",
        ),
    ]

    if tax_amount > Decimal("0.0000"):
        if not tax_account_id:
            raise AccountingError("Tax account ID is required when tax amount is greater than zero")
        lines.append(
            PostingLine(
                account_id=tax_account_id,
                debit=Decimal("0.0000"),
                credit=tax_amount,
                contact_id=customer_id,
                description="Tax liability",
            )
        )

    return PostingRequest(
        organization_id=organization_id,
        accounting_date=accounting_date,
        source_type="invoice",
        source_id=invoice_id,
        memo=f"Invoice issuance: {invoice_number or invoice_id}",
        created_by=created_by,
        lines=lines,
    )


async def build_jp02_invoice_void(
    organization_id: uuid.UUID,
    invoice_id: uuid.UUID,
    customer_id: uuid.UUID,
    accounting_date: date,
    net_amount: Decimal,
    tax_amount: Decimal,
    grand_total: Decimal,
    ar_account_id: uuid.UUID,
    sales_account_id: uuid.UUID,
    tax_account_id: Optional[uuid.UUID],
    original_journal_id: uuid.UUID,
    void_reason: str,
    created_by: uuid.UUID,
    invoice_number: Optional[str] = None,
) -> PostingRequest:
    """JP-02: Sales Invoice Void Reversal.
    Debit Sales Income (net_amount)
    Debit Tax Payable (tax_amount, if tax > 0)
    Credit Accounts Receivable (grand_total)
    """
    lines = [
        PostingLine(
            account_id=sales_account_id,
            debit=net_amount,
            credit=Decimal("0.0000"),
            contact_id=customer_id,
            description=f"Void Invoice {invoice_number or invoice_id} - Revenue reversal",
        )
    ]

    if tax_amount > Decimal("0.0000"):
        if not tax_account_id:
            raise AccountingError("Tax account ID is required when reversing tax liability")
        lines.append(
            PostingLine(
                account_id=tax_account_id,
                debit=tax_amount,
                credit=Decimal("0.0000"),
                contact_id=customer_id,
                description="Tax liability reversal",
            )
        )

    lines.append(
        PostingLine(
            account_id=ar_account_id,
            debit=Decimal("0.0000"),
            credit=grand_total,
            contact_id=customer_id,
            description="Accounts receivable reversal",
        )
    )

    return PostingRequest(
        organization_id=organization_id,
        accounting_date=accounting_date,
        source_type="invoice_void",
        source_id=invoice_id,
        memo=f"Void Invoice: {invoice_number or invoice_id}",
        reverses_journal_id=original_journal_id,
        reason=void_reason,
        created_by=created_by,
        lines=lines,
    )


async def build_jp03_payment_receipt(
    organization_id: uuid.UUID,
    payment_id: uuid.UUID,
    customer_id: uuid.UUID,
    accounting_date: date,
    amount: Decimal,
    deposit_account_id: uuid.UUID,
    ar_account_id: uuid.UUID,
    created_by: uuid.UUID,
    payment_reference: Optional[str] = None,
) -> PostingRequest:
    """JP-03: Customer Payment Receipt.
    Debit Cash/Bank Deposit Account (amount)
    Credit Accounts Receivable (amount)
    """
    lines = [
        PostingLine(
            account_id=deposit_account_id,
            debit=amount,
            credit=Decimal("0.0000"),
            contact_id=customer_id,
            description=f"Payment receipt {payment_reference or payment_id}",
        ),
        PostingLine(
            account_id=ar_account_id,
            debit=Decimal("0.0000"),
            credit=amount,
            contact_id=customer_id,
            description="Accounts receivable settlement",
        ),
    ]

    return PostingRequest(
        organization_id=organization_id,
        accounting_date=accounting_date,
        source_type="payment",
        source_id=payment_id,
        memo=f"Payment receipt: {payment_reference or payment_id}",
        created_by=created_by,
        lines=lines,
    )


async def build_jp04_payment_reversal(
    organization_id: uuid.UUID,
    payment_id: uuid.UUID,
    customer_id: uuid.UUID,
    accounting_date: date,
    amount: Decimal,
    deposit_account_id: uuid.UUID,
    ar_account_id: uuid.UUID,
    original_journal_id: uuid.UUID,
    reversal_reason: str,
    created_by: uuid.UUID,
    payment_reference: Optional[str] = None,
) -> PostingRequest:
    """JP-04: Payment Reversal.
    Debit Accounts Receivable (amount)
    Credit Cash/Bank Deposit Account (amount)
    """
    lines = [
        PostingLine(
            account_id=ar_account_id,
            debit=amount,
            credit=Decimal("0.0000"),
            contact_id=customer_id,
            description=f"Payment reversal {payment_reference or payment_id} - Restore receivable",
        ),
        PostingLine(
            account_id=deposit_account_id,
            debit=Decimal("0.0000"),
            credit=amount,
            contact_id=customer_id,
            description="Payment reversal - Bank refund/unsettle",
        ),
    ]

    return PostingRequest(
        organization_id=organization_id,
        accounting_date=accounting_date,
        source_type="payment_reversal",
        source_id=payment_id,
        memo=f"Payment reversal: {payment_reference or payment_id}",
        reverses_journal_id=original_journal_id,
        reason=reversal_reason,
        created_by=created_by,
        lines=lines,
    )


# ---------------------------------------------------------------------------
# Phase 6 Journal Posting Templates (JP-05 through JP-14)
# ---------------------------------------------------------------------------

async def build_jp05_credit_note_issuance(
    organization_id: uuid.UUID,
    credit_note_id: uuid.UUID,
    customer_id: uuid.UUID,
    accounting_date: date,
    net_amount: Decimal,
    tax_amount: Decimal,
    grand_total: Decimal,
    ar_account_id: uuid.UUID,
    sales_account_id: uuid.UUID,
    tax_account_id: Optional[uuid.UUID],
    created_by: uuid.UUID,
    credit_note_number: Optional[str] = None,
) -> PostingRequest:
    """JP-05: Sales Credit Note Issuance.
    Debit Sales Income (net_amount)
    Debit Tax Payable (tax_amount, if tax > 0)
    Credit Accounts Receivable (grand_total)
    """
    lines = [
        PostingLine(
            account_id=sales_account_id,
            debit=net_amount,
            credit=Decimal("0.0000"),
            contact_id=customer_id,
            description=f"Credit Note {credit_note_number or credit_note_id} revenue reduction",
        )
    ]

    if tax_amount > Decimal("0.0000"):
        if not tax_account_id:
            raise AccountingError("Tax account ID is required when credit note has tax amount")
        lines.append(
            PostingLine(
                account_id=tax_account_id,
                debit=tax_amount,
                credit=Decimal("0.0000"),
                contact_id=customer_id,
                description="Tax liability reduction",
            )
        )

    lines.append(
        PostingLine(
            account_id=ar_account_id,
            debit=Decimal("0.0000"),
            credit=grand_total,
            contact_id=customer_id,
            description=f"Credit Note {credit_note_number or credit_note_id} receivable reduction",
        )
    )

    return PostingRequest(
        organization_id=organization_id,
        accounting_date=accounting_date,
        source_type="credit_note",
        source_id=credit_note_id,
        memo=f"Credit note issuance: {credit_note_number or credit_note_id}",
        created_by=created_by,
        lines=lines,
    )


async def build_jp06_credit_note_void(
    organization_id: uuid.UUID,
    credit_note_id: uuid.UUID,
    customer_id: uuid.UUID,
    accounting_date: date,
    net_amount: Decimal,
    tax_amount: Decimal,
    grand_total: Decimal,
    ar_account_id: uuid.UUID,
    sales_account_id: uuid.UUID,
    tax_account_id: Optional[uuid.UUID],
    original_journal_id: uuid.UUID,
    void_reason: str,
    created_by: uuid.UUID,
    credit_note_number: Optional[str] = None,
) -> PostingRequest:
    """JP-06: Sales Credit Note Void Reversal.
    Debit Accounts Receivable (grand_total)
    Credit Sales Income (net_amount)
    Credit Tax Payable (tax_amount, if tax > 0)
    """
    lines = [
        PostingLine(
            account_id=ar_account_id,
            debit=grand_total,
            credit=Decimal("0.0000"),
            contact_id=customer_id,
            description=f"Void Credit Note {credit_note_number or credit_note_id} restore receivable",
        ),
        PostingLine(
            account_id=sales_account_id,
            debit=Decimal("0.0000"),
            credit=net_amount,
            contact_id=customer_id,
            description="Revenue restoration",
        ),
    ]

    if tax_amount > Decimal("0.0000"):
        if not tax_account_id:
            raise AccountingError("Tax account ID is required when reversing credit note tax")
        lines.append(
            PostingLine(
                account_id=tax_account_id,
                debit=Decimal("0.0000"),
                credit=tax_amount,
                contact_id=customer_id,
                description="Tax liability restoration",
            )
        )

    return PostingRequest(
        organization_id=organization_id,
        accounting_date=accounting_date,
        source_type="credit_note_void",
        source_id=credit_note_id,
        memo=f"Void Credit Note: {credit_note_number or credit_note_id}",
        reverses_journal_id=original_journal_id,
        reason=void_reason,
        created_by=created_by,
        lines=lines,
    )


async def build_jp07_bill_posting(
    organization_id: uuid.UUID,
    bill_id: uuid.UUID,
    vendor_id: uuid.UUID,
    accounting_date: date,
    lines_breakdown: List[tuple[uuid.UUID, Decimal, Optional[str]]],  # (account_id, debit_amount, description)
    tax_amount: Decimal,
    grand_total: Decimal,
    ap_account_id: uuid.UUID,
    tax_account_id: Optional[uuid.UUID],
    created_by: uuid.UUID,
    bill_number: Optional[str] = None,
) -> PostingRequest:
    """JP-07: Purchase Bill Posting.
    Debit Expense/Asset accounts per line (debit_amount)
    Debit Tax Payable / Input Tax (tax_amount, if tax > 0)
    Credit Accounts Payable (grand_total)
    """
    posting_lines: List[PostingLine] = []

    for acc_id, debit_amt, desc in lines_breakdown:
        posting_lines.append(
            PostingLine(
                account_id=acc_id,
                debit=debit_amt,
                credit=Decimal("0.0000"),
                contact_id=vendor_id,
                description=desc or f"Bill {bill_number or bill_id} expense",
            )
        )

    if tax_amount > Decimal("0.0000"):
        if not tax_account_id:
            raise AccountingError("Tax account ID is required when bill has tax amount")
        posting_lines.append(
            PostingLine(
                account_id=tax_account_id,
                debit=tax_amount,
                credit=Decimal("0.0000"),
                contact_id=vendor_id,
                description="Input tax on purchases",
            )
        )

    posting_lines.append(
        PostingLine(
            account_id=ap_account_id,
            debit=Decimal("0.0000"),
            credit=grand_total,
            contact_id=vendor_id,
            description=f"Bill {bill_number or bill_id} Accounts Payable",
        )
    )

    return PostingRequest(
        organization_id=organization_id,
        accounting_date=accounting_date,
        source_type="bill",
        source_id=bill_id,
        memo=f"Bill posting: {bill_number or bill_id}",
        created_by=created_by,
        lines=posting_lines,
    )


async def build_jp08_bill_void(
    organization_id: uuid.UUID,
    bill_id: uuid.UUID,
    vendor_id: uuid.UUID,
    accounting_date: date,
    lines_breakdown: List[tuple[uuid.UUID, Decimal, Optional[str]]],
    tax_amount: Decimal,
    grand_total: Decimal,
    ap_account_id: uuid.UUID,
    tax_account_id: Optional[uuid.UUID],
    original_journal_id: uuid.UUID,
    void_reason: str,
    created_by: uuid.UUID,
    bill_number: Optional[str] = None,
) -> PostingRequest:
    """JP-08: Purchase Bill Void Reversal.
    Debit Accounts Payable (grand_total)
    Credit Expense/Asset accounts per line (debit_amount)
    Credit Tax Payable / Input Tax (tax_amount, if tax > 0)
    """
    posting_lines: List[PostingLine] = [
        PostingLine(
            account_id=ap_account_id,
            debit=grand_total,
            credit=Decimal("0.0000"),
            contact_id=vendor_id,
            description=f"Void Bill {bill_number or bill_id} Accounts Payable reversal",
        )
    ]

    for acc_id, credit_amt, desc in lines_breakdown:
        posting_lines.append(
            PostingLine(
                account_id=acc_id,
                debit=Decimal("0.0000"),
                credit=credit_amt,
                contact_id=vendor_id,
                description=desc or f"Void Bill {bill_number or bill_id} expense reversal",
            )
        )

    if tax_amount > Decimal("0.0000"):
        if not tax_account_id:
            raise AccountingError("Tax account ID is required when reversing bill tax")
        posting_lines.append(
            PostingLine(
                account_id=tax_account_id,
                debit=Decimal("0.0000"),
                credit=tax_amount,
                contact_id=vendor_id,
                description="Input tax reversal",
            )
        )

    return PostingRequest(
        organization_id=organization_id,
        accounting_date=accounting_date,
        source_type="bill_void",
        source_id=bill_id,
        memo=f"Void Bill: {bill_number or bill_id}",
        reverses_journal_id=original_journal_id,
        reason=void_reason,
        created_by=created_by,
        lines=posting_lines,
    )


async def build_jp09_supplier_payment(
    organization_id: uuid.UUID,
    supplier_payment_id: uuid.UUID,
    vendor_id: uuid.UUID,
    accounting_date: date,
    amount: Decimal,
    payment_account_id: uuid.UUID,
    ap_account_id: uuid.UUID,
    created_by: uuid.UUID,
    payment_reference: Optional[str] = None,
) -> PostingRequest:
    """JP-09: Supplier Payment.
    Debit Accounts Payable (amount)
    Credit Cash/Bank Account (amount)
    """
    lines = [
        PostingLine(
            account_id=ap_account_id,
            debit=amount,
            credit=Decimal("0.0000"),
            contact_id=vendor_id,
            description=f"Supplier payment {payment_reference or supplier_payment_id} AP reduction",
        ),
        PostingLine(
            account_id=payment_account_id,
            debit=Decimal("0.0000"),
            credit=amount,
            contact_id=vendor_id,
            description=f"Supplier payment disbursement {payment_reference or supplier_payment_id}",
        ),
    ]

    return PostingRequest(
        organization_id=organization_id,
        accounting_date=accounting_date,
        source_type="bill_payment",
        source_id=supplier_payment_id,
        memo=f"Supplier payment: {payment_reference or supplier_payment_id}",
        created_by=created_by,
        lines=lines,
    )


async def build_jp10_supplier_payment_reversal(
    organization_id: uuid.UUID,
    supplier_payment_id: uuid.UUID,
    vendor_id: uuid.UUID,
    accounting_date: date,
    amount: Decimal,
    payment_account_id: uuid.UUID,
    ap_account_id: uuid.UUID,
    original_journal_id: uuid.UUID,
    reversal_reason: str,
    created_by: uuid.UUID,
    payment_reference: Optional[str] = None,
) -> PostingRequest:
    """JP-10: Supplier Payment Reversal.
    Debit Cash/Bank Account (amount)
    Credit Accounts Payable (amount)
    """
    lines = [
        PostingLine(
            account_id=payment_account_id,
            debit=amount,
            credit=Decimal("0.0000"),
            contact_id=vendor_id,
            description=f"Supplier payment reversal {payment_reference or supplier_payment_id} restore funds",
        ),
        PostingLine(
            account_id=ap_account_id,
            debit=Decimal("0.0000"),
            credit=amount,
            contact_id=vendor_id,
            description=f"Supplier payment reversal {payment_reference or supplier_payment_id} restore AP",
        ),
    ]

    return PostingRequest(
        organization_id=organization_id,
        accounting_date=accounting_date,
        source_type="bill_payment_reversal",
        source_id=supplier_payment_id,
        memo=f"Supplier payment reversal: {payment_reference or supplier_payment_id}",
        reverses_journal_id=original_journal_id,
        reason=reversal_reason,
        created_by=created_by,
        lines=lines,
    )


async def build_jp11_expense_posting(
    organization_id: uuid.UUID,
    expense_id: uuid.UUID,
    vendor_id: Optional[uuid.UUID],
    accounting_date: date,
    net_amount: Decimal,
    tax_amount: Decimal,
    total_amount: Decimal,
    expense_account_id: uuid.UUID,
    payment_account_id: uuid.UUID,
    tax_account_id: Optional[uuid.UUID],
    created_by: uuid.UUID,
    expense_number: Optional[str] = None,
    description: Optional[str] = None,
) -> PostingRequest:
    """JP-11: Direct Expense Recording.
    Debit Expense Account (net_amount)
    Debit Tax Account (tax_amount, if tax > 0)
    Credit Cash/Bank Account (total_amount)
    """
    lines = [
        PostingLine(
            account_id=expense_account_id,
            debit=net_amount,
            credit=Decimal("0.0000"),
            contact_id=vendor_id,
            description=description or f"Expense {expense_number or expense_id}",
        )
    ]

    if tax_amount > Decimal("0.0000"):
        if not tax_account_id:
            raise AccountingError("Tax account ID is required when expense has tax amount")
        lines.append(
            PostingLine(
                account_id=tax_account_id,
                debit=tax_amount,
                credit=Decimal("0.0000"),
                contact_id=vendor_id,
                description="Tax on expense",
            )
        )

    lines.append(
        PostingLine(
            account_id=payment_account_id,
            debit=Decimal("0.0000"),
            credit=total_amount,
            contact_id=vendor_id,
            description=f"Expense disbursement {expense_number or expense_id}",
        )
    )

    return PostingRequest(
        organization_id=organization_id,
        accounting_date=accounting_date,
        source_type="expense",
        source_id=expense_id,
        memo=f"Expense: {expense_number or expense_id}",
        created_by=created_by,
        lines=lines,
    )


async def build_jp12_expense_reversal(
    organization_id: uuid.UUID,
    expense_id: uuid.UUID,
    vendor_id: Optional[uuid.UUID],
    accounting_date: date,
    net_amount: Decimal,
    tax_amount: Decimal,
    total_amount: Decimal,
    expense_account_id: uuid.UUID,
    payment_account_id: uuid.UUID,
    tax_account_id: Optional[uuid.UUID],
    original_journal_id: uuid.UUID,
    void_reason: str,
    created_by: uuid.UUID,
    expense_number: Optional[str] = None,
) -> PostingRequest:
    """JP-12: Direct Expense Reversal.
    Debit Cash/Bank Account (total_amount)
    Credit Expense Account (net_amount)
    Credit Tax Account (tax_amount, if tax > 0)
    """
    lines = [
        PostingLine(
            account_id=payment_account_id,
            debit=total_amount,
            credit=Decimal("0.0000"),
            contact_id=vendor_id,
            description=f"Expense reversal {expense_number or expense_id} restore funds",
        ),
        PostingLine(
            account_id=expense_account_id,
            debit=Decimal("0.0000"),
            credit=net_amount,
            contact_id=vendor_id,
            description=f"Expense reversal {expense_number or expense_id}",
        ),
    ]

    if tax_amount > Decimal("0.0000"):
        if not tax_account_id:
            raise AccountingError("Tax account ID is required when reversing expense tax")
        lines.append(
            PostingLine(
                account_id=tax_account_id,
                debit=Decimal("0.0000"),
                credit=tax_amount,
                contact_id=vendor_id,
                description="Tax on expense reversal",
            )
        )

    return PostingRequest(
        organization_id=organization_id,
        accounting_date=accounting_date,
        source_type="expense_reversal",
        source_id=expense_id,
        memo=f"Expense reversal: {expense_number or expense_id}",
        reverses_journal_id=original_journal_id,
        reason=void_reason,
        created_by=created_by,
        lines=lines,
    )


async def build_jp14_opening_balance(
    organization_id: uuid.UUID,
    batch_id: uuid.UUID,
    accounting_date: date,
    lines: List[PostingLine],
    created_by: uuid.UUID,
    memo: Optional[str] = None,
) -> PostingRequest:
    """JP-14: Opening Balance Batch Posting.
    Emits balanced debits/credits across ledger accounts with net offset in Opening Balance Equity.
    """
    return PostingRequest(
        organization_id=organization_id,
        accounting_date=accounting_date,
        source_type="opening_balance",
        source_id=batch_id,
        memo=memo or f"Opening balance batch: {batch_id}",
        created_by=created_by,
        lines=lines,
    )

