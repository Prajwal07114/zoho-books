"""Credit Note Pydantic Schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class CreditNoteLineCreate(BaseModel):
    item_id: Optional[uuid.UUID] = None
    description: str = Field(min_length=1)
    quantity: Decimal = Field(gt=Decimal("0"))
    unit_price: Decimal = Field(ge=Decimal("0"))
    tax_rate_id: Optional[uuid.UUID] = None
    income_account_id: Optional[uuid.UUID] = None


class CreditNoteCreate(BaseModel):
    invoice_id: uuid.UUID
    credit_note_date: date
    currency_code: str = Field(default="USD", min_length=3, max_length=3)
    reason: Optional[str] = None
    notes: Optional[str] = None
    lines: List[CreditNoteLineCreate] = Field(min_length=1)


class CreditNoteLineResponse(BaseModel):
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


class CreditNoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    customer_id: uuid.UUID
    state: str
    credit_note_number: Optional[str]
    credit_note_date: date
    currency_code: str = "USD"
    net_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    allocated_amount: Optional[Decimal] = None
    refunded_amount: Optional[Decimal] = None
    reason: Optional[str] = None
    notes: Optional[str] = None
    snapshot: Optional[dict] = None
    issued_at: Optional[datetime] = None
    issue_journal_id: Optional[uuid.UUID] = None
    voided_at: Optional[datetime] = None
    void_reason: Optional[str] = None
    void_journal_id: Optional[uuid.UUID] = None
    lines: List[CreditNoteLineResponse] = []
    created_at: datetime



class CreditNoteVoidRequest(BaseModel):
    reason: str = Field(min_length=3)
