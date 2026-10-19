"""Customer Payment Service - Receipts, Invoice Allocation Rollups, Atomic JP-03 Posting, and JP-04 Reversals."""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.accounting import Account
from app.models.identity import User
from app.models.invoice import Invoice
from app.models.master import Contact
from app.models.organization import Organization
from app.models.payment import Payment, PaymentAllocation
from app.schemas.payment import PaymentCreate, PaymentResponse
from app.services.notification_service import NotificationService
from app.services.posting_gateway import (
    PostingGateway,
    PostingLine,
    PostingRequest,
    build_jp03_payment_receipt,
    build_jp04_payment_reversal,
)


class PaymentService:
    @staticmethod
    async def record_payment(
        db: AsyncSession,
        organization_id: uuid.UUID,
        payload: PaymentCreate,
        current_user: User,
    ) -> Payment:
        """Record customer payment receipt, allocate to open invoices, and atomically emit JP-03 journal."""
        # 1. Validate Customer
        cust_stmt = select(Contact).where(
            Contact.organization_id == organization_id,
            Contact.id == payload.customer_id,
            Contact.is_customer == True,
            Contact.status == "active",
        )
        cust_res = await db.execute(cust_stmt)
        customer = cust_res.scalar_one_or_none()
        if not customer:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Customer not found or not active",
            )

        # 2. Determine Deposit Account (Cash/Bank)
        deposit_acc_id = payload.deposit_account_id
        if not deposit_acc_id:
            bank_stmt = select(Account.id).where(
                Account.organization_id == organization_id,
                Account.system_key == "bank",
            )
            bank_res = await db.execute(bank_stmt)
            deposit_acc_id = bank_res.scalar_one_or_none()
            if not deposit_acc_id:
                # Fallback to cash
                cash_stmt = select(Account.id).where(
                    Account.organization_id == organization_id,
                    Account.system_key == "cash",
                )
                cash_res = await db.execute(cash_stmt)
                deposit_acc_id = cash_res.scalar_one_or_none()

        if not deposit_acc_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Deposit bank/cash account not found in Chart of Accounts",
            )

        # 3. Lookup AR System Account
        ar_stmt = select(Account.id).where(
            Account.organization_id == organization_id,
            Account.system_key == "accounts_receivable",
        )
        ar_res = await db.execute(ar_stmt)
        ar_acc_id = ar_res.scalar_one_or_none()
        if not ar_acc_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Accounts Receivable control account not found",
            )

        # 4. Validate Allocations
        total_allocated = Decimal("0.0000")
        invoices_to_allocate = []

        for alloc in payload.allocations:
            alloc_amount = Decimal(str(alloc.amount))
            if alloc_amount <= Decimal("0.0000"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Allocation amount must be strictly positive",
                )

            inv_stmt = (
                select(Invoice)
                .where(
                    Invoice.organization_id == organization_id,
                    Invoice.id == alloc.invoice_id,
                )
                .with_for_update()
            )
            inv_res = await db.execute(inv_stmt)
            invoice = inv_res.scalar_one_or_none()

            if not invoice:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Invoice {alloc.invoice_id} not found",
                )
            if invoice.customer_id != payload.customer_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invoice {alloc.invoice_id} does not belong to customer {payload.customer_id}",
                )
            if invoice.state != "issued":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot allocate payment to invoice in {invoice.state} state (only issued allowed)",
                )

            outstanding = invoice.total_amount - invoice.settled_amount
            if alloc_amount > outstanding:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Allocation amount ({alloc_amount}) exceeds outstanding invoice balance ({outstanding})",
                )

            total_allocated += alloc_amount
            invoices_to_allocate.append((invoice, alloc_amount))

        payment_amount = Decimal(str(payload.amount))
        if total_allocated > payment_amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Total allocated amount ({total_allocated}) cannot exceed total payment amount ({payment_amount})",
            )

        # 5. Pre-generate payment ID and post JP-03 Journal Atomically
        payment_id = uuid.uuid4()
        jp03_req = await build_jp03_payment_receipt(
            organization_id=organization_id,
            payment_id=payment_id,
            customer_id=payload.customer_id,
            accounting_date=payload.payment_date,
            amount=payment_amount,
            deposit_account_id=deposit_acc_id,
            ar_account_id=ar_acc_id,
            created_by=current_user.id,
            payment_reference=payload.reference,
        )
        journal = await PostingGateway.post_journal(db, jp03_req)

        # 6. Create Payment Record with valid journal_id
        payment = Payment(
            organization_id=organization_id,
            id=payment_id,
            customer_id=payload.customer_id,
            payment_date=payload.payment_date,
            amount=payment_amount,
            method=payload.method,
            deposit_account_id=deposit_acc_id,
            reference=payload.reference,
            notes=payload.notes,
            state="active",
            allocated_amount=Decimal("0.0000"),
            created_by=current_user.id,
            journal_id=journal.id,
        )
        db.add(payment)
        await db.flush()

        # 7. Apply Allocations (Triggers update settled_amount on invoices & allocated_amount on payment)
        for invoice, alloc_amount in invoices_to_allocate:
            allocation = PaymentAllocation(
                organization_id=organization_id,
                payment_id=payment.id,
                invoice_id=invoice.id,
                customer_id=payload.customer_id,
                amount=alloc_amount,
                status="active",
            )
            db.add(allocation)

        await db.flush()
        payment.allocated_amount = total_allocated

        # 8. Queue Payment Confirmation Notification (Transactional Outbox)
        if customer.email:
            org_stmt = select(Organization).where(Organization.id == organization_id)
            org_res = await db.execute(org_stmt)
            org = org_res.scalar_one_or_none()
            org_name = org.name if org else "Ledgerly"

            allocations_payload = []
            for inv, amt in invoices_to_allocate:
                rem = (inv.total_amount - inv.settled_amount) - amt
                allocations_payload.append({
                    "invoice_id": str(inv.id),
                    "invoice_number": inv.invoice_number,
                    "amount": str(amt),
                    "balance_due": str(max(Decimal("0.0000"), rem)),
                })

            await NotificationService.queue_notification(
                db=db,
                organization_id=organization_id,
                event_type="payment_received",
                source_type="payment",
                source_id=payment.id,
                idempotency_key=f"pay_received_{payment.id}",
                recipient_email=customer.email,
                recipient_name=customer.display_name,
                subject=f"Payment Receipt: {payment.reference or 'REC'} (${payment.amount:,.2f})",
                payload={
                    "payment_id": str(payment.id),
                    "reference": payment.reference or "Payment Received",
                    "amount": str(payment.amount),
                    "payment_date": payment.payment_date.isoformat(),
                    "customer_name": customer.display_name,
                    "organization_name": org_name,
                    "allocations": allocations_payload,
                },
            )

        return payment

    @staticmethod
    async def reverse_payment(
        db: AsyncSession,
        organization_id: uuid.UUID,
        payment_id: uuid.UUID,
        reversal_reason: str,
        current_user: User,
    ) -> Payment:
        """Reverse a payment receipt, unallocate settlements, and atomically emit JP-04 reversal journal."""
        # 1. Fetch and Lock Payment
        stmt = (
            select(Payment)
            .where(
                Payment.organization_id == organization_id,
                Payment.id == payment_id,
            )
            .options(selectinload(Payment.allocations))
            .with_for_update()
        )
        res = await db.execute(stmt)
        payment = res.scalar_one_or_none()

        if not payment:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
        if payment.state != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot reverse payment in {payment.state} state (only active allowed)",
            )

        # 2. Lookup AR Control Account
        ar_stmt = select(Account.id).where(
            Account.organization_id == organization_id,
            Account.system_key == "accounts_receivable",
        )
        ar_res = await db.execute(ar_stmt)
        ar_acc_id = ar_res.scalar_one()

        # 3. Post JP-04 Reversal Journal
        jp04_req = await build_jp04_payment_reversal(
            organization_id=organization_id,
            payment_id=payment.id,
            customer_id=payment.customer_id,
            accounting_date=datetime.utcnow().date(),
            amount=payment.amount,
            deposit_account_id=payment.deposit_account_id,
            ar_account_id=ar_acc_id,
            original_journal_id=payment.journal_id,
            reversal_reason=reversal_reason,
            created_by=current_user.id,
            payment_reference=payment.reference,
        )
        rev_journal = await PostingGateway.post_journal(db, jp04_req)

        # 4. Reverse Allocations (Trigger subtracts settled_amount from invoices and allocated_amount from payment)
        for alloc in payment.allocations:
            if alloc.status == "active":
                alloc.status = "reversed"
                alloc.reversed_at = datetime.utcnow()
                alloc.reversed_by = current_user.id

        await db.flush()

        # 5. Update Payment State
        payment.state = "reversed"
        payment.reversal_journal_id = rev_journal.id
        payment.reversed_at = datetime.utcnow()
        payment.reversed_by = current_user.id
        payment.reversal_reason = reversal_reason

        await db.flush()
        return payment
