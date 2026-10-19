"""Financial Reporting Service - Trial Balance & Sub-Ledger AR Reconciliation."""

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting import Account, JournalLine
from app.models.invoice import Invoice
from app.models.master import Contact
from app.schemas.reports import (
    AccountsReceivableReport,
    CustomerARSummaryRow,
    TrialBalanceAccountRow,
    TrialBalanceReport,
)


class ReportService:
    @staticmethod
    async def generate_trial_balance(
        db: AsyncSession,
        organization_id: uuid.UUID,
        as_of_date: Optional[date] = None,
    ) -> TrialBalanceReport:
        """Derive real-time Trial Balance from journal_lines proving double-entry balance equality."""
        if as_of_date is None:
            as_of_date = date.today()

        stmt = (
            select(
                Account.id,
                Account.code,
                Account.name,
                Account.account_type,
                Account.normal_balance,
                func.coalesce(func.sum(JournalLine.debit), Decimal("0.0000")).label("total_debit"),
                func.coalesce(func.sum(JournalLine.credit), Decimal("0.0000")).label("total_credit"),
            )
            .outerjoin(
                JournalLine,
                (Account.organization_id == JournalLine.organization_id)
                & (Account.id == JournalLine.account_id)
                & (JournalLine.accounting_date <= as_of_date),
            )
            .where(Account.organization_id == organization_id)
            .group_by(
                Account.id,
                Account.code,
                Account.name,
                Account.account_type,
                Account.normal_balance,
            )
            .order_by(Account.code)
        )

        res = await db.execute(stmt)
        rows = res.all()

        account_rows: List[TrialBalanceAccountRow] = []
        grand_debit = Decimal("0.0000")
        grand_credit = Decimal("0.0000")

        for r in rows:
            deb = Decimal(str(r.total_debit))
            cred = Decimal(str(r.total_credit))

            if r.normal_balance == "debit":
                bal = deb - cred
            else:
                bal = cred - deb

            account_rows.append(
                TrialBalanceAccountRow(
                    account_id=r.id,
                    code=r.code,
                    name=r.name,
                    account_type=r.account_type,
                    normal_balance=r.normal_balance,
                    debit=deb,
                    credit=cred,
                    balance=bal,
                )
            )

            grand_debit += deb
            grand_credit += cred

        is_balanced = grand_debit == grand_credit

        return TrialBalanceReport(
            as_of_date=as_of_date,
            total_debits=grand_debit,
            total_credits=grand_credit,
            is_balanced=is_balanced,
            accounts=account_rows,
        )

    @staticmethod
    async def generate_ar_report(
        db: AsyncSession,
        organization_id: uuid.UUID,
        as_of_date: Optional[date] = None,
    ) -> AccountsReceivableReport:
        """Derive Accounts Receivable sub-ledger report and reconcile to General Ledger AR balance."""
        if as_of_date is None:
            as_of_date = date.today()

        # 1. Authoritative GL Balance for Accounts Receivable
        gl_stmt = (
            select(
                func.coalesce(
                    func.sum(JournalLine.debit) - func.sum(JournalLine.credit),
                    Decimal("0.0000"),
                )
            )
            .join(
                Account,
                (Account.organization_id == JournalLine.organization_id)
                & (Account.id == JournalLine.account_id),
            )
            .where(
                JournalLine.organization_id == organization_id,
                Account.system_key == "accounts_receivable",
                JournalLine.accounting_date <= as_of_date,
            )
        )
        gl_res = await db.execute(gl_stmt)
        gl_balance = Decimal(str(gl_res.scalar() or "0.0000"))

        # 2. Query Sub-ledger Invoices (Issued invoices)
        inv_stmt = (
            select(
                Contact.id.label("customer_id"),
                Contact.display_name.label("customer_name"),
                Invoice.id.label("invoice_id"),
                Invoice.due_date,
                Invoice.total_amount,
                Invoice.settled_amount,
            )
            .join(
                Contact,
                (Contact.organization_id == Invoice.organization_id)
                & (Contact.id == Invoice.customer_id),
            )
            .where(
                Invoice.organization_id == organization_id,
                Invoice.state == "issued",
                Invoice.invoice_date <= as_of_date,
            )
            .order_by(Contact.display_name, Invoice.due_date)
        )
        inv_res = await db.execute(inv_stmt)
        invoices = inv_res.all()

        # 3. Group by customer and compute aging buckets
        customers: Dict[uuid.UUID, CustomerARSummaryRow] = {}
        total_outstanding = Decimal("0.0000")

        for inv in invoices:
            cust_id = inv.customer_id
            tot = Decimal(str(inv.total_amount))
            setl = Decimal(str(inv.settled_amount))
            outstanding = tot - setl

            if outstanding <= Decimal("0.0000"):
                continue

            total_outstanding += outstanding

            if cust_id not in customers:
                customers[cust_id] = CustomerARSummaryRow(
                    customer_id=cust_id,
                    customer_name=inv.customer_name,
                    total_invoiced=Decimal("0.0000"),
                    total_settled=Decimal("0.0000"),
                    outstanding_balance=Decimal("0.0000"),
                    current_amount=Decimal("0.0000"),
                    overdue_1_30=Decimal("0.0000"),
                    overdue_31_60=Decimal("0.0000"),
                    overdue_61_90=Decimal("0.0000"),
                    overdue_90_plus=Decimal("0.0000"),
                )

            row = customers[cust_id]
            row.total_invoiced += tot
            row.total_settled += setl
            row.outstanding_balance += outstanding

            if inv.due_date >= as_of_date:
                row.current_amount += outstanding
            else:
                days_overdue = (as_of_date - inv.due_date).days
                if days_overdue <= 30:
                    row.overdue_1_30 += outstanding
                elif days_overdue <= 60:
                    row.overdue_31_60 += outstanding
                elif days_overdue <= 90:
                    row.overdue_61_90 += outstanding
                else:
                    row.overdue_90_plus += outstanding

        is_reconciled = total_outstanding == gl_balance

        return AccountsReceivableReport(
            as_of_date=as_of_date,
            total_outstanding=total_outstanding,
            gl_control_account_balance=gl_balance,
            is_reconciled=is_reconciled,
            customers=list(customers.values()),
        )
