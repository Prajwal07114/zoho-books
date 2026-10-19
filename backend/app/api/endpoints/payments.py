"""Customer Payment API Endpoints."""

from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_tenant_session
from app.models.identity import User
from app.models.payment import Payment
from app.schemas.payment import PaymentCreate, PaymentResponse, PaymentReverseRequest
from app.services.payment_service import PaymentService

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post("", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
async def create_payment(
    payload: PaymentCreate,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> PaymentResponse:
    """Record customer payment receipt, allocate to invoices, and post JP-03 atomically."""
    org_id = uuid.UUID(x_organization_id)
    payment = await PaymentService.record_payment(
        db=db,
        organization_id=org_id,
        payload=payload,
        current_user=current_user,
    )
    # Eager load allocations before commit
    stmt = (
        select(Payment)
        .where(Payment.organization_id == org_id, Payment.id == payment.id)
        .options(selectinload(Payment.allocations))
    )
    res = await db.execute(stmt)
    full_payment = res.scalar_one()

    response = PaymentResponse.model_validate(full_payment)
    await db.commit()
    return response


@router.get("", response_model=List[PaymentResponse])
async def list_payments(
    state: Optional[str] = Query(None, description="Filter by payment state (active, reversed)"),
    customer_id: Optional[uuid.UUID] = Query(None, description="Filter by customer ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> List[Payment]:
    """List customer payments for the active organization."""
    stmt = select(Payment).options(selectinload(Payment.allocations))
    if state:
        stmt = stmt.where(Payment.state == state)
    if customer_id:
        stmt = stmt.where(Payment.customer_id == customer_id)

    stmt = stmt.order_by(Payment.payment_date.desc(), Payment.id.desc())
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_session),
) -> Payment:
    """Get single payment details by ID."""
    stmt = (
        select(Payment)
        .where(Payment.id == payment_id)
        .options(selectinload(Payment.allocations))
    )
    res = await db.execute(stmt)
    payment = res.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return payment


@router.post("/{payment_id}/reverse", response_model=PaymentResponse)
async def reverse_payment(
    payment_id: uuid.UUID,
    payload: PaymentReverseRequest,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> PaymentResponse:
    """Reverse a payment receipt, unallocate settlements, and post JP-04 atomically."""
    org_id = uuid.UUID(x_organization_id)
    payment = await PaymentService.reverse_payment(
        db=db,
        organization_id=org_id,
        payment_id=payment_id,
        reversal_reason=payload.reason,
        current_user=current_user,
    )
    stmt = (
        select(Payment)
        .where(Payment.organization_id == org_id, Payment.id == payment.id)
        .options(selectinload(Payment.allocations))
    )
    res = await db.execute(stmt)
    full_payment = res.scalar_one()

    response = PaymentResponse.model_validate(full_payment)
    await db.commit()
    return response
