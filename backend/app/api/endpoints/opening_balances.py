"""Opening Balance API Endpoints."""

from typing import Optional
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_tenant_session
from app.models.identity import User
from app.schemas.opening_balance import OpeningBalanceCreate, OpeningBalanceResponse
from app.services.opening_balance_service import OpeningBalanceService

router = APIRouter(prefix="/opening-balances", tags=["Opening Balances"])


@router.post("", response_model=OpeningBalanceResponse, status_code=status.HTTP_201_CREATED)
async def record_opening_balances(
    payload: OpeningBalanceCreate,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> OpeningBalanceResponse:
    """Record opening balance migration batch (JP-14)."""
    org_id = uuid.UUID(x_organization_id)
    batch = await OpeningBalanceService.record_opening_balances(db, org_id, payload, current_user)
    response = OpeningBalanceResponse.model_validate(batch)
    await db.commit()
    return response


@router.get("", response_model=Optional[OpeningBalanceResponse])
async def get_opening_balances(
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> Optional[OpeningBalanceResponse]:
    """Get active opening balance batch for the organization."""
    org_id = uuid.UUID(x_organization_id)
    batch = await OpeningBalanceService.get_active_batch(db, org_id)
    if not batch:
        return None
    return OpeningBalanceResponse.model_validate(batch)
