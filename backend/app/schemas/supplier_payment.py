"""Supplier Payment Pydantic Schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class SupplierPaymentAllocationCreate(BaseModel):
    bill_id: uuid.UUID
    amount: Decimal = Field(gt=Decimal("0"))


class SupplierPaymentCreate(BaseModel):
    vendor_id: uuid.UUID
    payment_date: date
    amount: Decimal = Field(gt=Decimal("0"))
    payment_account_id: Optional[uuid.UUID] = None  # defaults to bank/cash
    method: str = Field(default="bank_transfer")
    reference: Optional[str] = None
    notes: Optional[str] = None
    allocations: List[SupplierPaymentAllocationCreate] = Field(default_factory=list)


class SupplierPaymentAllocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    bill_id: uuid.UUID
    amount: Decimal
    status: str
    allocated_at: datetime


class SupplierPaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vendor_id: uuid.UUID
    payment_number: Optional[str]
    payment_date: date
    amount: Decimal
    method: str
    payment_account_id: uuid.UUID
    reference: Optional[str]
    notes: Optional[str]
    state: str
    allocated_amount: Decimal
    journal_id: uuid.UUID
    reversal_journal_id: Optional[uuid.UUID]
    reversed_at: Optional[datetime]
    reversal_reason: Optional[str]
    allocations: List[SupplierPaymentAllocationResponse] = []
    created_at: datetime


class SupplierPaymentReverseRequest(BaseModel):
    reason: str = Field(min_length=3)
