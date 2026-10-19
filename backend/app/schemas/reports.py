"""Financial Reporting Pydantic Schemas."""

from datetime import date
from decimal import Decimal
from typing import List
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
