"""Sales Invoice Pydantic Schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class InvoiceLineCreate(BaseModel):
    item_id: Optional[uuid.UUID] = None
    description: str = Field(min_length=1)
    quantity: Decimal = Field(gt=Decimal("0"))
    unit_price: Decimal = Field(ge=Decimal("0"))
    tax_rate_id: Optional[uuid.UUID] = None
    income_account_id: Optional[uuid.UUID] = None


class InvoiceCreate(BaseModel):
    customer_id: uuid.UUID
    invoice_date: date
    due_date: date
    currency_code: str = Field(default="USD", min_length=3, max_length=3)
    notes: Optional[str] = None
    terms: Optional[str] = None
    lines: List[InvoiceLineCreate] = Field(min_length=1)


class InvoiceLineResponse(BaseModel):
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
    income_account_id: uuid.UUID


class InvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    state: str
    invoice_number: Optional[str]
    invoice_date: date
    due_date: date
    currency_code: str
    net_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    settled_amount: Decimal
    notes: Optional[str]
    terms: Optional[str]
    snapshot: Optional[dict]
    issued_at: Optional[datetime]
    issue_journal_id: Optional[uuid.UUID]
    voided_at: Optional[datetime]
    void_reason: Optional[str]
    void_journal_id: Optional[uuid.UUID]
    lines: List[InvoiceLineResponse] = []
    created_at: datetime


class InvoiceVoidRequest(BaseModel):
    reason: str = Field(min_length=3)
