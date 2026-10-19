"""Sales Credit Note Service - Drafts, Issuance (JP-05), and Void Reversal (JP-06)."""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.accounting import Account, TaxRate
from app.models.credit_note import CreditNote, CreditNoteLine
from app.models.identity import User
from app.models.invoice import Invoice
from app.models.master import Contact, Item
from app.schemas.credit_note import CreditNoteCreate, CreditNoteLineCreate
from app.services.posting_gateway import (
    PostingGateway,
    build_jp05_credit_note_issuance,
    build_jp06_credit_note_void,
)


def quantize_money(amount: Decimal) -> Decimal:
    """Format and round monetary decimal to 4 standard fractional places."""
    return amount.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


class CreditNoteService:
    @staticmethod
    async def create_draft_credit_note(
        db: AsyncSession,
        organization_id: uuid.UUID,
        payload: CreditNoteCreate,
        current_user: User,
    ) -> CreditNote:
        """Create draft credit note linked to an issued customer invoice."""
        # 1. Fetch and Lock Invoice
        inv_stmt = (
            select(Invoice)
            .where(
                Invoice.organization_id == organization_id,
                Invoice.id == payload.invoice_id,
            )
            .with_for_update()
        )
        inv_res = await db.execute(inv_stmt)
        invoice = inv_res.scalar_one_or_none()

        if not invoice:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
        if invoice.state != "issued":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot issue credit note against invoice in '{invoice.state}' state (only issued allowed)",
            )

        # 2. Determine Default Income Account
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

        # 3. Create Credit Note Header
        credit_note = CreditNote(
            organization_id=organization_id,
            invoice_id=invoice.id,
            customer_id=invoice.customer_id,
            state="draft",
            credit_note_date=payload.credit_note_date,
            currency_code=payload.currency_code.upper(),
            reason=payload.reason or "Credit adjustment",
            created_by=current_user.id,
        )
        db.add(credit_note)
        await db.flush()


        # 4. Process Lines & Calculations
        net_total = Decimal("0.0000")
        tax_total = Decimal("0.0000")

        for idx, line_in in enumerate(payload.lines, start=1):
            qty = Decimal(str(line_in.quantity))
            price = Decimal(str(line_in.unit_price))
            subtotal = quantize_money(qty * price)

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

            cn_line = CreditNoteLine(
                organization_id=organization_id,
                credit_note_id=credit_note.id,
                line_no=idx,
                item_id=line_in.item_id,
                description=line_in.description.strip(),
                quantity=qty,
                unit_price=price,
                line_subtotal=subtotal,
                tax_rate_id=tax_rate_id,
                tax_amount=line_tax,
                line_total=line_total,
                income_account_id=income_acc,
            )
            db.add(cn_line)

            net_total += subtotal
            tax_total += line_tax

        grand_total = net_total + tax_total

        # 5. Over-Crediting Protection
        available_to_credit = invoice.total_amount - invoice.credit_noted_amount
        if grand_total > available_to_credit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Credit note total ({grand_total}) exceeds maximum available credit ({available_to_credit}) for this invoice",
            )

        credit_note.net_amount = net_total
        credit_note.tax_amount = tax_total
        credit_note.total_amount = grand_total

        await db.flush()
        return credit_note

    @staticmethod
    async def issue_credit_note(
        db: AsyncSession,
        organization_id: uuid.UUID,
        credit_note_id: uuid.UUID,
        current_user: User,
    ) -> CreditNote:
        """Issue credit note atomically: update invoice credit rollup and emit JP-05 journal."""
        # 1. Fetch and Lock Credit Note
        stmt = (
            select(CreditNote)
            .where(
                CreditNote.organization_id == organization_id,
                CreditNote.id == credit_note_id,
            )
            .options(selectinload(CreditNote.lines))
            .with_for_update()
        )
        res = await db.execute(stmt)
        credit_note = res.scalar_one_or_none()

        if not credit_note:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credit note not found")
        if credit_note.state != "draft":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot issue credit note in '{credit_note.state}' state (only draft allowed)",
            )
        if not credit_note.lines:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot issue a credit note without lines",
            )

        # 2. Lock Invoice and re-verify over-crediting
        inv_stmt = (
            select(Invoice)
            .where(
                Invoice.organization_id == organization_id,
                Invoice.id == credit_note.invoice_id,
            )
            .with_for_update()
        )
        inv_res = await db.execute(inv_stmt)
        invoice = inv_res.scalar_one_or_none()
        if not invoice or invoice.state != "issued":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Associated invoice is missing or no longer issued",
            )

        available_to_credit = invoice.total_amount - invoice.credit_noted_amount
        if credit_note.total_amount > available_to_credit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Credit note total ({credit_note.total_amount}) exceeds remaining allowable credit ({available_to_credit})",
            )

        # 3. Sequential Number
        count_stmt = select(func.count(CreditNote.id)).where(CreditNote.organization_id == organization_id)
        count_res = await db.execute(count_stmt)
        cn_count = (count_res.scalar_one() or 0) + 1
        credit_note_number = f"CN-{cn_count:04d}"

        # 4. Lookup Accounts
        ar_stmt = select(Account.id).where(
            Account.organization_id == organization_id,
            Account.system_key == "accounts_receivable",
        )
        ar_res = await db.execute(ar_stmt)
        ar_acc_id = ar_res.scalar_one_or_none()

        sales_stmt = select(Account.id).where(
            Account.organization_id == organization_id,
            Account.system_key == "sales_income",
        )
        sales_res = await db.execute(sales_stmt)
        sales_acc_id = sales_res.scalar_one_or_none()

        tax_acc_id = None
        if credit_note.tax_amount > Decimal("0.0000"):
            tax_stmt = select(Account.id).where(
                Account.organization_id == organization_id,
                Account.system_key == "tax_payable",
            )
            tax_res = await db.execute(tax_stmt)
            tax_acc_id = tax_res.scalar_one_or_none()

        # 5. Snapshot
        snapshot = {
            "version": 1,
            "issued_at": datetime.utcnow().isoformat(),
            "credit_note_number": credit_note_number,
            "invoice_id": str(invoice.id),
            "invoice_number": invoice.invoice_number,
            "customer_id": str(credit_note.customer_id),
            "net_amount": str(credit_note.net_amount),
            "tax_amount": str(credit_note.tax_amount),
            "total_amount": str(credit_note.total_amount),
        }

        # 6. Post JP-05 Journal
        jp05_req = await build_jp05_credit_note_issuance(
            organization_id=organization_id,
            credit_note_id=credit_note.id,
            customer_id=credit_note.customer_id,
            accounting_date=credit_note.credit_note_date,
            net_amount=credit_note.net_amount,
            tax_amount=credit_note.tax_amount,
            grand_total=credit_note.total_amount,
            ar_account_id=ar_acc_id,
            sales_account_id=sales_acc_id,
            tax_account_id=tax_acc_id,
            created_by=current_user.id,
            credit_note_number=credit_note_number,
        )
        journal = await PostingGateway.post_journal(db, jp05_req)

        # 7. Update Credit Note and Invoice
        credit_note.state = "issued"
        credit_note.credit_note_number = credit_note_number
        credit_note.issue_journal_id = journal.id

        invoice.credit_noted_amount += credit_note.total_amount

        await db.flush()
        return credit_note


    @staticmethod
    async def void_credit_note(
        db: AsyncSession,
        organization_id: uuid.UUID,
        credit_note_id: uuid.UUID,
        void_reason: str,
        current_user: User,
    ) -> CreditNote:
        """Void an issued credit note and emit JP-06 reversal journal."""
        stmt = (
            select(CreditNote)
            .where(
                CreditNote.organization_id == organization_id,
                CreditNote.id == credit_note_id,
            )
            .options(selectinload(CreditNote.lines))
            .with_for_update()
        )
        res = await db.execute(stmt)
        credit_note = res.scalar_one_or_none()

        if not credit_note:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credit note not found")
        if credit_note.state != "issued":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot void credit note in '{credit_note.state}' state (only issued allowed)",
            )
        if credit_note.refunded_amount > Decimal("0.0000"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot void credit note with active refunds",
            )

        # Lock Invoice to reduce credit_noted_amount
        inv_stmt = (
            select(Invoice)
            .where(
                Invoice.organization_id == organization_id,
                Invoice.id == credit_note.invoice_id,
            )
            .with_for_update()
        )
        inv_res = await db.execute(inv_stmt)
        invoice = inv_res.scalar_one_or_none()

        ar_stmt = select(Account.id).where(
            Account.organization_id == organization_id,
            Account.system_key == "accounts_receivable",
        )
        ar_res = await db.execute(ar_stmt)
        ar_acc_id = ar_res.scalar_one()

        sales_stmt = select(Account.id).where(
            Account.organization_id == organization_id,
            Account.system_key == "sales_income",
        )
        sales_res = await db.execute(sales_stmt)
        sales_acc_id = sales_res.scalar_one()

        tax_acc_id = None
        if credit_note.tax_amount > Decimal("0.0000"):
            tax_stmt = select(Account.id).where(
                Account.organization_id == organization_id,
                Account.system_key == "tax_payable",
            )
            tax_res = await db.execute(tax_stmt)
            tax_acc_id = tax_res.scalar_one()

        jp06_req = await build_jp06_credit_note_void(
            organization_id=organization_id,
            credit_note_id=credit_note.id,
            customer_id=credit_note.customer_id,
            accounting_date=credit_note.credit_note_date,
            net_amount=credit_note.net_amount,
            tax_amount=credit_note.tax_amount,
            grand_total=credit_note.total_amount,
            ar_account_id=ar_acc_id,
            sales_account_id=sales_acc_id,
            tax_account_id=tax_acc_id,
            original_journal_id=credit_note.issue_journal_id,
            void_reason=void_reason,
            created_by=current_user.id,
            credit_note_number=credit_note.credit_note_number,
        )
        rev_journal = await PostingGateway.post_journal(db, jp06_req)

        credit_note.state = "void"
        credit_note.void_journal_id = rev_journal.id

        if invoice:
            invoice.credit_noted_amount -= credit_note.total_amount

        await db.flush()
        return credit_note


    @staticmethod
    async def get_credit_note(
        db: AsyncSession,
        organization_id: uuid.UUID,
        credit_note_id: uuid.UUID,
    ) -> CreditNote:
        """Fetch credit note by ID with lines loaded."""
        stmt = (
            select(CreditNote)
            .where(
                CreditNote.organization_id == organization_id,
                CreditNote.id == credit_note_id,
            )
            .options(selectinload(CreditNote.lines))
        )
        res = await db.execute(stmt)
        credit_note = res.scalar_one_or_none()
        if not credit_note:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credit note not found")
        return credit_note

    @staticmethod
    async def list_credit_notes(
        db: AsyncSession,
        organization_id: uuid.UUID,
        customer_id: Optional[uuid.UUID] = None,
        invoice_id: Optional[uuid.UUID] = None,
        state: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[CreditNote]:
        """List credit notes with optional filters."""
        stmt = (
            select(CreditNote)
            .where(CreditNote.organization_id == organization_id)
            .options(selectinload(CreditNote.lines))
            .order_by(CreditNote.credit_note_date.desc(), CreditNote.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if customer_id:
            stmt = stmt.where(CreditNote.customer_id == customer_id)
        if invoice_id:
            stmt = stmt.where(CreditNote.invoice_id == invoice_id)
        if state:
            stmt = stmt.where(CreditNote.state == state)

        res = await db.execute(stmt)
        return list(res.scalars().all())
