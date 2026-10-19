"""Supplier Payment Service - Disbursements, Bill Allocation Rollups, and Reversals."""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.accounting import Account
from app.models.bill import Bill
from app.models.identity import User
from app.models.master import Contact
from app.models.supplier_payment import SupplierPayment, SupplierPaymentAllocation
from app.schemas.supplier_payment import SupplierPaymentCreate
from app.services.posting_gateway import (
    PostingGateway,
    build_jp09_supplier_payment,
    build_jp10_supplier_payment_reversal,
)


def quantize_money(amount: Decimal) -> Decimal:
    """Format and round monetary decimal to 4 standard fractional places."""
    return amount.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


class SupplierPaymentService:
    @staticmethod
    async def record_payment(
        db: AsyncSession,
        organization_id: uuid.UUID,
        payload: SupplierPaymentCreate,
        current_user: User,
    ) -> SupplierPayment:
        """Record supplier disbursement, allocate to open bills, and atomically emit JP-09 journal."""
        # 1. Validate Vendor
        vend_stmt = select(Contact).where(
            Contact.organization_id == organization_id,
            Contact.id == payload.vendor_id,
            Contact.is_vendor == True,
            Contact.status == "active",
        )
        vend_res = await db.execute(vend_stmt)
        vendor = vend_res.scalar_one_or_none()
        if not vendor:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vendor not found, inactive, or not configured as a vendor",
            )

        # 2. Determine Payment Account (Cash/Bank)
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

        # 3. Lookup AP System Account
        ap_stmt = select(Account.id).where(
            Account.organization_id == organization_id,
            Account.system_key == "accounts_payable",
        )
        ap_res = await db.execute(ap_stmt)
        ap_acc_id = ap_res.scalar_one_or_none()
        if not ap_acc_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Accounts Payable control account not found",
            )

        # 4. Validate Allocations and Lock Bills
        total_allocated = Decimal("0.0000")
        bills_to_allocate = []

        for alloc in payload.allocations:
            alloc_amount = quantize_money(Decimal(str(alloc.amount)))
            if alloc_amount <= Decimal("0.0000"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Allocation amount must be strictly positive",
                )

            bill_stmt = (
                select(Bill)
                .where(
                    Bill.organization_id == organization_id,
                    Bill.id == alloc.bill_id,
                )
                .with_for_update()
            )
            bill_res = await db.execute(bill_stmt)
            bill = bill_res.scalar_one_or_none()

            if not bill:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Bill {alloc.bill_id} not found",
                )
            if bill.vendor_id != payload.vendor_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Bill {alloc.bill_id} does not belong to vendor {payload.vendor_id}",
                )
            if bill.state != "posted":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot allocate payment to bill in '{bill.state}' state (only 'posted' allowed)",
                )

            outstanding = bill.total_amount - bill.paid_amount
            if alloc_amount > outstanding:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Allocation amount ({alloc_amount}) exceeds outstanding bill balance ({outstanding})",
                )

            total_allocated += alloc_amount
            bills_to_allocate.append((bill, alloc_amount))

        payment_amount = quantize_money(Decimal(str(payload.amount)))
        if total_allocated > payment_amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Total allocated amount ({total_allocated}) cannot exceed total payment amount ({payment_amount})",
            )

        # 5. Sequential Payment Number
        count_stmt = select(func.count(SupplierPayment.id)).where(SupplierPayment.organization_id == organization_id)
        count_res = await db.execute(count_stmt)
        spay_count = (count_res.scalar_one() or 0) + 1
        payment_number = f"SPAY-{spay_count:04d}"

        # 6. Post JP-09 Journal Atomically
        payment_id = uuid.uuid4()
        jp09_req = await build_jp09_supplier_payment(
            organization_id=organization_id,
            supplier_payment_id=payment_id,
            vendor_id=payload.vendor_id,
            accounting_date=payload.payment_date,
            amount=payment_amount,
            payment_account_id=payment_acc_id,
            ap_account_id=ap_acc_id,
            created_by=current_user.id,
            payment_reference=payload.reference or payment_number,
        )
        journal = await PostingGateway.post_journal(db, jp09_req)

        # 7. Create SupplierPayment Record
        payment = SupplierPayment(
            id=payment_id,
            organization_id=organization_id,
            vendor_id=payload.vendor_id,
            payment_number=payment_number,
            payment_date=payload.payment_date,
            amount=payment_amount,
            method=payload.method,
            payment_account_id=payment_acc_id,
            reference=payload.reference,
            notes=payload.notes,
            state="active",
            allocated_amount=total_allocated,
            created_by=current_user.id,
            journal_id=journal.id,
        )
        db.add(payment)
        await db.flush()

        # 8. Record Allocations and Update Bill Paid Balances
        for bill, alloc_amt in bills_to_allocate:
            allocation = SupplierPaymentAllocation(
                organization_id=organization_id,
                payment_id=payment.id,
                bill_id=bill.id,
                vendor_id=payment.vendor_id,
                amount=alloc_amt,
                status="active",
            )
            db.add(allocation)
            bill.paid_amount += alloc_amt

        await db.flush()
        return payment

    @staticmethod
    async def reverse_payment(
        db: AsyncSession,
        organization_id: uuid.UUID,
        payment_id: uuid.UUID,
        reversal_reason: str,
        current_user: User,
    ) -> SupplierPayment:
        """Reverse an active supplier payment, undo allocations, and emit JP-10 reversal journal."""
        # 1. Fetch and Lock Payment
        stmt = (
            select(SupplierPayment)
            .where(
                SupplierPayment.organization_id == organization_id,
                SupplierPayment.id == payment_id,
            )
            .options(selectinload(SupplierPayment.allocations))
            .with_for_update()
        )
        res = await db.execute(stmt)
        payment = res.scalar_one_or_none()

        if not payment:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supplier payment not found")
        if payment.state != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot reverse payment in state '{payment.state}' (only 'active' can be reversed)",
            )


        # 2. Lookup AP System Account
        ap_stmt = select(Account.id).where(
            Account.organization_id == organization_id,
            Account.system_key == "accounts_payable",
        )
        ap_res = await db.execute(ap_stmt)
        ap_acc_id = ap_res.scalar_one()

        # 3. Post JP-10 Reversal Journal
        jp10_req = await build_jp10_supplier_payment_reversal(
            organization_id=organization_id,
            supplier_payment_id=payment.id,
            vendor_id=payment.vendor_id,
            accounting_date=payment.payment_date,
            amount=payment.amount,
            payment_account_id=payment.payment_account_id,
            ap_account_id=ap_acc_id,
            original_journal_id=payment.journal_id,
            reversal_reason=reversal_reason,
            created_by=current_user.id,
            payment_reference=payment.reference or payment.payment_number,
        )
        rev_journal = await PostingGateway.post_journal(db, jp10_req)

        # 4. Reverse Allocations and Restore Bill Balances
        for alloc in payment.allocations:
            if alloc.status == "active":
                bill_stmt = (
                    select(Bill)
                    .where(
                        Bill.organization_id == organization_id,
                        Bill.id == alloc.bill_id,
                    )
                    .with_for_update()
                )
                bill_res = await db.execute(bill_stmt)
                bill = bill_res.scalar_one_or_none()
                if bill:
                    bill.paid_amount -= alloc.amount
                alloc.status = "reversed"

        # 5. Update Payment State
        payment.state = "reversed"
        payment.reversal_journal_id = rev_journal.id
        payment.reversed_at = datetime.utcnow()
        payment.reversal_reason = reversal_reason

        await db.flush()
        return payment

    @staticmethod
    async def get_payment(
        db: AsyncSession,
        organization_id: uuid.UUID,
        payment_id: uuid.UUID,
    ) -> SupplierPayment:
        """Fetch supplier payment with allocations."""
        stmt = (
            select(SupplierPayment)
            .where(
                SupplierPayment.organization_id == organization_id,
                SupplierPayment.id == payment_id,
            )
            .options(selectinload(SupplierPayment.allocations))
        )
        res = await db.execute(stmt)
        payment = res.scalar_one_or_none()
        if not payment:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supplier payment not found")
        return payment

    @staticmethod
    async def list_payments(
        db: AsyncSession,
        organization_id: uuid.UUID,
        vendor_id: Optional[uuid.UUID] = None,
        state: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[SupplierPayment]:
        """List supplier payments with optional filters."""
        stmt = (
            select(SupplierPayment)
            .where(SupplierPayment.organization_id == organization_id)
            .options(selectinload(SupplierPayment.allocations))
            .order_by(SupplierPayment.payment_date.desc(), SupplierPayment.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if vendor_id:
            stmt = stmt.where(SupplierPayment.vendor_id == vendor_id)
        if state:
            stmt = stmt.where(SupplierPayment.state == state)

        res = await db.execute(stmt)
        return list(res.scalars().all())
