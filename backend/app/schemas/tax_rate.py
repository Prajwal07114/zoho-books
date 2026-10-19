"""Tax Rate Schemas."""

from decimal import Decimal
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class TaxRateCreate(BaseModel):
    name: str = Field(min_length=1)
    rate_percent: Decimal = Field(ge=0, le=100)
    tax_account_id: Optional[UUID] = None


class TaxRateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    rate_percent: Decimal
    tax_account_id: Optional[UUID]
    is_active: bool
