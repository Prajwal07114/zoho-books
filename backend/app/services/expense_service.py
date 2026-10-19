"""Direct Business Expense Service - Recording (JP-11) and Void Reversal (JP-12)."""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting import Account, TaxRate
from app.models.expense import Expense
from app.models.identity import User
from app.models.master import Contact
from app.schemas.expense import ExpenseCreate
from app.services.posting_gateway import (
    PostingGateway,
    build_jp11_expense_posting,
    build_jp12_expense_reversal,
)


def quantize_money(amount: Decimal) -> Decimal:
    """Format and round monetary decimal to 4 standard fractional places."""
    return amount.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


class ExpenseService:
    @staticmethod
    async def record_expense(
        db: AsyncSession,
        organization_id: uuid.UUID,
        payload: ExpenseCreate,
        current_user: User,
    ) -> Expense:
        """Record direct expense and atomically emit JP-11 double-entry journal."""
        # 1. Validate Expense Account
        exp_acc_stmt = select(Account).where(
            Account.organization_id == organization_id,
            Account.id == payload.expense_account_id,
            Account.status == "active",
            Account.is_postable == True,
        )
        exp_acc_res = await db.execute(exp_acc_stmt)
        exp_acc = exp_acc_res.scalar_one_or_none()
        if not exp_acc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Expense account not found, inactive, or not postable",
            )
        if exp_acc.account_type not in ("expense", "asset"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Account '{exp_acc.name}' of type '{exp_acc.account_type}' is not eligible for expense recording",
            )

        # 2. Determine and Validate Payment Account (Cash/Bank)
        payment_acc_id = payload.payment_account_id
        if not payment_acc_id:
            bank_stmt = select(Account.id).where(
                Account.organization_id == organization_id,
                Account.system_key == "bank",
                Account.status == "active",
            )
            bank_res = await db.execute(bank_stmt)
            payment_acc_id = bank_res.scalar_one_or_none()
            if not payment_acc_id:
                cash_stmt = select(Account.id).where(
                    Account.organization_id == organization_id,
                    Account.system_key == "cash",
                    Account.status == "active",
                )
                cash_res = await db.execute(cash_stmt)
                payment_acc_id = cash_res.scalar_one_or_none()

        if not payment_acc_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Payment bank or cash account not found in Chart of Accounts",
            )

        pay_acc_stmt = select(Account).where(
            Account.organization_id == organization_id,
            Account.id == payment_acc_id,
            Account.status == "active",
            Account.is_postable == True,
        )
        pay_acc_res = await db.execute(pay_acc_stmt)
        pay_acc = pay_acc_res.scalar_one_or_none()
        if not pay_acc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment account not found, inactive, or not postable",
            )

        # 3. Optional Vendor Validation
        if payload.vendor_id:
            vend_stmt = select(Contact).where(
                Contact.organization_id == organization_id,
                Contact.id == payload.vendor_id,
                Contact.is_vendor == True,
                Contact.status == "active",
            )
            vend_res = await db.execute(vend_stmt)
            if vend_res.scalar_one_or_none() is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Referenced vendor not found, inactive, or not configured as a vendor",
                )

        # 4. Tax Calculation
        net_amount = quantize_money(Decimal(str(payload.net_amount)))
        tax_amount = Decimal("0.0000")
        tax_acc_id = None

        if payload.tax_rate_id:
            tax_stmt = select(TaxRate).where(
                TaxRate.organization_id == organization_id,
                TaxRate.id == payload.tax_rate_id,
                TaxRate.is_active == True,
            )
            tax_res = await db.execute(tax_stmt)
            tax_rate = tax_res.scalar_one_or_none()
            if tax_rate:
                tax_amount = quantize_money(net_amount * (tax_rate.rate_percent / Decimal("100.0000")))
                tax_acc_stmt = select(Account.id).where(
                    Account.organization_id == organization_id,
                    Account.system_key == "tax_payable",
                )
                tax_acc_res = await db.execute(tax_acc_stmt)
                tax_acc_id = tax_acc_res.scalar_one_or_none()

        total_amount = net_amount + tax_amount

        # 5. Sequential Number
        count_stmt = select(func.count(Expense.id)).where(Expense.organization_id == organization_id)
        count_res = await db.execute(count_stmt)
        exp_count = (count_res.scalar_one() or 0) + 1
        expense_number = f"EXP-{exp_count:04d}"

        # 6. Post JP-11 Journal
        expense_id = uuid.uuid4()
        jp11_req = await build_jp11_expense_posting(
            organization_id=organization_id,
            expense_id=expense_id,
            vendor_id=payload.vendor_id,
            accounting_date=payload.expense_date,
            net_amount=net_amount,
            tax_amount=tax_amount,
            total_amount=total_amount,
            expense_account_id=payload.expense_account_id,
            payment_account_id=payment_acc_id,
            tax_account_id=tax_acc_id,
            created_by=current_user.id,
            expense_number=expense_number,
            description=payload.description,
        )

        journal = await PostingGateway.post_journal(db, jp11_req)

        # 7. Create Expense Record
        expense = Expense(
            id=expense_id,
            organization_id=organization_id,
            expense_date=payload.expense_date,
            vendor_id=payload.vendor_id,
            expense_account_id=payload.expense_account_id,
            payment_account_id=payment_acc_id,
            tax_rate_id=payload.tax_rate_id,
            amount=net_amount,
            tax_amount=tax_amount,
            state="posted",
            reference=payload.reference,
            description=payload.description.strip(),
            journal_id=journal.id,
            created_by=current_user.id,
        )
        db.add(expense)
        await db.flush()
        return expense


    @staticmethod
    async def void_expense(
        db: AsyncSession,
        organization_id: uuid.UUID,
        expense_id: uuid.UUID,
        void_reason: str,
        current_user: User,
    ) -> Expense:
        """Void an expense and emit JP-12 reversal journal."""
        stmt = (
            select(Expense)
            .where(
                Expense.organization_id == organization_id,
                Expense.id == expense_id,
            )
            .with_for_update()
        )
        res = await db.execute(stmt)
        expense = res.scalar_one_or_none()

        if not expense:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
        if expense.payment_status != "posted":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot void expense in '{expense.payment_status}' status (only posted allowed)",
            )

        tax_acc_id = None
        if expense.tax_amount > Decimal("0.0000"):
            tax_stmt = select(Account.id).where(
                Account.organization_id == organization_id,
                Account.system_key == "tax_payable",
            )
            tax_res = await db.execute(tax_stmt)
            tax_acc_id = tax_res.scalar_one_or_none()

        jp12_req = await build_jp12_expense_reversal(
            organization_id=organization_id,
            expense_id=expense.id,
            vendor_id=expense.vendor_id,
            accounting_date=expense.expense_date,
            net_amount=expense.net_amount,
            tax_amount=expense.tax_amount,
            total_amount=expense.total_amount,
            expense_account_id=expense.expense_account_id,
            payment_account_id=expense.payment_account_id,
            tax_account_id=tax_acc_id,
            original_journal_id=expense.posting_journal_id,
            void_reason=void_reason,
            created_by=current_user.id,
            expense_number=expense.reference or str(expense.id),
        )

        void_journal = await PostingGateway.post_journal(db, jp12_req)

        expense.state = "void"
        expense.void_journal_id = void_journal.id

        await db.flush()
        return expense


    @staticmethod
    async def get_expense(
        db: AsyncSession,
        organization_id: uuid.UUID,
        expense_id: uuid.UUID,
    ) -> Expense:
        """Fetch expense by ID."""
        stmt = select(Expense).where(
            Expense.organization_id == organization_id,
            Expense.id == expense_id,
        )
        res = await db.execute(stmt)
        expense = res.scalar_one_or_none()
        if not expense:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
        return expense

    @staticmethod
    async def list_expenses(
        db: AsyncSession,
        organization_id: uuid.UUID,
        vendor_id: Optional[uuid.UUID] = None,
        payment_status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Expense]:
        """List expenses with optional filters."""
        stmt = (
            select(Expense)
            .where(Expense.organization_id == organization_id)
            .order_by(Expense.expense_date.desc(), Expense.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if vendor_id:
            stmt = stmt.where(Expense.vendor_id == vendor_id)
        if payment_status:
            stmt = stmt.where(Expense.payment_status == payment_status)

        res = await db.execute(stmt)
        return list(res.scalars().all())
