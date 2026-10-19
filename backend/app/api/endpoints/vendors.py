"""Vendor API Endpoints."""

from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_tenant_session
from app.models.identity import User
from app.schemas.vendor import VendorCreate, VendorResponse, VendorUpdate
from app.services.vendor_service import VendorService

router = APIRouter(prefix="/vendors", tags=["Vendors"])


@router.post("", response_model=VendorResponse, status_code=status.HTTP_201_CREATED)
async def create_vendor(
    payload: VendorCreate,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> VendorResponse:
    """Create a new vendor."""
    org_id = uuid.UUID(x_organization_id)
    vendor = await VendorService.create_vendor(db, org_id, payload)
    response = VendorResponse.model_validate(vendor)
    await db.commit()
    return response


@router.get("", response_model=List[VendorResponse])
async def list_vendors(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status (active/inactive)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> List[VendorResponse]:
    """List vendors for the active organization."""
    org_id = uuid.UUID(x_organization_id)
    vendors = await VendorService.list_vendors(db, org_id, status_filter=status_filter, limit=limit, offset=offset)
    return [VendorResponse.model_validate(v) for v in vendors]


@router.get("/{vendor_id}", response_model=VendorResponse)
async def get_vendor(
    vendor_id: uuid.UUID,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> VendorResponse:
    """Get vendor by ID."""
    org_id = uuid.UUID(x_organization_id)
    vendor = await VendorService.get_vendor(db, org_id, vendor_id)
    return VendorResponse.model_validate(vendor)


@router.patch("/{vendor_id}", response_model=VendorResponse)
async def update_vendor(
    vendor_id: uuid.UUID,
    payload: VendorUpdate,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> VendorResponse:
    """Update vendor attributes."""
    org_id = uuid.UUID(x_organization_id)
    vendor = await VendorService.update_vendor(db, org_id, vendor_id, payload)
    response = VendorResponse.model_validate(vendor)
    await db.commit()
    return response


@router.post("/{vendor_id}/deactivate", response_model=VendorResponse)
async def deactivate_vendor(
    vendor_id: uuid.UUID,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> VendorResponse:
    """Soft-deactivate vendor to preserve historical transactions."""
    org_id = uuid.UUID(x_organization_id)
    vendor = await VendorService.deactivate_vendor(db, org_id, vendor_id)
    response = VendorResponse.model_validate(vendor)
    await db.commit()
    return response
