"""Chart of Accounts API Endpoints."""

from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_tenant_session
from app.models.accounting import Account
from app.schemas.account import AccountResponse

router = APIRouter(prefix="/accounts", tags=["Chart of Accounts"])


@router.get("", response_model=List[AccountResponse])
async def list_accounts(
    db: AsyncSession = Depends(get_tenant_session),
) -> List[Account]:
    """List the Chart of Accounts for the active organization."""
    stmt = select(Account).order_by(Account.code)
    res = await db.execute(stmt)
    return list(res.scalars().all())
