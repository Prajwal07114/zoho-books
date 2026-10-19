"""Sales Invoice Service - Calculations, Numbering, Snapshotting, Issuance (JP-01), and Voiding (JP-02)."""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.accounting import Account, TaxRate
from app.models.identity import User
from app.models.invoice import Invoice, InvoiceLine
from app.models.master import Contact, Item
from app.models.organization import Organization, OrganizationSettings
from app.schemas.invoice import InvoiceCreate, InvoiceResponse, InvoiceLineResponse
from app.services.posting_gateway import (
    PostingGateway,
    PostingLine,
    PostingRequest,
    build_jp01_invoice_issuance,
    build_jp02_invoice_void,
)


def quantize_money(amount: Decimal) -> Decimal:
    """Format and round monetary decimal to 4 standard fractional places."""
    return amount.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


class InvoiceService:
    @staticmethod
    async def create_draft_invoice(
        db: AsyncSession,
        organization_id: uuid.UUID,
        payload: InvoiceCreate,
        current_user: User,
    ) -> Invoice:
        """Create a new sales invoice in draft state with deterministic line calculations."""
        # 1. Validate customer exists and belongs to active organization
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
                detail="Customer not found, inactive, or not configured as a customer",
            )

        # 2. Get default sales income account
        def_acc_stmt = select(Account.id).where(
            Account.organization_id == organization_id,
            Account.system_key == "sales_income",
        )
        def_acc_res = await db.execute(def_acc_stmt)
        default_income_acc_id = def_acc_res.scalar_one_or_none()
        if not default_income_acc_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Default sales income account (system key: sales_income) not found",
            )

        # 3. Create Invoice Header
        invoice = Invoice(
            organization_id=organization_id,
            customer_id=payload.customer_id,
            state="draft",
            invoice_date=payload.invoice_date,
            due_date=payload.due_date,
            currency_code=payload.currency_code.upper(),
            notes=payload.notes,
            terms=payload.terms,
        )
        db.add(invoice)
        await db.flush()

        # 4. Process Lines & Deterministic Calculations
        net_total = Decimal("0.0000")
        tax_total = Decimal("0.0000")

        for idx, line_in in enumerate(payload.lines, start=1):
            qty = Decimal(str(line_in.quantity))
            price = Decimal(str(line_in.unit_price))
            subtotal = quantize_money(qty * price)

            # Determine Income Account
            income_acc = line_in.income_account_id
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
                    if not income_acc and item.income_account_id:
                        income_acc = item.income_account_id
                    if not tax_rate_id and item.default_tax_rate_id:
                        tax_rate_id = item.default_tax_rate_id

            if not income_acc:
                income_acc = default_income_acc_id

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
                    line_tax = quantize_money(subtotal * (tax_rate.rate_percent / Decimal("100")))

            line_total = subtotal + line_tax

            inv_line = InvoiceLine(
                organization_id=organization_id,
                invoice_id=invoice.id,
                line_no=idx,
                item_id=line_in.item_id,
                description=line_in.description,
                quantity=qty,
                unit_price=price,
                line_subtotal=subtotal,
                tax_rate_id=tax_rate_id,
                tax_amount=line_tax,
                line_total=line_total,
                income_account_id=income_acc,
            )
            db.add(inv_line)

            net_total += subtotal
            tax_total += line_tax

        # Update Header Totals
        invoice.net_amount = net_total
        invoice.tax_amount = tax_total
        invoice.total_amount = net_total + tax_total

        await db.flush()
        return invoice

    @staticmethod
    async def issue_invoice(
        db: AsyncSession,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        current_user: User,
    ) -> Invoice:
        """Issue invoice atomically: assign number, freeze snapshot, and emit JP-01 journal."""
        # 1. Fetch and Lock Invoice
        stmt = (
            select(Invoice)
            .where(
                Invoice.organization_id == organization_id,
                Invoice.id == invoice_id,
            )
            .options(selectinload(Invoice.lines))
            .with_for_update()
        )
        res = await db.execute(stmt)
        invoice = res.scalar_one_or_none()

        if not invoice:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
        if invoice.state != "draft":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot issue invoice in {invoice.state} state (only draft allowed)",
            )
        if not invoice.lines:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot issue an invoice without lines",
            )

        # 2. Lock and retrieve sequential invoice number
        sett_stmt = (
            select(OrganizationSettings)
            .where(OrganizationSettings.organization_id == organization_id)
            .with_for_update()
        )
        sett_res = await db.execute(sett_stmt)
        settings = sett_res.scalar_one_or_none()
        if not settings:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Organization settings not found",
            )

        invoice_number = f"{settings.invoice_prefix}{settings.next_invoice_number:04d}"
        settings.next_invoice_number += 1

        # 3. Fetch Customer Details for Snapshot
        cust_stmt = select(Contact).where(
            Contact.organization_id == organization_id,
            Contact.id == invoice.customer_id,
        )
        cust_res = await db.execute(cust_stmt)
        customer = cust_res.scalar_one()

        # 4. Build Immutable Snapshot JSON
        snapshot = {
            "version": 1,
            "issued_at": datetime.utcnow().isoformat(),
            "customer": {
                "id": str(customer.id),
                "display_name": customer.display_name,
                "email": customer.email,
                "phone": customer.phone,
            },
            "invoice_number": invoice_number,
            "invoice_date": invoice.invoice_date.isoformat(),
            "due_date": invoice.due_date.isoformat(),
            "currency_code": invoice.currency_code,
            "net_amount": str(invoice.net_amount),
            "tax_amount": str(invoice.tax_amount),
            "total_amount": str(invoice.total_amount),
            "lines": [
                {
                    "line_no": line.line_no,
                    "description": line.description,
                    "quantity": str(line.quantity),
                    "unit_price": str(line.unit_price),
                    "line_subtotal": str(line.line_subtotal),
                    "tax_amount": str(line.tax_amount),
                    "line_total": str(line.line_total),
                }
                for line in invoice.lines
            ],
        }

        # 5. Lookup System Accounts for JP-01
        ar_stmt = select(Account.id).where(
            Account.organization_id == organization_id,
            Account.system_key == "accounts_receivable",
        )
        ar_res = await db.execute(ar_stmt)
        ar_acc_id = ar_res.scalar_one()

        tax_acc_id = None
        if invoice.tax_amount > Decimal("0.0000"):
            tax_stmt = select(Account.id).where(
                Account.organization_id == organization_id,
                Account.system_key == "tax_payable",
            )
            tax_res = await db.execute(tax_stmt)
            tax_acc_id = tax_res.scalar_one()

        # Build posting lines for JP-01
        # Line 1: Debit AR for total_amount
        posting_lines = [
            PostingLine(
                account_id=ar_acc_id,
                debit=invoice.total_amount,
                credit=Decimal("0.0000"),
                contact_id=invoice.customer_id,
                description=f"Invoice {invoice_number} Accounts Receivable",
            )
        ]

        # Lines 2..N: Credit income accounts per line
        # Group subtotal by income_account_id
        income_map: dict[uuid.UUID, Decimal] = {}
        for line in invoice.lines:
            income_map[line.income_account_id] = income_map.get(line.income_account_id, Decimal("0.0000")) + line.line_subtotal

        for acc_id, subtotal in income_map.items():
            posting_lines.append(
                PostingLine(
                    account_id=acc_id,
                    debit=Decimal("0.0000"),
                    credit=subtotal,
                    contact_id=invoice.customer_id,
                    description=f"Invoice {invoice_number} Sales Revenue",
                )
            )

        # Line for Tax (if > 0)
        if invoice.tax_amount > Decimal("0.0000"):
            posting_lines.append(
                PostingLine(
                    account_id=tax_acc_id,
                    debit=Decimal("0.0000"),
                    credit=invoice.tax_amount,
                    contact_id=invoice.customer_id,
                    description=f"Invoice {invoice_number} Tax Payable",
                )
            )

        posting_req = PostingRequest(
            organization_id=organization_id,
            accounting_date=invoice.invoice_date,
            source_type="invoice",
            source_id=invoice.id,
            memo=f"Invoice issuance: {invoice_number}",
            created_by=current_user.id,
            lines=posting_lines,
        )

        # 6. Post Journal JP-01
        journal = await PostingGateway.post_journal(db, posting_req)

        # 7. Update Invoice State to Issued
        invoice.state = "issued"
        invoice.invoice_number = invoice_number
        invoice.snapshot = snapshot
        invoice.issued_at = datetime.utcnow()
        invoice.issued_by = current_user.id
        invoice.issue_journal_id = journal.id

        await db.flush()
        return invoice

    @staticmethod
    async def void_invoice(
        db: AsyncSession,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        void_reason: str,
        current_user: User,
    ) -> Invoice:
        """Void an issued invoice: verify zero settlements, and post JP-02 reversal journal."""
        # 1. Fetch and Lock Invoice
        stmt = (
            select(Invoice)
            .where(
                Invoice.organization_id == organization_id,
                Invoice.id == invoice_id,
            )
            .options(selectinload(Invoice.lines))
            .with_for_update()
        )
        res = await db.execute(stmt)
        invoice = res.scalar_one_or_none()

        if not invoice:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
        if invoice.state != "issued":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot void an invoice in {invoice.state} state (only issued allowed)",
            )
        if invoice.settled_amount > Decimal("0.0000"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot void invoice with settled payments (settled: {invoice.settled_amount})",
            )

        # 2. Lookup System Accounts
        ar_stmt = select(Account.id).where(
            Account.organization_id == organization_id,
            Account.system_key == "accounts_receivable",
        )
        ar_res = await db.execute(ar_stmt)
        ar_acc_id = ar_res.scalar_one()

        tax_acc_id = None
        if invoice.tax_amount > Decimal("0.0000"):
            tax_stmt = select(Account.id).where(
                Account.organization_id == organization_id,
                Account.system_key == "tax_payable",
            )
            tax_res = await db.execute(tax_stmt)
            tax_acc_id = tax_res.scalar_one()

        # 3. Build JP-02 Reversal Posting Lines (Exact Inverse of JP-01)
        posting_lines = []

        # Debit income accounts per line
        income_map: dict[uuid.UUID, Decimal] = {}
        for line in invoice.lines:
            income_map[line.income_account_id] = income_map.get(line.income_account_id, Decimal("0.0000")) + line.line_subtotal

        for acc_id, subtotal in income_map.items():
            posting_lines.append(
                PostingLine(
                    account_id=acc_id,
                    debit=subtotal,
                    credit=Decimal("0.0000"),
                    contact_id=invoice.customer_id,
                    description=f"Void {invoice.invoice_number} Revenue Reversal",
                )
            )

        # Debit tax account (if tax > 0)
        if invoice.tax_amount > Decimal("0.0000"):
            posting_lines.append(
                PostingLine(
                    account_id=tax_acc_id,
                    debit=invoice.tax_amount,
                    credit=Decimal("0.0000"),
                    contact_id=invoice.customer_id,
                    description=f"Void {invoice.invoice_number} Tax Reversal",
                )
            )

        # Credit Accounts Receivable for total_amount
        posting_lines.append(
            PostingLine(
                account_id=ar_acc_id,
                debit=Decimal("0.0000"),
                credit=invoice.total_amount,
                contact_id=invoice.customer_id,
                description=f"Void {invoice.invoice_number} Receivable Reversal",
            )
        )

        posting_req = PostingRequest(
            organization_id=organization_id,
            accounting_date=datetime.utcnow().date(),
            source_type="invoice_void",
            source_id=invoice.id,
            memo=f"Void invoice: {invoice.invoice_number}",
            reverses_journal_id=invoice.issue_journal_id,
            reason=void_reason,
            created_by=current_user.id,
            lines=posting_lines,
        )

        # 4. Post Reversal Journal JP-02
        rev_journal = await PostingGateway.post_journal(db, posting_req)

        # 5. Update Invoice State to Void
        invoice.state = "void"
        invoice.voided_at = datetime.utcnow()
        invoice.voided_by = current_user.id
        invoice.void_reason = void_reason
        invoice.void_journal_id = rev_journal.id

        await db.flush()
        return invoice
