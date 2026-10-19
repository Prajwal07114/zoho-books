"""Purchase Bill Pydantic Schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class BillLineCreate(BaseModel):
    item_id: Optional[uuid.UUID] = None
    description: str = Field(min_length=1)
    quantity: Decimal = Field(gt=Decimal("0"))
    unit_price: Decimal = Field(ge=Decimal("0"))
    tax_rate_id: Optional[uuid.UUID] = None
    expense_account_id: Optional[uuid.UUID] = None


class BillCreate(BaseModel):
    vendor_id: uuid.UUID
    bill_date: date
    due_date: date
    vendor_invoice_number: Optional[str] = None
    currency_code: str = Field(default="USD", min_length=3, max_length=3)
    notes: Optional[str] = None
    terms: Optional[str] = None
    lines: List[BillLineCreate] = Field(min_length=1)


class BillLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    line_no: int
    item_id: Optional[uuid.UUID]
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_subtotal: Decimal
    tax_rate_id: Optional[uuid.UUID]
    tax_amount: Decimal
    line_total: Decimal
    expense_account_id: uuid.UUID


class BillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vendor_id: uuid.UUID
    state: str
    bill_number: Optional[str]
    vendor_invoice_number: Optional[str]
    bill_date: date
    due_date: date
    currency_code: str
    net_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    paid_amount: Optional[Decimal] = None
    settled_amount: Optional[Decimal] = None
    notes: Optional[str] = None
    terms: Optional[str] = None
    snapshot: Optional[dict] = None
    posted_at: Optional[datetime] = None
    post_journal_id: Optional[uuid.UUID] = None
    posting_journal_id: Optional[uuid.UUID] = None
    voided_at: Optional[datetime] = None
    void_reason: Optional[str] = None
    void_journal_id: Optional[uuid.UUID] = None
    lines: List[BillLineResponse] = []

    created_at: datetime


class BillVoidRequest(BaseModel):
    reason: str = Field(min_length=3)
