"""Financial Reporting Service - Trial Balance, AR & AP Subledger Reconciliation, GL, P&L, and Balance Sheet."""

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting import Account, Journal, JournalLine
from app.models.bill import Bill
from app.models.invoice import Invoice
from app.models.master import Contact
from app.schemas.reports import (
    AccountsPayableReport,
    AccountsReceivableReport,
    BalanceSheetReport,
    CustomerARSummaryRow,
    FinancialReportAccountItem,
    GeneralLedgerAccountReport,
    GeneralLedgerLineRow,
    GeneralLedgerReport,
    ProfitAndLossReport,
    TrialBalanceAccountRow,
    TrialBalanceReport,
    VendorAPSummaryRow,
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
                Invoice.credit_noted_amount,
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
            credited = Decimal(str(inv.credit_noted_amount or "0.0000"))
            outstanding = tot - setl - credited

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

    @staticmethod
    async def generate_ap_report(
        db: AsyncSession,
        organization_id: uuid.UUID,
        as_of_date: Optional[date] = None,
    ) -> AccountsPayableReport:
        """Derive Accounts Payable sub-ledger report and reconcile to General Ledger AP balance."""
        if as_of_date is None:
            as_of_date = date.today()

        # 1. Authoritative GL Balance for Accounts Payable (Credit - Debit)
        gl_stmt = (
            select(
                func.coalesce(
                    func.sum(JournalLine.credit) - func.sum(JournalLine.debit),
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
                Account.system_key == "accounts_payable",
                JournalLine.accounting_date <= as_of_date,
            )
        )
        gl_res = await db.execute(gl_stmt)
        gl_balance = Decimal(str(gl_res.scalar() or "0.0000"))

        # 2. Query Sub-ledger Bills (Posted bills)
        bill_stmt = (
            select(
                Contact.id.label("vendor_id"),
                Contact.display_name.label("vendor_name"),
                Bill.id.label("bill_id"),
                Bill.due_date,
                Bill.total_amount,
                Bill.settled_amount.label("paid_amount"),
            )
            .join(
                Contact,
                (Contact.organization_id == Bill.organization_id)
                & (Contact.id == Bill.vendor_id),
            )
            .where(
                Bill.organization_id == organization_id,
                Bill.state == "posted",
                Bill.bill_date <= as_of_date,
            )
            .order_by(Contact.display_name, Bill.due_date)
        )
        bill_res = await db.execute(bill_stmt)
        bills = bill_res.all()

        # 3. Group by vendor and compute aging buckets
        vendors: Dict[uuid.UUID, VendorAPSummaryRow] = {}
        total_outstanding = Decimal("0.0000")

        for b in bills:
            vend_id = b.vendor_id
            tot = Decimal(str(b.total_amount))
            paid = Decimal(str(b.paid_amount))
            outstanding = tot - paid

            if outstanding <= Decimal("0.0000"):
                continue

            total_outstanding += outstanding

            if vend_id not in vendors:
                vendors[vend_id] = VendorAPSummaryRow(
                    vendor_id=vend_id,
                    vendor_name=b.vendor_name,
                    total_billed=Decimal("0.0000"),
                    total_paid=Decimal("0.0000"),
                    outstanding_balance=Decimal("0.0000"),
                    current_amount=Decimal("0.0000"),
                    overdue_1_30=Decimal("0.0000"),
                    overdue_31_60=Decimal("0.0000"),
                    overdue_61_90=Decimal("0.0000"),
                    overdue_90_plus=Decimal("0.0000"),
                )

            row = vendors[vend_id]
            row.total_billed += tot
            row.total_paid += paid
            row.outstanding_balance += outstanding

            if b.due_date >= as_of_date:
                row.current_amount += outstanding
            else:
                days_overdue = (as_of_date - b.due_date).days
                if days_overdue <= 30:
                    row.overdue_1_30 += outstanding
                elif days_overdue <= 60:
                    row.overdue_31_60 += outstanding
                elif days_overdue <= 90:
                    row.overdue_61_90 += outstanding
                else:
                    row.overdue_90_plus += outstanding

        is_reconciled = total_outstanding == gl_balance

        return AccountsPayableReport(
            as_of_date=as_of_date,
            total_outstanding=total_outstanding,
            gl_control_account_balance=gl_balance,
            is_reconciled=is_reconciled,
            vendors=list(vendors.values()),
        )

    @staticmethod
    async def generate_general_ledger(
        db: AsyncSession,
        organization_id: uuid.UUID,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        account_id: Optional[uuid.UUID] = None,
    ) -> GeneralLedgerReport:
        """Derive General Ledger transactions, opening balances, running balances, and totals."""
        if to_date is None:
            to_date = date.today()
        if from_date is None:
            from_date = date(to_date.year, 1, 1)

        # 1. Fetch Accounts
        acc_stmt = select(Account).where(Account.organization_id == organization_id)
        if account_id:
            acc_stmt = acc_stmt.where(Account.id == account_id)
        acc_stmt = acc_stmt.order_by(Account.code)
        acc_res = await db.execute(acc_stmt)
        accounts = acc_res.scalars().all()

        reports: List[GeneralLedgerAccountReport] = []

        for acc in accounts:
            # Calculate opening balance prior to from_date
            op_stmt = select(
                func.coalesce(func.sum(JournalLine.debit), Decimal("0.0000")).label("deb"),
                func.coalesce(func.sum(JournalLine.credit), Decimal("0.0000")).label("cred"),
            ).where(
                JournalLine.organization_id == organization_id,
                JournalLine.account_id == acc.id,
                JournalLine.accounting_date < from_date,
            )
            op_res = await db.execute(op_stmt)
            op_row = op_res.one()
            op_deb = Decimal(str(op_row.deb))
            op_cred = Decimal(str(op_row.cred))

            if acc.normal_balance == "debit":
                opening_balance = op_deb - op_cred
            else:
                opening_balance = op_cred - op_deb

            # Fetch transactions within date range
            lines_stmt = (
                select(
                    JournalLine.accounting_date,
                    JournalLine.journal_id,
                    Journal.source_type,
                    Journal.source_id,
                    Journal.memo,
                    JournalLine.description,
                    JournalLine.contact_id,
                    Contact.display_name.label("contact_name"),
                    JournalLine.debit,
                    JournalLine.credit,
                )
                .join(Journal, Journal.id == JournalLine.journal_id)
                .outerjoin(Contact, Contact.id == JournalLine.contact_id)
                .where(
                    JournalLine.organization_id == organization_id,
                    JournalLine.account_id == acc.id,
                    JournalLine.accounting_date >= from_date,
                    JournalLine.accounting_date <= to_date,
                )
                .order_by(JournalLine.accounting_date, JournalLine.created_at, JournalLine.line_no)
            )
            lines_res = await db.execute(lines_stmt)
            lines = lines_res.all()

            running_balance = opening_balance
            total_debits = Decimal("0.0000")
            total_credits = Decimal("0.0000")
            gl_lines: List[GeneralLedgerLineRow] = []

            for ln in lines:
                deb = Decimal(str(ln.debit))
                cred = Decimal(str(ln.credit))

                total_debits += deb
                total_credits += cred

                if acc.normal_balance == "debit":
                    running_balance += deb - cred
                else:
                    running_balance += cred - deb

                gl_lines.append(
                    GeneralLedgerLineRow(
                        accounting_date=ln.accounting_date,
                        journal_id=ln.journal_id,
                        source_type=ln.source_type,
                        source_id=ln.source_id,
                        memo=ln.memo,
                        description=ln.description,
                        contact_id=ln.contact_id,
                        contact_name=ln.contact_name,
                        debit=deb,
                        credit=cred,
                        running_balance=running_balance,
                    )
                )

            closing_balance = running_balance

            reports.append(
                GeneralLedgerAccountReport(
                    account_id=acc.id,
                    code=acc.code,
                    name=acc.name,
                    account_type=acc.account_type,
                    normal_balance=acc.normal_balance,
                    opening_balance=opening_balance,
                    closing_balance=closing_balance,
                    total_debits=total_debits,
                    total_credits=total_credits,
                    lines=gl_lines,
                )
            )

        return GeneralLedgerReport(
            from_date=from_date,
            to_date=to_date,
            accounts=reports,
        )

    @staticmethod
    async def generate_profit_and_loss(
        db: AsyncSession,
        organization_id: uuid.UUID,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
    ) -> ProfitAndLossReport:
        """Derive Profit and Loss statement for income and expense accounts."""
        if to_date is None:
            to_date = date.today()
        if from_date is None:
            from_date = date(to_date.year, 1, 1)

        stmt = (
            select(
                Account.id,
                Account.code,
                Account.name,
                Account.account_type,
                Account.normal_balance,
                func.coalesce(func.sum(JournalLine.debit), Decimal("0.0000")).label("deb"),
                func.coalesce(func.sum(JournalLine.credit), Decimal("0.0000")).label("cred"),
            )
            .outerjoin(
                JournalLine,
                (Account.organization_id == JournalLine.organization_id)
                & (Account.id == JournalLine.account_id)
                & (JournalLine.accounting_date >= from_date)
                & (JournalLine.accounting_date <= to_date),
            )
            .where(
                Account.organization_id == organization_id,
                Account.account_type.in_(["income", "expense"]),
            )
            .group_by(Account.id, Account.code, Account.name, Account.account_type, Account.normal_balance)
            .order_by(Account.code)
        )
        res = await db.execute(stmt)
        rows = res.all()

        income_accounts: List[FinancialReportAccountItem] = []
        total_income = Decimal("0.0000")
        expense_accounts: List[FinancialReportAccountItem] = []
        total_expense = Decimal("0.0000")

        for r in rows:
            deb = Decimal(str(r.deb))
            cred = Decimal(str(r.cred))

            if r.account_type == "income":
                amt = cred - deb
                if amt != Decimal("0.0000") or r.code == "4010":
                    income_accounts.append(
                        FinancialReportAccountItem(account_id=r.id, code=r.code, name=r.name, amount=amt)
                    )
                    total_income += amt
            elif r.account_type == "expense":
                amt = deb - cred
                if amt != Decimal("0.0000") or r.code == "5010":
                    expense_accounts.append(
                        FinancialReportAccountItem(account_id=r.id, code=r.code, name=r.name, amount=amt)
                    )
                    total_expense += amt

        net_profit = total_income - total_expense

        return ProfitAndLossReport(
            from_date=from_date,
            to_date=to_date,
            income_accounts=income_accounts,
            total_income=total_income,
            expense_accounts=expense_accounts,
            total_expense=total_expense,
            net_profit=net_profit,
        )

    @staticmethod
    async def generate_balance_sheet(
        db: AsyncSession,
        organization_id: uuid.UUID,
        as_of_date: Optional[date] = None,
    ) -> BalanceSheetReport:
        """Derive Balance Sheet statement (Assets = Liabilities + Equity + Net Income)."""
        if as_of_date is None:
            as_of_date = date.today()

        stmt = (
            select(
                Account.id,
                Account.code,
                Account.name,
                Account.account_type,
                Account.normal_balance,
                func.coalesce(func.sum(JournalLine.debit), Decimal("0.0000")).label("deb"),
                func.coalesce(func.sum(JournalLine.credit), Decimal("0.0000")).label("cred"),
            )
            .outerjoin(
                JournalLine,
                (Account.organization_id == JournalLine.organization_id)
                & (Account.id == JournalLine.account_id)
                & (JournalLine.accounting_date <= as_of_date),
            )
            .where(Account.organization_id == organization_id)
            .group_by(Account.id, Account.code, Account.name, Account.account_type, Account.normal_balance)
            .order_by(Account.code)
        )
        res = await db.execute(stmt)
        rows = res.all()

        asset_accounts: List[FinancialReportAccountItem] = []
        total_assets = Decimal("0.0000")
        liability_accounts: List[FinancialReportAccountItem] = []
        total_liabilities = Decimal("0.0000")
        equity_accounts: List[FinancialReportAccountItem] = []
        total_equity = Decimal("0.0000")

        # Track cumulative revenue and expenses for current earnings
        cumulative_income = Decimal("0.0000")
        cumulative_expense = Decimal("0.0000")

        for r in rows:
            deb = Decimal(str(r.deb))
            cred = Decimal(str(r.cred))

            if r.account_type == "asset":
                amt = deb - cred
                asset_accounts.append(FinancialReportAccountItem(account_id=r.id, code=r.code, name=r.name, amount=amt))
                total_assets += amt
            elif r.account_type == "liability":
                amt = cred - deb
                liability_accounts.append(FinancialReportAccountItem(account_id=r.id, code=r.code, name=r.name, amount=amt))
                total_liabilities += amt
            elif r.account_type == "equity":
                amt = cred - deb
                equity_accounts.append(FinancialReportAccountItem(account_id=r.id, code=r.code, name=r.name, amount=amt))
                total_equity += amt
            elif r.account_type == "income":
                cumulative_income += cred - deb
            elif r.account_type == "expense":
                cumulative_expense += deb - cred

        current_year_earnings = cumulative_income - cumulative_expense
        total_equity_with_earnings = total_equity + current_year_earnings
        total_liabilities_and_equity = total_liabilities + total_equity_with_earnings

        is_balanced = total_assets == total_liabilities_and_equity

        return BalanceSheetReport(
            as_of_date=as_of_date,
            asset_accounts=asset_accounts,
            total_assets=total_assets,
            liability_accounts=liability_accounts,
            total_liabilities=total_liabilities,
            equity_accounts=equity_accounts,
            current_year_earnings=current_year_earnings,
            total_equity=total_equity_with_earnings,
            total_liabilities_and_equity=total_liabilities_and_equity,
            is_balanced=is_balanced,
        )
