"""Contact (Customer/Vendor) Schemas."""

from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ContactCreate(BaseModel):
    display_name: str = Field(min_length=1)
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    is_customer: bool = True
    is_vendor: bool = False
    tax_identifier: Optional[str] = None
    payment_terms_days: Optional[int] = Field(default=30, ge=0, le=365)
    billing_address: Optional[dict] = None


class ContactUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, min_length=1)
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    is_customer: Optional[bool] = None
    is_vendor: Optional[bool] = None
    tax_identifier: Optional[str] = None
    payment_terms_days: Optional[int] = Field(default=None, ge=0, le=365)
    billing_address: Optional[dict] = None
    status: Optional[str] = None


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    is_customer: bool
    is_vendor: bool
    tax_identifier: Optional[str] = None
    payment_terms_days: Optional[int] = None
    billing_address: Optional[dict] = None
    status: str
    created_at: datetime

