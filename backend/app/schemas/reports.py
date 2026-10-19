"""Financial Reporting Pydantic Schemas."""

from datetime import date
from decimal import Decimal
from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict


class TrialBalanceAccountRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    code: str
    name: str
    account_type: str
    normal_balance: str
    debit: Decimal
    credit: Decimal
    balance: Decimal


class TrialBalanceReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    as_of_date: date
    total_debits: Decimal
    total_credits: Decimal
    is_balanced: bool
    accounts: List[TrialBalanceAccountRow]


class CustomerARSummaryRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_id: uuid.UUID
    customer_name: str
    total_invoiced: Decimal
    total_settled: Decimal
    outstanding_balance: Decimal
    current_amount: Decimal
    overdue_1_30: Decimal
    overdue_31_60: Decimal
    overdue_61_90: Decimal
    overdue_90_plus: Decimal


class AccountsReceivableReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    as_of_date: date
    total_outstanding: Decimal
    gl_control_account_balance: Decimal
    is_reconciled: bool
    customers: List[CustomerARSummaryRow]


# ---------------------------------------------------------------------------
# General Ledger Report
# ---------------------------------------------------------------------------

class GeneralLedgerLineRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    accounting_date: date
    journal_id: uuid.UUID
    source_type: str
    source_id: Optional[uuid.UUID] = None
    memo: Optional[str] = None
    description: Optional[str] = None
    contact_id: Optional[uuid.UUID] = None
    contact_name: Optional[str] = None
    debit: Decimal
    credit: Decimal
    running_balance: Decimal


class GeneralLedgerAccountReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    code: str
    name: str
    account_type: str
    normal_balance: str
    opening_balance: Decimal
    closing_balance: Decimal
    total_debits: Decimal
    total_credits: Decimal
    lines: List[GeneralLedgerLineRow] = []


class GeneralLedgerReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_date: date
    to_date: date
    accounts: List[GeneralLedgerAccountReport] = []


# ---------------------------------------------------------------------------
# Profit and Loss Report
# ---------------------------------------------------------------------------

class FinancialReportAccountItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    code: str
    name: str
    amount: Decimal


class ProfitAndLossReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_date: date
    to_date: date
    income_accounts: List[FinancialReportAccountItem] = []
    total_income: Decimal
    expense_accounts: List[FinancialReportAccountItem] = []
    total_expense: Decimal
    net_profit: Decimal


# ---------------------------------------------------------------------------
# Balance Sheet Report
# ---------------------------------------------------------------------------

class BalanceSheetReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    as_of_date: date
    asset_accounts: List[FinancialReportAccountItem] = []
    total_assets: Decimal
    liability_accounts: List[FinancialReportAccountItem] = []
    total_liabilities: Decimal
    equity_accounts: List[FinancialReportAccountItem] = []
    current_year_earnings: Decimal
    total_equity: Decimal
    total_liabilities_and_equity: Decimal
    is_balanced: bool


# ---------------------------------------------------------------------------
# Accounts Payable Ageing Report
# ---------------------------------------------------------------------------

class VendorAPSummaryRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vendor_id: uuid.UUID
    vendor_name: str
    total_billed: Decimal
    total_paid: Decimal
    outstanding_balance: Decimal
    current_amount: Decimal
    overdue_1_30: Decimal
    overdue_31_60: Decimal
    overdue_61_90: Decimal
    overdue_90_plus: Decimal


class AccountsPayableReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    as_of_date: date
    total_outstanding: Decimal
    gl_control_account_balance: Decimal
    is_reconciled: bool
    vendors: List[VendorAPSummaryRow] = []

