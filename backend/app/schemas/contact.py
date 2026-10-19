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


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    email: Optional[str]
    phone: Optional[str]
    is_customer: bool
    is_vendor: bool
    status: str
    created_at: datetime
