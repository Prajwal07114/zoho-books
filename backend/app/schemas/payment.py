"""Customer Payment Pydantic Schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class PaymentAllocationCreate(BaseModel):
    invoice_id: uuid.UUID
    amount: Decimal = Field(gt=Decimal("0"))


class PaymentCreate(BaseModel):
    customer_id: uuid.UUID
    payment_date: date
    amount: Decimal = Field(gt=Decimal("0"))
    deposit_account_id: Optional[uuid.UUID] = None
    method: str = Field(default="bank_transfer")
    reference: Optional[str] = None
    notes: Optional[str] = None
    allocations: List[PaymentAllocationCreate] = Field(default_factory=list)


class PaymentAllocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    amount: Decimal
    status: str
    allocated_at: datetime


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    payment_number: Optional[str]
    payment_date: date
    amount: Decimal
    method: str
    deposit_account_id: uuid.UUID
    reference: Optional[str]
    notes: Optional[str]
    state: str
    allocated_amount: Decimal
    journal_id: uuid.UUID
    reversal_journal_id: Optional[uuid.UUID]
    reversed_at: Optional[datetime]
    reversal_reason: Optional[str]
    allocations: List[PaymentAllocationResponse] = []
    created_at: datetime


class PaymentReverseRequest(BaseModel):
    reason: str = Field(min_length=3)
