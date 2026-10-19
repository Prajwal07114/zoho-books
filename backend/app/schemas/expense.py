"""Direct Business Expense Pydantic Schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class ExpenseCreate(BaseModel):
    expense_date: date
    expense_account_id: uuid.UUID
    payment_account_id: Optional[uuid.UUID] = None  # defaults to bank/cash
    vendor_id: Optional[uuid.UUID] = None
    tax_rate_id: Optional[uuid.UUID] = None
    net_amount: Decimal = Field(gt=Decimal("0"))
    currency_code: str = Field(default="USD", min_length=3, max_length=3)
    payment_method: Optional[str] = "bank_transfer"
    reference: Optional[str] = None
    description: Optional[str] = Field(min_length=1)
    receipt_url: Optional[str] = None


class ExpenseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    expense_number: Optional[str] = None
    expense_date: date
    vendor_id: Optional[uuid.UUID] = None
    expense_account_id: uuid.UUID
    payment_account_id: uuid.UUID
    tax_rate_id: Optional[uuid.UUID] = None
    net_amount: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    tax_amount: Decimal = Decimal("0.0000")
    total_amount: Optional[Decimal] = None
    currency_code: Optional[str] = "USD"
    state: Optional[str] = "posted"
    payment_status: Optional[str] = "posted"
    payment_method: Optional[str] = None
    reference: Optional[str] = None
    description: Optional[str] = None
    receipt_url: Optional[str] = None
    journal_id: Optional[uuid.UUID] = None
    posting_journal_id: Optional[uuid.UUID] = None
    voided_at: Optional[datetime] = None
    void_reason: Optional[str] = None
    void_journal_id: Optional[uuid.UUID] = None
    created_at: datetime



class ExpenseVoidRequest(BaseModel):
    reason: str = Field(min_length=3)
