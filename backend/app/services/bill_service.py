"""Purchase Bill Service - Creation, Calculations, Posting (JP-07), and Voiding (JP-08)."""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.accounting import Account, TaxRate
from app.models.bill import Bill, BillLine
from app.models.identity import User
from app.models.master import Contact, Item
from app.schemas.bill import BillCreate, BillLineCreate, BillResponse
from app.services.posting_gateway import (
    PostingGateway,
    build_jp07_bill_posting,
    build_jp08_bill_void,
)


def quantize_money(amount: Decimal) -> Decimal:
    """Format and round monetary decimal to 4 standard fractional places."""
    return amount.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


class BillService:
    @staticmethod
    async def create_draft_bill(
        db: AsyncSession,
        organization_id: uuid.UUID,
        payload: BillCreate,
        current_user: User,
    ) -> Bill:
        """Create a new purchase bill in draft state."""
        # 1. Validate vendor exists, is active, and is configured as a vendor
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

        # 2. Check for duplicate vendor invoice number
        vendor_inv_num = payload.vendor_invoice_number.strip() if payload.vendor_invoice_number else None
        if vendor_inv_num:
            dup_stmt = select(Bill.id).where(
                Bill.organization_id == organization_id,
                Bill.vendor_id == payload.vendor_id,
                Bill.vendor_invoice_number == vendor_inv_num,
                Bill.state != "void",
            )
            dup_res = await db.execute(dup_stmt)
            if dup_res.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"A bill with vendor invoice number '{vendor_inv_num}' already exists for this vendor",
                )

        # 3. Determine default expense account
        def_acc_stmt = select(Account.id).where(
            Account.organization_id == organization_id,
            Account.system_key == "general_expense",
        )
        def_acc_res = await db.execute(def_acc_stmt)
        default_expense_acc_id = def_acc_res.scalar_one_or_none()
        if not default_expense_acc_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Default general expense account (system key: general_expense) not found",
            )

        # 4. Create Bill Header
        bill = Bill(
            organization_id=organization_id,
            vendor_id=payload.vendor_id,
            state="draft",
            vendor_invoice_number=vendor_inv_num or f"DRAFT-{uuid.uuid4().hex[:8]}",
            bill_date=payload.bill_date,
            due_date=payload.due_date,
            currency_code=payload.currency_code.upper(),
            notes=payload.notes,
            created_by=current_user.id,
        )

        db.add(bill)
        await db.flush()

        # 5. Process Lines & Deterministic Calculations
        net_total = Decimal("0.0000")
        tax_total = Decimal("0.0000")

        for idx, line_in in enumerate(payload.lines, start=1):
            qty = Decimal(str(line_in.quantity))
            price = Decimal(str(line_in.unit_price))
            subtotal = quantize_money(qty * price)

            expense_acc = line_in.expense_account_id
            tax_rate_id = line_in.tax_rate_id

            if line_in.item_id:
                item_stmt = select(Item).where(
                    Item.organization_id == organization_id,
                    Item.id == line_in.item_id,
                    Item.is_active == True,
                )
                item_res = await db.execute(item_stmt)
                item = item_res.scalar_one_or_none()
                if item:
                    if not expense_acc and item.expense_account_id:
                        expense_acc = item.expense_account_id
                    if not tax_rate_id and item.default_tax_rate_id:
                        tax_rate_id = item.default_tax_rate_id

            if not expense_acc:
                expense_acc = default_expense_acc_id

            # Determine Tax
            line_tax = Decimal("0.0000")
            if tax_rate_id:
                tax_stmt = select(TaxRate).where(
                    TaxRate.organization_id == organization_id,
                    TaxRate.id == tax_rate_id,
                    TaxRate.is_active == True,
                )
                tax_res = await db.execute(tax_stmt)
                tax_rate = tax_res.scalar_one_or_none()
                if tax_rate:
                    line_tax = quantize_money(subtotal * (tax_rate.rate_percent / Decimal("100.0000")))

            line_total = subtotal + line_tax

            bill_line = BillLine(
                organization_id=organization_id,
                bill_id=bill.id,
                line_no=idx,
                item_id=line_in.item_id,
                description=line_in.description.strip(),
                quantity=qty,
                unit_price=price,
                line_subtotal=subtotal,
                tax_rate_id=tax_rate_id,
                tax_amount=line_tax,
                line_total=line_total,
                expense_account_id=expense_acc,
            )
            db.add(bill_line)

            net_total += subtotal
            tax_total += line_tax

        bill.net_amount = net_total
        bill.tax_amount = tax_total
        bill.total_amount = net_total + tax_total

        await db.flush()
        return bill

    @staticmethod
    async def post_bill(
        db: AsyncSession,
        organization_id: uuid.UUID,
        bill_id: uuid.UUID,
        current_user: User,
    ) -> Bill:
        """Issue/post bill atomically: freeze snapshot and emit JP-07 journal."""
        # 1. Fetch and Lock Bill
        stmt = (
            select(Bill)
            .where(
                Bill.organization_id == organization_id,
                Bill.id == bill_id,
            )
            .options(selectinload(Bill.lines))
            .with_for_update()
        )
        res = await db.execute(stmt)
        bill = res.scalar_one_or_none()

        if not bill:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bill not found")
        if bill.state != "draft":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot post bill in {bill.state} state (only draft allowed)",
            )
        if not bill.lines:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot post a bill without lines",
            )

        # 2. Sequential Bill Number
        count_stmt = select(func.count(Bill.id)).where(Bill.organization_id == organization_id)
        count_res = await db.execute(count_stmt)
        bill_count = (count_res.scalar_one() or 0) + 1
        bill_number = f"BILL-{bill_count:04d}"

        # 3. Fetch Vendor for Snapshot
        vend_stmt = select(Contact).where(
            Contact.organization_id == organization_id,
            Contact.id == bill.vendor_id,
        )
        vend_res = await db.execute(vend_stmt)
        vendor = vend_res.scalar_one()

        snapshot = {
            "version": 1,
            "posted_at": datetime.utcnow().isoformat(),
            "vendor": {
                "id": str(vendor.id),
                "display_name": vendor.display_name,
                "email": vendor.email,
                "phone": vendor.phone,
                "tax_identifier": vendor.tax_identifier,
            },
            "bill_number": bill_number,
            "vendor_invoice_number": bill.vendor_invoice_number,
            "bill_date": bill.bill_date.isoformat(),
            "due_date": bill.due_date.isoformat(),
            "currency_code": bill.currency_code,
            "net_amount": str(bill.net_amount),
            "tax_amount": str(bill.tax_amount),
            "total_amount": str(bill.total_amount),
            "lines": [
                {
                    "line_no": line.line_no,
                    "description": line.description,
                    "quantity": str(line.quantity),
                    "unit_price": str(line.unit_price),
                    "line_subtotal": str(line.line_subtotal),
                    "tax_amount": str(line.tax_amount),
                    "line_total": str(line.line_total),
                    "expense_account_id": str(line.expense_account_id),
                }
                for line in bill.lines
            ],
        }

        # 4. Lookup Accounts Payable System Account
        ap_stmt = select(Account.id).where(
            Account.organization_id == organization_id,
            Account.system_key == "accounts_payable",
        )
        ap_res = await db.execute(ap_stmt)
        ap_acc_id = ap_res.scalar_one_or_none()
        if not ap_acc_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Accounts Payable control account (system key: accounts_payable) not found",
            )

        tax_acc_id = None
        if bill.tax_amount > Decimal("0.0000"):
            tax_stmt = select(Account.id).where(
                Account.organization_id == organization_id,
                Account.system_key == "tax_payable",
            )
            tax_res = await db.execute(tax_stmt)
            tax_acc_id = tax_res.scalar_one_or_none()
            if not tax_acc_id:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Tax Payable account (system key: tax_payable) not found",
                )

        # 5. Build posting lines breakdown
        lines_breakdown = [
            (line.expense_account_id, line.line_subtotal, line.description)
            for line in bill.lines
        ]

        jp07_req = await build_jp07_bill_posting(
            organization_id=organization_id,
            bill_id=bill.id,
            vendor_id=bill.vendor_id,
            accounting_date=bill.bill_date,
            lines_breakdown=lines_breakdown,
            tax_amount=bill.tax_amount,
            grand_total=bill.total_amount,
            ap_account_id=ap_acc_id,
            tax_account_id=tax_acc_id,
            created_by=current_user.id,
            bill_number=bill_number,
        )

        journal = await PostingGateway.post_journal(db, jp07_req)

        # 6. Update Bill State to Posted
        bill.state = "posted"
        bill.bill_number = bill_number
        bill.post_journal_id = journal.id

        await db.flush()
        return bill


    @staticmethod
    async def void_bill(
        db: AsyncSession,
        organization_id: uuid.UUID,
        bill_id: uuid.UUID,
        void_reason: str,
        current_user: User,
    ) -> Bill:
        """Void a posted bill by posting JP-08 reversal journal."""
        stmt = (
            select(Bill)
            .where(
                Bill.organization_id == organization_id,
                Bill.id == bill_id,
            )
            .options(selectinload(Bill.lines))
            .with_for_update()
        )
        res = await db.execute(stmt)
        bill = res.scalar_one_or_none()

        if not bill:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bill not found")
        if bill.state != "posted":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot void bill in '{bill.state}' state (only 'posted' bills can be voided)",
            )
        if bill.paid_amount > Decimal("0.0000"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot void bill with active payments (paid amount: {bill.paid_amount}). Reverse payments first.",
            )

        ap_stmt = select(Account.id).where(
            Account.organization_id == organization_id,
            Account.system_key == "accounts_payable",
        )
        ap_res = await db.execute(ap_stmt)
        ap_acc_id = ap_res.scalar_one()

        tax_acc_id = None
        if bill.tax_amount > Decimal("0.0000"):
            tax_stmt = select(Account.id).where(
                Account.organization_id == organization_id,
                Account.system_key == "tax_payable",
            )
            tax_res = await db.execute(tax_stmt)
            tax_acc_id = tax_res.scalar_one()

        lines_breakdown = [
            (line.expense_account_id, line.line_subtotal, line.description)
            for line in bill.lines
        ]

        jp08_req = await build_jp08_bill_void(
            organization_id=organization_id,
            bill_id=bill.id,
            vendor_id=bill.vendor_id,
            accounting_date=bill.bill_date,
            lines_breakdown=lines_breakdown,
            tax_amount=bill.tax_amount,
            grand_total=bill.total_amount,
            ap_account_id=ap_acc_id,
            tax_account_id=tax_acc_id,
            original_journal_id=bill.posting_journal_id,
            void_reason=void_reason,
            created_by=current_user.id,
            bill_number=bill.bill_number,
        )

        void_journal = await PostingGateway.post_journal(db, jp08_req)

        bill.state = "void"
        bill.void_journal_id = void_journal.id

        await db.flush()
        return bill


    @staticmethod
    async def get_bill(
        db: AsyncSession,
        organization_id: uuid.UUID,
        bill_id: uuid.UUID,
    ) -> Bill:
        """Fetch bill by ID with lines loaded."""
        stmt = (
            select(Bill)
            .where(
                Bill.organization_id == organization_id,
                Bill.id == bill_id,
            )
            .options(selectinload(Bill.lines))
        )
        res = await db.execute(stmt)
        bill = res.scalar_one_or_none()
        if not bill:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bill not found")
        return bill

    @staticmethod
    async def list_bills(
        db: AsyncSession,
        organization_id: uuid.UUID,
        vendor_id: Optional[uuid.UUID] = None,
        state: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Bill]:
        """List bills with optional filters."""
        stmt = (
            select(Bill)
            .where(Bill.organization_id == organization_id)
            .options(selectinload(Bill.lines))
            .order_by(Bill.bill_date.desc(), Bill.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if vendor_id:
            stmt = stmt.where(Bill.vendor_id == vendor_id)
        if state:
            stmt = stmt.where(Bill.state == state)

        res = await db.execute(stmt)
        return list(res.scalars().all())
