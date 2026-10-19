"""Supplier Payment API Endpoints."""

from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_tenant_session
from app.models.identity import User
from app.schemas.supplier_payment import (
    SupplierPaymentCreate,
    SupplierPaymentResponse,
    SupplierPaymentReverseRequest,
)
from app.services.supplier_payment_service import SupplierPaymentService

router = APIRouter(prefix="/supplier-payments", tags=["Supplier Payments"])


@router.post("", response_model=SupplierPaymentResponse, status_code=status.HTTP_201_CREATED)
async def record_payment(
    payload: SupplierPaymentCreate,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> SupplierPaymentResponse:
    """Record supplier payment, allocate to bills, and emit JP-09 journal."""
    org_id = uuid.UUID(x_organization_id)
    payment = await SupplierPaymentService.record_payment(db, org_id, payload, current_user)
    full_payment = await SupplierPaymentService.get_payment(db, org_id, payment.id)
    response = SupplierPaymentResponse.model_validate(full_payment)
    await db.commit()
    return response


@router.get("", response_model=List[SupplierPaymentResponse])
async def list_payments(
    vendor_id: Optional[uuid.UUID] = Query(None, description="Filter by vendor ID"),
    state: Optional[str] = Query(None, description="Filter by state (completed, reversed)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> List[SupplierPaymentResponse]:
    """List supplier payments."""
    org_id = uuid.UUID(x_organization_id)
    payments = await SupplierPaymentService.list_payments(
        db, org_id, vendor_id=vendor_id, state=state, limit=limit, offset=offset
    )
    return [SupplierPaymentResponse.model_validate(p) for p in payments]


@router.get("/{payment_id}", response_model=SupplierPaymentResponse)
async def get_payment(
    payment_id: uuid.UUID,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> SupplierPaymentResponse:
    """Get supplier payment by ID."""
    org_id = uuid.UUID(x_organization_id)
    payment = await SupplierPaymentService.get_payment(db, org_id, payment_id)
    return SupplierPaymentResponse.model_validate(payment)


@router.post("/{payment_id}/reverse", response_model=SupplierPaymentResponse)
async def reverse_payment(
    payment_id: uuid.UUID,
    payload: SupplierPaymentReverseRequest,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> SupplierPaymentResponse:
    """Reverse supplier payment and emit reversal journal (JP-10)."""
    org_id = uuid.UUID(x_organization_id)
    payment = await SupplierPaymentService.reverse_payment(db, org_id, payment_id, payload.reason, current_user)
    response = SupplierPaymentResponse.model_validate(payment)
    await db.commit()
    return response
