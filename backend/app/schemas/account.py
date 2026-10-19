"""Chart of Accounts Schemas."""

from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    account_type: str
    normal_balance: str
    is_system: bool
    system_key: Optional[str]
    is_postable: bool
    status: str
