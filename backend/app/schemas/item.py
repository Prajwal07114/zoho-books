"""Item (Product/Service) Schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ItemCreate(BaseModel):
    name: str = Field(min_length=1)
    item_type: str = Field(default="service")  # product, service
    sku: Optional[str] = None
    sales_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    income_account_id: Optional[UUID] = None
    default_tax_rate_id: Optional[UUID] = None


class ItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    item_type: str
    sku: Optional[str]
    sales_price: Decimal
    income_account_id: Optional[UUID]
    default_tax_rate_id: Optional[UUID]
    is_active: bool
    created_at: datetime
