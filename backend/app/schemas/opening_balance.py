"""Opening Balance Pydantic Schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class OpeningBalanceLineCreate(BaseModel):
    account_id: uuid.UUID
    debit: Decimal = Field(default=Decimal("0.0000"), ge=Decimal("0"))
    credit: Decimal = Field(default=Decimal("0.0000"), ge=Decimal("0"))
    description: Optional[str] = None


class OpeningBalanceCreate(BaseModel):
    conversion_date: date
    notes: Optional[str] = None
    lines: List[OpeningBalanceLineCreate] = Field(min_length=1)


class OpeningBalanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversion_date: date
    status: str
    total_debits: Optional[Decimal] = None
    total_credits: Optional[Decimal] = None
    journal_id: uuid.UUID
    notes: Optional[str] = None
    created_at: datetime

