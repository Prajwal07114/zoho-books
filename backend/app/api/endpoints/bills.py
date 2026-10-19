"""Purchase Bill API Endpoints."""

from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_tenant_session
from app.models.bill import Bill
from app.models.identity import User
from app.schemas.bill import BillCreate, BillResponse, BillVoidRequest
from app.services.bill_service import BillService

router = APIRouter(prefix="/bills", tags=["Purchase Bills"])


@router.post("", response_model=BillResponse, status_code=status.HTTP_201_CREATED)
async def create_bill(
    payload: BillCreate,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> BillResponse:
    """Create draft purchase bill."""
    org_id = uuid.UUID(x_organization_id)
    bill = await BillService.create_draft_bill(db, org_id, payload, current_user)
    # Reload with lines
    full_bill = await BillService.get_bill(db, org_id, bill.id)
    response = BillResponse.model_validate(full_bill)
    await db.commit()
    return response


@router.get("", response_model=List[BillResponse])
async def list_bills(
    vendor_id: Optional[uuid.UUID] = Query(None, description="Filter by vendor ID"),
    state: Optional[str] = Query(None, description="Filter by bill state (draft, posted, void)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> List[BillResponse]:
    """List purchase bills for the active organization."""
    org_id = uuid.UUID(x_organization_id)
    bills = await BillService.list_bills(db, org_id, vendor_id=vendor_id, state=state, limit=limit, offset=offset)
    return [BillResponse.model_validate(b) for b in bills]


@router.get("/{bill_id}", response_model=BillResponse)
async def get_bill(
    bill_id: uuid.UUID,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> BillResponse:
    """Get purchase bill by ID."""
    org_id = uuid.UUID(x_organization_id)
    bill = await BillService.get_bill(db, org_id, bill_id)
    return BillResponse.model_validate(bill)


@router.post("/{bill_id}/post", response_model=BillResponse)
async def post_bill(
    bill_id: uuid.UUID,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> BillResponse:
    """Post draft bill to general ledger (JP-07)."""
    org_id = uuid.UUID(x_organization_id)
    bill = await BillService.post_bill(db, org_id, bill_id, current_user)
    response = BillResponse.model_validate(bill)
    await db.commit()
    return response


@router.post("/{bill_id}/void", response_model=BillResponse)
async def void_bill(
    bill_id: uuid.UUID,
    payload: BillVoidRequest,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> BillResponse:
    """Void posted bill and emit reversal journal (JP-08)."""
    org_id = uuid.UUID(x_organization_id)
    bill = await BillService.void_bill(db, org_id, bill_id, payload.reason, current_user)
    response = BillResponse.model_validate(bill)
    await db.commit()
    return response
