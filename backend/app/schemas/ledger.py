"""Pydantic schemas for Journals and General Ledger entries."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict


class JournalLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    line_no: int
    account_id: uuid.UUID
    account_code: Optional[str] = None
    account_name: Optional[str] = None
    debit: Decimal
    credit: Decimal
    accounting_date: date
    contact_id: Optional[uuid.UUID] = None
    description: Optional[str] = None


class JournalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    journal_number: Optional[str] = None
    accounting_date: date
    memo: Optional[str] = None
    source_type: str
    source_id: Optional[uuid.UUID] = None
    currency_code: str
    total_amount: Decimal
    reverses_journal_id: Optional[uuid.UUID] = None
    created_at: datetime
    lines: List[JournalLineRead] = []
