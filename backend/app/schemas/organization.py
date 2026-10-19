"""Organization and Membership Schemas."""

from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1)
    legal_name: Optional[str] = None
    base_currency_code: str = Field(default="USD", min_length=3, max_length=3)
    time_zone: str = Field(default="UTC")


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    legal_name: Optional[str]
    base_currency_code: str
    time_zone: str
    created_at: datetime


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    organization_id: UUID
    user_id: UUID
    role: str
    status: str
